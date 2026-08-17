"""Provides fixed price electricity price data."""

from typing import Optional

from loguru import logger
from pydantic import Field

from akkudoktoreos.config.configabc import (
    SettingsBaseModel,
    ValueTimeWindowSequence,
)
from akkudoktoreos.prediction.elecpriceabc import ElecPriceProvider
from akkudoktoreos.utils.datetimeutil import DateTime, to_duration


class ElecPriceFixedCommonSettings(SettingsBaseModel):
    """Common configuration settings for fixed electricity pricing.

    This model defines a fixed electricity price schedule using a sequence
    of time windows. Each window specifies a time interval and the electricity
    price applicable during that interval.
    """

    elecprice_marketprice_amt_kwh: ValueTimeWindowSequence = Field(
        default_factory=ValueTimeWindowSequence,
        json_schema_extra={
            "description": (
                "Sequence of time windows defining the fixed "
                "price schedule. If not provided, no fixed pricing is applied."
            ),
            "examples": [
                {
                    "windows": [
                        {"start_time": "00:00", "duration": "8 hours", "value": 0.288},
                        {"start_time": "08:00", "duration": "16 hours", "value": 0.34},
                    ],
                }
            ],
        },
    )


class ElecPriceFixed(ElecPriceProvider):
    """Fixed price electricity price data.

    ElecPriceFixed is a singleton-based class that retrieves electricity price data
    from a fixed schedule defined by time windows.

    The provider generates hourly electricity prices based on the configured time windows.
    For each hour in the forecast period, it determines which time window applies and
    assigns the corresponding price.

    Attributes:
        time_windows: Sequence of time windows with associated electricity prices.
    """

    highest_orig_datetime: Optional[DateTime] = None

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the ElecPriceFixed provider."""
        return "ElecPriceFixed"

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update electricity price data from fixed schedule.

        Generates electricity prices based on the configured time windows
        at the optimization interval granularity. The price sequence starts
        synchronized to the wall clock at the next full interval boundary.

        Args:
            force_update: If True, forces update even if data exists.

        Raises:
            ValueError: If no time windows are configured.
        """
        prediction_key = "elecprice_marketprice_wh"
        raw_prediction_key = "elecprice_marketprice_raw_wh"
        time_windows_seq: ValueTimeWindowSequence = (
            self.config.elecprice.elecpricefixed.elecprice_marketprice_amt_kwh
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
            f"Generating {raw_prediction_key} for {total_hours} hours starting at {start_datetime}"
        )

        # Build the full price array in one call — kWh values aligned to the
        # optimization grid.  to_series mirrors the key_to_series signature so
        # the grid is constructed identically to how prediction data is read.
        prices_kwh = time_windows_seq.to_series(
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            interval=interval,
            dropna=False,
            boundary="context",
            align_to_interval=True,
        )

        # Convert kWh → Wh
        prices_wh = prices_kwh / 1000.0

        await self.key_from_series(raw_prediction_key, prices_wh)
        self.highest_orig_datetime = prices_wh.index.max()

        # Bounded to exactly the window just generated - covers the whole
        # forecast horizon in one shot, since ElecPriceFixed has no
        # fetch/predict split to worry about like the other providers.
        await self._store_gross_series(
            start_datetime=prices_wh.index.min(),
            end_datetime=prices_wh.index.max() + to_duration("1 second"),
        )

        logger.debug(f"Successfully generated {len(prices_wh)} `{prediction_key}` entries")
