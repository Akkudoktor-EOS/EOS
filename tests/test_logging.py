"""Test Module for logging Module."""
import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from loguru import logger

from akkudoktoreos.core.logging import logging_track_config

# -----------------------------
# logsettings
# -----------------------------

class TestLoggingCommonSettings:
    def teardown_method(self):
        """Reset Loguru after each test to avoid handler contamination."""
        logger.remove()

    def test_valid_console_level_sets_logging(self, config_eos, caplog):
        config_eos.track_nested_value("/logging", logging_track_config)
        config_eos.set_nested_value("/logging/console_level", "INFO")
        assert config_eos.get_nested_value("/logging/console_level") == "INFO"
        assert config_eos.logging.console_level == "INFO"
        assert any("console: INFO" in message for message in caplog.messages)

    def test_valid_console_level_calls_tracking_callback(self, config_eos):
        with patch("akkudoktoreos.core.logging.logging_track_config") as mock_setup:
            config_eos.track_nested_value("/logging", mock_setup)
            config_eos.set_nested_value("/logging/console_level", "INFO")
            assert config_eos.get_nested_value("/logging/console_level") == "INFO"
            assert config_eos.logging.console_level == "INFO"
            mock_setup.assert_called_once()
