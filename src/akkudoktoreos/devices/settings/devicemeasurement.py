"""Reading device measurements for the device settings.

Measurement access is asynchronous (``DataSequence.key_to_value``), so it is
kept out of the device settings models and out of the parameter resolution.
``get_measured_value`` is the only place where device settings talk to the
measurement subsystem; the device specific modules (e.g. ``batterymeasurement``,
``invertermeasurement``) turn the measured values into operating points.
"""

from typing import Optional

from loguru import logger

from akkudoktoreos.utils.datetimeutil import DateTime, to_duration

# Maximum age of a measurement that is still taken as "current".
DEVICE_MEASUREMENT_MAX_AGE: str = "1 hour"


async def get_measured_value(key: str, target_datetime: DateTime) -> Optional[float]:
    """Return the measured value for a measurement key nearest to a datetime.

    Args:
        key: Measurement key.
        target_datetime: Datetime the value is looked up for.

    Returns:
        The value nearest to target_datetime within DEVICE_MEASUREMENT_MAX_AGE,
        or None if there is no such value or the measurement cannot be queried.
    """
    try:
        from akkudoktoreos.core.coreabc import get_measurement

        value = await get_measurement().key_to_value(
            key=key,
            target_datetime=target_datetime,
            time_window=to_duration(DEVICE_MEASUREMENT_MAX_AGE),
        )
        return None if value is None else float(value)
    except Exception as exc:  # a missing measurement falls back to the defaults
        logger.warning(f"Device measurement '{key}' not available: {exc}")
        return None
