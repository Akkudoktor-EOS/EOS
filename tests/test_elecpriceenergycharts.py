import json
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pytest
import requests
from loguru import logger

from akkudoktoreos.core.cache import CacheFileStore
from akkudoktoreos.config.configabc import ValueTimeWindowSequence
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.prediction.elecpriceakkudoktor import (
    AkkudoktorElecPrice,
    AkkudoktorElecPriceValue,
    ElecPriceAkkudoktor,
)
from akkudoktoreos.prediction.elecpriceenergycharts import (
    ElecPriceEnergyCharts,
    EnergyChartsElecPrice,
)
from akkudoktoreos.utils.datetimeutil import to_datetime

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")

FILE_TESTDATA_ELECPRICE_ENERGYCHARTS_JSON = DIR_TESTDATA.joinpath(
    "elecpriceforecast_energycharts.json"
)


@pytest.fixture
def provider(monkeypatch, config_eos):
    """Fixture to create a ElecPriceProvider instance."""
    monkeypatch.setenv("EOS_ELECPRICE__ELECPRICE_PROVIDER", "ElecPriceEnergyCharts")
    config_eos.reset_settings()
    return ElecPriceEnergyCharts()


@pytest.fixture
def sample_energycharts_json():
    """Fixture that returns sample forecast data report."""
    with FILE_TESTDATA_ELECPRICE_ENERGYCHARTS_JSON.open(
        "r", encoding="utf-8", newline=None
    ) as f_res:
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
    """Test that ElecPriceForecast behaves as a singleton."""
    another_instance = ElecPriceEnergyCharts()
    assert provider is another_instance


def test_keeps_weekly_price_history(provider):
    """Retain enough native-resolution values for the weekly ETS forecast."""
    assert provider.historic_hours_min() == 24 * 35


def test_invalid_provider(provider, monkeypatch):
    """Test requesting an unsupported provider."""
    monkeypatch.setenv("EOS_ELECPRICE__ELECPRICE_PROVIDER", "<invalid>")
    provider.config.reset_settings()
    assert not provider.enabled()


# ------------------------------------------------
# Akkudoktor
# ------------------------------------------------


@patch("akkudoktoreos.prediction.elecpriceenergycharts.logger.error")
def test_validate_data_invalid_format(mock_logger, provider):
    """Test validation for invalid Energy-Charts data."""
    invalid_data = '{"invalid": "data"}'
    with pytest.raises(ValueError):
        provider._validate_data(invalid_data)
    mock_logger.assert_called_once_with(mock_logger.call_args[0][0])


@patch("requests.get")
def test_request_forecast(mock_get, provider, sample_energycharts_json):
    """Test requesting forecast from Energy-Charts."""
    # Mock response object
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = json.dumps(sample_energycharts_json)
    mock_get.return_value = mock_response

    # Test function
    energy_charts_data = provider._request_forecast()

    assert isinstance(energy_charts_data, EnergyChartsElecPrice)
    assert energy_charts_data.unix_seconds[0] == 1733785200
    assert energy_charts_data.price[0] == 92.85


@patch("requests.get")
def test_update_data(mock_get, provider, sample_energycharts_json, cache_store):
    """Test fetching forecast from Energy-Charts."""
    # Mock response object
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = json.dumps(sample_energycharts_json)
    mock_get.return_value = mock_response

    cache_store.clear(clear_all=True)

    # Call the method
    ems_eos = get_ems()
    ems_eos.set_start_datetime(to_datetime("2024-12-11 00:00:00", in_timezone="Europe/Berlin"))
    provider.update_data(force_enable=True, force_update=True)

    # Assert: Verify the result is as expected
    mock_get.assert_called_once()
    assert len(provider) == 72
    # The final raw timestamp already represents its complete interval. Thus the
    # 48 API values need 24, rather than 25, additional hourly forecasts.

    # Assert we get hours prioce values by resampling
    np_price_array = provider.key_to_array(
        key="elecprice_marketprice_wh",
        start_datetime=provider.ems_start_datetime,
        end_datetime=provider.end_datetime,
    )
    assert len(np_price_array) == provider.total_hours


def test_update_data_keeps_quarter_hour_resolution(provider):
    # Use a range that does not overlap the hourly fixture data used by the
    # neighbouring tests; the provider is a singleton by design.
    start = to_datetime("2025-01-15 00:00:00", in_timezone="Europe/Berlin")
    get_ems().set_start_datetime(start)
    provider.highest_orig_datetime = None
    raw_slots = provider.config.prediction.hours * 2
    energy_charts_data = EnergyChartsElecPrice(
        license_info="",
        unix_seconds=[int(start.add(minutes=15 * i).timestamp()) for i in range(raw_slots)],
        price=[100.0] * raw_slots,
        unit="EUR/MWh",
        deprecated=False,
    )

    with patch.object(provider, "_request_forecast", return_value=energy_charts_data):
        provider._update_data(force_update=True)

    result = provider.key_to_series(
        key="elecprice_marketprice_wh",
        start_datetime=start,
        end_datetime=start.add(hours=provider.config.prediction.hours),
    )
    assert len(result) == provider.config.prediction.hours * 4
    assert result.index.to_series().diff().dropna().dt.total_seconds().unique().tolist() == [900.0]


