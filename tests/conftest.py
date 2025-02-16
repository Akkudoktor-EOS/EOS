import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path
from typing import Generator, Optional, Union
from unittest.mock import PropertyMock, patch

import pendulum
import psutil
import pytest
import requests
from xprocess import ProcessStarter, XProcess

from akkudoktoreos.config.config import ConfigEOS, get_config
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.server.server import get_default_host

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
    parser.addoption(
        "--system-test",
        action="store_true",
        default=False,
        help="System test mode. Tests may access real resources, like prediction providers!",
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
def is_system_test(request):
    yield bool(request.config.getoption("--system-test"))


@pytest.fixture
def prediction_eos():
    from akkudoktoreos.prediction.prediction import get_prediction

    return get_prediction()


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
    if not bool(request.config.getoption("--check-config-side-effect")):
        yield
        return

    # Before test
    from platformdirs import user_config_dir

    user_dir = user_config_dir(ConfigEOS.APP_NAME)
    user_config_file = Path(user_dir).joinpath(ConfigEOS.CONFIG_FILE_NAME)
    cwd_config_file = Path.cwd().joinpath(ConfigEOS.CONFIG_FILE_NAME)
    assert not user_config_file.exists(), (
        f"Config file {user_config_file} exists, please delete before test!"
    )
    assert not cwd_config_file.exists(), (
        f"Config file {cwd_config_file} exists, please delete before test!"
    )

    # Yield to test
    yield

    # After test
    assert not user_config_file.exists(), (
        f"Config file {user_config_file} created, please check test!"
    )
    assert not cwd_config_file.exists(), (
        f"Config file {cwd_config_file} created, please check test!"
    )


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
    assert config_default_dirs[-1] / "data/cache" == config_eos.cache.path()
    assert config_default_dirs[-1] / "data/output" == config_eos.general.data_output_path
    return config_eos


@pytest.fixture
def config_default_dirs(tmpdir):
    """Fixture that provides a list of directories to be used as config dir."""
    tmp_user_home_dir = Path(tmpdir)

    # Default config directory from platform user config directory
    config_default_dir_user = tmp_user_home_dir / "config"

    # Default config directory from current working directory
    config_default_dir_cwd = tmp_user_home_dir / "cwd"
    config_default_dir_cwd.mkdir()

    # Default config directory from default config file
    config_default_dir_default = Path(__file__).parent.parent.joinpath("src/akkudoktoreos/data")

    # Default data directory from platform user data directory
    data_default_dir_user = tmp_user_home_dir

    return (
        config_default_dir_user,
        config_default_dir_cwd,
        config_default_dir_default,
        data_default_dir_user,
    )


@contextmanager
def server_base(xprocess: XProcess) -> Generator[dict[str, Union[str, int]], None, None]:
    """Fixture to start the server with temporary EOS_DIR and default config.

    Args:
        xprocess (XProcess): The pytest-xprocess fixture to manage the server process.

    Yields:
        dict[str, str]: A dictionary containing:
            - "server" (str): URL of the server.
            - "eos_dir" (str): Path to the temporary EOS_DIR.
    """
    host = get_default_host()
    port = 8503
    eosdash_port = 8504

    # Port of server may be still blocked by a server usage despite the other server already
    # shut down. CLOSE_WAIT, TIME_WAIT may typically take up to 120 seconds.
    server_timeout = 120

    server = f"http://{host}:{port}"
    eosdash_server = f"http://{host}:{eosdash_port}"
    eos_tmp_dir = tempfile.TemporaryDirectory()
    eos_dir = str(eos_tmp_dir.name)

    class Starter(ProcessStarter):
        # assure server to be installed
        try:
            project_dir = Path(__file__).parent.parent
            subprocess.run(
                [sys.executable, "-c", "import", "akkudoktoreos.server.eos"],
                check=True,
                env=os.environ,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=project_dir,
            )
        except subprocess.CalledProcessError:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", str(project_dir)],
                env=os.environ,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=project_dir,
            )

        # Set environment for server run
        env = os.environ.copy()
        env["EOS_DIR"] = eos_dir
        env["EOS_CONFIG_DIR"] = eos_dir

        # command to start server process
        args = [
            sys.executable,
            "-m",
            "akkudoktoreos.server.eos",
            "--host",
            host,
            "--port",
            str(port),
        ]

        # Will wait for 'server_timeout' seconds before timing out
        timeout = server_timeout

        # xprocess will now attempt to clean up upon interruptions
        terminate_on_interrupt = True

        # checks if our server is ready
        def startup_check(self):
            try:
                result = requests.get(f"{server}/v1/health", timeout=2)
                if result.status_code == 200:
                    return True
            except:
                pass
            return False

    def cleanup_eos_eosdash():
        # Cleanup any EOS process left.
        if os.name == "nt":
            # Windows does not provide SIGKILL
            sigkill = signal.SIGTERM
        else:
            sigkill = signal.SIGKILL  # type: ignore
        # - Use pid on EOS health endpoint
        try:
            result = requests.get(f"{server}/v1/health", timeout=2)
            if result.status_code == HTTPStatus.OK:
                pid = result.json()["pid"]
                os.kill(pid, sigkill)
                time.sleep(1)
                result = requests.get(f"{server}/v1/health", timeout=2)
                assert result.status_code != HTTPStatus.OK
        except:
            pass
        # - Use pids from processes on EOS port
        for retries in range(int(server_timeout / 3)):
            pids: list[int] = []
            for conn in psutil.net_connections(kind="inet"):
                if conn.laddr.port == port:
                    if conn.pid not in pids:
                        # Get fresh process info
                        try:
                            process = psutil.Process(conn.pid)
                            process_info = process.as_dict(attrs=["pid", "cmdline"])
                            if "akkudoktoreos.server.eos" in process_info["cmdline"]:
                                pids.append(conn.pid)
                        except:
                            # PID may already be dead
                            pass
                for pid in pids:
                    os.kill(pid, sigkill)
            if len(pids) == 0:
                break
            time.sleep(3)
        assert len(pids) == 0
        # Cleanup any EOSdash processes left.
        # - Use pid on EOSdash health endpoint
        try:
            result = requests.get(f"{eosdash_server}/eosdash/health", timeout=2)
            if result.status_code == HTTPStatus.OK:
                pid = result.json()["pid"]
                os.kill(pid, sigkill)
                time.sleep(1)
                result = requests.get(f"{eosdash_server}/eosdash/health", timeout=2)
                assert result.status_code != HTTPStatus.OK
        except:
            pass
        # - Use pids from processes on EOSdash port
        for retries in range(int(server_timeout / 3)):
            pids = []
            for conn in psutil.net_connections(kind="inet"):
                if conn.laddr.port == eosdash_port:
                    if conn.pid not in pids:
                        # Get fresh process info
                        try:
                            process = psutil.Process(conn.pid)
                            process_info = process.as_dict(attrs=["pid", "cmdline"])
                            if "akkudoktoreos.server.eosdash" in process_info["cmdline"]:
                                pids.append(conn.pid)
                        except:
                            # PID may already be dead
                            pass
            for pid in pids:
                os.kill(pid, sigkill)
            if len(pids) == 0:
                break
            time.sleep(3)
        assert len(pids) == 0

    # Kill all running eos and eosdash process - just to be sure
    cleanup_eos_eosdash()

    # Ensure there is an empty config file in the temporary EOS directory
    config_file_path = Path(eos_dir).joinpath(ConfigEOS.CONFIG_FILE_NAME)
    with config_file_path.open(mode="w", encoding="utf-8", newline="\n") as fd:
        json.dump({}, fd)

    # ensure process is running and return its logfile
    pid, logfile = xprocess.ensure("eos", Starter)
    logger.info(f"Started EOS ({pid}). This may take very long (up to {server_timeout} seconds).")
    logger.info(f"View xprocess logfile at: {logfile}")

    yield {
        "server": server,
        "eosdash_server": eosdash_server,
        "eos_dir": eos_dir,
        "timeout": server_timeout,
    }

    # clean up whole process tree afterwards
    xprocess.getinfo("eos").terminate()

    # Cleanup any EOS process left.
    cleanup_eos_eosdash()

    # Remove temporary EOS_DIR
    eos_tmp_dir.cleanup()


