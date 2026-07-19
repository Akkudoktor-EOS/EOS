"""Provides feed in tariff data."""

from typing import Optional

from loguru import logger
from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.prediction.feedintariffabc import FeedInTariffProvider


class FeedInTariffFixedCommonSettings(SettingsBaseModel):
    """Common settings for elecprice fixed price."""

    feed_in_tariff_kwh: Optional[float] = Field(
        default=None,
        ge=0,
        json_schema_extra={
            "description": "Electricity price feed in tariff [amount/kWh].",
            "examples": [0.078],
        },
    )


class FeedInTariffFixed(FeedInTariffProvider):
    """Fixed price feed in tariff data.

    FeedInTariffFixed is a singleton-based class that retrieves elecprice data.
    """

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the FeedInTariffFixed provider."""
        return "FeedInTariffFixed"

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update feed in tariff data with the fixed tariff.

        Fills the whole prediction horizon on the optimization interval grid,
        analogous to ElecPriceFixed. Writing only a single value at the current
        time would leave the `feed_in_tariff_wh` series without coverage of the
        parameter window used by the optimization.
        """
        error_msg = "Feed in tariff not provided"
        try:
            feed_in_tariff = (
                self.config.feedintariff.provider_settings.FeedInTariffFixed.feed_in_tariff_kwh
            )
        except Exception:
            logger.exception(error_msg)
            raise ValueError(error_msg)
        if feed_in_tariff is None:
            logger.error(error_msg)
            raise ValueError(error_msg)
        feed_in_tariff_wh = feed_in_tariff / 1000

        start_datetime = self.ems_start_datetime
        interval_seconds = self.config.optimization.interval
        total_hours = self.config.prediction.hours
        steps = int(total_hours * 3600 // interval_seconds)

        logger.debug(
            f"Generating fixed feed in tariff for {total_hours} hours starting at {start_datetime}"
        )

        for idx in range(steps):
            current_dt = start_datetime.add(seconds=idx * interval_seconds)
            await self.update_value(current_dt, "feed_in_tariff_wh", feed_in_tariff_wh)
