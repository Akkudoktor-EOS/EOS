import json
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest
import requests

from akkudoktoreos.core.ems import get_ems
from akkudoktoreos.prediction.elecpriceakkudoktor import (
    AkkudoktorElecPrice,
    AkkudoktorElecPriceValue,
    ElecPriceAkkudoktor,
)
from akkudoktoreos.utils.cacheutil import CacheFileStore
from akkudoktoreos.utils.datetimeutil import to_datetime

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")

FILE_TESTDATA_ELECPRICEAKKUDOKTOR_1_JSON = DIR_TESTDATA.joinpath(
    "elecpriceforecast_akkudoktor_1.json"
)


@pytest.fixture
def elecprice_provider(monkeypatch):
    """Fixture to create a ElecPriceProvider instance."""
    monkeypatch.setenv("elecprice_provider", "ElecPriceAkkudoktor")
    return ElecPriceAkkudoktor()


@pytest.fixture
def sample_akkudoktor_1_json():
    """Fixture that returns sample forecast data report."""
    with open(FILE_TESTDATA_ELECPRICEAKKUDOKTOR_1_JSON, "r") as f_res:
        input_data = json.load(f_res)
    return input_data


@pytest.fixture
def cache_store():
    """A pytest fixture that creates a new CacheFileStore instance for testing."""
    return CacheFileStore()


# ------------------------------------------------
# General forecast
# ------------------------------------------------


def test_singleton_instance(elecprice_provider):
    """Test that ElecPriceForecast behaves as a singleton."""
    another_instance = ElecPriceAkkudoktor()
    assert elecprice_provider is another_instance


def test_invalid_provider(elecprice_provider, monkeypatch):
    """Test requesting an unsupported elecprice_provider."""
    monkeypatch.setenv("elecprice_provider", "<invalid>")
    elecprice_provider.config.update()
    assert elecprice_provider.enabled() == False


# ------------------------------------------------
# Akkudoktor
# ------------------------------------------------


@patch("akkudoktoreos.prediction.elecpriceakkudoktor.logger.error")
def test_validate_data_invalid_format(mock_logger, elecprice_provider):
    """Test validation for invalid Akkudoktor data."""
    invalid_data = '{"invalid": "data"}'
    with pytest.raises(ValueError):
        elecprice_provider._validate_data(invalid_data)
    mock_logger.assert_called_once_with(mock_logger.call_args[0][0])


def test_calculate_weighted_mean(elecprice_provider):
    """Test calculation of weighted mean for electricity prices."""
    elecprice_provider.elecprice_8days = np.random.rand(24, 8) * 100
    price_mean = elecprice_provider._calculate_weighted_mean(day_of_week=2, hour=10)
    assert isinstance(price_mean, float)
    assert not np.isnan(price_mean)
    expected = np.array(
        [
            [1.0, 0.5, 0.25, 0.125, 0.0625, 0.03125, 0.015625, 1.0],
            [0.25, 1.0, 0.5, 0.125, 0.0625, 0.03125, 0.015625, 1.0],
            [0.125, 0.5, 1.0, 0.25, 0.0625, 0.03125, 0.015625, 1.0],
            [0.0625, 0.125, 0.5, 1.0, 0.25, 0.03125, 0.015625, 1.0],
            [0.0625, 0.125, 0.25, 0.5, 1.0, 0.03125, 0.015625, 1.0],
            [0.015625, 0.03125, 0.0625, 0.125, 0.5, 1.0, 0.25, 1.0],
            [0.015625, 0.03125, 0.0625, 0.125, 0.25, 0.5, 1.0, 1.0],
        ]
    )
    np.testing.assert_array_equal(elecprice_provider.elecprice_8days_weights_day_of_week, expected)


@patch("requests.get")
def test_request_forecast(mock_get, elecprice_provider, sample_akkudoktor_1_json):
    """Test requesting forecast from Akkudoktor."""
    # Mock response object
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = json.dumps(sample_akkudoktor_1_json)
    mock_get.return_value = mock_response

    # Preset, as this is usually done by update()
    elecprice_provider.config.update()

    # Test function
    akkudoktor_data = elecprice_provider._request_forecast()

    assert isinstance(akkudoktor_data, AkkudoktorElecPrice)
    assert akkudoktor_data.values[0] == AkkudoktorElecPriceValue(
        start_timestamp=1733871600000,
        end_timestamp=1733875200000,
        start="2024-12-10T23:00:00.000Z",
        end="2024-12-11T00:00:00.000Z",
        marketprice=115.94,
        unit="Eur/MWh",
        marketpriceEurocentPerKWh=11.59,
    )


