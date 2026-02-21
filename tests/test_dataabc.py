import json
from datetime import datetime, timezone
from typing import Any, ClassVar, List, Optional, Union

import numpy as np
import pandas as pd
import pendulum
import pytest
from pydantic import Field, ValidationError

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.core.dataabc import (
    DataABC,
    DataContainer,
    DataImportProvider,
    DataProvider,
    DataRecord,
    DataSequence,
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


class DerivedSequence(DataSequence):
    # overload
    records: List[DerivedRecord] = Field(
        default_factory=list, description="List of DerivedRecord records"
    )

    @classmethod
    def record_class(cls) -> Any:
        return DerivedRecord

class DerivedSequence2(DataSequence):
    # overload
    records: List[DerivedRecord] = Field(
        default_factory=list, description="List of DerivedRecord records"
    )

    @classmethod
    def record_class(cls) -> Any:
        return DerivedRecord

class DerivedDataProvider(DataProvider):
    """A concrete subclass of DataProvider for testing purposes."""

    # overload
    records: List[DerivedRecord] = Field(
        default_factory=list, description="List of DerivedRecord records"
    )
    provider_enabled: ClassVar[bool] = False
    provider_updated: ClassVar[bool] = False

    @classmethod
    def record_class(cls) -> Any:
        return DerivedRecord

    # Implement abstract methods for test purposes
    def provider_id(self) -> str:
        return "DerivedDataProvider"

    def enabled(self) -> bool:
        return self.provider_enabled

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        # Simulate update logic
        DerivedDataProvider.provider_updated = True


class DerivedDataImportProvider(DataImportProvider):
    """A concrete subclass of DataImportProvider for testing purposes."""

    # overload
    records: List[DerivedRecord] = Field(
        default_factory=list, description="List of DerivedRecord records"
    )
    provider_enabled: ClassVar[bool] = False
    provider_updated: ClassVar[bool] = False

    @classmethod
    def record_class(cls) -> Any:
        return DerivedRecord

    # Implement abstract methods for test purposes
    def provider_id(self) -> str:
        return "DerivedDataImportProvider"

    def enabled(self) -> bool:
        return self.provider_enabled

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        # Simulate update logic
        DerivedDataImportProvider.provider_updated = True


class DerivedDataContainer(DataContainer):
    providers: List[Union[DerivedDataProvider, DataProvider]] = Field(
        default_factory=list, description="List of data providers"
    )


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


class TestDataSequence:
    @pytest.fixture
    def sequence(self):
        sequence0 = DerivedSequence()
        assert len(sequence0) == 0
        return sequence0

    @pytest.fixture
    def sequence2(self):
        sequence = DerivedSequence()
        record1 = self.create_test_record(datetime(1970, 1, 1), 1970)
        record2 = self.create_test_record(datetime(1971, 1, 1), 1971)
        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)
        assert len(sequence) == 2
        return sequence

    def create_test_record(self, date, value):
        """Helper function to create a test DataRecord."""
        return DerivedRecord(date_time=date, data_value=value)

    # Test cases
    @pytest.mark.parametrize("tz_name", ["UTC", "Europe/Berlin", "Atlantic/Canary"])
    def test_min_max_datetime_timezone_and_order(self, sequence, tz_name, monkeypatch, config_eos):
        # Monkeypatch the read-only timezone property
        monkeypatch.setattr(config_eos.general.__class__, "timezone", property(lambda self: tz_name))

        # Create timezone-aware datetimes using the patched config
        dt_early = to_datetime("2024-01-01T00:00:00", in_timezone=config_eos.general.timezone)
        dt_late = to_datetime("2024-01-02T00:00:00", in_timezone=config_eos.general.timezone)

        # Insert in reverse order to verify sorting
        record1 = self.create_test_record(dt_late, 1)
        record2 = self.create_test_record(dt_early, 2)

        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)

        min_dt = sequence.min_datetime
        max_dt = sequence.max_datetime

        # --- Basic correctness ---
        assert min_dt == dt_early
        assert max_dt == dt_late

        # --- Must be timezone aware ---
        assert min_dt.tzinfo is not None
        assert max_dt.tzinfo is not None

        # --- Must preserve timezone ---
        assert min_dt.tzinfo.name == tz_name
        assert max_dt.tzinfo.name == tz_name


    def test_getitem(self, sequence):
        assert len(sequence) == 0
        dt = to_datetime("2024-01-01 00:00:00")
        record = self.create_test_record(dt, 0)
        sequence.insert_by_datetime(record)
        assert isinstance(sequence.get_by_datetime(dt), DerivedRecord)

    def test_setitem(self, sequence2):
        dt = to_datetime("2024-01-03", in_timezone="UTC")
        record = self.create_test_record(dt, 1)
        sequence2.insert_by_datetime(record)
        assert sequence2.records[2].date_time == dt

    def test_insert_reversed_date_record(self, sequence2):
        dt1 = to_datetime("2023-11-05", in_timezone="UTC")
        dt2 = to_datetime("2024-01-03", in_timezone="UTC")
        record1 = self.create_test_record(dt2, 0.8)
        record2 = self.create_test_record(dt1, 0.9) # reversed date
        sequence2.insert_by_datetime(record1)
        assert sequence2.records[2].date_time == dt2
        sequence2.insert_by_datetime(record2)
        assert len(sequence2) == 4
        assert sequence2.records[2] == record2

    def test_insert_duplicate_date_record(self, sequence):
        dt1 = to_datetime("2023-11-05")
        record1 = self.create_test_record(dt1, 0.8)
        record2 = self.create_test_record(dt1, 0.9)  # Duplicate date
        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)
        assert len(sequence) == 1
        assert sequence.get_by_datetime(dt1).data_value == 0.9  # Record should have merged with new value

    def test_key_to_series(self, sequence):
        dt = to_datetime(datetime(2023, 11, 6))
        record = self.create_test_record(dt, 0.8)
        sequence.insert_by_datetime(record)
        series = sequence.key_to_series("data_value")
        assert isinstance(series, pd.Series)

        retrieved_record = sequence.get_by_datetime(dt)
        assert retrieved_record is not None
        assert retrieved_record.data_value == 0.8

    def test_key_from_series(self, sequence):
        dt1 = to_datetime(datetime(2023, 11, 5))
        dt2 = to_datetime(datetime(2023, 11, 6))

        series = pd.Series(
            data=[0.8, 0.9], index=pd.to_datetime([dt1, dt2])
        )
        sequence.key_from_series("data_value", series)
        assert len(sequence) == 2

        record1 = sequence.get_by_datetime(dt1)
        assert record1 is not None
        assert record1.data_value == 0.8

        record2 = sequence.get_by_datetime(dt2)
        assert record2 is not None
        assert record2.data_value == 0.9

    def test_key_to_array(self, sequence):
        interval = to_duration("1 day")
        start_datetime = to_datetime("2023-11-6")
        last_datetime = to_datetime("2023-11-8")
        end_datetime = to_datetime("2023-11-9")

        record1 = self.create_test_record(start_datetime, float(start_datetime.day))
        sequence.insert_by_datetime(record1)
        record2 = self.create_test_record(last_datetime, float(last_datetime.day))
        sequence.insert_by_datetime(record2)

        retrieved_record1 = sequence.get_by_datetime(start_datetime)
        assert retrieved_record1 is not None
        assert retrieved_record1.data_value == 6.0

        retrieved_record2 = sequence.get_by_datetime(last_datetime)
        assert retrieved_record2 is not None
        assert retrieved_record2.data_value == 8.0

        series = sequence.key_to_series(
            key="data_value", start_datetime=start_datetime, end_datetime=end_datetime
        )
        assert len(series) == 2
        assert series[to_datetime("2023-11-6")] == 6
        assert series[to_datetime("2023-11-8")] == 8

        array = sequence.key_to_array(
            key="data_value",
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            interval=interval,
        )
        assert isinstance(array, np.ndarray)
        np.testing.assert_equal(array, [6.0, 7.0, 8.0])

    def test_key_to_array_linear_interpolation(self, sequence):
        """Test key_to_array with linear interpolation for numeric data."""
        interval = to_duration("1 hour")
        record1 = self.create_test_record(pendulum.datetime(2023, 11, 6, 0), 0.8)
        record2 = self.create_test_record(pendulum.datetime(2023, 11, 6, 2), 1.0)  # Gap of 2 hours
        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)

        array = sequence.key_to_array(
            key="data_value",
            start_datetime=pendulum.datetime(2023, 11, 6),
            end_datetime=pendulum.datetime(2023, 11, 6, 3),
            interval=interval,
            fill_method="linear",
        )
        assert len(array) == 3
        assert array[0] == 0.8
        assert array[1] == 0.9  # Interpolated value
        assert array[2] == 1.0


    def test_key_to_array_linear_interpolation_out_of_grid(self, sequence):
        """Test key_to_array with linear interpolation out of grid."""
        interval = to_duration("1 hour")
        start_datetime= to_datetime("2023-11-06T00:30:00") # out of grid
        end_datetime=to_datetime("2023-11-06T01:30:00") # out of grid

        record1_datetime = to_datetime("2023-11-06T00:00:00")
        record1 = self.create_test_record(record1_datetime, 1.0)

        record2_datetime = to_datetime("2023-11-06T02:00:00")
        record2 = self.create_test_record(record2_datetime, 2.0)  # Gap of 2 hours

        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)

        # Check test setup
        record1_timestamp = DatabaseTimestamp.from_datetime(record1_datetime)
        record2_timestamp = DatabaseTimestamp.from_datetime(record2_datetime)
        start_timestamp = DatabaseTimestamp.from_datetime(start_datetime)
        end_timestamp = DatabaseTimestamp.from_datetime(end_datetime)

        start_previous_timestamp = sequence.db_previous_timestamp(start_timestamp)
        assert start_previous_timestamp == record1_timestamp
        end_next_timestamp = sequence.db_next_timestamp(end_timestamp)
        assert end_next_timestamp == record2_timestamp

        # Test
        array = sequence.key_to_array(
            key="data_value",
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            interval=interval,
            fill_method="linear",
            boundary="context",
        )
        np.testing.assert_equal(array, [1.5])

    def test_key_to_array_ffill(self, sequence):
        """Test key_to_array with forward filling for missing values."""
        interval = to_duration("1 hour")
        record1 = self.create_test_record(pendulum.datetime(2023, 11, 6, 0), 0.8)
        record2 = self.create_test_record(pendulum.datetime(2023, 11, 6, 2), 1.0)
        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)

        array = sequence.key_to_array(
            key="data_value",
            start_datetime=pendulum.datetime(2023, 11, 6),
            end_datetime=pendulum.datetime(2023, 11, 6, 3),
            interval=interval,
            fill_method="ffill",
        )
        assert len(array) == 3
        assert array[0] == 0.8
        assert array[1] == 0.8  # Forward-filled value
        assert array[2] == 1.0

    def test_key_to_array_ffill_one_value(self, sequence):
        """Test key_to_array with forward filling for missing values and only one value at end available."""
        interval = to_duration("1 hour")
        record1 = self.create_test_record(pendulum.datetime(2023, 11, 6, 2), 1.0)
        sequence.insert_by_datetime(record1)

        array = sequence.key_to_array(
            key="data_value",
            start_datetime=pendulum.datetime(2023, 11, 6),
            end_datetime=pendulum.datetime(2023, 11, 6, 4),
            interval=interval,
            fill_method="ffill",
        )
        assert len(array) == 4
        assert array[0] == 1.0  # Backward-filled value
        assert array[1] == 1.0  # Backward-filled value
        assert array[2] == 1.0
        assert array[2] == 1.0  # Forward-filled value

    def test_key_to_array_bfill(self, sequence):
        """Test key_to_array with backward filling for missing values."""
        interval = to_duration("1 hour")
        record1 = self.create_test_record(pendulum.datetime(2023, 11, 6, 0), 0.8)
        record2 = self.create_test_record(pendulum.datetime(2023, 11, 6, 2), 1.0)
        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)

        array = sequence.key_to_array(
            key="data_value",
            start_datetime=pendulum.datetime(2023, 11, 6),
            end_datetime=pendulum.datetime(2023, 11, 6, 3),
            interval=interval,
            fill_method="bfill",
        )
        assert len(array) == 3
        assert array[0] == 0.8
        assert array[1] == 1.0  # Backward-filled value
        assert array[2] == 1.0

    def test_key_to_array_with_truncation(self, sequence):
        """Test truncation behavior in key_to_array."""
        interval = to_duration("1 hour")
        record1 = self.create_test_record(pendulum.datetime(2023, 11, 5, 23), 0.8)
        record2 = self.create_test_record(pendulum.datetime(2023, 11, 6, 1), 1.0)
        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)

        #assert sequence is None

        array = sequence.key_to_array(
            key="data_value",
            start_datetime=pendulum.datetime(2023, 11, 5, 23),
            end_datetime=pendulum.datetime(2023, 11, 6, 2),
            interval=interval,
        )

        assert len(array) == 3
        assert array[0] == 0.8
        assert array[1] == 0.9  # Interpolated from previous day
        assert array[2] == 1.0

    def test_key_to_array_with_none(self, sequence):
        """Test handling of empty series in key_to_array."""
        interval = to_duration("1 hour")
        array = sequence.key_to_array(
            key="data_value",
            start_datetime=pendulum.datetime(2023, 11, 6),
            end_datetime=pendulum.datetime(2023, 11, 6, 3),
            interval=interval,
        )
        assert isinstance(array, np.ndarray)
        assert np.all(array == None)

    def test_key_to_array_with_one(self, sequence):
        """Test handling of one element series in key_to_array."""
        interval = to_duration("1 hour")
        record1 = self.create_test_record(pendulum.datetime(2023, 11, 5, 23), 0.8)
        sequence.insert_by_datetime(record1)

        array = sequence.key_to_array(
            key="data_value",
            start_datetime=pendulum.datetime(2023, 11, 5, 23),
            end_datetime=pendulum.datetime(2023, 11, 6, 2),
            interval=interval,
        )
        assert len(array) == 3
        assert array[0] == 0.8
        assert array[1] == 0.8  # Interpolated from previous day
        assert array[2] == 0.8  # Interpolated from previous day

    def test_key_to_array_invalid_fill_method(self, sequence):
        """Test invalid fill_method raises an error."""
        interval = to_duration("1 hour")
        record1 = self.create_test_record(pendulum.datetime(2023, 11, 6, 0), 0.8)
        sequence.insert_by_datetime(record1)

        with pytest.raises(ValueError, match="Unsupported fill method: invalid"):
            sequence.key_to_array(
                key="data_value",
                start_datetime=pendulum.datetime(2023, 11, 6),
                end_datetime=pendulum.datetime(2023, 11, 6, 1),
                interval=interval,
                fill_method="invalid",
            )

    def test_key_to_array_resample_mean(self, sequence):
        """Test that numeric resampling uses mean when multiple values fall into one interval."""
        interval = to_duration("1 hour")
        # Insert values every 15 minutes within the same hour
        record1 = self.create_test_record(pendulum.datetime(2023, 11, 6, 0, 0), 1.0)
        record2 = self.create_test_record(pendulum.datetime(2023, 11, 6, 0, 15), 2.0)
        record3 = self.create_test_record(pendulum.datetime(2023, 11, 6, 0, 30), 3.0)
        record4 = self.create_test_record(pendulum.datetime(2023, 11, 6, 0, 45), 4.0)

        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)
        sequence.insert_by_datetime(record3)
        sequence.insert_by_datetime(record4)

        # Resample to hourly interval, expecting the mean of the 4 values
        array = sequence.key_to_array(
            key="data_value",
            start_datetime=pendulum.datetime(2023, 11, 6, 0),
            end_datetime=pendulum.datetime(2023, 11, 6, 1),
            interval=interval,
        )

        assert isinstance(array, np.ndarray)
        assert len(array) == 1  # one interval: 0:00-1:00
        # The first interval mean = (1+2+3+4)/4 = 2.5
        assert array[0] == pytest.approx(2.5)

    # ------------------------------------------------------------------
    # key_to_array — align_to_interval parameter
    # ------------------------------------------------------------------
    #
    # The existing tests above use start_datetime values that already sit on
    # clean hour/day boundaries, so the default alignment (origin=query_start)
    # and clock alignment (origin=epoch-floor) produce identical results.
    # The tests below specifically use off-boundary start times to expose
    # the difference and verify the new parameter.

    def test_key_to_array_align_false_origin_is_query_start(self, sequence):
        """Without align_to_interval the first bucket sits at query_start, not a clock boundary.

        With start_datetime at 10:07:00 and 15-min interval the first resampled
        bucket must be at 10:07:00 (origin = query_start), NOT at 10:00:00 or 10:15:00.
        """
        # Off-boundary start: 10:07
        start_dt = pendulum.datetime(2024, 6, 1, 10, 7, tz="UTC")
        end_dt = pendulum.datetime(2024, 6, 1, 12, 7, tz="UTC")

        # Records every 15 min so the resampled mean equals the input values
        for m in range(0, 120, 15):
            dt = pendulum.datetime(2024, 6, 1, 10, 7, tz="UTC").add(minutes=m)
            sequence.insert_by_datetime(self.create_test_record(dt, float(m)))

        array = sequence.key_to_array(
            key="data_value",
            start_datetime=start_dt,
            end_datetime=end_dt,
            interval=to_duration("15 minutes"),
            fill_method="time",
            boundary="strict",
            align_to_interval=False,
        )

        assert len(array) > 0
        # Reconstruct the pandas index that key_to_array used: origin=start_dt
        idx = pd.date_range(start=start_dt, periods=len(array), freq="900s")
        # First bucket must be exactly at start_dt (10:07)
        assert idx[0].minute == 7
        assert idx[0].second == 0

    def test_key_to_array_align_true_15min_buckets_on_quarter_hours(self, sequence):
        """align_to_interval=True produces timestamps on :00/:15/:30/:45 boundaries."""
        # Off-boundary start: 10:07
        start_dt = pendulum.datetime(2024, 6, 1, 10, 7, tz="UTC")
        end_dt = pendulum.datetime(2024, 6, 1, 12, 7, tz="UTC")

        # 1-min records across the window so resampling has data to work with
        for m in range(0, 121):
            dt = pendulum.datetime(2024, 6, 1, 10, 7, tz="UTC").add(minutes=m)
            sequence.insert_by_datetime(self.create_test_record(dt, float(m)))

        array = sequence.key_to_array(
            key="data_value",
            start_datetime=start_dt,
            end_datetime=end_dt,
            interval=to_duration("15 minutes"),
            fill_method="time",
            boundary="strict",
            align_to_interval=True,
        )

        assert len(array) > 0
        # Reconstruct the epoch-aligned index that key_to_array must have used
        import math
        epoch = int(start_dt.timestamp())
        floored_epoch = (epoch // 900) * 900  # floor to nearest 15-min boundary
        idx = pd.date_range(
            start=pd.Timestamp(floored_epoch, unit="s", tz="UTC"),
            periods=len(array),
            freq="900s",
        )
        # Every bucket must land on a :00/:15/:30/:45 minute mark with zero seconds
        for ts in idx:
            assert ts.minute % 15 == 0, (
                f"Bucket at {ts} is not on a 15-min boundary (minute={ts.minute})"
            )
            assert ts.second == 0, (
                f"Bucket at {ts} has non-zero seconds ({ts.second})"
            )

    def test_key_to_array_align_true_1hour_buckets_on_the_hour(self, sequence):
        """align_to_interval=True with 1-hour interval produces on-the-hour timestamps."""
        # Off-boundary start: 10:23
        start_dt = pendulum.datetime(2024, 6, 1, 10, 23, tz="UTC")
        end_dt = pendulum.datetime(2024, 6, 1, 15, 23, tz="UTC")

        for m in range(0, 301, 15):
            dt = pendulum.datetime(2024, 6, 1, 10, 23, tz="UTC").add(minutes=m)
            sequence.insert_by_datetime(self.create_test_record(dt, float(m)))

        array = sequence.key_to_array(
            key="data_value",
            start_datetime=start_dt,
            end_datetime=end_dt,
            interval=to_duration("1 hour"),
            fill_method="time",
            boundary="strict",
            align_to_interval=True,
        )

        assert len(array) > 0
        epoch = int(start_dt.timestamp())
        floored_epoch = (epoch // 3600) * 3600  # floor to nearest hour
        idx = pd.date_range(
            start=pd.Timestamp(floored_epoch, unit="s", tz="UTC"),
            periods=len(array),
            freq="1h",
        )
        for ts in idx:
            assert ts.minute == 0, (
                f"Bucket at {ts} should be on the hour (minute={ts.minute})"
            )
            assert ts.second == 0, (
                f"Bucket at {ts} has non-zero seconds ({ts.second})"
            )

    def test_key_to_array_align_true_when_start_already_on_boundary(self, sequence):
        """align_to_interval=True is a no-op when start_datetime is exactly on a boundary.

        With start at a clean 15-min mark both modes must produce identical arrays.
        """
        # Exactly on boundary: 10:00:00
        start_dt = pendulum.datetime(2024, 6, 1, 10, 0, tz="UTC")
        end_dt = pendulum.datetime(2024, 6, 1, 12, 0, tz="UTC")

        for m in range(0, 121, 15):
            dt = pendulum.datetime(2024, 6, 1, 10, 0, tz="UTC").add(minutes=m)
            sequence.insert_by_datetime(self.create_test_record(dt, float(m)))

        arr_aligned = sequence.key_to_array(
            key="data_value",
            start_datetime=start_dt,
            end_datetime=end_dt,
            interval=to_duration("15 minutes"),
            fill_method="time",
            boundary="strict",
            align_to_interval=True,
        )
        arr_default = sequence.key_to_array(
            key="data_value",
            start_datetime=start_dt,
            end_datetime=end_dt,
            interval=to_duration("15 minutes"),
            fill_method="time",
            boundary="strict",
            align_to_interval=False,
        )

        assert len(arr_aligned) == len(arr_default)
        np.testing.assert_array_almost_equal(arr_aligned, arr_default, decimal=6)

    def test_key_to_array_align_true_without_start_datetime(self, sequence):
        """align_to_interval=True with no start_datetime must not raise.

        Without a query_start there is no origin to snap; behaviour falls back
        to 'start_day' (same as default). No exception is expected.
        """
        for m in range(0, 121, 15):
            dt = pendulum.datetime(2024, 6, 1, 10, 7, tz="UTC").add(minutes=m)
            sequence.insert_by_datetime(self.create_test_record(dt, float(m)))

        array = sequence.key_to_array(
            key="data_value",
            start_datetime=None,
            end_datetime=pendulum.datetime(2024, 6, 1, 12, 7, tz="UTC"),
            interval=to_duration("15 minutes"),
            fill_method="time",
            boundary="strict",
            align_to_interval=True,
        )

        assert isinstance(array, np.ndarray)
        assert len(array) > 0

    def test_key_to_array_align_true_output_within_requested_window(self, sequence):
        """align_to_interval=True truncates output to [start_datetime, end_datetime).

        The epoch-floor origin may generate a bucket before start_datetime (e.g. 10:00
        when start is 10:07), but key_to_array must truncate it away.  The surviving
        buckets are verified directly by reconstructing the index from the first
        surviving timestamp (the first epoch-aligned bucket >= start_datetime).

        Also checks that all surviving buckets are on 15-min clock boundaries.
        """
        start_dt = pendulum.datetime(2024, 6, 1, 10, 7, tz="UTC")
        end_dt = pendulum.datetime(2024, 6, 1, 13, 7, tz="UTC")

        for m in range(0, 181):
            dt = pendulum.datetime(2024, 6, 1, 10, 7, tz="UTC").add(minutes=m)
            sequence.insert_by_datetime(self.create_test_record(dt, float(m)))

        array = sequence.key_to_array(
            key="data_value",
            start_datetime=start_dt,
            end_datetime=end_dt,
            interval=to_duration("15 minutes"),
            fill_method="time",
            boundary="strict",
            align_to_interval=True,
        )

        assert len(array) > 0

        # The first surviving bucket is the first epoch-aligned timestamp >= start_dt.
        # Compute it the same way key_to_array does: floor then step forward if needed.
        epoch = int(start_dt.timestamp())
        floored_epoch = (epoch // 900) * 900
        first_bucket = pd.Timestamp(floored_epoch, unit="s", tz="UTC")
        if first_bucket < pd.Timestamp(start_dt):
            first_bucket += pd.Timedelta(seconds=900)

        idx = pd.date_range(start=first_bucket, periods=len(array), freq="900s")

        start_pd = pd.Timestamp(start_dt)
        end_pd = pd.Timestamp(end_dt)
        for ts in idx:
            assert ts >= start_pd, f"Bucket {ts} is before start_datetime {start_pd}"
            assert ts < end_pd, f"Bucket {ts} is at or after end_datetime {end_pd}"
            assert ts.minute % 15 == 0, f"Bucket {ts} is not on a 15-min boundary"
            assert ts.second == 0, f"Bucket {ts} has non-zero seconds"

    def test_key_to_array_align_true_preserves_mean_values(self, sequence):
        """align_to_interval=True does not corrupt resampled values.

        A constant-valued series must resample to the same constant regardless
        of bucket alignment.
        """
        # 1-min records with constant value 42.0, starting off-boundary
        start_dt = pendulum.datetime(2024, 6, 1, 10, 7, tz="UTC")
        end_dt = pendulum.datetime(2024, 6, 1, 12, 7, tz="UTC")

        for m in range(0, 121):
            dt = pendulum.datetime(2024, 6, 1, 10, 7, tz="UTC").add(minutes=m)
            sequence.insert_by_datetime(self.create_test_record(dt, 42.0))

        array = sequence.key_to_array(
            key="data_value",
            start_datetime=start_dt,
            end_datetime=end_dt,
            interval=to_duration("15 minutes"),
            fill_method="time",
            boundary="strict",
            align_to_interval=True,
        )

        assert len(array) > 0
        for v in array:
            if v is not None:
                assert abs(v - 42.0) < 1e-6, f"Expected 42.0, got {v}"

    def test_key_to_array_align_true_compaction_call_pattern(self, sequence):
        """Verify the call pattern used by _db_compact_tier produces clock-aligned timestamps.

        _db_compact_tier calls key_to_array with boundary='strict', fill_method='time',
        align_to_interval=True on a window whose start has arbitrary sub-second precision.
        All output buckets must land on 15-min boundaries so that compacted records are
        stored at predictable, human-readable timestamps.
        """
        # Non-round base time: 08:43 — chosen to expose any origin-alignment bug
        base_dt = pendulum.datetime(2024, 6, 1, 8, 43, tz="UTC")
        window_end = pendulum.datetime(2024, 6, 1, 11, 43, tz="UTC")

        for m in range(0, 181):
            dt = base_dt.add(minutes=m)
            sequence.insert_by_datetime(self.create_test_record(dt, float(m)))

        array = sequence.key_to_array(
            key="data_value",
            start_datetime=base_dt,
            end_datetime=window_end,
            interval=to_duration("15 minutes"),
            fill_method="time",
            boundary="strict",
            align_to_interval=True,
        )

        assert len(array) > 0
        epoch = int(base_dt.timestamp())
        floored_epoch = (epoch // 900) * 900
        idx = pd.date_range(
            start=pd.Timestamp(floored_epoch, unit="s", tz="UTC"),
            periods=len(array),
            freq="900s",
        )
        for ts in idx:
            assert ts.minute % 15 == 0, (
                f"Compacted record at {ts} is not on a 15-min boundary (minute={ts.minute})"
            )
            assert ts.second == 0, (
                f"Compacted record at {ts} has non-zero seconds ({ts.second})"
            )

    def test_delete_by_datetime_range(self, sequence):
        dt1 = to_datetime("2023-11-05")
        dt2 = to_datetime("2023-11-06")
        dt3 = to_datetime("2023-11-07")
        record1 = self.create_test_record(dt1, 0.8)
        record2 = self.create_test_record(dt2, 0.9)
        record3 = self.create_test_record(dt3, 1.0)
        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)
        sequence.insert_by_datetime(record3)
        assert len(sequence) == 3
        sequence.delete_by_datetime(start_datetime=dt2, end_datetime=dt3)
        assert len(sequence) == 2
        assert sequence.records[0].date_time == dt1
        assert sequence.records[1].date_time == dt3

    def test_delete_by_datetime_start(self, sequence):
        dt1 = to_datetime("2023-11-05")
        dt2 = to_datetime("2023-11-06")
        record1 = self.create_test_record(dt1, 0.8)
        record2 = self.create_test_record(dt2, 0.9)
        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)
        assert len(sequence) == 2
        sequence.delete_by_datetime(start_datetime=dt2)
        assert len(sequence) == 1
        assert sequence.records[0].date_time == dt1

    def test_delete_by_datetime_end(self, sequence):
        dt1 = to_datetime("2023-11-05")
        dt2 = to_datetime("2023-11-06")
        record1 = self.create_test_record(dt1, 0.8)
        record2 = self.create_test_record(dt2, 0.9)
        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)
        assert len(sequence) == 2
        sequence.delete_by_datetime(end_datetime=dt2)
        assert len(sequence) == 1
        assert sequence.records[0].date_time == dt2

    def test_to_dict(self, sequence):
        dt = to_datetime("2023-11-06")
        record = self.create_test_record(dt, 0.8)
        sequence.insert_by_datetime(record)
        data_dict = sequence.to_dict()
        assert isinstance(data_dict, dict)
        # We need a new class - Sequences are singletons
        sequence2 = DerivedSequence2.from_dict(data_dict)
        assert sequence2.model_dump() == sequence.model_dump()

    def test_to_json(self, sequence):
        dt = to_datetime("2023-11-06")
        record = self.create_test_record(dt, 0.8)
        sequence.insert_by_datetime(record)
        json_str = sequence.to_json()
        assert isinstance(json_str, str)
        assert "2023-11-06" in json_str
        assert ": 0.8" in json_str

    def test_from_json(self, sequence, sequence2):
        json_str = sequence2.to_json()
        sequence = sequence.from_json(json_str)
        assert len(sequence) == len(sequence2)
        assert sequence.records[0].date_time == sequence2.records[0].date_time
        assert sequence.records[0].data_value == sequence2.records[0].data_value

    def test_key_to_value_exact_match(self, sequence):
        """Test key_to_value returns exact match when datetime matches a record."""
        dt = to_datetime("2023-11-05")
        record = self.create_test_record(dt, 0.75)
        sequence.insert_by_datetime(record)
        result = sequence.key_to_value("data_value", dt)
        assert result == 0.75

    def test_key_to_value_nearest(self, sequence):
        """Test key_to_value returns value closest in time to the given datetime."""
        record1 = self.create_test_record(datetime(2023, 11, 5, 12), 0.6)
        record2 = self.create_test_record(datetime(2023, 11, 6, 12), 0.9)
        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)
        dt = datetime(2023, 11, 6, 10)  # closer to record2
        result = sequence.key_to_value("data_value", dt, time_window=to_duration("48 hours"))
        assert result == 0.9

    def test_key_to_value_nearest_after(self, sequence):
        """Test key_to_value returns value nearest after the given datetime."""
        record1 = self.create_test_record(datetime(2023, 11, 5, 10), 0.7)
        record2 = self.create_test_record(datetime(2023, 11, 5, 15), 0.8)
        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)
        dt = datetime(2023, 11, 5, 14)  # closer to record2
        result = sequence.key_to_value("data_value", dt, time_window=to_duration("48 hours"))
        assert result == 0.8

    def test_key_to_value_empty_sequence(self, sequence):
        """Test key_to_value returns None when sequence is empty."""
        result = sequence.key_to_value("data_value", datetime(2023, 11, 5))
        assert result is None

    def test_key_to_value_missing_key(self, sequence):
        """Test key_to_value returns None when key is missing in records."""
        record = self.create_test_record(datetime(2023, 11, 5), None)
        sequence.insert_by_datetime(record)
        result = sequence.key_to_value("data_value", datetime(2023, 11, 5))
        assert result is None

    def test_key_to_value_multiple_records_with_none(self, sequence):
        """Test key_to_value skips records with None values."""
        r1 = self.create_test_record(datetime(2023, 11, 5), None)
        r2 = self.create_test_record(datetime(2023, 11, 6), 1.0)
        sequence.insert_by_datetime(r1)
        sequence.insert_by_datetime(r2)
        result = sequence.key_to_value("data_value", datetime(2023, 11, 5, 12), time_window=to_duration("48 hours"))
        assert result == 1.0

    def test_key_to_dict(self, sequence):
        record1 = self.create_test_record(datetime(2023, 11, 5), 0.8)
        record2 = self.create_test_record(datetime(2023, 11, 6), 0.9)
        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)
        data_dict = sequence.key_to_dict("data_value")
        assert isinstance(data_dict, dict)
        assert data_dict[to_datetime(datetime(2023, 11, 5), as_string=True)] == 0.8
        assert data_dict[to_datetime(datetime(2023, 11, 6), as_string=True)] == 0.9

    def test_key_to_lists(self, sequence):
        record1 = self.create_test_record(datetime(2023, 11, 5), 0.8)
        record2 = self.create_test_record(datetime(2023, 11, 6), 0.9)
        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)
        dates, values = sequence.key_to_lists("data_value")
        assert dates == [to_datetime(datetime(2023, 11, 5)), to_datetime(datetime(2023, 11, 6))]
        assert values == [0.8, 0.9]

    def test_to_dataframe_full_data(self, sequence):
        """Test conversion of all records to a DataFrame without filtering."""
        record1 = self.create_test_record("2024-01-01T12:00:00Z", 10)
        record2 = self.create_test_record("2024-01-01T13:00:00Z", 20)
        record3 = self.create_test_record("2024-01-01T14:00:00Z", 30)
        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)
        sequence.insert_by_datetime(record3)

        df = sequence.to_dataframe()

        # Validate DataFrame structure
        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        assert len(df) == 3  # All records should be included
        assert "data_value" in df.columns

    def test_to_dataframe_with_filter(self, sequence):
        """Test filtering records by datetime range."""
        record1 = self.create_test_record("2024-01-01T12:00:00Z", 10)
        record2 = self.create_test_record("2024-01-01T13:00:00Z", 20)
        record3 = self.create_test_record("2024-01-01T14:00:00Z", 30)
        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)
        sequence.insert_by_datetime(record3)

        start = to_datetime("2024-01-01T12:30:00Z")
        end = to_datetime("2024-01-01T14:00:00Z")

        df = sequence.to_dataframe(start_datetime=start, end_datetime=end)

        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        assert len(df) == 1  # Only one record should match the range
        assert df.index[0] == pd.Timestamp("2024-01-01T13:00:00Z")

    def test_to_dataframe_no_matching_records(self, sequence):
        """Test when no records match the given datetime filter."""
        record1 = self.create_test_record("2024-01-01T12:00:00Z", 10)
        record2 = self.create_test_record("2024-01-01T13:00:00Z", 20)
        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)

        start = to_datetime("2024-01-01T14:00:00Z")  # Start time after all records
        end = to_datetime("2024-01-01T15:00:00Z")

        df = sequence.to_dataframe(start_datetime=start, end_datetime=end)

        assert isinstance(df, pd.DataFrame)
        assert df.empty  # No records should match

    def test_to_dataframe_empty_sequence(self, sequence):
        """Test when DataSequence has no records."""
        sequence = DataSequence(records=[])

        df = sequence.to_dataframe()

        assert isinstance(df, pd.DataFrame)
        assert df.empty  # Should return an empty DataFrame

    def test_to_dataframe_no_start_datetime(self, sequence):
        """Test when only end_datetime is given (all past records should be included)."""
        record1 = self.create_test_record("2024-01-01T12:00:00Z", 10)
        record2 = self.create_test_record("2024-01-01T13:00:00Z", 20)
        record3 = self.create_test_record("2024-01-01T14:00:00Z", 30)
        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)
        sequence.insert_by_datetime(record3)

        end = to_datetime("2024-01-01T13:00:00Z")  # Include only first record

        df = sequence.to_dataframe(end_datetime=end)

        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        assert len(df) == 1
        assert df.index[0] == pd.Timestamp("2024-01-01T12:00:00Z")

    def test_to_dataframe_no_end_datetime(self, sequence):
        """Test when only start_datetime is given (all future records should be included)."""
        record1 = self.create_test_record("2024-01-01T12:00:00Z", 10)
        record2 = self.create_test_record("2024-01-01T13:00:00Z", 20)
        record3 = self.create_test_record("2024-01-01T14:00:00Z", 30)
        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)
        sequence.insert_by_datetime(record3)

        start = to_datetime("2024-01-01T13:00:00Z")  # Include last two records

        df = sequence.to_dataframe(start_datetime=start)

        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        assert len(df) == 2
        assert df.index[0] == pd.Timestamp("2024-01-01T13:00:00Z")


