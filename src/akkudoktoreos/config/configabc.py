"""Abstract and base classes for configuration."""

import calendar
import os
import sys
from typing import Any, ClassVar, Iterator, Optional, Union

import numpy as np
import pendulum
from babel.dates import get_day_names
from pydantic import Field, field_serializer, field_validator, model_validator

from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.utils.datetimeutil import (
    Date,
    DateTime,
    Duration,
    Time,
    to_duration,
)


def is_home_assistant_addon() -> bool:
    """Detect Home Assistant add-on environment.

    Home Assistant sets this environment variable automatically.
    """
    return "HASSIO_TOKEN" in os.environ or "SUPERVISOR_TOKEN" in os.environ


def runtime_environment() -> str:
    """Return a human-readable description of the runtime environment."""
    python_version = sys.version.split()[0]

    # Home Assistant add-on
    if is_home_assistant_addon():
        ha_version = os.getenv("HOMEASSISTANT_VERSION", "unknown")
        return f"Home Assistant add-on (HA {ha_version}, Python {python_version})"

    # Home Assistant Core integration
    if "HOMEASSISTANT_CONFIG" in os.environ:
        ha_version = os.getenv("HOMEASSISTANT_VERSION", "unknown")
        return f"Home Assistant Core (HA {ha_version}, Python {python_version})"

    # Docker container
    if os.path.exists("/.dockerenv"):
        return f"Docker container (Python {python_version})"

    # Default
    return f"Standalone Python (Python {python_version})"


class SettingsBaseModel(PydanticBaseModel):
    """Base model class for all settings configurations."""

    # EOS configuration - set by ConfigEOS
    config: ClassVar[Any] = None


