"""Provides feed-in tariff data from the dvhub.online price API."""

import time
from datetime import datetime
from typing import Any, Optional

import pandas as pd
import requests
from loguru import logger
from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.cache import cache_in_file
from akkudoktoreos.prediction.feedintariffabc import FeedInTariffProvider
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration


class FeedInTariffDvhubOnlineCommonSettings(SettingsBaseModel):
    """Common settings for the dvhub.online feed-in tariff provider."""

    base_url: str = Field(
        default="https://dvhub.online",
        json_schema_extra={
            "description": "Base URL of the dvhub.online price API.",
            "examples": ["https://dvhub.online"],
        },
    )
    zone: str = Field(
        default="DE-LU",
        json_schema_extra={
            "description": "Bidding zone passed to the dvhub.online price API.",
            "examples": ["DE-LU"],
        },
    )


class FeedInTariffDvhubOnline(FeedInTariffProvider):
    """Fetch dvhub.online day-ahead market prices as feed-in tariff data.

    dvhub.online serves EPEX day-ahead prices (15-minute slots, EUR/MWh) via
    ``GET /api/prices?start=YYYY-MM-DD&end=YYYY-MM-DD&zone=DE-LU``. The raw
    market price is stored as ``feed_in_tariff_wh`` (EUR/Wh) — like
    ``FeedInTariffEnergyCharts`` this intentionally adds no import charges or
    VAT, so the series is the direct-marketing revenue the optimizer needs.
    Slots beyond the published day-ahead horizon are left to the consumer's
    forward-fill (same behaviour as ``FeedInTariffImport``).
    """

    highest_orig_datetime: Optional[datetime] = None

    def historic_hours_min(self) -> int:
        """Day-ahead source without seasonal extrapolation — keep two days."""
        return 48

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the dvhub.online feed-in tariff provider."""
        return "FeedInTariffDvhubOnline"

    def _provider_settings(self) -> FeedInTariffDvhubOnlineCommonSettings:
        settings = self.config.feedintariff.provider_settings.FeedInTariffDvhubOnline
        if settings is None:
            return FeedInTariffDvhubOnlineCommonSettings()
        return settings

    @cache_in_file(with_ttl="1 hour")
    def _request_forecast(self, start_date: str, end_date: str) -> dict[str, Any]:
        """Fetch market prices from the dvhub.online price API."""
        settings = self._provider_settings()
        url = (
            f"{settings.base_url}/api/prices"
            f"?start={start_date}&end={end_date}&zone={settings.zone}"
        )
        # Retry transient network problems with a short backoff (same pattern
        # as FeedInTariffEnergyCharts): (connect, read) timeout tuple.
        max_attempts = 3
        last_exc: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                response = requests.get(
                    url, headers={"accept": "application/json"}, timeout=(5, 60)
                )
                logger.debug(f"Response from {url}: {response}")
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                    raise ValueError(
                        f"Unexpected dvhub.online price API response shape from {url}"
                    )
                self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)
                return data
            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
            ) as exc:
                last_exc = exc
                logger.warning(
                    "dvhub.online price request attempt {}/{} failed: {}",
                    attempt,
                    max_attempts,
                    exc,
                )
                if attempt < max_attempts:
                    time.sleep(2 * attempt)
        raise last_exc  # type: ignore[misc]

    def _parse_data(self, dvhub_data: dict[str, Any]) -> pd.Series:
        """dvhub.online entries {ts, price[EUR/MWh]} -> Series of EUR/Wh."""
        series_data = pd.Series(dtype=float)
        for entry in dvhub_data.get("data", []):
            try:
                orig_datetime = to_datetime(
                    entry["ts"], in_timezone=self.config.general.timezone
                )
                price_eur_per_mwh = float(entry["price"])
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Skipping malformed dvhub.online price entry {}: {}", entry, exc)
                continue
            series_data.at[orig_datetime] = price_eur_per_mwh / 1_000_000
        return series_data

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update feed-in tariff forecast data from dvhub.online."""
        if not self.ems_start_datetime:
            raise ValueError(f"Start DateTime not set: {self.ems_start_datetime}")

        start_date = to_datetime(
            self.ems_start_datetime - to_duration("2 days"), as_string="YYYY-MM-DD"
        )
        end_date = to_datetime(self.end_datetime, as_string="YYYY-MM-DD")

        try:
            dvhub_data = self._request_forecast(
                start_date=start_date, end_date=end_date, force_update=force_update
            )
            series_data = self._parse_data(dvhub_data)
            if series_data.empty:
                raise ValueError("No dvhub.online feed-in tariff data available")
            self.highest_orig_datetime = series_data.index.max()
            self.key_from_series("feed_in_tariff_wh", series_data)
        except Exception as exc:
            if self.highest_orig_datetime is None:
                # Cold start: nothing to fall back to — a failed fetch is fatal.
                raise
            # Transient outage with existing history: keep the history and let
            # downstream forward-fill cover the remaining slots.
            logger.warning(
                "dvhub.online feed-in tariff update failed ({}); keeping existing "
                "history until {}.",
                exc,
                self.highest_orig_datetime,
            )
