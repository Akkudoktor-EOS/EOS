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
    result = requests.get(f"{server}/gesamtlast_simple?year_energy=2000&")
    assert result.status_code == HTTPStatus.OK

    config = load_config(tmp_path, False)
    assert len(result.json()) == config.eos.prediction_hours

    result = requests.get(f"{server}/gesamtlast_simple")
    assert result.status_code == HTTPStatus.OK

    result = requests.get(f"{server}/pvforecast")
    assert result.status_code == HTTPStatus.OK

    # Assert that the status code is either 200 OK or 204 No Content
    assert result.status_code in {
        HTTPStatus.OK,
        HTTPStatus.NO_CONTENT,
    }, f"Unexpected status code: {result.status_code}"

    if result.status_code == HTTPStatus.OK:
        # If the status is 200, check that content is returned
        assert result.content, "Expected content, but none was returned."
    elif result.status_code == HTTPStatus.NO_CONTENT:
        # If the status is 204, ensure no content is returned
        assert not result.content, "204 No Content response should have no content."
