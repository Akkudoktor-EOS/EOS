from datetime import datetime, timezone
from typing import Any, ClassVar, List, Optional, Union

import numpy as np
import pandas as pd
import pendulum
import pytest
from pydantic import Field, ValidationError

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.dataabc import (
    DataBase,
    DataContainer,
    DataImportProvider,
    DataProvider,
    DataRecord,
    DataSequence,
)
from akkudoktoreos.core.ems import get_ems
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime, to_duration

# Derived classes for testing
# ---------------------------


class DerivedConfig(SettingsBaseModel):
    env_var: Optional[int] = Field(default=None, description="Test config by environment var")
    instance_field: Optional[str] = Field(default=None, description="Test config by instance field")
    class_constant: Optional[int] = Field(default=None, description="Test config by class constant")


class DerivedBase(DataBase):
    instance_field: Optional[str] = Field(default=None, description="Field Value")
    class_constant: ClassVar[int] = 30


class DerivedRecord(DataRecord):
    data_value: Optional[float] = Field(default=None, description="Data Value")


class DerivedSequence(DataSequence):
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


class TestDataBase:
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
        assert len(record) == 2

    def test_to_dict(self):
        record = self.create_test_record(datetime(2024, 1, 3, tzinfo=timezone.utc), 10.0)
        record.data_value = 20.0
        record_dict = record.to_dict()
        assert "data_value" in record_dict
        assert record_dict["data_value"] == 20.0
        record2 = DerivedRecord.from_dict(record_dict)
        assert record2 == record

    def test_to_json(self):
        record = self.create_test_record(datetime(2024, 1, 3, tzinfo=timezone.utc), 10.0)
        record.data_value = 20.0
        json_str = record.to_json()
        assert "data_value" in json_str
        assert "20.0" in json_str
        record2 = DerivedRecord.from_json(json_str)
        assert record2 == record


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
        sequence.append(record1)
        sequence.append(record2)
        assert len(sequence) == 2
        return sequence

    def create_test_record(self, date, value):
        """Helper function to create a test DataRecord."""
        return DerivedRecord(date_time=date, data_value=value)

    # Test cases
    def test_getitem(self, sequence):
        assert len(sequence) == 0
        record = self.create_test_record("2024-01-01 00:00:00", 0)
        sequence.insert_by_datetime(record)
        assert isinstance(sequence[0], DerivedRecord)

    def test_setitem(self, sequence2):
        new_record = self.create_test_record(datetime(2024, 1, 3, tzinfo=timezone.utc), 1)
        sequence2[0] = new_record
        assert sequence2[0].date_time == datetime(2024, 1, 3, tzinfo=timezone.utc)

    def test_set_record_at_index(self, sequence2):
        record1 = self.create_test_record(datetime(2024, 1, 3, tzinfo=timezone.utc), 1)
        record2 = self.create_test_record(datetime(2023, 11, 5), 0.8)
        sequence2[1] = record1
        assert sequence2[1].date_time == datetime(2024, 1, 3, tzinfo=timezone.utc)
        sequence2[0] = record2
        assert len(sequence2) == 2
        assert sequence2[0] == record2

    def test_insert_duplicate_date_record(self, sequence):
        record1 = self.create_test_record(datetime(2023, 11, 5), 0.8)
        record2 = self.create_test_record(datetime(2023, 11, 5), 0.9)  # Duplicate date
        sequence.insert_by_datetime(record1)
        sequence.insert_by_datetime(record2)
        assert len(sequence) == 1
        assert sequence[0].data_value == 0.9  # Record should have merged with new value

    def test_sort_by_datetime_ascending(self, sequence):
        """Test sorting records in ascending order by date_time."""
        records = [
            self.create_test_record(pendulum.datetime(2024, 11, 1), 0.7),
            self.create_test_record(pendulum.datetime(2024, 10, 1), 0.8),
            self.create_test_record(pendulum.datetime(2024, 12, 1), 0.9),
        ]
        for i, record in enumerate(records):
            sequence.insert(i, record)
        sequence.sort_by_datetime()
        sorted_dates = [record.date_time for record in sequence.records]
        for i, expected_date in enumerate(
            [
                pendulum.datetime(2024, 10, 1),
                pendulum.datetime(2024, 11, 1),
                pendulum.datetime(2024, 12, 1),
            ]
        ):
            assert compare_datetimes(sorted_dates[i], expected_date).equal

    def test_sort_by_datetime_descending(self, sequence):
        """Test sorting records in descending order by date_time."""
        records = [
            self.create_test_record(pendulum.datetime(2024, 11, 1), 0.7),
            self.create_test_record(pendulum.datetime(2024, 10, 1), 0.8),
            self.create_test_record(pendulum.datetime(2024, 12, 1), 0.9),
        ]
        for i, record in enumerate(records):
            sequence.insert(i, record)
        sequence.sort_by_datetime(reverse=True)
        sorted_dates = [record.date_time for record in sequence.records]
        for i, expected_date in enumerate(
            [
                pendulum.datetime(2024, 12, 1),
                pendulum.datetime(2024, 11, 1),
                pendulum.datetime(2024, 10, 1),
            ]
        ):
            assert compare_datetimes(sorted_dates[i], expected_date).equal

    def test_sort_by_datetime_with_none(self, sequence):
        """Test sorting records when some date_time values are None."""
        records = [
            self.create_test_record(pendulum.datetime(2024, 11, 1), 0.7),
            self.create_test_record(pendulum.datetime(2024, 10, 1), 0.8),
            self.create_test_record(pendulum.datetime(2024, 12, 1), 0.9),
        ]
        for i, record in enumerate(records):
            sequence.insert(i, record)
        sequence.records[2].date_time = None
        assert sequence.records[2].date_time is None
        sequence.sort_by_datetime()
        sorted_dates = [record.date_time for record in sequence.records]
        for i, expected_date in enumerate(
            [
                None,  # None values should come first
                pendulum.datetime(2024, 10, 1),
                pendulum.datetime(2024, 11, 1),
            ]
        ):
            if expected_date is None:
                assert sorted_dates[i] is None
            else:
                assert compare_datetimes(sorted_dates[i], expected_date).equal

    def test_sort_by_datetime_error_on_uncomparable(self, sequence):
        """Test error is raised when date_time contains uncomparable values."""
        records = [
            self.create_test_record(pendulum.datetime(2024, 11, 1), 0.7),
            self.create_test_record(pendulum.datetime(2024, 12, 1), 0.9),
            self.create_test_record(pendulum.datetime(2024, 10, 1), 0.8),
        ]
        for i, record in enumerate(records):
            sequence.insert(i, record)
        with pytest.raises(
            ValidationError, match="Date string not_a_datetime does not match any known formats."
        ):
            sequence.records[2].date_time = "not_a_datetime"  # Invalid date_time
            sequence.sort_by_datetime()

    def test_key_to_series(self, sequence):
        record = self.create_test_record(datetime(2023, 11, 6), 0.8)
        sequence.append(record)
        series = sequence.key_to_series("data_value")
        assert isinstance(series, pd.Series)
        assert series[to_datetime(datetime(2023, 11, 6))] == 0.8

    def test_key_from_series(self, sequence):
        series = pd.Series(
            data=[0.8, 0.9], index=pd.to_datetime([datetime(2023, 11, 5), datetime(2023, 11, 6)])
        )
        sequence.key_from_series("data_value", series)
        assert len(sequence) == 2
        assert sequence[0].data_value == 0.8
        assert sequence[1].data_value == 0.9

    def test_key_to_array(self, sequence):
        interval = to_duration("1 day")
        start_datetime = to_datetime("2023-11-6")
        last_datetime = to_datetime("2023-11-8")
        end_datetime = to_datetime("2023-11-9")
        record = self.create_test_record(start_datetime, float(start_datetime.day))
        sequence.insert_by_datetime(record)
        record = self.create_test_record(last_datetime, float(last_datetime.day))
        sequence.insert_by_datetime(record)
        assert sequence[0].data_value == 6.0
        assert sequence[1].data_value == 8.0

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
        assert len(array) == 3
        assert array[0] == start_datetime.day
        assert array[1] == 7
        assert array[2] == last_datetime.day

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

        array = sequence.key_to_array(
            key="data_value",
            start_datetime=pendulum.datetime(2023, 11, 6),
            end_datetime=pendulum.datetime(2023, 11, 6, 2),
            interval=interval,
        )
        assert len(array) == 2
        assert array[0] == 0.9  # Interpolated from previous day
        assert array[1] == 1.0

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
            start_datetime=pendulum.datetime(2023, 11, 6),
            end_datetime=pendulum.datetime(2023, 11, 6, 2),
            interval=interval,
        )
        assert len(array) == 2
        assert array[0] == 0.8  # Interpolated from previous day
        assert array[1] == 0.8

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

    def test_to_datetimeindex(self, sequence2):
        record1 = self.create_test_record(datetime(2023, 11, 5), 0.8)
        record2 = self.create_test_record(datetime(2023, 11, 6), 0.9)
        sequence2.insert(0, record1)
        sequence2.insert(1, record2)
        dt_index = sequence2.to_datetimeindex()
        assert isinstance(dt_index, pd.DatetimeIndex)
        assert dt_index[0] == to_datetime(datetime(2023, 11, 5))
        assert dt_index[1] == to_datetime(datetime(2023, 11, 6))

    def test_delete_by_datetime_range(self, sequence):
        record1 = self.create_test_record(datetime(2023, 11, 5), 0.8)
        record2 = self.create_test_record(datetime(2023, 11, 6), 0.9)
        record3 = self.create_test_record(datetime(2023, 11, 7), 1.0)
        sequence.append(record1)
        sequence.append(record2)
        sequence.append(record3)
        assert len(sequence) == 3
        sequence.delete_by_datetime(
            start_datetime=datetime(2023, 11, 6), end_datetime=datetime(2023, 11, 7)
        )
        assert len(sequence) == 2
        assert sequence[0].date_time == to_datetime(datetime(2023, 11, 5))
        assert sequence[1].date_time == to_datetime(datetime(2023, 11, 7))

    def test_delete_by_datetime_start(self, sequence):
        record1 = self.create_test_record(datetime(2023, 11, 5), 0.8)
        record2 = self.create_test_record(datetime(2023, 11, 6), 0.9)
        sequence.append(record1)
        sequence.append(record2)
        assert len(sequence) == 2
        sequence.delete_by_datetime(start_datetime=datetime(2023, 11, 6))
        assert len(sequence) == 1
        assert sequence[0].date_time == to_datetime(datetime(2023, 11, 5))

    def test_delete_by_datetime_end(self, sequence):
        record1 = self.create_test_record(datetime(2023, 11, 5), 0.8)
        record2 = self.create_test_record(datetime(2023, 11, 6), 0.9)
        sequence.append(record1)
        sequence.append(record2)
        assert len(sequence) == 2
        sequence.delete_by_datetime(end_datetime=datetime(2023, 11, 6))
        assert len(sequence) == 1
        assert sequence[0].date_time == to_datetime(datetime(2023, 11, 6))

    def test_filter_by_datetime(self, sequence):
        record1 = self.create_test_record(datetime(2023, 11, 5), 0.8)
        record2 = self.create_test_record(datetime(2023, 11, 6), 0.9)
        sequence.append(record1)
        sequence.append(record2)
        filtered_sequence = sequence.filter_by_datetime(start_datetime=datetime(2023, 11, 6))
        assert len(filtered_sequence) == 1
        assert filtered_sequence[0].date_time == to_datetime(datetime(2023, 11, 6))

    def test_to_dict(self, sequence):
        record = self.create_test_record(datetime(2023, 11, 6), 0.8)
        sequence.append(record)
        data_dict = sequence.to_dict()
        assert isinstance(data_dict, dict)
        sequence_other = sequence.from_dict(data_dict)
        assert sequence_other == sequence

    def test_to_json(self, sequence):
        record = self.create_test_record(datetime(2023, 11, 6), 0.8)
        sequence.append(record)
        json_str = sequence.to_json()
        assert isinstance(json_str, str)
        assert "2023-11-06" in json_str
        assert ": 0.8" in json_str

    def test_from_json(self, sequence, sequence2):
        json_str = sequence2.to_json()
        sequence = sequence.from_json(json_str)
        assert len(sequence) == len(sequence2)
        assert sequence[0].date_time == sequence2[0].date_time
        assert sequence[0].data_value == sequence2[0].data_value

    def test_key_to_dict(self, sequence):
        record1 = self.create_test_record(datetime(2023, 11, 5), 0.8)
        record2 = self.create_test_record(datetime(2023, 11, 6), 0.9)
        sequence.append(record1)
        sequence.append(record2)
        data_dict = sequence.key_to_dict("data_value")
        assert isinstance(data_dict, dict)
        assert data_dict[to_datetime(datetime(2023, 11, 5), as_string=True)] == 0.8
        assert data_dict[to_datetime(datetime(2023, 11, 6), as_string=True)] == 0.9

    def test_key_to_lists(self, sequence):
        record1 = self.create_test_record(datetime(2023, 11, 5), 0.8)
        record2 = self.create_test_record(datetime(2023, 11, 6), 0.9)
        sequence.append(record1)
        sequence.append(record2)
        dates, values = sequence.key_to_lists("data_value")
        assert dates == [to_datetime(datetime(2023, 11, 5)), to_datetime(datetime(2023, 11, 6))]
        assert values == [0.8, 0.9]

    def test_to_dataframe_full_data(self, sequence):
        """Test conversion of all records to a DataFrame without filtering."""
        record1 = self.create_test_record("2024-01-01T12:00:00Z", 10)
        record2 = self.create_test_record("2024-01-01T13:00:00Z", 20)
        record3 = self.create_test_record("2024-01-01T14:00:00Z", 30)
        sequence.append(record1)
        sequence.append(record2)
        sequence.append(record3)

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
        sequence.append(record1)
        sequence.append(record2)
        sequence.append(record3)

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
        sequence.append(record1)
        sequence.append(record2)

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
        sequence.append(record1)
        sequence.append(record2)
        sequence.append(record3)

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
        sequence.append(record1)
        sequence.append(record2)
        sequence.append(record3)

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

        assert provider.start_datetime == sample_start_datetime

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
        provider.records = [
            self.create_test_record(sample_start_datetime - to_duration("3 hours"), 1),
            self.create_test_record(sample_start_datetime - to_duration("1 hour"), 2),
            self.create_test_record(sample_start_datetime + to_duration("1 hour"), 3),
        ]

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


