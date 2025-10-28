"""Provides feed in tariff data."""

from typing import Optional

from loguru import logger
from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.prediction.feedintariffabc import FeedInTariffProvider
from akkudoktoreos.utils.datetimeutil import to_datetime


class FeedInTariffFixedCommonSettings(SettingsBaseModel):
    """Common settings for elecprice fixed price."""

    feed_in_tariff_kwh: Optional[float] = Field(
        default=None,
        ge=0,
        description="Electricity price feed in tariff [€/kWH].",
        examples=[0.078],
    )


class FeedInTariffFixed(FeedInTariffProvider):
    """Fixed price feed in tariff data.

    FeedInTariffFixed is a singleton-based class that retrieves elecprice data.
    """

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the FeedInTariffFixed provider."""
        return "FeedInTariffFixed"

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        error_msg = "Feed in tariff not provided"
        try:
            feed_in_tariff = (
                self.config.feedintariff.provider_settings.FeedInTariffFixed.feed_in_tariff_kwh
            )
        except:
            logger.exception(error_msg)
            raise ValueError(error_msg)
        if feed_in_tariff is None:
            logger.error(error_msg)
            raise ValueError(error_msg)
        feed_in_tariff_wh = feed_in_tariff / 1000
        self.update_value(to_datetime(), "feed_in_tariff_wh", feed_in_tariff_wh)
