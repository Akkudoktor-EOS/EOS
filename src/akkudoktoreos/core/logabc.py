"""Abstract and base classes for logging."""

import logging


def logging_str_to_level(level_str: str) -> int:
    """Convert log level string to logging level."""
    if level_str == "DEBUG":
        level = logging.DEBUG
    elif level_str == "INFO":
        level = logging.INFO
    elif level_str == "WARNING":
        level = logging.WARNING
    elif level_str == "CRITICAL":
        level = logging.CRITICAL
    elif level_str == "ERROR":
        level = logging.ERROR
    else:
        raise ValueError(f"Unknown loggin level: {level_str}")
    return level
