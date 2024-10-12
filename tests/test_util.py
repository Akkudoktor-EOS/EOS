"""Test Module for Utilities Module."""

from datetime import datetime
from zoneinfo import ZoneInfo

from akkudoktoreos.util import to_datetime

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