def test_update_data_repairs_short_quarter_hour_history(provider):
    """A previously retained 48-hour series is replaced with the full ETS history."""
    start = to_datetime("2026-08-01 00:00:00", in_timezone="Europe/Berlin")
    get_ems().set_start_datetime(start)
    provider.highest_orig_datetime = start.add(hours=24)
    short_history = pd.Series(
        0.0001,
        index=pd.date_range(start=start.subtract(hours=48), periods=192, freq="15min"),
    )
    weekly_history = pd.Series(
        0.0001,
        index=pd.date_range(start=start.subtract(days=35), periods=3204, freq="15min"),
    )
    refreshed_data = EnergyChartsElecPrice(
        license_info="",
        unix_seconds=[int(provider.highest_orig_datetime.timestamp())],
        price=[100.0],
        unit="EUR/MWh",
        deprecated=False,
    )
    predicted_slots = provider.config.prediction.hours * 4 - 97

    with (
        patch.object(
            ElecPriceEnergyCharts,
            "key_to_series",
            side_effect=[short_history, weekly_history],
        ),
        patch.object(
            ElecPriceEnergyCharts, "key_to_array", return_value=weekly_history.to_numpy()
        ),
        patch.object(ElecPriceEnergyCharts, "key_from_series"),
        patch.object(
            ElecPriceEnergyCharts, "_request_forecast", return_value=refreshed_data
        ) as request,
        patch.object(
            ElecPriceEnergyCharts,
            "_predict_ets",
            return_value=np.full(predicted_slots, 0.0001),
        ) as predict,
    ):
        provider._update_data()

    assert request.call_args.kwargs["start_date"] == "2026-06-27"
    assert predict.call_args.kwargs["seasonal_periods"] == 168 * 4


def test_parse_data_adds_constant_charges_variable_network_fees_and_vat(provider):
    """Build the gross retail price from market price and the matching Module 3 fee."""
    provider.config.elecprice.charges_kwh = None
    provider.config.elecprice.charge_components_kwh = {
        "electricity_tax": 0.0205,
        "concession_fee": 0.0132,
        "kwkg_levy": 0.00446,
        "section_19_levy": 0.01559,
        "offshore_grid_levy": 0.00941,
    }
    provider.config.elecprice.vat_rate = 1.19
    provider.config.elecprice.network_fees_kwh = ValueTimeWindowSequence(
        windows=[
            {"start_time": "00:00", "duration": "7 hours", "value": 0.0095},
            {"start_time": "07:00", "duration": "8 hours", "value": 0.0953},
            {"start_time": "15:00", "duration": "5 hours", "value": 0.1565},
            {"start_time": "20:00", "duration": "4 hours", "value": 0.0953},
        ]
    )
    start = to_datetime("2026-01-15 00:00:00", in_timezone="Europe/Berlin")
    timestamps = [start, start.add(hours=7), start.add(hours=15), start.add(hours=20)]
    data = EnergyChartsElecPrice(
        license_info="",
        unix_seconds=[int(timestamp.timestamp()) for timestamp in timestamps],
        price=[100.0] * len(timestamps),
        unit="EUR/MWh",
        deprecated=False,
    )

    result_kwh = provider._parse_data(data) * 1000

    assert result_kwh.iloc[0] == pytest.approx((0.1 + 0.06316 + 0.0095) * 1.19)
    assert result_kwh.iloc[1] == pytest.approx((0.1 + 0.06316 + 0.0953) * 1.19)
    assert result_kwh.iloc[2] == pytest.approx((0.1 + 0.06316 + 0.1565) * 1.19)
    assert result_kwh.iloc[3] == pytest.approx((0.1 + 0.06316 + 0.0953) * 1.19)


def test_market_price_charge_round_trip(provider):
    """Seasonal forecasting can remove and reapply timestamp-dependent retail charges."""
    provider.config.elecprice.charges_kwh = None
    provider.config.elecprice.charge_components_kwh = {"statutory_charges": 0.06316}
    provider.config.elecprice.vat_rate = 1.19
    provider.config.elecprice.network_fees_kwh = ValueTimeWindowSequence(
        windows=[{"start_time": "15:00", "duration": "5 hours", "value": 0.1565}]
    )
    timestamp = to_datetime("2026-01-15 16:30:00", in_timezone="Europe/Berlin")
    market_price_wh = -0.00002

    retail_price_wh = provider._price_with_charges(market_price_wh, timestamp)

    assert provider._price_without_charges(retail_price_wh, timestamp) == pytest.approx(
        market_price_wh
    )