@patch("requests.get")
def test_update_data(mock_get, elecprice_provider, sample_akkudoktor_1_json, cache_store):
    """Test fetching forecast from Akkudoktor."""
    # Mock response object
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = json.dumps(sample_akkudoktor_1_json)
    mock_get.return_value = mock_response

    cache_store.clear(clear_all=True)

    # Call the method
    ems_eos = get_ems()
    ems_eos.set_start_datetime(to_datetime("2024-12-11 00:00:00", in_timezone="Europe/Berlin"))
    elecprice_provider.update_data(force_enable=True, force_update=True)

    # Assert: Verify the result is as expected
    mock_get.assert_called_once()
    assert len(elecprice_provider) == 49  # prediction hours + 1

    # Assert we get prediction_hours prioce values by resampling
    np_price_array = elecprice_provider.key_to_array(
        key="elecprice_marketprice",
        start_datetime=elecprice_provider.start_datetime,
        end_datetime=elecprice_provider.end_datetime,
    )
    assert len(np_price_array) == elecprice_provider.total_hours

    # with open(FILE_TESTDATA_ELECPRICEAKKUDOKTOR_2_JSON, "w") as f_out:
    #    f_out.write(elecprice_provider.to_json())


@patch("requests.get")
def test_update_data_with_incomplete_forecast(mock_get, elecprice_provider):
    """Test `_update_data` with incomplete or missing forecast data."""
    incomplete_data: dict = {"meta": {}, "values": []}
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = json.dumps(incomplete_data)
    mock_get.return_value = mock_response
    with pytest.raises(ValueError):
        elecprice_provider._update_data(force_update=True)


@pytest.mark.parametrize(
    "status_code, exception",
    [(400, requests.exceptions.HTTPError), (500, requests.exceptions.HTTPError), (200, None)],
)
@patch("requests.get")
def test_request_forecast_status_codes(
    mock_get, elecprice_provider, sample_akkudoktor_1_json, status_code, exception
):
    """Test handling of various API status codes."""
    mock_response = Mock()
    mock_response.status_code = status_code
    mock_response.content = json.dumps(sample_akkudoktor_1_json)
    mock_response.raise_for_status.side_effect = (
        requests.exceptions.HTTPError if exception else None
    )
    mock_get.return_value = mock_response
    if exception:
        with pytest.raises(exception):
            elecprice_provider._request_forecast()
    else:
        elecprice_provider._request_forecast()


@patch("akkudoktoreos.utils.cacheutil.CacheFileStore")
def test_cache_integration(mock_cache, elecprice_provider):
    """Test caching of 8-day electricity price data."""
    mock_cache_instance = mock_cache.return_value
    mock_cache_instance.get.return_value = None  # Simulate no cache
    elecprice_provider._update_data(force_update=True)
    mock_cache_instance.create.assert_called_once()
    mock_cache_instance.get.assert_called_once()


def test_key_to_array_resampling(elecprice_provider):
    """Test resampling of forecast data to NumPy array."""
    elecprice_provider.update_data(force_update=True)
    array = elecprice_provider.key_to_array(
        key="elecprice_marketprice",
        start_datetime=elecprice_provider.start_datetime,
        end_datetime=elecprice_provider.end_datetime,
    )
    assert isinstance(array, np.ndarray)
    assert len(array) == elecprice_provider.total_hours


# ------------------------------------------------
# Development Akkudoktor
# ------------------------------------------------


@pytest.mark.skip(reason="For development only")
def test_akkudoktor_development_forecast_data(elecprice_provider):
    """Fetch data from real Akkudoktor server."""
    # Preset, as this is usually done by update_data()
    elecprice_provider.start_datetime = to_datetime("2024-10-26 00:00:00")

    akkudoktor_data = elecprice_provider._request_forecast()

    with open(FILE_TESTDATA_ELECPRICEAKKUDOKTOR_1_JSON, "w") as f_out:
        json.dump(akkudoktor_data, f_out, indent=4)
