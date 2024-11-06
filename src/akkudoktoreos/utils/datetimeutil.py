"""Utility functions for date-time conversion tasks.

Functions:
----------
- to_datetime: Converts various date or time inputs to a timezone-aware or naive `datetime`
  object or formatted string.
- to_duration: Converts various time delta inputs to a `timedelta`object.
- to_timezone: Converts utc offset or location latitude and longitude to a `timezone` object.

Example usage:
--------------

    # Date-time conversion
    >>> date_str = "2024-10-15"
    >>> date_obj = to_datetime(date_str)
    >>> print(date_obj)  # Output: datetime object for '2024-10-15'

    # Time delta conversion
    >>> to_duration("2 days 5 hours")

    # Timezone detection
    >>> to_timezone(location={40.7128, -74.0060})
"""

import re
from datetime import date, datetime, timedelta
from typing import Any, List, Literal, Optional, Tuple, Union, overload

import pendulum
from pendulum import DateTime
from pendulum.tz.timezone import Timezone
from timezonefinder import TimezoneFinder

from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)


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
                logger.debug(
                    f"Str Fmt converted: {dt}, tz={dt.tz} from {date_input}, tz={in_timezone}"
                )
                break
            except ValueError as e:
                logger.debug(f"{date_input}, {fmt}, {e}")
                dt = None
        else:
            # DateTime input with timezone info
            try:
                dt = pendulum.parse(date_input)
                logger.debug(
                    f"Pendulum Fmt converted: {dt}, tz={dt.tz} from {date_input}, tz={in_timezone}"
                )
            except pendulum.parsing.exceptions.ParserError as e:
                logger.debug(f"Date string {date_input} does not match any Pendulum formats: {e}")
                dt = None
        if dt is None:
            raise ValueError(f"Date string {date_input} does not match any known formats.")
    elif date_input is None:
        dt = (
            pendulum.today(tz=in_timezone).end_of("day")
            if to_maxtime
            else pendulum.today(tz=in_timezone).start_of("day")
        )
    elif isinstance(date_input, datetime):
        dt = pendulum.instance(date_input)
    elif isinstance(date_input, date):
        dt = pendulum.instance(
            datetime.combine(date_input, datetime.max.time() if to_maxtime else datetime.min.time())
        )
    elif isinstance(date_input, (int, float)):
        dt = pendulum.from_timestamp(date_input, tz="UTC")
    else:
        error_msg = f"Unsupported date input type: {type(date_input)}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    # Represent in target timezone
    dt_in_tz = dt.in_timezone(in_timezone)
    logger.debug(
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


def to_duration(
    input_value: Union[timedelta, str, int, float, Tuple[int, int, int, int], List[int]],
) -> timedelta:
    """Converts various input types into a timedelta object using pendulum.

    Args:
        input_value (Union[timedelta, str, int, float, tuple, list]): Input to be converted
            into a timedelta:
            - str: A duration string like "2 days", "5 hours", "30 minutes", or a combination.
            - int/float: Number representing seconds.
            - tuple/list: A tuple or list in the format (days, hours, minutes, seconds).

    Returns:
        timedelta: A timedelta object corresponding to the input value.

    Raises:
        ValueError: If the input format is not supported.

    Examples:
        >>> to_duration("2 days 5 hours")
        timedelta(days=2, seconds=18000)

        >>> to_duration(3600)
        timedelta(seconds=3600)

        >>> to_duration((1, 2, 30, 15))
        timedelta(days=1, seconds=90315)
    """
    if isinstance(input_value, timedelta):
        return input_value

    if isinstance(input_value, (int, float)):
        # Handle integers or floats as seconds
        return timedelta(seconds=input_value)

    elif isinstance(input_value, (tuple, list)):
        # Handle tuple or list: (days, hours, minutes, seconds)
        if len(input_value) == 4:
            days, hours, minutes, seconds = input_value
            return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
        else:
            error_msg = f"Expected a tuple or list of length 4, got {len(input_value)}"
            logger.error(error_msg)
            raise ValueError(error_msg)

    elif isinstance(input_value, str):
        # Use pendulum's parsing for human-readable duration strings
        try:
            duration = pendulum.parse(input_value)
            return duration - duration.start_of("day")
        except pendulum.parsing.exceptions.ParserError as e:
            logger.debug(f"Invalid Pendulum time string format '{input_value}': {e}")

        # Handle strings like "2 days 5 hours 30 minutes"
        total_seconds = 0
        time_units = {
            "day": 86400,  # 24 * 60 * 60
            "hour": 3600,
            "minute": 60,
            "second": 1,
        }

        # Regular expression to match time components like '2 days', '5 hours', etc.
        matches = re.findall(r"(\d+)\s*(days?|hours?|minutes?|seconds?)", input_value)

        if not matches:
            error_msg = f"Invalid time string format '{input_value}'"
            logger.error(error_msg)
            raise ValueError(error_msg)

        for value, unit in matches:
            unit = unit.lower().rstrip("s")  # Normalize unit
            if unit in time_units:
                total_seconds += int(value) * time_units[unit]
            else:
                error_msg = f"Unsupported time unit: {unit}"
                logger.error(error_msg)
                raise ValueError(error_msg)

        return pendulum.duration(seconds=total_seconds)

    else:
        error_msg = f"Unsupported input type: {type(input_value)}"
        logger.error(error_msg)
        raise ValueError(error_msg)


timezone_finder = TimezoneFinder()  # Static variable for caching


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

        >>> to_timezone(location={40.7128, -74.0060})
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
            tz_name = timezone_finder.timezone_at(lat=lat, lng=lon)
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
