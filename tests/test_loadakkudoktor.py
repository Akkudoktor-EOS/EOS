from unittest.mock import patch

import numpy as np
import pendulum
import pytest

from akkudoktoreos.config.config import get_config
from akkudoktoreos.core.ems import get_ems
from akkudoktoreos.prediction.loadakkudoktor import (
    LoadAkkudoktor,
    LoadAkkudoktorCommonSettings,
)

config_eos = get_config()
ems_eos = get_ems()


@pytest.fixture
def load_provider(monkeypatch):
    """Fixture to create a LoadAkkudoktor instance."""
    settings = {
        "load_provider": "LoadAkkudoktor",
        "load_name": "Akkudoktor Profile",
        "loadakkudoktor_year_energy": "1000",
    }
    config_eos.merge_settings_from_dict(settings)
    return LoadAkkudoktor()


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
    ems_eos.set_start_datetime(pendulum.datetime(2024, 1, 1))

    # Assure there are no prediction records
    load_provider.clear()
    assert len(load_provider) == 0

    # Execute the method
    load_provider._update_data()

    # Validate that update_value is called
    assert len(load_provider) > 0
