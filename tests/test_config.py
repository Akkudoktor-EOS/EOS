import tempfile
from pathlib import Path
from typing import Any, Optional, Union
from unittest.mock import patch

import numpy as np
import pytest
from loguru import logger
from pydantic import IPvAnyAddress, ValidationError

from akkudoktoreos.config.config import ConfigEOS, GeneralSettings
from akkudoktoreos.devices.devices import BATTERY_DEFAULT_CHARGE_RATES


def assert_values_equal(actual, expected):
    """Compare values, handling lists and numpy arrays."""
    if isinstance(actual, (list, np.ndarray)) or isinstance(expected, (list, np.ndarray)):
        # Convert both to numpy arrays for comparison
        actual_arr = np.array(actual)
        expected_arr = np.array(expected)
        assert np.array_equal(actual_arr, expected_arr), f"Expected {expected_arr}, but got {actual_arr}"
    else:
        assert actual == expected, f"Expected {expected}, but got {actual}"


# overwrite config_mixin fixture from conftest
@pytest.fixture(autouse=True)
def config_mixin():
    pass


def test_fixture_new_config_file(config_default_dirs):
    """Assure fixture stash_config_file is working."""
    config_default_dir_user, config_default_dir_cwd, _, _ = config_default_dirs

    config_file_user = config_default_dir_user.joinpath(ConfigEOS.CONFIG_FILE_NAME)
    config_file_cwd = config_default_dir_cwd.joinpath(ConfigEOS.CONFIG_FILE_NAME)

    assert not config_file_user.exists()
    assert not config_file_cwd.exists()


def test_config_constants(config_eos):
    """Test config constants are the way expected by the tests."""
    assert config_eos.APP_NAME == "net.akkudoktor.eos"
    assert config_eos.APP_AUTHOR == "akkudoktor"
    assert config_eos.EOS_DIR == "EOS_DIR"
    assert config_eos.ENCODING == "UTF-8"
    assert config_eos.CONFIG_FILE_NAME == "EOS.config.json"


def test_computed_paths(config_eos):
    """Test computed paths for output and cache."""
    # Don't actually try to create the data folder
    with patch("pathlib.Path.mkdir"):
        config_eos.merge_settings_from_dict(
            {
                "general": {
                    "data_folder_path": "/base/data",
                    "data_output_subpath": "extra/output",
                },
                "cache": {
                    "subpath": "somewhere/cache",
                },
            }
        )
    assert config_eos.general.data_folder_path == Path("/base/data")
    assert config_eos.general.data_output_path == Path("/base/data/extra/output")
    assert config_eos.cache.path() == Path("/base/data/somewhere/cache")
    # Check non configurable pathes
    assert config_eos.package_root_path == Path(__file__).parent.parent.resolve().joinpath(
        "src/akkudoktoreos"
    )
    # reset settings so the config_eos fixture can verify the default paths
    config_eos.reset_settings()


def test_config_from_env(monkeypatch, config_eos):
    """Test configuration from env."""
    assert config_eos.server.port == 8503
    assert config_eos.server.eosdash_port is None

    monkeypatch.setenv("EOS_SERVER__PORT", "8553")
    monkeypatch.setenv("EOS_SERVER__EOSDASH_PORT", "8555")

    config_eos.reset_settings()

    assert config_eos.server.port == 8553
    assert config_eos.server.eosdash_port == 8555


def test_config_ipaddress(monkeypatch, config_eos):
    """Test configuration for IP adresses."""
    assert config_eos.server.host == "127.0.0.1"

    monkeypatch.setenv("EOS_SERVER__HOST", "0.0.0.0")
    config_eos.reset_settings()
    assert config_eos.server.host == "0.0.0.0"

    monkeypatch.setenv("EOS_SERVER__HOST", "mail.akkudoktor.net")
    config_eos.reset_settings()
    assert config_eos.server.host == "mail.akkudoktor.net"

    # keep last
    monkeypatch.setenv("EOS_SERVER__HOST", "localhost")
    config_eos.reset_settings()
    assert config_eos.server.host == "localhost"


def test_singleton_behavior(config_eos, config_default_dirs):
    """Test that ConfigEOS behaves as a singleton."""
    initial_cfg_file = config_eos.general.config_file_path
    with patch(
        "akkudoktoreos.config.config.user_config_dir", return_value=str(config_default_dirs[0])
    ):
        instance1 = ConfigEOS()
        instance2 = ConfigEOS()
    assert instance1 is config_eos
    assert instance1 is instance2
    assert instance1.general.config_file_path == initial_cfg_file


