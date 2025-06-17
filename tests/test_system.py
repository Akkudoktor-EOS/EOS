import json
import os
import signal
import time
from http import HTTPStatus
from pathlib import Path

import pytest
import requests

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")

FILE_TESTDATA_EOSSERVER_CONFIG_1 = DIR_TESTDATA.joinpath("eosserver_config_1.json")


class TestSystem:
    def test_prediction_brightsky(self, server_setup_for_class, is_system_test):
        """Test weather prediction by BrightSky."""
        server = server_setup_for_class["server"]
        eos_dir = server_setup_for_class["eos_dir"]

        result = requests.get(f"{server}/v1/config", timeout=2)
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
            assert result.status_code == HTTPStatus.OK, f"Failed: {result.headers} {result.text}"

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
            assert result.status_code == HTTPStatus.OK, f"Failed: {result.headers} {result.text}"

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

            # Clear expired cache - should clear nothing as all cache files expire in the future
            result = requests.post(f"{server}/v1/admin/cache/clear-expired")
            assert result.status_code == HTTPStatus.OK
            cache_cleared = result.json()
            assert cache_cleared == cache

            # Force clear cache
            result = requests.post(f"{server}/v1/admin/cache/clear")
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
