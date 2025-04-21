"""Test Module for pendulum.datetimeutil Module."""

import pendulum
import pytest
import re
from pendulum.tz.timezone import Timezone

from akkudoktoreos.utils.datetimeutil import (
    compare_datetimes,
    hours_in_day,
    to_datetime,
    to_duration,
    to_timezone,
    MAX_DURATION_STRING_LENGTH,
    DatetimesComparisonResult,
)

# -----------------------------
# to_datetime
# -----------------------------


# Test cases for valid pendulum.duration inputs
@pytest.mark.parametrize(
    "test_case, local_timezone, date_input, as_string, in_timezone, to_naiv, to_maxtime, expected_output, expected_approximately",
    [
        # ---------------------------------------
        # from string to pendulum.datetime object
        # ---------------------------------------
        # - no timezone
        (
            "TC001",
            "Etc/UTC",
            "2024-01-01",
            None,
            None,
            None,
            False,
            pendulum.datetime(2024, 1, 1, 0, 0, 0, tz="Etc/UTC"),
            False,
        ),
        (
            "TC002",
            "Europe/Berlin",
            "2024-01-01",
            None,
            None,
            None,
            False,
            pendulum.datetime(2024, 1, 1, 0, 0, 0, tz="Europe/Berlin"),
            False,
        ),
        (
            "TC003",
            "Europe/Berlin",
            "2024-01-01",
            None,
            None,
            None,
            False,
            pendulum.datetime(2023, 12, 31, 23, 0, 0, tz="Etc/UTC"),
            False,
        ),
        (
            "TC004",
            "Europe/Paris",
            "2024-01-01 00:00:00",
            None,
            None,
            None,
            False,
            pendulum.datetime(2024, 1, 1, 0, 0, 0, tz="Europe/Paris"),
            False,
        ),
        (
            "TC005",
            "Etc/UTC",
            "2024-01-01 00:00:00",
            None,
            None,
            None,
            False,
            pendulum.datetime(2024, 1, 1, 1, 0, 0, tz="Europe/Berlin"),
            False,
        ),
        (
            "TC006",
            "Europe/Berlin",
            "2024-01-01 00:00:00",
            None,
            None,
            None,
            False,
            pendulum.datetime(2023, 12, 31, 23, 0, 0, tz="Etc/UTC"),
            False,
        ),
        (
            "TC007",
            "Atlantic/Canary",
            "2024-01-01 12:00:00",
            None,
            None,
            None,
            False,
            pendulum.datetime(
                2024,
                1,
                1,
                12,
                0,
                0,
                tz="Atlantic/Canary",
            ),
            False,
        ),
        (
            "TC008",
            "Etc/UTC",
            "2024-01-01 12:00:00",
            None,
            None,  # force local timezone
            None,
            False,
            pendulum.datetime(2024, 1, 1, 13, 0, 0, tz="Europe/Berlin"),
            False,
        ),
        (
            "TC009",
            "Europe/Berlin",
            "2024-01-01 12:00:00",
            None,
            None,
            None,
            False,
            pendulum.datetime(2024, 1, 1, 11, 0, 0, tz="Etc/UTC"),
            False,
        ),
        # - with timezone
        (
            "TC010",
            "Etc/UTC",
            "02/02/24",
            None,
            "Europe/Berlin",
            None,
            False,
            pendulum.datetime(2024, 2, 2, 0, 0, 0, tz="Europe/Berlin"),
            False,
        ),
        (
            "TC011",
            "Etc/UTC",
            "2024-03-03T10:20:30.000+01:00",  # No dalight saving time at this date
            None,
            "Europe/Berlin",
            None,
            None,
            pendulum.datetime(2024, 3, 3, 10, 20, 30, 0, tz="Europe/Berlin"),
            False,
        ),
        (
            "TC012",
            "Etc/UTC",
            "2024-04-04T10:20:30.000+02:00",
            None,
            "Europe/Berlin",
            False,
            None,
            pendulum.datetime(2024, 4, 4, 10, 20, 30, 0, tz="Europe/Berlin"),
            False,
        ),
        (
            "TC013",
            "Etc/UTC",
            "2024-05-05T10:20:30.000+02:00",
            None,
            "Europe/Berlin",
            True,
            None,
            pendulum.naive(2024, 5, 5, 10, 20, 30, 0),
            False,
        ),
        # - without local timezone as UTC
        (
            "TC014",
            "Atlantic/Canary",
            "02/02/24",
            None,
            "UTC",
            None,
            False,
            pendulum.datetime(2024, 2, 2, 0, 0, 0, tz="UTC"),
            False,
        ),
        (
            "TC015",
            "Atlantic/Canary",
            "2024-03-03T10:20:30.000Z",  # No dalight saving time at this date
            None,
            None,
            None,
            None,
            pendulum.datetime(2024, 3, 3, 10, 20, 30, 0, tz="UTC"),
            False,
        ),
        # ---------------------------------------
        # from pendulum.datetime to pendulum.datetime object
        # ---------------------------------------
        (
            "TC016",
            "Atlantic/Canary",
            pendulum.datetime(2024, 4, 4, 0, 0, 0),
            None,
            None,
            None,
            False,
            pendulum.datetime(2024, 4, 4, 0, 0, 0, tz="Etc/UTC"),
            False,
        ),
        (
            "TC017",
            "Atlantic/Canary",
            pendulum.datetime(2024, 4, 4, 1, 0, 0),
            None,
            "Europe/Berlin",
            None,
            False,
            pendulum.datetime(2024, 4, 4, 3, 0, 0, tz="Europe/Berlin"),
            False,
        ),
        (
            "TC018",
            "Atlantic/Canary",
            pendulum.datetime(2024, 4, 4, 1, 0, 0, tz="Etc/UTC"),
            None,
            "Europe/Berlin",
            None,
            False,
            pendulum.datetime(2024, 4, 4, 3, 0, 0, tz="Europe/Berlin"),
            False,
        ),
        (
            "TC019",
            "Atlantic/Canary",
            pendulum.datetime(2024, 4, 4, 2, 0, 0, tz="Europe/Berlin"),
            None,
            "Etc/UTC",
            None,
            False,
            pendulum.datetime(2024, 4, 4, 0, 0, 0, tz="Etc/UTC"),
            False,
        ),
        # ---------------------------------------
        # from string to UTC string
        # ---------------------------------------
        # - no timezone
        #   local timezone UTC
        (
            "TC020",
            "Etc/UTC",
            "2023-11-06T00:00:00",
            "UTC",
            None,
            None,
            None,
            "2023-11-06T00:00:00Z",
            False,
        ),
        #    local timezone "Europe/Berlin"
        (
            "TC021",
            "Europe/Berlin",
            "2023-11-06T00:00:00",
            "UTC",
            "Europe/Berlin",
            None,
            None,
            "2023-11-05T23:00:00Z",
            False,
        ),
        # - no microseconds
        (
            "TC022",
            "Atlantic/Canary",
            "2024-10-30T00:00:00+01:00",
            "UTC",
            None,
            None,
            None,
            "2024-10-29T23:00:00Z",
            False,
        ),
        (
            "TC023",
            "Atlantic/Canary",
            "2024-10-30T01:00:00+01:00",
            "utc",
            None,
            None,
            None,
            "2024-10-30T00:00:00Z",
            False,
        ),
        # - with microseconds
        (
            "TC024",
            "Atlantic/Canary",
            "2024-10-07T10:20:30.000+02:00",
            "UTC",
            None,
            None,
            None,
            "2024-10-07T08:20:30Z",
            False,
        ),
        # ---------------------------------------
        # from None to pendulum.datetime object
        # ---------------------------------------
        # - no timezone
        #   local timezone
        (
            "TC025",
            None,
            None,
            None,
            None,
            None,
            None,
            pendulum.now(),
            True,
        ),
    ],
)
def test_to_datetime(
    set_other_timezone,
    test_case,
    local_timezone,
    date_input,
    as_string,
    in_timezone,
    to_naiv,
    to_maxtime,
    expected_output,
    expected_approximately,
):
    """Test pendulum.datetime conversion with valid inputs."""
    set_other_timezone(local_timezone)
    result = to_datetime(
        date_input,
        as_string=as_string,
        in_timezone=in_timezone,
        to_naiv=to_naiv,
        to_maxtime=to_maxtime,
    )
    # if isinstance(date_input, str):
    #    print(f"Input:    {date_input}")
    # else:
    #    print(f"Input:    {date_input} tz={date_input.timezone}")
    if isinstance(expected_output, str):
        # print(f"Expected: {expected_output}")
        # print(f"Result:   {result}")
        assert result == expected_output
    elif expected_output.timezone is None:
        # We expect an exception
        with pytest.raises(TypeError):
            assert compare_datetimes(result, expected_output).equal
    else:
        compare = compare_datetimes(result, expected_output)
        # print(f"---- Testcase:  {test_case} ----")
        # print(f"Expected: {expected_output} tz={expected_output.timezone}")
        # print(f"Result:   {result} tz={result.timezone}")
        # print(f"Compare:  {compare}")
        if expected_approximately:
            assert compare.time_diff < 300
        else:
            assert compare.equal == True