class TestDataImportProvider:
    # Fixtures and helper functions
    @pytest.fixture
    def provider(self):
        """Fixture to provide an instance of DerivedDataImportProvider for testing."""
        DerivedDataImportProvider.provider_enabled = True
        DerivedDataImportProvider.provider_updated = False
        return DerivedDataImportProvider()

    @pytest.mark.parametrize(
        "start_datetime, value_count, expected_mapping_count",
        [
            ("2024-11-10 00:00:00", 24, 24),  # No DST in Germany
            ("2024-08-10 00:00:00", 24, 24),  # DST in Germany
            ("2024-03-31 00:00:00", 24, 23),  # DST change in Germany (23 hours/ day)
            ("2024-10-27 00:00:00", 24, 25),  # DST change in Germany (25 hours/ day)
        ],
    )
    def test_import_datetimes(self, provider, start_datetime, value_count, expected_mapping_count):
        start_datetime = to_datetime(start_datetime, in_timezone="Europe/Berlin")

        value_datetime_mapping = provider.import_datetimes(start_datetime, value_count)

        assert len(value_datetime_mapping) == expected_mapping_count

    @pytest.mark.parametrize(
        "start_datetime, value_count, expected_mapping_count",
        [
            ("2024-11-10 00:00:00", 24, 24),  # No DST in Germany
            ("2024-08-10 00:00:00", 24, 24),  # DST in Germany
            ("2024-03-31 00:00:00", 24, 23),  # DST change in Germany (23 hours/ day)
            ("2024-10-27 00:00:00", 24, 25),  # DST change in Germany (25 hours/ day)
        ],
    )
    def test_import_datetimes_utc(
        self, set_other_timezone, provider, start_datetime, value_count, expected_mapping_count
    ):
        original_tz = set_other_timezone("Etc/UTC")
        start_datetime = to_datetime(start_datetime, in_timezone="Europe/Berlin")
        assert start_datetime.timezone.name == "Europe/Berlin"

        value_datetime_mapping = provider.import_datetimes(start_datetime, value_count)

        assert len(value_datetime_mapping) == expected_mapping_count


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
        provider.clear()
        assert len(provider) == 0
        provider.append(record1)
        provider.append(record2)
        provider.append(record3)
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
        assert len(container_with_providers) == 3

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
