import time
from http import HTTPStatus

import requests


class TestEOSDash:
    def test_eosdash_started(self, server_setup_for_class, is_system_test):
        """Test the EOSdash server is started by EOS server."""
        server = server_setup_for_class["server"]
        eosdash_server = server_setup_for_class["eosdash_server"]
        eos_dir = server_setup_for_class["eos_dir"]
        timeout = server_setup_for_class["timeout"]

        # Assure EOSdash is up
        startup = False
        error = ""
        for retries in range(int(timeout / 3)):
            try:
                result = requests.get(f"{eosdash_server}/eosdash/health", timeout=2)
                if result.status_code == HTTPStatus.OK:
                    startup = True
                    break
                error = f"{result.status_code}, {str(result.content)}"
            except Exception as ex:
                error = str(ex)
            time.sleep(3)
        assert startup, f"Connection to {eosdash_server}/eosdash/health failed: {error}"
        assert result.json()["status"] == "alive"

    def test_eosdash_proxied_by_eos(self, server_setup_for_class, is_system_test):
        """Test the EOSdash server proxied by EOS server."""
        server = server_setup_for_class["server"]
        eos_dir = server_setup_for_class["eos_dir"]
        timeout = server_setup_for_class["timeout"]

        # Assure EOSdash is up
        startup = False
        error = ""
        for retries in range(int(timeout / 3)):
            try:
                result = requests.get(f"{server}/eosdash/health", timeout=2)
                if result.status_code == HTTPStatus.OK:
                    startup = True
                    break
                error = f"{result.status_code}, {str(result.content)}"
            except Exception as ex:
                error = str(ex)
            time.sleep(3)
        assert startup, f"Connection to {server}/eosdash/health failed: {error}"
        assert result.json()["status"] == "alive"
