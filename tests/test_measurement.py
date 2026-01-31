import numpy as np
import pytest
from pendulum import datetime, duration

from akkudoktoreos.config.config import SettingsEOS
from akkudoktoreos.measurement.measurement import (
    MeasurementCommonSettings,
    MeasurementDataRecord,
    get_measurement,
)


class TestMeasurementDataRecord:
    """Test suite for the MeasurementDataRecord class.

    Ensuring that both dictionary-like and attribute-style access work correctly for fields and
    configured measurements.
    """

    @pytest.fixture
    def sample_config(self, config_eos):
        """Fixture to configure the measurement keys on the global config."""
        config_eos.measurement.load_emr_keys = ["dish_washer_mr", "temp"]
        config_eos.measurement.pv_production_emr_keys = ["solar_power"]
        return config_eos

    @pytest.fixture
    def record(self, sample_config):
        """Fixture to create a sample MeasurementDataRecord with some measurements set."""
        rec = MeasurementDataRecord(date_time=None)
        rec.configured_data = {"dish_washer_mr": 123.0, "solar_power": 456.0}
        return rec

    def test_record_keys_includes_measurement_keys(self, record):
        """Ensure record_keys includes all configured measurement keys."""
        assert set(record.record_keys()) >= set(record.config.measurement.keys)

    def test_record_keys_writable_includes_measurement_keys(self, record):
        """Ensure record_keys_writable includes all configured measurement keys."""
        assert set(record.record_keys_writable()) >= set(record.config.measurement.keys)

    def test_getitem_existing_field(self, record):
        """Test that __getitem__ returns correct value for existing native field."""
        record.date_time = "2024-01-01T00:00:00+00:00"
        assert record["date_time"] is not None

    def test_getitem_existing_measurement(self, record):
        """Test that __getitem__ retrieves existing measurement values."""
        assert record["dish_washer_mr"] == 123.0
        assert record["solar_power"] == 456.0

    def test_getitem_missing_measurement_returns_none(self, record):
        """Test that __getitem__ returns None for missing but known measurement keys."""
        assert record["temp"] is None

    def test_getitem_raises_keyerror(self, record):
        """Test that __getitem__ raises KeyError for completely unknown keys."""
        with pytest.raises(KeyError):
            _ = record["nonexistent"]

    def test_setitem_field(self, record):
        """Test setting a native field using __setitem__."""
        record["date_time"] = "2025-01-01T12:00:00+00:00"
        assert str(record.date_time).startswith("2025-01-01")

    def test_setitem_measurement(self, record):
        """Test setting a known measurement key using __setitem__."""
        record["temp"] = 25.5
        assert record["temp"] == 25.5

    def test_setitem_invalid_key_raises(self, record):
        """Test that __setitem__ raises KeyError for unknown keys."""
        with pytest.raises(KeyError):
            record["unknown_key"] = 123

    def test_delitem_field(self, record):
        """Test deleting a native field using __delitem__."""
        record["date_time"] = "2025-01-01T12:00:00+00:00"
        del record["date_time"]
        assert record.date_time is None

    def test_delitem_measurement(self, record):
        """Test deleting a known measurement key using __delitem__."""
        del record["solar_power"]
        assert record["solar_power"] is None

    def test_delitem_unknown_raises(self, record):
        """Test that __delitem__ raises KeyError for unknown keys."""
        with pytest.raises(KeyError):
            del record["nonexistent"]

    def test_attribute_get_existing_field(self, record):
        """Test accessing a native field via attribute."""
        record.date_time = "2025-01-01T12:00:00+00:00"
        assert record.date_time is not None

    def test_attribute_get_existing_measurement(self, record):
        """Test accessing an existing measurement via attribute."""
        assert record.dish_washer_mr == 123.0

    def test_attribute_get_missing_measurement(self, record):
        """Test accessing a missing but known measurement returns None."""
        assert record.temp is None

    def test_attribute_get_invalid_raises(self, record):
        """Test accessing an unknown attribute raises AttributeError."""
        with pytest.raises(AttributeError):
            _ = record.nonexistent

    def test_attribute_set_existing_field(self, record):
        """Test setting a native field via attribute."""
        record.date_time = "2025-06-25T12:00:00+00:00"
        assert record.date_time is not None

    def test_attribute_set_existing_measurement(self, record):
        """Test setting a known measurement key via attribute."""
        record.temp = 99.9
        assert record["temp"] == 99.9

    def test_attribute_set_invalid_raises(self, record):
        """Test setting an unknown attribute raises AttributeError."""
        with pytest.raises(AttributeError):
            record.invalid = 123

    def test_delattr_field(self, record):
        """Test deleting a native field via attribute."""
        record.date_time = "2025-06-25T12:00:00+00:00"
        del record.date_time
        assert record.date_time is None

    def test_delattr_measurement(self, record):
        """Test deleting a known measurement key via attribute."""
        record.temp = 88.0
        del record.temp
        assert record.temp is None

    def test_delattr_ignored_missing_measurement_key(self, record):
        """Test deleting a known measurement key that was never set is a no-op."""
        del record.temp
        assert record.temp is None

    def test_len_and_iter(self, record):
        """Test that __len__ and __iter__ behave as expected."""
        keys = list(iter(record))
        assert set(record.record_keys_writable()) == set(keys)
        assert len(record) == len(keys)

    def test_in_operator_includes_measurements(self, record):
        """Test that 'in' operator includes measurement keys."""
        assert "dish_washer_mr" in record
        assert "temp" in record  # known key, even if not yet set
        assert "nonexistent" not in record

    def test_hasattr_behavior(self, record):
        """Test that hasattr returns True for fields and known measurements."""
        assert hasattr(record, "date_time")
        assert hasattr(record, "dish_washer_mr")
        assert hasattr(record, "temp")  # allowed, even if not yet set
        assert not hasattr(record, "nonexistent")

    def test_model_validate_roundtrip(self, record):
        """Test that MeasurementDataRecord can be serialized and revalidated."""
        dumped = record.model_dump()
        restored = MeasurementDataRecord.model_validate(dumped)
        assert restored.dish_washer_mr == 123.0
        assert restored.solar_power == 456.0
        assert restored.temp is None  # not set

    def test_copy_preserves_measurements(self, record):
        """Test that copying preserves measurement values."""
        record.temp = 22.2
        copied = record.model_copy()
        assert copied.dish_washer_mr == 123.0
        assert copied.temp == 22.2
        assert copied is not record

    def test_equality_includes_measurements(self, record):
        """Test that equality includes the `measurements` content."""
        other = record.model_copy()
        assert record == other

    def test_inequality_differs_with_measurements(self, record):
        """Test that records with different measurements are not equal."""
        other = record.model_copy(deep=True)
        # Modify one measurement value in the copy
        other["dish_washer_mr"] = 999.9
        assert record != other

    def test_in_operator_for_measurements_and_fields(self, record):
        """Ensure 'in' works for both fields and configured measurement keys."""
        assert "dish_washer_mr" in record
        assert "solar_power" in record
        assert "date_time" in record  # standard field
        assert "temp" in record       # allowed but not yet set
        assert "unknown" not in record

    def test_hasattr_equivalence_to_getattr(self, record):
        """hasattr should return True for all valid keys/measurements."""
        assert hasattr(record, "dish_washer_mr")
        assert hasattr(record, "temp")
        assert hasattr(record, "date_time")
        assert not hasattr(record, "nonexistent")

    def test_dir_includes_measurement_keys(self, record):
        """`dir(record)` should include measurement keys for introspection.
         It shall not include the internal 'measurements' attribute.
        """
        keys = dir(record)
        assert "measurements" not in keys
        for key in record.config.measurement.keys:
            assert key in keys


