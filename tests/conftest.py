import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from fnmatch import fnmatch
from http import HTTPStatus
from pathlib import Path
from typing import Generator, Optional, Union
from unittest.mock import PropertyMock, patch

import pendulum
import psutil
import pytest
import requests
from _pytest.logging import LogCaptureFixture
from loguru import logger
from xprocess import ProcessStarter, XProcess

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.coreabc import get_config, get_prediction, singletons_init
from akkudoktoreos.core.version import _version_date_hash, version
from akkudoktoreos.server.server import get_default_host

# -----------------------------------------------
# Adapt pytest logging handling to Loguru logging
# -----------------------------------------------

@pytest.fixture
def caplog(caplog: LogCaptureFixture):
    """Propagate Loguru logs to the pytest caplog handler."""
    handler_id = logger.add(
        caplog.handler,
        format="{message}",
        level=0,
        filter=lambda record: record["level"].no >= caplog.handler.level,
        enqueue=False,  # Set to 'True' if your test is spawning child processes.
    )
    yield caplog
    try:
        logger.remove(handler_id)
    except:
        # May already be deleted
        pass


@pytest.fixture
def reportlog(pytestconfig):
    """Propagate Loguru logs to the pytest terminal reporter."""
    logging_plugin = pytestconfig.pluginmanager.getplugin("logging-plugin")
    handler_id = logger.add(logging_plugin.report_handler, format="{message}")
    yield
    try:
        logger.remove(handler_id)
    except:
        # May already be deleted
        pass


@pytest.fixture(autouse=True)
def propagate_logs():
    """Deal with the pytest --log-cli-level command-line flag.

    This option controls the standard logging logs, not loguru ones.
    For this reason, we first install a PropagateHandler for compatibility.
    """
    class PropagateHandler(logging.Handler):
        def emit(self, record):
            if logging.getLogger(record.name).isEnabledFor(record.levelno):
                logging.getLogger(record.name).handle(record)

    logger.remove()
    logger.add(PropagateHandler(), format="{message}")
    yield


@pytest.fixture()
def disable_debug_logging(scope="session", autouse=True):
    """Automatically disable debug logging for all tests."""
    logger.remove()  # Remove all loggers
    logger.add(sys.stderr, level="INFO")  # Only show INFO and above


# -----------------------------------------------
# Provide pytest options for specific test setups
# -----------------------------------------------

