import time
from http import HTTPStatus

import requests


class TestEOSDash:

    def _assert_server_alive(self, base_url: str, timeout: int):
        """Poll the /eosdash/health endpoint until it's alive or timeout reached."""
        startup = False
        error = ""
        result = None

        for _ in range(int(timeout / 3)):
            try:
                result = requests.get(f"{base_url}/eosdash/health", timeout=2)
                if result.status_code == HTTPStatus.OK:
                    startup = True
                    break
                error = f"{result.status_code}, {str(result.content)}"
            except Exception as ex:
                error = str(ex)
            time.sleep(3)

        assert startup, f"Connection to {base_url}/eosdash/health failed: {error}"
        assert result is not None
        assert result.json()["status"] == "alive"

    def test_eosdash_started(self, server_setup_for_class, is_system_test):
        """Test the EOSdash server is started by EOS server."""
        eosdash_server = server_setup_for_class["eosdash_server"]
        timeout = server_setup_for_class["timeout"]
        self._assert_server_alive(eosdash_server, timeout)

    def test_eosdash_proxied_by_eos(self, server_setup_for_class, is_system_test):
        """Test the EOSdash server proxied by EOS server."""
        server = server_setup_for_class["server"]
        timeout = server_setup_for_class["timeout"]
        self._assert_server_alive(server, timeout)
