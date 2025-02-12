import json
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from akkudoktoreos.core.cache import CacheFileStore
from akkudoktoreos.core.ems import get_ems
from akkudoktoreos.prediction.weatherbrightsky import WeatherBrightSky
from akkudoktoreos.utils.datetimeutil import to_datetime

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")

FILE_TESTDATA_WEATHERBRIGHTSKY_1_JSON = DIR_TESTDATA.joinpath("weatherforecast_brightsky_1.json")
FILE_TESTDATA_WEATHERBRIGHTSKY_2_JSON = DIR_TESTDATA.joinpath("weatherforecast_brightsky_2.json")


@pytest.fixture
def provider(monkeypatch):
    """Fixture to create a WeatherProvider instance."""
    monkeypatch.setenv("EOS_WEATHER__WEATHER_PROVIDER", "BrightSky")
    monkeypatch.setenv("EOS_GENERAL__LATITUDE", "50.0")
    monkeypatch.setenv("EOS_GENERAL__LONGITUDE", "10.0")
    return WeatherBrightSky()


@pytest.fixture
def sample_brightsky_1_json():
    """Fixture that returns sample forecast data report."""
    with FILE_TESTDATA_WEATHERBRIGHTSKY_1_JSON.open("r", encoding="utf-8", newline=None) as f_res:
        input_data = json.load(f_res)
    return input_data


@pytest.fixture
def sample_brightsky_2_json():
    """Fixture that returns sample forecast data report."""
    with FILE_TESTDATA_WEATHERBRIGHTSKY_2_JSON.open("r", encoding="utf-8", newline=None) as f_res:
        input_data = json.load(f_res)
    return input_data


@pytest.fixture
def cache_store():
    """A pytest fixture that creates a new CacheFileStore instance for testing."""
    return CacheFileStore()


# ------------------------------------------------
# General forecast
# ------------------------------------------------


def test_singleton_instance(provider):
    """Test that WeatherForecast behaves as a singleton."""
    another_instance = WeatherBrightSky()
    assert provider is another_instance


def test_invalid_provider(provider, monkeypatch):
    """Test requesting an unsupported provider."""
    monkeypatch.setenv("EOS_WEATHER__WEATHER_PROVIDER", "<invalid>")
    provider.config.reset_settings()
    assert not provider.enabled()


def test_invalid_coordinates(provider, monkeypatch):
    """Test invalid coordinates raise ValueError."""
    monkeypatch.setenv("EOS_GENERAL__LATITUDE", "1000")
    monkeypatch.setenv("EOS_GENERAL__LONGITUDE", "1000")
    with pytest.raises(
        ValueError,  # match="Latitude '1000' and/ or longitude `1000` out of valid range."
    ):
        provider.config.reset_settings()


# ------------------------------------------------
# Irradiance caclulation
# ------------------------------------------------


def test_irridiance_estimate_from_cloud_cover(provider):
    """Test cloud cover to irradiance estimation."""
    cloud_cover_data = pd.Series(
        data=[20, 50, 80], index=pd.date_range("2023-10-22", periods=3, freq="h")
    )

    ghi, dni, dhi = provider.estimate_irradiance_from_cloud_cover(50.0, 10.0, cloud_cover_data)

    assert ghi == [0, 0, 0]
    assert dhi == [0, 0, 0]
    assert dni == [0, 0, 0]


# ------------------------------------------------
# BrightSky
# ------------------------------------------------


@patch("requests.get")
def test_request_forecast(mock_get, provider, sample_brightsky_1_json):
    """Test requesting forecast from BrightSky."""
    # Mock response object
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = json.dumps(sample_brightsky_1_json)
    mock_get.return_value = mock_response

    # Test function
    brightsky_data = provider._request_forecast()

    assert isinstance(brightsky_data, dict)
    assert brightsky_data["weather"][0] == {
        "timestamp": "2024-10-26T00:00:00+02:00",
        "source_id": 46567,
        "precipitation": 0.0,
        "pressure_msl": 1022.9,
        "sunshine": 0.0,
        "temperature": 6.2,
        "wind_direction": 40,
        "wind_speed": 4.7,
        "cloud_cover": 100,
        "dew_point": 5.8,
        "relative_humidity": 97,
        "visibility": 140,
        "wind_gust_direction": 70,
        "wind_gust_speed": 11.9,
        "condition": "dry",
        "precipitation_probability": None,
        "precipitation_probability_6h": None,
        "solar": None,
        "fallback_source_ids": {
            "wind_gust_speed": 219419,
            "pressure_msl": 219419,
            "cloud_cover": 219419,
            "wind_gust_direction": 219419,
            "wind_direction": 219419,
            "wind_speed": 219419,
            "sunshine": 219419,
            "visibility": 219419,
        },
        "icon": "cloudy",
    }


@patch("requests.get")
def test_update_data(mock_get, provider, sample_brightsky_1_json, cache_store):
    """Test fetching forecast from BrightSky."""
    # Mock response object
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = json.dumps(sample_brightsky_1_json)
    mock_get.return_value = mock_response

    cache_store.clear(clear_all=True)

    # Call the method
    ems_eos = get_ems()
    ems_eos.set_start_datetime(to_datetime("2024-10-26 00:00:00", in_timezone="Europe/Berlin"))
    provider.update_data(force_enable=True, force_update=True)

    # Assert: Verify the result is as expected
    mock_get.assert_called_once()
    assert len(provider) == 338

    # with open(FILE_TESTDATA_WEATHERBRIGHTSKY_2_JSON, "w") as f_out:
    #    f_out.write(provider.to_json())


# ------------------------------------------------
# Development BrightSky
# ------------------------------------------------


def test_brightsky_development_forecast_data(provider, config_eos, is_system_test):
    """Fetch data from real BrightSky server."""
    if not is_system_test:
        return

    # Preset, as this is usually done by update_data()
    ems_eos = get_ems()
    ems_eos.set_start_datetime(to_datetime("2024-10-26 00:00:00", in_timezone="Europe/Berlin"))
    config_eos.general.latitude = 50.0
    config_eos.general.longitude = 10.0

    brightsky_data = provider._request_forecast()

    with FILE_TESTDATA_WEATHERBRIGHTSKY_1_JSON.open("w", encoding="utf-8", newline="\n") as f_out:
        json.dump(brightsky_data, f_out, indent=4)