def pytest_addoption(parser):
    parser.addoption(
        "--finalize", action="store_true", default=False, help="Run with all tests."
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
def is_finalize(request):
    yield bool(request.config.getoption("--finalize"))


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
def is_ci() -> bool:
    """Returns True if running on GitHub Actions CI, False otherwise."""
    return os.getenv("CI") == "true"


@pytest.fixture
def prediction_eos():
    return get_prediction()


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


# ------------------------------------
# Provide pytest EOS config management
# ------------------------------------


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


@pytest.fixture(autouse=True)
def user_cwd(config_default_dirs):
    """Patch cwd provided by module pathlib.Path.cwd."""
    with patch(
        "pathlib.Path.cwd",
        return_value=config_default_dirs[1],
    ) as user_cwd_patch:
        yield user_cwd_patch


@pytest.fixture(autouse=True)
def user_config_dir(config_default_dirs):
    """Patch user_config_dir provided by module platformdirs."""
    with patch(
        "akkudoktoreos.config.config.user_config_dir",
        return_value=str(config_default_dirs[0]),
    ) as user_dir_patch:
        yield user_dir_patch


@pytest.fixture(autouse=True)
def user_data_dir(config_default_dirs):
    """Patch user_data_dir provided by module platformdirs."""
    with patch(
        "akkudoktoreos.config.config.user_data_dir",
        return_value=str(config_default_dirs[-1] / "data"),
    ) as user_dir_patch:
        yield user_dir_patch


@pytest.fixture
def config_eos_factory(
    disable_debug_logging,
    user_config_dir,
    user_data_dir,
    user_cwd,
    config_default_dirs,
    monkeypatch,
):
    """Factory fixture for creating a fully initialized ``ConfigEOS`` instance.

    Returns a callable that creates a ``ConfigEOS`` singleton with a controlled
    filesystem layout and environment variables. Allows tests to customize which
    pydantic-settings sources are enabled (init, env, dotenv, file, secrets).

    The factory ensures:
    - Required directories exist
    - No pre-existing config files are present
    - Settings are reloaded to respect test-specific configuration
    - Dependent singletons are initialized

    The singleton instance is reset during fixture teardown.
    """
    def _create(init: dict[str, bool] | None = None) -> ConfigEOS:
        init = init or {
            "with_init_settings": True,
            "with_env_settings": True,
            "with_dotenv_settings": False,
            "with_file_settings": False,
            "with_file_secret_settings": False,
        }

        # reset singleton before touching env or config
        ConfigEOS.reset_instance()
        ConfigEOS._init_config_eos = {
            "with_init_settings": True,
            "with_env_settings": True,
            "with_dotenv_settings": True,
            "with_file_settings": True,
            "with_file_secret_settings": True,
        }
        ConfigEOS._config_file_path = None
        ConfigEOS._force_documentation_mode = False

        data_folder_path = config_default_dirs[-1] / "data"
        data_folder_path.mkdir(exist_ok=True)

        config_dir = config_default_dirs[0]
        config_dir.mkdir(exist_ok=True)

        cwd = config_default_dirs[1]
        cwd.mkdir(exist_ok=True)

        monkeypatch.setenv("EOS_CONFIG_DIR", str(config_dir))
        monkeypatch.setenv("EOS_GENERAL__DATA_FOLDER_PATH", str(data_folder_path))
        monkeypatch.setenv("EOS_GENERAL__DATA_CACHE_SUBPATH", "cache")
        monkeypatch.setenv("EOS_GENERAL__DATA_OUTPUT_SUBPATH", "output")

        # Ensure no config files exist
        config_file = config_dir / ConfigEOS.CONFIG_FILE_NAME
        config_file_cwd = cwd / ConfigEOS.CONFIG_FILE_NAME
        assert not config_file.exists()
        assert not config_file_cwd.exists()

        config_eos = get_config(init=init)
        # Ensure newly created configurations are respected
        # Note: Workaround for pydantic_settings and pytest
        config_eos.reset_settings()

        # Check user data directory pathes (config_default_dirs[-1] == data_default_dir_user)
        assert config_eos.general.data_folder_path == data_folder_path
        assert config_eos.general.data_output_subpath == Path("output")
        assert config_eos.cache.subpath == "cache"
        assert config_eos.cache.path() == config_default_dirs[-1] / "data/cache"
        assert config_eos.logging.file_path == config_default_dirs[-1] / "data/output/eos.log"

        # Check config file path
        assert str(config_eos.general.config_file_path) == str(config_file)
        assert config_file.exists()
        assert not config_file_cwd.exists()

        # Initialize all other singletons (if not already initialized)
        singletons_init()

        return config_eos

    yield _create

    # teardown - final safety net
    ConfigEOS.reset_instance()


@pytest.fixture
def config_eos(config_eos_factory) -> ConfigEOS:
    """Fixture to reset EOS config to default values."""
    config_eos = config_eos_factory()
    return config_eos


# ------------------------------------
# Provide pytest EOS server management
# ------------------------------------


def cleanup_eos_eosdash(
    host: str,
    port: int,
    eosdash_host: str,
    eosdash_port: int,
    server_timeout: float = 10.0,
) -> None:
    """Clean up any running EOS and EOSdash processes.

    Args:
        host (str): EOS server host (e.g., "127.0.0.1").
        port (int): Port number used by the EOS process.
        eosdash_hostr (str): EOSdash server host.
        eosdash_port (int): Port used by EOSdash.
        server_timeout (float): Timeout in seconds before giving up.
    """
    server = f"http://{host}:{port}"
    eosdash_server = f"http://{eosdash_host}:{eosdash_port}"

    sigkill = signal.SIGTERM if os.name == "nt" else signal.SIGKILL

    # Attempt to shut down EOS via health endpoint
    try:
        result = requests.get(f"{server}/v1/health", timeout=2)
        if result.status_code == HTTPStatus.OK:
            pid = result.json()["pid"]
            os.kill(pid, sigkill)
            time.sleep(1)
            result = requests.get(f"{server}/v1/health", timeout=2)
            assert result.status_code != HTTPStatus.OK
    except Exception:
        pass

    # Fallback: kill processes bound to the EOS port
    pids: list[int] = []
    for _ in range(int(server_timeout / 3)):
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr.port == port and conn.pid is not None:
                try:
                    process = psutil.Process(conn.pid)
                    cmdline = process.as_dict(attrs=["cmdline"])["cmdline"]
                    if "akkudoktoreos.server.eos" in " ".join(cmdline):
                        pids.append(conn.pid)
                except Exception:
                    pass
        for pid in pids:
            os.kill(pid, sigkill)
        running = False
        for pid in pids:
            try:
                proc = psutil.Process(pid)
                status = proc.status()
                if status != psutil.STATUS_ZOMBIE:
                    running = True
                    break
            except psutil.NoSuchProcess:
                continue
        if not running:
            break
        time.sleep(3)

    # Check for processes still running (maybe zombies).
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            status = proc.status()
            assert status == psutil.STATUS_ZOMBIE, f"Cleanup EOS expected zombie, got {status} for PID {pid}"
        except psutil.NoSuchProcess:
            # Process already reaped (possibly by init/systemd)
            continue

    # Attempt to shut down EOSdash via health endpoint
    for srv in (eosdash_server, "http://127.0.0.1:8504", "http://127.0.0.1:8555"):
        try:
            result = requests.get(f"{srv}/eosdash/health", timeout=2)
            if result.status_code == HTTPStatus.OK:
                pid = result.json()["pid"]
                os.kill(pid, sigkill)
                time.sleep(1)
                result = requests.get(f"{srv}/eosdash/health", timeout=2)
                assert result.status_code != HTTPStatus.OK
        except Exception:
            pass

    # Fallback: kill EOSdash processes bound to known ports
    pids = []
    for _ in range(int(server_timeout / 3)):
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr.port in (eosdash_port, 8504, 8555) and conn.pid is not None:
                try:
                    process = psutil.Process(conn.pid)
                    cmdline = process.as_dict(attrs=["cmdline"])["cmdline"]
                    if "akkudoktoreos.server.eosdash" in " ".join(cmdline):
                        pids.append(conn.pid)
                except Exception:
                    pass
        for pid in pids:
            os.kill(pid, sigkill)
        running = False
        for pid in pids:
            try:
                proc = psutil.Process(pid)
                status = proc.status()
                if status != psutil.STATUS_ZOMBIE:
                    running = True
                    break
            except psutil.NoSuchProcess:
                continue
        if not running:
            break
        time.sleep(3)

    # Check for processes still running (maybe zombies).
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            status = proc.status()
            assert status == psutil.STATUS_ZOMBIE, f"Cleanup EOSdash expected zombie, got {status} for PID {pid}"
        except psutil.NoSuchProcess:
            # Process already reaped (possibly by init/systemd)
            continue


@contextmanager
def server_base(
    xprocess: XProcess,
    extra_env: Optional[dict[str, str]] = None
) -> Generator[dict[str, Union[str, int]], None, None]:
    """Fixture to start the server with temporary EOS_DIR and default config.

    Args:
        xprocess (XProcess): The pytest-xprocess fixture to manage the server process.
        extra_env (Optional[dict[str, str]]): Environment variables to set before server startup.

    Yields:
        dict[str, str]: A dictionary containing:
            - "server" (str): URL of the server.
            - "port": port
            - "eosdash_server": eosdash_server
            - "eosdash_port": eosdash_port
            - "eos_dir" (str): Path to the temporary EOS_DIR.
            - "timeout": server_timeout
    """
    host = get_default_host()
    port = 8503
    server = f"http://{host}:{port}"

    # Port of server may be still blocked by a server usage despite the other server already
    # shut down. CLOSE_WAIT, TIME_WAIT may typically take up to 120 seconds.
    server_timeout = 120

    if extra_env and extra_env.get("EOS_SERVER__EOSDASH_HOST", None):
        eosdash_host = extra_env["EOS_SERVER__EOSDASH_HOST"]
    else:
        eosdash_host = host
    if extra_env and extra_env.get("EOS_SERVER__EOSDASH_PORT", None):
        eosdash_port: int = int(extra_env["EOS_SERVER__EOSDASH_PORT"])
    else:
        eosdash_port = 8504
    eosdash_server = f"http://{eosdash_host}:{eosdash_port}"

    eos_tmp_dir = tempfile.TemporaryDirectory()
    eos_dir = str(eos_tmp_dir.name)
    eos_general_data_folder_path = str(Path(eos_dir) / "data")

    class Starter(ProcessStarter):
        # Set environment for server run
        env = os.environ.copy()
        env["EOS_DIR"] = eos_dir
        env["EOS_CONFIG_DIR"] = eos_dir
        env["EOS_GENERAL__DATA_FOLDER_PATH"] = eos_general_data_folder_path
        if extra_env:
            env.update(extra_env)

        project_dir = Path(__file__).parent.parent

        @staticmethod
        def _ensure_package(env: dict, project_dir: Path) -> None:
            """Ensure 'akkudoktoreos' is importable in this Python environment."""
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
                # If inside a normal venv or uv-managed environment, install in place
                uv_root = os.getenv("UV_VENV_ROOT")  # set by uv if active
                venv_active = hasattr(sys, "real_prefix") or sys.prefix != sys.base_prefix
                if uv_root or venv_active:
                    print("Package not found, installing in current environment...")
                    subprocess.run(
                        [sys.executable, "-m", "pip", "install", "-e", str(project_dir)],
                        check=True,
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=project_dir,
                    )
                else:
                    raise RuntimeError(
                        "Cannot import 'akkudoktoreos.server.eos' in the system Python. "
                        "Activate a virtual environment first."
                    )

        _ensure_package(env, project_dir)

        # Set command to start server process
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
                response = requests.get(f"{server}/v1/health", timeout=10)
                logger.debug(f"[xprocess] Health check: {response.status_code}")
                if response.status_code == 200:
                    return True
                logger.debug(f"[xprocess] Health check: {response}")
            except Exception as e:
                logger.debug(f"[xprocess] Exception during health check: {e}")
            return False

        def wait_callback(self):
            """Assert that process is ready to answer queries using provided
            callback funtion. Will raise TimeoutError if self.callback does not
            return True before self.timeout seconds"""
            from datetime import datetime

            while True:
                time.sleep(1.0)
                if self.startup_check():
                    return True
                if datetime.now() > self._max_time:
                    info = self.process.getinfo("eos")
                    error_msg = (
                        f"The provided startup check could not assert process responsiveness\n"
                        f"within the specified time interval of {self.timeout} seconds.\n"
                        f"Server log is in '{info.logpath}'.\n"
                    )
                    raise TimeoutError(error_msg)

    # Kill all running eos and eosdash process - just to be sure
    cleanup_eos_eosdash(host, port, eosdash_host, eosdash_port, server_timeout)

    # Ensure there is an empty config file in the temporary EOS directory
    config_file_path = Path(eos_dir).joinpath(ConfigEOS.CONFIG_FILE_NAME)
    with config_file_path.open(mode="w", encoding="utf-8", newline="\n") as fd:
        json.dump({}, fd)
    logger.info(f"Created empty config file in {config_file_path}.")

    # ensure process is running and return its logfile
    pid, logfile = xprocess.ensure("eos", Starter)
    logger.info(f"Started EOS ({pid}). This may take very long (up to {server_timeout} seconds).")
    logger.info(f"EOS_DIR: {Starter.env["EOS_DIR"]}, EOS_CONFIG_DIR: {Starter.env["EOS_CONFIG_DIR"]}")
    logger.info(f"View xprocess logfile at: {logfile}")

    yield {
        "server": server,
        "port": port,
        "eosdash_server": eosdash_server,
        "eosdash_port": eosdash_port,
        "eos_dir": eos_dir,
        "timeout": server_timeout,
    }

     # clean up whole process tree afterwards
    xprocess.getinfo("eos").terminate()

    # Cleanup any EOS process left.
    cleanup_eos_eosdash(host, port, eosdash_host, eosdash_port, server_timeout)

    # Remove temporary EOS_DIR
    eos_tmp_dir.cleanup()


@pytest.fixture(scope="class")
def server_setup_for_class(request, xprocess) -> Generator[dict[str, Union[str, int]], None, None]:
    """A fixture to start the server for a test class.

    Get env vars from the test class attribute `eos_env`, if defined
    """
    extra_env = getattr(request.cls, "eos_env", None)

    with server_base(xprocess, extra_env=extra_env) as result:
        yield result


@pytest.fixture(scope="function")
def server_setup_for_function(xprocess) -> Generator[dict[str, Union[str, int]], None, None]:
    """A fixture to start the server for a test function."""
    with server_base(xprocess) as result:
        yield result


# --------------------------------------
# Provide version and hash check support
# --------------------------------------


@pytest.fixture(scope="session")
def version_and_hash() -> Generator[dict[str, Optional[str]], None, None]:
    """Return version info as in in version.py and calculate current hash.

    Runs once per test session.
    """
    info = version()
    _, info["hash_current"] = _version_date_hash()

    yield info

    # After all tests


# ------------------------------
# Provide pytest timezone change
# ------------------------------


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
