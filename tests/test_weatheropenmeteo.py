import json
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import pytest
from loguru import logger

from akkudoktoreos.core.cache import CacheFileStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.prediction.weatheropenmeteo import WeatherOpenMeteo
from akkudoktoreos.utils.datetimeutil import to_datetime

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")

FILE_TESTDATA_WEATHEROPENMETEO_1_JSON = DIR_TESTDATA.joinpath("weatherforecast_openmeteo_1.json")
FILE_TESTDATA_WEATHEROPENMETEO_2_JSON = DIR_TESTDATA.joinpath("weatherforecast_openmeteo_2.json")


@pytest.fixture
def provider(monkeypatch):
    """Fixture to create a WeatherProvider instance."""
    monkeypatch.setenv("EOS_WEATHER__WEATHER_PROVIDER", "OpenMeteo")
    monkeypatch.setenv("EOS_GENERAL__LATITUDE", "50.0")
    monkeypatch.setenv("EOS_GENERAL__LONGITUDE", "10.0")
    return WeatherOpenMeteo()


@pytest.fixture
def sample_openmeteo_1_json():
    """Fixture that returns sample forecast data report from Open-Meteo."""
    with FILE_TESTDATA_WEATHEROPENMETEO_1_JSON.open("r", encoding="utf-8", newline=None) as f_res:
        input_data = json.load(f_res)
    return input_data


@pytest.fixture
def sample_openmeteo_2_json():
    """Fixture that returns sample processed forecast data from Open-Meteo."""
    with FILE_TESTDATA_WEATHEROPENMETEO_2_JSON.open("r", encoding="utf-8", newline=None) as f_res:
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
    another_instance = WeatherOpenMeteo()
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
# Irradiance calculation
# ------------------------------------------------


def test_irridiance_estimate_from_cloud_cover(provider):
    """Test cloud cover to irradiance estimation (fallback method)."""
    cloud_cover_data = pd.Series(
        data=[20, 50, 80], index=pd.date_range("2023-10-22", periods=3, freq="h")
    )

    ghi, dni, dhi = provider.estimate_irradiance_from_cloud_cover(50.0, 10.0, cloud_cover_data)

    # This is just the fallback method - Open-Meteo normally provides direct values
    assert len(ghi) == 3
    assert len(dni) == 3
    assert len(dhi) == 3
    # Values should be floats (actual values depend on the algorithm)
    assert all(isinstance(x, float) for x in ghi)
    assert all(isinstance(x, float) for x in dni)
    assert all(isinstance(x, float) for x in dhi)


# ------------------------------------------------
# Open-Meteo
# ------------------------------------------------


@patch("requests.get")
def test_request_forecast(mock_get, provider, sample_openmeteo_1_json):
    """Test requesting forecast from Open-Meteo."""
    # Mock response object
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = sample_openmeteo_1_json
    mock_response.content = json.dumps(sample_openmeteo_1_json)
    mock_get.return_value = mock_response

    # Test function
    openmeteo_data = provider._request_forecast()

    # Verify API was called with correct parameters
    mock_get.assert_called_once()
    call_args = mock_get.call_args
    assert call_args[0][0] == "https://api.open-meteo.com/v1/forecast"
    assert "latitude" in call_args[1]["params"]
    assert "longitude" in call_args[1]["params"]
    assert "hourly" in call_args[1]["params"]

    # Verify returned data structure
    assert isinstance(openmeteo_data, dict)
    assert "hourly" in openmeteo_data
    assert "time" in openmeteo_data["hourly"]
    assert "temperature_2m" in openmeteo_data["hourly"]
    assert "shortwave_radiation" in openmeteo_data["hourly"]  # GHI
    assert "direct_radiation" in openmeteo_data["hourly"]     # DNI
    assert "diffuse_radiation" in openmeteo_data["hourly"]    # DHI


@patch("requests.get")
def test_update_data(mock_get, provider, sample_openmeteo_1_json, cache_store):
    """Test fetching and processing forecast from Open-Meteo."""
    # Mock response object
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = sample_openmeteo_1_json
    mock_response.content = json.dumps(sample_openmeteo_1_json)
    mock_get.return_value = mock_response

    cache_store.clear(clear_all=True)

    # Call the method
    ems_eos = get_ems()
    start_datetime = to_datetime("2026-03-02 09:00:00+01:00", in_timezone="Europe/Berlin")
    ems_eos.set_start_datetime(start_datetime)
    provider.update_data(force_enable=True, force_update=True)

    # Assert: Verify the result is as expected
    mock_get.assert_called_once()
    assert len(provider) > 0

    # Verify that direct radiation values were properly mapped
    # Get the first record and check for irradiance values
    value_datetime = to_datetime("2026-03-04 09:00:00+01:00", in_timezone="Europe/Berlin")
    assert provider.key_to_value("weather_ghi", target_datetime=start_datetime) == 21.8
    assert provider.key_to_value("weather_dni", target_datetime=start_datetime) == 1.2
    assert provider.key_to_value("weather_dhi", target_datetime=start_datetime) == 20.5


