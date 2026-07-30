"""Provide feed-in tariff data from Akkudoktor market prices."""

import time
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import requests
from loguru import logger

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.cache import cache_in_file
from akkudoktoreos.prediction.elecpriceakkudoktor import (
    AkkudoktorElecPrice,
    ElecPriceAkkudoktor,
)
from akkudoktoreos.prediction.feedintariffabc import FeedInTariffProvider
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration


class FeedInTariffAkkudoktorCommonSettings(SettingsBaseModel):
    """Settings for the Akkudoktor feed-in tariff provider.

    The public Akkudoktor price endpoint only needs the timezone already
    configured in ``general.timezone``, so no provider-specific values are
    currently required.
    """


class FeedInTariffAkkudoktor(FeedInTariffProvider):
    """Use raw Akkudoktor day-ahead market prices as feed-in tariff data.

    The upstream aWATTar endpoint currently supplies hourly values. EOS stores
    those source values unchanged; consumers requesting a shorter interval can
    forward-fill them onto the optimization grid.

    Electricity export charges and VAT are intentionally not added. Prices
    returned in EUR/MWh are converted to EUR/Wh and stored under
    ``feed_in_tariff_wh``.
    """

    highest_orig_datetime: Optional[datetime] = None

    def historic_hours_min(self) -> int:
        """Keep enough history for weekly seasonal price extrapolation."""
        return 24 * 35

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique provider identifier."""
        return "FeedInTariffAkkudoktor"

    @cache_in_file(with_ttl="1 hour")
    def _request_forecast(self) -> AkkudoktorElecPrice:
        """Fetch market prices from the public Akkudoktor API."""
        if not self.ems_start_datetime:
            raise ValueError(f"Start DateTime not set: {self.ems_start_datetime}")

        start_date = to_datetime(
            self.ems_start_datetime - to_duration("35 days"), as_string="YYYY-MM-DD"
        )
        end_date = to_datetime(self.end_datetime, as_string="YYYY-MM-DD")
        timezone = self.config.general.timezone
        url = f"https://api.akkudoktor.net/prices?start={start_date}&end={end_date}&tz={timezone}"

        max_attempts = 3
        last_exc: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                response = requests.get(url, timeout=(5, 20))
                logger.debug("Response from {}: {}", url, response)
                response.raise_for_status()
                data = ElecPriceAkkudoktor._validate_data(response.content)
                self.update_datetime = to_datetime(in_timezone=timezone)
                return data
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_exc = exc
                logger.warning(
                    "Akkudoktor feed-in tariff request attempt {}/{} failed: {}",
                    attempt,
                    max_attempts,
                    exc,
                )
                if attempt < max_attempts:
                    time.sleep(2 * attempt)

        raise last_exc  # type: ignore[misc]

    def _parse_data(self, data: AkkudoktorElecPrice) -> pd.Series:
        """Convert raw EUR/MWh values to a timezone-aware EUR/Wh series."""
        series = pd.Series(dtype=float)
        for value in data.values:
            timestamp = to_datetime(value.start, in_timezone=self.config.general.timezone)
            series.at[timestamp] = value.marketprice / 1_000_000
        return series

    def _predict_prices(self, history: np.ndarray, hours: int) -> np.ndarray:
        """Extend published prices to the configured prediction horizon."""
        predictor = ElecPriceAkkudoktor()
        if len(history) > 800:
            return predictor._predict_ets(history, seasonal_periods=168, hours=hours)
        if len(history) > 168:
            return predictor._predict_ets(history, seasonal_periods=24, hours=hours)
        if len(history) > 0:
            logger.warning(
                "Using median fallback for Akkudoktor feed-in tariff with only {} values.",
                len(history),
            )
            return predictor._predict_median(history, hours=hours)
        raise ValueError("No Akkudoktor feed-in tariff data available")

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update raw prices and extrapolate any missing horizon values."""
        if not self.ems_start_datetime:
            raise ValueError(f"Start DateTime not set: {self.ems_start_datetime}")

        try:
            data = self._request_forecast(force_update=force_update)  # type: ignore[call-arg]
            series = self._parse_data(data)
            if series.empty:
                raise ValueError("No Akkudoktor feed-in tariff data available")
            self.highest_orig_datetime = to_datetime(
                series.index.max(), in_timezone=self.config.general.timezone
            )
            await self.key_from_series("feed_in_tariff_wh", series)
        except Exception as exc:
            if self.highest_orig_datetime is None:
                raise
            logger.warning(
                "Akkudoktor feed-in tariff update failed ({}); retaining existing data.",
                exc,
            )

        if self.highest_orig_datetime is None:
            raise ValueError("Highest original datetime not available")

        history = np.asarray(
            await self.key_to_array(
                key="feed_in_tariff_wh",
                end_datetime=self.highest_orig_datetime,
                fill_method="linear",
            ),
            dtype=float,
        )
        covered_hours = (
            int((self.highest_orig_datetime - self.ems_start_datetime).total_seconds() // 3600) + 1
        )
        needed_hours = self.config.prediction.hours - max(covered_hours, 0)
        if needed_hours <= 0:
            return

        prediction = self._predict_prices(history, needed_hours)
        prediction_series = pd.Series(
            data=prediction,
            index=[
                self.highest_orig_datetime + to_duration(f"{i + 1} hours")
                for i in range(len(prediction))
            ],
        )
        await self.key_from_series("feed_in_tariff_wh", prediction_series)
