import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import numpy as np
import pytest
from pydantic import ValidationError

from akkudoktoreos.config.config import ConfigEOS, GeneralSettings
from akkudoktoreos.devices.devices import DevicesCommonSettings


def assert_values_equal(actual, expected):
    """Compare values, handling lists and numpy arrays."""
    if isinstance(actual, (list, np.ndarray)) or isinstance(expected, (list, np.ndarray)):
        # Convert both to numpy arrays for comparison
        actual_arr = np.array(actual)
        expected_arr = np.array(expected)
        assert np.array_equal(actual_arr, expected_arr), f"Expected {expected_arr}, but got {actual_arr}"
    else:
        assert actual == expected, f"Expected {expected}, but got {actual}"


# ------------------------------------
# Runtime settings priority (issue #1303)
# ------------------------------------


@pytest.fixture
def config_eos_file(config_eos_factory) -> ConfigEOS:
    """ConfigEOS with the EOS configuration file as an active settings source."""
    return config_eos_factory(
        init={
            "with_init_settings": True,
            "with_env_settings": True,
            "with_dotenv_settings": False,
            "with_file_settings": True,
            "with_file_secret_settings": False,
        }
    )


def write_config_file(config_eos: ConfigEOS, settings: dict[str, Any]) -> None:
    """Write settings to the EOS configuration file and load them."""
    settings = {"general": {"version": config_eos.general.version}, **settings}
    config_file_path = config_eos.general.config_file_path
    assert config_file_path is not None
    config_file_path.write_text(json.dumps(settings), encoding="utf-8")
    config_eos.reset_settings()


