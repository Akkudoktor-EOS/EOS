from http import HTTPStatus
from pathlib import Path

import requests

from akkudoktoreos.config import CONFIG_FILE_NAME, load_config


def test_fixture_setup(server, tmp_path: Path) -> None:
    """Test if the fixture sets up the server with the env var."""
    # validate correct path in server
    config = load_config(tmp_path, False)
    assert tmp_path.joinpath(CONFIG_FILE_NAME).is_file()
    cache = tmp_path / config.directories.cache
    assert cache.is_dir()


def test_server(server, tmp_path: Path):
    """Test the server."""
    result = requests.get(f"{server}/load_total_simple?year_energy=2000")
    assert result.status_code == HTTPStatus.OK

    config = load_config(tmp_path, False)
    assert len(result.json()) == config.eos.prediction_hours