class TestDataProvider:
    # Fixtures and helper functions
    @pytest.fixture
    def provider(self):
        """Fixture to provide an instance of TestDataProvider for testing."""
        DerivedDataProvider.provider_enabled = True
        DerivedDataProvider.provider_updated = False
        return DerivedDataProvider()

    @pytest.fixture
    def sample_start_datetime(self):
        """Fixture for a sample start datetime."""
        return to_datetime(datetime(2024, 11, 1, 12, 0))

    def create_test_record(self, date, value):
        """Helper function to create a test DataRecord."""
        return DerivedRecord(date_time=date, data_value=value)

    # Tests

    def test_singleton_behavior(self, provider):
        """Test that DataProvider enforces singleton behavior."""
        instance1 = provider
        instance2 = DerivedDataProvider()
        assert instance1 is instance2, (
            "Singleton pattern is not enforced; instances are not the same."
        )

    def test_update_method_with_defaults(self, provider, sample_start_datetime, monkeypatch):
        """Test the `update` method with default parameters."""
        ems_eos = get_ems()

        ems_eos.set_start_datetime(sample_start_datetime)
        provider.update_data()

        assert provider.ems_start_datetime == sample_start_datetime

    def test_update_method_force_enable(self, provider, monkeypatch):
        """Test that `update` executes when `force_enable` is True, even if `enabled` is False."""
        # Override enabled to return False for this test
        DerivedDataProvider.provider_enabled = False
        DerivedDataProvider.provider_updated = False
        provider.update_data(force_enable=True)
        assert provider.enabled() is False, "Provider should be disabled, but enabled() is True."
        assert DerivedDataProvider.provider_updated is True, (
            "Provider should have been executed, but was not."
        )

    def test_delete_by_datetime(self, provider, sample_start_datetime):
        """Test `delete_by_datetime` method for removing records by datetime range."""
        # Add records to the provider for deletion testing
        records = [
            self.create_test_record(sample_start_datetime - to_duration("3 hours"), 1),
            self.create_test_record(sample_start_datetime - to_duration("1 hour"), 2),
            self.create_test_record(sample_start_datetime + to_duration("1 hour"), 3),
        ]
        for record in records:
            provider.insert_by_datetime(record)

        provider.delete_by_datetime(
            start_datetime=sample_start_datetime - to_duration("2 hours"),
            end_datetime=sample_start_datetime + to_duration("2 hours"),
        )
        assert len(provider.records) == 1, (
            "Only one record should remain after deletion by datetime."
        )
        assert provider.records[0].date_time == sample_start_datetime - to_duration("3 hours"), (
            "Unexpected record remains."
        )


