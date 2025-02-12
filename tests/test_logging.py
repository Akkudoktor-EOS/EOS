"""Test Module for logging Module."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from akkudoktoreos.core.logging import get_logger

# -----------------------------
# get_logger
# -----------------------------


def test_get_logger_console_logging():
    """Test logger creation with console logging."""
    logger = get_logger("test_logger", logging_level="DEBUG")

    # Check logger name
    assert logger.name == "test_logger"

    # Check logger level
    assert logger.level == logging.DEBUG

    # Check console handler is present
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.StreamHandler)


def test_get_logger_file_logging(tmpdir):
    """Test logger creation with file logging."""
    log_file = Path(tmpdir).joinpath("test.log")
    logger = get_logger("test_logger", log_file=str(log_file), logging_level="WARNING")

    # Check logger name
    assert logger.name == "test_logger"

    # Check logger level
    assert logger.level == logging.WARNING

    # Check console handler is present
    assert len(logger.handlers) == 2  # One for console and one for file
    assert isinstance(logger.handlers[0], logging.StreamHandler)
    assert isinstance(logger.handlers[1], RotatingFileHandler)

    # Check file existence
    assert log_file.exists()


def test_get_logger_no_file_logging():
    """Test logger creation without file logging."""
    logger = get_logger("test_logger")

    # Check logger name
    assert logger.name == "test_logger"

    # Check logger level
    assert logger.level == logging.INFO

    # Check no file handler is present
    assert len(logger.handlers) >= 1  # First is console handler (maybe be pytest handler)
    assert isinstance(logger.handlers[0], logging.StreamHandler)


def test_get_logger_with_invalid_level():
    """Test logger creation with an invalid logging level."""
    with pytest.raises(ValueError, match="Unknown loggin level: INVALID"):
        logger = get_logger("test_logger", logging_level="INVALID")
