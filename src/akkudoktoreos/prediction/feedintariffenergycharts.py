"""Provides feed-in tariff data from Energy-Charts market prices."""

import time
from datetime import datetime
from typing import Optional

import pandas as pd
import requests
from loguru import logger
from pydantic import Field, PrivateAttr

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.cache import cache_in_file
from akkudoktoreos.prediction.elecpriceenergycharts import (
    ElecPriceEnergyCharts,
    EnergyChartsBiddingZones,
    EnergyChartsElecPrice,
)
from akkudoktoreos.prediction.feedintariffabc import FeedInTariffProvider
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration

MIRROR_HISTORY_HOURS = 48


class FeedInTariffEnergyChartsCommonSettings(SettingsBaseModel):
    """Common settings for Energy-Charts feed-in tariff provider."""

    bidding_zone: EnergyChartsBiddingZones = Field(
        default=EnergyChartsBiddingZones.DE_LU,
        json_schema_extra={
            "description": (
                "Bidding Zone: 'AT', 'BE', 'CH', 'CZ', 'DE-LU', 'DE-AT-LU', 'DK1', "
                "'DK2', 'FR', 'HU', 'IT-NORTH', 'NL', 'NO2', 'PL', 'SE4' or 'SI'"
            ),
            "examples": ["DE-LU"],
        },
    )

    follow_elecprice: bool = Field(
        default=False,
        json_schema_extra={
            "description": (
                "Use the day-ahead market price series of the active electricity price provider as "
                "the feed-in market price instead of fetching and extrapolating an own series. "
                "Purchase and feed-in forecasts then stay consistent when the electricity price "
                "provider forecasts differently from the seasonal extrapolation (e.g. "
                "'ElecPriceAkkudoktorML'). Only applied if that provider delivers an exchange "
                "price of the same bidding zone and covers the whole prediction horizon; "
                "otherwise the own series is used."
            ),
            "examples": [True],
        },
    )


