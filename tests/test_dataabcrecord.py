"""Pytest test for data records fro dataabc module."""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, ClassVar, List, Optional, Union

import numpy as np
import pandas as pd
import pendulum
import pytest
from pydantic import Field, ValidationError

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.dataabc import (
    DataABC,
    DataRecord,
)
from akkudoktoreos.core.databaseabc import DatabaseTimestamp
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime, to_duration

# Derived classes for testing
# ---------------------------

class DerivedConfig(SettingsBaseModel):
    env_var: Optional[int] = Field(default=None, description="Test config by environment var")
    instance_field: Optional[str] = Field(default=None, description="Test config by instance field")
    class_constant: Optional[int] = Field(default=None, description="Test config by class constant")


class DerivedBase(DataABC):
    instance_field: Optional[str] = Field(default=None, description="Field Value")
    class_constant: ClassVar[int] = 30


class DerivedRecord(DataRecord):
    """Date Record derived from base class DataRecord.

    The derived data record got the
    - `data_value` field and the
    - `dish_washer_emr`, `solar_power`, `temp` configurable field like data.
    """

    data_value: Optional[float] = Field(default=None, description="Data Value")

    @classmethod
    def configured_data_keys(cls) -> Optional[list[str]]:
        return ["dish_washer_emr", "solar_power", "temp"]


# Tests
# ----------


class TestDataABC:
    @pytest.fixture
    def base(self):
        # Provide default values for configuration
        derived = DerivedBase()
        return derived

    def test_get_config_value_key_error(self, base):
        with pytest.raises(AttributeError):
            base.config.non_existent_key


