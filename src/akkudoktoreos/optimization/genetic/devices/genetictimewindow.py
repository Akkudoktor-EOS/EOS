"""Time windows of the GENETIC home appliance simulation.

GENETIC's own copy of the window logic it needs from ``config.configabc`` (ADR 0004: optimizers
duplicate code instead of sharing it), on frozen dataclasses instead of settings models. The
request's ``TimeWindow`` models derive them; the semantics are the same:

- ``start_time`` is a naive wall-clock time, interpreted in the timezone of the datetime it is
  compared with.
- Date, weekday and month restrictions apply to the calendar date the window starts on.
- The end is exclusive: ``end_time`` is the first occurrence of that clock time strictly after
  the start; the window ends at the later of ``end_time`` and ``start_time + duration``.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

import numpy as np

from akkudoktoreos.utils.datetimeutil import Date, DateTime, Duration, Time


@dataclass(frozen=True, slots=True)
class GeneticTimeWindow:
    """A time window of the simulation, derived from a request's time window.

    Attributes:
        start_time: Naive local start time.
        duration: Length of the window.
        end_time: Naive local end time; ``None`` uses the duration only.
        day_of_week: Weekday the window starts on, 0 (Monday) to 6 (Sunday); ``None`` any day.
        date: Calendar date the window starts on; ``None`` any date.
        month: Month the window starts in, 1 to 12; ``None`` any month.
        value: Value of the window; for cycle windows the 0-based cycle index, ``None`` belongs
            to no cycle.
    """

    start_time: Time
    duration: Duration
    end_time: Optional[Time] = None
    day_of_week: Optional[int] = None
    date: Optional[Date] = None
    month: Optional[int] = None
    value: Optional[float] = None

    def start_end(self, reference_date: DateTime) -> tuple[DateTime, DateTime]:
        """Return the start and exclusive end of the window opening on a date.

        Args:
            reference_date: Datetime identifying the calendar date the window starts on. Its
                time of day is ignored.

        Returns:
            The start datetime and the exclusive end datetime.
        """
        start_day = reference_date.start_of("day")

        # Use Pendulum's default (second occurrence) for ambiguous boundaries,
        # independently of the fold carried by the reference datetime.
        start = start_day.replace(
            hour=self.start_time.hour,
            minute=self.start_time.minute,
            second=self.start_time.second,
            microsecond=self.start_time.microsecond,
            fold=1,
        )

        duration_end = start + self.duration

        if self.end_time is None:
            return start, duration_end

        start_clock = (
            self.start_time.hour,
            self.start_time.minute,
            self.start_time.second,
            self.start_time.microsecond,
        )
        end_clock = (
            self.end_time.hour,
            self.end_time.minute,
            self.end_time.second,
            self.end_time.microsecond,
        )

        # Choose the date before setting the time: a nonexistent clock time on
        # the start day must not be normalized and carried into the following day.
        end_day = start_day.add(days=1) if end_clock <= start_clock else start_day
        explicit_end = end_day.replace(
            hour=self.end_time.hour,
            minute=self.end_time.minute,
            second=self.end_time.second,
            microsecond=self.end_time.microsecond,
            fold=1,
        )

        # Same-timezone datetime ordering ignores fold during the repeated DST
        # hour. Pendulum subtraction measures elapsed time between the instants.
        end = explicit_end if (explicit_end - duration_end).total_seconds() > 0 else duration_end
        return start, end

    def matches_start_date(self, start_date: DateTime) -> bool:
        """Whether a window opening on ``start_date`` satisfies the date restrictions.

        Args:
            start_date: Datetime identifying the candidate start date; its time is ignored.

        Returns:
            True if all configured date restrictions match.
        """
        if self.date is not None and start_date.date() != self.date:
            return False
        if self.day_of_week is not None and start_date.day_of_week != self.day_of_week:
            return False
        if self.month is not None and start_date.month != self.month:
            return False
        return True

    def contains(self, date_time: DateTime) -> bool:
        """Whether a datetime lies within an occurrence of the window.

        Args:
            date_time: The datetime to test; its timezone interprets the wall-clock times.

        Returns:
            True if an applicable occurrence of the window contains the datetime.
        """
        current_day = date_time.start_of("day")

        # Include earlier starts for overnight and multi-day durations. One
        # extra calendar day covers shorter days at daylight saving transitions.
        days_back = max(1, math.ceil(self.duration.total_seconds() / 86400) + 1)
        for offset in range(days_back + 1):
            start_day = current_day.subtract(days=offset)
            if not self.matches_start_date(start_day):
                continue
            start, end = self.start_end(start_day)
            if (date_time - start).total_seconds() < 0 or (end - date_time).total_seconds() <= 0:
                continue
            return True
        return False


def windows_to_array(
    windows: Sequence[GeneticTimeWindow],
    start_datetime: DateTime,
    end_datetime: DateTime,
    interval: Duration,
) -> np.ndarray:
    """Return 1.0 for each grid step inside any of the windows, else 0.0.

    The grid runs from ``start_datetime`` (inclusive) to ``end_datetime`` (exclusive) in steps of
    ``interval``; ``start_datetime`` is used as given.

    Args:
        windows: The windows.
        start_datetime: First step of the grid.
        end_datetime: Upper bound of the grid (exclusive).
        interval: Step size.

    Returns:
        Array of shape ``(n_steps,)``, dtype float64.
    """
    interval_s = interval.total_seconds()
    result = []
    current = start_datetime
    while (end_datetime - current).total_seconds() > 0:
        result.append(1.0 if any(window.contains(current) for window in windows) else 0.0)
        current = current.add(seconds=interval_s)
    return np.array(result, dtype=np.float64)


def cycles_to_matrix(
    windows: Sequence[GeneticTimeWindow],
    start_datetime: DateTime,
    end_datetime: DateTime,
    interval: Duration,
) -> tuple[list[int], np.ndarray]:
    """Return the cycles of the windows and, per cycle, which grid steps its windows cover.

    A window's cycle is the integer part of its ``value``; windows without a value are ignored.
    ``start_datetime`` is floored to the interval in wall-clock time; the step count rounds up.

    Args:
        windows: Cycle windows.
        start_datetime: First step of the grid (inclusive).
        end_datetime: Upper bound of the grid (exclusive).
        interval: Step size.

    Returns:
        The sorted cycle indices and a float64 matrix of shape ``(len(cycles), n_steps)``.
    """
    interval_s = interval.total_seconds()

    if interval_s > 0:
        wall_s = (
            start_datetime.hour * 3600
            + start_datetime.minute * 60
            + start_datetime.second
            + start_datetime.microsecond / 1_000_000
        )
        remainder_s = wall_s % interval_s
        if remainder_s:
            start_datetime = start_datetime.subtract(seconds=remainder_s)

    cycles = sorted({int(window.value) for window in windows if window.value is not None})
    steps = (
        math.ceil((end_datetime - start_datetime).total_seconds() / interval_s)
        if interval_s > 0
        else 0
    )
    matrix = np.zeros((len(cycles), steps), dtype=np.float64)
    if interval_s <= 0:
        return cycles, matrix
    for row, cycle in enumerate(cycles):
        matrix[row] = windows_to_array(
            [w for w in windows if w.value is not None and int(w.value) == cycle],
            start_datetime,
            end_datetime,
            interval,
        )
    return cycles, matrix