class NewTestDataImportProvider:

    # Fixtures and helper functions
    @pytest.fixture
    def provider(self):
        """Fixture to provide an instance of DerivedDataImportProvider for testing."""
        DerivedDataImportProvider.provider_enabled = True
        DerivedDataImportProvider.provider_updated = True
        return DerivedDataImportProvider()

# ---------------------------------------------------------------------------
# import_from_dict
# ---------------------------------------------------------------------------

    def test_import_from_dict_basic(self, provider):
        data = {
            "start_datetime": "2024-01-01 00:00:00",
            "interval": "1 hour",
            "power": [1, 2, 3],
        }

        provider.import_from_dict(data)

        assert provider.records is not None
        assert provider.records[0]["power"] == 1
        assert provider.records[1]["power"] == 2


    def test_import_from_dict_default_start_and_interval(self, provider):
        data = {
            "power": [10, 20],
        }

        provider.import_from_dict(data)

        assert len(provider._updates) == 2


    def test_import_from_dict_with_prefix(self, provider):
        data = {
            "load_power": [1, 2],
            "other": [5, 6],
        }

        provider.import_from_dict(data, key_prefix="load")

        assert len(provider._updates) == 2
        assert all(update[1] == "load_power" for update in provider._updates)


    def test_import_from_dict_mismatching_lengths(self, provider):
        data = {
            "power": [1, 2],
            "voltage": [1],
        }

        with pytest.raises(ValueError):
            provider.import_from_dict(data)


    def test_import_from_dict_invalid_interval(self, provider):
        data = {
            "interval": "17 minutes",  # does not divide hour
            "power": [1, 2, 3],
        }

        with pytest.raises(NotImplementedError):
            provider.import_from_dict(data)


    def test_import_from_dict_skips_none_and_nan(self, provider):
        data = {
            "power": [1, None, np.nan, 4],
        }

        provider.import_from_dict(data)

        # only 1 and 4 should be written
        assert len(provider._updates) == 2
        assert provider._updates[0][2] == 1
        assert provider._updates[1][2] == 4


    def test_import_from_dict_invalid_value_type(self, provider):
        data = {
            "power": "not a list"
        }

        with pytest.raises(ValueError):
            provider.import_from_dict(data)