@pytest.fixture(scope="class")
def server_setup_for_class(xprocess) -> Generator[dict[str, Union[str, int]], None, None]:
    """A fixture to start the server for a test class."""
    with server_base(xprocess) as result:
        yield result


@pytest.fixture(scope="function")
def server_setup_for_function(xprocess) -> Generator[dict[str, Union[str, int]], None, None]:
    """A fixture to start the server for a test function."""
    with server_base(xprocess) as result:
        yield result


@pytest.fixture
def server(xprocess, config_eos, config_default_dirs) -> Generator[str, None, None]:
    """Fixture to start the server.

    Provides URL of the server.
    """
    # create url/port info to the server
    url = "http://0.0.0.0:8503"

    class Starter(ProcessStarter):
        # Set environment before any subprocess run, to keep custom config dir
        env = os.environ.copy()
        env["EOS_DIR"] = str(config_default_dirs[-1])
        project_dir = config_eos.package_root_path.parent.parent

        # assure server to be installed
        try:
            subprocess.run(
                [sys.executable, "-c", "import", "akkudoktoreos.server.eos"],
                check=True,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=project_dir,
            )
        except subprocess.CalledProcessError:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", str(project_dir)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        # command to start server process
        args = [sys.executable, "-m", "akkudoktoreos.server.eos"]

        # will wait for xx seconds before timing out
        timeout = 10

        # xprocess will now attempt to clean up upon interruptions
        terminate_on_interrupt = True

        # checks if our server is ready
        def startup_check(self):
            try:
                result = requests.get(f"{url}/v1/health")
                if result.status_code == 200:
                    return True
            except:
                pass
            return False

    # ensure process is running and return its logfile
    pid, logfile = xprocess.ensure("eos", Starter)
    print(f"View xprocess logfile at: {logfile}")

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