class TimeWindow(SettingsBaseModel):
    """Model defining a daily or date time window with optional localization support.

    Represents a time interval starting at `start_time` and lasting for `duration`.
    Can restrict applicability to a specific day of the week or a specific calendar date.
    Supports day names in multiple languages via locale-aware parsing.

    Timezone contract:

    ``start_time`` is always **naive** (no ``tzinfo``).  It is interpreted as a
    local wall-clock time in whatever timezone the caller's ``date_time`` or
    ``reference_date`` carries.  When those arguments are timezone-aware the
    window boundaries are evaluated in that timezone; when they are naive,
    arithmetic is performed as-is (no timezone conversion occurs).

    ``date``, being a calendar ``Date`` object, is inherently timezone-free.

    This design avoids the ambiguity that arises when a stored ``start_time``
    carries its own timezone that differs from the caller's timezone, and keeps
    the model serialisable without timezone state.
    """

    start_time: Time = Field(
        ...,
        json_schema_extra={
            "description": (
                "Naive start time of the time window (time of day, no timezone). "
                "Interpreted in the timezone of the datetime passed to contains() "
                "or earliest_start_time()."
            ),
            "examples": [
                "00:00:00",
            ],
        },
    )
    duration: Duration = Field(
        ...,
        json_schema_extra={
            "description": "Duration of the time window starting from `start_time`.",
            "examples": [
                "2 hours",
            ],
        },
    )
    day_of_week: Optional[Union[int, str]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Optional day of the week restriction. "
                "Can be specified as integer (0=Monday to 6=Sunday) or localized weekday name. "
                "If None, applies every day unless `date` is set."
            ),
            "examples": [
                None,
            ],
        },
    )
    date: Optional[Date] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Optional specific calendar date for the time window. "
                "Naive — matched against the local date of the datetime passed to contains(). "
                "Overrides `day_of_week` if set."
            ),
            "examples": [
                None,
            ],
        },
    )
    locale: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Locale used to parse weekday names in `day_of_week` when given as string. "
                "If not set, Pendulum's default locale is used. "
                "Examples: 'en', 'de', 'fr', etc."
            ),
            "examples": [
                None,
            ],
        },
    )

    @field_validator("start_time", mode="after")
    @classmethod
    def require_naive_start_time(cls, value: Time) -> Time:
        """Strip timezone from ``start_time`` if present, emitting a debug message.

        ``start_time`` must be naive: it is interpreted as wall-clock time in
        the timezone of the ``date_time`` / ``reference_date`` supplied at call
        time.  The project's ``to_time`` helper may silently attach a timezone
        during deserialisation; rather than rejecting such values the validator
        strips the timezone and logs a debug message so the behaviour is
        transparent without breaking normal construction.

        Args:
            value: The ``Time`` value to validate.

        Returns:
            A naive ``Time`` with the same hour / minute / second / microsecond
            but no ``tzinfo``.
        """
        if value.tzinfo is not None:
            import logging

            logging.getLogger(__name__).debug(
                "TimeWindow.start_time received an aware Time (%s); "
                "stripping timezone '%s'. start_time is always interpreted "
                "as wall-clock time in the timezone of the datetime passed "
                "to contains() / earliest_start_time() / latest_start_time().",
                value,
                value.tzinfo,
            )
            value = value.replace(tzinfo=None)
        return value

    @field_validator("duration", mode="before")
    @classmethod
    def transform_to_duration(cls, value: Any) -> Duration:
        """Converts various duration formats into Duration.

        Args:
            value: The value to convert to Duration.

        Returns:
            Duration: The converted Duration object.
        """
        return to_duration(value)

    @model_validator(mode="after")
    def validate_day_of_week_with_locale(self) -> "TimeWindow":
        """Validates and normalizes the `day_of_week` field using the specified locale.

        This method supports both integer (0–6) and string inputs for ``day_of_week``.
        String inputs are matched first against English weekday names (case-insensitive),
        and then against localized weekday names using the provided ``locale``.

        If a valid match is found, ``day_of_week`` is converted to its corresponding
        integer value (0 for Monday through 6 for Sunday).

        Returns:
            TimeWindow: The validated instance with ``day_of_week`` normalized to an integer.

        Raises:
            ValueError: If ``day_of_week`` is an invalid integer (not in 0–6),
                        or an unrecognized string (not matching English or localized names),
                        or of an unsupported type.
        """
        if self.day_of_week is None:
            return self

        if isinstance(self.day_of_week, int):
            if not 0 <= self.day_of_week <= 6:
                raise ValueError("day_of_week must be in 0 (Monday) to 6 (Sunday)")
            return self

        if isinstance(self.day_of_week, str):
            # Try matching against English names first (lowercase)
            english_days = {name.lower(): i for i, name in enumerate(calendar.day_name)}
            lowercase_value = self.day_of_week.lower()
            if lowercase_value in english_days:
                self.day_of_week = english_days[lowercase_value]
                return self

            # Try localized names
            if self.locale:
                localized_days = {
                    get_day_names("wide", locale=self.locale)[i].lower(): i for i in range(7)
                }
                if lowercase_value in localized_days:
                    self.day_of_week = localized_days[lowercase_value]
                    return self

            raise ValueError(
                f"Invalid weekday name '{self.day_of_week}' for locale '{self.locale}'. "
                f"Expected English names (monday–sunday) or localized names."
            )

        raise ValueError(f"Invalid type for day_of_week: {type(self.day_of_week)}")

    @field_serializer("duration")
    def serialize_duration(self, value: Duration) -> str:
        """Serialize duration to string."""
        return str(value)

    def _window_start_end(self, reference_date: DateTime) -> tuple[DateTime, DateTime]:
        """Get the actual start and end datetimes for the time window on a given date.

        ``start_time`` is naive and is interpreted as a wall-clock time in
        the timezone of ``reference_date``.  When ``reference_date`` is
        timezone-aware the resulting window boundaries carry the same timezone;
        when it is naive the arithmetic is performed without timezone conversion.

        Args:
            reference_date: The reference date on which to calculate the window.
                May be timezone-aware or naive.

        Returns:
            tuple[DateTime, DateTime]: Start and end datetimes for the time window,
            in the same timezone as ``reference_date``.
        """
        # start_time is always naive: just replace the time components on
        # reference_date directly.  The result inherits reference_date's timezone
        # (or lack thereof) automatically.
        start = reference_date.replace(
            hour=self.start_time.hour,
            minute=self.start_time.minute,
            second=self.start_time.second,
            microsecond=self.start_time.microsecond,
        )
        end = start + self.duration
        return start, end

    def contains(self, date_time: DateTime, duration: Optional[Duration] = None) -> bool:
        """Check whether a datetime (and optional duration) fits within the time window.

        ``start_time`` is naive and is interpreted as wall-clock time in the
        timezone of ``date_time``.  Day-of-week and date constraints are
        evaluated against ``date_time`` after any timezone conversion has
        been applied.

        Args:
            date_time: The datetime to test.  May be timezone-aware or naive.
            duration: An optional duration that must fit entirely within the
                time window starting from ``date_time``.

        Returns:
            bool: True if the datetime (and optional duration) is fully
            contained in the time window, False otherwise.
        """
        # Date and weekday constraints are checked against date_time as-is;
        # since start_time is naive it is always interpreted in date_time's tz.
        if self.date and date_time.date() != self.date:
            return False

        if self.day_of_week is not None and date_time.day_of_week != self.day_of_week:
            return False

        start, end = self._window_start_end(date_time)

        if not (start <= date_time < end):
            return False

        if duration is not None:
            return date_time + duration <= end

        return True

    def earliest_start_time(
        self, duration: Duration, reference_date: Optional[DateTime] = None
    ) -> Optional[DateTime]:
        """Get the earliest datetime that allows a duration to fit within the time window.

        Args:
            duration: The duration that needs to fit within the window.
            reference_date: The date to check for the time window. Defaults to today.

        Returns:
            The earliest start time for the duration, or None if it doesn't fit.
        """
        if reference_date is None:
            reference_date = pendulum.today()

        if self.date and reference_date.date() != self.date:
            return None

        if self.day_of_week is not None and reference_date.day_of_week != self.day_of_week:
            return None

        if duration > self.duration:
            return None

        window_start, _ = self._window_start_end(reference_date)
        return window_start

    def latest_start_time(
        self, duration: Duration, reference_date: Optional[DateTime] = None
    ) -> Optional[DateTime]:
        """Get the latest datetime that allows a duration to fit within the time window.

        Args:
            duration: The duration that needs to fit within the window.
            reference_date: The date to check for the time window. Defaults to today.

        Returns:
            The latest start time for the duration, or None if it doesn't fit.
        """
        if reference_date is None:
            reference_date = pendulum.today()

        if self.date and reference_date.date() != self.date:
            return None

        if self.day_of_week is not None and reference_date.day_of_week != self.day_of_week:
            return None

        if duration > self.duration:
            return None

        window_start, window_end = self._window_start_end(reference_date)
        latest_start = window_end - duration

        if latest_start < window_start:
            return None

        return latest_start

    def can_fit_duration(
        self, duration: Duration, reference_date: Optional[DateTime] = None
    ) -> bool:
        """Check if a duration can fit within the time window on a given date.

        Args:
            duration: The duration to check.
            reference_date: The date to check for the time window. Defaults to today.

        Returns:
            bool: True if the duration can fit, False otherwise.
        """
        return self.earliest_start_time(duration, reference_date) is not None

    def available_duration(self, reference_date: Optional[DateTime] = None) -> Optional[Duration]:
        """Get the total available duration for the time window on a given date.

        Args:
            reference_date: The date to check for the time window. Defaults to today.

        Returns:
            The available duration, or None if the date doesn't match constraints.
        """
        if reference_date is None:
            reference_date = pendulum.today()

        if self.date and reference_date.date() != self.date:
            return None

        if self.day_of_week is not None and reference_date.day_of_week != self.day_of_week:
            return None

        return self.duration