class FeedInTariffEnergyCharts(FeedInTariffProvider):
    """Fetch Energy-Charts market prices as feed-in tariff data.

    This provider stores the raw Energy-Charts day-ahead market price as
    ``feed_in_tariff_raw_wh``, and derives ``feed_in_tariff_wh`` from it by
    applying any configured feed-in fees (unconditionally; a fee-free result
    requires leaving the ElecFee provider unconfigured).
    """

    highest_orig_datetime: Optional[datetime] = None
    _zone_warning_logged: bool = PrivateAttr(default=False)

    def historic_hours_min(self) -> int:
        """Keep enough history for weekly seasonal price extrapolation."""
        return 24 * 35

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the Energy-Charts feed-in tariff provider."""
        return "FeedInTariffEnergyCharts"

    def _coverage_resolution_seconds(self, source_series: pd.Series) -> int:
        """Infer the recent source cadence without changing prediction resolution.

        The caller excludes predicted timestamps. Four matching spacings among the
        latest five identify a short cadence transition, allowing one exceptional
        gap. Otherwise use the median over the last 24 hours; sparse data uses
        the shared resolution helper's hourly fallback.
        """
        if source_series.empty:
            return 3600

        recent_series = source_series.sort_index()
        recent_series = recent_series[
            recent_series.index >= recent_series.index[-1] - pd.Timedelta(hours=24)
        ]
        index = pd.DatetimeIndex(recent_series.index).drop_duplicates()
        deltas = index.to_series().diff().dropna().dt.total_seconds().tail(5)
        counts = deltas.value_counts()
        if not counts.empty and counts.iloc[0] >= 4:
            resolution = float(counts.index[0])
            if resolution > 0 and 3600 % resolution == 0:
                return int(resolution)

        return self._resolution_seconds(recent_series)

    def _has_complete_published_horizon(
        self, *, now: pd.Timestamp, resolution_seconds: int
    ) -> bool:
        """Return whether stored source data covers all currently published intervals.

        Energy-Charts timestamps identify interval starts. The actual coverage therefore ends one
        source interval after ``highest_orig_datetime``. Before 14:00, prices through the end of
        the current day are expected; from 14:00 onward, the following day is expected as well.
        """
        if self.highest_orig_datetime is None:
            return False

        published_days = 1 if now.hour < 14 else 2
        required_coverage_end = now.normalize() + pd.DateOffset(days=published_days)
        coverage_end = pd.Timestamp(self.highest_orig_datetime) + pd.Timedelta(
            seconds=resolution_seconds
        )
        return coverage_end >= required_coverage_end

    def _follows_elecprice(self) -> bool:
        """Return whether the feed-in market price follows the electricity price provider."""
        settings = self.config.feedintariff.energycharts
        return settings is not None and settings.follow_elecprice

    def _bidding_zone(self) -> str:
        settings = self.config.feedintariff.energycharts
        if settings is None:
            return EnergyChartsBiddingZones.DE_LU.value
        bidding_zone = settings.bidding_zone
        if isinstance(bidding_zone, EnergyChartsBiddingZones):
            return bidding_zone.value
        return str(bidding_zone)

    @cache_in_file(with_ttl="1 hour")
    def _request_forecast(self, start_date: Optional[str] = None) -> EnergyChartsElecPrice:
        """Fetch electricity price forecast data from Energy-Charts API.

        This method sends a request to Energy-Charts API to retrieve forecast data for a specified
        date range. The response data is parsed and returned as JSON for further processing.

        Returns:
            dict: The parsed JSON response from Energy-Charts API containing forecast data.

        Raises:
            ValueError: If the API response does not include expected `electricity price` data.
        """
        source = "https://api.energy-charts.info"
        if start_date is None:
            start_date = to_datetime(
                self.ems_start_datetime - to_duration("35 days"), as_string="YYYY-MM-DD"
            )

        last_date = to_datetime(self.end_datetime, as_string="YYYY-MM-DD")
        url = f"{source}/price?bzn={self._bidding_zone()}&start={start_date}&end={last_date}"

        # Retry transient network problems (timeouts / connection resets) a few
        # times with a short backoff. Uses a (connect, read) timeout tuple so a
        # slow-to-respond API does not block forever but also is not aborted
        # after a too-short single read window.
        max_attempts = 3
        last_exc: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                response = requests.get(url, timeout=(5, 60))
                logger.debug(f"Response from {url}: {response}")
                response.raise_for_status()
                energy_charts_data = ElecPriceEnergyCharts._validate_data(response.content)
                self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)
                return energy_charts_data
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_exc = exc
                logger.warning(
                    "Energy-Charts request attempt {}/{} failed: {}",
                    attempt,
                    max_attempts,
                    exc,
                )
                if attempt < max_attempts:
                    time.sleep(2 * attempt)
        # All attempts exhausted - re-raise the last transient error so the
        # caller (_update_data) can decide whether to fall back to history.
        raise last_exc  # type: ignore[misc]

    def _parse_data(self, energy_charts_data: EnergyChartsElecPrice) -> pd.Series:
        # Assumption that all lists are the same length and are ordered chronologically
        # in ascending order and have the same timestamps.

        # Initialize
        highest_orig_datetime = None  # newest datetime from the api after that we want to update.
        prices_wh = pd.Series(dtype=float)  # Initialize an empty series

        # Iterate over timestamps and prices together
        for unix_sec, price_eur_per_mwh in zip(
            energy_charts_data.unix_seconds, energy_charts_data.price
        ):
            orig_datetime = to_datetime(unix_sec, in_timezone=self.config.general.timezone)

            # Track the latest datetime
            if highest_orig_datetime is None or orig_datetime > highest_orig_datetime:
                highest_orig_datetime = orig_datetime

            # Convert EUR/MWh to EUR/Wh
            price_wh = price_eur_per_mwh / 1_000_000

            # Store in series
            prices_wh.loc[(orig_datetime,)] = price_wh

        # Always raw here — fees are applied once, later, over the complete
        # raw+predicted series in _store_gross_series().
        return prices_wh

    async def _mirror_elecprice_raw(self) -> bool:
        """Mirror the raw exchange price series of the active electricity price provider.

        If the setting ``follow_elecprice`` is on, the feed-in revenue is the exchange price.
        Fetching and extrapolating a second, independent series makes the purchase and feed-in
        forecasts diverge as soon as the electricity price provider forecasts differently (e.g. a
        model instead of the seasonal extrapolation). In that case the raw feed-in series is copied
        from ``elecprice_marketprice_raw_wh`` for every slot of the horizon; the configured feed-in
        fees are applied unchanged afterwards.

        Returns:
            True if the series was mirrored, False if the regular own fetch and extrapolation has to
            run (setting off, other electricity price provider or zone, or no series covering the
            horizon).
        """
        if not self._follows_elecprice():
            return False
        if not self.ems_start_datetime or not self.config.elecprice.provider:
            return False
        elec_provider = self.prediction.provider_by_id(self.config.elecprice.provider)
        elec_zone = elec_provider.exchange_price_zone() if elec_provider is not None else None
        if elec_zone is None:
            return False  # the electricity price series is not a day-ahead exchange price
        if elec_zone != self._bidding_zone():
            if not self._zone_warning_logged:
                self._zone_warning_logged = True
                logger.warning(
                    "{}: the electricity price provider uses bidding zone {} but the feed-in "
                    "provider uses {}; the feed-in market price is not mirrored",
                    self.provider_id(),
                    elec_zone,
                    self._bidding_zone(),
                )
            return False
        start = self.ems_start_datetime - to_duration(f"{MIRROR_HISTORY_HOURS} hours")
        end = self.ems_start_datetime + to_duration(f"{self.config.prediction.hours} hours")
        try:
            raw = await self.prediction.key_to_raw_series(
                key="elecprice_marketprice_raw_wh",
                start_datetime=start,
                end_datetime=end,
                dropna=False,
            )
        except KeyError:
            return False
        if raw.loc[raw.index >= pd.Timestamp(self.ems_start_datetime)].isna().any():
            # A gap in the forecast horizon would leave stale own values in the feed-in series.
            logger.warning(
                "{}: the electricity price series has gaps in the forecast horizon; "
                "using the own market price series",
                self.provider_id(),
            )
            return False
        raw = raw.dropna()  # gaps in the history before the start are simply not mirrored
        if len(raw) < 2:
            return False
        resolution_seconds = self._resolution_seconds(raw)
        if raw.index.max() < pd.Timestamp(end) - pd.Timedelta(seconds=2 * resolution_seconds):
            logger.warning(
                "{}: the electricity price series ends at {} before the horizon end {}; "
                "using the own market price series",
                self.provider_id(),
                raw.index.max(),
                end,
            )
            return False
        window_start = to_datetime(raw.index.min())
        window_end = to_datetime(raw.index.max()) + to_duration(f"{resolution_seconds} seconds")
        # Own values of another resolution (e.g. hourly from an earlier extrapolation) would survive
        # between the mirrored slots: clear the window first.
        try:
            await self.key_delete_by_datetime("feed_in_tariff_raw_wh", window_start, window_end)
            await self.key_delete_by_datetime("feed_in_tariff_wh", window_start, window_end)
            await self.key_from_series("feed_in_tariff_raw_wh", raw)
            await self._store_gross_series(start_datetime=window_start, end_datetime=window_end)
        except Exception as exc:
            # Do not leave a half-written window behind: the regular own fetch rewrites it.
            logger.warning(
                "{}: mirroring the electricity price series failed ({}); using the own market "
                "price series",
                self.provider_id(),
                exc,
            )
            return False
        logger.info(
            "{}: feed-in market price mirrored from the {} series ({} slots up to {})",
            self.provider_id(),
            self.config.elecprice.provider,
            len(raw),
            raw.index.max(),
        )
        return True

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update feed-in tariff forecast data from Energy-Charts.

        Retrieves data from Energy-Charts, maps each Energy-Charts field to the corresponding
        `FeedInTariffDataRecord` and applies any necessary scaling.

        The final mapped and processed data is inserted into the sequence as `FeedInTariffDataRecord`.
        """
        # Tomorrow's prices are available every day at 14:00.
        now = pd.Timestamp.now(tz=self.config.general.timezone)

        if not self.ems_start_datetime:
            raise ValueError(f"Start DateTime not set: {self.ems_start_datetime}")

        if await self._mirror_elecprice_raw():
            return

        # Lower bound for the gross-series recompute at the end of this method:
        # defaults to "from now", widened to the fetched window's start if a
        # fetch actually happens below.
        gross_start_datetime = self.ems_start_datetime

        # Set default start_datetime - try to take data from 5 weeks back for prediction
        past_days = 35
        start_datetime = self.ems_start_datetime - to_duration(f"{past_days} days")

        # Determine if update is needed and what start date is really necessary
        needs_update = False
        raw_series: Optional[pd.Series] = None
        if self.highest_orig_datetime:
            source_end = to_datetime(self.highest_orig_datetime).add(seconds=1)
            raw_series = await self.key_to_raw_series(
                key="feed_in_tariff_raw_wh",
                end_datetime=max(gross_start_datetime, source_end),
            )
            raw_history = raw_series[
                (raw_series.index >= pd.Timestamp(start_datetime))
                & (raw_series.index < pd.Timestamp(gross_start_datetime))
            ]
            raw_series = raw_series[raw_series.index <= pd.Timestamp(self.highest_orig_datetime)]

            if raw_history.empty:
                # We need the default start date (35 days in past)
                needs_update = True
            else:
                # A later update must not mistake the current forecast window for
                # sufficient ETS history. Require the same amount of data that the
                # weekly prediction branch in _predict needs; otherwise fetch 35
                # days again and repair an already-truncated in-memory history.
                resolution_seconds = self._resolution_seconds(raw_history)
                slots_per_hour = 3600 // resolution_seconds
                if len(raw_history) <= 2 * 168 * slots_per_hour:
                    # Not enough slots in history, default start date
                    needs_update = True
                elif force_update:
                    # Use default start date in case of forced update
                    needs_update = True
                else:
                    source_resolution_seconds = self._coverage_resolution_seconds(raw_series)
                    if not self._has_complete_published_horizon(
                        now=now, resolution_seconds=source_resolution_seconds
                    ):
                        # We have enough history, but not every expected source interval.
                        start_datetime = gross_start_datetime
                        needs_update = True
        else:
            needs_update = True

        if needs_update:
            logger.info(
                "Update FeedInTariffEnergyCharts is needed, last in history: {}, "
                "force_update={}, start_datetime={}",
                self.highest_orig_datetime,
                bool(force_update),
                start_datetime,
            )
            # Set start_date try to take data from 5 weeks back for prediction
            start_date = to_datetime(
                self.ems_start_datetime - to_duration(f"{past_days} days"),
                as_string="YYYY-MM-DD",
            )
            # Get Energy-Charts electricity price data
            try:
                energy_charts_data = self._request_forecast(
                    start_date=to_datetime(start_datetime, as_string="YYYY-MM-DD"),
                    force_update=force_update,
                )  # type: ignore

                # Parse and store data
                series_data = self._parse_data(energy_charts_data)
                if series_data.empty:
                    raise ValueError("No Energy-Charts feed-in tariff data available")
                self.highest_orig_datetime = to_datetime(series_data.index.max())
                await self.key_from_series("feed_in_tariff_raw_wh", series_data)
                raw_series = None  # Reload the newly fetched source before forecasting.
                # Newly fetched data widens the window that needs its gross
                # (fee-inclusive) values recomputed.
                gross_start_datetime = to_datetime(series_data.index.min())
            except Exception as exc:
                if self.highest_orig_datetime is None:
                    raise
                logger.warning(
                    "Energy-Charts feed-in tariff update failed ({}); keeping "
                    "existing history until {} and extrapolating the remaining "
                    "slots via ETS.",
                    exc,
                    self.highest_orig_datetime,
                )
        else:
            logger.info(
                "No update FeedInTariffEnergyCharts is needed, last in history: {}",
                self.highest_orig_datetime,
            )

        if not self.highest_orig_datetime:
            error_msg = f"Highest original datetime not available: {self.highest_orig_datetime}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        if raw_series is None:
            raw_series = await self.key_to_raw_series(
                key="feed_in_tariff_raw_wh",
                end_datetime=to_datetime(self.highest_orig_datetime).add(seconds=1),
            )
        resolution_seconds = self._resolution_seconds(raw_series)
        slots_per_hour = 3600 // resolution_seconds

        # Raw history only. Guaranteed fee-free regardless of which branch ran
        # above, so ETS/median always trains on the true wholesale-price signal.
        history = await self.key_to_array(
            key="feed_in_tariff_raw_wh",
            end_datetime=to_datetime(self.highest_orig_datetime),
            interval=to_duration(f"{resolution_seconds} seconds"),
            fill_method="linear",
        )

        # Signed gap: positive when existing raw data already reaches past
        # ems_start_datetime (fewer slots left to predict); negative when the
        # newest known data point (highest_orig_datetime) is older than
        # ems_start_datetime, e.g. after a fetch outage - in that case we need
        # extra slots to also backfill the gap up to ems_start_datetime, on top
        # of the full prediction.hours horizon beyond it.
        covered_slots = int(
            (self.highest_orig_datetime - self.ems_start_datetime).total_seconds()
            // resolution_seconds
        )
        needed_slots = self.config.prediction.hours * slots_per_hour - covered_slots

        if needed_slots <= 0:
            # This might keep data longer than
            # self.ems_start_datetime + self.config.prediction.hours in the records
            logger.warning(
                "No feed-in tariff prediction needed. needed_slots={}, hours={}, "
                "resolution_seconds={}, highest_orig_datetime={}, start_datetime={}",
                needed_slots,
                self.config.prediction.hours,
                resolution_seconds,
                self.highest_orig_datetime,
                self.ems_start_datetime,
            )
            # Fee schedule may have changed since the last run even without new
            # market data; recompute gross only for the window that was
            # actually touched (or is still forward-looking) this cycle.
            await self._store_gross_series(
                start_datetime=gross_start_datetime,
                end_datetime=to_datetime(self.highest_orig_datetime).add(seconds=1),
            )
            return

        prediction = self._predict(history, needed_slots, slots_per_hour=slots_per_hour)

        # write predictions into the records, update if exist.
        prediction_series = pd.Series(
            data=prediction,
            index=[
                self.highest_orig_datetime + to_duration(f"{(i + 1) * resolution_seconds} seconds")
                for i in range(len(prediction))
            ],
        )
        await self.key_from_series("feed_in_tariff_raw_wh", prediction_series)

        # Bounded to [gross_start_datetime, end of the freshly predicted tail) -
        # covers exactly what was fetched and/or predicted this cycle, not the
        # entire (potentially multi-year) retained history.
        await self._store_gross_series(
            start_datetime=gross_start_datetime,
            end_datetime=to_datetime(prediction_series.index.max()) + to_duration("1 second"),
        )
