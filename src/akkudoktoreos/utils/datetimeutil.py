"""Utility module for date, time, and timezone handling.

This module provides a unified interface for working with dates, times, durations, and timezones.
It leverages the `pendulum` library to simplify conversions between string representations,
native `datetime`/`date`/`timedelta` types, Unix timestamps, and timezone-aware types.

Features:
---------
- Parse and normalize various date or timestamp formats into a `pendulum.DateTime`.
- Convert durations from strings or numerics into `pendulum.Duration`.
- Infer timezone from UTC offset or geolocation.
- Support for custom output formats (ISO 8601, UTC normalized, or user-specified formats).
- Makes pendulum types usable in pydantic models using `pydantic_extra_types.pendulum_dt`
  and the `Time` class.

Types:
------
- `Time`: Pendulum's time type with timezone awareness.
- `DateTime`: Pendulum's timezone-aware datetime type.
- `Date`: Pendulum's date type.
- `Duration`: Pendulum's representation of a time delta.
- `TimeWindow`: Daily or specific date time window with optional localization support.

Functions:
----------
- `to_time`: Convert diverse time inputs into a `Time` or formatted string.
- `to_datetime`: Convert diverse date/time inputs into a `DateTime` or formatted string.
- `to_duration`: Convert strings or numerics into a `Duration`.
- `to_timezone`: Convert a UTC offset or geographic coordinate into a `Timezone` or its name.

Usage Examples:
---------------
    >>> to_time("15:30:00", in_timezone="Europe/Berlin")
    Time(17, 30, 0, tzinfo=Timezone('Europe/Berlin'))

    >>> to_datetime("2024-10-13T15:30:00", in_timezone="Europe/Berlin")
    DateTime(2024, 10, 13, 17, 30, 0, tzinfo=Timezone('Europe/Berlin'))

    >>> to_duration("2 days 5 hours")
    Duration(days=2, hours=5)

    >>> to_timezone(location=(40.7128, -74.0060), as_string=True)
    'America/New_York'

See each function's docstring for detailed argument options and examples.
"""

import calendar
import datetime
import re
from typing import Any, Iterator, List, Literal, Optional, Tuple, Union, overload

import pendulum
from babel.dates import get_day_names
from loguru import logger
from pendulum.tz.timezone import Timezone
from pydantic import (
    BaseModel,
    Field,
    GetCoreSchemaHandler,
    field_serializer,
    field_validator,
    model_validator,
)
from pydantic_core import core_schema
from pydantic_extra_types.pendulum_dt import (  # make pendulum types pydantic
    Date,
    DateTime,
    Duration,
)
from tzfpy import get_tz

MAX_DURATION_STRING_LENGTH = 350


class Time(pendulum.Time):
    """A timezone-aware Time class derived from pendulum.Time.

    Provides methods to get hour information for specific timezones
    with local timezone as default.
    Makes Time handled by pydantic.
    """

    def __new__(
        cls,
        *args: Any,
        tzinfo: Optional[Union[datetime.tzinfo, pendulum.Timezone, str]] = None,
        **kwargs: Any,
    ) -> "Time":
        """Create a new Time instance with optional tzinfo parameter.

        Args:
            *args: Positional arguments passed to pendulum.Time
            tzinfo: Optional timezone information - can be:
                   - datetime.tzinfo object
                   - pendulum.Timezone object
                   - string (timezone name like 'UTC', 'Europe/Berlin')
            **kwargs: Keyword arguments passed to pendulum.Time
        """
        # Extract tzinfo from args if one of them is a Time-like object
        for arg in args:
            if isinstance(arg, (pendulum.Time, Time)) and arg.tzinfo:
                tzinfo = tzinfo or arg.tzinfo

        # Check if tzinfo is already in kwargs, use it if our tzinfo param is None
        existing_tzinfo = kwargs.pop("tzinfo", None)
        if tzinfo is None and existing_tzinfo is not None:
            tzinfo = existing_tzinfo

        # Create the base instance without tzinfo (pendulum.Time does not understand),
        # but with pydantic validation
        instance = super().__new__(cls, *args, **kwargs)

        if tzinfo is not None:
            # Convert string timezone names to pendulum timezone
            if isinstance(tzinfo, str):
                tzinfo = pendulum.timezone(tzinfo)
            # Convert datetime.tzinfo to pendulum timezone if needed
            elif isinstance(tzinfo, datetime.tzinfo) and not isinstance(tzinfo, pendulum.Timezone):
                # For standard datetime tzinfo, we need to handle conversion
                # This is a simplified approach - you might need more sophisticated handling
                tzinfo = pendulum.timezone(str(tzinfo))

            # Use pendulum.Time.replace() directly to avoid recursive pydantic validation
            pend_instance = pendulum.Time(*args, **kwargs)
            pend_instance = pend_instance.replace(tzinfo=tzinfo)
            # Create Time instance from pendulum Time instance to avoid recursive pydantic validation
            instance = cls._create_from_pendulum_time(pend_instance)

        return instance

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: type, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_after_validator_function(
            cls._validate,
            core_schema.any_schema(),  # Accept any input, let _validate handle the logic
            serialization=core_schema.plain_serializer_function_ser_schema(
                cls._serialize,
                return_schema=core_schema.str_schema(),
            ),
        )

    @classmethod
    def _validate(cls, value: Any) -> "Time":
        """Validate input value and convert to Time instance using to_time function."""
        if isinstance(value, cls):
            return value

        # Handle None values explicitly
        if value is None:
            raise ValueError("Time value cannot be None")

        try:
            # Use to_time function to convert the value
            time_obj = to_time(value, to_naive=False)

            # If to_time returned a string (shouldn't happen)
            if isinstance(time_obj, str):
                raise ValueError(f"Unexpected string result from to_time: {time_obj}")

            # If it's already our custom Time class, return it
            if isinstance(time_obj, cls):
                return time_obj

            # Convert pendulum.Time to our custom Time class
            if isinstance(time_obj, pendulum.Time):
                # Convert to our custom class without triggering validation
                return cls._create_from_pendulum_time(time_obj)

            raise ValueError(f"Cannot convert {type(time_obj)} to Time")

        except Exception as e:
            raise ValueError(f"Invalid time value: {value}") from e

    @classmethod
    def _create_from_pendulum_time(cls, pend_time: pendulum.Time) -> "Time":
        """Create a Time instance from a pendulum.Time object.

        This bypasses Pydantic validation and ensures proper internal state.
        """
        # Construct a new pendulum.Time instance explicitly
        time_obj = pendulum.Time(
            pend_time.hour,
            pend_time.minute,
            pend_time.second,
            pend_time.microsecond,
        )
        time_obj = time_obj.replace(tzinfo=pend_time.tzinfo)

        # Bypass __init__ and __new__ by directly casting the type
        time_obj.__class__ = cls  # This is safe since Time inherits from pendulum.Time

        return time_obj

    @classmethod
    def _serialize(cls, value: Optional["Time"]) -> str:
        """Serialize Time instance to string.

        Returns timezone-aware format if timezone info is present,
        otherwise returns naive format.
        """
        if value is None:
            return ""

        tz = value.tzinfo
        if tz is None:
            return value.format("HH:mm:ss.SSSSSS")
        tz_str = str(tz)
        if tz_str in ("UTC", "Etc/UTC"):
            return f"{value.format('HH:mm:ss.SSSSSS')} UTC"
        if re.match(r"[+-]\d{2}:?\d{2}", tz_str):
            return value.format("HH:mm:ss.SSSSSSZZ")
        return f"{value.format('HH:mm:ss.SSSSSS')} {tz_str}"

    def __repr__(self) -> str:
        """Enhanced repr with more detailed information."""
        tz_info = f", tzinfo={self.tzinfo}" if self.tzinfo else ""
        return f"Time({self.hour}, {self.minute}, {self.second}, {self.microsecond}{tz_info})"

    def __str__(self) -> str:
        """String representation for user-friendly display."""
        return self._serialize(self)

    def __eq__(self, other: Any) -> bool:
        """Enhanced equality comparison that handles timezone conversion."""
        if not isinstance(other, (pendulum.Time, Time)):
            return False

        # If both have timezone info, compare in UTC
        if self.tzinfo and other.tzinfo:
            # Convert both to UTC for comparison
            self_utc = self.in_timezone("UTC")
            other_utc = other.in_timezone("UTC")
            return (self_utc.hour, self_utc.minute, self_utc.second, self_utc.microsecond) == (
                other_utc.hour,
                other_utc.minute,
                other_utc.second,
                other_utc.microsecond,
            )

        # If neither has timezone info, compare directly
        if not self.tzinfo and not other.tzinfo:
            return super().__eq__(other)

        # Mixed timezone/naive comparison - only equal if times are exactly the same
        return super().__eq__(other)

    def __hash__(self) -> int:
        """Hash function that considers timezone."""
        if self.tzinfo:
            # Hash based on UTC time
            utc_time = self.in_timezone("UTC")
            return hash(
                (utc_time.hour, utc_time.minute, utc_time.second, utc_time.microsecond, "UTC")
            )
        return hash((self.hour, self.minute, self.second, self.microsecond, None))

    def to_local(self) -> "Time":
        """Convert to local timezone."""
        if not self.tzinfo:
            return self  # Already naive, assume local
        return self.in_timezone(pendulum.local_timezone())

    def to_utc(self) -> "Time":
        """Convert to UTC timezone."""
        return self.in_timezone("UTC")

    def in_timezone(self, timezone: Union[str, pendulum.Timezone]) -> "Time":
        """Convert to specified timezone."""
        if isinstance(timezone, str):
            timezone = pendulum.timezone(timezone)

        if self.is_aware():
            # For timezone conversion, we need a reference date
            # Use today's date as reference
            today = pendulum.today(self.tzinfo)
            dt = today.at(self.hour, self.minute, self.second, self.microsecond)
            dt = dt.in_timezone(timezone)  # Convert to target timezone
            t = dt.time()  # Extract naiv time component
            t = t.replace(tzinfo=timezone)  # Add target timezone
            time_obj = self._create_from_pendulum_time(t)
        else:
            # Assume current time is in local timezone
            time_obj = self.replace(tzinfo=pendulum.local_timezone())

        return time_obj

    def is_naive(self) -> bool:
        """Check if time is timezone-naive."""
        return self.tzinfo is None

    def is_aware(self) -> bool:
        """Check if time is timezone-aware."""
        return self.tzinfo is not None

    def replace_timezone(self, tz: Union[str, pendulum.Timezone, None]) -> "Time":
        """Replace timezone without converting the time value."""
        if isinstance(tz, str):
            tz = pendulum.timezone(tz)
        return self.replace(tzinfo=tz)

    def format_user_friendly(
        self, include_seconds: bool = False, include_timezone: Optional[bool] = None
    ) -> str:
        """Format time in a user-friendly way.

        Args:
            include_seconds: Whether to include seconds in the output
            include_timezone: Whether to include timezone info (auto-detected if None)
        """
        if include_timezone is None:
            include_timezone = self.tzinfo is not None

        if include_seconds:
            time_format = "HH:mm:ss"
        else:
            time_format = "HH:mm"

        if include_timezone and self.tzinfo:
            time_format += " ZZ"

        return self.format(time_format)

    @classmethod
    def now(cls, tz: Union[str, pendulum.Timezone] = None) -> "Time":
        """Get current time with optional timezone."""
        if tz:
            if isinstance(tz, str):
                tz = pendulum.timezone(tz)
            now = pendulum.now(tz)
        else:
            now = pendulum.now()

        return cls(now.hour, now.minute, now.second, now.microsecond, tzinfo=now.tzinfo)

    @classmethod
    def parse(cls, time_string: str) -> "Time":
        """Parse time string using your enhanced parser."""
        parsed = _parse_time_string(time_string)
        return cls(
            parsed.hour, parsed.minute, parsed.second, parsed.microsecond, tzinfo=parsed.tzinfo
        )


