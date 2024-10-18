"""Test Module for datetimeutil Module."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from akkudoktoreos.datetimeutil import to_datetime, to_timedelta

# -----------------------------
# to_datetime
# -----------------------------


def test_to_datetime():
    """Test date conversion as needed by PV forecast data."""
    date_time = to_datetime(
        "2024-10-07T10:20:30.000+02:00", to_timezone="Europe/Berlin", to_naiv=False
    )
    expected_date_time = datetime(2024, 10, 7, 10, 20, 30, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    assert date_time == expected_date_time

    date_time = to_datetime(
        "2024-10-07T10:20:30.000+02:00", to_timezone="Europe/Berlin", to_naiv=True
    )
    expected_date_time = datetime(2024, 10, 7, 10, 20, 30, 0)
    assert date_time == expected_date_time

    date_time = to_datetime("2024-10-07", to_timezone="Europe/Berlin", to_naiv=False)
    expected_date_time = datetime(2024, 10, 7, 0, 0, 0, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    assert date_time == expected_date_time

    date_time = to_datetime("2024-10-07", to_timezone="Europe/Berlin", to_naiv=True)
    expected_date_time = datetime(2024, 10, 7, 0, 0, 0, 0)
    assert date_time == expected_date_time


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
