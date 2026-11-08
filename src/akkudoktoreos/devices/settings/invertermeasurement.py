"""Reading the inverter operating point from measurements.

``measure_operating_point`` turns the measured ambient temperature of one
inverter into an ``InverterOperatingPoint``. ``get_measured_value`` (from
``devicemeasurement``) talks to the measurement subsystem.
"""

import math
from typing import TYPE_CHECKING, Optional

from akkudoktoreos.devices.settings.devicemeasurement import (
    get_measured_value as get_measured_value,
)
from akkudoktoreos.devices.settings.inverterresolution import (
    INVERTER_DEFAULT_TEMPERATURE_DEGC,
    InverterOperatingPoint,
)
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime

if TYPE_CHECKING:
    from akkudoktoreos.devices.settings.invertersettings import InverterCommonSettings


def _temperature_or_default(value: Optional[float]) -> float:
    """Return a measured ambient temperature [degC], or the default."""
    if value is None or not math.isfinite(value):
        return INVERTER_DEFAULT_TEMPERATURE_DEGC
    return value


async def measure_operating_point(
    settings: "InverterCommonSettings", target_datetime: Optional[DateTime] = None
) -> InverterOperatingPoint:
    """Read the current operating point of an inverter from its measurements.

    A missing, stale, non-finite or unavailable temperature measurement falls back
    to ``INVERTER_DEFAULT_TEMPERATURE_DEGC`` (45 degC). This function never raises
    for missing measurements.

    Args:
        settings: Inverter device settings, providing the measurement key.
        target_datetime: Datetime the measurement is looked up for, now if None.

    Returns:
        The measured operating point.
    """
    target = to_datetime(target_datetime) if target_datetime is not None else to_datetime()
    temperature = await get_measured_value(settings.measurement_key_temperature_degc, target)
    return InverterOperatingPoint(temperature_degC=_temperature_or_default(temperature))