# -----------------------------
# to_duration
# -----------------------------


# Test cases for valid duration inputs
@pytest.mark.parametrize(
    "input_value, expected_output",
    [
        # duration input
        (pendulum.duration(days=1), pendulum.duration(days=1)),
        # String input
        ("2 days", pendulum.duration(days=2)),
        ("5 hours", pendulum.duration(hours=5)),
        ("47 hours", pendulum.duration(hours=47)),
        ("48 hours", pendulum.duration(seconds=48 * 3600)),
        ("30 minutes", pendulum.duration(minutes=30)),
        ("45 seconds", pendulum.duration(seconds=45)),
        (
            "1 day 2 hours 30 minutes 15 seconds",
            pendulum.duration(days=1, hours=2, minutes=30, seconds=15),
        ),
        ("3 days 4 hours", pendulum.duration(days=3, hours=4)),
        # Integer/Float input
        (3600, pendulum.duration(seconds=3600)),  # 1 hour
        (86400, pendulum.duration(days=1)),  # 1 day
        (1800.5, pendulum.duration(seconds=1800.5)),  # 30 minutes and 0.5 seconds
        # Tuple/List input
        ((1, 2, 30, 15), pendulum.duration(days=1, hours=2, minutes=30, seconds=15)),
        ([0, 10, 0, 0], pendulum.duration(hours=10)),
    ],
)
def test_to_duration_valid(input_value, expected_output):
    """Test to_duration with valid inputs."""
    assert to_duration(input_value) == expected_output