# ---------------------------------------------------------------------------
# import_from_dataframe
# ---------------------------------------------------------------------------

    def test_import_from_dataframe_with_datetime_index(self, provider):
        index = pd.date_range("2024-01-01", periods=3, freq="H")
        df = pd.DataFrame({"power": [1, 2, 3]}, index=index)

        provider.import_from_dataframe(df)

        assert len(provider._updates) == 3
        assert provider._updates[0][2] == 1


    def test_import_from_dataframe_without_datetime_index(self, provider):
        df = pd.DataFrame({"power": [5, 6, 7]})

        provider.import_from_dataframe(
            df,
            start_datetime=datetime(2024, 1, 1),
            interval=to_duration("1 hour"),
        )

        assert len(provider._updates) == 3


    def test_import_from_dataframe_prefix_filter(self, provider):
        df = pd.DataFrame({
            "load_power": [1, 2],
            "other": [3, 4],
        })

        provider.import_from_dataframe(df, key_prefix="load")

        assert len(provider._updates) == 2
        assert all(update[1] == "load_power" for update in provider._updates)


    def test_import_from_dataframe_invalid_input(self, provider):
        with pytest.raises(ValueError):
            provider.import_from_dataframe("not a dataframe")


# ---------------------------------------------------------------------------
# import_from_json
# ---------------------------------------------------------------------------

    def test_import_from_json_simple_dict(self, provider):
        json_str = json.dumps({
            "power": [1, 2, 3]
        })

        provider.import_from_json(json_str)

        assert len(provider._updates) == 3


    def test_import_from_json_invalid(self, provider):
        with pytest.raises(ValueError):
            provider.import_from_json("this is not json")


