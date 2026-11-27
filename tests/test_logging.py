"""Test Module for logging Module."""
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from loguru import logger

from akkudoktoreos.core.logging import (
    _iter_lines_reverse,
    logging_track_config,
    read_file_log,
)
from akkudoktoreos.server import eos
from akkudoktoreos.utils.datetimeutil import to_datetime

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


# -----------------------------
# read_file_log
# -----------------------------

LOG_START = to_datetime("2026-11-27T10:00:00+00:00")


@pytest.fixture
def serialized_log(tmp_path):
    """Write records through a sink configured like the EOS file sink (``serialize=True``).

    Record ``i`` is logged at ``LOG_START + i minutes``: ``info 0`` .. ``info 299``, a warning
    ``snow: coverage <i>%`` after ``info 50``, ``info 150`` and ``info 250``, then ``last line``.
    """
    path = tmp_path / "eos.log"
    sink_id = logger.add(path, serialize=True, level="DEBUG", format="{message}")
    minute = 0

    def log_at(level: str, message: str) -> None:
        nonlocal minute
        at = LOG_START.add(minutes=minute)
        logger.patch(lambda record: record.update(time=at)).log(level, message)
        minute += 1

    for i in range(300):
        log_at("INFO", f"info {i}")
        if i % 100 == 50:
            log_at("WARNING", f"snow: coverage {i}%")
    log_at("ERROR", "last line")
    logger.remove(sink_id)
    return path


def _messages(logs: list[dict]) -> list[str]:
    return [log["record"]["message"] for log in logs]


class TestReadFileLog:
    def test_level_filter(self, serialized_log):
        logs = read_file_log(serialized_log, limit=10, level="warning")
        assert _messages(logs) == [
            "snow: coverage 50%",
            "snow: coverage 150%",
            "snow: coverage 250%",
        ]

    def test_contains_filter_ignores_case(self, serialized_log):
        assert len(read_file_log(serialized_log, limit=10, contains="COVERAGE")) == 3

    def test_regex_filter(self, serialized_log):
        logs = read_file_log(serialized_log, limit=10, regex=r"coverage 1\d0%")
        assert _messages(logs) == ["snow: coverage 150%"]

    def test_invalid_regex_raises_value_error(self, serialized_log):
        with pytest.raises(ValueError, match="regular expression"):
            read_file_log(serialized_log, regex="(")

    def test_time_filter(self, serialized_log):
        # Minute 2 is "info 2", minute 4 is "info 4".
        logs = read_file_log(
            serialized_log,
            from_time="2026-11-27T10:02:00+00:00",
            to_time="2026-11-27T11:04:00+01:00",
        )
        assert _messages(logs) == ["info 2", "info 3", "info 4"]

    @pytest.mark.parametrize(
        ("timezone", "expected"),
        [(None, ["info 2"]), ("Europe/Berlin", []), ("America/New_York", [])],
    )
    def test_time_without_offset_is_in_given_timezone(self, serialized_log, timezone, expected):
        logs = read_file_log(
            serialized_log,
            from_time="2026-11-27T10:02:00",
            to_time="2026-11-27T10:02:30",
            timezone=timezone,
        )
        assert _messages(logs) == expected
        if timezone == "Europe/Berlin":
            logs = read_file_log(
                serialized_log,
                from_time="2026-11-27T11:02:00",
                to_time="2026-11-27T11:02:30",
                timezone=timezone,
            )
            assert _messages(logs) == ["info 2"]

    def test_invalid_time_raises_value_error(self, serialized_log):
        with pytest.raises(ValueError, match="date/time"):
            read_file_log(serialized_log, from_time="not a time")

    def test_tail_returns_newest_in_file_order(self, serialized_log):
        logs = read_file_log(serialized_log, limit=3, tail=True)
        assert _messages(logs) == ["info 298", "info 299", "last line"]

    def test_tail_with_filter_finds_entry_far_back(self, serialized_log):
        # The newest warning is ~50 lines back, beyond any window of `limit * 5` lines.
        logs = read_file_log(serialized_log, limit=1, tail=True, level="WARNING")
        assert _messages(logs) == ["snow: coverage 250%"]

    def test_without_tail_returns_oldest_matches(self, serialized_log):
        logs = read_file_log(serialized_log, limit=2, contains="coverage")
        assert _messages(logs) == ["snow: coverage 50%", "snow: coverage 150%"]

    def test_skips_incomplete_last_line(self, serialized_log):
        with serialized_log.open("a", encoding="utf-8") as f:
            f.write('{"text": "partial", "record": {"mess')
        logs = read_file_log(serialized_log, limit=1, tail=True)
        assert _messages(logs) == ["last line"]

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_file_log(tmp_path / "missing.log")


class TestIterLinesReverse:
    @pytest.mark.parametrize("block_size", [1, 7, 64, 65536])
    def test_block_boundaries(self, tmp_path, block_size):
        path = tmp_path / "x.log"
        path.write_bytes("a\nbä\n\nccc\r\nd".encode("utf-8"))
        with path.open("rb") as f:
            got = [line.decode("utf-8").strip() for line in _iter_lines_reverse(f, block_size)]
        assert got == ["d", "ccc", "bä", "a"]


class TestLoggingGetLogEndpoint:
    @pytest.fixture
    def client(self, config_eos, monkeypatch, serialized_log):
        config = SimpleNamespace(
            logging=SimpleNamespace(file_path=serialized_log),
            general=SimpleNamespace(timezone="Europe/Berlin"),
        )
        monkeypatch.setattr(eos, "get_config", lambda: config)
        return TestClient(eos.app)

    def test_tail_with_level(self, client):
        response = client.get("/v1/logging/log", params={"tail": True, "limit": 1, "level": "WARNING"})
        assert response.status_code == 200
        assert _messages(response.json()) == ["snow: coverage 250%"]

    def test_time_without_offset_uses_location_timezone(self, client):
        response = client.get(
            "/v1/logging/log",
            params={"from_time": "2026-11-27T11:02:00", "to_time": "2026-11-27T11:02:30"},
        )
        assert response.status_code == 200
        assert _messages(response.json()) == ["info 2"]

    def test_invalid_regex_is_client_error(self, client):
        response = client.get("/v1/logging/log", params={"regex": "("})
        assert response.status_code == 400
        assert response.headers["content-type"].startswith("application/problem+json")
