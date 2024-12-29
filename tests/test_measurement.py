import numpy as np
import pytest
from pendulum import datetime, duration

from akkudoktoreos.config.config import SettingsEOS
from akkudoktoreos.measurement.measurement import MeasurementDataRecord, get_measurement


@pytest.fixture
def measurement_eos():
    """Fixture to create a Measurement instance."""
    measurement = get_measurement()
    measurement.records = [
        MeasurementDataRecord(
            date_time=datetime(2023, 1, 1, hour=0),
            measurement_load0_mr=100,
            measurement_load1_mr=200,
        ),
        MeasurementDataRecord(
            date_time=datetime(2023, 1, 1, hour=1),
            measurement_load0_mr=150,
            measurement_load1_mr=250,
        ),
        MeasurementDataRecord(
            date_time=datetime(2023, 1, 1, hour=2),
            measurement_load0_mr=200,
            measurement_load1_mr=300,
        ),
        MeasurementDataRecord(
            date_time=datetime(2023, 1, 1, hour=3),
            measurement_load0_mr=250,
            measurement_load1_mr=350,
        ),
        MeasurementDataRecord(
            date_time=datetime(2023, 1, 1, hour=4),
            measurement_load0_mr=300,
            measurement_load1_mr=400,
        ),
        MeasurementDataRecord(
            date_time=datetime(2023, 1, 1, hour=5),
            measurement_load0_mr=350,
            measurement_load1_mr=450,
        ),
    ]
    return measurement


def test_interval_count(measurement_eos):
    """Test interval count calculation."""
    start = datetime(2023, 1, 1, 0)
    end = datetime(2023, 1, 1, 3)
    interval = duration(hours=1)

    assert measurement_eos._interval_count(start, end, interval) == 3


def test_interval_count_invalid_end_before_start(measurement_eos):
    """Test interval count raises ValueError when end_datetime is before start_datetime."""
    start = datetime(2023, 1, 1, 3)
    end = datetime(2023, 1, 1, 0)
    interval = duration(hours=1)

    with pytest.raises(ValueError, match="end_datetime must be after start_datetime"):
        measurement_eos._interval_count(start, end, interval)


def test_interval_count_invalid_non_positive_interval(measurement_eos):
    """Test interval count raises ValueError when interval is non-positive."""
    start = datetime(2023, 1, 1, 0)
    end = datetime(2023, 1, 1, 3)

    with pytest.raises(ValueError, match="interval must be positive"):
        measurement_eos._interval_count(start, end, duration(hours=0))


def test_energy_from_meter_readings_valid_input(measurement_eos):
    """Test _energy_from_meter_readings with valid inputs and proper alignment of load data."""
    key = "measurement_load0_mr"
    start_datetime = datetime(2023, 1, 1, 0)
    end_datetime = datetime(2023, 1, 1, 5)
    interval = duration(hours=1)

    load_array = measurement_eos._energy_from_meter_readings(
        key, start_datetime, end_datetime, interval
    )

    expected_load_array = np.array([50, 50, 50, 50, 50])  # Differences between consecutive readings
    np.testing.assert_array_equal(load_array, expected_load_array)


def test_energy_from_meter_readings_empty_array(measurement_eos):
    """Test _energy_from_meter_readings with no data (empty array)."""
    key = "measurement_load0_mr"
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


def test_energy_from_meter_readings_misaligned_array(measurement_eos):
    """Test _energy_from_meter_readings with misaligned array size."""
    key = "measurement_load1_mr"
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


def test_energy_from_meter_readings_partial_data(measurement_eos, caplog):
    """Test _energy_from_meter_readings with partial data (misaligned but empty array)."""
    key = "measurement_load2_mr"
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


def test_energy_from_meter_readings_negative_interval(measurement_eos):
    """Test _energy_from_meter_readings with a negative interval."""
    key = "measurement_load3_mr"
    start_datetime = datetime(2023, 1, 1, 0)
    end_datetime = datetime(2023, 1, 1, 5)
    interval = duration(hours=-1)

    with pytest.raises(ValueError, match="interval must be positive"):
        measurement_eos._energy_from_meter_readings(key, start_datetime, end_datetime, interval)


def test_load_total(measurement_eos):
    """Test total load calculation."""
    start = datetime(2023, 1, 1, 0)
    end = datetime(2023, 1, 1, 2)
    interval = duration(hours=1)

    result = measurement_eos.load_total(start_datetime=start, end_datetime=end, interval=interval)

    # Expected total load per interval
    expected = np.array([100, 100])  # Differences between consecutive meter readings
    np.testing.assert_array_equal(result, expected)


def test_load_total_no_data(measurement_eos):
    """Test total load calculation with no data."""
    measurement_eos.records = []
    start = datetime(2023, 1, 1, 0)
    end = datetime(2023, 1, 1, 3)
    interval = duration(hours=1)

    result = measurement_eos.load_total(start_datetime=start, end_datetime=end, interval=interval)
    expected = np.zeros(3)  # No data, so all intervals are zero
    np.testing.assert_array_equal(result, expected)


def test_name_to_key(measurement_eos):
    """Test name_to_key functionality."""
    settings = SettingsEOS(
        measurement_load0_name="Household",
        measurement_load1_name="Heat Pump",
    )
    measurement_eos.config.merge_settings(settings)

    assert measurement_eos.name_to_key("Household", "measurement_load") == "measurement_load0_mr"
    assert measurement_eos.name_to_key("Heat Pump", "measurement_load") == "measurement_load1_mr"
    assert measurement_eos.name_to_key("Unknown", "measurement_load") is None


def test_name_to_key_invalid_topic(measurement_eos):
    """Test name_to_key with an invalid topic."""
    settings = SettingsEOS(
        measurement_load0_name="Household",
        measurement_load1_name="Heat Pump",
    )
    measurement_eos.config.merge_settings(settings)

    assert measurement_eos.name_to_key("Household", "invalid_topic") is None


def test_load_total_partial_intervals(measurement_eos):
    """Test total load calculation with partial intervals."""
    start = datetime(2023, 1, 1, 0, 30)  # Start in the middle of an interval
    end = datetime(2023, 1, 1, 1, 30)  # End in the middle of another interval
    interval = duration(hours=1)

    result = measurement_eos.load_total(start_datetime=start, end_datetime=end, interval=interval)
    expected = np.array([100])  # Only one complete interval covered
    np.testing.assert_array_equal(result, expected)
