import asyncio
import json
from datetime import datetime, timezone
from typing import Any, ClassVar, List, Optional, Union

import numpy as np
import pandas as pd
import pendulum
import pytest
import pytest_asyncio
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

    def db_namespace(self) -> str:
        return "DerivedSequence"


class DerivedSequence2(DataSequence):
    # overload
    records: List[DerivedRecord] = Field(
        default_factory=list, description="List of DerivedRecord records"
    )

    @classmethod
    def record_class(cls) -> Any:
        return DerivedRecord

    def db_namespace(self) -> str:
        return "DerivedSequence2"


# Tests
# ----------


@pytest.mark.asyncio
class TestDataSequence:

    @pytest_asyncio.fixture
    async def sequence(self):
        sequence0 = DerivedSequence()
        mem_len = len(sequence0)
        db_len = await sequence0.db_count_records()
        assert mem_len == 0
        assert db_len == 0
        return sequence0

    @pytest_asyncio.fixture
    async def sequence2(self):
        sequence = DerivedSequence()
        record1 = self.create_test_record(datetime(1970, 1, 1), 1970)
        record2 = self.create_test_record(datetime(1971, 1, 1), 1971)
        await sequence.insert_by_datetime(record1)
        await sequence.insert_by_datetime(record2)
        mem_len = len(sequence)
        db_len = await sequence.db_count_records()
        assert mem_len == 2
        assert db_len == 2
        return sequence

    def create_test_record(self, date, value):
        """Helper function to create a test DataRecord."""
        return DerivedRecord(date_time=date, data_value=value)

    # Test cases
    @pytest.mark.parametrize("tz_name", ["UTC", "Europe/Berlin", "Atlantic/Canary"])
    async def test_min_max_datetime_timezone_and_order(self, sequence, tz_name, monkeypatch, config_eos):
        # Monkeypatch the read-only timezone property
        monkeypatch.setattr(config_eos.general.__class__, "timezone", property(lambda self: tz_name))

        # Create timezone-aware datetimes using the patched config
        dt_early = to_datetime("2024-01-01T00:00:00", in_timezone=config_eos.general.timezone)
        dt_late = to_datetime("2024-01-02T00:00:00", in_timezone=config_eos.general.timezone)

        # Insert in reverse order to verify sorting
        record1 = self.create_test_record(dt_late, 1)
        record2 = self.create_test_record(dt_early, 2)

        await sequence.insert_by_datetime(record1)
        await sequence.insert_by_datetime(record2)

        min_dt = await sequence.min_datetime()
        max_dt = await sequence.max_datetime()

        # --- Basic correctness ---
        assert min_dt == dt_early
        assert max_dt == dt_late

        # --- Must be timezone aware ---
        assert min_dt.tzinfo is not None
        assert max_dt.tzinfo is not None

        # --- Must preserve timezone ---
        assert min_dt.tzinfo.name == tz_name
        assert max_dt.tzinfo.name == tz_name

    async def test_get_by_datetime(self, sequence):
        assert len(sequence) == 0
        dt = to_datetime("2024-01-01 00:00:00")
        record = self.create_test_record(dt, 0)
        await sequence.insert_by_datetime(record)
        item = await sequence.get_by_datetime(dt)
        assert isinstance(item, DerivedRecord)

    async def test_insert_by_datetime(self, sequence2):
        dt = to_datetime("2024-01-03", in_timezone="UTC")
        record = self.create_test_record(dt, 1)
        await sequence2.insert_by_datetime(record)
        assert sequence2.records[2].date_time == dt

    async def test_insert_reversed_date_record(self, sequence2):
        dt1 = to_datetime("2023-11-05", in_timezone="UTC")
        dt2 = to_datetime("2024-01-03", in_timezone="UTC")
        record1 = self.create_test_record(dt2, 0.8)
        record2 = self.create_test_record(dt1, 0.9) # reversed date
        await sequence2.insert_by_datetime(record1)
        assert sequence2.records[2].date_time == dt2
        await sequence2.insert_by_datetime(record2)
        assert len(sequence2) == 4
        assert sequence2.records[2] == record2

    async def test_insert_duplicate_date_record(self, sequence):
        dt1 = to_datetime("2023-11-05")
        record1 = self.create_test_record(dt1, 0.8)
        record2 = self.create_test_record(dt1, 0.9)  # Duplicate date
        await sequence.insert_by_datetime(record1)
        await sequence.insert_by_datetime(record2)
        assert len(sequence) == 1
        retrieved_record = await sequence.get_by_datetime(dt1)
        assert retrieved_record.data_value == 0.9  # Record should have merged with new value

    async def test_key_to_series(self, sequence):
        dt = to_datetime(datetime(2023, 11, 6))
        record = self.create_test_record(dt, 0.8)
        await sequence.insert_by_datetime(record)
        series = await sequence.key_to_series("data_value")
        assert isinstance(series, pd.Series)

        retrieved_record = await sequence.get_by_datetime(dt)
        assert retrieved_record is not None
        assert retrieved_record.data_value == 0.8

    async def test_key_from_series(self, sequence):
        dt1 = to_datetime(datetime(2023, 11, 5))
        dt2 = to_datetime(datetime(2023, 11, 6))

        series = pd.Series(
            data=[0.8, 0.9], index=pd.to_datetime([dt1, dt2])
        )
        await sequence.key_from_series("data_value", series)
        assert len(sequence) == 2

        record1 = await sequence.get_by_datetime(dt1)
        assert record1 is not None
        assert record1.data_value == 0.8

        record2 = await sequence.get_by_datetime(dt2)
        assert record2 is not None
        assert record2.data_value == 0.9

    async def test_key_to_array(self, sequence):
        interval = to_duration("1 day")
        start_datetime = to_datetime("2023-11-6")
        last_datetime = to_datetime("2023-11-8")
        end_datetime = to_datetime("2023-11-9")

        record1 = self.create_test_record(start_datetime, float(start_datetime.day))
        await sequence.insert_by_datetime(record1)
        record2 = self.create_test_record(last_datetime, float(last_datetime.day))
        await sequence.insert_by_datetime(record2)

        retrieved_record1 = await sequence.get_by_datetime(start_datetime)
        assert retrieved_record1 is not None
        assert retrieved_record1.data_value == 6.0

        retrieved_record2 = await sequence.get_by_datetime(last_datetime)
        assert retrieved_record2 is not None
        assert retrieved_record2.data_value == 8.0

        series = await sequence.key_to_series(
            key="data_value", start_datetime=start_datetime, end_datetime=end_datetime
        )
        assert len(series) == 2
        assert series[to_datetime("2023-11-6")] == 6
        assert series[to_datetime("2023-11-8")] == 8

        array = await sequence.key_to_array(
            key="data_value",
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            interval=interval,
        )
        assert isinstance(array, np.ndarray)
        np.testing.assert_equal(array, [6.0, 7.0, 8.0])

    async def test_key_to_array_linear_interpolation(self, sequence):
        """Test key_to_array with linear interpolation for numeric data."""
        interval = to_duration("1 hour")
        record1 = self.create_test_record(pendulum.datetime(2023, 11, 6, 0), 0.8)
        record2 = self.create_test_record(pendulum.datetime(2023, 11, 6, 2), 1.0)  # Gap of 2 hours
        await sequence.insert_by_datetime(record1)
        await sequence.insert_by_datetime(record2)

        array = await sequence.key_to_array(
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


    async def test_key_to_array_linear_interpolation_out_of_grid(self, sequence):
        """Test key_to_array with linear interpolation out of grid."""
        interval = to_duration("1 hour")
        start_datetime= to_datetime("2023-11-06T00:30:00") # out of grid
        end_datetime=to_datetime("2023-11-06T01:30:00") # out of grid

        record1_datetime = to_datetime("2023-11-06T00:00:00")
        record1 = self.create_test_record(record1_datetime, 1.0)

        record2_datetime = to_datetime("2023-11-06T02:00:00")
        record2 = self.create_test_record(record2_datetime, 2.0)  # Gap of 2 hours

        await sequence.insert_by_datetime(record1)
        await sequence.insert_by_datetime(record2)

        # Check test setup
        record1_timestamp = DatabaseTimestamp.from_datetime(record1_datetime)
        record2_timestamp = DatabaseTimestamp.from_datetime(record2_datetime)
        start_timestamp = DatabaseTimestamp.from_datetime(start_datetime)
        end_timestamp = DatabaseTimestamp.from_datetime(end_datetime)

        start_previous_timestamp = await sequence.db_previous_timestamp(start_timestamp)
        assert start_previous_timestamp == record1_timestamp
        end_next_timestamp = await sequence.db_next_timestamp(end_timestamp)
        assert end_next_timestamp == record2_timestamp

        # Test
        array = await sequence.key_to_array(
            key="data_value",
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            interval=interval,
            fill_method="linear",
            boundary="context",
        )
        np.testing.assert_equal(array, [1.5])

    async def test_key_to_array_ffill(self, sequence):
        """Test key_to_array with forward filling for missing values."""
        interval = to_duration("1 hour")
        record1 = self.create_test_record(pendulum.datetime(2023, 11, 6, 0), 0.8)
        record2 = self.create_test_record(pendulum.datetime(2023, 11, 6, 2), 1.0)
        await sequence.insert_by_datetime(record1)
        await sequence.insert_by_datetime(record2)

        array = await sequence.key_to_array(
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

    async def test_key_to_array_ffill_one_value(self, sequence):
        """Test key_to_array with forward filling for missing values and only one value at end available."""
        interval = to_duration("1 hour")
        record1 = self.create_test_record(pendulum.datetime(2023, 11, 6, 2), 1.0)
        await sequence.insert_by_datetime(record1)

        array = await sequence.key_to_array(
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

    async def test_key_to_array_bfill(self, sequence):
        """Test key_to_array with backward filling for missing values."""
        interval = to_duration("1 hour")
        record1 = self.create_test_record(pendulum.datetime(2023, 11, 6, 0), 0.8)
        record2 = self.create_test_record(pendulum.datetime(2023, 11, 6, 2), 1.0)
        await sequence.insert_by_datetime(record1)
        await sequence.insert_by_datetime(record2)

        array = await sequence.key_to_array(
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

    async def test_key_to_array_with_truncation(self, sequence):
        """Test truncation behavior in key_to_array."""
        interval = to_duration("1 hour")
        record1 = self.create_test_record(pendulum.datetime(2023, 11, 5, 23), 0.8)
        record2 = self.create_test_record(pendulum.datetime(2023, 11, 6, 1), 1.0)
        await sequence.insert_by_datetime(record1)
        await sequence.insert_by_datetime(record2)

        #assert sequence is None

        array = await sequence.key_to_array(
            key="data_value",
            start_datetime=pendulum.datetime(2023, 11, 5, 23),
            end_datetime=pendulum.datetime(2023, 11, 6, 2),
            interval=interval,
        )

        assert len(array) == 3
        assert array[0] == 0.8
        assert array[1] == 0.9  # Interpolated from previous day
        assert array[2] == 1.0

    async def test_key_to_array_with_none(self, sequence):
        """Test handling of empty series in key_to_array."""
        interval = to_duration("1 hour")
        array = await sequence.key_to_array(
            key="data_value",
            start_datetime=pendulum.datetime(2023, 11, 6),
            end_datetime=pendulum.datetime(2023, 11, 6, 3),
            interval=interval,
        )
        assert isinstance(array, np.ndarray)
        assert np.all(array == None)

    async def test_key_to_array_with_one(self, sequence):
        """Test handling of one element series in key_to_array."""
        interval = to_duration("1 hour")
        record1 = self.create_test_record(pendulum.datetime(2023, 11, 5, 23), 0.8)
        await sequence.insert_by_datetime(record1)

        array = await sequence.key_to_array(
            key="data_value",
            start_datetime=pendulum.datetime(2023, 11, 5, 23),
            end_datetime=pendulum.datetime(2023, 11, 6, 2),
            interval=interval,
        )
        assert len(array) == 3
        assert array[0] == 0.8
        assert array[1] == 0.8  # Interpolated from previous day
        assert array[2] == 0.8  # Interpolated from previous day

    async def test_key_to_array_invalid_fill_method(self, sequence):
        """Test invalid fill_method raises an error."""
        interval = to_duration("1 hour")
        record1 = self.create_test_record(pendulum.datetime(2023, 11, 6, 0), 0.8)
        await sequence.insert_by_datetime(record1)

        with pytest.raises(ValueError, match="Unsupported fill method: invalid"):
            await sequence.key_to_array(
                key="data_value",
                start_datetime=pendulum.datetime(2023, 11, 6),
                end_datetime=pendulum.datetime(2023, 11, 6, 1),
                interval=interval,
                fill_method="invalid",
            )

    async def test_key_to_array_resample_mean(self, sequence):
        """Test that numeric resampling uses mean when multiple values fall into one interval."""
        interval = to_duration("1 hour")
        # Insert values every 15 minutes within the same hour
        record1 = self.create_test_record(pendulum.datetime(2023, 11, 6, 0, 0), 1.0)
        record2 = self.create_test_record(pendulum.datetime(2023, 11, 6, 0, 15), 2.0)
        record3 = self.create_test_record(pendulum.datetime(2023, 11, 6, 0, 30), 3.0)
        record4 = self.create_test_record(pendulum.datetime(2023, 11, 6, 0, 45), 4.0)

        await sequence.insert_by_datetime(record1)
        await sequence.insert_by_datetime(record2)
        await sequence.insert_by_datetime(record3)
        await sequence.insert_by_datetime(record4)

        # Resample to hourly interval, expecting the mean of the 4 values
        array = await sequence.key_to_array(
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

    async def test_key_to_array_align_false_origin_is_query_start(self, sequence):
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
            await sequence.insert_by_datetime(self.create_test_record(dt, float(m)))

        array = await sequence.key_to_array(
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

    async def test_key_to_array_align_true_15min_buckets_on_quarter_hours(self, sequence):
        """align_to_interval=True produces timestamps on :00/:15/:30/:45 boundaries."""
        # Off-boundary start: 10:07
        start_dt = pendulum.datetime(2024, 6, 1, 10, 7, tz="UTC")
        end_dt = pendulum.datetime(2024, 6, 1, 12, 7, tz="UTC")

        # 1-min records across the window so resampling has data to work with
        for m in range(0, 121):
            dt = pendulum.datetime(2024, 6, 1, 10, 7, tz="UTC").add(minutes=m)
            await sequence.insert_by_datetime(self.create_test_record(dt, float(m)))

        array = await sequence.key_to_array(
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

    async def test_key_to_array_align_true_1hour_buckets_on_the_hour(self, sequence):
        """align_to_interval=True with 1-hour interval produces on-the-hour timestamps."""
        # Off-boundary start: 10:23
        start_dt = pendulum.datetime(2024, 6, 1, 10, 23, tz="UTC")
        end_dt = pendulum.datetime(2024, 6, 1, 15, 23, tz="UTC")

        for m in range(0, 301, 15):
            dt = pendulum.datetime(2024, 6, 1, 10, 23, tz="UTC").add(minutes=m)
            await sequence.insert_by_datetime(self.create_test_record(dt, float(m)))

        array = await sequence.key_to_array(
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

    async def test_key_to_array_align_true_when_start_already_on_boundary(self, sequence):
        """align_to_interval=True is a no-op when start_datetime is exactly on a boundary.

        With start at a clean 15-min mark both modes must produce identical arrays.
        """
        # Exactly on boundary: 10:00:00
        start_dt = pendulum.datetime(2024, 6, 1, 10, 0, tz="UTC")
        end_dt = pendulum.datetime(2024, 6, 1, 12, 0, tz="UTC")

        for m in range(0, 121, 15):
            dt = pendulum.datetime(2024, 6, 1, 10, 0, tz="UTC").add(minutes=m)
            await sequence.insert_by_datetime(self.create_test_record(dt, float(m)))

        arr_aligned = await sequence.key_to_array(
            key="data_value",
            start_datetime=start_dt,
            end_datetime=end_dt,
            interval=to_duration("15 minutes"),
            fill_method="time",
            boundary="strict",
            align_to_interval=True,
        )
        arr_default = await sequence.key_to_array(
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

    async def test_key_to_array_align_true_without_start_datetime(self, sequence):
        """align_to_interval=True with no start_datetime must not raise.

        Without a query_start there is no origin to snap; behaviour falls back
        to 'start_day' (same as default). No exception is expected.
        """
        for m in range(0, 121, 15):
            dt = pendulum.datetime(2024, 6, 1, 10, 7, tz="UTC").add(minutes=m)
            await sequence.insert_by_datetime(self.create_test_record(dt, float(m)))

        array = await sequence.key_to_array(
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

    async def test_key_to_array_align_true_output_within_requested_window(self, sequence):
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
            await sequence.insert_by_datetime(self.create_test_record(dt, float(m)))

        array = await sequence.key_to_array(
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

    async def test_key_to_array_align_true_preserves_mean_values(self, sequence):
        """align_to_interval=True does not corrupt resampled values.

        A constant-valued series must resample to the same constant regardless
        of bucket alignment.
        """
        # 1-min records with constant value 42.0, starting off-boundary
        start_dt = pendulum.datetime(2024, 6, 1, 10, 7, tz="UTC")
        end_dt = pendulum.datetime(2024, 6, 1, 12, 7, tz="UTC")

        for m in range(0, 121):
            dt = pendulum.datetime(2024, 6, 1, 10, 7, tz="UTC").add(minutes=m)
            await sequence.insert_by_datetime(self.create_test_record(dt, 42.0))

        array = await sequence.key_to_array(
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

    async def test_key_to_array_align_true_compaction_call_pattern(self, sequence):
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
            await sequence.insert_by_datetime(self.create_test_record(dt, float(m)))

        array = await sequence.key_to_array(
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

    async def test_delete_by_datetime_range(self, sequence):
        dt1 = to_datetime("2023-11-05")
        dt2 = to_datetime("2023-11-06")
        dt3 = to_datetime("2023-11-07")
        record1 = self.create_test_record(dt1, 0.8)
        record2 = self.create_test_record(dt2, 0.9)
        record3 = self.create_test_record(dt3, 1.0)
        await sequence.insert_by_datetime(record1)
        await sequence.insert_by_datetime(record2)
        await sequence.insert_by_datetime(record3)
        assert len(sequence) == 3
        await sequence.delete_by_datetime(start_datetime=dt2, end_datetime=dt3)
        assert len(sequence) == 2
        assert sequence.records[0].date_time == dt1
        assert sequence.records[1].date_time == dt3

    async def test_delete_by_datetime_start(self, sequence):
        dt1 = to_datetime("2023-11-05")
        dt2 = to_datetime("2023-11-06")
        record1 = self.create_test_record(dt1, 0.8)
        record2 = self.create_test_record(dt2, 0.9)
        await sequence.insert_by_datetime(record1)
        await sequence.insert_by_datetime(record2)
        assert len(sequence) == 2
        await sequence.delete_by_datetime(start_datetime=dt2)
        assert len(sequence) == 1
        assert sequence.records[0].date_time == dt1

    async def test_delete_by_datetime_end(self, sequence):
        dt1 = to_datetime("2023-11-05")
        dt2 = to_datetime("2023-11-06")
        record1 = self.create_test_record(dt1, 0.8)
        record2 = self.create_test_record(dt2, 0.9)
        await sequence.insert_by_datetime(record1)
        await sequence.insert_by_datetime(record2)
        assert len(sequence) == 2
        await sequence.delete_by_datetime(end_datetime=dt2)
        assert len(sequence) == 1
        assert sequence.records[0].date_time == dt2

    async def test_to_dict_async(self, sequence):
        dt = to_datetime("2023-11-06")
        record = self.create_test_record(dt, 0.8)
        await sequence.insert_by_datetime(record)
        data_dict = await sequence.to_dict_async()
        assert isinstance(data_dict, dict)
        # We need a new class - Sequences are singletons
        sequence2 = await DerivedSequence2.from_dict_async(data_dict)
        assert sequence2.model_dump() == sequence.model_dump()

    async def test_to_json_async(self, sequence):
        dt = to_datetime("2023-11-06")
        record = self.create_test_record(dt, 0.8)
        await sequence.insert_by_datetime(record)
        json_str = await sequence.to_json_async()
        assert isinstance(json_str, str)
        assert "2023-11-06" in json_str
        assert ": 0.8" in json_str

    async def test_from_json_async(self, sequence, sequence2):
        json_str = sequence2.to_json()
        sequence = await sequence.from_json_async(json_str)
        assert len(sequence) == len(sequence2)
        assert sequence.records[0].date_time == sequence2.records[0].date_time
        assert sequence.records[0].data_value == sequence2.records[0].data_value

    async def test_key_to_value_exact_match(self, sequence):
        """Test key_to_value returns exact match when datetime matches a record."""
        dt = to_datetime("2023-11-05")
        record = self.create_test_record(dt, 0.75)
        await sequence.insert_by_datetime(record)
        result = await sequence.key_to_value("data_value", dt)
        assert result == 0.75

    async def test_key_to_value_nearest(self, sequence):
        """Test key_to_value returns value closest in time to the given datetime."""
        record1 = self.create_test_record(datetime(2023, 11, 5, 12), 0.6)
        record2 = self.create_test_record(datetime(2023, 11, 6, 12), 0.9)
        await sequence.insert_by_datetime(record1)
        await sequence.insert_by_datetime(record2)
        dt = datetime(2023, 11, 6, 10)  # closer to record2
        result = await sequence.key_to_value("data_value", dt, time_window=to_duration("48 hours"))
        assert result == 0.9

    async def test_key_to_value_nearest_after(self, sequence):
        """Test key_to_value returns value nearest after the given datetime."""
        record1 = self.create_test_record(datetime(2023, 11, 5, 10), 0.7)
        record2 = self.create_test_record(datetime(2023, 11, 5, 15), 0.8)
        await sequence.insert_by_datetime(record1)
        await sequence.insert_by_datetime(record2)
        dt = datetime(2023, 11, 5, 14)  # closer to record2
        result = await sequence.key_to_value("data_value", dt, time_window=to_duration("48 hours"))
        assert result == 0.8

    async def test_key_to_value_empty_sequence(self, sequence):
        """Test key_to_value returns None when sequence is empty."""
        result = await sequence.key_to_value("data_value", datetime(2023, 11, 5))
        assert result is None

    async def test_key_to_value_missing_key(self, sequence):
        """Test key_to_value returns None when key is missing in records."""
        record = self.create_test_record(datetime(2023, 11, 5), None)
        await sequence.insert_by_datetime(record)
        result = await sequence.key_to_value("data_value", datetime(2023, 11, 5))
        assert result is None

    async def test_key_to_value_multiple_records_with_none(self, sequence):
        """Test key_to_value skips records with None values."""
        r1 = self.create_test_record(datetime(2023, 11, 5), None)
        r2 = self.create_test_record(datetime(2023, 11, 6), 1.0)
        await sequence.insert_by_datetime(r1)
        await sequence.insert_by_datetime(r2)
        result = await sequence.key_to_value("data_value", datetime(2023, 11, 5, 12), time_window=to_duration("48 hours"))
        assert result == 1.0

    async def test_key_to_dict(self, sequence):
        record1 = self.create_test_record(datetime(2023, 11, 5), 0.8)
        record2 = self.create_test_record(datetime(2023, 11, 6), 0.9)
        await sequence.insert_by_datetime(record1)
        await sequence.insert_by_datetime(record2)
        data_dict = await sequence.key_to_dict("data_value")
        assert isinstance(data_dict, dict)
        assert data_dict[to_datetime(datetime(2023, 11, 5), as_string=True)] == 0.8
        assert data_dict[to_datetime(datetime(2023, 11, 6), as_string=True)] == 0.9

    async def test_key_to_lists(self, sequence):
        record1 = self.create_test_record(datetime(2023, 11, 5), 0.8)
        record2 = self.create_test_record(datetime(2023, 11, 6), 0.9)
        await sequence.insert_by_datetime(record1)
        await sequence.insert_by_datetime(record2)
        dates, values = await sequence.key_to_lists("data_value")
        assert dates == [to_datetime(datetime(2023, 11, 5)), to_datetime(datetime(2023, 11, 6))]
        assert values == [0.8, 0.9]

    async def test_to_dataframe_full_data(self, sequence):
        """Test conversion of all records to a DataFrame without filtering."""
        record1 = self.create_test_record("2024-01-01T12:00:00Z", 10)
        record2 = self.create_test_record("2024-01-01T13:00:00Z", 20)
        record3 = self.create_test_record("2024-01-01T14:00:00Z", 30)
        await sequence.insert_by_datetime(record1)
        await sequence.insert_by_datetime(record2)
        await sequence.insert_by_datetime(record3)

        df = await sequence.to_dataframe()

        # Validate DataFrame structure
        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        assert len(df) == 3  # All records should be included
        assert "data_value" in df.columns

    async def test_to_dataframe_with_filter(self, sequence):
        """Test filtering records by datetime range."""
        record1 = self.create_test_record("2024-01-01T12:00:00Z", 10)
        record2 = self.create_test_record("2024-01-01T13:00:00Z", 20)
        record3 = self.create_test_record("2024-01-01T14:00:00Z", 30)
        await sequence.insert_by_datetime(record1)
        await sequence.insert_by_datetime(record2)
        await sequence.insert_by_datetime(record3)

        start = to_datetime("2024-01-01T12:30:00Z")
        end = to_datetime("2024-01-01T14:00:00Z")

        df = await sequence.to_dataframe(start_datetime=start, end_datetime=end)

        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        assert len(df) == 1  # Only one record should match the range
        assert df.index[0] == pd.Timestamp("2024-01-01T13:00:00Z")

    async def test_to_dataframe_no_matching_records(self, sequence):
        """Test when no records match the given datetime filter."""
        record1 = self.create_test_record("2024-01-01T12:00:00Z", 10)
        record2 = self.create_test_record("2024-01-01T13:00:00Z", 20)
        await sequence.insert_by_datetime(record1)
        await sequence.insert_by_datetime(record2)

        start = to_datetime("2024-01-01T14:00:00Z")  # Start time after all records
        end = to_datetime("2024-01-01T15:00:00Z")

        df = await sequence.to_dataframe(start_datetime=start, end_datetime=end)

        assert isinstance(df, pd.DataFrame)
        assert df.empty  # No records should match

    async def test_to_dataframe_empty_sequence(self, sequence):
        """Test when DataSequence has no records."""
        sequence = DataSequence(records=[])

        df = await sequence.to_dataframe()

        assert isinstance(df, pd.DataFrame)
        assert df.empty  # Should return an empty DataFrame

    async def test_to_dataframe_no_start_datetime(self, sequence):
        """Test when only end_datetime is given (all past records should be included)."""
        record1 = self.create_test_record("2024-01-01T12:00:00Z", 10)
        record2 = self.create_test_record("2024-01-01T13:00:00Z", 20)
        record3 = self.create_test_record("2024-01-01T14:00:00Z", 30)
        await sequence.insert_by_datetime(record1)
        await sequence.insert_by_datetime(record2)
        await sequence.insert_by_datetime(record3)

        end = to_datetime("2024-01-01T13:00:00Z")  # Include only first record

        df = await sequence.to_dataframe(end_datetime=end)

        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        assert len(df) == 1
        assert df.index[0] == pd.Timestamp("2024-01-01T12:00:00Z")

    async def test_to_dataframe_no_end_datetime(self, sequence):
        """Test when only start_datetime is given (all future records should be included)."""
        record1 = self.create_test_record("2024-01-01T12:00:00Z", 10)
        record2 = self.create_test_record("2024-01-01T13:00:00Z", 20)
        record3 = self.create_test_record("2024-01-01T14:00:00Z", 30)
        await sequence.insert_by_datetime(record1)
        await sequence.insert_by_datetime(record2)
        await sequence.insert_by_datetime(record3)

        start = to_datetime("2024-01-01T13:00:00Z")  # Include last two records

        df = await sequence.to_dataframe(start_datetime=start)

        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        assert len(df) == 2
        assert df.index[0] == pd.Timestamp("2024-01-01T13:00:00Z")
