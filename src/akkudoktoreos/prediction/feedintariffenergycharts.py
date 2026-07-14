"""Provides feed-in tariff data from Energy-Charts market prices."""

from datetime import datetime
from typing import Optional

import pandas as pd
import requests
from loguru import logger
from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.cache import cache_in_file
from akkudoktoreos.prediction.elecpriceenergycharts import (
    ElecPriceEnergyCharts,
    EnergyChartsBiddingZones,
    EnergyChartsElecPrice,
)
from akkudoktoreos.prediction.feedintariffabc import FeedInTariffProvider
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration


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


class FeedInTariffEnergyCharts(FeedInTariffProvider):
    """Fetch Energy-Charts market prices as feed-in tariff data.

    This provider stores the raw Energy-Charts day-ahead market price as
    ``feed_in_tariff_wh``. Unlike ``ElecPriceEnergyCharts`` it intentionally
    does not add electricity import charges or VAT.
    """

    highest_orig_datetime: Optional[datetime] = None

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the Energy-Charts feed-in tariff provider."""
        return "FeedInTariffEnergyCharts"

    def _bidding_zone(self) -> str:
        settings = self.config.feedintariff.provider_settings.FeedInTariffEnergyCharts
        if settings is None:
            return EnergyChartsBiddingZones.DE_LU.value
        bidding_zone = settings.bidding_zone
        if isinstance(bidding_zone, EnergyChartsBiddingZones):
            return bidding_zone.value
        return str(bidding_zone)

    @cache_in_file(with_ttl="1 hour")
    def _request_forecast(self, start_date: Optional[str] = None) -> EnergyChartsElecPrice:
        """Fetch market price forecast data from Energy-Charts."""
        source = "https://api.energy-charts.info"
        if start_date is None:
            start_date = to_datetime(
                self.ems_start_datetime - to_duration("35 days"), as_string="YYYY-MM-DD"
            )

        last_date = to_datetime(self.end_datetime, as_string="YYYY-MM-DD")
        url = f"{source}/price?bzn={self._bidding_zone()}&start={start_date}&end={last_date}"
        response = requests.get(url, timeout=30)
        logger.debug(f"Response from {url}: {response}")
        response.raise_for_status()
        energy_charts_data = ElecPriceEnergyCharts._validate_data(response.content)
        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)
        return energy_charts_data

    def _parse_data(self, energy_charts_data: EnergyChartsElecPrice) -> pd.Series:
        series_data = pd.Series(dtype=float)
        for unix_sec, price_eur_per_mwh in zip(
            energy_charts_data.unix_seconds, energy_charts_data.price, strict=False
        ):
            orig_datetime = to_datetime(unix_sec, in_timezone=self.config.general.timezone)
            series_data.at[orig_datetime] = price_eur_per_mwh / 1_000_000
        return series_data

    def _predict_prices(self, history, slots: int, slots_per_hour: int):
        energycharts = ElecPriceEnergyCharts()
        if len(history) > 800 * slots_per_hour:
            return energycharts._predict_ets(
                history, seasonal_periods=168 * slots_per_hour, hours=slots
            )
        if len(history) > 168 * slots_per_hour:
            return energycharts._predict_ets(
                history, seasonal_periods=24 * slots_per_hour, hours=slots
            )
        if len(history) > 0:
            return energycharts._predict_median(history, hours=slots)
        logger.error("No feed-in tariff data available for Energy-Charts prediction")
        raise ValueError("No data available")

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update feed-in tariff forecast data from Energy-Charts."""
        now = pd.Timestamp.now(tz=self.config.general.timezone)
        midnight = now.normalize()
        hours_ahead = 23 if now.time() < pd.Timestamp("14:00").time() else 47
        end = midnight + pd.Timedelta(hours=hours_ahead)

        if not self.ems_start_datetime:
            raise ValueError(f"Start DateTime not set: {self.ems_start_datetime}")

        past_days = 35
        if self.highest_orig_datetime:
            history_series = self.key_to_series(
                key="feed_in_tariff_wh", start_datetime=self.ems_start_datetime
            )
            if not history_series.empty and history_series.index.min() <= self.ems_start_datetime:
                past_days = 0
            needs_update = end > self.highest_orig_datetime
        else:
            needs_update = True

        if needs_update:
            logger.info(
                "Update FeedInTariffEnergyCharts is needed, last in history: {}",
                self.highest_orig_datetime,
            )
            start_date = to_datetime(
                self.ems_start_datetime - to_duration(f"{past_days} days"),
                as_string="YYYY-MM-DD",
            )
            energy_charts_data = self._request_forecast(
                start_date=start_date, force_update=force_update
            )
            series_data = self._parse_data(energy_charts_data)
            if series_data.empty:
                raise ValueError("No Energy-Charts feed-in tariff data available")
            self.highest_orig_datetime = series_data.index.max()
            self.key_from_series("feed_in_tariff_wh", series_data)
        else:
            logger.info(
                "No update FeedInTariffEnergyCharts is needed, last in history: {}",
                self.highest_orig_datetime,
            )

        if not self.highest_orig_datetime:
            error_msg = f"Highest original datetime not available: {self.highest_orig_datetime}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        raw_series = self.key_to_series(
            key="feed_in_tariff_wh",
            end_datetime=to_datetime(self.highest_orig_datetime).add(seconds=1),
        )
        resolution_seconds = ElecPriceEnergyCharts._resolution_seconds(raw_series)
        slots_per_hour = 3600 // resolution_seconds
        history = self.key_to_array(
            key="feed_in_tariff_wh",
            end_datetime=self.highest_orig_datetime,
            interval=to_duration(f"{resolution_seconds} seconds"),
            fill_method="linear",
        )

        covered_slots = 0
        if self.highest_orig_datetime >= self.ems_start_datetime:
            covered_slots = (
                int(
                    (self.highest_orig_datetime - self.ems_start_datetime).total_seconds()
                    // resolution_seconds
                )
                + 1
            )
        needed_slots = self.config.prediction.hours * slots_per_hour - covered_slots

        if needed_slots <= 0:
            logger.warning(
                "No feed-in tariff prediction needed. needed_slots={}, hours={}, "
                "resolution_seconds={}, highest_orig_datetime={}, start_datetime={}",
                needed_slots,
                self.config.prediction.hours,
                resolution_seconds,
                self.highest_orig_datetime,
                self.ems_start_datetime,
            )
            return

        prediction = self._predict_prices(history, needed_slots, slots_per_hour)
        prediction_series = pd.Series(
            data=prediction,
            index=[
                self.highest_orig_datetime + to_duration(f"{(i + 1) * resolution_seconds} seconds")
                for i in range(len(prediction))
            ],
        )
        self.key_from_series("feed_in_tariff_wh", prediction_series)
