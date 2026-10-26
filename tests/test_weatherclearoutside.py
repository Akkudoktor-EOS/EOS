from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from akkudoktoreos.core.cache import CacheFileStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.prediction.weatherclearoutside import WeatherClearOutside
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")

FILE_TESTDATA_WEATHERCLEAROUTSIDE_1_HTML = DIR_TESTDATA.joinpath("weatherforecast_clearout_1.html")
FILE_TESTDATA_WEATHERCLEAROUTSIDE_1_DATA = DIR_TESTDATA.joinpath("weatherforecast_clearout_1.json")


@pytest.fixture
def provider(config_eos):
    """Fixture to create a WeatherProvider instance."""
    settings = {
        "weather": {
            "provider": "ClearOutside",
        },
        "general": {
            "latitude": 50.0,
            "longitude": 10.0,
        },
    }
    config_eos.merge_settings_from_dict(settings)
    provider = WeatherClearOutside()
    assert provider.enabled()
    provider._db_reset_state()
    return provider


@pytest.fixture
def sample_clearout_1_html():
    """Fixture that returns sample forecast data report."""
    with FILE_TESTDATA_WEATHERCLEAROUTSIDE_1_HTML.open(
        "r", encoding="utf-8", newline=None
    ) as f_res:
        input_data = f_res.read()
    return input_data


@pytest.fixture
def sample_clearout_1_data():
    """Fixture that returns sample forecast data."""
    with FILE_TESTDATA_WEATHERCLEAROUTSIDE_1_DATA.open("r", encoding="utf-8", newline=None) as f_in:
        json_str = f_in.read()
        data = WeatherClearOutside.from_json(json_str)
    return data


@pytest.fixture
def cache_store():
    """A pytest fixture that creates a new CacheFileStore instance for testing."""
    return CacheFileStore()


# ------------------------------------------------
# General WeatherProvider
# ------------------------------------------------


def test_singleton_instance(provider):
    """Test that WeatherForecast behaves as a singleton."""
    another_instance = WeatherClearOutside()
    assert provider is another_instance


def test_invalid_provider(provider, config_eos):
    """Test requesting an unsupported provider."""
    settings = {
        "weather": {
            "provider": "<invalid>",
        }
    }
    with pytest.raises(ValueError, match="not a valid weather provider"):
        config_eos.merge_settings_from_dict(settings)


def test_invalid_coordinates(provider, config_eos):
    """Test invalid coordinates raise ValueError."""
    settings = {
        "weather": {
            "provider": "ClearOutside",
        },
        "general": {
            "latitude": 1000.0,
            "longitude": 1000.0,
        },
    }
    with pytest.raises(
        ValueError,  # match="Latitude '1000' and/ or longitude `1000` out of valid range."
    ):
        config_eos.merge_settings_from_dict(settings)


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
# ClearOutside
# ------------------------------------------------


@patch("requests.get")
def test_request_forecast(mock_get, provider, sample_clearout_1_html, config_eos):
    """Test fetching forecast from ClearOutside."""
    # Mock response object
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = sample_clearout_1_html
    mock_get.return_value = mock_response

    # Preset, as this is usually done by update()
    config_eos.update()

    # Test function
    response = provider._request_forecast()

    assert response.status_code == 200
    assert response.content == sample_clearout_1_html


@pytest.mark.asyncio
@patch("requests.get")
async def test_update_data(mock_get, provider, sample_clearout_1_html, sample_clearout_1_data):
    # Mock response object
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = sample_clearout_1_html
    mock_get.return_value = mock_response

    expected_start = to_datetime("2024-10-26 00:00:00", in_timezone="Europe/Berlin")
    expected_end = to_datetime("2024-10-28 00:00:00", in_timezone="Europe/Berlin")
    expected_keep = to_datetime("2024-10-24 00:00:00", in_timezone="Europe/Berlin")

    # Call the method
    ems_eos = get_ems()
    ems_eos.set_start_datetime(expected_start)
    await provider.update_data()

    # Check for correct prediction time window
    assert provider.config.prediction.hours == 48
    assert provider.config.prediction.historic_hours == 48
    assert compare_datetimes(provider.ems_start_datetime, expected_start).equal
    assert compare_datetimes(provider.end_datetime, expected_end).equal
    assert compare_datetimes(provider.keep_datetime, expected_keep).equal

    # Verify the data
    assert len(provider) == 165  # 6 days, 24 hours per day - 7th day 21 hours

    # Check that specific values match the expected output
    # for i, record in enumerate(weather_data.records):
    #    # Compare datetime and specific values
    #    assert record.datetime == sample_clearout_1_data.records[i].datetime
    #    assert record.data['total_clouds'] == sample_clearout_1_data.records[i].data['total_clouds']
    #    # Check additional weather attributes as necessary


@pytest.mark.asyncio
@pytest.mark.skip(reason="Test fixture to be improved")
@patch("requests.get")
async def test_cache_forecast(mock_get, provider, sample_clearout_1_html, cache_store):
    """Test that ClearOutside forecast data is cached with TTL.

    This can not be tested with mock_get. Mock objects are not pickable and therefor can not be
    cached to a file. Keep it for documentation.
    """
    # Mock response object
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = sample_clearout_1_html
    mock_get.return_value = mock_response

    cache_store.clear(clear_all=True)

    await provider.update_data()
    mock_get.assert_called_once()
    forecast_data_first = provider.to_json()

    await provider.update_data()
    forecast_data_second = provider.to_json()
    # Verify that cache returns the same object without calling the method again
    assert forecast_data_first == forecast_data_second
    # A mock object is not pickable and therefor can not be chached to file
    assert mock_get.call_count == 2