class TimeWindowSequence(SettingsBaseModel):
    """Model representing a sequence of time windows with collective operations.

    Manages multiple TimeWindow objects and provides methods to work with them
    as a cohesive unit for scheduling and availability checking.
    """

    windows: list[TimeWindow] = Field(
        default_factory=list,
        json_schema_extra={"description": "List of TimeWindow objects that make up this sequence."},
    )

    def __iter__(self) -> Iterator[TimeWindow]:
        """Allow iteration over the time windows."""
        return iter(self.windows)

    def __len__(self) -> int:
        """Return the number of time windows in the sequence."""
        return len(self.windows)

    def __getitem__(self, index: int) -> TimeWindow:
        """Allow indexing into the time windows."""
        return self.windows[index]

    def contains(self, date_time: DateTime, duration: Optional[Duration] = None) -> bool:
        """Check if any time window in the sequence contains the given datetime and duration.

        Args:
            date_time: The datetime to test.
            duration: An optional duration that must fit entirely within one of the time windows.

        Returns:
            bool: True if any time window contains the datetime (and optional duration), False if no windows.
        """
        return any(window.contains(date_time, duration) for window in self.windows)

    def earliest_start_time(
        self, duration: Duration, reference_date: Optional[DateTime] = None
    ) -> Optional[DateTime]:
        """Get the earliest datetime across all windows that allows a duration to fit.

        Args:
            duration: The duration that needs to fit within a window.
            reference_date: The date to check for the time windows. Defaults to today.

        Returns:
            The earliest start time across all windows, or None if no window can fit the duration.
        """
        if not self.windows:
            return None

        if reference_date is None:
            reference_date = pendulum.today()

        earliest_times = [
            t
            for window in self.windows
            if (t := window.earliest_start_time(duration, reference_date)) is not None
        ]
        return min(earliest_times) if earliest_times else None

    def latest_start_time(
        self, duration: Duration, reference_date: Optional[DateTime] = None
    ) -> Optional[DateTime]:
        """Get the latest datetime across all windows that allows a duration to fit.

        Args:
            duration: The duration that needs to fit within a window.
            reference_date: The date to check for the time windows. Defaults to today.

        Returns:
            The latest start time across all windows, or None if no window can fit the duration.
        """
        if not self.windows:
            return None

        if reference_date is None:
            reference_date = pendulum.today()

        latest_times = [
            t
            for window in self.windows
            if (t := window.latest_start_time(duration, reference_date)) is not None
        ]
        return max(latest_times) if latest_times else None

    def can_fit_duration(
        self, duration: Duration, reference_date: Optional[DateTime] = None
    ) -> bool:
        """Check if the duration can fit within any time window in the sequence.

        Args:
            duration: The duration to check.
            reference_date: The date to check for the time windows. Defaults to today.

        Returns:
            bool: True if any window can fit the duration, False if no windows.
        """
        return any(window.can_fit_duration(duration, reference_date) for window in self.windows)

    def available_duration(self, reference_date: Optional[DateTime] = None) -> Optional[Duration]:
        """Get the total available duration across all applicable windows.

        Args:
            reference_date: The date to check for the time windows. Defaults to today.

        Returns:
            The sum of available durations from all applicable windows, or None if no windows apply.
        """
        if not self.windows:
            return None

        if reference_date is None:
            reference_date = pendulum.today()

        durations = [
            d
            for window in self.windows
            if (d := window.available_duration(reference_date)) is not None
        ]
        if not durations:
            return None
        total = Duration()
        for d in durations:
            total += d
        return total

    def get_applicable_windows(self, reference_date: Optional[DateTime] = None) -> list[TimeWindow]:
        """Get all windows that apply to the given reference date.

        Args:
            reference_date: The date to check for the time windows. Defaults to today.

        Returns:
            List of TimeWindow objects that apply to the reference date.
        """
        if reference_date is None:
            reference_date = pendulum.today()

        return [
            window
            for window in self.windows
            if window.available_duration(reference_date) is not None
        ]

    def find_windows_for_duration(
        self, duration: Duration, reference_date: Optional[DateTime] = None
    ) -> list[TimeWindow]:
        """Find all windows that can accommodate the given duration.

        Args:
            duration: The duration that needs to fit.
            reference_date: The date to check for the time windows. Defaults to today.

        Returns:
            List of TimeWindow objects that can fit the duration.
        """
        if reference_date is None:
            reference_date = pendulum.today()

        return [
            window for window in self.windows if window.can_fit_duration(duration, reference_date)
        ]

    def get_all_possible_start_times(
        self, duration: Duration, reference_date: Optional[DateTime] = None
    ) -> list[tuple[DateTime, DateTime, TimeWindow]]:
        """Get all possible start time ranges for a duration across all windows.

        Args:
            duration: The duration that needs to fit.
            reference_date: The date to check for the time windows. Defaults to today.

        Returns:
            List of tuples containing (earliest_start, latest_start, window) for each
            window that can accommodate the duration.
        """
        if reference_date is None:
            reference_date = pendulum.today()

        result = []
        for window in self.windows:
            earliest = window.earliest_start_time(duration, reference_date)
            latest = window.latest_start_time(duration, reference_date)
            if earliest is not None and latest is not None:
                result.append((earliest, latest, window))
        return result

    def to_array(
        self,
        start_datetime: DateTime,
        end_datetime: DateTime,
        interval: Duration,
        dropna: bool = True,
        boundary: str = "context",
        align_to_interval: bool = True,
    ) -> np.ndarray:
        """Return a 1-D NumPy array indicating window coverage over a time grid.

        The time grid is constructed from ``start_datetime`` to ``end_datetime``
        (exclusive) in steps of ``interval``, matching the ``key_to_array``
        signature used by the prediction store.  Each element is ``1.0`` when
        the corresponding step falls inside any window in this sequence, and
        ``0.0`` otherwise.

        Parameters mirror ``key_to_array`` so that ``to_array`` can be used as
        a drop-in source in the same contexts:

        Args:
            start_datetime: First step of the time grid (inclusive).
            end_datetime: Upper bound of the time grid (exclusive).
            interval: Fixed step size between consecutive grid points.
            dropna: Unused for ``TimeWindowSequence`` (no NaN values are
                produced — every step is either ``0.0`` or ``1.0``). Accepted
                for signature compatibility.
            boundary: Controls range enforcement.  Only ``"context"`` is
                currently supported; the output is always clipped to
                ``[start_datetime, end_datetime)``.
            align_to_interval: When ``True``, ``start_datetime`` is floored to
                the nearest interval boundary in wall-clock time before
                generating the grid (e.g. 08:10 with a 1-hour interval becomes
                08:00).  The timezone (or naivety) of ``start_datetime`` is
                preserved exactly — no UTC conversion is performed.  When
                ``False``, ``start_datetime`` is used as-is.

        Returns:
            ``np.ndarray`` of shape ``(n_steps,)`` with ``dtype=float64``.
            ``1.0`` at position ``i`` means step ``i`` is inside a window;
            ``0.0`` means it is not.

        Raises:
            ValueError: If ``boundary`` is not ``"context"``.
        """
        if boundary != "context":
            raise ValueError(f"Unsupported boundary {boundary!r}. Only 'context' is supported.")

        interval_s = interval.total_seconds()

        if align_to_interval and interval_s > 0:
            # Floor purely in wall-clock seconds so the timezone (or naivety)
            # of start_datetime is never touched and no UTC conversion occurs.
            # This is correct regardless of the machine's local timezone.
            wall_s = (
                start_datetime.hour * 3600
                + start_datetime.minute * 60
                + start_datetime.second
                + start_datetime.microsecond / 1_000_000
            )
            remainder_s = wall_s % interval_s
            if remainder_s:
                start_datetime = start_datetime.subtract(seconds=remainder_s)

        result = []
        current = start_datetime
        while current < end_datetime:
            result.append(1.0 if self.contains(current) else 0.0)
            current = current.add(seconds=interval_s)

        return np.array(result, dtype=np.float64)

    def add_window(self, window: TimeWindow) -> None:
        """Add a new time window to the sequence.

        Args:
            window: The TimeWindow to add.
        """
        self.windows.append(window)

    def remove_window(self, index: int) -> TimeWindow:
        """Remove a time window from the sequence by index.

        Args:
            index: The index of the window to remove.

        Returns:
            The removed TimeWindow.

        Raises:
            IndexError: If the index is out of range.
        """
        if not self.windows:
            raise IndexError("pop from empty list")
        return self.windows.pop(index)

    def clear_windows(self) -> None:
        """Remove all windows from the sequence."""
        self.windows.clear()

    def sort_windows_by_start_time(self, reference_date: Optional[DateTime] = None) -> None:
        """Sort the windows by their start time on the given reference date.

        Windows that don't apply to the reference date are placed at the end.

        Args:
            reference_date: The date to use for sorting. Defaults to today.
        """
        if not self.windows:
            return

        if reference_date is None:
            reference_date = pendulum.today()

        def sort_key(window: TimeWindow) -> tuple[int, DateTime]:
            start_time = window.earliest_start_time(Duration(), reference_date)
            if start_time is None:
                return (1, reference_date)
            return (0, start_time)

        self.windows.sort(key=sort_key)


