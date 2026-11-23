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

from dataclasses import dataclass
from typing import Optional

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
