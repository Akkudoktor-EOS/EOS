"""Test Module for datetimeutil Module."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from akkudoktoreos.datetimeutil import to_datetime, to_timedelta, to_timezone

# -----------------------------
# to_datetime
# -----------------------------


# Test cases for valid timedelta inputs
@pytest.mark.parametrize(
    "date_input, as_string, to_timezone, to_naiv, to_maxtime, expected_output",
    [
        # as datetime object
        (
            "2024-10-07T10:20:30.000+02:00",
            None,
            "Europe/Berlin",
            None,
            None,
            datetime(2024, 10, 7, 10, 20, 30, 0, tzinfo=ZoneInfo("Europe/Berlin")),
        ),
        (
            "2024-10-07T10:20:30.000+02:00",
            None,
            "Europe/Berlin",
            False,
            None,
            datetime(2024, 10, 7, 10, 20, 30, 0, tzinfo=ZoneInfo("Europe/Berlin")),
        ),
        (
            "2024-10-07T10:20:30.000+02:00",
            None,
            "Europe/Berlin",
            True,
            None,
            datetime(2024, 10, 7, 10, 20, 30, 0),
        ),
        # as string
        ("2024-10-07T10:20:30.000+02:00", "UTC", None, None, None, "2024-10-07T08:20:30+00:00"),
        ("2024-10-07T10:20:30.000+02:00", "utc", None, None, None, "2024-10-07T08:20:30+00:00"),
    ],
)
def test_to_datetime(date_input, as_string, to_timezone, to_naiv, to_maxtime, expected_output):
    """Test datetime conversion with valid inputs."""
    assert (
        to_datetime(
            date_input,
            as_string=as_string,
            to_timezone=to_timezone,
            to_naiv=to_naiv,
            to_maxtime=to_maxtime,
        )
        == expected_output
    )


# -----------------------------
# to_timedelta
# -----------------------------


# Test cases for valid timedelta inputs
@pytest.mark.parametrize(
    "input_value, expected_output",
    [
        # timedelta input
        (timedelta(days=1), timedelta(days=1)),
        # String input
        ("2 days", timedelta(days=2)),
        ("5 hours", timedelta(hours=5)),
        ("30 minutes", timedelta(minutes=30)),
        ("45 seconds", timedelta(seconds=45)),
        ("1 day 2 hours 30 minutes 15 seconds", timedelta(days=1, hours=2, minutes=30, seconds=15)),
        ("3 days 4 hours", timedelta(days=3, hours=4)),
        # Integer/Float input
        (3600, timedelta(seconds=3600)),  # 1 hour
        (86400, timedelta(days=1)),  # 1 day
        (1800.5, timedelta(seconds=1800.5)),  # 30 minutes and 0.5 seconds
        # Tuple/List input
        ((1, 2, 30, 15), timedelta(days=1, hours=2, minutes=30, seconds=15)),
        ([0, 10, 0, 0], timedelta(hours=10)),
    ],
)
def test_to_timedelta_valid(input_value, expected_output):
    """Test to_timedelta with valid inputs."""
    assert to_timedelta(input_value) == expected_output


# -----------------------------
# to_timezone
# -----------------------------


def test_to_timezone_string():
    """Test to_timezone function returns correct timezone as a string."""
    lat, lon = 40.7128, -74.0060  # New York City coordinates
    result = to_timezone(lat, lon, as_string=True)
    assert result == "America/New_York", "Expected timezone string 'America/New_York'"


def test_to_timezone_zoneinfo():
    """Test to_timezone function returns correct timezone as a ZoneInfo object."""
    lat, lon = 40.7128, -74.0060  # New York City coordinates
    result = to_timezone(lat, lon)
    assert isinstance(result, ZoneInfo), "Expected a ZoneInfo object"
    assert result.key == "America/New_York", "Expected ZoneInfo key 'America/New_York'"


def test_to_timezone_invalid_coordinates():
    """Test to_timezone function handles invalid coordinates gracefully."""
    lat, lon = 100.0, 200.0  # Invalid coordinates outside Earth range
    with pytest.raises(ValueError, match="Invalid location"):
        to_timezone(lat, lon, as_string=True)
