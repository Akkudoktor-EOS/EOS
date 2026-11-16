"""Electricity price provider with a residual-load model for days D+2..D+5.

Known day-ahead prices (bidding zone DE-LU) come from Energy-Charts, with SMARD as fallback (or the
other way round, see ``price_source``), and stay unchanged. For the days after the last published
day the provider replaces the seasonal ETS extrapolation by a model that predicts the residual load
(load - wind - PV) from the ECMWF weather forecast (Open-Meteo, 12 fixed weather points) and turns
it into a price with a monotonic price curve that is anchored at the known price level. Beyond day
D+5, and whenever the model is not available (no data yet, not trained yet, source error, too little
history), the seasonal ETS extrapolation of the base class is kept and a log message is written.

Side output (not used by the optimization): ``uncertainty.json`` in the provider data folder with
the 10/25/50/75/90 % quantiles per hour (scenarios from past residual-load errors and level-scaled
price remainders).
"""

import asyncio
import datetime as dt
import json
import time
from pathlib import Path
from typing import Any, Callable, Literal, Optional, cast

import numpy as np
import pandas as pd
from loguru import logger
from pydantic import Field, PrivateAttr

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.prediction import elecpriceakkudoktormlcore as core
from akkudoktoreos.prediction.elecpriceakkudoktormlengine import (
    AkkudoktorMLEngine,
    ForecastOutcome,
)
from akkudoktoreos.prediction.elecpriceenergycharts import (
    ElecPriceEnergyCharts,
    EnergyChartsElecPrice,
)
from akkudoktoreos.prediction.elecpricesmard import ElecPriceSMARD
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration

RAW_KEY = "elecprice_marketprice_raw_wh"
_LOG_THROTTLE_S = 6 * 3600
PUBLICATION_HOUR = (
    14  # next-day prices are expected from this local hour on (as in the Energy-Charts provider)
)
REFETCH_INCOMPLETE_S = (
    300  # minimum interval of forced requests while the expected period is still missing
)


class ElecPriceAkkudoktorMLCommonSettings(SettingsBaseModel):
    """Common settings for the Akkudoktor ML electricity price provider."""

    price_source: Literal["energycharts", "smard"] = Field(
        default="energycharts",
        json_schema_extra={
            "description": (
                "Source of the published day-ahead prices (bidding zone DE-LU): 'energycharts' "
                "(published first, SMARD as fallback) or 'smard' (Energy-Charts as fallback). "
                "SMARD always provides the load and generation data."
            ),
            "examples": ["energycharts"],
        },
    )

    write_uncertainty: bool = Field(
        default=True,
        json_schema_extra={
            "description": (
                "Write quantiles of the price forecast to uncertainty.json in the provider data "
                "folder (informational only, not used by the optimization)."
            ),
            "examples": [True],
        },
    )


