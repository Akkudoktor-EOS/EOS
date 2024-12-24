from http import HTTPStatus

import requests

from akkudoktoreos.config.config import get_config

config_eos = get_config()


def test_server(server):
    """Test the server."""
    # validate correct path in server
    assert config_eos.data_folder_path is not None
    assert config_eos.data_folder_path.is_dir()

    result = requests.get(f"{server}/v1/config?")
    assert result.status_code == HTTPStatus.OK
