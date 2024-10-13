import os
import subprocess
import sys
from pathlib import Path

import pytest
from xprocess import ProcessStarter

from akkudoktoreos.config import EOS_DIR, AppConfig, load_config


@pytest.fixture(name="tmp_config")
def load_config_tmp(tmp_path: Path) -> AppConfig:
    "Creates an AppConfig from default.config.json with a tmp output directory."
    config = load_config(tmp_path)
    config.directories.output = tmp_path
    return config


@pytest.fixture
def server(xprocess, tmp_path: Path):
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
            project_dir = Path(__file__).parent.parent
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", project_dir],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        # command to start server process
        args = [sys.executable, "-m", "akkudoktoreosserver.flask_server"]
        env = {EOS_DIR: f"{tmp_path}", **os.environ.copy()}

        # startup pattern
        pattern = "Debugger PIN:"
        # search the first 30 lines for the startup pattern, if not found
        # a RuntimeError will be raised informing the user
        max_read_lines = 30

        # will wait for 10 seconds before timing out
        timeout = 20

        # xprocess will now attempt to clean up upon interruptions
        terminate_on_interrupt = True

    # ensure process is running and return its logfile
    logfile = xprocess.ensure("akkudoktoreosserver", Starter)

    # create url/port info to the server
    url = "http://127.0.0.1:8503"
    yield url

    # clean up whole process tree afterwards
    xprocess.getinfo("akkudoktoreosserver").terminate()
