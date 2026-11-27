"""Electricity price provider with a residual-load model for days D+2..D+5.

Known day-ahead prices come from SMARD, with Energy-Charts filling missing DE-LU slots. For the days after the
last published day the provider replaces the seasonal ETS extrapolation by a model that predicts the residual load
(load - wind - PV) from the ECMWF weather forecast (Open-Meteo, 12 fixed weather points) and turns it into a price with a
monotonic price curve that is anchored at the known price level. Beyond day D+5 and whenever the model is not
available (no data yet, not trained yet, source error, too little history) the behaviour of ``ElecPriceSMARD``
(ETS) is kept and a log message is written.

Measured quality in the replay (2024-09 .. 2026-11, issue time 13:00, no look-ahead): hourly error D+2..D+5
about 28 % (test period) to 35 % (blind period) below the ETS extrapolation.

Side output (not used by the optimization): ``<data folder>/elecprice_residualload/uncertainty.json`` with the
10/25/50/75/90 % quantiles per hour (scenarios U3: past residual-load errors and level-scaled price remainders).
"""

import asyncio
import datetime as dt
import json
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests
from loguru import logger

from akkudoktoreos.prediction import elecpriceresidualloadcore as core
from akkudoktoreos.prediction.elecpriceenergycharts import (
    ElecPriceEnergyCharts,
    EnergyChartsElecPrice,
)
from akkudoktoreos.prediction.elecpriceresidualloadengine import (
    ForecastOutcome,
    ResidualLoadEngine,
)
from akkudoktoreos.prediction.elecpricesmard import ElecPriceSMARD
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration

RAW_KEY = "elecprice_marketprice_raw_wh"
_ENGINES: dict[str, ResidualLoadEngine] = {}
_LOG_THROTTLE_S = 6 * 3600


def get_engine(folder: Path) -> ResidualLoadEngine:
    """One engine (cache, models, error history) per data folder."""
    key = str(Path(folder).resolve())
    if key not in _ENGINES:
        _ENGINES[key] = ResidualLoadEngine(Path(folder))
    return _ENGINES[key]


