import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional
from unittest.mock import PropertyMock, patch

import pendulum
import pytest
from xprocess import ProcessStarter

from akkudoktoreos.config.config import ConfigEOS, get_config
from akkudoktoreos.core.logging import get_logger

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
    parser.addoption(
        "--check-config-side-effect",
        action="store_true",
        default=False,
        help="Verify that user config file is non-existent (will also fail if user config file exists before test run).",
    )


@pytest.fixture
def is_full_run(request):
    yield bool(request.config.getoption("--full-run"))


@pytest.fixture(autouse=True)
def config_mixin(config_eos):
    with patch(
        "akkudoktoreos.core.coreabc.ConfigMixin.config", new_callable=PropertyMock
    ) as config_mixin_patch:
        config_mixin_patch.return_value = config_eos
        yield config_mixin_patch


@pytest.fixture
def devices_eos(config_mixin):
    from akkudoktoreos.devices.devices import get_devices

    devices = get_devices()
    print("devices_eos reset!")
    devices.reset()
    return devices


@pytest.fixture
def devices_mixin(devices_eos):
    with patch(
        "akkudoktoreos.core.coreabc.DevicesMixin.devices", new_callable=PropertyMock
    ) as devices_mixin_patch:
        devices_mixin_patch.return_value = devices_eos
        yield devices_mixin_patch


# Test if test has side effect of writing to system (user) config file
# Before activating, make sure that no user config file exists (e.g. ~/.config/net.akkudoktoreos.eos/EOS.config.json)
@pytest.fixture(autouse=True)
def cfg_non_existent(request):
    yield
    if bool(request.config.getoption("--check-config-side-effect")):
        from platformdirs import user_config_dir

        user_dir = user_config_dir(ConfigEOS.APP_NAME)
        assert not Path(user_dir).joinpath(ConfigEOS.CONFIG_FILE_NAME).exists()
        assert not Path.cwd().joinpath(ConfigEOS.CONFIG_FILE_NAME).exists()


@pytest.fixture(autouse=True)
def user_cwd(config_default_dirs):
    with patch(
        "pathlib.Path.cwd",
        return_value=config_default_dirs[1],
    ) as user_cwd_patch:
        yield user_cwd_patch


@pytest.fixture(autouse=True)
def user_config_dir(config_default_dirs):
    with patch(
        "akkudoktoreos.config.config.user_config_dir",
        return_value=str(config_default_dirs[0]),
    ) as user_dir_patch:
        yield user_dir_patch


@pytest.fixture(autouse=True)
def user_data_dir(config_default_dirs):
    with patch(
        "akkudoktoreos.config.config.user_data_dir",
        return_value=str(config_default_dirs[-1] / "data"),
    ) as user_dir_patch:
        yield user_dir_patch


@pytest.fixture
def config_eos(
    disable_debug_logging,
    user_config_dir,
    user_data_dir,
    user_cwd,
    config_default_dirs,
    monkeypatch,
) -> ConfigEOS:
    """Fixture to reset EOS config to default values."""
    monkeypatch.setenv(
        "EOS_CONFIG__DATA_CACHE_SUBPATH", str(config_default_dirs[-1] / "data/cache")
    )
    monkeypatch.setenv(
        "EOS_CONFIG__DATA_OUTPUT_SUBPATH", str(config_default_dirs[-1] / "data/output")
    )
    config_file = config_default_dirs[0] / ConfigEOS.CONFIG_FILE_NAME
    config_file_cwd = config_default_dirs[1] / ConfigEOS.CONFIG_FILE_NAME
    assert not config_file.exists()
    assert not config_file_cwd.exists()
    config_eos = get_config()
    config_eos.reset_settings()
    assert config_file == config_eos.general.config_file_path
    assert config_file.exists()
    assert not config_file_cwd.exists()
    assert config_default_dirs[-1] / "data" == config_eos.general.data_folder_path
    assert config_default_dirs[-1] / "data/cache" == config_eos.general.data_cache_path
    assert config_default_dirs[-1] / "data/output" == config_eos.general.data_output_path
    return config_eos


@pytest.fixture
def config_default_dirs():
    """Fixture that provides a list of directories to be used as config dir."""
    with tempfile.TemporaryDirectory() as tmp_user_home_dir:
        # Default config directory from platform user config directory
        config_default_dir_user = Path(tmp_user_home_dir) / "config"

        # Default config directory from current working directory
        config_default_dir_cwd = Path(tmp_user_home_dir) / "cwd"
        config_default_dir_cwd.mkdir()

        # Default config directory from default config file
        config_default_dir_default = Path(__file__).parent.parent.joinpath("src/akkudoktoreos/data")

        # Default data directory from platform user data directory
        data_default_dir_user = Path(tmp_user_home_dir)
        yield (
            config_default_dir_user,
            config_default_dir_cwd,
            config_default_dir_default,
            data_default_dir_user,
        )


@pytest.fixture
def server(xprocess, config_eos, config_default_dirs):
    """Fixture to start the server.

    Provides URL of the server.
    """

    class Starter(ProcessStarter):
        # Set environment before any subprocess run, to keep custom config dir
        env = os.environ.copy()
        env["EOS_DIR"] = str(config_default_dirs[-1])
        project_dir = config_eos.package_root_path

        # assure server to be installed
        try:
            subprocess.run(
                [sys.executable, "-c", "import akkudoktoreos.server.eos"],
                check=True,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=project_dir,
            )
        except subprocess.CalledProcessError:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", project_dir],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        # command to start server process
        args = [sys.executable, "-m", "akkudoktoreos.server.eos"]

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
    print(f"View xprocess logfile at: {logfile}")

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
