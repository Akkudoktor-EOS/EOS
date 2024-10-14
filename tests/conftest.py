import os
import subprocess
import sys

import pytest
from xprocess import ProcessStarter


def pytest_addoption(parser):
    parser.addoption(
        "--full-run", action="store_true", default=False, help="Run with all optimization tests."
    )


@pytest.fixture
def is_full_run(request):
    yield bool(request.config.getoption("--full-run"))


@pytest.fixture
def server(xprocess):
    class Starter(ProcessStarter):
        # assure server to be installed
        try:
            subprocess.run(
                [sys.executable, "-c", "import akkudoktoreosserver"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError:
            test_dir = os.path.dirname(os.path.realpath(__file__))
            project_dir = os.path.abspath(os.path.join(test_dir, ".."))
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", project_dir],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        # command to start server process
        args = [sys.executable, "-m", "akkudoktoreosserver.flask_server"]

        # startup pattern
        pattern = "Debugger PIN:"
        # search the first 12 lines for the startup pattern, if not found
        # a RuntimeError will be raised informing the user
        max_read_lines = 12

        # will wait for 10 seconds before timing out
        timeout = 30

        # xprocess will now attempt to clean up upon interruptions
        terminate_on_interrupt = True

    # ensure process is running and return its logfile
    logfile = xprocess.ensure("akkudoktoreosserver", Starter)

    # create url/port info to the server
    url = "http://127.0.0.1:8503"
    yield url

    # clean up whole process tree afterwards
    xprocess.getinfo("akkudoktoreosserver").terminate()
