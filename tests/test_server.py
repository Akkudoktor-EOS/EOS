import json
import os
import signal
import time
from http import HTTPStatus
from pathlib import Path

import psutil
import pytest
import requests
from conftest import cleanup_eos_eosdash

from akkudoktoreos.core.version import __version__
from akkudoktoreos.server.server import get_default_host, wait_for_port_free


class TestServer:
    def test_server_setup_for_class(self, server_setup_for_class):
        """Ensure server is started."""
        server = server_setup_for_class["server"]
        eos_dir = server_setup_for_class["eos_dir"]

        # Assure server is running
        result = requests.get(f"{server}/v1/health", timeout=2)
        assert result.status_code == HTTPStatus.OK
        health = result.json()
        assert health["status"] == "alive"
        assert health["version"] == __version__

        result = requests.get(f"{server}/v1/config", timeout=2)
        assert result.status_code == HTTPStatus.OK

        # Get testing config
        config_json = result.json()
        config_folder_path = Path(config_json["general"]["config_folder_path"])
        config_file_path = Path(config_json["general"]["config_file_path"])
        data_folder_path = Path(config_json["general"]["data_folder_path"])
        data_ouput_path = Path(config_json["general"]["data_output_path"])
        # Assure we are working in test environment
        assert str(config_folder_path).startswith(eos_dir)
        assert str(config_file_path).startswith(eos_dir)
        assert str(data_folder_path).startswith(eos_dir)
        assert str(data_ouput_path).startswith(eos_dir)


