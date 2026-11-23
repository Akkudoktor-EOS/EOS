"""Periodic full charge of a stationary battery.

LFP batteries can only recalibrate their state of charge close to full charge,
so many battery management systems expect a full charge every few weeks. The
last full charge is read from the measured state of charge; nothing is stored.
"""

import math
from datetime import timedelta
from typing import Any, Iterable, Optional

from akkudoktoreos.utils.datetimeutil import DateTime

# A due full charge never has to be finished in less time than this, so the
# optimizer can still pick a cheap slot even when the deadline is close or
# already overdue.
FULL_CHARGE_MIN_LEAD_HOURS = 24


def full_charge_deadline(
    samples: Iterable[tuple[DateTime, Any]],
    now: DateTime,
    interval_days: float,
    detect_percentage: float,
) -> Optional[DateTime]:
    """Return the moment by which the next full charge is due.

    Args:
        samples: Measured state of charge as (time, factor 0.0 ... 1.0) pairs,
            covering at least the last ``interval_days`` before ``now``. Samples
            after ``now`` and invalid values are ignored.
        now: Current time.
        interval_days: Maximum number of days between two full charges.
        detect_percentage: State of charge from which a sample counts as full [%].

    Returns:
        The deadline, which may already have passed, or None if there is no
        valid sample at all.

    Without a full charge among the samples, the interval counts from the oldest
    sample. A history that covers the whole interval therefore makes the full
    charge due now, while a fresh installation is not forced into a full charge
    on its first day.
    """
    interval = timedelta(days=interval_days)
    valid = [
        (date, value)
        for date, value in samples
        if value is not None
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and date.timestamp() <= now.timestamp()
    ]
    if not valid:
        return None

    full = [date for date, value in valid if value * 100 >= detect_percentage - 1e-9]
    if full:
        reference = max(full, key=lambda date: date.timestamp())
    else:
        reference = min((date for date, _ in valid), key=lambda date: date.timestamp())

    return reference + interval


def enforced_full_charge_deadline(deadline: DateTime, now: DateTime) -> DateTime:
    """Deadline handed to the optimizer, at least the minimum lead time from now.

    Whether a full charge is due is decided on the real deadline. Only the
    moment it has to be finished by is moved, so that a close or overdue full
    charge can still use the cheapest slot of the next hours.

    Args:
        deadline: Deadline from ``full_charge_deadline``.
        now: Current time.

    Returns:
        ``deadline``, or ``FULL_CHARGE_MIN_LEAD_HOURS`` after ``now`` if that is later.
    """
    earliest = now.add(hours=FULL_CHARGE_MIN_LEAD_HOURS)
    if deadline.timestamp() < earliest.timestamp():
        return earliest
    return deadline
