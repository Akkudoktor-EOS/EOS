from unittest.mock import patch

import numpy as np
import pendulum
import pytest

from akkudoktoreos.core.ems import get_ems
from akkudoktoreos.measurement.measurement import MeasurementDataRecord, get_measurement
from akkudoktoreos.prediction.loadakkudoktor import (
    LoadAkkudoktor,
    LoadAkkudoktorCommonSettings,
)
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime, to_duration


@pytest.fixture
def load_provider(config_eos):
    """Fixture to initialise the LoadAkkudoktor instance."""
    settings = {
        "load_provider": "LoadAkkudoktor",
        "load_name": "Akkudoktor Profile",
        "loadakkudoktor_year_energy": "1000",
    }
    config_eos.merge_settings_from_dict(settings)
    return LoadAkkudoktor()


@pytest.fixture
def measurement_eos():
    """Fixture to initialise the Measurement instance."""
    measurement = get_measurement()
    load0_mr = 500
    load1_mr = 500
    dt = to_datetime("2024-01-01T00:00:00")
    interval = to_duration("1 hour")
    for i in range(25):
        measurement.records.append(
            MeasurementDataRecord(
                date_time=dt,
                measurement_load0_mr=load0_mr,
                measurement_load1_mr=load1_mr,
            )
        )
        dt += interval
        load0_mr += 50
        load1_mr += 50
    assert compare_datetimes(measurement.min_datetime, to_datetime("2024-01-01T00:00:00")).equal
    assert compare_datetimes(measurement.max_datetime, to_datetime("2024-01-02T00:00:00")).equal
    return measurement


@pytest.fixture
def mock_load_profiles_file(tmp_path):
    """Fixture to create a mock load profiles file."""
    load_profiles_path = tmp_path / "load_profiles.npz"
    np.savez(
        load_profiles_path,
        yearly_profiles=np.random.rand(365, 24),  # Random load profiles
        yearly_profiles_std=np.random.rand(365, 24),  # Random standard deviation
    )
    return load_profiles_path


def test_loadakkudoktor_settings_validator():
    """Test the field validator for `loadakkudoktor_year_energy`."""
    settings = LoadAkkudoktorCommonSettings(loadakkudoktor_year_energy=1234)
    assert isinstance(settings.loadakkudoktor_year_energy, float)
    assert settings.loadakkudoktor_year_energy == 1234.0

    settings = LoadAkkudoktorCommonSettings(loadakkudoktor_year_energy=1234.56)
    assert isinstance(settings.loadakkudoktor_year_energy, float)
    assert settings.loadakkudoktor_year_energy == 1234.56


def test_loadakkudoktor_provider_id(load_provider):
    """Test the `provider_id` class method."""
    assert load_provider.provider_id() == "LoadAkkudoktor"


@patch("akkudoktoreos.prediction.loadakkudoktor.Path")
@patch("akkudoktoreos.prediction.loadakkudoktor.np.load")
def test_load_data_from_mock(mock_np_load, mock_path, mock_load_profiles_file, load_provider):
    """Test the `load_data` method."""
    # Mock path behavior to return the test file
    mock_path.return_value.parent.parent.joinpath.return_value = mock_load_profiles_file

    # Mock numpy load to return data similar to what would be in the file
    mock_np_load.return_value = {
        "yearly_profiles": np.ones((365, 24)),
        "yearly_profiles_std": np.zeros((365, 24)),
    }

    # Test data loading
    data_year_energy = load_provider.load_data()
    assert data_year_energy is not None
    assert data_year_energy.shape == (365, 2, 24)


def test_load_data_from_file(load_provider):
    """Test `load_data` loads data from the profiles file."""
    data_year_energy = load_provider.load_data()
    assert data_year_energy is not None


@patch("akkudoktoreos.prediction.loadakkudoktor.LoadAkkudoktor.load_data")
def test_update_data(mock_load_data, load_provider):
    """Test the `_update` method."""
    mock_load_data.return_value = np.random.rand(365, 2, 24)

    # Mock methods for updating values
    ems_eos = get_ems()
    ems_eos.set_start_datetime(pendulum.datetime(2024, 1, 1))

    # Assure there are no prediction records
    load_provider.clear()
    assert len(load_provider) == 0

    # Execute the method
    load_provider._update_data()

    # Validate that update_value is called
    assert len(load_provider) > 0


def test_calculate_adjustment(load_provider, measurement_eos):
    """Test `_calculate_adjustment` for various scenarios."""
    data_year_energy = np.random.rand(365, 2, 24)

    # Call the method and validate results
    weekday_adjust, weekend_adjust = load_provider._calculate_adjustment(data_year_energy)
    assert weekday_adjust.shape == (24,)
    assert weekend_adjust.shape == (24,)

    data_year_energy = np.zeros((365, 2, 24))
    weekday_adjust, weekend_adjust = load_provider._calculate_adjustment(data_year_energy)

    assert weekday_adjust.shape == (24,)
    expected = np.array(
        [
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
        ]
    )
    np.testing.assert_array_equal(weekday_adjust, expected)

    assert weekend_adjust.shape == (24,)
    expected = np.array(
        [
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ]
    )
    np.testing.assert_array_equal(weekend_adjust, expected)


def test_load_provider_adjustments_with_mock_data(load_provider):
    """Test full integration of adjustments with mock data."""
    with patch(
        "akkudoktoreos.prediction.loadakkudoktor.LoadAkkudoktor._calculate_adjustment"
    ) as mock_adjust:
        mock_adjust.return_value = (np.zeros(24), np.zeros(24))

        # Test execution
        load_provider._update_data()
        assert mock_adjust.called