class ValueTimeWindow(TimeWindow):
    """Value applicable during a specific time window.

    This model extends `TimeWindow` by associating a value with the defined time interval.
    """

    value: Optional[float] = Field(
        default=None,
        ge=0,
        json_schema_extra={
            "description": ("Value applicable during this time window."),
            "examples": [0.288],
        },
    )


class ValueTimeWindowSequence(TimeWindowSequence):
    """Sequence of value time windows.

    This model specializes `TimeWindowSequence` to ensure that all
    contained windows are instances of `ValueTimeWindow`.
    It provides the full set of sequence operations (containment checks,
    availability, start time calculations) for value windows.
    """

    windows: list[ValueTimeWindow] = Field(
        default_factory=list,
        json_schema_extra={
            "description": (
                "Ordered list of value time windows. "
                "Each window defines a time interval and an associated value."
            ),
        },
    )

    def get_value_for_datetime(self, dt: DateTime) -> float:
        """Get value for a specific datetime.

        Args:
            dt: Datetime to get value for.

        Returns:
            float: value or 0.0 if no window matches.
        """
        for window in self.windows:
            if window.contains(dt):
                return window.value or 0.0
        return 0.0

    def to_array(
        self,
        start_datetime: DateTime,
        end_datetime: DateTime,
        interval: Duration,
        dropna: bool = True,
        boundary: str = "context",
        align_to_interval: bool = True,
    ) -> np.ndarray:
        """Return a 1-D NumPy array of window values over a time grid.

        The time grid is constructed from ``start_datetime`` to ``end_datetime``
        (exclusive) in steps of ``interval``, matching the ``key_to_array``
        signature used by the prediction store.  Each element holds the
        ``value`` of the first matching window at that step, ``0.0`` when no
        window matches, or ``NaN`` when the matching window has ``value=None``
        and ``dropna=False``.

        When ``dropna=True`` steps whose matching window has ``value=None`` are
        omitted from the output entirely (the array is shorter than the full
        grid), consistent with the ``key_to_array`` ``dropna`` contract.

        Parameters mirror ``key_to_array`` so that ``to_array`` can be used as
        a drop-in source in the same contexts:

        Args:
            start_datetime: First step of the time grid (inclusive).
            end_datetime: Upper bound of the time grid (exclusive).
            interval: Fixed step size between consecutive grid points.
            dropna: When ``True``, steps whose matching window carries
                ``value=None`` are dropped from the output array.  When
                ``False``, those steps emit ``NaN``.
            boundary: Controls range enforcement.  Only ``"context"`` is
                currently supported; the output is always clipped to
                ``[start_datetime, end_datetime)``.
            align_to_interval: When ``True``, ``start_datetime`` is floored to
                the nearest interval boundary in wall-clock time before
                generating the grid (e.g. 08:10 with a 1-hour interval becomes
                08:00).  The timezone (or naivety) of ``start_datetime`` is
                preserved exactly — no UTC conversion is performed.  When
                ``False``, ``start_datetime`` is used as-is.

        Returns:
            ``np.ndarray`` of shape ``(n_steps,)`` with ``dtype=float64``.
            Positive values are window values; ``0.0`` means no window matched;
            ``NaN`` means a window matched but its value was ``None`` (only
            when ``dropna=False``).

        Raises:
            ValueError: If ``boundary`` is not ``"context"``.
        """
        if boundary != "context":
            raise ValueError(f"Unsupported boundary {boundary!r}. Only 'context' is supported.")

        interval_s = interval.total_seconds()

        if align_to_interval and interval_s > 0:
            # Floor purely in wall-clock seconds so the timezone (or naivety)
            # of start_datetime is never touched and no UTC conversion occurs.
            # This is correct regardless of the machine's local timezone.
            wall_s = (
                start_datetime.hour * 3600
                + start_datetime.minute * 60
                + start_datetime.second
                + start_datetime.microsecond / 1_000_000
            )
            remainder_s = wall_s % interval_s
            if remainder_s:
                start_datetime = start_datetime.subtract(seconds=remainder_s)

        result = []
        current = start_datetime
        while current < end_datetime:
            step_value: Optional[float] = None
            matched = False
            for window in self.windows:
                if window.contains(current):
                    step_value = window.value
                    matched = True
                    break

            if not matched:
                result.append(0.0)
            elif step_value is None:
                if not dropna:
                    result.append(float("nan"))
                # else: omit this step entirely (dropna=True)
            else:
                result.append(step_value)

            current = current.add(seconds=interval_s)

        return np.array(result, dtype=np.float64)
