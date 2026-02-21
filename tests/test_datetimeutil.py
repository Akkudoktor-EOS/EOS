"""Comprehensive test suite for the date/time utility module.

This test suite covers all classes and functions in the datetimeutil module,
including edge cases, error handling, and timezone behavior.
"""

import datetime
import json
import re
from typing import Any
from unittest.mock import MagicMock, patch

import babel
import pendulum
import pytest
from pendulum.tz.timezone import Timezone
from pydantic import ValidationError

from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.utils.datetimeutil import (
    MAX_DURATION_STRING_LENGTH,
    Date,
    DateTime,
    Duration,
    Time,
    TimeWindow,
    TimeWindowSequence,
    _parse_time_string,
    compare_datetimes,
    hours_in_day,
    to_datetime,
    to_duration,
    to_time,
    to_timezone,
)

# ----------
# Time Class
# ----------


class TestTimeParsing:
    """Comprehensive tests for string → pendulum.Time conversion and roundtrip correctness."""

    # -------------------------------
    # VALID FORMATS
    # -------------------------------
    @pytest.mark.parametrize(
        "input_str, expected",
        [
            # 24-hour basic formats
            ("00:00", (0, 0, 0)),
            ("23:59:59", (23, 59, 59)),
            ("14:30", (14, 30, 0)),
            ("14:30:45", (14, 30, 45)),
            ("14:30:45.123456", (14, 30, 45)),
            ("1430", (14, 30, 0)),
            ("143045", (14, 30, 45)),
            ("930", (9, 30, 0)),
            ("14", (14, 0, 0)),
            ("14.5", (14, 30, 0)),
            ("14.25", (14, 15, 0)),
            ("14h30", (14, 30, 0)),
            ("14-30", (14, 30, 0)),
            ("14 30", (14, 30, 0)),

            # 12-hour AM/PM formats
            ("12:00 AM", (0, 0, 0)),
            ("12:00 PM", (12, 0, 0)),
            ("2:30 PM", (14, 30, 0)),
            ("2:30:45 PM", (14, 30, 45)),
            ("2PM", (14, 0, 0)),
            ("11AM", (11, 0, 0)),

            # Compact & decimal
            ("3", (3, 0, 0)),
            ("23.75", (23, 45, 0)),

            # Offset-based / ISO-like
            ("08:00:00.000000+01:00", (8, 0, 0)),
            ("14:30 +05:30", (14, 30, 0)),
            ("14:30 -03:00", (14, 30, 0)),
            ("22:15 -0800", (22, 15, 0)),

            # Alternative separators
            ("14-30", (14, 30, 0)),
            ("14 30", (14, 30, 0)),
            ("14-30-45", (14, 30, 45)),
            ("14 30 45", (14, 30, 45)),

            # Timezones by abbreviation
            ("14:30 UTC", (14, 30, 0)),
            ("14:30 GMT", (14, 30, 0)),
            ("2:30 PM EST", (14, 30, 0)),
            ("9:15 CST", (9, 15, 0)),
            ("23:59 PST", (23, 59, 0)),

            # Named timezones
            ("14h30 Europe/Berlin", (14, 30, 0)),
            ("14:30 America/New_York", (14, 30, 0)),
            ("08:15 Asia/Tokyo", (8, 15, 0)),
            ("23:45 Australia/Sydney", (23, 45, 0)),
        ],
    )
    def test_parse_time_string_valid(self, input_str, expected):
        """Ensure various valid time strings parse correctly."""
        result = _parse_time_string(input_str)
        assert isinstance(result, pendulum.Time)
        assert (result.hour, result.minute, result.second) == expected[:3]

    # -------------------------------
    # INVALID INPUTS
    # -------------------------------
    @pytest.mark.parametrize(
        "input_str",
        [
            "",              # empty
            "25:00",         # invalid hour
            "14:61",         # invalid minute
            "2:30 XM",       # bad AM/PM
            "noonish",       # nonsense
            "2400",          # invalid compact
            "24.999",        # beyond 23.999
            "14:30 Mars/Terra",  # invalid tz
        ],
    )
    def test_parse_time_string_invalid(self, input_str):
        """Invalid inputs should raise ValueError."""
        with pytest.raises(ValueError):
            _parse_time_string(input_str)

    # -------------------------------
    # TIMEZONE HANDLING
    # -------------------------------
    @pytest.mark.parametrize(
        "input_str, tz_name",
        [
            ("14:30 UTC", "UTC"),
            ("14:30 Europe/Berlin", "Europe/Berlin"),
            ("2:30 PM PST", "America/Los_Angeles"),
            ("08:00:00.000000+01:00", "+01:00"),
            ("14:30 +05:30", "+05:30"),
            ("22:00 -04:00", "-04:00"),
        ],
    )
    def test_parse_time_string_with_timezone(self, input_str, tz_name):
        """Test timezone-aware parsing results in a Time with tzinfo."""
        t = _parse_time_string(input_str)
        assert isinstance(t, pendulum.Time)
        assert t.tzinfo is not None
        # compare normalized zone name
        assert tz_name.split("/")[-1] in str(t.tzinfo) or tz_name in str(t.tzinfo), f"{str(t.tzinfo)} vs. expected {tz_name}"

    @pytest.mark.parametrize(
        "time_str",
        [
            "08:00:00.000000+01:00",
            "14:30 UTC",
            "2:30 PM PST",
            "14h30 Europe/Berlin",
            "23:45 America/New_York",
        ],
    )
    def test_roundtrip_to_string(self, time_str):
        """Test that parsing and serializing preserves hour, minute, offset."""
        t = _parse_time_string(time_str)
        s = t.isoformat()
        reparsed = _parse_time_string(s)
        assert t.hour == reparsed.hour
        assert t.minute == reparsed.minute
        assert t.second == reparsed.second
        assert t.utcoffset() == reparsed.utcoffset()

    def test_microsecond_precision_and_offset(self):
        """Ensure microseconds and offset are exact."""
        t = _parse_time_string("08:00:00.000001+01:00")
        assert t.microsecond == 1
        assert t.strftime("%z") in ("+0100", "+01:00")

    def test_parse_edge_cases(self):
        """Test parsing edge cases."""
        # Test with whitespace
        result = _parse_time_string("  14:30  ")
        assert result.hour == 14
        assert result.minute == 30

        # Test case insensitivity
        result = _parse_time_string("2:30 pm")
        assert result.hour == 14
        assert result.minute == 30

        # Test mixed case
        result = _parse_time_string("14H30")
        assert result.hour == 14
        assert result.minute == 30