def _parse_time_string(time_str: str, default_date: pendulum.Date = None) -> pendulum.Time:
    """Parse various time string formats with comprehensive patterns and timezone support.

    Supports a wide variety of time formats including:

    Basic 24-hour formats:
        - "14:30" - Standard HH:MM format
        - "14:30:45" - HH:MM:SS format
        - "14:30:45.123456" - HH:MM:SS with microseconds
        - "1430" - Compact HHMM format
        - "143045" - Compact HHMMSS format
        - "930" - Short format (9:30)
        - "14" - Hour only
        - "14.5" - Decimal time (14:30)
        - "14h30" - European format with 'h'
        - "14-30" - With dash separator
        - "14 30" - With space separator

    12-hour AM/PM formats:
        - "2:30 PM" - Standard 12-hour with seconds
        - "2:30:45 PM" - 12-hour with seconds
        - "2PM" - Short AM/PM format
        - "11AM" - Short AM/PM format

    Timezone formats:
        - "14:30 UTC" - With UTC timezone
        - "14:30 GMT" - With GMT timezone
        - "2:30 PM EST" - 12-hour with timezone abbreviation
        - "14:30 +05:30" - With offset timezone
        - "14:30 -0800" - With compact offset
        - "930 PST" - Any format can have timezone
        - "14h30 America/New_York" - With full timezone name

    Args:
        time_str: The time string to parse
        default_date: Default date to use when timezone is present (defaults to today)

    Returns:
        pendulum.Time object, optionally with timezone information attached

    Raises:
        ValueError: If the time string cannot be parsed or contains invalid time components
    """
    time_str = time_str.strip()
    original_str = time_str

    # Validate basic format first
    if not time_str:
        raise ValueError("Empty time string")

    # Extract timezone information first
    timezone_info = None
    time_part = time_str

    # Pattern for timezone at the end: +HH:MM, -HH:MM, +HHMM, -HHMM, UTC, GMT, EST, PST, etc.
    tz_pattern = re.compile(
        r"(.+?)\s*([+-]\d{2}:?\d{2}|UTC[+-]?\d{0,2}:?\d{0,2}|GMT[+-]?\d{0,2}:?\d{0,2}|[A-Z]{3,4}|[A-Za-z_]+/[A-Za-z_]+)$",
        re.IGNORECASE,
    )
    tz_match = tz_pattern.match(time_str)

    if tz_match:
        time_part = tz_match.group(1).strip()
        timezone_str = tz_match.group(2).strip()

        # Parse timezone
        if timezone_str.upper() in ["UTC", "GMT"]:
            timezone_info = pendulum.timezone("UTC")
        elif timezone_str.upper() in ["EST", "EDT"]:
            timezone_info = pendulum.timezone("America/New_York")
        elif timezone_str.upper() in ["CST", "CDT"]:
            timezone_info = pendulum.timezone("America/Chicago")
        elif timezone_str.upper() in ["MST", "MDT"]:
            timezone_info = pendulum.timezone("America/Denver")
        elif timezone_str.upper() in ["PST", "PDT"]:
            timezone_info = pendulum.timezone("America/Los_Angeles")
        elif re.match(r"[A-Za-z_]+/[A-Za-z_]+", timezone_str):
            # Try to parse as a standard timezone name
            try:
                timezone_info = pendulum.timezone(timezone_str)
            except:
                raise ValueError(f"Unknown timezone: {timezone_str}")
        elif re.match(r"[+-]\d{2}:?\d{2}", timezone_str):
            # Handle offset format like +05:30, -08:00, +0530, -0800
            clean_tz = timezone_str.replace(":", "")
            if len(clean_tz) == 5:  # +HHMM or -HHMM
                sign = clean_tz[0]
                hours = int(clean_tz[1:3])
                minutes = int(clean_tz[3:5])
                offset_minutes = hours * 60 + minutes
                if sign == "-":
                    offset_minutes = -offset_minutes
                timezone_info = pendulum.tz.timezone.FixedTimezone(offset_minutes * 60)
        else:
            raise ValueError(f"Unknown timezone: {timezone_str}")

    # Now parse the time part (convert to uppercase for AM/PM matching)
    time_part_upper = time_part.upper()

    # Pattern 1: HH:MM:SS.microseconds format
    pattern1 = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?$")
    match = pattern1.match(time_part_upper)
    if match:
        hour, minute, second = int(match.group(1)), int(match.group(2)), int(match.group(3))
        microsecond = int((match.group(4) or "0").ljust(6, "0")[:6])
        if hour > 23 or minute > 59 or second > 59:
            raise ValueError(f"Invalid time components: {hour}:{minute}:{second}")
        time_obj = pendulum.time(hour, minute, second, microsecond)

        if timezone_info:
            return time_obj.replace(tzinfo=timezone_info)
        return time_obj

    # Pattern 2: HH:MM format
    pattern2 = re.compile(r"^(\d{1,2}):(\d{2})$")
    match = pattern2.match(time_part_upper)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            raise ValueError(f"Invalid time components: {hour}:{minute}")
        time_obj = pendulum.time(hour, minute)

        if timezone_info:
            return time_obj.replace(tzinfo=timezone_info)
        return time_obj

    # Pattern 3: 12-hour format with AM/PM (HH:MM:SS AM/PM or HH:MM AM/PM)
    pattern3 = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)$")
    match = pattern3.match(time_part_upper)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        second = int(match.group(3)) if match.group(3) else 0
        am_pm = match.group(4)

        if hour > 12 or hour < 1 or minute > 59 or second > 59:
            raise ValueError(f"Invalid 12-hour time: {original_str}")

        # Convert to 24-hour format
        if am_pm == "AM":
            if hour == 12:
                hour = 0
        else:  # PM
            if hour != 12:
                hour += 12

        time_obj = pendulum.time(hour, minute, second)

        if timezone_info:
            return time_obj.replace(tzinfo=timezone_info)
        return time_obj

    # Pattern 4: Short AM/PM format (e.g., "2PM", "11AM")
    pattern4 = re.compile(r"^(\d{1,2})\s*(AM|PM)$")
    match = pattern4.match(time_part_upper)
    if match:
        hour = int(match.group(1))
        am_pm = match.group(2)

        if hour > 12 or hour < 1:
            raise ValueError(f"Invalid 12-hour time: {original_str}")

        # Convert to 24-hour format
        if am_pm == "AM":
            if hour == 12:
                hour = 0
        else:  # PM
            if hour != 12:
                hour += 12

        time_obj = pendulum.time(hour, 0)

        if timezone_info:
            return time_obj.replace(tzinfo=timezone_info)
        return time_obj

    # Pattern 5: European format with 'h' (e.g., "14h30", "9h15")
    pattern5 = re.compile(r"^(\d{1,2})H(\d{2})$")
    match = pattern5.match(time_part_upper)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            raise ValueError(f"Invalid time components: {hour}:{minute}")
        time_obj = pendulum.time(hour, minute)

        if timezone_info:
            return time_obj.replace(tzinfo=timezone_info)
        return time_obj

    # Pattern 6: Compact format (HHMM, HHMMSS)
    if time_part_upper.isdigit():
        if len(time_part_upper) == 4:
            hour, minute = int(time_part_upper[:2]), int(time_part_upper[2:])
            if hour > 23 or minute > 59:
                raise ValueError(f"Invalid time components: {hour}:{minute}")
            time_obj = pendulum.time(hour, minute)

            if timezone_info:
                return time_obj.replace(tzinfo=timezone_info)
            return time_obj
        elif len(time_part_upper) == 6:
            hour, minute, second = (
                int(time_part_upper[:2]),
                int(time_part_upper[2:4]),
                int(time_part_upper[4:6]),
            )
            if hour > 23 or minute > 59 or second > 59:
                raise ValueError(f"Invalid time components: {hour}:{minute}:{second}")
            time_obj = pendulum.time(hour, minute, second)

            if timezone_info:
                return time_obj.replace(tzinfo=timezone_info)
            return time_obj
        elif len(time_part_upper) == 3:
            # Handle formats like "930" as 9:30
            hour, minute = int(time_part_upper[0]), int(time_part_upper[1:])
            if hour > 23 or minute > 59:
                raise ValueError(f"Invalid time components: {hour}:{minute}")
            time_obj = pendulum.time(hour, minute)

            if timezone_info:
                return time_obj.replace(tzinfo=timezone_info)
            return time_obj
        elif len(time_part_upper) == 1 or len(time_part_upper) == 2:
            # Handle single/double digit hours
            hour = int(time_part_upper)
            if hour > 23:
                raise ValueError(f"Invalid hour: {hour}")
            time_obj = pendulum.time(hour, 0)

            if timezone_info:
                return time_obj.replace(tzinfo=timezone_info)
            return time_obj

    # Pattern 7: Decimal time (e.g., "14.5" as 14:30)
    try:
        if "." in time_part and time_part.replace(".", "").isdigit():
            float_val = float(time_part)
            if float_val < 0 or float_val >= 24:
                raise ValueError(f"Hour must be between 0 and 23.999..., got {float_val}")
            hour = int(float_val)
            minutes = int((float_val - hour) * 60)
            seconds = int(((float_val - hour) * 60 - minutes) * 60)
            time_obj = pendulum.time(hour, minutes, seconds)

            if timezone_info:
                return time_obj.replace(tzinfo=timezone_info)
            return time_obj
    except ValueError:
        pass

    # Pattern 8: Handle various separators (but be careful with dots)
    separators = ["-", " "]  # Removed '.' to avoid conflict with decimal times
    for sep in separators:
        if sep in time_part:
            parts = time_part.split(sep)
            if len(parts) >= 2 and all(part.isdigit() for part in parts[:3]):
                hour = int(parts[0])
                minute = int(parts[1])
                second = int(parts[2]) if len(parts) > 2 else 0
                if hour > 23 or minute > 59 or second > 59:
                    raise ValueError(f"Invalid time components: {hour}:{minute}:{second}")
                time_obj = pendulum.time(hour, minute, second)

                if timezone_info:
                    return time_obj.replace(tzinfo=timezone_info)
                return time_obj

    raise ValueError(f"Unable to parse time string: '{original_str}'")