def test_to_duration_summation():
    start_datetime = to_datetime("2028-01-11 00:00:00")
    index_datetime = start_datetime
    for i in range(48):
        expected_datetime = start_datetime + to_duration(f"{i} hours")
        assert index_datetime == expected_datetime
        index_datetime += to_duration("1 hour")
    assert index_datetime == to_datetime("2028-01-13 00:00:00")


# -----------------------------
# to_timezone
# -----------------------------


def test_to_timezone_string():
    """Test to_timezone function returns correct timezone as a string."""
    location = (40.7128, -74.0060)  # New York City coordinates
    result = to_timezone(location=location, as_string=True)
    assert result == "America/New_York", "Expected timezone string 'America/New_York'"


def test_to_timezone_timezone():
    """Test to_timezone function returns correct timezone as a Timezone object."""
    location = (40.7128, -74.0060)  # New York City coordinates
    result = to_timezone(location=location)
    assert isinstance(result, Timezone), "Expected a Timezone object"
    assert result.name == "America/New_York", "Expected Timezone name 'America/New_York'"


def test_to_timezone_invalid_coordinates():
    """Test to_timezone function handles invalid coordinates gracefully."""
    location = (100.0, 200.0)  # Invalid coordinates outside Earth range
    with pytest.raises(ValueError, match="Invalid latitude/longitude"):
        to_timezone(location=location, as_string=True)


# -----------------------------
# hours_in_day
# -----------------------------


