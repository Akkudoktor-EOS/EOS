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

            result = requests.get(f"{server}/v1/prediction/series?key=loadforecast_power_w")
            assert result.status_code == HTTPStatus.OK

            data = result.json()
            assert len(data["data"]) > 24

        else:
            pass

    def test_measurement(self, server_setup_for_class, is_system_test):
        """Test measurement endpoints comprehensively."""
        server = server_setup_for_class["server"]

        # ----------------------------------------------------------------------
        # 1. Setup: Reset config with test measurement keys
        # ----------------------------------------------------------------------
        with FILE_TESTDATA_EOSSERVER_CONFIG_1.open("r", encoding="utf-8", newline=None) as fd:
            config = json.load(fd)

        config.setdefault("measurement", {})
        config["measurement"]["pv_production_emr_keys"] = ["pv1_emr", "pv2_emr"]
        config["measurement"]["load_emr_keys"] = ["load1_emr"]

        result = requests.put(f"{server}/v1/config", json=config)
        assert result.status_code == HTTPStatus.OK, f"Config update failed: {result.text}"

        # ----------------------------------------------------------------------
        # 2. GET /v1/measurement/keys
        # ----------------------------------------------------------------------
        result = requests.get(f"{server}/v1/measurement/keys")
        assert result.status_code == HTTPStatus.OK, f"Failed to get measurement keys: {result.text}"

        keys = result.json()
        assert isinstance(keys, list)
        assert "pv1_emr" in keys
        assert "pv2_emr" in keys
        assert "load1_emr" in keys

        # ----------------------------------------------------------------------
        # 3. PUT /v1/measurement/value
        # ----------------------------------------------------------------------
        # Float value
        result = requests.put(
            f"{server}/v1/measurement/value",
            params={
                "datetime": "2026-03-08T18:00:00Z",
                "key": "pv1_emr",
                "value": "1000.0",
            },
        )
        assert result.status_code == HTTPStatus.OK, f"Failed to PUT float value: {result.text}"
        series_response = result.json()
        # PydanticDateTimeSeries has shape: {"data": {datetime_str: value}, "dtype": str, "tz": str|None}
        assert "data" in series_response
        assert isinstance(series_response["data"], dict)
        assert len(series_response["data"]) >= 1

        # String value that converts to float
        result = requests.put(
            f"{server}/v1/measurement/value",
            params={
                "datetime": "2026-03-08T19:00:00Z",
                "key": "pv1_emr",
                "value": "2000.0",
            },
        )
        assert result.status_code == HTTPStatus.OK, f"Failed to PUT string float value: {result.text}"

        # Non-numeric string value must be rejected
        result = requests.put(
            f"{server}/v1/measurement/value",
            params={
                "datetime": "2026-03-08T20:00:00Z",
                "key": "pv1_emr",
                "value": "not_a_number",
            },
        )
        assert result.status_code == HTTPStatus.BAD_REQUEST, (
            f"Expected 400 for non-numeric string, got {result.status_code}"
        )

        # Non-existent key must be rejected
        result = requests.put(
            f"{server}/v1/measurement/value",
            params={
                "datetime": "2026-03-08T18:00:00Z",
                "key": "non_existent_key",
                "value": "1000.0",
            },
        )
        assert result.status_code == HTTPStatus.NOT_FOUND, (
            f"Expected 404 for unknown key, got {result.status_code}"
        )

        # Missing required parameter (datetime)
        result = requests.put(
            f"{server}/v1/measurement/value",
            params={"key": "pv1_emr", "value": "1000.0"},
        )
        assert result.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, (
            f"Expected 422 for missing datetime, got {result.status_code}"
        )

        # ----------------------------------------------------------------------
        # 4. GET /v1/measurement/series
        # ----------------------------------------------------------------------
        result = requests.get(f"{server}/v1/measurement/series", params={"key": "pv1_emr"})
        assert result.status_code == HTTPStatus.OK, f"Failed to GET series: {result.text}"

        series_response = result.json()
        # PydanticDateTimeSeries: {"data": {datetime_str: value, ...}, "dtype": "float64", "tz": ...}
        assert "data" in series_response
        assert isinstance(series_response["data"], dict)
        assert "dtype" in series_response
        assert len(series_response["data"]) >= 2  # at least the two values inserted above

        # Non-existent key must be rejected
        result = requests.get(
            f"{server}/v1/measurement/series", params={"key": "non_existent_key"}
        )
        assert result.status_code == HTTPStatus.NOT_FOUND, (
            f"Expected 404 for unknown series key, got {result.status_code}"
        )

        # ----------------------------------------------------------------------
        # 5. PUT /v1/measurement/series
        # PydanticDateTimeSeries payload: {"data": {datetime_str: value, ...}, "dtype": "float64", "tz": "UTC"}
        # ----------------------------------------------------------------------
        series_payload = {
            "data": {
                "2026-03-08T10:00:00+00:00": 500.0,
                "2026-03-08T11:00:00+00:00": 600.0,
                "2026-03-08T12:00:00+00:00": 700.0,
            },
            "dtype": "float64",
            "tz": "UTC",
        }
        result = requests.put(
            f"{server}/v1/measurement/series",
            params={"key": "pv2_emr"},
            json=series_payload,
        )
        assert result.status_code == HTTPStatus.OK, f"Failed to PUT series: {result.text}"

        series_response = result.json()
        assert "data" in series_response
        assert isinstance(series_response["data"], dict)
        assert len(series_response["data"]) >= 3

        # Verify the data round-trips correctly
        result = requests.get(f"{server}/v1/measurement/series", params={"key": "pv2_emr"})
        assert result.status_code == HTTPStatus.OK
        fetched = result.json()
        fetched_values = list(fetched["data"].values())
        assert 500.0 in fetched_values
        assert 600.0 in fetched_values
        assert 700.0 in fetched_values

        # Non-existent key must be rejected
        result = requests.put(
            f"{server}/v1/measurement/series",
            params={"key": "non_existent_key"},
            json=series_payload,
        )
        assert result.status_code == HTTPStatus.NOT_FOUND, (
            f"Expected 404 for unknown series PUT key, got {result.status_code}"
        )

        # ----------------------------------------------------------------------
        # 6. PUT /v1/measurement/dataframe
        # PydanticDateTimeDataFrame payload:
        #   {"data": {datetime_str: {"col1": val, ...}, ...}, "dtypes": {}, "tz": ..., "datetime_columns": [...]}
        # ----------------------------------------------------------------------
        dataframe_payload = {
            "data": {
                "2026-03-08T00:00:00+00:00": {"pv1_emr": 100.5, "load1_emr": 50.2},
                "2026-03-08T01:00:00+00:00": {"pv1_emr": 200.3, "load1_emr": 45.1},
                "2026-03-08T02:00:00+00:00": {"pv1_emr": 300.7, "load1_emr": 48.9},
            },
            "dtypes": {"pv1_emr": "float64", "load1_emr": "float64"},
            "tz": "UTC",
            "datetime_columns": [],
        }
        result = requests.put(f"{server}/v1/measurement/dataframe", json=dataframe_payload)
        assert result.status_code == HTTPStatus.OK, f"Failed to PUT dataframe: {result.text}"

        # Verify data was loaded for both columns
        for key in ("pv1_emr", "load1_emr"):
            result = requests.get(f"{server}/v1/measurement/series", params={"key": key})
            assert result.status_code == HTTPStatus.OK, f"Failed to verify series for {key}"
            series_response = result.json()
            assert len(series_response["data"]) >= 3, f"Expected >=3 data points for {key}"

        # Invalid dataframe structure (row columns inconsistent) must be rejected
        invalid_dataframe_payload = {
            "data": {
                "2026-03-08T00:00:00+00:00": {"pv1_emr": 100.0},
                "2026-03-08T01:00:00+00:00": {"pv1_emr": 200.0, "load1_emr": 45.0},  # extra column
            },
            "dtypes": {},
            "tz": "UTC",
            "datetime_columns": [],
        }
        result = requests.put(f"{server}/v1/measurement/dataframe", json=invalid_dataframe_payload)
        assert result.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, (
            f"Expected 422 for inconsistent dataframe columns, got {result.status_code}"
        )

        # ----------------------------------------------------------------------
        # 7. PUT /v1/measurement/data
        # PydanticDateTimeData payload (RootModel):
        #   Dict[str, Union[str, List[Union[float, int, str, None]]]]
        # Columnar format: keys are column names (or special "start_datetime"/"interval"),
        # values are flat lists of equal length. Datetime index is given via start_datetime + interval.
        # ----------------------------------------------------------------------
        data_payload = {
            "start_datetime": "2026-03-09T00:00:00+00:00",
            "interval": "1 hour",
            "pv1_emr": [400.2, 450.1],
            "load1_emr": [60.5, 55.3],
            "pv2_emr": [150.8, 175.2],
        }
        result = requests.put(f"{server}/v1/measurement/data", json=data_payload)
        assert result.status_code == HTTPStatus.OK, f"Failed to PUT data dict: {result.text}"

        # Verify all three keys received the values
        for key, expected_values in (
            ("pv1_emr", [400.2, 450.1]),
            ("load1_emr", [60.5, 55.3]),
            ("pv2_emr", [150.8, 175.2]),
        ):
            result = requests.get(f"{server}/v1/measurement/series", params={"key": key})
            assert result.status_code == HTTPStatus.OK, f"Failed to verify {key} after data PUT"
            fetched = result.json()
            fetched_values = list(fetched["data"].values())
            for expected in expected_values:
                assert expected in fetched_values, (
                    f"Expected {expected} in {key} series, got {fetched_values}"
                )

        # ----------------------------------------------------------------------
        # 8. Edge case: invalid datetime in value PUT
        # ----------------------------------------------------------------------
        result = requests.put(
            f"{server}/v1/measurement/value",
            params={
                "datetime": "not-a-datetime",
                "key": "pv1_emr",
                "value": "1000.0",
            },
        )
        assert result.status_code == HTTPStatus.BAD_REQUEST, (
            f"Expected 400 for invalid datetime, got {result.status_code}"
        )

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
