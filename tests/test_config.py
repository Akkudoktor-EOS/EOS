import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from akkudoktoreos.config.config import ConfigEOS, GeneralSettings
from akkudoktoreos.core.logging import get_logger

logger = get_logger(__name__)


# overwrite config_mixin fixture from conftest
@pytest.fixture(autouse=True)
def config_mixin():
    pass


def test_fixture_new_config_file(config_default_dirs):
    """Assure fixture stash_config_file is working."""
    config_default_dir_user, config_default_dir_cwd, _, _ = config_default_dirs

    config_file_path_user = config_default_dir_user.joinpath(ConfigEOS.CONFIG_FILE_NAME)
    config_file_path_cwd = config_default_dir_cwd.joinpath(ConfigEOS.CONFIG_FILE_NAME)

    assert not config_file_path_user.exists()
    assert not config_file_path_cwd.exists()


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
                    "data_cache_subpath": "somewhere/cache",
                }
            }
        )
    assert config_eos.general.data_output_path == Path("/base/data/extra/output")
    assert config_eos.general.data_cache_path == Path("/base/data/somewhere/cache")
    # reset settings so the config_eos fixture can verify the default paths
    config_eos.reset_settings()


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


def test_default_config_path(config_eos, config_default_dirs):
    """Test that the default config file path is computed correctly."""
    _, _, config_default_dir_default, _ = config_default_dirs

    expected_path = config_default_dir_default.joinpath("default.config.json")
    assert config_eos.config_default_file_path == expected_path
    assert config_eos.config_default_file_path.is_file()


def test_config_file_priority(config_default_dirs):
    """Test config file priority."""
    from akkudoktoreos.config.config import get_config

    config_default_dir_user, config_default_dir_cwd, _, _ = config_default_dirs

    config_file = Path(config_default_dir_cwd) / ConfigEOS.CONFIG_FILE_NAME
    config_file.write_text("{}")
    config_eos = get_config()
    assert config_eos.general.config_file_path == config_file

    config_file = Path(config_default_dir_user) / ConfigEOS.CONFIG_FILE_NAME
    config_file.parent.mkdir()
    config_file.write_text("{}")
    config_eos.update()
    assert config_eos.general.config_file_path == config_file


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
