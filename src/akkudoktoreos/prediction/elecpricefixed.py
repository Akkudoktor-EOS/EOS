"""Provides fixed price electricity price data."""

from typing import Optional

from loguru import logger
from pydantic import Field

from akkudoktoreos.config.configabc import (
    SettingsBaseModel,
    ValueTimeWindowSequence,
)
from akkudoktoreos.prediction.elecpriceabc import ElecPriceProvider
from akkudoktoreos.utils.datetimeutil import to_duration


class ElecPriceFixedCommonSettings(SettingsBaseModel):
    """Common configuration settings for fixed electricity pricing.

    This model defines a fixed electricity price schedule using a sequence
    of time windows. Each window specifies a time interval and the electricity
    price applicable during that interval.
    """

    apply_fees: bool = Field(
        default=False,
        json_schema_extra={
            "description": (
                "Apply electricity fees as given by the ElecFee provider to the electricity prices. "
                "Electricity fees are added to the consumed energy prices."
            ),
        },
    )

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

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the ElecPriceFixed provider."""
        return "ElecPriceFixed"

    def _apply_fees_enabled(self) -> bool:
        """Application of fees by apply_fees() enabled."""
        return self.config.elecprice.elecpricefixed.apply_fees

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
        time_windows_seq: ValueTimeWindowSequence = (
            self.config.elecprice.elecpricefixed.elecprice_marketprice_amt_kwh
        )

        if time_windows_seq is None or not time_windows_seq.windows:
            warning_msg = f"No time windows configured for `{prediction_key}`, defaulting to 0."
            logger.warning(warning_msg)
            await self.update_value(self.ems_start_datetime, prediction_key, 0.0)
            return

        start_datetime = self.ems_start_datetime
        interval_seconds = 900  # Usual smallest time interval (15 min) used in electricty prices
        total_hours = self.config.prediction.hours
        interval = to_duration(interval_seconds)

        end_datetime = start_datetime.add(hours=total_hours)

        logger.debug(
            f"Generating {prediction_key} for {total_hours} hours starting at {start_datetime}"
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

        # Apply fees
        prices_wh = await self.apply_fees(prices_wh)

        await self.key_from_series(prediction_key, prices_wh)

        logger.debug(f"Successfully generated {len(prices_wh)} `{prediction_key}` entries")