class TestServerStartStop:
    def test_server_start_eosdash(self, tmpdir):
        """Test the EOSdash server startup from EOS."""
        # Do not use any fixture as this will make pytest the owner of the EOSdash port.
        host = get_default_host()
        port = 8503
        eosdash_host = host
        eosdash_port = 8504
        timeout = 120

        server = f"http://{host}:{port}"
        eosdash_server = f"http://{eosdash_host}:{eosdash_port}"
        eos_dir = str(tmpdir)

        # Cleanup any EOS and EOSdash process left.
        cleanup_eos_eosdash(host, port, eosdash_host, eosdash_port, timeout)

        # Import after test setup to prevent creation of config file before test
        from akkudoktoreos.server.eos import start_eosdash

        # Port may be blocked
        assert wait_for_port_free(eosdash_port, timeout=120, waiting_app_name="EOSdash")

        process = start_eosdash(
            host=eosdash_host,
            port=eosdash_port,
            eos_host=host,
            eos_port=port,
            log_level="DEBUG",
            access_log=False,
            reload=False,
            eos_dir=eos_dir,
            eos_config_dir=eos_dir,
        )

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
        health = result.json()
        assert health["status"] == "alive"
        assert health["version"] == __version__

        # Shutdown eosdash
        try:
            result = requests.get(f"{eosdash_server}/eosdash/health", timeout=2)
            if result.status_code == HTTPStatus.OK:
                pid = result.json()["pid"]
                os.kill(pid, signal.SIGTERM)
                time.sleep(1)
                result = requests.get(f"{eosdash_server}/eosdash/health", timeout=2)
                assert result.status_code != HTTPStatus.OK
        except:
            pass

        # Cleanup any EOS and EOSdash process left.
        cleanup_eos_eosdash(host, port, eosdash_host, eosdash_port, timeout)

    @pytest.mark.skipif(os.name == "nt", reason="Server restart not supported on Windows")
    def test_server_restart(self, server_setup_for_function, is_system_test):
        """Test server restart."""
        server = server_setup_for_function["server"]
        eos_dir = server_setup_for_function["eos_dir"]
        timeout = server_setup_for_function["timeout"]

        result = requests.get(f"{server}/v1/config")
        assert result.status_code == HTTPStatus.OK

        # Get testing config
        config_json = result.json()
        config_folder_path = Path(config_json["general"]["config_folder_path"])
        config_file_path = Path(config_json["general"]["config_file_path"])
        data_folder_path = Path(config_json["general"]["data_folder_path"])
        data_ouput_path = Path(config_json["general"]["data_output_path"])
        cache_file_path = data_folder_path.joinpath(config_json["cache"]["subpath"]).joinpath(
            "cachefilestore.json"
        )
        # Assure we are working in test environment
        assert str(config_folder_path).startswith(eos_dir)
        assert str(config_file_path).startswith(eos_dir)
        assert str(data_folder_path).startswith(eos_dir)
        assert str(data_ouput_path).startswith(eos_dir)

        if is_system_test:
            # Prepare cache entry and get cached data
            result = requests.put(f"{server}/v1/config/weather/provider", json="BrightSky")
            assert result.status_code == HTTPStatus.OK

            result = requests.post(f"{server}/v1/prediction/update/BrightSky")
            assert result.status_code == HTTPStatus.OK

            result = requests.get(f"{server}/v1/prediction/series?key=weather_temp_air")
            assert result.status_code == HTTPStatus.OK
            data = result.json()
            assert data["data"] != {}

            result = requests.put(f"{server}/v1/config/file")
            assert result.status_code == HTTPStatus.OK

        # Save cache
        result = requests.post(f"{server}/v1/admin/cache/save")
        assert result.status_code == HTTPStatus.OK
        cache = result.json()

        assert cache_file_path.exists()

        result = requests.get(f"{server}/v1/admin/cache")
        assert result.status_code == HTTPStatus.OK
        cache = result.json()

        result = requests.get(f"{server}/v1/health")
        assert result.status_code == HTTPStatus.OK
        pid = result.json()["pid"]

        result = requests.post(f"{server}/v1/admin/server/restart")
        assert result.status_code == HTTPStatus.OK
        assert "Restarting EOS.." in result.json()["message"]
        new_pid = result.json()["pid"]

        # Wait for server to shut down
        for retries in range(10):
            try:
                result = requests.get(f"{server}/v1/health", timeout=2)
                if result.status_code == HTTPStatus.OK:
                    pid = result.json()["pid"]
                    if pid == new_pid:
                        # Already started
                        break
                else:
                    break
            except Exception as ex:
                break
            time.sleep(3)

        # Assure EOS is up again
        startup = False
        error = ""
        for retries in range(int(timeout / 5)):
            try:
                result = requests.get(f"{server}/v1/health", timeout=2)
                if result.status_code == HTTPStatus.OK:
                    startup = True
                    break
                error = f"{result.status_code}, {str(result.content)}"
            except Exception as ex:
                error = str(ex)
            time.sleep(5)

        assert startup, f"Connection to {server}/v1/health failed: {error}"
        assert result.json()["status"] == "alive"
        pid = result.json()["pid"]
        assert pid == new_pid

        result = requests.get(f"{server}/v1/admin/cache")
        assert result.status_code == HTTPStatus.OK
        new_cache = result.json()

        assert cache.items() <= new_cache.items()

        if is_system_test:
            result = requests.get(f"{server}/v1/config")
            assert result.status_code == HTTPStatus.OK
            assert result.json()["weather"]["provider"] == "BrightSky"

            # Wait for initialisation task to have finished
            time.sleep(5)

            result = requests.get(f"{server}/v1/prediction/series?key=weather_temp_air")
            assert result.status_code == HTTPStatus.OK
            assert result.json() == data

        # Shutdown the newly created server
        result = requests.post(f"{server}/v1/admin/server/shutdown")
        assert result.status_code == HTTPStatus.OK
        assert "Stopping EOS.." in result.json()["message"]
        new_pid = result.json()["pid"]


class TestServerWithEnv:
    eos_env = {
        "EOS_SERVER__EOSDASH_PORT": "8555",
    }

    def test_server_setup_for_class(self, server_setup_for_class):
        """Ensure server is started with environment passed to configuration."""
        server = server_setup_for_class["server"]

        assert server_setup_for_class["eosdash_port"] == int(self.eos_env["EOS_SERVER__EOSDASH_PORT"])

        result = requests.get(f"{server}/v1/config")
        assert result.status_code == HTTPStatus.OK

        # Get testing config
        config_json = result.json()

        # Assure config got configuration from environment
        assert config_json["server"]["eosdash_port"] == int(self.eos_env["EOS_SERVER__EOSDASH_PORT"])
