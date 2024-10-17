"""util.py.

This module provides utility functions and a class for in-memory managing of cache files and
handling logging and date-time conversion tasks.

The `CacheFileStore` class is a singleton-based, thread-safe key-value store for managing
temporary file objects, allowing the creation, retrieval, and management of cache files.
Additionally, the module offers a flexible logging setup (`get_logger`) and a utility
to handle different date-time formats (`to_datetime`).

Classes:
--------
- CacheFileStore: A thread-safe, singleton class for in-memory managing of file-like cache objects.
- CacheFileStoreMeta: Metaclass for enforcing the singleton behavior in `CacheFileStore`.

Functions:
----------
- get_logger: Creates and configures a logger with console and optional rotating file logging.
- to_datetime: Converts various date or time inputs to a timezone-aware or naive `datetime`
  object or formatted string.

Example usage:
--------------
    # CacheFileStore usage
    >>> cache_store = CacheFileStore()
    >>> cache_store.create('example_key')
    >>> cache_file = cache_store.get('example_key')
    >>> cache_file.write('Some data')
    >>> cache_file.seek(0)
    >>> print(cache_file.read())  # Output: 'Some data'

    # Logger setup
    >>> logger = get_logger(__name__, log_file="app.log", logging_level="DEBUG")
    >>> logger.info("Logging initialized.")

    # Date-time conversion
    >>> date_str = "2024-10-15"
    >>> date_obj = to_datetime(date_str)
    >>> print(date_obj)  # Output: datetime object for '2024-10-15'

Notes:
------
- Cache files are automatically associated with the current date unless specified.
- The logger supports rotating log files to prevent excessive log file size.
- The `to_datetime` function supports a wide variety of input types and formats.
"""

import logging
import os
from datetime import date, datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Optional, Union
from zoneinfo import ZoneInfo


def get_logger(
    name: str,
    log_file: Optional[str] = None,
    logging_level: Optional[str] = "INFO",
    max_bytes: int = 5000000,
    backup_count: int = 5,
) -> logging.Logger:
    """Creates and configures a logger with a given name.

    The logger supports logging to both the console and an optional log file. File logging is
    handled by a rotating file handler to prevent excessive log file size.

    Args:
        name (str): The name of the logger, typically `__name__` from the calling module.
        log_file (Optional[str]): Path to the log file for file logging. If None, no file logging is done.
        logging_level (Optional[str]): Logging level (e.g., "INFO", "DEBUG"). Defaults to "INFO".
        max_bytes (int): Maximum size in bytes for log file before rotation. Defaults to 5 MB.
        backup_count (int): Number of backup log files to keep. Defaults to 5.

    Returns:
        logging.Logger: Configured logger instance.

    Example:
        logger = get_logger(__name__, log_file="app.log", logging_level="DEBUG")
        logger.info("Application started")
    """
    # Create a logger with the specified name
    logger = logging.getLogger(name)
    logger.propagate = True
    if logging_level == "DEBUG":
        level = logging.DEBUG
    elif logging_level == "INFO":
        level = logging.INFO
    elif logging_level == "WARNING":
        level = logging.WARNING
    elif logging_level == "ERROR":
        level = logging.ERROR
    else:
        level = logging.DEBUG
    logger.setLevel(level)

    # Prevent loggers from being added multiple times
    if not logger.handlers:
        # Create a console handler with a standard output stream
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)

        # Create a formatter that defines the log message format
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        console_handler.setFormatter(formatter)

        # Add the console handler to the logger
        logger.addHandler(console_handler)

        # If a log file path is specified, create a rotating file handler
        if log_file:
            # Ensure the log directory exists
            log_dir = os.path.dirname(log_file)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir)

            # Create a rotating file handler
            file_handler = RotatingFileHandler(
                log_file, maxBytes=max_bytes, backupCount=backup_count
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)

            # Add the file handler to the logger
            logger.addHandler(file_handler)

    return logger


logger = get_logger(__file__)


def to_datetime(
    date_input: Union[datetime, date, str, int, float, None],
    as_string: Optional[str] = None,
    to_timezone: Optional[Union[timezone, str]] = None,
    to_naiv: Optional[bool] = None,
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
        dt_object = datetime.combine(date_input, datetime.min.time())
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
        dt_object = datetime.combine(date.today(), datetime.min.time())
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
