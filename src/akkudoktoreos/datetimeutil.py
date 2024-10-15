"""Utility functions for date-time conversion tasks.

Functions:
----------
- to_datetime: Converts various date or time inputs to a timezone-aware or naive `datetime`
  object or formatted string.
- to_timedelta: Converts various time delta inputs to a `timedelta`object.

Example usage:
--------------

    # Date-time conversion
    >>> date_str = "2024-10-15"
    >>> date_obj = to_datetime(date_str)
    >>> print(date_obj)  # Output: datetime object for '2024-10-15'

    # Time delta conversion
    >>> to_timedelta("2 days 5 hours")
"""

import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional, Union
from zoneinfo import ZoneInfo


def to_datetime(
    date_input: Union[datetime, date, str, int, float, None],
    as_string: Optional[str] = None,
    to_timezone: Optional[Union[timezone, str]] = None,
    to_naiv: Optional[bool] = None,
    to_maxtime: Optional[bool] = None,
):
    """Converts a date input to a datetime object or a formatted string with timezone support.

    Args:
        date_input (Union[datetime, date, str, int, float, None]): The date input to convert.
            Accepts a date string, a datetime object, a date object or a Unix timestamp.
        as_string (Optional[str]): If format string is given return datetime as a string.
                                   Otherwise, return datetime object. The default.
        to_timezone (Optional[Union[timezone, str]]):
                            Optional timezone object or name (e.g., 'UTC', 'Europe/Berlin').
                            If provided, the datetime will be converted to this timezone.
                            If not provided, the datetime will be converted to the local timezone.
        to_naiv (Optional[bool]):
                        If True, remove timezone info from datetime after conversion. The default.
                        If False, keep timezone info after conversion.
        to_maxtime (Optional[bool]):
                        If True, convert to maximum time of no time is given. The default.
                        If False, convert to minimum time if no time is given.

    Example:
        to_datetime("2027-12-12 24:13:12", as_string = "%Y-%m-%dT%H:%M:%S.%f%z")

    Returns:
        datetime or str: Converted date as a datetime object or a formatted string with timezone.

    Raises:
        ValueError: If the date input is not a valid type or format.
    """
    if isinstance(date_input, datetime):
        dt_object = date_input
    elif isinstance(date_input, date):
        # Convert date object to datetime object
        if to_maxtime is None or to_maxtime:
            dt_object = datetime.combine(date_input, time.max)
        else:
            dt_object = datetime.combine(date_input, time.max)
    elif isinstance(date_input, (int, float)):
        # Convert timestamp to datetime object
        dt_object = datetime.fromtimestamp(date_input, tz=timezone.utc)
    elif isinstance(date_input, str):
        # Convert string to datetime object
        try:
            # Try ISO format
            dt_object = datetime.fromisoformat(date_input[:-1])  # Remove 'Z' for UTC
        except ValueError as e:
            formats = [
                "%Y-%m-%d",  # Format: 2024-10-13
                "%d/%m/%Y",  # Format: 13/10/2024
                "%m-%d-%Y",  # Format: 10-13-2024
                "%Y.%m.%d",  # Format: 2024.10.13
                "%d %b %Y",  # Format: 13 Oct 2024
                "%d %B %Y",  # Format: 13 October 2024
                "%Y-%m-%d %H:%M:%S%z",  # Format with timezone: 2024-10-13 15:30:00+0000
                "%Y-%m-%d %H:%M:%S %Z",  # Format with timezone: 2024-10-13 15:30:00 UTC
                "%Y-%m-%dT%H:%M:%S.%f%z",  # Format with timezone: 2024-10-13T15:30:00.000+0000
            ]

            for fmt in formats:
                try:
                    dt_object = datetime.strptime(date_input, fmt)
                    break
                except ValueError as e:
                    dt_object = None
                    continue
            if dt_object is None:
                raise ValueError(f"Date string {date_input} does not match any known formats.")
    elif date_input is None:
        if to_maxtime is None or to_maxtime:
            dt_object = datetime.combine(date.today(), time.max)
        else:
            dt_object = datetime.combine(date.today(), time.min)
    else:
        raise ValueError(f"Unsupported date input type: {type(date_input)}")

    # Get local timezone
    local_date = datetime.now().astimezone()
    local_tz_name = local_date.tzname()
    local_utc_offset = local_date.utcoffset()
    local_timezone = timezone(local_utc_offset, local_tz_name)

    # Get target timezone
    if to_timezone:
        if isinstance(to_timezone, timezone):
            target_timezone = to_timezone
        elif isinstance(to_timezone, str):
            try:
                target_timezone = ZoneInfo(to_timezone)
            except Exception as e:
                raise ValueError(f"Invalid timezone: {to_timezone}") from e
        else:
            raise ValueError(f"Invalid timezone: {to_timezone}")

    # Adjust/Add timezone information
    if dt_object.tzinfo is None or dt_object.tzinfo.utcoffset(dt_object) is None:
        # datetime object is naive (not timezone aware)
        # Add timezone
        if to_timezone is None:
            # Add local timezone
            dt_object = dt_object.replace(tzinfo=local_timezone)
        else:
            # Set to target timezone
            dt_object = dt_object.replace(tzinfo=target_timezone)
    elif to_timezone:
        # Localize the datetime object to given target timezone
        dt_object = dt_object.astimezone(target_timezone)
    else:
        # Localize the datetime object to local timezone
        dt_object = dt_object.astimezone(local_timezone)

    if to_naiv is None or to_naiv:
        # naiv not given defaults to True
        # Remove timezone info to make the datetime naiv
        dt_object = dt_object.replace(tzinfo=None)

    if as_string:
        # Return formatted string as defined by as_string
        return dt_object.strftime(as_string)
    else:
        return dt_object


def to_timedelta(input_value):
    """Converts various input types into a timedelta object.

    Args:
        input_value (Union[timedelta, str, int, float, tuple, list]): Input to be converted
            timedelta.
            - str: A string like "2 days", "5 hours", "30 minutes", or a combination.
            - int/float: Number representing seconds.
            - tuple/list: A tuple or list in the format (days, hours, minutes, seconds).

    Returns:
        timedelta: A timedelta object corresponding to the input value.

    Raises:
        ValueError: If the input format is not supported.

    Examples:
        >>> to_timedelta("2 days 5 hours")
        datetime.timedelta(days=2, seconds=18000)

        >>> to_timedelta(3600)
        datetime.timedelta(seconds=3600)

        >>> to_timedelta((1, 2, 30, 15))
        datetime.timedelta(days=1, seconds=90315)
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
            raise ValueError(f"Expected a tuple or list of length 4, got {len(input_value)}")

    elif isinstance(input_value, str):
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
            raise ValueError(f"Invalid time string format: {input_value}")

        for value, unit in matches:
            unit = unit.lower().rstrip("s")  # Normalize unit
            if unit in time_units:
                total_seconds += int(value) * time_units[unit]
            else:
                raise ValueError(f"Unsupported time unit: {unit}")

        return timedelta(seconds=total_seconds)

    else:
        raise ValueError(f"Unsupported input type: {type(input_value)}")
