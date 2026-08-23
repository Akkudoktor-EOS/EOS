"""Provides fixed fee electricity fee data."""

from typing import Optional

from loguru import logger
from pydantic import Field

from akkudoktoreos.config.configabc import (
    SettingsBaseModel,
    ValueTimeWindowSequence,
)
from akkudoktoreos.prediction.elecfeeabc import ElecFeeProvider
from akkudoktoreos.utils.datetimeutil import to_duration


class ElecFeeFixedCommonSettings(SettingsBaseModel):
    """Common settings for fixed electricity fees.

    This model defines a fixed electricity fee schedule using a sequence
    of time windows. Each window specifies a time interval and the electricity
    fee applicable during that interval.
    """

    consumption_amt_kwh: ValueTimeWindowSequence = Field(
        default_factory=ValueTimeWindowSequence,
        json_schema_extra={
            "description": (
                "Sequence of time windows defining the total fixed per-kWh electricty fee "
                "charged for consumed energy, accumulating all applicable fixed "
                "per-kWh charges (e.g. network charge, metering fee, concession "
                "fee) into a single amount [amount/kWh]. If not provided, no fixed "
                "per-kWh consumption fee is applied."
            ),
            "examples": [
                {
                    "windows": [
                        {"start_time": "00:00", "duration": "8 hours", "value": 0.00288},
                        {"start_time": "08:00", "duration": "16 hours", "value": 0.0034},
                    ],
                }
            ],
        },
    )

    consumption_percent_amt: ValueTimeWindowSequence = Field(
        default_factory=ValueTimeWindowSequence,
        json_schema_extra={
            "description": (
                "Sequence of time windows defining the total fixed electricity surcharge "
                "applied as a percentage of the monetary amount already charged "
                "for consumed energy, accumulating all applicable percentage-based "
                "surcharges (e.g. VAT, electricity tax) into a single percentage "
                "[%]. This is a percentage of the fee amount, not a per-kWh rate. "
                "If not provided, no percentage-based consumption surcharge is "
                "applied."
            ),
            "examples": [
                {
                    "windows": [
                        {"start_time": "00:00", "duration": "24 hours", "value": 19.0},
                    ],
                }
            ],
        },
    )

    feedin_amt_kwh: ValueTimeWindowSequence = Field(
        default_factory=ValueTimeWindowSequence,
        json_schema_extra={
            "description": (
                "Sequence of time windows defining the total deduction from feed-in energy "
                "per Wh [amount/Wh]. This is the accumulation of all fixed per-Wh charges "
                "deducted from feed-in energy - such as metering fees or grid-operator handling "
                "charges - into a single amount. Applied after the percentage-based "
                "deduction, i.e. it reduces the price by a flat amount per Wh "
                "rather than by a share of the raw price. If not provided, no fixed per-kWh "
                "feed-in fee is applied."
            ),
            "examples": [
                {
                    "windows": [
                        {"start_time": "00:00", "duration": "8 hours", "value": 0.00288},
                        {"start_time": "08:00", "duration": "16 hours", "value": 0.0034},
                    ],
                }
            ],
        },
    )

    feedin_percent_amt: ValueTimeWindowSequence = Field(
        default_factory=ValueTimeWindowSequence,
        json_schema_extra={
            "description": (
                "Sequence of time windows defining the total percentage deducted from the raw "
                "feed-in price (spot price) [%]. This is the accumulation of all percentage-based "
                "deductions payable on the feed-in tariff - such as a marketing or balancing "
                "fee retained by the aggregator - into a single percentage. It is "
                "applied as `raw_price * (100 - percent) / 100`, i.e. it scales "
                "down the raw price rather than adding a surcharge to it. If not provided, no "
                "percentage-based feed-in deduction is applied."
            ),
            "examples": [
                {
                    "windows": [
                        {"start_time": "00:00", "duration": "24 hours", "value": 19.0},
                    ],
                }
            ],
        },
    )


class ElecFeeFixed(ElecFeeProvider):
    """Fixed fee electricity fee data.

    ElecFeeFixed is a singleton-based class that retrieves electricity fee data
    from a fixed schedule defined by time windows.

    The provider generates hourly electricity fees based on the configured time windows.
    For each hour in the forecast period, it determines which time window applies and
    assigns the corresponding fee.

    Attributes:
        time_windows: Sequence of time windows with associated electricity fees.
    """

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the ElecFeeFixed provider."""
        return "ElecFeeFixed"

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update electricity fee data from fixed schedule.

        Generates electricity fees based on the configured time windows
        at the optimization interval granularity. The fee sequence starts
        synchronized to the wall clock at the next full interval boundary.

        Args:
            force_update: If True, forces update even if data exists.

        Raises:
            ValueError: If no time windows are configured.
        """
        elecfee_spec: dict[str, ValueTimeWindowSequence] = {
            "elecfee_consumption_amt_wh": self.config.elecfee.elecfeefixed.consumption_amt_kwh,
            "elecfee_consumption_percent_amt": self.config.elecfee.elecfeefixed.consumption_percent_amt,
            "elecfee_feedin_amt_wh": self.config.elecfee.elecfeefixed.feedin_amt_kwh,
            "elecfee_feedin_percent_amt": self.config.elecfee.elecfeefixed.feedin_percent_amt,
        }

        for prediction_key, time_window_seq in elecfee_spec.items():
            if time_window_seq is None or not time_window_seq.windows:
                warning_msg = f"No time windows configured for `{prediction_key}`, defaulting to 0."
                logger.warning(warning_msg)
                await self.update_value(self.ems_start_datetime, prediction_key, 0.0)
                continue

            start_datetime = self.ems_start_datetime
            interval_seconds = 900  # Usual smallest time interval (15 min) used in electricty fees
            total_hours = self.config.prediction.hours
            interval = to_duration(interval_seconds)

            end_datetime = start_datetime.add(hours=total_hours)

            logger.debug(
                f"Generating `{prediction_key}` for {total_hours} hours "
                f"starting at {start_datetime}"
            )

            # Build the full fee array in one call — kWh values aligned to the
            # optimization grid.  to_series mirrors the key_to_series signature so
            # the grid is constructed identically to how prediction data is read.
            fees = time_window_seq.to_series(
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                interval=interval,
                dropna=True,
                boundary="context",
                align_to_interval=True,
            )

            if prediction_key.endswith("_wh"):
                # Convert kWh → Wh
                fees = fees / 1000.0

            await self.key_from_series(prediction_key, fees)

            logger.debug(f"Successfully generated {len(fees)} `{prediction_key}` entries")