# ---------------------------------------------------------------------------
# import_from_file
# ---------------------------------------------------------------------------

    def test_import_from_file(self, provider, tmp_path):
        file_path = tmp_path / "data.json"

        file_path.write_text(json.dumps({
            "power": [1, 2]
        }))

        provider.import_from_file(file_path)

        assert len(provider._updates) == 2



class TestDataContainer:
    # Fixture and helpers
    @pytest.fixture
    def container(self):
        container = DerivedDataContainer()
        return container

    @pytest.fixture
    def container_with_providers(self):
        record1 = self.create_test_record(datetime(2023, 11, 5), 1)
        record2 = self.create_test_record(datetime(2023, 11, 6), 2)
        record3 = self.create_test_record(datetime(2023, 11, 7), 3)
        provider = DerivedDataProvider()
        provider.delete_by_datetime(start_datetime=None, end_datetime=None)
        assert len(provider) == 0
        provider.insert_by_datetime(record1)
        provider.insert_by_datetime(record2)
        provider.insert_by_datetime(record3)
        assert len(provider) == 3
        container = DerivedDataContainer()
        container.providers.clear()
        assert len(container.providers) == 0
        container.providers.append(provider)
        assert len(container.providers) == 1
        return container

    def create_test_record(self, date, value):
        """Helper function to create a test DataRecord."""
        return DerivedRecord(date_time=date, data_value=value)

    def test_append_provider(self, container):
        assert len(container.providers) == 0
        container.providers.append(DerivedDataProvider())
        assert len(container.providers) == 1
        assert isinstance(container.providers[0], DerivedDataProvider)

    @pytest.mark.skip(reason="type check not implemented")
    def test_append_provider_invalid_type(self, container):
        with pytest.raises(ValueError, match="must be an instance of DataProvider"):
            container.providers.append("not_a_provider")

    def test_getitem_existing_key(self, container_with_providers):
        assert len(container_with_providers.providers) == 1
        # check all keys are available (don't care for position)
        for key in ["data_value", "date_time"]:
            assert key in list(container_with_providers.keys())
        series = container_with_providers["data_value"]
        assert isinstance(series, pd.Series)
        assert series.name == "data_value"
        assert series.tolist() == [1.0, 2.0, 3.0]

    def test_getitem_non_existing_key(self, container_with_providers):
        with pytest.raises(KeyError, match="No data found for key 'non_existent_key'"):
            container_with_providers["non_existent_key"]

    def test_setitem_existing_key(self, container_with_providers):
        new_series = container_with_providers["data_value"]
        new_series[:] = [4, 5, 6]
        container_with_providers["data_value"] = new_series
        series = container_with_providers["data_value"]
        assert series.name == "data_value"
        assert series.tolist() == [4, 5, 6]

    def test_setitem_invalid_value(self, container_with_providers):
        with pytest.raises(ValueError, match="Value must be an instance of pd.Series"):
            container_with_providers["test_key"] = "not_a_series"

    def test_setitem_non_existing_key(self, container_with_providers):
        new_series = pd.Series([4, 5, 6], name="non_existent_key")
        with pytest.raises(KeyError, match="Key 'non_existent_key' not found"):
            container_with_providers["non_existent_key"] = new_series

    def test_delitem_existing_key(self, container_with_providers):
        del container_with_providers["data_value"]
        series = container_with_providers["data_value"]
        assert series.name == "data_value"
        assert series.tolist() == []

    def test_delitem_non_existing_key(self, container_with_providers):
        with pytest.raises(KeyError, match="Key 'non_existent_key' not found"):
            del container_with_providers["non_existent_key"]

    def test_len(self, container_with_providers):
        assert len(container_with_providers) == 5

    def test_repr(self, container_with_providers):
        representation = repr(container_with_providers)
        assert representation.startswith("DerivedDataContainer(")
        assert "DerivedDataProvider" in representation

    def test_to_json(self, container_with_providers):
        json_str = container_with_providers.to_json()
        container_other = DerivedDataContainer.from_json(json_str)
        assert container_other == container_with_providers

    def test_from_json(self, container_with_providers):
        json_str = container_with_providers.to_json()
        container = DerivedDataContainer.from_json(json_str)
        assert isinstance(container, DerivedDataContainer)
        assert len(container.providers) == 1
        assert container.providers[0] == container_with_providers.providers[0]

    def test_provider_by_id(self, container_with_providers):
        provider = container_with_providers.provider_by_id("DerivedDataProvider")
        assert isinstance(provider, DerivedDataProvider)
