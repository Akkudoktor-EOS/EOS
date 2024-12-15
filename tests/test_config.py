import os
import shutil
import tempfile
from pathlib import Path

import pytest

from akkudoktoreos.config.config import ConfigEOS, get_config
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)

config_eos = get_config()

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")

FILE_TESTDATA_CONFIGEOS_1_JSON = DIR_TESTDATA.joinpath(config_eos.CONFIG_FILE_NAME)
FILE_TESTDATA_CONFIGEOS_1_DIR = FILE_TESTDATA_CONFIGEOS_1_JSON.parent


@pytest.fixture
def reset_config_singleton():
    """Fixture to reset the ConfigEOS singleton instance before a test."""
    ConfigEOS.reset_instance()
    yield
    ConfigEOS.reset_instance()


def test_fixture_stash_config_file(stash_config_file, config_default_dirs):
    """Assure fixture stash_config_file is working."""
    config_default_dir_user, config_default_dir_cwd, _ = config_default_dirs

    config_file_path_user = config_default_dir_user.joinpath(config_eos.CONFIG_FILE_NAME)
    config_file_path_cwd = config_default_dir_cwd.joinpath(config_eos.CONFIG_FILE_NAME)

    assert not config_file_path_user.exists()
    assert not config_file_path_cwd.exists()


def test_config_constants():
    """Test config constants are the way expected by the tests."""
    assert config_eos.APP_NAME == "net.akkudoktor.eos"
    assert config_eos.APP_AUTHOR == "akkudoktor"
    assert config_eos.EOS_DIR == "EOS_DIR"
    assert config_eos.ENCODING == "UTF-8"
    assert config_eos.CONFIG_FILE_NAME == "EOS.config.json"


def test_computed_paths(reset_config):
    """Test computed paths for output and cache."""
    config_eos.merge_settings_from_dict(
        {
            "data_folder_path": "/base/data",
            "data_output_subpath": "output",
            "data_cache_subpath": "cache",
        }
    )
    assert config_eos.data_output_path == Path("/base/data/output")
    assert config_eos.data_cache_path == Path("/base/data/cache")


def test_singleton_behavior(reset_config_singleton):
    """Test that ConfigEOS behaves as a singleton."""
    instance1 = ConfigEOS()
    instance2 = ConfigEOS()
    assert instance1 is instance2


def test_default_config_path(reset_config, config_default_dirs, stash_config_file):
    """Test that the default config file path is computed correctly."""
    _, _, config_default_dir_default = config_default_dirs

    expected_path = config_default_dir_default.joinpath("default.config.json")
    assert config_eos.config_default_file_path == expected_path
    assert config_eos.config_default_file_path.is_file()


def test_config_folder_path(reset_config, config_default_dirs, stash_config_file, monkeypatch):
    """Test that _config_folder_path identifies the correct config directory or None."""
    config_default_dir_user, _, _ = config_default_dirs

    # All config files are stashed away, no config folder path
    assert config_eos._config_folder_path() is None

    config_file_user = config_default_dir_user.joinpath(config_eos.CONFIG_FILE_NAME)
    shutil.copy2(config_eos.config_default_file_path, config_file_user)
    assert config_eos._config_folder_path() == config_default_dir_user

    monkeypatch.setenv("EOS_DIR", str(FILE_TESTDATA_CONFIGEOS_1_DIR))
    assert config_eos._config_folder_path() == FILE_TESTDATA_CONFIGEOS_1_DIR

    # Cleanup after the test
    os.remove(config_file_user)


def test_config_copy(reset_config, stash_config_file, monkeypatch):
    """Test if the config is copied to the provided path."""
    temp_dir = tempfile.TemporaryDirectory()
    temp_folder_path = Path(temp_dir.name)
    temp_config_file_path = temp_folder_path.joinpath(config_eos.CONFIG_FILE_NAME).resolve()
    monkeypatch.setenv(config_eos.EOS_DIR, str(temp_folder_path))
    if temp_config_file_path.exists():
        temp_config_file_path.unlink()
    assert not temp_config_file_path.exists()
    assert config_eos._config_folder_path() is None
    assert config_eos._config_file_path() == temp_config_file_path

    config_eos.from_config_file()
    assert temp_config_file_path.exists()

    # Cleanup after the test
    temp_dir.cleanup()