class TestTime:
    """Test suite for the custom Time class."""

    def test_time_creation_basic(self):
        """Test basic Time object creation."""
        t = Time(14, 30, 45, 123456)
        assert t.hour == 14
        assert t.minute == 30
        assert t.second == 45
        assert t.microsecond == 123456
        assert t.tzinfo is None

    def test_time_creation_with_timezone(self):
        """Test Time object creation with timezone."""
        berlin_tz = pendulum.timezone("Europe/Berlin")
        t = Time(14, 30, 0, tzinfo=berlin_tz)
        assert t.hour == 14
        assert t.minute == 30
        assert t.second == 0
        assert t.tzinfo == berlin_tz

    def test_pydantic_validation_valid_time(self):
        """Test pydantic validation with valid Time object."""
        berlin_tz = pendulum.timezone("Europe/Berlin")
        pend_time = pendulum.time(14, 30, 0).replace(tzinfo=berlin_tz)

        # This should not raise an exception
        validated = Time._validate(pend_time)
        assert isinstance(validated, Time)
        assert validated.hour == 14
        assert validated.minute == 30
        assert validated.tzinfo == berlin_tz

    def test_pydantic_validation_valid_time_in_other_timezone(self, set_other_timezone):
        """Test pydantic validation with valid Time object running in different timezone."""
        timezone = set_other_timezone()

        berlin_tz = pendulum.timezone("Europe/Berlin")
        pend_time = pendulum.time(14, 30, 0).replace(tzinfo=berlin_tz)
        assert isinstance(pend_time.tzinfo, Timezone)
        assert pend_time.tzinfo == berlin_tz

        # This should not raise an exception
        validated = Time._validate(pend_time)
        assert isinstance(validated, Time)
        assert validated.hour == 14
        assert validated.minute == 30
        assert validated.tzinfo == berlin_tz

    def test_pydantic_validation_string_input(self):
        """Test pydantic validation with string input."""
        time_str = "14:30:45"
        validated = Time._validate(time_str)
        assert isinstance(validated, Time)
        assert validated.hour == 14
        assert validated.minute == 30
        assert validated.second == 45

    def test_pydantic_validation_none_input(self):
        """Test pydantic validation with None input raises ValueError."""
        with pytest.raises(ValueError, match="Time value cannot be None"):
            Time._validate(None)

    def test_pydantic_validation_invalid_input(self):
        """Test pydantic validation with invalid input."""
        with pytest.raises(ValueError, match="Invalid time value"):
            Time._validate("invalid_time")

    def test_serialization_naive_time(self):
        """Test serialization of naive Time object."""
        t = Time(14, 30, 45, 123456)
        serialized = Time._serialize(t)
        assert serialized == "14:30:45.123456"

    def test_serialization_timezone_aware_time(self):
        """Test serialization of timezone-aware Time object."""
        berlin_tz = pendulum.timezone("Europe/Berlin")
        t = Time(14, 30, 45, 123456, tzinfo=berlin_tz)
        serialized = Time._serialize(t)
        assert "14:30:45.123456" in serialized
        assert "Europe/Berlin" in serialized or "+0" in serialized

    def test_serialization_none_value(self):
        """Test serialization of None value."""
        serialized = Time._serialize(None)
        assert serialized == ""

    def test_repr_naive_time(self):
        """Test __repr__ for naive Time."""
        t = Time(14, 30, 45, 123456)
        repr_str = repr(t)
        assert "Time(14, 30, 45, 123456)" in repr_str
        assert "tzinfo" not in repr_str

    def test_repr_timezone_aware_time(self):
        """Test __repr__ for timezone-aware Time."""
        berlin_tz = pendulum.timezone("Europe/Berlin")
        t = Time(14, 30, 45, 123456, tzinfo=berlin_tz)
        repr_str = repr(t)
        assert "Time(14, 30, 45, 123456, tzinfo=" in repr_str
        assert "Europe/Berlin" in repr_str

    def test_str_representation(self):
        """Test __str__ method."""
        t = Time(14, 30, 45, 123456)
        str_repr = str(t)
        assert str_repr == "14:30:45.123456"

    def test_equality_naive_times(self):
        """Test equality comparison for naive times."""
        t1 = Time(14, 30, 45)
        t2 = Time(14, 30, 45)
        t3 = Time(14, 30, 46)

        assert t1 == t2
        assert t1 != t3

    def test_equality_timezone_aware_times(self):
        """Test equality comparison for timezone-aware times."""
        berlin_tz = pendulum.timezone("Europe/Berlin")
        utc_tz = pendulum.timezone("UTC")

        t1 = Time(14, 30, 0, tzinfo=berlin_tz)
        t2 = Time(14, 30, 0, tzinfo=berlin_tz)
        t3 = Time(14, 30, 0, tzinfo=utc_tz)

        assert t1 == t2

    def test_equality_mixed_timezone_naive(self):
        """Test equality comparison between timezone-aware and naive times."""
        berlin_tz = pendulum.timezone("Europe/Berlin")
        t1 = Time(14, 30, 0, tzinfo=berlin_tz)
        t2 = Time(14, 30, 0)  # naive

        # Mixed comparison should use direct comparison
        assert t1 == t2

    def test_hash_naive_time(self):
        """Test hash function for naive time."""
        t1 = Time(14, 30, 45)
        t2 = Time(14, 30, 45)

        assert hash(t1) == hash(t2)

        # Test that times can be used in sets
        time_set = {t1, t2}
        assert len(time_set) == 1

    def test_hash_timezone_aware_time(self):
        """Test hash function for timezone-aware time."""
        berlin_tz = pendulum.timezone("Europe/Berlin")
        t1 = Time(14, 30, 0, tzinfo=berlin_tz)
        t2 = Time(14, 30, 0, tzinfo=berlin_tz)

        assert hash(t1) == hash(t2)

    def test_is_naive(self):
        """Test is_naive method."""
        t_naive = Time(14, 30, 0)
        t_aware = Time(14, 30, 0, tzinfo=pendulum.timezone("UTC"))

        assert t_naive.is_naive() is True
        assert t_aware.is_naive() is False

    def test_is_aware(self):
        """Test is_aware method."""
        t_naive = Time(14, 30, 0)
        t_aware = Time(14, 30, 0, tzinfo=pendulum.timezone("UTC"))

        assert t_naive.is_aware() is False
        assert t_aware.is_aware() is True

    def test_replace_timezone(self):
        """Test replace_timezone method."""
        t = Time(14, 30, 0)
        berlin_tz = pendulum.timezone("Europe/Berlin")

        t_with_tz = t.replace_timezone(berlin_tz)
        assert t_with_tz.tzinfo == berlin_tz
        assert t_with_tz.hour == 14  # Time should not change

        # Test with string timezone
        t_with_str_tz = t.replace_timezone("UTC")
        assert t_with_str_tz.tzinfo == pendulum.timezone("UTC")

    def test_replace_timezone_none(self):
        """Test replace_timezone with None removes timezone."""
        berlin_tz = pendulum.timezone("Europe/Berlin")
        t = Time(14, 30, 0, tzinfo=berlin_tz)

        t_naive = t.replace_timezone(None)
        assert t_naive.tzinfo is None

    def test_format_user_friendly_basic(self):
        """Test format_user_friendly with basic options."""
        t = Time(14, 30, 45)

        # Without seconds
        formatted = t.format_user_friendly(include_seconds=False)
        assert formatted == "14:30"

        # With seconds
        formatted = t.format_user_friendly(include_seconds=True)
        assert formatted == "14:30:45"

    def test_format_user_friendly_with_timezone(self):
        """Test format_user_friendly with timezone."""
        berlin_tz = pendulum.timezone("Europe/Berlin")
        t = Time(14, 30, 45, tzinfo=berlin_tz)

        # Auto-include timezone
        formatted = t.format_user_friendly()
        assert "14:30" in formatted
        assert any(tz_indicator in formatted for tz_indicator in ["+", "-", "Z"])

    def test_now_classmethod(self):
        """Test now() class method."""
        now = Time.now()
        assert isinstance(now, Time)
        assert now.tzinfo is not None  # Should have timezone info

        # Test with specific timezone
        utc_now = Time.now("UTC")
        assert isinstance(utc_now, Time)
        assert utc_now.tzinfo == pendulum.timezone("UTC")

    def test_parse_classmethod(self):
        """Test parse() class method."""
        time_str = "14:30:45"
        parsed = Time.parse(time_str)
        assert isinstance(parsed, Time)
        assert parsed.hour == 14
        assert parsed.minute == 30
        assert parsed.second == 45

    def test_in_timezone_conversion(self):
        """Test in_timezone method for actual timezone conversion."""
        utc_tz = pendulum.timezone("UTC")
        berlin_tz = pendulum.timezone("Europe/Berlin")

        # Create UTC time
        utc_time = Time(12, 0, 0, tzinfo=utc_tz)

        # Convert to Berlin time
        berlin_time = utc_time.in_timezone(berlin_tz)
        assert isinstance(berlin_time, Time)
        assert berlin_time.tzinfo == berlin_tz
        # The actual hour will depend on DST, but it should be different from 12
        # This is a simplified test - you may need to adjust based on actual conversion logic

    def test_in_timezone_naive_time(self):
        """Test in_timezone with naive time."""
        t = Time(14, 30, 0)  # naive
        berlin_tz = pendulum.timezone("Europe/Berlin")

        result = t.in_timezone(berlin_tz)
        assert isinstance(result, Time)
        # Should assume local timezone and convert

    def test_to_local(self):
        """Test to_local method."""
        utc_tz = pendulum.timezone("UTC")
        t = Time(12, 0, 0, tzinfo=utc_tz)

        local_time = t.to_local()
        assert isinstance(local_time, Time)
        assert local_time.tzinfo == pendulum.local_timezone()

    def test_to_utc(self):
        """Test to_utc method."""
        berlin_tz = pendulum.timezone("Europe/Berlin")
        t = Time(14, 0, 0, tzinfo=berlin_tz)

        utc_time = t.to_utc()
        assert isinstance(utc_time, Time)
        assert utc_time.tzinfo == pendulum.timezone("UTC")

    def test_create_from_pendulum_time(self):
        """Test _create_from_pendulum_time class method."""
        berlin_tz = pendulum.timezone("Europe/Berlin")
        pend_time = pendulum.time(14, 30, 45, 123456).replace(tzinfo=berlin_tz)

        custom_time = Time._create_from_pendulum_time(pend_time)
        assert isinstance(custom_time, Time)
        assert custom_time.hour == 14
        assert custom_time.minute == 30
        assert custom_time.second == 45
        assert custom_time.microsecond == 123456
        assert custom_time.tzinfo == berlin_tz


