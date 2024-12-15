import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import pendulum
import platformdirs
import pytest
from xprocess import ProcessStarter

from akkudoktoreos.config.config import get_config
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)


@pytest.fixture()
def disable_debug_logging(scope="session", autouse=True):
    """Automatically disable debug logging for all tests."""
    original_levels = {}
    root_logger = logging.getLogger()

    original_levels[root_logger] = root_logger.level
    root_logger.setLevel(logging.INFO)

    for logger_name, logger in logging.root.manager.loggerDict.items():
        if isinstance(logger, logging.Logger):
            original_levels[logger] = logger.level
            if logger.level <= logging.DEBUG:
                logger.setLevel(logging.INFO)

    yield

    for logger, level in original_levels.items():
        logger.setLevel(level)


def pytest_addoption(parser):
    parser.addoption(
        "--full-run", action="store_true", default=False, help="Run with all optimization tests."
    )


@pytest.fixture
def is_full_run(request):
    yield bool(request.config.getoption("--full-run"))


@pytest.fixture
def reset_config(disable_debug_logging):
    """Fixture to reset EOS config to default values."""
    config_eos = get_config()
    config_eos.reset_settings()
    config_eos.reset_to_defaults()
    return config_eos


@pytest.fixture
def config_default_dirs():
    """Fixture that provides a list of directories to be used as config dir."""
    config_eos = get_config()
    # Default config directory from platform user config directory
    config_default_dir_user = Path(platformdirs.user_config_dir(config_eos.APP_NAME))
    # Default config directory from current working directory
    config_default_dir_cwd = Path.cwd()
    # Default config directory from default config file
    config_default_dir_default = Path(__file__).parent.parent.joinpath("src/akkudoktoreos/data")
    return config_default_dir_user, config_default_dir_cwd, config_default_dir_default


@pytest.fixture
def stash_config_file(config_default_dirs):
    """Fixture to temporarily stash away an existing config file during a test.

    If the specified config file exists, it moves the file to a temporary directory.
    The file is restored to its original location after the test.

    Keep right most in fixture parameter list to assure application at last.

    Returns:
        Path: Path to the stashed config file.
    """
    config_eos = get_config()
    config_default_dir_user, config_default_dir_cwd, _ = config_default_dirs

    config_file_path_user = config_default_dir_user.joinpath(config_eos.CONFIG_FILE_NAME)
    config_file_path_cwd = config_default_dir_cwd.joinpath(config_eos.CONFIG_FILE_NAME)

    original_config_file_user = None
    original_config_file_cwd = None
    if config_file_path_user.exists():
        original_config_file_user = config_file_path_user
    if config_file_path_cwd.exists():
        original_config_file_cwd = config_file_path_cwd

    temp_dir = tempfile.TemporaryDirectory()
    temp_file_user = None
    temp_file_cwd = None

    # If the file exists, move it to the temporary directory
    if original_config_file_user:
        temp_file_user = Path(temp_dir.name) / f"user.{original_config_file_user.name}"
        shutil.move(original_config_file_user, temp_file_user)
        assert not original_config_file_user.exists()
        logger.debug(f"Stashed: '{original_config_file_user}'")
    if original_config_file_cwd:
        temp_file_cwd = Path(temp_dir.name) / f"cwd.{original_config_file_cwd.name}"
        shutil.move(original_config_file_cwd, temp_file_cwd)
        assert not original_config_file_cwd.exists()
        logger.debug(f"Stashed: '{original_config_file_cwd}'")

    # Yield the temporary file path to the test
    yield temp_file_user, temp_file_cwd

    # Cleanup after the test
    if temp_file_user:
        # Restore the file to its original location
        shutil.move(temp_file_user, original_config_file_user)
        assert original_config_file_user.exists()
    if temp_file_cwd:
        # Restore the file to its original location
        shutil.move(temp_file_cwd, original_config_file_cwd)
        assert original_config_file_cwd.exists()
    temp_dir.cleanup()


@pytest.fixture
def server(xprocess, tmp_path: Path):
    """Fixture to start the server.

    Provides URL of the server.
    """

    class Starter(ProcessStarter):
        # assure server to be installed
        try:
            subprocess.run(
                [sys.executable, "-c", "import akkudoktoreos.server.fastapi_server"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError:
            project_dir = Path(__file__).parent.parent.parent
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", project_dir],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        # command to start server process
        args = [sys.executable, "-m", "akkudoktoreos.server.fastapi_server"]
        config_eos = get_config()
        settings = {
            "data_folder_path": tmp_path,
        }
        config_eos.merge_settings_from_dict(settings)

        # startup pattern
        pattern = "Application startup complete."
        # search this number of lines for the startup pattern, if not found
        # a RuntimeError will be raised informing the user
        max_read_lines = 30

        # will wait for 30 seconds before timing out
        timeout = 30

        # xprocess will now attempt to clean up upon interruptions
        terminate_on_interrupt = True

    # ensure process is running and return its logfile
    pid, logfile = xprocess.ensure("eos", Starter)

    # create url/port info to the server
    url = "http://127.0.0.1:8503"
    yield url

    # clean up whole process tree afterwards
    xprocess.getinfo("eos").terminate()


@pytest.fixture
def set_other_timezone():
    """Temporarily sets a timezone for Pendulum during a test.

    Resets to the original timezone after the test completes.
    """
    original_timezone = pendulum.local_timezone()

    default_other_timezone = "Atlantic/Canary"
    if default_other_timezone == original_timezone:
        default_other_timezone = "Asia/Singapore"

    def _set_timezone(other_timezone: Optional[str] = None) -> str:
        if other_timezone is None:
            other_timezone = default_other_timezone
        pendulum.set_local_timezone(other_timezone)
        assert pendulum.local_timezone() == other_timezone
        return other_timezone

    yield _set_timezone

    # Restore the original timezone
    pendulum.set_local_timezone(original_timezone)
    assert pendulum.local_timezone() == original_timezone