# ------------------------------------------------
# Test specific Open-Meteo features
# ------------------------------------------------


def test_openmeteo_radiation_mapping(provider):
    """Test that radiation values are correctly mapped from Open-Meteo keys."""
    # Verify mapping contains the radiation fields
    from akkudoktoreos.prediction.weatheropenmeteo import WeatherDataOpenMeteoMapping

    radiation_keys = [item[0] for item in WeatherDataOpenMeteoMapping
                     if item[0] in ['shortwave_radiation', 'direct_radiation', 'diffuse_radiation']]

    assert 'shortwave_radiation' in radiation_keys
    assert 'direct_radiation' in radiation_keys
    assert 'diffuse_radiation' in radiation_keys

    # Verify they map to correct descriptions
    for key, desc, _ in WeatherDataOpenMeteoMapping:
        if key == 'shortwave_radiation':
            assert desc == "Global Horizontal Irradiance (W/m2)"
        elif key == 'direct_radiation':
            assert desc == "Direct Normal Irradiance (W/m2)"
        elif key == 'diffuse_radiation':
            assert desc == "Diffuse Horizontal Irradiance (W/m2)"


def test_openmeteo_unit_conversions(provider):
    """Test that unit conversions are correctly applied."""
    from akkudoktoreos.prediction.weatheropenmeteo import WeatherDataOpenMeteoMapping

    # Check wind speed conversion (m/s to km/h)
    wind_speed_mapping = next(item for item in WeatherDataOpenMeteoMapping
                             if item[0] == 'wind_speed_10m')
    assert wind_speed_mapping[2] == 3.6  # Conversion factor

    # Check pressure conversion (Pa to hPa)
    pressure_mapping = next(item for item in WeatherDataOpenMeteoMapping
                           if item[0] == 'pressure_msl')
    assert pressure_mapping[2] == 0.01  # Conversion factor


@patch("requests.get")
def test_forecast_days_calculation(mock_get, provider, sample_openmeteo_1_json):
    """Test that forecast_days is correctly calculated."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = sample_openmeteo_1_json
    mock_response.content = json.dumps(sample_openmeteo_1_json)
    mock_get.return_value = mock_response

    ems_eos = get_ems()

    # Test with 3 days forecast
    start = to_datetime(in_timezone="Europe/Berlin")
    ems_eos.set_start_datetime(start)

    provider._request_forecast()

    # Check that forecast_days was set correctly
    call_args = mock_get.call_args
    params = call_args[1]["params"]
    assert params["forecast_days"]


# ------------------------------------------------
# Development Open-Meteo
# ------------------------------------------------


def test_openmeteo_development_forecast_data(provider, config_eos, is_system_test):
    """Fetch data from real Open-Meteo server for development purposes."""
    if not is_system_test:
        return

    # Us actual date for forecast (not historic data)
    now = to_datetime(in_timezone="Europe/Berlin")
    start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = start_date + pd.Timedelta(days=3)  # 3 Tage Vorhersage

    ems_eos = get_ems()
    ems_eos.set_start_datetime(start_date)

    config_eos.general.latitude = 50.0
    config_eos.general.longitude = 10.0

    # Fetch raw data from Open-Meteo
    try:
        openmeteo_data = provider._request_forecast()

        # Save raw API response
        with FILE_TESTDATA_WEATHEROPENMETEO_1_JSON.open("w", encoding="utf-8", newline="\n") as f_out:
            json.dump(openmeteo_data, f_out, indent=4)

        # Update and process data
        provider.update_data(force_enable=True, force_update=True)

        # Save processed data
        with FILE_TESTDATA_WEATHEROPENMETEO_2_JSON.open("w", encoding="utf-8", newline="\n") as f_out:
            f_out.write(provider.model_dump_json(indent=4))

        # Verify radiation values
        if len(provider) > 0:
            records = list(provider.data_records.values())

            # Check fo radiation values available
            has_ghi = any(hasattr(r, 'ghi') and r.ghi is not None for r in records)
            has_dni = any(hasattr(r, 'dni') and r.dni is not None for r in records)
            has_dhi = any(hasattr(r, 'dhi') and r.dhi is not None for r in records)

            logger.info(f"Open-Meteo data verification: GHI={has_ghi}, DNI={has_dni}, DHI={has_dhi}")

            # Optional: Check for positive values (at day time)
            daytime_values = [getattr(r, 'ghi', 0) for r in records[:24]
                            if hasattr(r, 'ghi') and r.ghi is not None and r.ghi > 10]
            if daytime_values:
                logger.info(f"Found {len(daytime_values)} positive GHI values")

    except Exception as e:
        logger.error(f"Error fetching Open-Meteo data: {e}")
        # Debug-Ausgabe
        logger.error(f"Request would have been: https://api.open-meteo.com/v1/forecast?latitude=50.0&longitude=10.0&hourly=temperature_2m,relative_humidity_2m,shortwave_radiation&timezone=Europe/Berlin&forecast_days=3")
        raise
