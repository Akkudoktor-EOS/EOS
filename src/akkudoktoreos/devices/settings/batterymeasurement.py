"""Reading the battery operating point from measurements.

Measurement access is asynchronous (``DataSequence.key_to_value``), so it is
kept out of the battery settings model and out of the parameter resolution.
``get_measured_value`` is the only place that talks to the measurement
subsystem; ``measure_operating_point`` turns the measured values of one battery
into a ``BatteryOperatingPoint``.
"""

import math
from typing import TYPE_CHECKING, Optional

from loguru import logger

from akkudoktoreos.devices.settings.batteryresolution import (
    BATTERY_DEFAULT_SOC_FACTOR,
    BATTERY_DEFAULT_TEMPERATURE_DEGC,
    BatteryOperatingPoint,
)
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime, to_duration

if TYPE_CHECKING:
    from akkudoktoreos.devices.settings.batterysettings import BatteriesCommonSettings


# Maximum age of a measurement that is still taken as "current".
BATTERY_MEASUREMENT_MAX_AGE: str = "1 hour"


async def get_measured_value(key: str, target_datetime: DateTime) -> Optional[float]:
    """Return the measured value for a measurement key nearest to a datetime.

    Args:
        key: Measurement key.
        target_datetime: Datetime the value is looked up for.

    Returns:
        The value nearest to target_datetime within BATTERY_MEASUREMENT_MAX_AGE,
        or None if there is no such value or the measurement cannot be queried.
    """
    try:
        from akkudoktoreos.core.coreabc import get_measurement

        value = await get_measurement().key_to_value(
            key=key,
            target_datetime=target_datetime,
            time_window=to_duration(BATTERY_MEASUREMENT_MAX_AGE),
        )
        return None if value is None else float(value)
    except Exception as exc:  # a missing measurement falls back to the defaults
        logger.warning(f"Battery measurement '{key}' not available: {exc}")
        return None


def _soc_factor_or_default(value: Optional[float]) -> float:
    """Return a measured SoC factor clamped to [0, 1], or the default."""
    if value is None or not math.isfinite(value):
        return BATTERY_DEFAULT_SOC_FACTOR
    return min(max(value, 0.0), 1.0)


def _temperature_or_default(value: Optional[float]) -> float:
    """Return a measured battery temperature [degC], or the default."""
    if value is None or not math.isfinite(value):
        return BATTERY_DEFAULT_TEMPERATURE_DEGC
    return value


def _power_magnitude_or_none(value: Optional[float]) -> Optional[float]:
    """Return a measured power as positive magnitude [W], or None.

    A missing, non-finite or zero measurement means the battery is not
    operating in that direction, so there is no meaningful C-rate.
    """
    if value is None or not math.isfinite(value) or value == 0.0:
        return None
    return abs(value)


async def measure_operating_point(
    settings: "BatteriesCommonSettings", target_datetime: Optional[DateTime] = None
) -> BatteryOperatingPoint:
    """Read the current operating point of a battery from its measurements.

    Missing, non-finite or unavailable measurements fall back to the reference
    operating point (25 degC, SoC 0.5, no power); the SoC factor is clamped to
    [0.0 ... 1.0]. This function never raises for missing measurements.

    Args:
        settings: Battery device settings, providing the measurement keys.
        target_datetime: Datetime the measurements are looked up for, now if None.

    Returns:
        The measured operating point.
    """
    target = to_datetime(target_datetime) if target_datetime is not None else to_datetime()
    temperature = await get_measured_value(settings.measurement_key_temperature_degc, target)
    soc_factor = await get_measured_value(settings.measurement_key_soc_factor, target)
    charge_power = await get_measured_value(settings.measurement_key_charge_power_w, target)
    discharge_power = await get_measured_value(settings.measurement_key_discharge_power_w, target)
    return BatteryOperatingPoint(
        temperature_degC=_temperature_or_default(temperature),
        soc_factor=_soc_factor_or_default(soc_factor),
        charge_power_w=_power_magnitude_or_none(charge_power),
        discharge_power_w=_power_magnitude_or_none(discharge_power),
    )
