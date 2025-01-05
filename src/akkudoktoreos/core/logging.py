"""Utility functions for handling logging tasks.

Functions:
----------
- get_logger: Creates and configures a logger with console and optional rotating file logging.

Example usage:
--------------
    # Logger setup
    >>> logger = get_logger(__name__, log_file="app.log", logging_level="DEBUG")
    >>> logger.info("Logging initialized.")

Notes:
------
- The logger supports rotating log files to prevent excessive log file size.
"""

import logging as pylogging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional

from akkudoktoreos.core.logabc import logging_str_to_level


def get_logger(
    name: str,
    log_file: Optional[str] = None,
    logging_level: Optional[str] = None,
    max_bytes: int = 5000000,
    backup_count: int = 5,
) -> pylogging.Logger:
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
    logger = pylogging.getLogger(name)
    logger.propagate = True
    if logging_level is not None:
        level = logging_str_to_level(logging_level)
        logger.setLevel(level)

    # The log message format
    formatter = pylogging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # Prevent loggers from being added multiple times
    # There may already be a logger from pytest
    if not logger.handlers:
        # Create a console handler with a standard output stream
        console_handler = pylogging.StreamHandler()
        if logging_level is not None:
            console_handler.setLevel(level)
        console_handler.setFormatter(formatter)

        # Add the console handler to the logger
        logger.addHandler(console_handler)

    if log_file and len(logger.handlers) < 2:  # We assume a console logger to be the first logger
        # If a log file path is specified, create a rotating file handler

        # Ensure the log directory exists
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)

        # Create a rotating file handler
        file_handler = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count)
        if logging_level is not None:
            file_handler.setLevel(level)
        file_handler.setFormatter(formatter)

        # Add the file handler to the logger
        logger.addHandler(file_handler)

    return logger
