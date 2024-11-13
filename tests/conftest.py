import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from xprocess import ProcessStarter

from akkudoktoreos.config import EOS_DIR, AppConfig, load_config


@pytest.fixture(name="tmp_config")
def load_config_tmp(tmp_path: Path) -> AppConfig:
    """Creates an AppConfig from default.config.json with a tmp output directory."""
    config = load_config(tmp_path)
    config.directories.output = tmp_path
    return config


@pytest.fixture(autouse=True)
def disable_debug_logging():
    # Temporarily set logging level higher than DEBUG
    logging.disable(logging.DEBUG)
    yield
    # Re-enable logging back to its original state after the test
    logging.disable(logging.NOTSET)


def pytest_addoption(parser):
    parser.addoption(
        "--full-run", action="store_true", default=False, help="Run with all optimization tests."
    )


@pytest.fixture
def is_full_run(request):
    yield bool(request.config.getoption("--full-run"))


@pytest.fixture
def server(xprocess, tmp_path: Path):
    """Fixture to start the server.

    Provides URL of the server.
    """

    class Starter(ProcessStarter):
        # assure server to be installed
        try:
            subprocess.run(
                [sys.executable, "-c", "import akkudoktoreos.server"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError:
            project_dir = Path(__file__).parent.parent
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", project_dir],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        # command to start server process
        args = [sys.executable, "-m", "akkudoktoreos.server.fastapi_server"]
        env = {EOS_DIR: f"{tmp_path}", **os.environ.copy()}

        # startup pattern
        pattern = "Application startup complete."
        # search the first 12 lines for the startup pattern, if not found
        # a RuntimeError will be raised informing the user
        max_read_lines = 30

        # will wait for 30 seconds before timing out
        timeout = 30

        # xprocess will now attempt to clean up upon interruptions
        terminate_on_interrupt = True

    # ensure process is running and return its logfile
    logfile = xprocess.ensure("eos", Starter)

    # create url/port info to the server
    url = "http://127.0.0.1:8503"
    yield url

    # clean up whole process tree afterwards
    xprocess.getinfo("eos").terminate()


@pytest.fixture
def other_timezone():
    """Fixture to temporarily change the timezone.

    Restores the original timezone after the test.
    """
    original_tz = os.environ.get("TZ", None)

    other_tz = "Atlantic/Canary"
    if original_tz == other_tz:
        other_tz = "Asia/Singapore"

    # Change the timezone to another
    os.environ["TZ"] = other_tz
    time.tzset()  # For Unix/Linux to apply the timezone change

    yield os.environ["TZ"]  # Yield control back to the test case

    # Restore the original timezone after the test
    if original_tz:
        os.environ["TZ"] = original_tz
    else:
        del os.environ["TZ"]
    time.tzset()  # Re-apply the original timezone