# -------
# to_time
# -------


class TestToTime:
    """Test suite for the to_time function."""

    def test_to_time_string_input(self):
        """Test to_time with string input."""
        result = to_time("14:30:45")
        assert isinstance(result, Time)
        assert result.hour == 14
        assert result.minute == 30
        assert result.second == 45

    def test_to_time_time_object_input(self):
        """Test to_time with Time object input."""
        t = Time(14, 30, 45)
        result = to_time(t)
        assert isinstance(result, Time)
        assert result.hour == 14
        assert result.minute == 30
        assert result.second == 45

    def test_to_time_pendulum_time_input(self):
        """Test to_time with pendulum.Time input."""
        pend_time = pendulum.time(14, 30, 45)
        result = to_time(pend_time)
        assert isinstance(result, Time)
        assert result.hour == 14
        assert result.minute == 30
        assert result.second == 45

    def test_to_time_datetime_time_input(self):
        """Test to_time with datetime.time input."""
        dt_time = datetime.time(14, 30, 45)
        result = to_time(dt_time)
        assert isinstance(result, Time)
        assert result.hour == 14
        assert result.minute == 30
        assert result.second == 45

    def test_to_time_datetime_datetime_input(self):
        """Test to_time with datetime.datetime input."""
        dt_datetime = datetime.datetime(2023, 10, 15, 14, 30, 45)
        result = to_time(dt_datetime, in_timezone = "UTC")
        assert isinstance(result, Time)
        assert result.hour == 14
        assert result.minute == 30
        assert result.second == 45

    def test_to_time_integer_input(self):
        """Test to_time with integer input (hour only)."""
        result = to_time(14)
        assert isinstance(result, Time)
        assert result.hour == 14
        assert result.minute == 0
        assert result.second == 0

    def test_to_time_float_input(self):
        """Test to_time with float input (decimal hours)."""
        result = to_time(14.5)  # 14:30
        assert isinstance(result, Time)
        assert result.hour == 14
        assert result.minute == 30
        assert result.second == 0

    def test_to_time_tuple_input(self):
        """Test to_time with tuple input."""
        test_cases = [
            ((14,), 14, 0, 0, 0),
            ((14, 30), 14, 30, 0, 0),
            ((14, 30, 45), 14, 30, 45, 0),
            ((14, 30, 45, 123456), 14, 30, 45, 123456),
        ]

        for tuple_input, expected_hour, expected_minute, expected_second, expected_microsecond in test_cases:
            result = to_time(tuple_input)
            assert isinstance(result, Time)
            assert result.hour == expected_hour
            assert result.minute == expected_minute
            assert result.second == expected_second
            assert result.microsecond == expected_microsecond

    def test_to_time_with_timezone(self):
        """Test to_time with timezone parameter."""
        result = to_time("14:30", in_timezone="Europe/Berlin")
        assert isinstance(result, Time)
        assert result.hour == 14
        assert result.minute == 30
        assert result.tzinfo == pendulum.timezone("Europe/Berlin")

    def test_to_time_to_naive(self):
        """Test to_time with to_naive=True."""
        #result = to_time("14:30", in_timezone="Europe/Berlin", to_naive=True)
        result = to_time("14:30", to_naive=True)
        #result = to_time("14:30")
        assert isinstance(result, Time)
        assert result.hour == 14
        assert result.minute == 30
        assert result.tzinfo is None

    def test_to_time_as_string_true(self):
        """Test to_time with as_string=True."""
        result = to_time("14:30:45", as_string=True)
        assert isinstance(result, str)
        assert "14:30:45" in result

    def test_to_time_as_string_format(self):
        """Test to_time with custom format string."""
        result = to_time("14:30:45", as_string="HH:mm")
        assert isinstance(result, str)
        assert result == "14:30"

    def test_to_time_timezone_conversion(self):
        """Test to_time with timezone conversion."""
        berlin_tz = pendulum.timezone("Europe/Berlin")
        utc_tz = pendulum.timezone("UTC")

        # Create time with Berlin timezone
        berlin_time = pendulum.time(14, 30, 0).replace(tzinfo=berlin_tz)

        # Convert to UTC
        result = to_time(berlin_time, in_timezone="UTC")
        assert isinstance(result, Time)
        assert result.tzinfo == utc_tz
        # The hour should be different due to timezone conversion

    def test_to_time_invalid_timezone(self):
        """Test to_time with invalid timezone."""
        with pytest.raises(ValueError, match="Invalid timezone"):
            to_time("14:30", in_timezone="Invalid/Timezone")

    def test_to_time_invalid_input_type(self):
        """Test to_time with invalid input type."""
        with pytest.raises(ValueError, match="Unsupported type"):
            to_time({"invalid": "input"})

    def test_to_time_invalid_hour_integer(self):
        """Test to_time with invalid hour as integer."""
        with pytest.raises(ValueError, match="Hour must be between 0 and 23"):
            to_time(25)

    def test_to_time_invalid_hour_float(self):
        """Test to_time with invalid hour as float."""
        with pytest.raises(ValueError, match="Hour must be between 0 and 23"):
            to_time(25.5)

    def test_to_time_empty_tuple(self):
        """Test to_time with empty tuple."""
        with pytest.raises(ValueError, match="Empty tuple provided"):
            to_time(())

    def test_to_time_pendulum_datetime_input(self):
        """Test to_time with pendulum DateTime input."""
        dt = pendulum.datetime(2023, 10, 15, 14, 30, 45)
        result = to_time(dt, in_timezone = "UTC")
        assert isinstance(result, Time)
        assert result.hour == 14
        assert result.minute == 30
        assert result.second == 45

    def test_to_time_with_timezone_object(self):
        """Test to_time with timezone object instead of string."""
        berlin_tz = pendulum.timezone("Europe/Berlin")
        result = to_time("14:30", in_timezone=berlin_tz)
        assert isinstance(result, Time)
        assert result.tzinfo == berlin_tz

    def test_to_time_invalid_timezone_type(self):
        """Test to_time with invalid timezone type."""
        with pytest.raises(ValueError, match="Invalid timezone"):
            to_time("14:30", in_timezone=123)

    def test_to_time_microseconds_precision(self):
        """Test to_time preserves microsecond precision."""
        result = to_time("14:30:45.123456")
        assert isinstance(result, Time)
        assert result.microsecond == 123456

    def test_to_time_fallback_parsing(self):
        """Test to_time fallback parsing mechanisms."""
        # Test with a format that might not be caught by the main parser
        # This tests the fallback to pendulum.parse
        result = to_time("14:30:45")
        assert isinstance(result, Time)
        assert result.hour == 14
        assert result.minute == 30
        assert result.second == 45

    @patch('akkudoktoreos.utils.datetimeutil.logger.trace')
    def test_to_time_logging_on_parse_failures(self, mock_trace):
        """Test that parsing failures are logged appropriately."""
        # This test verifies that failed parsing attempts are logged
        with pytest.raises(ValueError):
            to_time("definitely_invalid_time_format")

        # Verify that trace logs were called for failed parsing attempts
        assert mock_trace.called

    def test_to_time_timezone_aware_datetime_input(self):
        """Test to_time with timezone-aware datetime input."""
        tz = datetime.timezone.utc
        dt = datetime.datetime(2023, 10, 15, 14, 30, 45, tzinfo=tz)
        result = to_time(dt)
        assert isinstance(result, Time)
        assert result.hour == 14
        assert result.minute == 30
        assert result.second == 45
        assert result.tzinfo is not None