class ElecPriceResidualLoad(ElecPriceSMARD):
    """Published day-ahead prices with a weather-driven residual-load forecast."""

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the residual-load provider."""
        return "ElecPriceResidualLoad"

    def _request_forecast(
        self, start_date: Optional[str] = None, *, force_update: Optional[bool] = False
    ) -> EnergyChartsElecPrice:
        """Fill missing published DE-LU price slots with Energy-Charts."""
        settings = self.config.elecprice.smard
        if (
            settings.filter_id != 4169
            or settings.region not in {"DE", "DE-LU"}
            or self._bidding_zone() != "DE-LU"
        ):
            return super()._request_forecast(start_date=start_date, force_update=force_update)  # type: ignore[call-arg]

        smard = None
        smard_error = None
        try:
            smard = super()._request_forecast(start_date=start_date, force_update=force_update)  # type: ignore[call-arg]
        except (requests.RequestException, ValueError) as exc:
            smard_error = exc

        prices = dict(zip(smard.unix_seconds, smard.price, strict=True)) if smard else {}
        now = pd.Timestamp.now(tz=self.config.general.timezone)
        today = now.normalize()
        tomorrow = today + pd.DateOffset(days=1)
        day_after_tomorrow = today + pd.DateOffset(days=2)
        expected_end = tomorrow if now.hour < 14 else day_after_tomorrow
        expected = pd.date_range(today, expected_end, freq="15min", inclusive="left")
        if smard and all(int(slot.timestamp()) in prices for slot in expected):
            return smard

        try:
            energy_charts = ElecPriceEnergyCharts._request_forecast(  # type: ignore[call-arg]
                self, start_date=start_date, force_update=force_update
            )
            if "".join(energy_charts.unit.split()) != "EUR/MWh":
                raise ValueError(f"Unexpected Energy-Charts price unit: {energy_charts.unit}")
            fallback = {
                ts: price
                for ts, price in zip(energy_charts.unix_seconds, energy_charts.price, strict=True)
                if ts < day_after_tomorrow.timestamp() and np.isfinite(price)
            }
        except (requests.RequestException, ValueError) as exc:
            logger.warning("ElecPriceResidualLoad: Energy-Charts fallback failed ({})", exc)
            if smard_error is not None:
                raise smard_error from exc
            if smard is None:
                raise ValueError("SMARD returned no price series.") from exc
            return smard

        # Only add slots from complete market days. Incomplete source data must
        # not extend the published horizon across missing quarter-hours.
        fallback_days = {pd.Timestamp(ts, unit="s", tz=today.tz).normalize() for ts in fallback}
        added = 0
        for day in sorted(fallback_days):
            day_slots = [
                int(slot.timestamp())
                for slot in pd.date_range(
                    day, day + pd.DateOffset(days=1), freq="15min", inclusive="left"
                )
            ]
            if all(ts in prices or ts in fallback for ts in day_slots):
                missing = {ts: fallback[ts] for ts in day_slots if ts not in prices}
                prices.update(missing)
                added += len(missing)
        if not added:
            if smard_error is not None:
                raise smard_error
            if smard is None:
                raise ValueError("SMARD returned no price series.")
            return smard

        logger.info(
            "ElecPriceResidualLoad: Energy-Charts DE-LU filled {} missing SMARD slots",
            added,
        )
        timestamps = sorted(prices)
        license_info = energy_charts.license_info
        if smard is not None:
            license_info = f"{smard.license_info} / {license_info}"
        return EnergyChartsElecPrice(
            unix_seconds=timestamps,
            price=[prices[ts] for ts in timestamps],
            license_info=license_info,
            unit="EUR/MWh",
            deprecated=False,
        )

    # ------------------------------------------------------------------ helpers
    def _engine_folder(self) -> Path:
        return Path(self.config.general.data_folder_path) / "elecprice_residualload"

    def _log_fallback(self, reason: str) -> None:
        now = time.time()
        last = getattr(self.__class__, "_last_fallback_log", {})
        if now - last.get(reason, 0.0) > _LOG_THROTTLE_S:
            last[reason] = now
            self.__class__._last_fallback_log = last  # type: ignore[attr-defined]
            logger.warning("ElecPriceResidualLoad: Rückfall auf ETS ({})", reason)

    @staticmethod
    def _known_hourly_ct(raw_wh: pd.Series) -> pd.Series:
        """Known raw prices (EUR/Wh, any slot size) -> hourly mean in ct/kWh (UTC hours, only complete hours)."""
        s = raw_wh.dropna()
        s.index = pd.DatetimeIndex(s.index).tz_convert("UTC")
        agg = s.resample("1h").agg(["mean", "count"])
        per_hour = int(agg["count"].max()) if len(agg) else 1
        return (agg.loc[agg["count"] >= per_hour, "mean"] * 1e5).rename("price_ct")

    @staticmethod
    def _last_complete_local_day(hourly_ct: pd.Series) -> Optional[dt.date]:
        """Latest local day of which all hours are known."""
        if hourly_ct.empty:
            return None
        local = pd.DatetimeIndex(hourly_ct.index).tz_convert(core.TZ_LOCAL)
        for d in sorted(set(local.date), reverse=True)[:3]:
            if hourly_ct.index.isin(core.local_day_hours(d)).sum() == len(core.local_day_hours(d)):
                return d
        return None

    # ------------------------------------------------------------------ update
    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Base update (SMARD + ETS), then replace the ETS slots of the model days by the model prices."""
        await super()._update_data(force_update=force_update)
        try:
            await self._apply_model()
        except Exception as exc:  # noqa: BLE001 - never leave EOS without prices
            self._log_fallback(f"{type(exc).__name__}: {exc}")

    async def _apply_model(self) -> None:
        highest = self.highest_orig_datetime
        if highest is None or not self.ems_start_datetime:
            return
        raw = await self.key_to_raw_series(
            key=RAW_KEY,
            start_datetime=to_datetime(highest).subtract(days=14),
            end_datetime=to_datetime(highest).add(seconds=1),
        )
        if raw.empty:
            self._log_fallback("keine bekannten Preise")
            return
        resolution = self._resolution_seconds(raw)
        hourly = self._known_hourly_ct(raw)
        known_day = self._last_complete_local_day(hourly)
        if known_day is None:
            self._log_fallback("kein vollständiger bekannter Preistag")
            return
        model_day = known_day - dt.timedelta(days=1)  # D = letzter bekannter Preistag - 1
        now_local = pd.Timestamp.now(tz=self.config.general.timezone)
        engine = get_engine(self._engine_folder())
        core.preload()
        outcome, reason = await asyncio.to_thread(
            engine.forecast, model_day, hourly, now_local.date()
        )
        if outcome is None:
            self._log_fallback(reason)
            return

        # slots the base class predicted: (highest, highest + needed_slots * resolution]
        slots_per_hour = 3600 // resolution
        covered = int(
            (to_datetime(highest) - self.ems_start_datetime).total_seconds() // resolution
        )
        needed = self.config.prediction.hours * slots_per_hour - covered
        if needed <= 0:
            return
        h0 = pd.Timestamp(to_datetime(highest).in_timezone("UTC").isoformat())
        slots = pd.DatetimeIndex(
            [h0 + pd.Timedelta(seconds=(i + 1) * resolution) for i in range(needed)]
        )
        point = outcome.forecast.point
        hour_of_slot = slots.floor("1h")
        vals_ct = point.reindex(hour_of_slot).to_numpy()
        ok = np.isfinite(vals_ct)
        if not ok.any():
            self._log_fallback("Modell liefert keine Preise für den Prognosezeitraum")
            return
        tz = self.config.general.timezone
        series = pd.Series(
            vals_ct[ok] / 1e5,
            index=[to_datetime(t.to_pydatetime(), in_timezone=tz) for t in slots[ok]],
        )
        await self.key_from_series(RAW_KEY, series)
        await self._store_gross_series(
            start_datetime=to_datetime(series.index.min()),
            end_datetime=to_datetime(series.index.max()) + to_duration("1 second"),
        )
        logger.info(
            "ElecPriceResidualLoad: Modellpreise für {} Slots ab {} übernommen (Modelltag D={}, Anker {:+.2f} ct/kWh, "
            "Modellmonat {:%Y-%m})",
            int(ok.sum()),
            series.index.min(),
            model_day,
            outcome.forecast.offset,
            outcome.model_month,
        )
        self._write_uncertainty(outcome, model_day)

    def _write_uncertainty(self, outcome: ForecastOutcome, model_day: dt.date) -> None:
        folder = self._engine_folder()
        folder.mkdir(parents=True, exist_ok=True)
        idx = outcome.forecast.point.index
        doc = {
            "provider": self.provider_id(),
            "unit": "ct/kWh",
            "issued_at": pd.Timestamp.now(tz="UTC").isoformat(),
            "model_day": model_day.isoformat(),
            "model_month": outcome.model_month.strftime("%Y-%m"),
            "quantile_levels": list(core.QUANTILES),
            "hours_utc": [t.isoformat() for t in idx],
            "point": [
                None if not np.isfinite(v) else round(float(v), 4) for v in outcome.forecast.point
            ],
            "quantiles": None,
            "reason": outcome.uncertainty_reason,
            "model_basis": "pv_nonnegative_u3_v1",
            "calibration": outcome.calibration,
            "revision": outcome.revision,
        }
        if outcome.quantiles is not None:
            doc["quantiles"] = {
                f"{q:g}": [round(float(v), 4) for v in row]
                for q, row in zip(core.QUANTILES, outcome.quantiles)
            }
        tmp = folder / "uncertainty.json.tmp"
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        tmp.replace(folder / "uncertainty.json")