class TestDataRecord:
    def create_test_record(self, date, value):
        """Helper function to create a test DataRecord."""
        return DerivedRecord(date_time=date, data_value=value)

    @pytest.fixture
    def record(self):
        """Fixture to create a sample DerivedDataRecord with some data set."""
        rec = DerivedRecord(date_time=to_datetime("1967-01-11"), data_value=10.0)
        rec.configured_data = {"dish_washer_emr": 123.0, "solar_power": 456.0}
        return rec

    def test_getitem(self):
        record = self.create_test_record(datetime(2024, 1, 3, tzinfo=timezone.utc), 10.0)
        assert record["data_value"] == 10.0

    def test_setitem(self):
        record = self.create_test_record(datetime(2024, 1, 3, tzinfo=timezone.utc), 10.0)
        record["data_value"] = 20.0
        assert record.data_value == 20.0

    def test_delitem(self):
        record = self.create_test_record(datetime(2024, 1, 3, tzinfo=timezone.utc), 10.0)
        record.data_value = 20.0
        del record["data_value"]
        assert record.data_value is None

    def test_len(self):
        record = self.create_test_record(datetime(2024, 1, 3, tzinfo=timezone.utc), 10.0)
        record.date_time = None
        record.data_value = 20.0
        assert len(record) == 5 # 2 regular fields + 3 configured data "fields"

    def test_to_dict(self):
        record = self.create_test_record(datetime(2024, 1, 3, tzinfo=timezone.utc), 10.0)
        record.data_value = 20.0
        record_dict = record.to_dict()
        assert "data_value" in record_dict
        assert record_dict["data_value"] == 20.0
        record2 = DerivedRecord.from_dict(record_dict)
        assert record2.model_dump() == record.model_dump()

    def test_to_json(self):
        record = self.create_test_record(datetime(2024, 1, 3, tzinfo=timezone.utc), 10.0)
        record.data_value = 20.0
        json_str = record.to_json()
        assert "data_value" in json_str
        assert "20.0" in json_str
        record2 = DerivedRecord.from_json(json_str)
        assert record2.model_dump() == record.model_dump()

    def test_record_keys_includes_configured_data_keys(self, record):
        """Ensure record_keys includes all configured configured data keys."""
        assert set(record.record_keys()) >= set(record.configured_data_keys())

    def test_record_keys_writable_includes_configured_data_keys(self, record):
        """Ensure record_keys_writable includes all configured configured data keys."""
        assert set(record.record_keys_writable()) >= set(record.configured_data_keys())

    def test_getitem_existing_field(self, record):
        """Test that __getitem__ returns correct value for existing native field."""
        record.date_time = "2024-01-01T00:00:00+00:00"
        assert record["date_time"] is not None

    def test_getitem_existing_configured_data(self, record):
        """Test that __getitem__ retrieves existing configured data values."""
        assert record["dish_washer_emr"] == 123.0
        assert record["solar_power"] == 456.0

    def test_getitem_missing_configured_data_returns_none(self, record):
        """Test that __getitem__ returns None for missing but known configured data keys."""
        assert record["temp"] is None

    def test_getitem_raises_keyerror(self, record):
        """Test that __getitem__ raises KeyError for completely unknown keys."""
        with pytest.raises(KeyError):
            _ = record["nonexistent"]

    def test_setitem_field(self, record):
        """Test setting a native field using __setitem__."""
        record["date_time"] = "2025-01-01T12:00:00+00:00"
        assert str(record.date_time).startswith("2025-01-01")

    def test_setitem_configured_data(self, record):
        """Test setting a known configured data key using __setitem__."""
        record["temp"] = 25.5
        assert record.configured_data["temp"] == 25.5

    def test_setitem_invalid_key_raises(self, record):
        """Test that __setitem__ raises KeyError for unknown keys."""
        with pytest.raises(KeyError):
            record["unknown_key"] = 123

    def test_delitem_field(self, record):
        """Test deleting a native field using __delitem__."""
        record["date_time"] = "2025-01-01T12:00:00+00:00"
        del record["date_time"]
        assert record.date_time is None

    def test_delitem_configured_data(self, record):
        """Test deleting a known configured data key using __delitem__."""
        del record["solar_power"]
        assert "solar_power" not in record.configured_data

    def test_delitem_unknown_raises(self, record):
        """Test that __delitem__ raises KeyError for unknown keys."""
        with pytest.raises(KeyError):
            del record["nonexistent"]

    def test_attribute_get_existing_field(self, record):
        """Test accessing a native field via attribute."""
        record.date_time = "2025-01-01T12:00:00+00:00"
        assert record.date_time is not None

    def test_attribute_get_existing_configured_data(self, record):
        """Test accessing an existing configured data via attribute."""
        assert record.dish_washer_emr == 123.0

    def test_attribute_get_missing_configured_data(self, record):
        """Test accessing a missing but known configured data returns None."""
        assert record.temp is None

    def test_attribute_get_invalid_raises(self, record):
        """Test accessing an unknown attribute raises AttributeError."""
        with pytest.raises(AttributeError):
            _ = record.nonexistent

    def test_attribute_set_existing_field(self, record):
        """Test setting a native field via attribute."""
        record.date_time = "2025-06-25T12:00:00+00:00"
        assert record.date_time is not None

    def test_attribute_set_existing_configured_data(self, record):
        """Test setting a known configured data key via attribute."""
        record.temp = 99.9
        assert record.configured_data["temp"] == 99.9

    def test_attribute_set_invalid_raises(self, record):
        """Test setting an unknown attribute raises AttributeError."""
        with pytest.raises(AttributeError):
            record.invalid = 123

    def test_delattr_field(self, record):
        """Test deleting a native field via attribute."""
        record.date_time = "2025-06-25T12:00:00+00:00"
        del record.date_time
        assert record.date_time is None

    def test_delattr_configured_data(self, record):
        """Test deleting a known configured data key via attribute."""
        record.temp = 88.0
        del record.temp
        assert "temp" not in record.configured_data

    def test_delattr_ignored_missing_configured_data_key(self, record):
        """Test deleting a known configured data key that was never set is a no-op."""
        del record.temp
        assert "temp" not in record.configured_data

    def test_len_and_iter(self, record):
        """Test that __len__ and __iter__ behave as expected."""
        keys = list(iter(record))
        assert set(record.record_keys_writable()) == set(keys)
        assert len(record) == len(keys)

    def test_in_operator_includes_configured_data(self, record):
        """Test that 'in' operator includes configured data keys."""
        assert "dish_washer_emr" in record
        assert "temp" in record  # known key, even if not yet set
        assert "nonexistent" not in record

    def test_hasattr_behavior(self, record):
        """Test that hasattr returns True for fields and known configured dataWs."""
        assert hasattr(record, "date_time")
        assert hasattr(record, "dish_washer_emr")
        assert hasattr(record, "temp")  # allowed, even if not yet set
        assert not hasattr(record, "nonexistent")

    def test_model_validate_roundtrip(self, record):
        """Test that MeasurementDataRecord can be serialized and revalidated."""
        dumped = record.model_dump()
        restored = DerivedRecord.model_validate(dumped)
        assert restored.dish_washer_emr == 123.0
        assert restored.solar_power == 456.0
        assert restored.temp is None  # not set

    def test_copy_preserves_configured_data(self, record):
        """Test that copying preserves configured data values."""
        record.temp = 22.2
        copied = record.model_copy()
        assert copied.dish_washer_emr == 123.0
        assert copied.temp == 22.2
        assert copied is not record

    def test_equality_includes_configured_data(self, record):
        """Test that equality includes the `configured data` content."""
        other = record.model_copy()
        assert record == other

    def test_inequality_differs_with_configured_data(self, record):
        """Test that records with different configured datas are not equal."""
        other = record.model_copy(deep=True)
        # Modify one configured data value in the copy
        other.configured_data["dish_washer_emr"] = 999.9
        assert record != other

    def test_in_operator_for_configured_data_and_fields(self, record):
        """Ensure 'in' works for both fields and configured configured data keys."""
        assert "dish_washer_emr" in record
        assert "solar_power" in record
        assert "date_time" in record  # standard field
        assert "temp" in record       # allowed but not yet set
        assert "unknown" not in record

    def test_hasattr_equivalence_to_getattr(self, record):
        """hasattr should return True for all valid keys/configured datas."""
        assert hasattr(record, "dish_washer_emr")
        assert hasattr(record, "temp")
        assert hasattr(record, "date_time")
        assert not hasattr(record, "nonexistent")

    def test_dir_includes_configured_data_keys(self, record):
        """`dir(record)` should include configured data keys for introspection.
         It shall not include the internal 'configured datas' attribute.
        """
        keys = dir(record)
        assert "configured datas" not in keys
        for key in record.configured_data_keys():
            assert key in keys

    def test_init_configured_field_like_data_applies_before_model_init(self):
        """Test that keys listed in `_configured_data_keys` are moved to `configured_data` at init time."""
        record = DerivedRecord(
            date_time="2024-01-03T00:00:00+00:00",
            data_value=42.0,
            dish_washer_emr=111.1,
            solar_power=222.2,
            temp=333.3  # assume `temp` is also a valid configured key
        )

        assert record.data_value == 42.0
        assert record.configured_data == {
            "dish_washer_emr": 111.1,
            "solar_power": 222.2,
            "temp": 333.3,
        }
