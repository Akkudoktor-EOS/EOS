"""Tests for the stringutil module."""

import pytest

from akkudoktoreos.utils.stringutil import str2bool


class TestStr2Bool:
    """Unit tests for the str2bool function."""

    @pytest.mark.parametrize(
        "input_value",
        ["yes", "YES", "y", "Y", "true", "TRUE", "t", "T", "1", "on", "ON"],
    )
    def test_truthy_values(self, input_value):
        """Test that all accepted truthy string values return True."""
        assert str2bool(input_value) is True

    @pytest.mark.parametrize(
        "input_value",
        ["no", "NO", "n", "N", "false", "FALSE", "f", "F", "0", "off", "OFF"],
    )
    def test_falsy_values(self, input_value):
        """Test that all accepted falsy string values return False."""
        assert str2bool(input_value) is False

    def test_bool_input_returns_itself(self):
        """Test that passing a boolean returns the same value."""
        assert str2bool(True) is True
        assert str2bool(False) is False

    def test_whitespace_is_ignored(self):
        """Test that surrounding whitespace does not affect the result."""
        assert str2bool("  yes ") is True
        assert str2bool("\tno\n") is False

    def test_invalid_string_raises_value_error(self):
        """Test that invalid strings raise a ValueError."""
        with pytest.raises(ValueError, match="Invalid boolean value"):
            str2bool("maybe")
        with pytest.raises(ValueError):
            str2bool("truthish")

    def test_type_error_on_non_string_non_bool(self):
        """Test that non-string, non-boolean inputs raise ValueError."""
        with pytest.raises(ValueError, match="Invalid boolean value"):
            str2bool(None)
        with pytest.raises(ValueError, match="Invalid boolean value"):
            str2bool(1.23)
        with pytest.raises(ValueError, match="Invalid boolean value"):
            str2bool([])