class TestMeasurement:
    """Test suite for the Measuremen class."""

    @pytest.fixture
    def measurement_eos(self, config_eos):
        """Fixture to create a Measurement instance."""
        # Load meter readings are in kWh
        config_eos.measurement.load_emr_keys = ["load0_mr", "load1_mr", "load2_mr", "load3_mr"]
        measurement = get_measurement()
        record0 = MeasurementDataRecord(
            date_time=datetime(2023, 1, 1, hour=0),
            load0_mr=100,
            load1_mr=200,
        )
        assert record0.load0_mr == 100
        assert record0.load1_mr == 200
        measurement.records = [
            MeasurementDataRecord(
                date_time=datetime(2023, 1, 1, hour=0),
                load0_mr=100,
                load1_mr=200,
            ),
            MeasurementDataRecord(
                date_time=datetime(2023, 1, 1, hour=1),
                load0_mr=150,
                load1_mr=250,
            ),
            MeasurementDataRecord(
                date_time=datetime(2023, 1, 1, hour=2),
                load0_mr=200,
                load1_mr=300,
            ),
            MeasurementDataRecord(
                date_time=datetime(2023, 1, 1, hour=3),
                load0_mr=250,
                load1_mr=350,
            ),
            MeasurementDataRecord(
                date_time=datetime(2023, 1, 1, hour=4),
                load0_mr=300,
                load1_mr=400,
            ),
            MeasurementDataRecord(
                date_time=datetime(2023, 1, 1, hour=5),
                load0_mr=350,
                load1_mr=450,
            ),
        ]
        return measurement

    def test_interval_count(self, measurement_eos):
        """Test interval count calculation."""
        start = datetime(2023, 1, 1, 0)
        end = datetime(2023, 1, 1, 3)
        interval = duration(hours=1)

        assert measurement_eos._interval_count(start, end, interval) == 3

    def test_interval_count_invalid_end_before_start(self, measurement_eos):
        """Test interval count raises ValueError when end_datetime is before start_datetime."""
        start = datetime(2023, 1, 1, 3)
        end = datetime(2023, 1, 1, 0)
        interval = duration(hours=1)

        with pytest.raises(ValueError, match="end_datetime must be after start_datetime"):
            measurement_eos._interval_count(start, end, interval)

    def test_interval_count_invalid_non_positive_interval(self, measurement_eos):
        """Test interval count raises ValueError when interval is non-positive."""
        start = datetime(2023, 1, 1, 0)
        end = datetime(2023, 1, 1, 3)

        with pytest.raises(ValueError, match="interval must be positive"):
            measurement_eos._interval_count(start, end, duration(hours=0))

    def test_energy_from_meter_readings_valid_input(self, measurement_eos):
        """Test _energy_from_meter_readings with valid inputs and proper alignment of load data."""
        key = "load0_mr"
        start_datetime = datetime(2023, 1, 1, 0)
        end_datetime = datetime(2023, 1, 1, 5)
        interval = duration(hours=1)

        load_array = measurement_eos._energy_from_meter_readings(
            key, start_datetime, end_datetime, interval
        )

        expected_load_array = np.array([50, 50, 50, 50, 50])  # Differences between consecutive readings
        np.testing.assert_array_equal(load_array, expected_load_array)

    def test_energy_from_meter_readings_empty_array(self, measurement_eos):
        """Test _energy_from_meter_readings with no data (empty array)."""
        key = "load0_mr"
        start_datetime = datetime(2023, 1, 1, 0)
        end_datetime = datetime(2023, 1, 1, 5)
        interval = duration(hours=1)

        # Use empyt records array
        measurement_eos.records = []

        load_array = measurement_eos._energy_from_meter_readings(
            key, start_datetime, end_datetime, interval
        )

        # Expected: an array of zeros with one less than the number of intervals
        expected_size = (
            measurement_eos._interval_count(start_datetime, end_datetime + interval, interval) - 1
        )
        expected_load_array = np.zeros(expected_size)
        np.testing.assert_array_equal(load_array, expected_load_array)

    def test_energy_from_meter_readings_misaligned_array(self, measurement_eos):
        """Test _energy_from_meter_readings with misaligned array size."""
        key = "load1_mr"
        start_datetime = measurement_eos.min_datetime
        end_datetime = measurement_eos.max_datetime
        interval = duration(hours=1)

        # Use misaligned array, latest interval set to 2 hours (instead of 1 hour)
        measurement_eos.records[-1].date_time = datetime(2023, 1, 1, 6)

        load_array = measurement_eos._energy_from_meter_readings(
            key, start_datetime, end_datetime, interval
        )

        expected_load_array = np.array([50, 50, 50, 50, 25])  # Differences between consecutive readings
        np.testing.assert_array_equal(load_array, expected_load_array)

    def test_energy_from_meter_readings_partial_data(self, measurement_eos, caplog):
        """Test _energy_from_meter_readings with partial data (misaligned but empty array)."""
        key = "load2_mr"
        start_datetime = datetime(2023, 1, 1, 0)
        end_datetime = datetime(2023, 1, 1, 5)
        interval = duration(hours=1)

        with caplog.at_level("DEBUG"):
            load_array = measurement_eos._energy_from_meter_readings(
                key, start_datetime, end_datetime, interval
            )

        expected_size = (
            measurement_eos._interval_count(start_datetime, end_datetime + interval, interval) - 1
        )
        expected_load_array = np.zeros(expected_size)
        np.testing.assert_array_equal(load_array, expected_load_array)

    def test_energy_from_meter_readings_negative_interval(self, measurement_eos):
        """Test _energy_from_meter_readings with a negative interval."""
        key = "load3_mr"
        start_datetime = datetime(2023, 1, 1, 0)
        end_datetime = datetime(2023, 1, 1, 5)
        interval = duration(hours=-1)

        with pytest.raises(ValueError, match="interval must be positive"):
            measurement_eos._energy_from_meter_readings(key, start_datetime, end_datetime, interval)

    def test_load_total_kwh(self, measurement_eos):
        """Test total load calculation."""
        start = datetime(2023, 1, 1, 0)
        end = datetime(2023, 1, 1, 2)
        interval = duration(hours=1)

        result = measurement_eos.load_total_kwh(start_datetime=start, end_datetime=end, interval=interval)

        # Expected total load per interval
        expected = np.array([100, 100])  # Differences between consecutive meter readings
        np.testing.assert_array_equal(result, expected)

    def test_load_total_kwh_no_data(self, measurement_eos):
        """Test total load calculation with no data."""
        measurement_eos.records = []
        start = datetime(2023, 1, 1, 0)
        end = datetime(2023, 1, 1, 3)
        interval = duration(hours=1)

        result = measurement_eos.load_total_kwh(start_datetime=start, end_datetime=end, interval=interval)
        expected = np.zeros(3)  # No data, so all intervals are zero
        np.testing.assert_array_equal(result, expected)

    def test_load_total_kwh_partial_intervals(self, measurement_eos):
        """Test total load calculation with partial intervals."""
        start = datetime(2023, 1, 1, 0, 30)  # Start in the middle of an interval
        end = datetime(2023, 1, 1, 1, 30)  # End in the middle of another interval
        interval = duration(hours=1)

        result = measurement_eos.load_total_kwh(start_datetime=start, end_datetime=end, interval=interval)
        expected = np.array([100])  # Only one complete interval covered
        np.testing.assert_array_equal(result, expected)