# ----------------
# to_time and Time
# ----------------


class TestTimeUtilityIntegration:
    """Integration tests for the time utility functions."""

    def test_time_roundtrip_serialization(self):
        """Test that Time objects can be serialized and deserialized."""
        original = Time(14, 30, 45, 123456, tzinfo=pendulum.timezone("Europe/Berlin"))

        # Serialize
        serialized = Time._serialize(original)
        assert serialized == "14:30:45.123456 Europe/Berlin"

        # Parse back
        parsed = Time.parse(serialized)

        assert parsed.hour == original.hour
        assert parsed.minute == original.minute
        assert parsed.second == original.second
        assert parsed.microsecond == original.microsecond

    def test_time_pydantic_integration(self):
        """Test Time class integration with Pydantic models."""
        class TestModel(PydanticBaseModel):
            test_time: Time

        # Test with string input
        model = TestModel(test_time="14:30:45")
        assert isinstance(model.test_time, Time)
        assert model.test_time.hour == 14


    def test_time_class_uses_to_time_logic(self):
        """Test that Time class validation uses the same logic as to_time."""
        # Test with various inputs that both should handle identically
        test_cases = [
            "14:30",
            14.5,
            (14, 30),
            datetime.time(14, 30),
            pendulum.time(14, 30)
        ]

        class TestModel(PydanticBaseModel):
            test_time: Time

        for case in test_cases:
            # Both should produce the same result
            direct_result = to_time(case)
            model_result = TestModel(test_time=case).test_time

            assert direct_result.hour == model_result.hour
            assert direct_result.minute == model_result.minute
            assert direct_result.second == model_result.second


# ------------------------------------
# date and time types used in pydantic
# ------------------------------------

class ScheduleModel(PydanticBaseModel):
    start_time: Time
    run_duration: Duration
    scheduled_at: DateTime
    run_on: Date


