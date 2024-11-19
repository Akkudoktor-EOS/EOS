"""Test Module for logutil Module."""

import logging
import os
from logging.handlers import RotatingFileHandler

import pytest

from akkudoktoreos.utils.logutil import get_logger

# -----------------------------
# get_logger
# -----------------------------


@pytest.fixture
def clean_up_log_file():
    """Fixture to clean up log files after tests."""
    log_file = "test.log"
    yield log_file
    if os.path.exists(log_file):
        os.remove(log_file)


def test_get_logger_console_logging(clean_up_log_file):
    """Test logger creation with console logging."""
    logger = get_logger("test_logger", logging_level="DEBUG")

    # Check logger name
    assert logger.name == "test_logger"

    # Check logger level
    assert logger.level == logging.DEBUG

    # Check console handler is present
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.StreamHandler)


def test_get_logger_file_logging(clean_up_log_file):
    """Test logger creation with file logging."""
    logger = get_logger("test_logger", log_file="test.log", logging_level="WARNING")

    # Check logger name
    assert logger.name == "test_logger"

    # Check logger level
    assert logger.level == logging.WARNING

    # Check console handler is present
    assert len(logger.handlers) == 2  # One for console and one for file
    assert isinstance(logger.handlers[0], logging.StreamHandler)
    assert isinstance(logger.handlers[1], RotatingFileHandler)

    # Check file existence
    assert os.path.exists("test.log")


def test_get_logger_no_file_logging(clean_up_log_file):
    """Test logger creation without file logging."""
    logger = get_logger("test_logger")

    # Check logger name
    assert logger.name == "test_logger"

    # Check logger level
    assert logger.level == logging.INFO

    # Check no file handler is present
    assert len(logger.handlers) >= 1  # First is console handler (maybe be pytest handler)
    assert isinstance(logger.handlers[0], logging.StreamHandler)


def test_get_logger_with_invalid_level(clean_up_log_file):
    """Test logger creation with an invalid logging level."""
    logger = get_logger("test_logger", logging_level="INVALID")

    # Check logger name
    assert logger.name == "test_logger"

    # Check default logging level is DEBUG
    assert logger.level == logging.DEBUG
