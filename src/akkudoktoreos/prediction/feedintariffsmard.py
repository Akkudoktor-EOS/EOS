"""Provide direct-marketing feed-in prices from SMARD day-ahead data."""

from typing import Optional

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.prediction.elecpriceenergycharts import EnergyChartsElecPrice
from akkudoktoreos.prediction.elecpricesmard import ElecPriceSMARD
from akkudoktoreos.prediction.feedintariffenergycharts import FeedInTariffEnergyCharts


class FeedInTariffSMARDCommonSettings(SettingsBaseModel):
    """Settings for SMARD feed-in prices shared with ``elecprice.smard``."""

    apply_fees: bool = Field(
        default=False,
        json_schema_extra={
            "description": (
                "Apply electricity fees as given by the ElecFee provider to the feed-in tariff. "
                "Electricity fees are subtracted from the feed-in energy prices."
            ),
        },
    )


class FeedInTariffSMARD(FeedInTariffEnergyCharts):
    """Use raw SMARD day-ahead market prices for direct-marketing feed-in revenue."""

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the direct SMARD feed-in provider."""
        return "FeedInTariffSMARD"

    def _apply_fees_enabled(self) -> bool:
        """Application of fees by apply_fees() enabled."""
        return self.config.feedintariff.smard.apply_fees

    def _request_forecast(
        self, start_date: Optional[str] = None, force_update: Optional[bool] = False
    ) -> EnergyChartsElecPrice:
        """Reuse the cached direct SMARD request without import-price components."""
        return ElecPriceSMARD()._request_forecast(  # type: ignore[call-arg]
            start_date=start_date, force_update=force_update
        )