class TestPendulumTypes:

    def test_valid_schedule_model(self):
        model = ScheduleModel(
            start_time="14:30:00",
            run_duration=to_duration("PT2H"),
            scheduled_at=to_datetime("2025-07-04T09:00:00+02:00"),
            run_on=to_datetime("2025-07-04")
        )

        assert isinstance(model.start_time, pendulum.Time)
        assert isinstance(model.run_duration, pendulum.Duration)
        assert isinstance(model.scheduled_at, pendulum.DateTime)
        assert isinstance(model.run_on, pendulum.Date)

        assert model.start_time.hour == 14
        assert model.run_duration.in_hours() == 2
        assert model.scheduled_at.to_date_string() == "2025-07-04"
        assert model.run_on.to_date_string() == "2025-07-04"

    def test_json_serialization(self):
        model = ScheduleModel(
            start_time=pendulum.time(6, 15),
            run_duration=pendulum.duration(minutes=45),
            scheduled_at=pendulum.datetime(2025, 7, 4, 6, 15, tz="Europe/Berlin"),
            run_on=pendulum.date(2025, 7, 4)
        )

        json_data = model.model_dump(mode="json")
        assert "06:15:00" in json_data["start_time"]
        assert "PT45M" in json_data["run_duration"]
        assert "2025-07-04T06:15:00" in json_data["scheduled_at"]
        assert "2025-07-04" in json_data["run_on"]

        json_str = model.model_dump_json()
        assert '"06:15:00' in json_str
        assert "45 minutes" in json_str
        assert "2025-07-04 06:15:00" in json_str
        assert '"2025-07-04"' in json_str

    def test_invalid_start_time(self):
        with pytest.raises(ValidationError):
            ScheduleModel(
                start_time="invalid",
                run_duration="PT1H",
                scheduled_at="2025-07-04T09:00:00+02:00",
                run_on="2025-07-04"
            )

    def test_invalid_duration(self):
        with pytest.raises(ValidationError):
            ScheduleModel(
                start_time="10:00:00",
                run_duration="2 hours",  # invalid ISO 8601 duration
                scheduled_at="2025-07-04T09:00:00+02:00",
                run_on="2025-07-04"
            )

    def test_type_coercion(self):
        dt = pendulum.datetime(2025, 7, 4, 12, 0)
        model = ScheduleModel(
            start_time=pendulum.time(12, 0),
            run_duration=pendulum.duration(hours=3),
            scheduled_at=dt,
            run_on=dt.date()
        )
        assert model.scheduled_at.hour == 12
        assert model.run_duration.total_minutes() == 180


# -----------------------------
# TimeWindow
# -----------------------------


class TestTimeWindow:
    """Tests for the TimeWindow model."""

    def test_datetime_within_and_outside_window(self):
        """Test datetime containment logic inside and outside the time window."""
        window = TimeWindow(start_time=Time(6, 0), duration=Duration(hours=3))
        assert window.contains(DateTime(2025, 7, 12, 7, 30)) is True  # Inside
        assert window.contains(DateTime(2025, 7, 12, 9, 30)) is False  # Outside

    def test_contains_with_duration(self):
        """Test datetime with duration that does and doesn't fit in the window."""
        window = TimeWindow(start_time=Time(6, 0), duration=Duration(hours=3))
        assert window.contains(DateTime(2025, 7, 12, 6, 30), duration=Duration(minutes=60)) is True
        assert window.contains(DateTime(2025, 7, 12, 6, 30), duration=Duration(hours=3)) is False

    def test_day_of_week_filter(self):
        """Test time window restricted by day of week."""
        window = TimeWindow(start_time=Time(6, 0), duration=Duration(hours=2), day_of_week=5)  # Saturday
        assert window.contains(DateTime(2025, 7, 12, 6, 30)) is True   # Saturday
        assert window.contains(DateTime(2025, 7, 11, 6, 30)) is False  # Friday

    def test_day_of_week_as_english_name(self):
        """Test time window with English weekday name."""
        window = TimeWindow(start_time=Time(6, 0), duration=Duration(hours=2), day_of_week="monday")
        assert window.contains(DateTime(2025, 7, 7, 6, 30)) is True   # Monday
        assert window.contains(DateTime(2025, 7, 5, 6, 30)) is False  # Saturday

    def test_specific_date_filter(self):
        """Test time window restricted by exact date."""
        window = TimeWindow(start_time=Time(6, 0), duration=Duration(hours=2), date=Date(2025, 7, 12))
        assert window.contains(DateTime(2025, 7, 12, 6, 30)) is True
        assert window.contains(DateTime(2025, 7, 13, 6, 30)) is False

    def test_invalid_field_types_raise_validation(self):
        """Test invalid types raise a Pydantic validation error."""
        with pytest.raises(ValidationError):
            TimeWindow(start_time="not_a_time", duration="3h")

    @pytest.mark.parametrize("locale, weekday_name, expected_dow", [
        ("de", "Montag", 0),
        ("de", "Samstag", 5),
        ("es", "lunes", 0),
        ("es", "sábado", 5),
        ("fr", "lundi", 0),
        ("fr", "samedi", 5),
    ])
    def test_localized_day_names(self, locale, weekday_name, expected_dow):
        """Test that localized weekday names are resolved to correct weekday index."""
        window = TimeWindow(start_time=Time(6, 0), duration=Duration(hours=2), day_of_week=weekday_name, locale=locale)
        assert window.day_of_week == expected_dow


# ------------------
# TimeWindowSequence
# ------------------


