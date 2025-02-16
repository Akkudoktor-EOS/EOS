import json
import os
import signal
import time
from http import HTTPStatus
from pathlib import Path

import psutil
import pytest
import requests

from akkudoktoreos.server.server import get_default_host

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")

FILE_TESTDATA_EOSSERVER_CONFIG_1 = DIR_TESTDATA.joinpath("eosserver_config_1.json")


class TestServer:
    def test_server_setup_for_class(self, server_setup_for_class):
        """Ensure server is started."""
        server = server_setup_for_class["server"]
        eos_dir = server_setup_for_class["eos_dir"]

        result = requests.get(f"{server}/v1/config")
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

    def test_prediction_brightsky(self, server_setup_for_class, is_system_test):
        """Test weather prediction by BrightSky."""
        server = server_setup_for_class["server"]
        eos_dir = server_setup_for_class["eos_dir"]

        result = requests.get(f"{server}/v1/config")
        assert result.status_code == HTTPStatus.OK

        # Get testing config
        config_json = result.json()
        config_folder_path = Path(config_json["general"]["config_folder_path"])
        # Assure we are working in test environment
        assert str(config_folder_path).startswith(eos_dir)

        result = requests.put(f"{server}/v1/config/weather/provider", json="BrightSky")
        assert result.status_code == HTTPStatus.OK

        # Assure prediction is enabled
        result = requests.get(f"{server}/v1/prediction/providers?enabled=true")
        assert result.status_code == HTTPStatus.OK
        providers = result.json()
        assert "BrightSky" in providers

        if is_system_test:
            result = requests.post(f"{server}/v1/prediction/update/BrightSky")
            assert result.status_code == HTTPStatus.OK

            result = requests.get(f"{server}/v1/prediction/series?key=weather_temp_air")
            assert result.status_code == HTTPStatus.OK

            data = result.json()
            assert len(data["data"]) > 24

        else:
            pass

    def test_prediction_clearoutside(self, server_setup_for_class, is_system_test):
        """Test weather prediction by ClearOutside."""
        server = server_setup_for_class["server"]
        eos_dir = server_setup_for_class["eos_dir"]

        result = requests.put(f"{server}/v1/config/weather/provider", json="ClearOutside")
        assert result.status_code == HTTPStatus.OK

        # Assure prediction is enabled
        result = requests.get(f"{server}/v1/prediction/providers?enabled=true")
        assert result.status_code == HTTPStatus.OK
        providers = result.json()
        assert "ClearOutside" in providers

        if is_system_test:
            result = requests.post(f"{server}/v1/prediction/update/ClearOutside")
            assert result.status_code == HTTPStatus.OK

            result = requests.get(f"{server}/v1/prediction/series?key=weather_temp_air")
            assert result.status_code == HTTPStatus.OK

            data = result.json()
            assert len(data["data"]) > 24

        else:
            pass

    def test_prediction_pvforecastakkudoktor(self, server_setup_for_class, is_system_test):
        """Test PV prediction by PVForecastAkkudoktor."""
        server = server_setup_for_class["server"]
        eos_dir = server_setup_for_class["eos_dir"]

        # Reset config
        with FILE_TESTDATA_EOSSERVER_CONFIG_1.open("r", encoding="utf-8", newline=None) as fd:
            config = json.load(fd)
        config["pvforecast"]["provider"] = "PVForecastAkkudoktor"
        result = requests.put(f"{server}/v1/config", json=config)
        assert result.status_code == HTTPStatus.OK

        # Assure prediction is enabled
        result = requests.get(f"{server}/v1/prediction/providers?enabled=true")
        assert result.status_code == HTTPStatus.OK
        providers = result.json()
        assert "PVForecastAkkudoktor" in providers

        if is_system_test:
            result = requests.post(f"{server}/v1/prediction/update/PVForecastAkkudoktor")
            assert result.status_code == HTTPStatus.OK

            result = requests.get(f"{server}/v1/prediction/series?key=pvforecast_ac_power")
            assert result.status_code == HTTPStatus.OK

            data = result.json()
            assert len(data["data"]) > 24

        else:
            pass

    def test_prediction_elecpriceakkudoktor(self, server_setup_for_class, is_system_test):
        """Test electricity price prediction by ElecPriceImport."""
        server = server_setup_for_class["server"]
        eos_dir = server_setup_for_class["eos_dir"]

        # Reset config
        with FILE_TESTDATA_EOSSERVER_CONFIG_1.open("r", encoding="utf-8", newline=None) as fd:
            config = json.load(fd)
        config["elecprice"]["provider"] = "ElecPriceAkkudoktor"
        result = requests.put(f"{server}/v1/config", json=config)
        assert result.status_code == HTTPStatus.OK

        # Assure prediction is enabled
        result = requests.get(f"{server}/v1/prediction/providers?enabled=true")
        assert result.status_code == HTTPStatus.OK
        providers = result.json()
        assert "ElecPriceAkkudoktor" in providers

        if is_system_test:
            result = requests.post(f"{server}/v1/prediction/update/ElecPriceAkkudoktor")
            assert result.status_code == HTTPStatus.OK

            result = requests.get(f"{server}/v1/prediction/series?key=elecprice_marketprice_wh")
            assert result.status_code == HTTPStatus.OK

            data = result.json()
            assert len(data["data"]) > 24

        else:
            pass

    def test_prediction_loadakkudoktor(self, server_setup_for_class, is_system_test):
        """Test load prediction by LoadAkkudoktor."""
        server = server_setup_for_class["server"]
        eos_dir = server_setup_for_class["eos_dir"]

        result = requests.put(f"{server}/v1/config/load/provider", json="LoadAkkudoktor")
        assert result.status_code == HTTPStatus.OK

        # Assure prediction is enabled
        result = requests.get(f"{server}/v1/prediction/providers?enabled=true")
        assert result.status_code == HTTPStatus.OK
        providers = result.json()
        assert "LoadAkkudoktor" in providers

        if is_system_test:
            result = requests.post(f"{server}/v1/prediction/update/LoadAkkudoktor")
            assert result.status_code == HTTPStatus.OK

            result = requests.get(f"{server}/v1/prediction/series?key=load_mean")
            assert result.status_code == HTTPStatus.OK

            data = result.json()
            assert len(data["data"]) > 24

        else:
            pass

    def test_admin_cache(self, server_setup_for_class, is_system_test):
        """Test whether cache is reconstructed from cached files."""
        server = server_setup_for_class["server"]
        eos_dir = server_setup_for_class["eos_dir"]

        result = requests.get(f"{server}/v1/admin/cache")
        assert result.status_code == HTTPStatus.OK
        cache = result.json()

        if is_system_test:
            # There should be some cache data
            assert cache != {}

            # Save cache
            result = requests.post(f"{server}/v1/admin/cache/save")
            assert result.status_code == HTTPStatus.OK
            cache_saved = result.json()
            assert cache_saved == cache

            # Clear cache - should clear nothing as all cache files expire in the future
            result = requests.post(f"{server}/v1/admin/cache/clear")
            assert result.status_code == HTTPStatus.OK
            cache_cleared = result.json()
            assert cache_cleared == cache

            # Force clear cache
            result = requests.post(f"{server}/v1/admin/cache/clear?clear_all=true")
            assert result.status_code == HTTPStatus.OK
            cache_cleared = result.json()
            assert cache_cleared == {}

            # Try to load already deleted cache entries
            result = requests.post(f"{server}/v1/admin/cache/load")
            assert result.status_code == HTTPStatus.OK
            cache_loaded = result.json()
            assert cache_loaded == {}

            # Cache should still be empty
            result = requests.get(f"{server}/v1/admin/cache")
            assert result.status_code == HTTPStatus.OK
            cache = result.json()
            assert cache == {}