def test_config_file_priority(config_default_dirs):
    """Test config file priority.

    Priority is:
        1. environment variable directory
        2. user configuration directory
        3. current working directory
    """

    config_default_dir_user, config_default_dir_cwd, _, _ = config_default_dirs
    config_file_cwd = Path(config_default_dir_cwd) / ConfigEOS.CONFIG_FILE_NAME
    config_file_user = Path(config_default_dir_user) / ConfigEOS.CONFIG_FILE_NAME

    assert not config_file_cwd.exists()
    assert not config_file_user.exists()

    # current working directory (prio 3)
    config_file_cwd.write_text("{}")

    config_eos = ConfigEOS()
    config_eos.update()
    assert config_eos.general.config_file_path == config_file_cwd

    # user configuration directory (prio 2)
    config_file_user.parent.mkdir()
    config_file_user.write_text("{}")

    config_eos.update()
    assert config_eos.general.config_file_path == config_file_user


@patch("akkudoktoreos.config.config.user_config_dir")
def test_get_config_file_path(user_config_dir_patch, config_eos, config_default_dirs, monkeypatch):
    """Test that _get_config_file_path identifies the correct config file."""
    config_default_dir_user, _, _, _ = config_default_dirs
    user_config_dir_patch.return_value = str(config_default_dir_user)

    def cfg_file(dir: Path) -> Path:
        return dir.joinpath(ConfigEOS.CONFIG_FILE_NAME)

    # Config newly created from fixture with fresh user config directory
    assert config_eos._get_config_file_path() == (cfg_file(config_default_dir_user), True)
    cfg_file(config_default_dir_user).unlink()

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = Path(temp_dir)
        monkeypatch.setenv("EOS_DIR", str(temp_dir_path))
        assert config_eos._get_config_file_path() == (cfg_file(temp_dir_path), False)

        monkeypatch.setenv("EOS_CONFIG_DIR", "config")
        assert config_eos._get_config_file_path() == (
            cfg_file(temp_dir_path / "config"),
            False,
        )

        monkeypatch.setenv("EOS_CONFIG_DIR", str(temp_dir_path / "config2"))
        assert config_eos._get_config_file_path() == (
            cfg_file(temp_dir_path / "config2"),
            False,
        )

        monkeypatch.delenv("EOS_DIR")
        monkeypatch.setenv("EOS_CONFIG_DIR", "config3")
        assert config_eos._get_config_file_path() == (cfg_file(config_default_dir_user), False)

        monkeypatch.setenv("EOS_CONFIG_DIR", str(temp_dir_path / "config3"))
        assert config_eos._get_config_file_path() == (
            cfg_file(temp_dir_path / "config3"),
            False,
        )