class TestTimeWindowSequence:
    """Test suite for TimeWindowSequence model."""

    @pytest.fixture
    def sample_time_window_1(self):
        """Morning window: 9:00 AM - 12:00 PM."""
        return TimeWindow(
            start_time=Time(9, 0, 0),
            duration=Duration(hours=3)
        )

    @pytest.fixture
    def sample_time_window_2(self):
        """Afternoon window: 2:00 PM - 5:00 PM."""
        return TimeWindow(
            start_time=Time(14, 0, 0),
            duration=Duration(hours=3)
        )

    @pytest.fixture
    def monday_window(self):
        """Monday only window: 10:00 AM - 11:00 AM."""
        return TimeWindow(
            start_time=Time(10, 0, 0),
            duration=Duration(hours=1),
            day_of_week=0  # Monday
        )

    @pytest.fixture
    def specific_date_window(self):
        """Specific date window: 1:00 PM - 3:00 PM on 2025-01-15."""
        return TimeWindow(
            start_time=Time(13, 0, 0),
            duration=Duration(hours=2),
            date=Date(2025, 1, 15)
        )

    @pytest.fixture
    def sample_sequence(self, sample_time_window_1, sample_time_window_2):
        """Sequence with morning and afternoon windows."""
        return TimeWindowSequence(windows=[sample_time_window_1, sample_time_window_2])

    @pytest.fixture
    def sample_sequence_json(self, sample_time_window_1, sample_time_window_2):
        """Sequence with morning and afternoon windows."""
        seq_json = TimeWindowSequence(windows=[sample_time_window_1, sample_time_window_2]).model_dump()
        return seq_json

    @pytest.fixture
    def sample_sequence_json_str(self, sample_time_window_1, sample_time_window_2):
        """Sequence with morning and afternoon windows."""
        seq_json_str = TimeWindowSequence(windows=[sample_time_window_1, sample_time_window_2]).model_dumps(indent=2)
        return seq_json_str

    @pytest.fixture
    def reference_date(self):
        """Reference date for testing: 2025-01-15 (Wednesday)."""
        return pendulum.parse("2025-01-15T08:00:00")

    def test_init_with_none_windows(self):
        """Test initialization with None windows creates empty list."""
        sequence = TimeWindowSequence()
        assert sequence.windows == []
        assert len(sequence) == 0

    def test_init_with_explicit_none(self):
        """Test initialization with explicit None windows."""
        sequence = TimeWindowSequence(windows=None)
        assert sequence.windows == []
        assert len(sequence) == 0

    def test_init_with_empty_list(self):
        """Test initialization with empty list."""
        sequence = TimeWindowSequence(windows=[])
        assert sequence.windows == []
        assert len(sequence) == 0

    def test_init_with_windows(self, sample_time_window_1, sample_time_window_2):
        """Test initialization with windows."""
        sequence = TimeWindowSequence(windows=[sample_time_window_1, sample_time_window_2])
        assert len(sequence) == 2
        assert sequence.windows is not None # make mypy happy
        assert sequence.windows[0] == sample_time_window_1
        assert sequence.windows[1] == sample_time_window_2

    def test_iterator_protocol(self, sample_sequence):
        """Test that sequence supports iteration."""
        windows = list(sample_sequence)
        assert len(windows) == 2
        assert all(isinstance(window, TimeWindow) for window in windows)

    def test_indexing(self, sample_sequence, sample_time_window_1):
        """Test indexing into sequence."""
        assert sample_sequence[0] == sample_time_window_1

    def test_length(self, sample_sequence):
        """Test len() support."""
        assert len(sample_sequence) == 2

    def test_contains_empty_sequence(self, reference_date):
        """Test contains() with empty sequence returns False."""
        sequence = TimeWindowSequence()
        assert not sequence.contains(reference_date)
        assert not sequence.contains(reference_date, Duration(hours=1))

    def test_contains_datetime_in_window(self, sample_sequence, reference_date):
        """Test contains() finds datetime in one of the windows."""
        # 10:00 AM should be in the morning window (9:00 AM - 12:00 PM)
        test_time = reference_date.replace(hour=10, minute=0)
        assert sample_sequence.contains(test_time)

    def test_contains_datetime_not_in_any_window(self, sample_sequence, reference_date):
        """Test contains() returns False when datetime is not in any window."""
        # 1:00 PM should not be in any window (gap between morning and afternoon)
        test_time = reference_date.replace(hour=13, minute=0)
        assert not sample_sequence.contains(test_time)

    def test_contains_with_duration_fits(self, sample_sequence, reference_date):
        """Test contains() with duration that fits in a window."""
        # 10:00 AM with 1 hour duration should fit in morning window
        test_time = reference_date.replace(hour=10, minute=0)
        assert sample_sequence.contains(test_time, Duration(hours=1))

    def test_contains_with_duration_too_long(self, sample_sequence, reference_date):
        """Test contains() with duration that doesn't fit in any window."""
        # 11:00 AM with 2 hours duration won't fit in remaining morning window time
        test_time = reference_date.replace(hour=11, minute=0)
        assert not sample_sequence.contains(test_time, Duration(hours=2))

    def test_earliest_start_time_empty_sequence(self, reference_date):
        """Test earliest_start_time() with empty sequence returns None."""
        sequence = TimeWindowSequence()
        assert sequence.earliest_start_time(Duration(hours=1), reference_date) is None

    def test_earliest_start_time_finds_earliest(self, sample_sequence, reference_date):
        """Test earliest_start_time() finds the earliest time across all windows."""
        # Should return 9:00 AM (start of morning window)
        earliest = sample_sequence.earliest_start_time(Duration(hours=1), reference_date)
        expected = reference_date.replace(hour=9, minute=0, second=0, microsecond=0)
        assert earliest == expected

    def test_earliest_start_time_duration_too_long(self, sample_sequence, reference_date):
        """Test earliest_start_time() with duration longer than any window."""
        # 4 hours won't fit in any 3-hour window
        assert sample_sequence.earliest_start_time(Duration(hours=4), reference_date) is None

    def test_latest_start_time_empty_sequence(self, reference_date):
        """Test latest_start_time() with empty sequence returns None."""
        sequence = TimeWindowSequence()
        assert sequence.latest_start_time(Duration(hours=1), reference_date) is None

    def test_latest_start_time_finds_latest(self, sample_sequence, reference_date):
        """Test latest_start_time() finds the latest time across all windows."""
        # Should return 4:00 PM (latest start for 1 hour in afternoon window)
        latest = sample_sequence.latest_start_time(Duration(hours=1), reference_date)
        expected = reference_date.replace(hour=16, minute=0, second=0, microsecond=0)
        assert latest == expected

    def test_can_fit_duration_empty_sequence(self, reference_date):
        """Test can_fit_duration() with empty sequence returns False."""
        sequence = TimeWindowSequence()
        assert not sequence.can_fit_duration(Duration(hours=1), reference_date)

    def test_can_fit_duration_fits_in_one_window(self, sample_sequence, reference_date):
        """Test can_fit_duration() returns True when duration fits in one window."""
        assert sample_sequence.can_fit_duration(Duration(hours=2), reference_date)

    def test_can_fit_duration_too_long(self, sample_sequence, reference_date):
        """Test can_fit_duration() returns False when duration is too long."""
        assert not sample_sequence.can_fit_duration(Duration(hours=4), reference_date)

    def test_available_duration_empty_sequence(self, reference_date):
        """Test available_duration() with empty sequence returns None."""
        sequence = TimeWindowSequence()
        assert sequence.available_duration(reference_date) is None

    def test_available_duration_sums_all_windows(self, sample_sequence, reference_date):
        """Test available_duration() sums durations from all applicable windows."""
        # 3 hours + 3 hours = 6 hours total
        total = sample_sequence.available_duration(reference_date)
        assert total == Duration(hours=6)

    def test_available_duration_with_day_restriction(self, monday_window, reference_date):
        """Test available_duration() respects day restrictions."""
        sequence = TimeWindowSequence(windows=[monday_window])

        # Reference date is Wednesday, so Monday window shouldn't apply
        assert sequence.available_duration(reference_date) is None

        # Monday date should apply
        monday_date = pendulum.parse("2025-01-13T08:00:00")  # Monday
        assert sequence.available_duration(monday_date) == Duration(hours=1)

    def test_get_applicable_windows_empty_sequence(self, reference_date):
        """Test get_applicable_windows() with empty sequence."""
        sequence = TimeWindowSequence()
        assert sequence.get_applicable_windows(reference_date) == []

    def test_get_applicable_windows_all_apply(self, sample_sequence, reference_date):
        """Test get_applicable_windows() returns all windows when they all apply."""
        applicable = sample_sequence.get_applicable_windows(reference_date)
        assert len(applicable) == 2

    def test_get_applicable_windows_with_restrictions(self, monday_window, reference_date):
        """Test get_applicable_windows() respects day restrictions."""
        sequence = TimeWindowSequence(windows=[monday_window])

        # Wednesday - no applicable windows
        assert sequence.get_applicable_windows(reference_date) == []

        # Monday - one applicable window
        monday_date = pendulum.parse("2025-01-13T08:00:00")
        applicable = sequence.get_applicable_windows(monday_date)
        assert len(applicable) == 1
        assert applicable[0] == monday_window

    def test_find_windows_for_duration_empty_sequence(self, reference_date):
        """Test find_windows_for_duration() with empty sequence."""
        sequence = TimeWindowSequence()
        assert sequence.find_windows_for_duration(Duration(hours=1), reference_date) == []

    def test_find_windows_for_duration_all_fit(self, sample_sequence, reference_date):
        """Test find_windows_for_duration() when duration fits in all windows."""
        fitting = sample_sequence.find_windows_for_duration(Duration(hours=2), reference_date)
        assert len(fitting) == 2

    def test_find_windows_for_duration_some_fit(self, sample_sequence, reference_date):
        """Test find_windows_for_duration() when duration fits in some windows."""
        # Add a short window that can't fit 2.5 hours
        short_window = TimeWindow(start_time=Time(18, 0, 0), duration=Duration(hours=1))
        sequence = TimeWindowSequence(windows=sample_sequence.windows + [short_window])

        fitting = sequence.find_windows_for_duration(Duration(hours=2, minutes=30), reference_date)
        assert len(fitting) == 2  # Only the first two windows can fit 2.5 hours

    def test_get_all_possible_start_times_empty_sequence(self, reference_date):
        """Test get_all_possible_start_times() with empty sequence."""
        sequence = TimeWindowSequence()
        assert sequence.get_all_possible_start_times(Duration(hours=1), reference_date) == []

    def test_get_all_possible_start_times_multiple_windows(self, sample_sequence, reference_date):
        """Test get_all_possible_start_times() returns ranges for all fitting windows."""
        ranges = sample_sequence.get_all_possible_start_times(Duration(hours=1), reference_date)
        assert len(ranges) == 2

        # Check morning window range
        earliest_morning, latest_morning, morning_window = ranges[0]
        assert earliest_morning == reference_date.replace(hour=9, minute=0, second=0, microsecond=0)
        assert latest_morning == reference_date.replace(hour=11, minute=0, second=0, microsecond=0)

        # Check afternoon window range
        earliest_afternoon, latest_afternoon, afternoon_window = ranges[1]
        assert earliest_afternoon == reference_date.replace(hour=14, minute=0, second=0, microsecond=0)
        assert latest_afternoon == reference_date.replace(hour=16, minute=0, second=0, microsecond=0)

    def test_add_window(self, sample_time_window_1):
        """Test adding a window to the sequence."""
        sequence = TimeWindowSequence()
        assert len(sequence) == 0

        sequence.add_window(sample_time_window_1)
        assert len(sequence) == 1
        assert sequence[0] == sample_time_window_1

    def test_remove_window(self, sample_sequence, sample_time_window_1):
        """Test removing a window from the sequence."""
        assert len(sample_sequence) == 2

        removed = sample_sequence.remove_window(0)
        assert removed == sample_time_window_1
        assert len(sample_sequence) == 1

    def test_remove_window_invalid_index(self, sample_sequence):
        """Test removing a window with invalid index raises IndexError."""
        with pytest.raises(IndexError):
            sample_sequence.remove_window(10)

    def test_remove_window_from_empty_sequence(self):
        """Test removing a window from empty sequence raises IndexError."""
        sequence = TimeWindowSequence()
        with pytest.raises(IndexError):
            sequence.remove_window(0)

    def test_clear_windows(self, sample_sequence):
        """Test clearing all windows from the sequence."""
        assert len(sample_sequence) == 2

        sample_sequence.clear_windows()
        assert len(sample_sequence) == 0
        assert sample_sequence.windows == []

    def test_sort_windows_by_start_time(self, reference_date):
        """Test sorting windows by start time."""
        # Create windows in reverse chronological order
        afternoon_window = TimeWindow(start_time=Time(14, 0, 0), duration=Duration(hours=2))
        morning_window = TimeWindow(start_time=Time(9, 0, 0), duration=Duration(hours=2))
        evening_window = TimeWindow(start_time=Time(18, 0, 0), duration=Duration(hours=2))

        sequence = TimeWindowSequence(windows=[afternoon_window, morning_window, evening_window])
        sequence.sort_windows_by_start_time(reference_date)

        # Should now be sorted: morning, afternoon, evening
        assert sequence[0] == morning_window
        assert sequence[1] == afternoon_window
        assert sequence[2] == evening_window

    def test_sort_windows_with_non_applicable_windows(self, monday_window, reference_date):
        """Test sorting windows with some non-applicable windows."""
        daily_window = TimeWindow(start_time=Time(10, 0, 0), duration=Duration(hours=1))

        sequence = TimeWindowSequence(windows=[monday_window, daily_window])
        sequence.sort_windows_by_start_time(reference_date)  # Wednesday

        # Daily window should come first (applicable), Monday window last (not applicable)
        assert sequence[0] == daily_window
        assert sequence[1] == monday_window

    def test_sort_windows_empty_sequence(self, reference_date):
        """Test sorting an empty sequence doesn't raise errors."""
        sequence = TimeWindowSequence()
        sequence.sort_windows_by_start_time(reference_date)
        assert len(sequence) == 0

    def test_default_reference_date_handling(self, sample_sequence):
        """Test that methods handle default reference date (today) correctly."""
        # These should not raise errors and should return reasonable values
        assert isinstance(sample_sequence.can_fit_duration(Duration(hours=1)), bool)
        assert sample_sequence.available_duration() is not None
        assert isinstance(sample_sequence.get_applicable_windows(), list)

    def test_specific_date_window_functionality(self, specific_date_window):
        """Test functionality with specific date restrictions."""
        sequence = TimeWindowSequence(windows=[specific_date_window])

        # Should work on the specific date
        specific_date = pendulum.parse("2025-01-15T12:00:00")
        assert sequence.can_fit_duration(Duration(hours=1), specific_date)

        # Should not work on other dates
        other_date = pendulum.parse("2025-01-16T12:00:00")
        assert not sequence.can_fit_duration(Duration(hours=1), other_date)

    def test_edge_cases_with_zero_duration(self, sample_sequence, reference_date):
        """Test edge cases with zero duration."""
        zero_duration = Duration()

        # Should be able to fit zero duration
        assert sample_sequence.can_fit_duration(zero_duration, reference_date)

        # Should find start times for zero duration
        earliest = sample_sequence.earliest_start_time(zero_duration, reference_date)
        assert earliest is not None

    def test_overlapping_windows(self, reference_date):
        """Test behavior with overlapping windows."""
        window1 = TimeWindow(start_time=Time(10, 0, 0), duration=Duration(hours=3))
        window2 = TimeWindow(start_time=Time(11, 0, 0), duration=Duration(hours=3))

        sequence = TimeWindowSequence(windows=[window1, window2])

        # Should handle overlapping windows correctly
        test_time = reference_date.replace(hour=11, minute=30)
        assert sequence.contains(test_time)

        # Total duration should be sum of both windows (even though they overlap)
        total = sequence.available_duration(reference_date)
        assert total == Duration(hours=6)

    def test_sequence_model_dump(self, sample_sequence_json):
        """Test that model dump creates the correct json."""
        assert sample_sequence_json == json.loads("""
{
    "windows": [
        {
            "start_time": "09:00:00.000000",
            "duration": "3 hours",
            "day_of_week": null,
            "date": null,
            "locale": null
        },
        {
            "start_time": "14:00:00.000000",
            "duration": "3 hours",
            "day_of_week": null,
            "date": null,
            "locale": null
        }
    ]
}""")


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
            "UTC",
            "2024-01-03",
            None,
            "UTC",
            None,
            False,
            pendulum.datetime(2024, 1, 3, 0, 0, 0, tz="UTC"),
            False,
        ),
        (
            "TC015",
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
            "TC016",
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
            "TC017",
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
            "TC018",
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
            "TC019",
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
            "TC020",
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
            "TC021",
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
            "TC022",
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
            "TC023",
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
            "TC024",
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
            "TC025",
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
            "TC026",
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

class TestToDuration:
    # ------------------------------------------------------------------
    # Valid input conversions (no formatting)
    # ------------------------------------------------------------------
    @pytest.mark.parametrize(
        "input_value, expected_output",
        [
            # duration input
            (pendulum.duration(days=1), pendulum.duration(days=1)),

            # String input
            ("1 hour", pendulum.duration(hours=1)),
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

            # Integer / Float
            (3600, pendulum.duration(seconds=3600)),
            (86400, pendulum.duration(days=1)),
            (1800.5, pendulum.duration(seconds=1800.5)),

            # Tuple / List
            ((1, 2, 30, 15), pendulum.duration(days=1, hours=2, minutes=30, seconds=15)),
            ([0, 10, 0, 0], pendulum.duration(hours=10)),
        ],
    )
    def test_to_duration_valid(self, input_value, expected_output):
        """Test that valid inputs convert to correct Duration objects."""
        assert to_duration(input_value) == expected_output

    # ------------------------------------------------------------------
    # ISO-8601 output (`as_string=True`)
    # ------------------------------------------------------------------
    @pytest.mark.parametrize(
        "input_value, expected",
        [
            ("15 minutes", "PT15M"),
            ("1 hour 30 minutes", "PT1H30M"),
            ("45 seconds", "PT45S"),
            ("1 hour 5 seconds", "PT1H5S"),
            ("2 days", "P2D"),
            ("2 days 3 hours 4 minutes 5 seconds", "P2DT3H4M5S"),
            ("0 seconds", "PT0S"),
        ]
    )
    def test_as_string_true_iso8601(self, input_value, expected):
        """Test ISO-8601 duration strings for various inputs."""
        assert to_duration(input_value, as_string=True) == expected

    # ------------------------------------------------------------------
    # Human readable (`as_string="human"`)
    # ------------------------------------------------------------------
    def test_as_string_human(self):
        assert to_duration("90 seconds", as_string="human") == "1 minute 30 seconds"

    # ------------------------------------------------------------------
    # Pandas frequency (`as_string="pandas"`)
    # ------------------------------------------------------------------
    @pytest.mark.parametrize(
        "input_value, expected",
        [
            ("1 hour", "1h"),
            ("2 hours", "2h"),
            ("15 minutes", "15min"),
            ("90 minutes", "90min"),
            ("30 seconds", "30s"),
            ("900 seconds", "15min"),
        ],
    )
    def test_as_string_pandas(self, input_value, expected):
        assert to_duration(input_value, as_string="pandas") == expected

    # ------------------------------------------------------------------
    # Custom format strings
    # ------------------------------------------------------------------
    def test_as_string_custom_seconds(self):
        assert to_duration("75 seconds", as_string="Total: {S}s") == "Total: 75s"

    def test_as_string_custom_minutes(self):
        assert to_duration("15 minutes", as_string="{M}m total") == "15m total"

    def test_as_string_custom_hours(self):
        assert to_duration("7200 seconds", as_string="{H} hours") == "2 hours"

    def test_as_string_custom_human_alias(self):
        assert to_duration("30 minutes", as_string="{f}") == "30 minutes"

    # ------------------------------------------------------------------
    # Invalid input handling
    # ------------------------------------------------------------------
    @pytest.mark.parametrize(
        "input_value",
        [
            "not a duration",
            "5 lightyears",
            (1, 2, 3),             # wrong tuple size
            {"a": 1},              # unsupported type
            None,
        ],
    )
    def test_invalid_inputs_raise(self, input_value):
        with pytest.raises(ValueError):
            to_duration(input_value)

    # ------------------------------------------------------------------
    # Invalid as_string values
    # ------------------------------------------------------------------
    def test_invalid_as_string_raises(self):
        with pytest.raises(ValueError):
            to_duration("5 minutes", as_string=123) # type: ignore

    def test_summation(self):
        start_datetime = to_datetime("2028-01-11 00:00:00")
        index_datetime = start_datetime
        for i in range(48):
            expected_datetime = start_datetime + to_duration(f"{i} hours")
            assert index_datetime == expected_datetime
            index_datetime += to_duration("1 hour")
        assert index_datetime == to_datetime("2028-01-13 00:00:00")

    def test_excessive_length_raises_valueerror(self):
        """Test that to_duration raises ValueError for strings exceeding max length.

        This test covers the fix for the ReDoS vulnerability.
        Related to: #494
        """
        # String exceeds limits
        long_string = "a" * (MAX_DURATION_STRING_LENGTH + 50)

        # Expected Errormessage – ESCAPED für Regex
        expected_error_message = re.escape(
            f"Input string exceeds maximum allowed length ({MAX_DURATION_STRING_LENGTH})."
        )

        # Check if error was raised
        with pytest.raises(ValueError, match=expected_error_message):
            to_duration(long_string)

        # Optional: String exactly at the limit should NOT trigger the length check.
        at_limit_string = "b" * MAX_DURATION_STRING_LENGTH
        try:
            to_duration(at_limit_string)
        except ValueError as e:
            if str(e) == f"Input string exceeds maximum allowed length ({MAX_DURATION_STRING_LENGTH}).":
                pytest.fail(
                    f"to_duration raised length ValueError unexpectedly for string at limit: {at_limit_string}"
                )
            pass


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
