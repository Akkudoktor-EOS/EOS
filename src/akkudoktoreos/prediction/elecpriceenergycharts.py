"""Retrieves and processes electricity price forecast data from Energy-Charts.

This module provides classes and mappings to manage electricity price data obtained from the
Energy-Charts API, including support for various electricity price attributes such as temperature,
humidity, cloud cover, and solar irradiance. The data is mapped to the `ElecPriceDataRecord`
format, enabling consistent access to forecasted and historical electricity price attributes.
"""

import time
from enum import StrEnum
from typing import Any, List, Optional, Union

import pandas as pd
import requests
from loguru import logger
from pydantic import Field, ValidationError

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.cache import cache_in_file
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.prediction.elecpriceabc import ElecPriceProvider
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime, to_duration


class EnergyChartsBiddingZones(StrEnum):
    """Energy Charts Bidding Zones."""

    AT = "AT"
    BE = "BE"
    CH = "CH"
    CZ = "CZ"
    DE_LU = "DE-LU"
    DE_AT_LU = "DE-AT-LU"
    DK1 = "DK1"
    DK2 = "DK2"
    FR = "FR"
    HU = "HU"
    IT_North = "IT-NORTH"
    NL = "NL"
    NO2 = "NO2"
    PL = "PL"
    SE4 = "SE4"
    SI = "SI"


class EnergyChartsElecPrice(PydanticBaseModel):
    license_info: str
    unix_seconds: List[int]
    price: List[float]
    unit: str
    deprecated: bool


class ElecPriceEnergyChartsCommonSettings(SettingsBaseModel):
    """Common settings for Energy Charts electricity price provider."""

    bidding_zone: EnergyChartsBiddingZones = Field(
        default=EnergyChartsBiddingZones.DE_LU,
        json_schema_extra={
            "description": (
                "Bidding Zone: 'AT', 'BE', 'CH', 'CZ', 'DE-LU', 'DE-AT-LU', 'DK1', 'DK2', 'FR', "
                "'HU', 'IT-NORTH', 'NL', 'NO2', 'PL', 'SE4' or 'SI'"
            ),
            "examples": ["AT"],
        },
    )