TimeLike = Union[
    str,
    int,
    float,
    Tuple[int, ...],
    Time,
    datetime.time,
    datetime.datetime,
    pendulum.Time,
    DateTime,
]


# Overload 1: Returns Time
@overload
def to_time(
    value: TimeLike,
    in_timezone: Union[str, pendulum.tz.Timezone, None] = ...,
    to_naive: bool = ...,
    as_string: Literal[False, None] = ...,
) -> Time: ...


# Overload 2: Returns str
@overload
def to_time(
    value: TimeLike,
    in_timezone: Union[str, pendulum.tz.Timezone, None] = ...,
    to_naive: bool = ...,
    as_string: Union[str, Literal[True]] = ...,
) -> str: ...


# Implementation that satisfies both
def to_time(
    value: TimeLike,
    in_timezone: Union[str, pendulum.tz.Timezone, None] = None,
    to_naive: bool = False,
    as_string: Union[str, bool, None] = None,
) -> Union[Time, str]:
    """Convert a time-like value into a timezone-aware Time object or formatted string.

    Args:
        value: A time representation. Supports:
            - Time
            - pendulum.Time or pendulum.DateTime
            - datetime.time or datetime.datetime
            - strings like "14:30", "2:30 PM", "1430", "14:30:00.123", "2PM", "14h30"
            - int (e.g. 14 → 14:00)
            - float (e.g. 14.5 → 14:30)
            - tuple like (14,), (14, 30), (14, 30, 15)

        in_timezone: Optional timezone name or object (e.g., "Europe/Berlin").
            Defaults to the local timezone.

        to_naive: If True, return a timezone-naive Time object.

        as_string: If True, return time as "HH:mm:ss ZZ".
            If a format string is provided, it's passed to `pendulum.Time.format()`.

    Returns:
        Time or str: A time object or its formatted string.

    Raises:
        ValueError: If the input cannot be interpreted as a valid time.
        TypeError: If timezone is not a valid type.
    """
    # Validate and set timezone
    try:
        if in_timezone is None:
            timezone = pendulum.local_timezone()
            if isinstance(timezone, str):
                timezone = pendulum.timezone(timezone)
        elif isinstance(in_timezone, str):
            timezone = pendulum.timezone(in_timezone)
        elif isinstance(in_timezone, pendulum.tz.Timezone):
            timezone = in_timezone
        else:
            raise TypeError(f"Invalid timezone type: {type(in_timezone)}")
        if not isinstance(timezone, Timezone):
            # Should never happen
            raise TypeError(f"Invalid timezone conversion to type: {type(timezone)} ({timezone})")
    except Exception as e:
        raise ValueError(f"Invalid timezone: {in_timezone}") from e

    def finalize(t: pendulum.Time) -> Union[Time, str]:
        """Finalize the time object with timezone and formatting."""
        nonlocal timezone, in_timezone
        try:
            if to_naive:
                t = t.replace(tzinfo=None)
            # Apply timezone if not naive
            elif t.tzinfo:
                if in_timezone is not None and t.tzinfo != timezone:
                    # Convert from original timezone to selected timezone
                    # For timezone conversion, we need a reference date
                    # Use today's date as reference
                    today = pendulum.today(t.tzinfo)
                    dt = today.at(t.hour, t.minute, t.second, t.microsecond)
                    dt = dt.in_timezone(timezone)  # Convert to target timezone
                    t = dt.time()  # Extract time component (always naive)
                    t = t.replace(tzinfo=timezone)  # Add timezone to naive time
            else:
                # Just set the timezone
                t = t.replace(tzinfo=timezone)

            if as_string:
                if isinstance(as_string, str):
                    return t.format(as_string)
                elif t.tzinfo is not None:
                    return t.format("HH:mm:ss ZZ")
                else:
                    return t.format("HH:mm:ss")

            return Time(t.hour, t.minute, t.second, t.microsecond, tzinfo=t.tzinfo)
        except Exception as e:
            raise ValueError(f"Failed to finalize time object: {t}") from e

    # Handle different input types
    try:
        if isinstance(value, Time):
            return finalize(value)

        if isinstance(value, pendulum.Time):
            return finalize(value)

        # Handle DateTime class if it exists
        if hasattr(value, "in_tz") and hasattr(value, "time"):
            return finalize(value.in_tz(timezone).time())

        if isinstance(value, datetime.time):
            base = pendulum.time(value.hour, value.minute, value.second, value.microsecond)
            return finalize(base)

        if isinstance(value, datetime.datetime):
            if value.tzinfo:
                # Convert tzinfo to a string (name or offset)
                tz_name = value.tzinfo.tzname(value)
                # Safely get Pendulum timezone
                try:
                    timezone = pendulum.timezone(tz_name)
                except Exception:
                    # fallback to fixed offset if tz_name is something like 'UTC+02:00'
                    utc_offset = value.tzinfo.utcoffset(value)
                    if utc_offset is None:
                        utc_offset_total_seconds = 0.0
                    else:
                        utc_offset_total_seconds = utc_offset.total_seconds()
                    timezone = pendulum.FixedTimezone(utc_offset_total_seconds // 60)
            pdt = pendulum.instance(value).in_tz(timezone)
            return finalize(pdt.time())

        if isinstance(value, tuple):
            if not value:
                raise ValueError("Empty tuple provided")
            # Pad tuple with zeros if needed
            padded = tuple(list(value) + [0] * (4 - len(value)))[:4]
            base = pendulum.time(*padded)
            return finalize(base)

        if isinstance(value, int):
            if value < 0 or value > 23:
                raise ValueError(f"Hour must be between 0 and 23, got {value}")
            base = pendulum.time(value, 0)
            return finalize(base)

        if isinstance(value, float):
            if value < 0 or value >= 24:
                raise ValueError(f"Hour must be between 0 and 23.999..., got {value}")
            hour = int(value)
            minutes = int((value - hour) * 60)
            seconds = int(((value - hour) * 60 - minutes) * 60)
            microseconds = int(((((value - hour) * 60 - minutes) * 60 - seconds) * 1_000_000))
            base = pendulum.time(hour, minutes, seconds, microseconds)
            return finalize(base)

        if isinstance(value, str):
            # Try our comprehensive string parser first
            try:
                parsed_time = _parse_time_string(value)
                return finalize(parsed_time)
            except ValueError as e:
                logger.trace(f"Custom parser failed for: {value} - {e}")

            # Fallback to pendulum's parser
            try:
                dt = pendulum.parse(value, strict=False).in_tz(timezone)
                return finalize(dt.time())
            except Exception as e:
                logger.trace(f"Pendulum parser failed for '{value}': {e}")

            # Try parsing with ISO time prefix
            try:
                dt = pendulum.parse(f"T{value}", strict=False).in_tz(timezone)
                return finalize(dt.time())
            except Exception as e:
                logger.trace(f"ISO time parser failed for 'T{value}': {e}")

            # Try parsing as part of a full datetime
            try:
                dt = pendulum.parse(f"2000-01-01 {value}", strict=False).in_tz(timezone)
                return finalize(dt.time())
            except Exception as e:
                logger.trace(f"Full datetime parser failed for '2000-01-01 {value}': {e}")

            # If all parsing attempts fail, raise a more specific error
            raise ValueError(f"Unable to parse time string: '{value}'")

        raise ValueError(f"Unsupported type: {type(value)}")

    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Invalid time value: {value!r} of type: {type(value)}") from e


class TimeWindow(BaseModel):
    """Model defining a daily or specific date time window with optional localization support.

    Represents a time interval starting at `start_time` and lasting for `duration`.
    Can restrict applicability to a specific day of the week or a specific calendar date.
    Supports day names in multiple languages via locale-aware parsing.
    """

    start_time: Time = Field(
        ..., json_schema_extra={"description": "Start time of the time window (time of day)."}
    )
    duration: Duration = Field(
        ...,
        json_schema_extra={
            "description": "Duration of the time window starting from `start_time`."
        },
    )
    day_of_week: Optional[Union[int, str]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Optional day of the week restriction. "
                "Can be specified as integer (0=Monday to 6=Sunday) or localized weekday name. "
                "If None, applies every day unless `date` is set."
            )
        },
    )
    date: Optional[Date] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Optional specific calendar date for the time window. Overrides `day_of_week` if set."
            )
        },
    )
    locale: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Locale used to parse weekday names in `day_of_week` when given as string. "
                "If not set, Pendulum's default locale is used. "
                "Examples: 'en', 'de', 'fr', etc."
            )
        },
    )

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

        This method supports both integer (0–6) and string inputs for `day_of_week`.
        String inputs are matched first against English weekday names (case-insensitive),
        and then against localized weekday names using the provided `locale`.

        If a valid match is found, `day_of_week` is converted to its corresponding
        integer value (0 for Monday through 6 for Sunday).

        Returns:
            TimeWindow: The validated instance with `day_of_week` normalized to an integer.

        Raises:
            ValueError: If `day_of_week` is an invalid integer (not in 0–6),
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

        This method computes the concrete start and end datetimes of the configured
        time window for a specific date, taking into account timezone information.

        Handles timezone-aware and naive `DateTime` and `Time` objects:
        - If both `reference_date` and `start_time` have timezones but differ,
        `start_time` is converted to the timezone of `reference_date`.
        - If only one has a timezone, the other inherits it.
        - If both are naive, UTC is assumed for both.

        Args:
            reference_date: The reference date on which to calculate the window.

        Returns:
            tuple[DateTime, DateTime]: A tuple containing the start and end datetimes
            for the time window, both timezone-aware.
        """
        ref_tz = reference_date.timezone
        start_tz = self.start_time.tzinfo

        # --- Timezone resolution logic ---
        if ref_tz and start_tz:
            # Both aware: align start_time to reference_date's tz
            if ref_tz != start_tz:
                start_time = self.start_time.in_timezone(ref_tz)
            else:
                start_time = self.start_time
        elif ref_tz and not start_tz:
            # Only reference_date aware → assume same tz for time
            start_time = self.start_time.replace_timezone(ref_tz)
        elif not ref_tz and start_tz:
            # Only start_time aware → apply its tz to reference_date
            reference_date = reference_date.replace(tzinfo=start_tz)
            start_time = self.start_time
        else:
            # Both naive → default to UTC
            reference_date = reference_date.replace(tzinfo="UTC")
            start_time = self.start_time.replace_timezone("UTC")

        # --- Build window start ---
        start = reference_date.replace(
            hour=start_time.hour,
            minute=start_time.minute,
            second=start_time.second,
            microsecond=start_time.microsecond,
        )

        # --- Compute window end ---
        end = start + self.duration
        return start, end

    def contains(self, date_time: DateTime, duration: Optional[Duration] = None) -> bool:
        """Check whether a datetime (and optional duration) fits within the time window.

        This method checks if a given datetime `date_time` lies within the start time and duration
        defined by the `TimeWindow`. If `duration` is provided, it also ensures that
        the full duration starting at `date_time` ends before or at the end of the time window.

        Handles timezone-aware and naive datetimes:
        - If both `date_time` and `start_time` are timezone-aware but differ → align `start_time`
        to `date_time`’s timezone.
        - If only one has a timezone → assign it to the other.
        - If both are naive → assume UTC for both.

        If `day_of_week` or `date` are specified in the time window, the method will also
        ensure that `date_time` falls on the correct day or matches the exact date.

        Args:
            date_time: The datetime to test.
            duration: An optional duration that must fit entirely within the time window
                starting from `date_time`.

        Returns:
            bool: True if the datetime (and optional duration) is fully contained in the
            time window, False otherwise.
        """
        start_time = self.start_time  # work on a local copy to avoid mutating self
        start_tz = getattr(start_time, "tzinfo", None)
        ref_tz = date_time.timezone

        # --- Handle timezone logic ---
        if ref_tz and start_tz:
            # Both aware but different → align start_time to date_time's timezone
            if ref_tz != start_tz:
                start_time = start_time.in_timezone(ref_tz)
        elif ref_tz and not start_tz:
            # Only date_time aware → assign its timezone to start_time
            start_time = start_time.replace_timezone(ref_tz)
        elif not ref_tz and start_tz:
            # Only start_time aware → assign its timezone to date_time
            date_time = date_time.replace(tzinfo=start_tz)
        else:
            # Both naive → assume UTC
            date_time = date_time.replace(tzinfo="UTC")
            start_time = start_time.replace_timezone("UTC")

        # --- Date and weekday constraints ---
        if self.date and date_time.date() != self.date:
            return False

        if self.day_of_week is not None and date_time.day_of_week != self.day_of_week:
            return False

        # --- Compute window start and end for this date ---
        start, end = self._window_start_end(date_time)

        # --- Check containment ---
        if not (start <= date_time < end):
            return False

        if duration is not None:
            date_time_end = date_time + duration
            return date_time_end <= end

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

        # Check if the reference date matches our constraints
        if self.date and reference_date.date() != self.date:
            return None

        if self.day_of_week is not None and reference_date.day_of_week != self.day_of_week:
            return None

        # Check if the duration can fit within the time window
        if duration > self.duration:
            return None

        window_start, window_end = self._window_start_end(reference_date)

        # The earliest start time is simply the window start time
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

        # Check if the reference date matches our constraints
        if self.date and reference_date.date() != self.date:
            return None

        if self.day_of_week is not None and reference_date.day_of_week != self.day_of_week:
            return None

        # Check if the duration can fit within the time window
        if duration > self.duration:
            return None

        window_start, window_end = self._window_start_end(reference_date)

        # The latest start time is the window end minus the duration
        latest_start = window_end - duration

        # Ensure the latest start time is not before the window start
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


class TimeWindowSequence(BaseModel):
    """Model representing a sequence of time windows with collective operations.

    Manages multiple TimeWindow objects and provides methods to work with them
    as a cohesive unit for scheduling and availability checking.
    """

    windows: Optional[list[TimeWindow]] = Field(
        default_factory=list,
        json_schema_extra={"description": "List of TimeWindow objects that make up this sequence."},
    )

    @field_validator("windows")
    @classmethod
    def validate_windows(cls, v: Optional[list[TimeWindow]]) -> list[TimeWindow]:
        """Validate windows and convert None to empty list."""
        if v is None:
            return []
        return v

    def model_post_init(self, __context: Any) -> None:
        """Ensure windows is always a list after initialization."""
        if self.windows is None:
            self.windows = []

    def __iter__(self) -> Iterator[TimeWindow]:
        """Allow iteration over the time windows."""
        return iter(self.windows or [])

    def __len__(self) -> int:
        """Return the number of time windows in the sequence."""
        return len(self.windows or [])

    def __getitem__(self, index: int) -> TimeWindow:
        """Allow indexing into the time windows."""
        if not self.windows:
            raise IndexError("list index out of range")
        return self.windows[index]

    def contains(self, date_time: DateTime, duration: Optional[Duration] = None) -> bool:
        """Check if any time window in the sequence contains the given datetime and duration.

        Args:
            date_time: The datetime to test.
            duration: An optional duration that must fit entirely within one of the time windows.

        Returns:
            bool: True if any time window contains the datetime (and optional duration), False if no windows.
        """
        if not self.windows:
            return False
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

        earliest_times = []

        for window in self.windows:
            earliest = window.earliest_start_time(duration, reference_date)
            if earliest is not None:
                earliest_times.append(earliest)

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

        latest_times = []

        for window in self.windows:
            latest = window.latest_start_time(duration, reference_date)
            if latest is not None:
                latest_times.append(latest)

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
        if not self.windows:
            return False

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

        total_duration = Duration()
        has_applicable_windows = False

        for window in self.windows:
            window_duration = window.available_duration(reference_date)
            if window_duration is not None:
                total_duration += window_duration
                has_applicable_windows = True

        return total_duration if has_applicable_windows else None

    def get_applicable_windows(self, reference_date: Optional[DateTime] = None) -> list[TimeWindow]:
        """Get all windows that apply to the given reference date.

        Args:
            reference_date: The date to check for the time windows. Defaults to today.

        Returns:
            List of TimeWindow objects that apply to the reference date.
        """
        if not self.windows:
            return []

        if reference_date is None:
            reference_date = pendulum.today()

        applicable_windows = []

        for window in self.windows:
            if window.available_duration(reference_date) is not None:
                applicable_windows.append(window)

        return applicable_windows

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
        if not self.windows:
            return []

        if reference_date is None:
            reference_date = pendulum.today()

        fitting_windows = []

        for window in self.windows:
            if window.can_fit_duration(duration, reference_date):
                fitting_windows.append(window)

        return fitting_windows

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
        if not self.windows:
            return []

        if reference_date is None:
            reference_date = pendulum.today()

        possible_times = []

        for window in self.windows:
            earliest = window.earliest_start_time(duration, reference_date)
            latest = window.latest_start_time(duration, reference_date)

            if earliest is not None and latest is not None:
                possible_times.append((earliest, latest, window))

        return possible_times

    def add_window(self, window: TimeWindow) -> None:
        """Add a new time window to the sequence.

        Args:
            window: The TimeWindow to add.
        """
        if self.windows is None:
            self.windows = []
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
        if self.windows is not None:
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
            """Sort key: (priority, start_time) where priority 0 = applicable, 1 = not applicable."""
            start_time = window.earliest_start_time(Duration(), reference_date)
            if start_time is None:
                # Non-applicable windows get a high priority (sorted last) and a dummy time
                return (1, reference_date)
            return (0, start_time)

        self.windows.sort(key=sort_key)


@overload
def to_datetime(
    date_input: Optional[Any] = None,
    as_string: Literal[False] | None = None,
    in_timezone: Optional[Union[str, Timezone]] = None,
    to_naiv: Optional[bool] = None,
    to_maxtime: Optional[bool] = None,
) -> DateTime: ...


@overload
def to_datetime(
    date_input: Optional[Any] = None,
    as_string: str | Literal[True] = True,
    in_timezone: Optional[Union[str, Timezone]] = None,
    to_naiv: Optional[bool] = None,
    to_maxtime: Optional[bool] = None,
) -> str: ...


def to_datetime(
    date_input: Optional[Any] = None,
    as_string: Optional[Union[str, bool]] = None,
    in_timezone: Optional[Union[str, Timezone]] = None,
    to_naiv: Optional[bool] = None,
    to_maxtime: Optional[bool] = None,
) -> Union[DateTime, str]:
    """Convert a date input into a Pendulum DateTime object or a formatted string, with optional timezone handling.

    This function handles various date input formats, adjusts for timezones, and provides flexibility for formatting and time adjustments. For date strings without explicit timezone information, the local timezone is assumed. Be aware that Pendulum DateTime objects created without a timezone default to UTC.

    Args:
        date_input (Optional[Any]): The date input to convert. Supported types include:
            - `str`: A date string in various formats (e.g., "2024-10-13", "13 Oct 2024").
            - `pendulum.DateTime`: A Pendulum DateTime object.
            - `pendulum.Date`: A Pendulum Date object, which will be converted to a datetime at the start or end of the day.
            - `datetime.datetime`: A standard Python datetime object.
            - `datetime.date`: A date object, which will be converted to a datetime at the start or end of the day.
            - `int` or `float`: A Unix timestamp, interpreted as seconds since the epoch (UTC).
            - `None`: Defaults to the current date and time, adjusted to the start or end of the day based on `to_maxtime`.

        as_string (Optional[Union[str, bool]]): Determines the output format:
            - `True`: Returns the datetime in ISO 8601 string format.
            - `"UTC"` or `"utc"`: Returns the datetime normalized to UTC as an ISO 8601 string.
            - `str`: A custom date format string for the output (e.g., "YYYY-MM-DD HH:mm:ss").
            - `False` or `None` (default): Returns a `pendulum.DateTime` object.

        in_timezone (Optional[Union[str, Timezone]]): Specifies the target timezone for the result.
            - Can be a timezone string (e.g., "UTC", "Europe/Berlin") or a `pendulum.Timezone` object.
            - Defaults to the local timezone if not provided.

        to_naiv (Optional[bool]): If `True`, removes timezone information from the resulting datetime object.
            - Defaults to `False`.

        to_maxtime (Optional[bool]): Determines the time portion of the resulting datetime for date inputs:
            - `True`: Sets the time to the end of the day (23:59:59).
            - `False` or `None`: Sets the time to the start of the day (00:00:00).
            - Ignored if `date_input` includes an explicit time or if the input is a timestamp.

    Returns:
        pendulum.DateTime or str:
            - A timezone-aware Pendulum DateTime object by default.
            - A string representation if `as_string` is specified.

    Raises:
        ValueError: If `date_input` is not a valid or supported type, or if the date string cannot be parsed.

    Examples:
        >>> to_datetime("2024-10-13", as_string=True, in_timezone="UTC")
        '2024-10-13T00:00:00+00:00'

        >>> to_datetime("2024-10-13T15:30:00", in_timezone="Europe/Berlin")
        DateTime(2024, 10, 13, 17, 30, 0, tzinfo=Timezone('Europe/Berlin'))

        >>> to_datetime(date(2024, 10, 13), to_maxtime=True)
        DateTime(2024, 10, 13, 23, 59, 59, tzinfo=Timezone('Local'))

        >>> to_datetime(1698784800, as_string="YYYY-MM-DD HH:mm:ss", in_timezone="UTC")
        '2024-10-31 12:00:00'
    """
    # Timezone to convert to
    if in_timezone is None:
        in_timezone = pendulum.local_timezone()
    elif not isinstance(in_timezone, Timezone):
        in_timezone = pendulum.timezone(in_timezone)

    if isinstance(date_input, DateTime):
        dt = date_input
    elif isinstance(date_input, Date):
        dt = pendulum.datetime(
            year=date_input.year, month=date_input.month, day=date_input.day, tz=in_timezone
        )
        if to_maxtime:
            dt = dt.end_of("day")
        else:
            dt = dt.start_of("day")
    elif isinstance(date_input, str):
        # Convert to timezone aware datetime
        dt = None
        formats = [
            "YYYY-MM-DD",  # Format: 2024-10-13
            "DD/MM/YY",  # Format: 13/10/24
            "DD/MM/YYYY",  # Format: 13/10/2024
            "MM-DD-YYYY",  # Format: 10-13-2024
            "D.M.YYYY",  # Format: 1.7.2024
            "YYYY.MM.DD",  # Format: 2024.10.13
            "D MMM YYYY",  # Format: 13 Oct 2024
            "D MMMM YYYY",  # Format: 13 October 2024
            "YYYY-MM-DD HH:mm:ss",  # Format: 2024-10-13 15:30:00
            "YYYY-MM-DDTHH:mm:ss",  # Format: 2024-10-13T15:30:00
        ]
        for fmt in formats:
            # DateTime input without timezone info
            try:
                fmt_tz = f"{fmt} z"
                dt_tz = f"{date_input} {in_timezone}"
                dt = pendulum.from_format(dt_tz, fmt_tz)
                logger.trace(
                    f"Str Fmt converted: {dt}, tz={dt.tz} from {date_input}, tz={in_timezone}"
                )
                break
            except ValueError as e:
                logger.trace(f"{date_input}, {fmt}, {e}")
                dt = None
        else:
            # DateTime input with timezone info
            try:
                dt = pendulum.parse(date_input)
                logger.trace(
                    f"Pendulum Fmt converted: {dt}, tz={dt.tz} from {date_input}, tz={in_timezone}"
                )
            except pendulum.parsing.exceptions.ParserError as e:
                logger.trace(f"Date string {date_input} does not match any Pendulum formats: {e}")
                dt = None
        if dt is None:
            # Some special values
            if date_input.lower() == "infinity":
                # Subtract one year from max as max datetime will create an overflow error in certain context.
                dt = DateTime.max.subtract(years=1)
        if dt is None:
            try:
                timestamp = float(date_input)
                dt = pendulum.from_timestamp(timestamp, tz="UTC")
            except (ValueError, TypeError) as e:
                logger.trace(f"Date string {date_input} does not match timestamp format: {e}")
                dt = None
        if dt is None:
            raise ValueError(f"Date string {date_input} does not match any known formats.")
    elif date_input is None:
        dt = pendulum.now(tz=in_timezone)
    elif isinstance(date_input, datetime.datetime):
        dt = pendulum.instance(date_input)
    elif isinstance(date_input, datetime.date):
        dt = pendulum.instance(
            datetime.datetime.combine(
                date_input,
                datetime.datetime.max.time() if to_maxtime else datetime.datetime.min.time(),
            )
        )
    elif isinstance(date_input, (int, float)):
        dt = pendulum.from_timestamp(date_input, tz="UTC")
    else:
        error_msg = f"Unsupported date input type: {type(date_input)}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    # Represent in target timezone
    dt_in_tz = dt.in_timezone(in_timezone)
    logger.trace(
        f"\nTimezone adapted to: {in_timezone}\nfrom: {dt} tz={dt.timezone}\nto:   {dt_in_tz} tz={dt_in_tz.tz}"
    )
    dt = dt_in_tz

    # Remove timezone info if specified
    if to_naiv:
        dt = dt.naive()

    # Return as formatted string if specified
    if isinstance(as_string, str):
        if as_string.lower() == "utc":
            return dt.in_timezone("UTC").to_iso8601_string()
        else:
            return dt.format(as_string)
    if isinstance(as_string, bool) and as_string is True:
        return dt.to_iso8601_string()

    return dt


# to duration helper
def duration_to_iso8601(duration: pendulum.Duration) -> str:
    """Convert pendulum.Duration to ISO-8601 duration string."""
    total_seconds = int(duration.total_seconds())

    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)

    parts = ["P"]
    if days:
        parts.append(f"{days}D")

    time_parts = []
    if hours:
        time_parts.append(f"{hours}H")
    if minutes:
        time_parts.append(f"{minutes}M")
    if seconds:
        time_parts.append(f"{seconds}S")

    if time_parts:
        parts.append("T")
        parts.extend(time_parts)
    elif len(parts) == 1:  # zero duration
        parts.append("T0S")

    return "".join(parts)


@overload
def to_duration(
    input_value: Union[
        Duration, datetime.timedelta, str, int, float, Tuple[int, int, int, int], List[int]
    ],
    as_string: Literal[False] | None = None,
) -> Duration: ...


@overload
def to_duration(
    input_value: Union[
        Duration, datetime.timedelta, str, int, float, Tuple[int, int, int, int], List[int]
    ],
    as_string: str | Literal[True] = True,
) -> str: ...


def to_duration(
    input_value: Union[
        Duration, datetime.timedelta, str, int, float, Tuple[int, int, int, int], List[int]
    ],
    as_string: Optional[Union[str, bool]] = None,
) -> Union[Duration, str]:
    """Converts various input types into a `pendulum.Duration` or a formatted duration string.

    Args:
        input_value (Union[Duration, timedelta, str, int, float, tuple, list]):
            The input value to convert into a duration.
            Supported types include:

            - `pendulum.Duration`: Returned unchanged unless formatting is requested.
            - `datetime.timedelta`: Converted based on total seconds.
            - `str`: A duration expression (e.g., `"15 minutes"`, `"2 hours"`),
              or a string parsed by Pendulum.
            - `int` or `float`: Interpreted as a number of seconds.
            - `tuple` or `list`: Must be `(days, hours, minutes, seconds)`.

        as_string (Optional[Union[str, bool]]):
            Controls the output format of the returned duration:

            - `None` or `False` (default):
                Returns a `pendulum.Duration` object.
            - `True`:
                Returns an ISO-8601 duration string (e.g., `"PT15M"`).
            - `"human"`:
                Returns a human-readable form (e.g., `"15 minutes"`).
            - `"pandas"`:
                Returns a Pandas frequency string such as:
                - `"1h"` for 1 hour
                - `"15min"` for 15 minutes
                - `"900s"` for 900 seconds
            - `str`:
                A custom format pattern. The following format tokens are supported:
                - `{S}` → total seconds
                - `{M}` → total minutes (integer)
                - `{H}` → total hours (integer)
                - `{f}` → human-friendly representation (Pendulum `in_words()`)

    Example:
                    `"Duration: {M} minutes"` → `"Duration: 15 minutes"`

    Returns:
        Union[Duration, str]:
            - A `pendulum.Duration` if no formatting is requested.
            - A formatted string depending on the `as_string` option.

    Raises:
        ValueError:
            - If the input type is unsupported.
            - If a duration string cannot be parsed.
            - If `as_string` contains an unsupported format option.

    Examples:
        >>> to_duration("15 minutes")
        <Duration [900 seconds]>

        >>> to_duration("15 minutes", as_string=True)
        'PT15M'

        >>> to_duration("15 minutes", as_string="human")
        '15 minutes'

        >>> to_duration("90 seconds", as_string="pandas")
        '90S'

        >>> to_duration("15 minutes", as_string="{M}m")
        '15m'
    """
    # ---- normalize to pendulum.Duration ----
    duration = None

    if isinstance(input_value, Duration):
        duration = input_value

    elif isinstance(input_value, datetime.timedelta):
        duration = pendulum.duration(seconds=input_value.total_seconds())

    elif isinstance(input_value, (int, float)):
        duration = pendulum.duration(seconds=input_value)

    elif isinstance(input_value, (tuple, list)):
        if len(input_value) != 4:
            error_msg = f"Expected tuple/list length 4, got {len(input_value)}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        days, hours, minutes, seconds = input_value
        duration = pendulum.duration(days=days, hours=hours, minutes=minutes, seconds=seconds)

    elif isinstance(input_value, str):
        # first try pendulum.parse
        try:
            parsed = pendulum.parse(input_value)
            if isinstance(parsed, pendulum.Duration):
                duration = parsed  # Already a duration
            else:
                # It's a DateTime, calculate duration from start of day
                duration = parsed - parsed.start_of("day")
        except pendulum.parsing.exceptions.ParserError as e:
            logger.trace(f"Invalid Pendulum time string format '{input_value}': {e}")

            # Mitigate ReDoS vulnerability (#494) by checking input string length.
            if len(input_value) > MAX_DURATION_STRING_LENGTH:
                error_msg = (
                    f"Input string exceeds maximum allowed length ({MAX_DURATION_STRING_LENGTH})."
                )
                logger.error(error_msg)
                raise ValueError(error_msg)

            # Handle strings like "2 days 5 hours 30 minutes"
            matches = re.findall(r"(\d+)\s*(days?|hours?|minutes?|seconds?)", input_value)
            if not matches:
                error_msg = f"Invalid time string format '{input_value}'"
                logger.error(error_msg)
                raise ValueError(error_msg)

            total_seconds = 0
            time_units = {
                "day": 86400,
                "hour": 3600,
                "minute": 60,
                "second": 1,
            }
            for value, unit in matches:
                unit = unit.lower().rstrip("s")  # Normalize unit
                if unit in time_units:
                    total_seconds += int(value) * time_units[unit]
                else:
                    error_msg = f"Unsupported time unit: {unit}"
                    logger.error(error_msg)
                    raise ValueError(error_msg)

            duration = pendulum.duration(seconds=total_seconds)

    else:
        error_msg = f"Unsupported input type: {type(input_value)}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    # ---- now apply as_string rules ----
    if not as_string:
        return duration

    total_seconds = int(duration.total_seconds())

    # Boolean True → ISO-8601
    if as_string is True:
        return duration_to_iso8601(duration)

    # Human-readable
    if as_string == "human":
        return duration.in_words()

    # Pandas frequency
    if as_string == "pandas":
        # hours?
        if total_seconds % 3600 == 0:
            return f"{total_seconds // 3600}h"
        # minutes?
        if total_seconds % 60 == 0:
            return f"{total_seconds // 60}min"
        # else seconds (fallback)
        return f"{total_seconds}s"

    # Custom format string
    if isinstance(as_string, str):
        return as_string.format(
            S=total_seconds,
            M=total_seconds // 60,
            H=total_seconds // 3600,
            f=duration.in_words(),
        )

    error_msg = f"Unsupported as_string value: {as_string}"
    logger.error(error_msg)
    raise ValueError(error_msg)


@overload
def to_timezone(
    utc_offset: Optional[float] = None,
    location: Optional[Tuple[float, float]] = None,
    as_string: Literal[True] = True,
) -> str: ...


@overload
def to_timezone(
    utc_offset: Optional[float] = None,
    location: Optional[Tuple[float, float]] = None,
    as_string: Literal[False] | None = None,
) -> Timezone: ...


def to_timezone(
    utc_offset: Optional[float] = None,
    location: Optional[Tuple[float, float]] = None,
    as_string: Optional[bool] = False,
) -> Union[Timezone, str]:
    """Determines the timezone either by UTC offset, geographic location, or local system timezone.

    By default, it returns a `Timezone` object representing the timezone.
    If `as_string` is set to `True`, the function returns the timezone name as a string instead.

    Args:
        utc_offset (Optional[float]): UTC offset in hours. Positive for UTC+, negative for UTC-.
        location (Optional[Tuple[float,float]]): A tuple containing latitude and longitude as floats.
        as_string (Optional[bool]):
            - If `True`, returns the timezone as a string (e.g., "America/New_York").
            - If `False` or not provided, returns a `Timezone` object for the timezone.

    Returns:
        Union[Timezone, str]:
            - A timezone name as a string (e.g., "America/New_York") if `as_string` is `True`.
            - A `Timezone` object if `as_string` is `False` or not provided.

    Raises:
        ValueError: If invalid inputs are provided.

    Example:
        >>> to_timezone(utc_offset=5.5, as_string=True)
        'UTC+05:30'

        >>> to_timezone(location=(40.7128, -74.0060))
        <Timezone [America/New_York]>

        >>> to_timezone()
        <Timezone [America/New_York]>  # Returns local timezone
    """
    if utc_offset is not None:
        if not isinstance(utc_offset, (int, float)):
            raise ValueError("UTC offset must be an integer or float representing hours.")
        if not -24 <= utc_offset <= 24:
            raise ValueError("UTC offset must be within the range -24 to +24 hours.")

        # Convert UTC offset to an Etc/GMT-compatible format
        hours = int(utc_offset)
        minutes = int((abs(utc_offset) - abs(hours)) * 60)
        sign = "-" if utc_offset >= 0 else "+"
        offset_str = f"Etc/GMT{sign}{abs(hours)}"
        if minutes > 0:
            offset_str += f":{minutes:02}"

        if as_string:
            return offset_str
        return pendulum.timezone(offset_str)

    # Handle location-based lookup
    if location is not None:
        try:
            lat, lon = location
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError(f"Invalid latitude/longitude: {lat}, {lon}")
            tz_name = get_tz(lon, lat)
            if not tz_name:
                raise ValueError(
                    f"No timezone found for coordinates: latitude {lat}, longitude {lon}"
                )
        except Exception as e:
            raise ValueError(f"Error determining timezone for location {location}: {e}") from e

        if as_string:
            return tz_name
        return pendulum.timezone(tz_name)

    # Fallback to local timezone
    local_tz = pendulum.local_timezone()
    if isinstance(local_tz, str):
        local_tz = pendulum.timezone(local_tz)
    if as_string:
        return local_tz.name
    return local_tz


def hours_in_day(dt: Optional[DateTime] = None) -> int:
    """Returns the number of hours in the given date's day, considering DST transitions.

    Args:
        dt (Optional[pendulum.DateTime]): The date to check (no time component).

    Returns:
        int: The number of hours in the day (23, 24, or 25).
    """
    if dt is None:
        dt = to_datetime()

    # Start and end of the day in the local timezone
    start_of_day = pendulum.datetime(dt.year, dt.month, dt.day, 0, 0, 0, tz=dt.timezone)
    end_of_day = start_of_day.add(days=1)

    # Calculate the difference in hours between the two
    duration = end_of_day - start_of_day
    return int(duration.total_hours())


class DatetimesComparisonResult:
    """Encapsulates the result of comparing two Pendulum DateTime objects.

    Attributes:
        equal (bool): Indicates whether the two datetimes are exactly equal
            (including timezone and DST state).
        same_instant (bool): Indicates whether the two datetimes represent the same
            point in time, regardless of their timezones.
        time_diff (float): The time difference between the two datetimes in seconds.
        timezone_diff (bool): Indicates whether the timezones of the two datetimes are different.
        dst_diff (bool): Indicates whether the two datetimes differ in their DST states.
        approximately_equal (bool): Indicates whether the time difference between the
            two datetimes is within the specified tolerance.
        ge (bool): True if `dt1` is greater than or equal to `dt2`.
        gt (bool): True if `dt1` is strictly greater than `dt2`.
        le (bool): True if `dt1` is less than or equal to `dt2`.
        lt (bool): True if `dt1` is strictly less than `dt2`.
    """

    def __init__(
        self,
        equal: bool,
        same_instant: bool,
        time_diff: float,
        timezone_diff: bool,
        dst_diff: bool,
        approximately_equal: bool,
    ):
        self.equal = equal
        self.same_instant = same_instant
        self.time_diff = time_diff
        self.timezone_diff = timezone_diff
        self.dst_diff = dst_diff
        self.approximately_equal = approximately_equal

    @property
    def ge(self) -> bool:
        """Greater than or equal: True if `dt1` >= `dt2`."""
        return self.equal or self.time_diff > 0

    @property
    def gt(self) -> bool:
        """Strictly greater than: True if `dt1` > `dt2`."""
        return not self.equal and self.time_diff > 0

    @property
    def le(self) -> bool:
        """Less than or equal: True if `dt1` <= `dt2`."""
        return self.equal or self.time_diff < 0

    @property
    def lt(self) -> bool:
        """Strictly less than: True if `dt1` < `dt2`."""
        return not self.equal and self.time_diff < 0

    def __repr__(self) -> str:
        return (
            f"ComparisonResult(equal={self.equal}, "
            f"same_instant={self.same_instant}, "
            f"time_diff={self.time_diff}, "
            f"timezone_diff={self.timezone_diff}, "
            f"dst_diff={self.dst_diff}, "
            f"approximately_equal={self.approximately_equal}, "
            f"ge={self.ge}, gt={self.gt}, le={self.le}, lt={self.lt})"
        )


def compare_datetimes(
    dt1: DateTime,
    dt2: DateTime,
    tolerance: Optional[Union[int, pendulum.Duration]] = None,
) -> DatetimesComparisonResult:
    """Compares two Pendulum DateTime objects with precision, including DST and timezones.

    This function evaluates various aspects of the relationship between two datetime objects:
    - Exact equality, including timezone and DST state.
    - Whether they represent the same instant in time (ignoring timezones).
    - The absolute time difference in seconds.
    - Differences in timezone and DST state.
    - Approximate equality based on a specified tolerance.
    - Greater or lesser comparisons.

    Args:
        dt1 (pendulum.DateTime): The first datetime object to compare.
        dt2 (pendulum.DateTime): The second datetime object to compare.
        tolerance (Optional[Union[int, pendulum.Duration]]): An optional tolerance for comparison.
            - If an integer is provided, it is interpreted as seconds.
            - If a `pendulum.Duration` is provided, its total seconds are used.
            - If not provided, no tolerance is applied.

    Returns:
        DatetimesComparisonResult: An object containing the results of the comparison, including:
            - `equal`: Whether the datetimes are exactly equal.
            - `same_instant`: Whether the datetimes represent the same instant.
            - `time_diff`: The time difference in seconds.
            - `timezone_diff`: Whether the timezones differ.
            - `dst_diff`: Whether the DST states differ.
            - `approximately_equal`: Whether the time difference is within the tolerance.
            - `ge`, `gt`, `le`, `lt`: Relational comparisons between the two datetimes.

    Examples:
        Compare two datetimes exactly:
        >>> dt1 = pendulum.datetime(2023, 7, 1, 12, tz='Europe/Berlin')
        >>> dt2 = pendulum.datetime(2023, 7, 1, 12, tz='UTC')
        >>> compare_datetimes(dt1, dt2)
        DatetimesComparisonResult(equal=False, same_instant=True, time_diff=7200, timezone_diff=True, dst_diff=False, approximately_equal=False, ge=False, gt=False, le=True, lt=True)

        Compare with a tolerance:
        >>> compare_datetimes(dt1, dt2, tolerance=7200)
        DatetimesComparisonResult(equal=False, same_instant=True, time_diff=7200, timezone_diff=True, dst_diff=False, approximately_equal=True, ge=False, gt=False, le=True, lt=True)
    """
    # Normalize tolerance to seconds
    if tolerance is None:
        tolerance_seconds = 0
    elif isinstance(tolerance, pendulum.Duration):
        tolerance_seconds = tolerance.total_seconds()
    else:
        tolerance_seconds = int(tolerance)

    # Strict equality check (includes timezone and DST)
    is_equal = dt1.in_tz("UTC") == dt2.in_tz("UTC")

    # Instant comparison (point in time, might be in different timezones)
    is_same_instant = dt1.int_timestamp == dt2.int_timestamp

    # Time difference calculation. Throws exception if diverging timezone awareness.
    time_diff = dt1.int_timestamp - dt2.int_timestamp

    # Timezone comparison
    timezone_diff = dt1.timezone_name != dt2.timezone_name

    # DST state comparison
    dst_diff = dt1.is_dst() != dt2.is_dst()

    # Tolerance-based approximate equality
    is_approximately_equal = time_diff <= tolerance_seconds

    return DatetimesComparisonResult(
        equal=is_equal,
        same_instant=is_same_instant,
        time_diff=time_diff,
        timezone_diff=timezone_diff,
        dst_diff=dst_diff,
        approximately_equal=is_approximately_equal,
    )