class TestServerStartStop:
    def test_server_start_eosdash(self, tmpdir):
        """Test the EOSdash server startup from EOS."""
        # Do not use any fixture as this will make pytest the owner of the EOSdash port.
        host = get_default_host()
        if os.name == "nt":
            # Windows does not provide SIGKILL
            sigkill = signal.SIGTERM  # type: ignore[attr-defined]
        else:
            sigkill = signal.SIGKILL  # type: ignore
        port = 8503
        eosdash_port = 8504
        timeout = 120

        server = f"http://{host}:{port}"
        eosdash_server = f"http://{host}:{eosdash_port}"
        eos_dir = str(tmpdir)

        # Cleanup any EOSdash process left.
        try:
            result = requests.get(f"{eosdash_server}/eosdash/health", timeout=2)
            if result.status_code == HTTPStatus.OK:
                pid = result.json()["pid"]
                os.kill(pid, sigkill)
                time.sleep(1)
                result = requests.get(f"{eosdash_server}/eosdash/health", timeout=2)
                assert result.status_code != HTTPStatus.OK
        except:
            pass

        # Wait for EOSdash port to be freed
        process_info: list[dict] = []
        for retries in range(int(timeout / 3)):
            process_info = []
            pids: list[int] = []
            for conn in psutil.net_connections(kind="inet"):
                if conn.laddr.port == eosdash_port:
                    if conn.pid not in pids:
                        # Get fresh process info
                        process = psutil.Process(conn.pid)
                        pids.append(conn.pid)
                        process_info.append(process.as_dict(attrs=["pid", "cmdline"]))
            if len(process_info) == 0:
                break
            time.sleep(3)
        assert len(process_info) == 0

        # Import after test setup to prevent creation of config file before test
        from akkudoktoreos.server.eos import start_eosdash

        process = start_eosdash(
            host=host,
            port=eosdash_port,
            eos_host=host,
            eos_port=port,
            log_level="debug",
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
        assert result.json()["status"] == "alive"

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
        for retries in range(int(timeout / 3)):
            try:
                result = requests.get(f"{server}/v1/health", timeout=2)
                if result.status_code == HTTPStatus.OK:
                    startup = True
                    break
                error = f"{result.status_code}, {str(result.content)}"
            except Exception as ex:
                error = str(ex)
            time.sleep(3)

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