@pytest.mark.parametrize(
    "local_timezone, date, in_timezone, expected_hours",
    [
        ("Etc/UTC", "2024-11-10 00:00:00", "Europe/Berlin", 24),  # No DST in Germany
        ("Etc/UTC", "2024-08-10 00:00:00", "Europe/Berlin", 24),  # DST in Germany
        ("Etc/UTC", "2024-03-31 00:00:00", "Europe/Berlin", 23),  # DST change (23 hours/ day)
        ("Etc/UTC", "2024-10-27 00:00:00", "Europe/Berlin", 25),  # DST change (25 hours/ day)
        ("Europe/Berlin", "2024-11-10 00:00:00", "Europe/Berlin", 24),  # No DST in Germany
        ("Europe/Berlin", "2024-08-10 00:00:00", "Europe/Berlin", 24),  # DST in Germany
        ("Europe/Berlin", "2024-03-31 00:00:00", "Europe/Berlin", 23),  # DST change (23 hours/ day)
        ("Europe/Berlin", "2024-10-27 00:00:00", "Europe/Berlin", 25),  # DST change (25 hours/ day)
    ],
)
def test_hours_in_day(set_other_timezone, local_timezone, date, in_timezone, expected_hours):
    """Test the `test_hours_in_day` function."""
    set_other_timezone(local_timezone)
    date_input = to_datetime(date, in_timezone=in_timezone)
    assert date_input.timezone.name == in_timezone
    assert hours_in_day(date_input) == expected_hours


# -----------------------------
# compare_datetimes
# -----------------------------


@pytest.mark.parametrize(
    "dt1, dt2, equal, ge, gt, le, lt",
    [
        # Same time in the same timezone
        (
            pendulum.datetime(2024, 3, 15, 12, 0, 0, tz="UTC"),
            pendulum.datetime(2024, 3, 15, 12, 0, 0, tz="UTC"),
            True,
            True,
            False,
            True,
            False,
        ),
        (
            pendulum.datetime(2024, 4, 4, 0, 0, 0, tz="Europe/Berlin"),
            pendulum.datetime(2024, 4, 4, 0, 0, 0, tz="Europe/Berlin"),
            True,
            True,
            False,
            True,
            False,
        ),
        # Same instant in different timezones (converted to UTC)
        (
            pendulum.datetime(2024, 3, 15, 8, 0, 0, tz="Europe/Berlin"),
            pendulum.datetime(2024, 3, 15, 7, 0, 0, tz="UTC"),
            True,
            True,
            False,
            True,
            False,
        ),
        # Different times across timezones (converted to UTC)
        (
            pendulum.datetime(2024, 3, 15, 8, 0, 0, tz="America/New_York"),
            pendulum.datetime(2024, 3, 15, 12, 0, 0, tz="UTC"),
            True,
            True,
            False,
            True,
            False,
        ),
    ],
)
def test_compare_datetimes_equal(dt1, dt2, equal, ge, gt, le, lt):
    # requal = compare_datetimes(dt1, dt2).equal
    # rgt = compare_datetimes(dt1, dt2).gt
    # rge = compare_datetimes(dt1, dt2).ge
    # rlt = compare_datetimes(dt1, dt2).lt
    # rle = compare_datetimes(dt1, dt2).le
    # print(f"{dt1} vs. {dt2}: expected equal={equal}, ge={ge}, gt={gt}, le={le}, lt={lt}")
    # print(f"{dt1} vs. {dt2}: result   equal={requal}, ge={rge}, gt={rgt}, le={rle}, lt={rlt}")
    assert compare_datetimes(dt1, dt2).equal == equal
    assert compare_datetimes(dt1, dt2).ge == ge
    assert compare_datetimes(dt1, dt2).gt == gt
    assert compare_datetimes(dt1, dt2).le == le
    assert compare_datetimes(dt1, dt2).lt == lt