class ElecPriceAkkudoktorML(ElecPriceSMARD):
    """Day-ahead prices extended for D+2..D+5 by a weather-driven price model."""

    _last_published_end: Optional[pd.Timestamp] = PrivateAttr(default=None)
    _last_forced_at: float = PrivateAttr(default=0.0)
    _state: str = PrivateAttr(default="")
    _state_logged_at: float = PrivateAttr(default=0.0)
    _engine: Optional[AkkudoktorMLEngine] = PrivateAttr(default=None)

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the residual-load provider."""
        return "ElecPriceAkkudoktorML"

    # ------------------------------------------------------------------ helpers
    def _engine_folder(self) -> Path:
        return Path(self.config.general.data_folder_path) / "elecprice_akkudoktorml"

    def _get_engine(self) -> AkkudoktorMLEngine:
        """Cache, models and error history of the data folder (created on first use)."""
        folder = self._engine_folder()
        if self._engine is None or self._engine.folder != folder:
            self._engine = AkkudoktorMLEngine(folder)
        return self._engine

    def _note_state(self, state: str, level: str, message: str, *args: Any) -> None:
        """Log a state message once per state and repeat it every few hours.

        Every other call of the same state is logged at debug level only, so the log does not fill
        up although the provider is updated several times per planning cycle.
        """
        now = time.time()
        if state != self._state or now - self._state_logged_at > _LOG_THROTTLE_S:
            self._state, self._state_logged_at = state, now
            logger.log(level.upper(), message, *args)
        else:
            logger.debug(message, *args)

    def _log_fallback(self, reason: str) -> None:
        self._note_state(
            f"fallback: {reason}",
            "warning",
            "ElecPriceAkkudoktorML: falling back to the seasonal ETS forecast ({})",
            reason,
        )

    @staticmethod
    def _known_hourly_ct(raw_wh: pd.Series) -> pd.Series:
        """Known raw prices (EUR/Wh) -> hourly mean in ct/kWh (UTC hours, complete hours only)."""
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

    # ------------------------------------------------------------------ published prices
    def _bidding_zone(self) -> str:
        """The model is valid for DE-LU only, whatever the Energy-Charts zone setting says."""
        return "DE-LU"

    def _now(self) -> pd.Timestamp:
        return pd.Timestamp.now(tz=self.config.general.timezone)

    def _expected_published_end(self) -> pd.Timestamp:
        """End of the period with published day-ahead prices (next day from 14:00 on)."""
        now = self._now()
        return now.normalize() + pd.DateOffset(days=1 if now.hour < PUBLICATION_HOUR else 2)

    @staticmethod
    def _published_end(prices: EnergyChartsElecPrice) -> pd.Timestamp:
        """End (exclusive) of a published price series: last interval start plus its length."""
        seconds = prices.unix_seconds
        step = seconds[-1] - seconds[-2] if len(seconds) > 1 else 3600
        return pd.Timestamp(seconds[-1] + step, unit="s", tz="UTC")

    def _request_source(
        self, source: str, start_date: Optional[str], force_update: Optional[bool]
    ) -> EnergyChartsElecPrice:
        """Request the published prices from one source ("energycharts" or "smard")."""
        # The sources are request methods of the parent classes; they only use self.config and
        # self.ems_start_datetime.
        if source == "smard":
            settings = self.config.elecprice.smard
            if settings.region != "DE" or settings.filter_id != 4169:
                # the model is valid for the DE-LU day-ahead price only
                raise ValueError(
                    "elecprice.smard.region/filter_id are not the defaults (DE, 4169) of the DE-LU "
                    "price"
                )
        cls = ElecPriceEnergyCharts if source == "energycharts" else ElecPriceSMARD
        request = cast(Callable[..., EnergyChartsElecPrice], cls._request_forecast)
        return request(self, start_date=start_date, force_update=force_update)

    def _request_forecast(
        self, start_date: Optional[str] = None, force_update: Optional[bool] = False
    ) -> EnergyChartsElecPrice:
        """Published day-ahead prices from the primary source, the other one as fallback.

        Energy-Charts usually publishes the next day earlier than SMARD. If the primary source fails
        or does not yet contain the expected period, the other source is asked as well and the
        answer that reaches further is used. The request methods cache responses for one hour; while
        the expected period is still missing, a fresh request is forced at most every
        ``REFETCH_INCOMPLETE_S`` seconds instead of reusing the incomplete answer.
        """
        order = ["energycharts", "smard"]
        if self.config.elecprice.akkudoktorml.price_source == "smard":
            order.reverse()
        expected = self._expected_published_end()
        force = bool(force_update)
        if (
            not force
            and self._last_published_end is not None
            and self._last_published_end < expected
            and time.time() - self._last_forced_at > REFETCH_INCOMPLETE_S
        ):
            force = True
            self._last_forced_at = time.time()
        best: Optional[EnergyChartsElecPrice] = None
        last_exc: Optional[Exception] = None
        for source in order:
            try:
                prices = self._request_source(source, start_date, force)
            except Exception as exc:  # try the other source
                last_exc = exc
                self._log_fallback(f"price source {source}: {type(exc).__name__}: {exc}")
                continue
            if best is None or self._published_end(prices) > self._published_end(best):
                best = prices
            if self._published_end(best) >= expected:
                break
        if best is None:
            raise last_exc if last_exc is not None else RuntimeError("no price source available")
        self._last_published_end = self._published_end(best)
        return best

    # ------------------------------------------------------------------ update
    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Base update (published prices + ETS), then replace the ETS slots by model prices."""
        await super()._update_data(force_update=force_update)
        try:
            await self._apply_model()
        except Exception as exc:  # never leave EOS without prices
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
            self._log_fallback("no known prices")
            return
        resolution = self._resolution_seconds(raw)
        hourly = self._known_hourly_ct(raw)
        known_day = self._last_complete_local_day(hourly)
        if known_day is None:
            self._log_fallback("no complete known price day")
            return
        model_day = known_day - dt.timedelta(days=1)  # D = last known price day - 1
        now_local = self._now()
        engine = self._get_engine()

        def blocking_forecast() -> tuple[Optional[ForecastOutcome], str]:
            core.preload()  # imports scikit-learn on first use: keep it out of the event loop
            return engine.forecast(model_day, hourly, now_local.date())

        outcome, reason = await asyncio.to_thread(blocking_forecast)
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
            self._log_fallback("model returns no prices for the forecast period")
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
        self._note_state(
            "model",
            "info",
            "ElecPriceAkkudoktorML: model prices active for {} slots from {} (model day D={}, "
            "anchor {:+.2f} ct/kWh, "
            "model month {:%Y-%m})",
            int(ok.sum()),
            series.index.min(),
            model_day,
            outcome.forecast.offset,
            outcome.model_month,
        )
        if self.config.elecprice.akkudoktorml.write_uncertainty:
            await asyncio.to_thread(self._write_uncertainty, outcome, model_day)

    def _write_uncertainty(self, outcome: ForecastOutcome, model_day: dt.date) -> None:
        folder = self._engine_folder()
        folder.mkdir(parents=True, exist_ok=True)
        idx = outcome.forecast.point.index
        doc: dict[str, Any] = {
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
        }
        if outcome.quantiles is not None:
            doc["quantiles"] = {
                f"{q:g}": [round(float(v), 4) for v in row]
                for q, row in zip(core.QUANTILES, outcome.quantiles)
            }
        tmp = folder / "uncertainty.json.tmp"
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        tmp.replace(folder / "uncertainty.json")