class TestConfigEOS:
    """Configuration loading and runtime overrides without the ConfigMixin patch."""

    @pytest.fixture(autouse=True)
    def config_mixin(self):
        """Exercise real configuration lookup within this class."""
        pass

    def test_fixture_new_config_file(self, config_default_dirs):
        """Assure fixture stash_config_file is working."""
        config_default_dir_user, config_default_dir_cwd, _, _ = config_default_dirs

        config_file_user = config_default_dir_user.joinpath(ConfigEOS.CONFIG_FILE_NAME)
        config_file_cwd = config_default_dir_cwd.joinpath(ConfigEOS.CONFIG_FILE_NAME)

        assert not config_file_user.exists()
        assert not config_file_cwd.exists()

    def test_config_constants(self, config_eos):
        """Test config constants are the way expected by the tests."""
        assert config_eos.APP_NAME == "net.akkudoktor.eos"
        assert config_eos.APP_AUTHOR == "akkudoktor"
        assert config_eos.EOS_DIR == "EOS_DIR"
        assert config_eos.ENCODING == "UTF-8"
        assert config_eos.CONFIG_FILE_NAME == "EOS.config.json"

    def test_computed_paths(self, config_eos, monkeypatch):
        """Test computed paths for output and cache."""

        # Ensure we do not have the environment patched by conftest.py
        monkeypatch.delenv("EOS_GENERAL__DATA_FOLDER_PATH", raising=False)
        monkeypatch.delenv("EOS_GENERAL__DATA_CACHE_SUBPATH", raising=False)
        monkeypatch.delenv("EOS_GENERAL__DATA_OUTPUT_SUBPATH", raising=False)

        # Don't actually try to create the data folder
        with patch("pathlib.Path.mkdir"), \
             patch("pathlib.Path.is_dir", return_value=True), \
             patch("pathlib.Path.exists", return_value=True):
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

    def test_config_from_env(self, monkeypatch, config_eos):
        """Test configuration from env."""
        assert config_eos.server.port == 8503
        assert config_eos.server.eosdash_port == 8504

        monkeypatch.setenv("EOS_SERVER__PORT", "8553")
        monkeypatch.setenv("EOS_SERVER__EOSDASH_PORT", "8555")

        config_eos.reset_settings()

        assert config_eos.server.port == 8553
        assert config_eos.server.eosdash_port == 8555

    def test_config_ipaddress(self, monkeypatch, config_eos):
        """Validate configured IPs and hostnames without external DNS."""
        resolver = Mock(side_effect={
            "mail.akkudoktor.net": "192.0.2.1",
            "localhost": "127.0.0.1",
        }.__getitem__)
        monkeypatch.setattr("akkudoktoreos.server.server.socket.gethostbyname", resolver)
        assert config_eos.server.host == "127.0.0.1"

        monkeypatch.setenv("EOS_SERVER__HOST", "0.0.0.0")
        config_eos.reset_settings()
        assert config_eos.server.host == "0.0.0.0"
        resolver.assert_not_called()

        monkeypatch.setenv("EOS_SERVER__HOST", "mail.akkudoktor.net")
        config_eos.reset_settings()
        assert config_eos.server.host == "mail.akkudoktor.net"
        resolver.assert_called_with("mail.akkudoktor.net")

        # keep last
        monkeypatch.setenv("EOS_SERVER__HOST", "localhost")
        config_eos.reset_settings()
        assert config_eos.server.host == "localhost"
        resolver.assert_called_with("localhost")

    def test_singleton_behavior(self, config_eos, config_default_dirs, monkeypatch):
        """Test that ConfigEOS behaves as a singleton."""
        config_eos.reset_instance()

        monkeypatch.setenv("EOS_CONFIG_DIR", str(config_default_dirs[0]))

        instance1 = ConfigEOS()
        instance2 = ConfigEOS()

        assert instance1 is not config_eos
        assert instance1 is instance2
        assert instance1._config_file_path == instance2._config_file_path

    def test_config_file_priority(self, config_default_dirs):
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
    def test_get_config_file_path(self, user_config_dir_patch, config_eos, config_default_dirs, monkeypatch):
        """Test that _get_config_file_path identifies the correct config file."""
        config_default_dir_user, _, _, _ = config_default_dirs
        user_config_dir_patch.return_value = str(config_default_dir_user)

        def cfg_file(dir: Path) -> Path:
            # Match production directory normalization, including macOS /var aliases.
            return dir.resolve().joinpath(ConfigEOS.CONFIG_FILE_NAME)

        # Config newly created from fixture with fresh user config directory
        assert config_eos._get_config_file_path() == (cfg_file(config_default_dir_user), True)
        cfg_file(config_default_dir_user).unlink()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            monkeypatch.setenv("EOS_DIR", str(temp_dir_path))
            monkeypatch.delenv("EOS_CONFIG_DIR", raising=False)
            assert config_eos._get_config_file_path() == (cfg_file(temp_dir_path), False)

            monkeypatch.setenv("EOS_CONFIG_DIR", "config")
            config_dir = temp_dir_path / "config"
            config_dir.mkdir(exist_ok=True)
            assert config_eos._get_config_file_path() == (
                cfg_file(config_dir),
                False,
            )

            monkeypatch.setenv("EOS_CONFIG_DIR", str(temp_dir_path / "config2"))
            config_dir = temp_dir_path / "config2"
            config_dir.mkdir(exist_ok=True)
            assert config_eos._get_config_file_path() == (
                cfg_file(config_dir),
                False,
            )

            monkeypatch.delenv("EOS_DIR")
            monkeypatch.setenv("EOS_CONFIG_DIR", "config3")
            assert config_eos._get_config_file_path() == (cfg_file(config_default_dir_user), False)

            monkeypatch.setenv("EOS_CONFIG_DIR", str(temp_dir_path / "config3"))
            config_dir = temp_dir_path / "config3"
            config_dir.mkdir(exist_ok=True)
            assert config_eos._get_config_file_path() == (
                cfg_file(config_dir),
                False,
            )

    def test_config_copy(self, config_eos, monkeypatch):
        """Test if the config is copied to the provided path."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_folder_path = Path(temp_dir)
            temp_config_file_path = temp_folder_path.joinpath(config_eos.CONFIG_FILE_NAME).resolve()
            monkeypatch.setenv("EOS_CONFIG_DIR", str(temp_folder_path))
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
    def test_config_common_settings_valid(self, latitude, longitude, expected_timezone):
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
    def test_config_common_settings_invalid(self, field_name, invalid_value, expected_error):
        """Test invalid settings for PredictionCommonSettings."""
        valid_data = {
            "latitude": 40.7128,
            "longitude": -74.0060,
        }
        assert GeneralSettings.model_validate(valid_data) is not None
        valid_data[field_name] = invalid_value

        with pytest.raises(ValidationError, match=expected_error):
            GeneralSettings.model_validate(valid_data)

    def test_config_common_settings_no_location(self):
        """Test that timezone is None when latitude and longitude are not provided."""
        settings = GeneralSettings(latitude=None, longitude=None)
        assert settings.timezone is None

    def test_config_common_settings_with_location(self):
        """Test that timezone is correctly computed when latitude and longitude are provided."""
        settings = GeneralSettings(latitude=34.0522, longitude=-118.2437)
        assert settings.timezone == "America/Los_Angeles"

    def test_config_common_settings_timezone_none_when_coordinates_missing(self):
        """Test that timezone is None when latitude or longitude is missing."""
        config_no_latitude = GeneralSettings(latitude=None, longitude=-74.0060)
        config_no_longitude = GeneralSettings(latitude=40.7128, longitude=None)
        config_no_coords = GeneralSettings(latitude=None, longitude=None)

        assert config_no_latitude.timezone is None
        assert config_no_longitude.timezone is None
        assert config_no_coords.timezone is None

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
                "devices/max_home_appliances",
                1,
                [("devices.max_home_appliances", 1), ],
                None,
            ),
            # Correct value to generate a windows list
            (
                "devices/home_appliances",
                {
                    "dishwasher1": {
                        "device_id": "dishwasher1",
                        "consumption_wh": 2000,
                        "duration_h": 3,
                        "cycle_time_windows": {
                            "windows": [
                                {
                                    "start_time": "08:00",
                                    "duration": "5 hours",
                                    "value": 0,
                                },
                                {
                                    "start_time": "15:00",
                                    "duration": "3 hours",
                                    "value": 0,
                                },
                            ],
                        },
                    },
                },
                [
                    ("devices.home_appliances['dishwasher1'].device_id", "dishwasher1"),
                    ("devices.home_appliances['dishwasher1'].consumption_wh", 2000),
                    ("devices.home_appliances['dishwasher1'].duration_h", 3),
                ],
                None,
            ),
            # Invalid value for list
            (
                "devices/home_appliances/dishwasher1/cycle_time_windows/windows",
                "invalid",
                [
                    (
                        "devices.home_appliances['dishwasher1'].cycle_time_windows",
                        [
                            {
                                "start_time": "08:00",
                                "duration": "5 hours",
                                "value": 0,
                            },
                            {
                                "start_time": "15:00",
                                "duration": "3 hours",
                                "value": 0,
                            },
                        ],
                    )
                ],
                ValueError,
            ),
            # Invalid index (out of bound)
            (
                "devices/home_appliances/dishwasher1/cycle_time_windows/windows/10",
                {
                    "start_time": "17:00",
                    "duration": "1 hour",
                    "value": 0,
                },
                [
                    (
                        "devices.home_appliances['dishwasher1'].cycle_time_windows.windows[10]",
                        {
                            "start_time": "17:00",
                            "duration": "1 hour",
                            "value": 0,
                        },
                    )
                ],
                TypeError,
            ),
            # Invalid index (no number)
            (
                "devices/home_appliances/dishwasher1/cycle_time_windows/windows/test",
                {
                    "start_time": "17:00",
                    "duration": "1 hour",
                    "value": 0,
                },
                [
                    (
                        "devices.home_appliances['dishwasher1'].cycle_time_windows.windows[0]",
                        {
                            "start_time": "08:00",
                            "duration": "5 hours",
                            "value": 0,
                        },
                    )
                ],
                IndexError,
            ),
            # Unset value (set None)
            (
                "devices/home_appliances/dishwasher1/cycle_time_windows/windows/0",
                None,
                [
                    (
                        "devices.home_appliances['dishwasher1'].cycle_time_windows.windows[0]",
                        {
                            "start_time": "08:00",
                            "duration": "5 hours",
                            "value": 0,
                        },
                    )
                ],
                TypeError,
            ),
        ],
    )
    def test_set_nested_key(self, path, value, expected, exception, config_eos):
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

    def test_set_nested_key_read_only_computed_field(self, config_eos):
        """Setting a computed field is reported as read-only, not as missing."""
        path = "adapter/homeassistant/eos_device_instruction_entity_ids"
        with pytest.raises(TypeError, match="read-only computed field"):
            config_eos.set_nested_value(path, ["sensor.eos_battery1"])

    def test_set_nested_key_unknown_field_still_reports_missing(self, config_eos):
        """A field that does not exist keeps the original 'does not exist' message."""
        with pytest.raises(TypeError, match="does not exist in model"):
            config_eos.set_nested_value("adapter/homeassistant/no_such_field", 1)

    @pytest.mark.parametrize(
        "path, expected_value, exception",
        [
            ("general/latitude", 52.52, None),
            ("general/latitude/", 52.52, None),
            ("general/latitude/test", None, KeyError),
        ],
    )
    def test_get_nested_key(self, path, expected_value, exception, config_eos):
        if not exception:
            assert config_eos.get_nested_value(path) == expected_value
        else:
            with pytest.raises(exception):
                config_eos.get_nested_value(path)

    def test_merge_settings_from_dict_invalid(self, config_eos):
        """Test merging invalid data."""
        invalid_settings = {
            "general": {
                "latitude": "invalid_latitude"  # Should be a float
            },
        }

        with pytest.raises(Exception):  # Pydantic ValidationError expected
            config_eos.merge_settings_from_dict(invalid_settings)

    def test_merge_settings_partial(self, config_eos):
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
                "electric_vehicles": {
                    "ev1": {"charge_rates": [0.0, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0],}
                },
            }
        }

        config_eos.merge_settings_from_dict(partial_settings)
        assert config_eos.devices.max_electric_vehicles == 1
        assert len(config_eos.devices.electric_vehicles) == 1
        assert_values_equal(config_eos.devices.electric_vehicles["ev1"].charge_rates, [0.0, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0])

        # Assure re-apply generates the same config
        config_eos.merge_settings_from_dict(partial_settings)
        assert config_eos.devices.max_electric_vehicles == 1
        assert len(config_eos.devices.electric_vehicles) == 1
        assert_values_equal(config_eos.devices.electric_vehicles["ev1"].charge_rates, [0.0, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0])

        # Assure update keeps same values
        config_eos.update()
        assert config_eos.devices.max_electric_vehicles == 1
        assert len(config_eos.devices.electric_vehicles) == 1
        assert_values_equal(config_eos.devices.electric_vehicles["ev1"].charge_rates, [0.0, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0])

    def test_merge_settings_empty(self, config_eos):
        """Test merging an empty dictionary does not change settings."""
        original_latitude = config_eos.general.latitude

        config_eos.merge_settings_from_dict({})  # No changes

        assert config_eos.general.latitude == original_latitude  # Should remain unchanged

    def test_merge_settings_overrides_config_file(self, config_eos_file):
        """Runtime settings take precedence over the EOS configuration file."""
        write_config_file(
            config_eos_file,
            {
                "optimization": {"genetic": {"individuals": 200}},
                "pvforecast": {
                    "planes": [
                        {"surface_tilt": 30.0, "surface_azimuth": azimuth, "peakpower": 5.0}
                        for azimuth in (0.0, 90.0, 180.0, 270.0)
                    ]
                },
            },
        )
        assert config_eos_file.optimization.genetic.individuals == 200
        assert len(config_eos_file.pvforecast.planes) == 4

        config_eos_file.merge_settings_from_dict(
            {
                "optimization": {"genetic": {"individuals": 300}},
                "pvforecast": {
                    "planes": [{"surface_tilt": 30.0, "surface_azimuth": 180.0, "peakpower": 5.0}]
                },
            }
        )

        assert config_eos_file.optimization.genetic.individuals == 300
        assert len(config_eos_file.pvforecast.planes) == 1

    def test_merge_settings_overrides_env(self, config_eos_file, monkeypatch):
        """Runtime settings take precedence over environment variables."""
        monkeypatch.setenv("EOS_OPTIMIZATION__GENETIC__INDIVIDUALS", "150")
        config_eos_file.reset_settings()
        assert config_eos_file.optimization.genetic.individuals == 150

        config_eos_file.merge_settings_from_dict({"optimization": {"genetic": {"individuals": 300}}})

        assert config_eos_file.optimization.genetic.individuals == 300

    def test_env_overrides_config_file_after_merge(self, config_eos_file, monkeypatch):
        """Environment variables keep precedence over the config file for untouched keys."""
        write_config_file(config_eos_file, {"server": {"port": 9000}})
        monkeypatch.setenv("EOS_SERVER__PORT", "9500")
        config_eos_file.reset_settings()
        assert config_eos_file.server.port == 9500

        # A runtime update of an unrelated key must not freeze the env value
        config_eos_file.merge_settings_from_dict({"general": {"latitude": 51.1657}})
        assert config_eos_file.general.latitude == 51.1657
        assert config_eos_file.server.port == 9500

        monkeypatch.setenv("EOS_SERVER__PORT", "9600")
        config_eos_file.reset_settings()
        assert config_eos_file.server.port == 9600

    def test_reset_settings_drops_runtime_settings(self, config_eos_file):
        """Reset drops runtime settings and falls back to the config file."""
        write_config_file(config_eos_file, {"optimization": {"genetic": {"individuals": 200}}})

        config_eos_file.merge_settings_from_dict({"optimization": {"genetic": {"individuals": 300}}})
        assert config_eos_file.optimization.genetic.individuals == 300

        config_eos_file.reset_settings()
        assert config_eos_file.optimization.genetic.individuals == 200

    def test_set_nested_value_survives_merge(self, config_eos_file):
        """Granular updates are not lost by a later bulk update."""
        write_config_file(
            config_eos_file,
            {
                "general": {"latitude": 48.0},
                "optimization": {"genetic": {"individuals": 200}},
                "pvforecast": {
                    "planes": [
                        {"surface_tilt": 30.0, "surface_azimuth": azimuth, "peakpower": 5.0}
                        for azimuth in (0.0, 90.0)
                    ]
                },
            },
        )

        config_eos_file.set_nested_value("optimization/genetic/individuals", 400)
        # A list index can not be expressed by the settings dictionary
        config_eos_file.set_nested_value("pvforecast/planes/1/peakpower", 9.9)
        # Clearing a value must not be reverted by the config file either
        config_eos_file.set_nested_value("general/latitude", None)

        config_eos_file.merge_settings_from_dict({"server": {"port": 8600}})

        assert config_eos_file.server.port == 8600
        assert config_eos_file.optimization.genetic.individuals == 400
        assert config_eos_file.pvforecast.planes[1].peakpower == 9.9
        assert config_eos_file.general.latitude is None

    def test_revert_settings_restores_backup(self, config_eos_file):
        """Revert restores the backup values even if the config file differs."""
        write_config_file(config_eos_file, {"optimization": {"genetic": {"individuals": 200}}})

        config_file_path = config_eos_file.general.config_file_path
        assert config_file_path is not None
        backup_path = config_file_path.with_suffix(".backup")
        backup_path.write_text(
            json.dumps(
                {
                    "general": {"version": config_eos_file.general.version},
                    "optimization": {"genetic": {"individuals": 500}},
                }
            ),
            encoding="utf-8",
        )

        config_eos_file.revert_settings("backup")

        assert config_eos_file.optimization.genetic.individuals == 500

    def test_config_from_env_on_first_init(self, config_eos, config_default_dirs, monkeypatch):
        """Environment variables are applied on the first configuration build."""
        config_eos.reset_instance()

        monkeypatch.setenv("EOS_CONFIG_DIR", str(config_default_dirs[0]))
        monkeypatch.setenv("EOS_SERVER__PORT", "8553")

        assert ConfigEOS().server.port == 8553


class TestConfigEOSToConfigFile:

    def test_to_config_file_writes_file(self, config_eos):
        config_path = config_eos.general.config_file_path

        # Remove file to test writing
        config_path.unlink(missing_ok=True)

        config_eos.to_config_file()

        assert config_path.exists()
        assert config_path.read_text().strip().startswith("{")

    def test_to_config_file_excludes_computed_fields(self, config_eos):
        config_path = config_eos.general.config_file_path

        config_eos.to_config_file()
        data = json.loads(config_path.read_text())

        assert "timezone" not in data["general"]
        assert "data_output_path" not in data["general"]
        assert "config_folder_path" not in data["general"]
        assert "config_file_path" not in data["general"]

    def test_to_config_file_excludes_defaults(self, config_eos):
        """Ensure fields with default values are excluded when saving config."""

        # Pick fields that have defaults
        default_latitude = GeneralSettings.model_fields["latitude"].default
        default_longitude = GeneralSettings.model_fields["longitude"].default

        # Ensure fields are at default values
        config_eos.general.latitude = default_latitude
        config_eos.general.longitude = default_longitude

        # Save the config using the correct path managed by config_eos
        config_eos.to_config_file()

        # Read back JSON from the correct path
        config_file_path = config_eos.general.config_file_path
        content = json.loads(config_file_path.read_text(encoding="utf-8"))

        # Default fields should not appear
        assert "latitude" not in content["general"]
        assert "longitude" not in content["general"]

        # Non-default value should appear
        config_eos.general.latitude = 48.0
        config_eos.to_config_file()
        content = json.loads(config_file_path.read_text(encoding="utf-8"))
        assert content["general"]["latitude"] == 48.0

    def test_to_config_file_excludes_none_fields(self, config_eos):
        config_eos.general.latitude = None

        config_path = config_eos.general.config_file_path
        config_eos.to_config_file()

        data = json.loads(config_path.read_text())

        assert "latitude" not in data["general"]

    def test_to_config_file_includes_version(tmp_path, config_eos):
        """Ensure general.version is always included."""
        # Save config
        config_eos.to_config_file()

        # Read back JSON
        config_file_path = config_eos.general.config_file_path
        content = json.loads(config_file_path.read_text(encoding="utf-8"))

        # Assert 'version' is included even if default
        assert content["general"]["version"] == config_eos.general.version

    def test_to_config_file_roundtrip(self, config_eos):
        config_eos.merge_settings_from_dict(
            {
                "general": {"latitude": 48.0},
                "server": {"port": 9000},
            }
        )

        config_path = config_eos.general.config_file_path
        config_eos.to_config_file()

        raw_data = json.loads(config_path.read_text())
        reloaded = ConfigEOS.model_validate(raw_data)

        assert reloaded.general.latitude == 48.0
        assert reloaded.server.port == 9000


class TestDeviceSettings:
    def test_device_map_supplies_stable_identity(self):
        raw = {"batteries": {"house": {"capacity_wh": 12000}}}
        a = DevicesCommonSettings.model_validate(raw)
        b = DevicesCommonSettings.model_validate(raw)
        assert a.batteries is not None and b.batteries is not None
        assert a.batteries["house"].device_id == b.batteries["house"].device_id == "house"
        assert "house-soc-factor" in a.measurement_keys

    def test_device_map_rejects_conflicting_identity(self):
        with pytest.raises(ValidationError, match="device_id"):
            DevicesCommonSettings.model_validate({"batteries": {"house": {"device_id": "other"}}})