@patch("requests.get")
def test_update_data_with_incomplete_forecast(mock_get, provider):
    """Test `_update_data` with incomplete or missing forecast data."""
    incomplete_data: dict = {
        "license_info": "",
        "unix_seconds": [],
        "price": [],
        "unit": "",
        "deprecated": False,
    }
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = json.dumps(incomplete_data)
    mock_get.return_value = mock_response
    logger.info("The following errors are intentional and part of the test.")
    with pytest.raises(ValueError):
        provider._update_data(force_update=True)


@pytest.mark.parametrize(
    "status_code, exception",
    [(400, requests.exceptions.HTTPError), (500, requests.exceptions.HTTPError), (200, None)],
)
@patch("requests.get")
def test_request_forecast_status_codes(
    mock_get, provider, sample_energycharts_json, status_code, exception
):
    """Test handling of various API status codes."""
    mock_response = Mock()
    mock_response.status_code = status_code
    mock_response.content = json.dumps(sample_energycharts_json)
    mock_response.raise_for_status.side_effect = (
        requests.exceptions.HTTPError if exception else None
    )
    mock_get.return_value = mock_response
    if exception:
        with pytest.raises(exception):
            provider._request_forecast()
    else:
        provider._request_forecast()


@patch("requests.get")
@patch("akkudoktoreos.core.cache.CacheFileStore")
def test_cache_integration(mock_cache, mock_get, provider, sample_energycharts_json):
    """Test caching of 8-day electricity price data."""
    # Mock response object
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = json.dumps(sample_energycharts_json)
    mock_get.return_value = mock_response

    # Mock cache object
    mock_cache_instance = mock_cache.return_value
    mock_cache_instance.get.return_value = None  # Simulate no cache

    provider._update_data(force_update=True)
    mock_cache_instance.create.assert_called_once()
    mock_cache_instance.get.assert_called_once()


def test_key_to_array_resampling(provider):
    """Test resampling of forecast data to NumPy array."""
    provider.update_data(force_update=True)
    array = provider.key_to_array(
        key="elecprice_marketprice_wh",
        start_datetime=provider.ems_start_datetime,
        end_datetime=provider.end_datetime,
    )
    assert isinstance(array, np.ndarray)
    assert len(array) == provider.total_hours


@patch("requests.get")
def test_request_forecast_url_bidding_zone_is_value(mock_get, provider, sample_energycharts_json):
    """Test that the bidding zone in the API URL uses the enum *value* (e.g. 'DE-LU'),
    not the enum repr (e.g. 'EnergyChartsBiddingZones.DE_LU').

    Regression test for: bzn=EnergyChartsBiddingZones.DE_LU appearing in the URL
    instead of bzn=DE-LU, which caused a 400 Bad Request from the Energy-Charts API.
    """
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = json.dumps(sample_energycharts_json)
    mock_get.return_value = mock_response

    provider._request_forecast(force_update=True)

    assert mock_get.called, "requests.get was never called"
    actual_url: str = mock_get.call_args[0][0]

    # Extract the bzn= query parameter value from the URL
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(actual_url)
    query_params = parse_qs(parsed.query)

    assert "bzn" in query_params, f"'bzn' parameter missing from URL: {actual_url}"
    bzn_value = query_params["bzn"][0]

    # Must be the raw enum value, never contain a class name or dot notation
    assert "." not in bzn_value, (
        f"Bidding zone in URL looks like an enum repr: '{bzn_value}'. "
        f"Use .value when building the URL, not str(enum)."
    )
    assert bzn_value == provider.config.elecprice.energycharts.bidding_zone.value, (
        f"Expected bzn='{provider.config.elecprice.energycharts.bidding_zone.value}' "
        f"but got bzn='{bzn_value}' in URL: {actual_url}"
    )


# ------------------------------------------------
# Development Energy Charts
# ------------------------------------------------


@pytest.mark.skip(reason="For development only")
def test_energycharts_development_forecast_data(provider):
    """Fetch data from real Energy-Charts server."""
    # Preset, as this is usually done by update_data()
    provider.ems_start_datetime = to_datetime("2024-10-26 00:00:00")

    energy_charts_data = provider._request_forecast()

    with FILE_TESTDATA_ELECPRICE_ENERGYCHARTS_JSON.open(
        "w", encoding="utf-8", newline="\n"
    ) as f_out:
        json.dump(energy_charts_data, f_out, indent=4)