def test_config_copy(config_eos, monkeypatch):
    """Test if the config is copied to the provided path."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_folder_path = Path(temp_dir)
        temp_config_file_path = temp_folder_path.joinpath(config_eos.CONFIG_FILE_NAME).resolve()
        monkeypatch.setenv(config_eos.EOS_DIR, str(temp_folder_path))
        assert not temp_config_file_path.exists()
        with patch("akkudoktoreos.config.config.user_config_dir", return_value=temp_dir):
            assert config_eos._get_config_file_path() == (temp_config_file_path, False)
            config_eos.update()
        assert temp_config_file_path.exists()


@pytest.mark.parametrize(
    "latitude, longitude, expected_timezone",
    [
        (40.7128, -74.0060, "America/New_York"),  # Valid latitude/longitude
        (None, None, None),  # No location
        (51.5074, -0.1278, "Europe/London"),  # Another valid location
    ],
)
def test_config_common_settings_valid(latitude, longitude, expected_timezone):
    """Test valid settings for GeneralSettings."""
    general_settings = GeneralSettings(
        latitude=latitude,
        longitude=longitude,
    )
    assert general_settings.latitude == latitude
    assert general_settings.longitude == longitude
    assert general_settings.timezone == expected_timezone


@pytest.mark.parametrize(
    "field_name, invalid_value, expected_error",
    [
        ("latitude", -91.0, "Input should be greater than or equal to -90"),
        ("latitude", 91.0, "Input should be less than or equal to 90"),
        ("longitude", -181.0, "Input should be greater than or equal to -180"),
        ("longitude", 181.0, "Input should be less than or equal to 180"),
    ],
)
def test_config_common_settings_invalid(field_name, invalid_value, expected_error):
    """Test invalid settings for PredictionCommonSettings."""
    valid_data = {
        "latitude": 40.7128,
        "longitude": -74.0060,
    }
    assert GeneralSettings(**valid_data) is not None
    valid_data[field_name] = invalid_value

    with pytest.raises(ValidationError, match=expected_error):
        GeneralSettings(**valid_data)


def test_config_common_settings_no_location():
    """Test that timezone is None when latitude and longitude are not provided."""
    settings = GeneralSettings(latitude=None, longitude=None)
    assert settings.timezone is None


def test_config_common_settings_with_location():
    """Test that timezone is correctly computed when latitude and longitude are provided."""
    settings = GeneralSettings(latitude=34.0522, longitude=-118.2437)
    assert settings.timezone == "America/Los_Angeles"


def test_config_common_settings_timezone_none_when_coordinates_missing():
    """Test that timezone is None when latitude or longitude is missing."""
    config_no_latitude = GeneralSettings(latitude=None, longitude=-74.0060)
    config_no_longitude = GeneralSettings(latitude=40.7128, longitude=None)
    config_no_coords = GeneralSettings(latitude=None, longitude=None)

    assert config_no_latitude.timezone is None
    assert config_no_longitude.timezone is None
    assert config_no_coords.timezone is None



# Test partial assignments and possible side effects
@pytest.mark.parametrize(
    "path, value, expected, exception",
    [
        # Correct value assignment
        (
            "general/latitude",
            42.0,
            [("general.latitude", 42.0), ("general.longitude", 13.405)],
            None,
        ),
        # Correct value assignment (trailing /)
        (
            "general/latitude/",
            41,
            [("general.latitude", 41.0), ("general.longitude", 13.405)],
            None,
        ),
        # Correct value assignment (cast)
        (
            "general/latitude",
            "43.0",
            [("general.latitude", 43.0), ("general.longitude", 13.405)],
            None,
        ),
        # Invalid value assignment (constraint)
        (
            "general/latitude",
            91.0,
            [("general.latitude", 52.52), ("general.longitude", 13.405)],
            ValueError,
        ),
        # Invalid value assignment (type)
        (
            "general/latitude",
            "test",
            [("general.latitude", 52.52), ("general.longitude", 13.405)],
            ValueError,
        ),
        # Invalid path
        (
            "general/latitude/test",
            "",
            [("general.latitude", 52.52), ("general.longitude", 13.405)],
            KeyError,
        ),
        # Correct value nested assignment
        (
            "general",
            {"latitude": 22},
            [("general.latitude", 22.0), ("general.longitude", 13.405)],
            None,
        ),
        # Invalid value nested assignment
        (
            "general",
            {"latitude": "test"},
            [("general.latitude", 52.52), ("general.longitude", 13.405)],
            ValueError,
        ),
        # Correct value assignment - preparation for list
        (
            "devices/max_electric_vehicles",
            1,
            [("devices.max_electric_vehicles", 1), ],
            None,
        ),
        # Correct value for list
        (
            "devices/electric_vehicles/0/charge_rates",
            [0.1, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0],
            [
                (
                    "devices.electric_vehicles[0].charge_rates",
                    [0.1, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0],
                )
            ],
            None,
        ),
        # Invalid value for list
        (
            "devices/electric_vehicles/0/charge_rates",
            "invalid",
            [
                (
                    "devices.electric_vehicles[0].charge_rates",
                    BATTERY_DEFAULT_CHARGE_RATES,
                )
            ],
            ValueError,
        ),
        # Invalid index (out of bound)
        (
            "devices/electric_vehicles/0/charge_rates/10",
            0,
            [
                (
                    "devices.electric_vehicles[0].charge_rates",
                    BATTERY_DEFAULT_CHARGE_RATES,
                )
            ],
            KeyError,
        ),
        # Invalid index (no number)
        (
            "devices/electric_vehicles/0/charge_rates/test",
            0,
            [
                (
                    "devices.electric_vehicles[0].charge_rates",
                    BATTERY_DEFAULT_CHARGE_RATES,
                )
            ],
            KeyError,
        ),
        # Unset value (set None)
        (
            "devices/electric_vehicles/0/charge_rates",
            None,
            [
                (
                    "devices.electric_vehicles[0].charge_rates",
                    BATTERY_DEFAULT_CHARGE_RATES,
                )
            ],
            None,
        ),
    ],
)
def test_set_nested_key(path, value, expected, exception, config_eos):
    if not exception:
        config_eos.set_nested_value(path, value)
        for expected_path, expected_value in expected:
            actual_value = eval(f"config_eos.{expected_path}")
            assert_values_equal(actual_value, expected_value)
    else:
        try:
            config_eos.set_nested_value(path, value)
            for expected_path, expected_value in expected:
                actual_value = eval(f"config_eos.{expected_path}")
                assert_values_equal(actual_value, expected_value)
            pytest.fail(
                f"Expected exception {exception} but none was raised. Set '{expected_path}' to '{actual_value}'"
            )
        except Exception as e:
            assert isinstance(e, exception), (
                f"Expected exception {exception}, but got {type(e)}: {e}"
            )


@pytest.mark.parametrize(
    "path, expected_value, exception",
    [
        ("general/latitude", 52.52, None),
        ("general/latitude/", 52.52, None),
        ("general/latitude/test", None, KeyError),
    ],
)
def test_get_nested_key(path, expected_value, exception, config_eos):
    if not exception:
        assert config_eos.get_nested_value(path) == expected_value
    else:
        with pytest.raises(exception):
            config_eos.get_nested_value(path)


def test_merge_settings_from_dict_invalid(config_eos):
    """Test merging invalid data."""
    invalid_settings = {
        "general": {
            "latitude": "invalid_latitude"  # Should be a float
        },
    }

    with pytest.raises(Exception):  # Pydantic ValidationError expected
        config_eos.merge_settings_from_dict(invalid_settings)


def test_merge_settings_partial(config_eos):
    """Test merging only a subset of settings."""

    partial_settings: dict[str, Any] = {
        "general": {
            "latitude": 51.1657  # Only latitude is updated
        },
    }

    config_eos.merge_settings_from_dict(partial_settings)
    assert config_eos.general.latitude == 51.1657
    assert config_eos.general.longitude == 13.405  # Should remain unchanged

    #-----------------

    partial_settings = {
        "weather": {
            "provider": "BrightSky",
        },
    }

    config_eos.merge_settings_from_dict(partial_settings)
    assert config_eos.weather.provider == "BrightSky"

    #-----------------

    partial_settings = {
        "general": {
            "latitude": None,
        },
        "weather": {
            "provider": "ClearOutside",
        },
    }

    config_eos.merge_settings_from_dict(partial_settings)
    assert config_eos.general.latitude is None
    assert config_eos.weather.provider == "ClearOutside"

    # Assure update keeps same values
    config_eos.update()
    assert config_eos.general.latitude is None
    assert config_eos.weather.provider == "ClearOutside"

    #-----------------

    partial_settings = {
        "devices": {
            "max_electric_vehicles": 1,
            "electric_vehicles": [
                {
                    "charge_rates": [0.0, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0],
                }
            ],
        }
    }

    config_eos.merge_settings_from_dict(partial_settings)
    assert config_eos.devices.max_electric_vehicles == 1
    assert len(config_eos.devices.electric_vehicles) == 1
    assert_values_equal(config_eos.devices.electric_vehicles[0].charge_rates, [0.0, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0])

    # Assure re-apply generates the same config
    config_eos.merge_settings_from_dict(partial_settings)
    assert config_eos.devices.max_electric_vehicles == 1
    assert len(config_eos.devices.electric_vehicles) == 1
    assert_values_equal(config_eos.devices.electric_vehicles[0].charge_rates, [0.0, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0])

    # Assure update keeps same values
    config_eos.update()
    assert config_eos.devices.max_electric_vehicles == 1
    assert len(config_eos.devices.electric_vehicles) == 1
    assert_values_equal(config_eos.devices.electric_vehicles[0].charge_rates, [0.0, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0])


def test_merge_settings_empty(config_eos):
    """Test merging an empty dictionary does not change settings."""
    original_latitude = config_eos.general.latitude

    config_eos.merge_settings_from_dict({})  # No changes

    assert config_eos.general.latitude == original_latitude  # Should remain unchanged
