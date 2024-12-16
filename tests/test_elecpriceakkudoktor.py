import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

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

ems_eos = get_ems()


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
    ems_eos.set_start_datetime(to_datetime("2024-12-11 00:00:00", in_timezone="Europe/Berlin"))
    elecprice_provider.update_data(force_enable=True, force_update=True)

    # Assert: Verify the result is as expected
    mock_get.assert_called_once()
    assert len(elecprice_provider) == 25

    # Assert we get prediction_hours prioce values by resampling
    np_price_array = elecprice_provider.key_to_array(
        key="elecprice_marketprice",
        start_datetime=elecprice_provider.start_datetime,
        end_datetime=elecprice_provider.end_datetime,
    )
    assert len(np_price_array) == elecprice_provider.total_hours

    # with open(FILE_TESTDATA_ELECPRICEAKKUDOKTOR_2_JSON, "w") as f_out:
    #    f_out.write(elecprice_provider.to_json())


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
