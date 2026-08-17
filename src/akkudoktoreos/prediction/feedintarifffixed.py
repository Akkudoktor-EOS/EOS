"""Provides feed in tariff data."""

from typing import Optional

from loguru import logger
from pydantic import Field

from akkudoktoreos.config.configabc import (
    SettingsBaseModel,
    ValueTimeWindowSequence,
)
from akkudoktoreos.prediction.feedintariffabc import FeedInTariffProvider
from akkudoktoreos.utils.datetimeutil import DateTime, to_duration


class FeedInTariffFixedCommonSettings(SettingsBaseModel):
    """Common settings for elecprice fixed price."""

    feed_in_tariff_amt_kwh: ValueTimeWindowSequence = Field(
        default_factory=ValueTimeWindowSequence,
        json_schema_extra={
            "description": (
                "Sequence of time windows defining the electricity feed in tariff [amount/kWh]. "
                "If not provided, no fixed feed in tariff is applied."
            ),
            "examples": [
                {
                    "windows": [
                        {"start_time": "00:00", "duration": "8 hours", "value": 0.028},
                        {"start_time": "08:00", "duration": "16 hours", "value": 0.034},
                    ],
                }
            ],
        },
    )


class FeedInTariffFixed(FeedInTariffProvider):
    """Fixed price feed in tariff data.

    FeedInTariffFixed is a singleton-based class that retrieves elecprice data.
    """

    highest_orig_datetime: Optional[DateTime] = None

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the FeedInTariffFixed provider."""
        return "FeedInTariffFixed"

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update feed-in tariff data from fixed schedule.

        Generates feed-in tariff based on the configured time windows
        at the optimization interval granularity. The tariff sequence starts
        synchronized to the wall clock at the next full interval boundary.

        Args:
            force_update: If True, forces update even if data exists.

        Raises:
            ValueError: If no time windows are configured.
        """
        prediction_key = "feed_in_tariff_wh"
        raw_prediction_key = "feed_in_tariff_raw_wh"
        time_windows_seq: ValueTimeWindowSequence = (
            self.config.feedintariff.feedintarifffixed.feed_in_tariff_amt_kwh
        )

        start_datetime = self.ems_start_datetime
        interval_seconds = 900  # Usual smallest time interval (15 min) used in electricty prices
        total_hours = self.config.prediction.hours
        interval = to_duration(interval_seconds)
        end_datetime = start_datetime.add(hours=total_hours)

        if time_windows_seq is None or not time_windows_seq.windows:
            warning_msg = f"No time windows configured for `{raw_prediction_key}`, defaulting to 0."
            logger.warning(warning_msg)
            # Store two values to have a default interval to be used by _apply_fees
            end_datetime = start_datetime + interval
            await self.update_value(start_datetime, raw_prediction_key, 0.0)
            await self.update_value(end_datetime, raw_prediction_key, 0.0)
            self.highest_orig_datetime = end_datetime
            await self._store_gross_series(
                start_datetime=start_datetime,
                end_datetime=end_datetime + to_duration("1 second"),
            )
            return

        logger.debug(
            f"Generating {prediction_key} for {total_hours} hours starting at {start_datetime}"
        )

        # Build the full tariff array in one call — kWh values aligned to the
        # optimization grid.  to_series mirrors the key_to_series signature so
        # the grid is constructed identically to how prediction data is read.
        tariffs_kwh = time_windows_seq.to_series(
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            interval=interval,
            dropna=False,
            boundary="context",
            align_to_interval=True,
        )

        # Convert kWh → Wh
        tariffs_wh = tariffs_kwh / 1000.0

        await self.key_from_series(raw_prediction_key, tariffs_wh)
        self.highest_orig_datetime = tariffs_wh.index.max()

        # Bounded to exactly the window just generated - covers the whole
        # forecast horizon in one shot, since FeedInTariffFixed has no
        # fetch/predict split to worry about like the other providers.
        await self._store_gross_series(
            start_datetime=tariffs_wh.index.min(),
            end_datetime=tariffs_wh.index.max() + to_duration("1 second"),
        )

        logger.debug(f"Successfully generated {len(tariffs_wh)} `{prediction_key}` entries")