@pytest.mark.parametrize(
    "dt1, dt2, equal, ge, gt, le, lt",
    [
        # Different times in the same timezone
        (
            pendulum.datetime(2024, 3, 15, 11, 0, 0, tz="UTC"),
            pendulum.datetime(2024, 3, 15, 12, 0, 0, tz="UTC"),
            False,
            False,
            False,
            True,
            True,
        ),
        # Different times across timezones (converted to UTC)
        (
            pendulum.datetime(2024, 3, 15, 6, 0, 0, tz="America/New_York"),
            pendulum.datetime(2024, 3, 15, 12, 0, 0, tz="UTC"),
            False,
            False,
            False,
            True,
            True,
        ),
        # DST changes: spring forward
        (
            pendulum.datetime(2024, 3, 10, 1, 59, 0, tz="America/New_York"),
            pendulum.datetime(2024, 3, 10, 3, 0, 0, tz="America/New_York"),
            False,
            False,
            False,
            True,
            True,
        ),
        # DST changes: fall back
        (
            pendulum.datetime(2024, 11, 3, 1, 0, 0, tz="America/New_York"),
            pendulum.datetime(2024, 11, 3, 1, 30, 0, tz="America/New_York"),
            False,
            False,
            False,
            True,
            True,
        ),
    ],
)
def test_compare_datetimes_lt(dt1, dt2, equal, ge, gt, le, lt):
    # requal = compare_datetimes(dt1, dt2).equal
    # rgt = compare_datetimes(dt1, dt2).gt
    # rge = compare_datetimes(dt1, dt2).ge
    # rlt = compare_datetimes(dt1, dt2).lt
    # rle = compare_datetimes(dt1, dt2).le
    # print(f"{dt1} vs. {dt2}: expected equal={equal}, ge={ge}, gt={gt}, le={le}, lt={lt}")
    # print(f"{dt1} vs. {dt2}: result   equal={requal}, ge={rge}, gt={rgt}, le={rle}, lt={rlt}")
    assert compare_datetimes(dt1, dt2).equal == equal
    assert compare_datetimes(dt1, dt2).ge == ge
    assert compare_datetimes(dt1, dt2).gt == gt
    assert compare_datetimes(dt1, dt2).le == le
    assert compare_datetimes(dt1, dt2).lt == lt


@pytest.mark.parametrize(
    "dt1, dt2",
    [
        # Different times in the same timezone
        (
            pendulum.datetime(2024, 3, 15, 13, 0, 0, tz="UTC"),
            pendulum.datetime(2024, 3, 15, 12, 0, 0, tz="UTC"),
        ),
    ],
)
def test_compare_datetimes_gt(dt1, dt2):
    # requal = compare_datetimes(dt1, dt2).equal
    # rgt = compare_datetimes(dt1, dt2).gt
    # rge = compare_datetimes(dt1, dt2).ge
    # rlt = compare_datetimes(dt1, dt2).lt
    # rle = compare_datetimes(dt1, dt2).le
    # print(f"{dt1} vs. {dt2}: expected equal={equal}, ge={ge}, gt={gt}, le={le}, lt={lt}")
    # print(f"{dt1} vs. {dt2}: result   equal={requal}, ge={rge}, gt={rgt}, le={rle}, lt={rlt}")
    assert compare_datetimes(dt1, dt2).equal == False
    assert compare_datetimes(dt1, dt2).ge
    assert compare_datetimes(dt1, dt2).gt
    assert compare_datetimes(dt1, dt2).le == False
    assert compare_datetimes(dt1, dt2).lt == False



def test_to_duration_excessive_length_raises_valueerror():
    """
    Test that to_duration raises ValueError for strings exceeding max length.
    This test covers the fix for the ReDoS vulnerability.

    Related to: #494
    """
    # String länger als erlaubt
    long_string = "a" * (MAX_DURATION_STRING_LENGTH + 50)

    # Erwartete Fehlermeldung – ESCAPED für Regex!
    expected_error_message = re.escape(
        f"Input string exceeds maximum allowed length ({MAX_DURATION_STRING_LENGTH})."
    )

    # Prüfen, ob Fehler korrekt ausgelöst wird
    with pytest.raises(ValueError, match=expected_error_message):
        to_duration(long_string)

    # Optional: String genau am Limit darf den Length-Check NICHT triggern
    at_limit_string = "b" * MAX_DURATION_STRING_LENGTH
    try:
        to_duration(at_limit_string)
    except ValueError as e:
        if str(e) == f"Input string exceeds maximum allowed length ({MAX_DURATION_STRING_LENGTH}).":
            pytest.fail(f"to_duration raised length ValueError unexpectedly for string at limit: {at_limit_string}")
        # Alle anderen Fehler sind okay (z. B. Formatfehler)
        pass