class ElecPriceEnergyCharts(ElecPriceProvider):
    """Fetch and process electricity price forecast data from Energy-Charts.

    ElecPriceEnergyCharts is a singleton-based class that retrieves electricity price forecast data
    from the Energy-Charts API and maps it to `ElecPriceDataRecord` fields, applying
    any necessary scaling or unit corrections. It manages the forecast over a range
    of hours into the future and retains historical data.

    Attributes:
        hours (int, optional): Number of hours in the future for the forecast.
        historic_hours (int, optional): Number of past hours for retaining data.
        start_datetime (datetime, optional): Start datetime for forecasts, defaults to the current datetime.
        end_datetime (datetime, computed): The forecast's end datetime, computed based on `start_datetime` and `hours`.
        keep_datetime (datetime, computed): The datetime to retain historical data, computed from `start_datetime` and `historic_hours`.

    Methods:
        provider_id(): Returns a unique identifier for the provider.
        _request_forecast(): Fetches the forecast from the Energy-Charts API.
        _update_data(): Processes and updates forecast data from Energy-Charts in ElecPriceDataRecord format.
    """

    highest_orig_datetime: Optional[DateTime] = None

    def historic_hours_min(self) -> int:
        """Keep enough history for weekly seasonal price extrapolation."""
        return 24 * 35

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the Energy-Charts provider."""
        return "ElecPriceEnergyCharts"

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

    @classmethod
    def _validate_data(cls, json_str: Union[bytes, Any]) -> EnergyChartsElecPrice:
        """Validate Energy-Charts Electricity Price forecast data."""
        try:
            energy_charts_data = EnergyChartsElecPrice.model_validate_json(json_str)
        except ValidationError as e:
            error_msg = ""
            for error in e.errors():
                field = " -> ".join(str(x) for x in error["loc"])
                message = error["msg"]
                error_type = error["type"]
                error_msg += f"Field: {field}\nError: {message}\nType: {error_type}\n"
            logger.error(f"Energy-Charts schema change: {error_msg}")
            raise ValueError(error_msg)
        return energy_charts_data

    def _bidding_zone(self) -> str:
        settings = self.config.elecprice.energycharts
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
            # Try to take data from 5 weeks back for prediction
            start_date = to_datetime(
                self.ems_start_datetime - to_duration("35 days"), as_string="YYYY-MM-DD"
            )

        last_date = to_datetime(self.end_datetime, as_string="YYYY-MM-DD")
        bidding_zone = str(self.config.elecprice.energycharts.bidding_zone)
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

    async def _parse_data(self, energy_charts_data: EnergyChartsElecPrice) -> pd.Series:
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
            prices_wh.at[orig_datetime] = price_wh

        # Always raw here — fees are applied once, later, over the complete
        # raw+predicted series in _store_gross_series().
        return prices_wh

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update forecast data in the ElecPriceDataRecord format.

        Retrieves data from Energy-Charts, maps each Energy-Charts field to the corresponding
        `ElecPriceDataRecord` and applies any necessary scaling.

        The final mapped and processed data is inserted into the sequence as `ElecPriceDataRecord`.
        """
        # Tomorrow's prices are available every day at 14:00.
        now = pd.Timestamp.now(tz=self.config.general.timezone)

        if not self.ems_start_datetime:
            raise ValueError(f"Start DateTime not set: {self.ems_start_datetime}")

        # Lower bound for the gross-series recompute below: defaults to "from
        # now", i.e. only the still-relevant forward-looking window, unless a
        # fresh fetch widens it to cover newly-arrived historic data too.
        gross_start_datetime = self.ems_start_datetime

        # Set default start_datetime - try to take data from 5 weeks back for prediction
        past_days = 35
        start_datetime = self.ems_start_datetime - to_duration(f"{past_days} days")

        # Determine if update is needed and what start date is really necessary
        needs_update = False
        if self.highest_orig_datetime:
            raw_history = await self.key_to_raw_series(
                key="elecprice_marketprice_raw_wh",
                start_datetime=start_datetime,
                end_datetime=gross_start_datetime,
            )

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
                    # The latest source data may have a different resolution than
                    # the history before ems_start_datetime. Use its final 24 hours
                    # so older, finer intervals cannot dominate the median, and
                    # exclude the predicted tail.
                    source_series = await self.key_to_raw_series(
                        key="elecprice_marketprice_raw_wh",
                        start_datetime=to_datetime(self.highest_orig_datetime).subtract(hours=24),
                        end_datetime=to_datetime(self.highest_orig_datetime).add(seconds=1),
                    )
                    source_resolution_seconds = self._resolution_seconds(source_series)
                    if not self._has_complete_published_horizon(
                        now=now, resolution_seconds=source_resolution_seconds
                    ):
                        start_datetime = gross_start_datetime
                        needs_update = True
        else:
            needs_update = True

        if needs_update:
            logger.info(
                "Update ElecPriceEnergyCharts is needed, last in history: {}, "
                "force_update={}, start_datetime={}",
                self.highest_orig_datetime,
                bool(force_update),
                start_datetime,
            )

            # Get Energy-Charts electricity price data
            try:
                energy_charts_data = self._request_forecast(
                    start_date=to_datetime(start_datetime, as_string="YYYY-MM-DD"),
                    force_update=force_update,
                )  # type: ignore

                # Parse and store data
                series_data = await self._parse_data(energy_charts_data)
                if series_data.empty:
                    raise ValueError("No Energy-Charts electricity price data available")
                self.highest_orig_datetime = to_datetime(series_data.index.max())
                await self.key_from_series("elecprice_marketprice_raw_wh", series_data)
                # Newly fetched data widens the window that needs its gross
                # (fee-inclusive) values recomputed.
                gross_start_datetime = to_datetime(series_data.index.min())
            except Exception as exc:
                if self.highest_orig_datetime is None:
                    raise
                logger.warning(
                    "Energy-Charts electricity price update failed ({}); keeping "
                    "existing history until {} and extrapolating the remaining "
                    "slots via ETS.",
                    exc,
                    self.highest_orig_datetime,
                )
        else:
            logger.info(
                "No Update ElecPriceEnergyCharts is needed, last in history: {}",
                self.highest_orig_datetime,
            )

        if not self.highest_orig_datetime:
            error_msg = f"Highest original datetime not available: {self.highest_orig_datetime}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        raw_series = await self.key_to_raw_series(
            key="elecprice_marketprice_raw_wh",
            end_datetime=to_datetime(self.highest_orig_datetime).add(seconds=1),
        )
        resolution_seconds = self._resolution_seconds(raw_series)
        slots_per_hour = 3600 // resolution_seconds

        # Raw history only. Guaranteed fee-free regardless of which branch ran
        # above, so ETS/median always trains on the true wholesale-price signal.
        history = await self.key_to_array(
            key="elecprice_marketprice_raw_wh",
            end_datetime=self.highest_orig_datetime,
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
                "No electricity price prediction needed. needed_slots={}, hours={}, "
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
        await self.key_from_series("elecprice_marketprice_raw_wh", prediction_series)

        # Bounded to [gross_start_datetime, end of the freshly predicted tail) -
        # covers exactly what was fetched and/or predicted this cycle, not the
        # entire (potentially multi-year) retained history.
        await self._store_gross_series(
            start_datetime=gross_start_datetime,
            end_datetime=to_datetime(prediction_series.index.max()) + to_duration("1 second"),
        )
