from http import HTTPStatus

import requests


def test_server(server, config_eos):
    """Test the server."""
    # validate correct path in server
    assert config_eos.data_folder_path is not None
    assert config_eos.data_folder_path.is_dir()

    result = requests.get(f"{server}/v1/config?")
    assert result.status_code == HTTPStatus.OK
