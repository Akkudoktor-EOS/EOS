"""Provide direct-marketing feed-in prices from SMARD day-ahead data."""

from typing import Optional

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.prediction.elecpriceenergycharts import EnergyChartsElecPrice
from akkudoktoreos.prediction.elecpricesmard import ElecPriceSMARD
from akkudoktoreos.prediction.feedintariffenergycharts import FeedInTariffEnergyCharts


class FeedInTariffSMARDCommonSettings(SettingsBaseModel):
    """Settings for SMARD feed-in prices shared with ``elecprice.smard``."""

    follow_elecprice: bool = Field(
        default=False,
        json_schema_extra={
            "description": (
                "Use the day-ahead market price series of the active electricity price provider "
                "as the feed-in market price instead of an own extrapolation (see "
                "'feedintariff.energycharts.follow_elecprice')."
            ),
            "examples": [True],
        },
    )


class FeedInTariffSMARD(FeedInTariffEnergyCharts):
    """Use raw SMARD day-ahead market prices for direct-marketing feed-in revenue."""

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the direct SMARD feed-in provider."""
        return "FeedInTariffSMARD"

    def _bidding_zone(self) -> str:
        """Bidding zone of the SMARD region (the region "DE" is the DE-LU day-ahead price)."""
        region = str(self.config.elecprice.smard.region)
        return "DE-LU" if region == "DE" else region

    def _follows_elecprice(self) -> bool:
        settings = self.config.feedintariff.smard
        return settings is not None and settings.follow_elecprice

    def _request_forecast(
        self, start_date: Optional[str] = None, force_update: Optional[bool] = False
    ) -> EnergyChartsElecPrice:
        """Reuse the cached direct SMARD request without import-price components."""
        return ElecPriceSMARD()._request_forecast(  # type: ignore[call-arg]
            start_date=start_date, force_update=force_update
        )
