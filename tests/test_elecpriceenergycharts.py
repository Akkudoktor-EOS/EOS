import asyncio
import json
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest
import requests
from loguru import logger

from akkudoktoreos.core.cache import CacheFileStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.prediction.elecfeefixed import ElecFeeFixed
from akkudoktoreos.prediction.elecpriceakkudoktor import (
    AkkudoktorElecPrice,
    AkkudoktorElecPriceValue,
    ElecPriceAkkudoktor,
)
from akkudoktoreos.prediction.elecpriceenergycharts import (
    ElecPriceEnergyCharts,
    EnergyChartsElecPrice,
)
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration

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
    with FILE_TESTDATA_ELECPRICE_ENERGYCHARTS_JSON.open(
        "r", encoding="utf-8", newline=None
    ) as f_res:
        input_data = json.load(f_res)
    """Fixture that returns sample forecast data report."""
    return input_data


@pytest.fixture
def cache_store():
    """A pytest fixture that creates a new CacheFileStore instance for testing."""
    return CacheFileStore()


class TestElecPriceEnergyCharts:
    # ------------------------------------------------
    # General forecast
    # ------------------------------------------------

    def test_singleton_instance(self, provider):
        """Test that ElecPriceForecast behaves as a singleton."""
        another_instance = ElecPriceEnergyCharts()
        assert provider is another_instance

    def test_invalid_provider(self, provider, monkeypatch):
        """Test requesting an unsupported provider."""
        monkeypatch.setenv("EOS_ELECPRICE__ELECPRICE_PROVIDER", "<invalid>")
        provider.config.reset_settings()
        assert not provider.enabled()

    # ------------------------------------------------
    # EnergyCharts
    # ------------------------------------------------

    @patch("akkudoktoreos.prediction.elecpriceenergycharts.logger.error")
    def test_validate_data_invalid_format(self, mock_logger, provider):
        """Test validation for invalid Energy-Charts data."""
        invalid_data = '{"invalid": "data"}'
        with pytest.raises(ValueError):
            provider._validate_data(invalid_data)
        mock_logger.assert_called_once_with(mock_logger.call_args[0][0])

    @patch("requests.get")
    def test_request_forecast(self, mock_get, provider, sample_energycharts_json):
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

    @pytest.mark.asyncio
    @patch("requests.get")
    async def test_update_data(self, mock_get, provider, sample_energycharts_json, cache_store):
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
        await provider.update_data(force_enable=True, force_update=True)

        # Assert: Verify the result is as expected
        mock_get.assert_called_once()
        assert (
            len(provider) == 73
        )  # we have 48 datasets in the api response, we want to know 48h into the future. The data we get has already 23h into the future so we need only 25h more. 48+25=73

        # Assert we get hours prioce values by resampling
        np_price_array = await provider.key_to_array(
            key="elecprice_marketprice_wh",
            start_datetime=provider.ems_start_datetime,
            end_datetime=provider.end_datetime,
            fill_method="ffill",
        )
        assert len(np_price_array) == provider.total_hours

    @pytest.mark.asyncio
    @patch("requests.get")
    async def test_update_data_with_incomplete_forecast(self, mock_get, provider):
        """Test `_update_data` with incomplete or missing forecast data."""
        incomplete_data: dict = {
            "license_info": "",
            "unix_seconds": [],
            "price": [],
            "unit": "",
            "deprecated": False
        }
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = json.dumps(incomplete_data)
        mock_get.return_value = mock_response
        logger.info("The following errors are intentional and part of the test.")
        with pytest.raises(ValueError):
            await provider._update_data(force_update=True)

    @pytest.mark.asyncio
    async def test_update_data_keeps_quarter_hour_resolution(self, provider):
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
            await provider._update_data(force_update=True)
        result = await provider.key_to_series(
            key="elecprice_marketprice_wh",
            start_datetime=start,
            end_datetime=start.add(hours=provider.config.prediction.hours),
            interval=to_duration("15 minutes"),
        )
        assert len(result) == provider.config.prediction.hours * 4
        assert result.index.to_series().diff().dropna().dt.total_seconds().unique().tolist() == [900.0]

    @pytest.mark.asyncio
    async def test_update_data_adds_fees(self, provider, config_eos):
        """Build the gross retail price from market price and the matching Module 3 fee."""

        fixed_fees_amt_kwh: float = (
            0.0205 # electricity_tax
            + 0.0132 # concession_fee
            + 0.00446 # kwkg_levy
            + 0.01559 # section_19_levy
            + 0.00941 # offshore_grid_levy
        )

        amt_kwh: list[float] = [ # includes dynamic network fees \
            0.0095 + fixed_fees_amt_kwh,
            0.0953 + fixed_fees_amt_kwh,
            0.1565 + fixed_fees_amt_kwh,
            0.0953 + fixed_fees_amt_kwh,
        ]

        percent_amt: float = 19.0 # VAT %

        config_eos.merge_settings_from_dict(
            {
                "prediction": {
                    "hours": 48,
                },
                "elecfee": {
                    "provider": "ElecFeeFixed",
                    "elecfeefixed": {
                        "consumption_amt_kwh": {
                            "windows": [
                                {"start_time": "00:00", "duration": "7 hours", "value": amt_kwh[0]},
                                {"start_time": "07:00", "duration": "8 hours", "value": amt_kwh[1]},
                                {"start_time": "15:00", "duration": "5 hours", "value": amt_kwh[2]},
                                {"start_time": "20:00", "duration": "4 hours", "value": amt_kwh[3]},
                            ],
                        },
                        "consumption_percent_amt": {
                            "windows": [
                                {"start_time": "00:00", "duration": "24 hours", "value": percent_amt},
                            ],
                        },
                    },
                },
                "elecprice": {
                    "provider": "ElecPriceEnergyCharts",
                    "energycharts": {
                        "apply_fees": True,
                    },
                },
            },
        )

        ems_eos = get_ems()

        start = to_datetime("2026-01-15 00:00:00", in_timezone="Europe/Berlin")
        ems_eos.set_start_datetime(start)

        # Create fees prediction
        await ElecFeeFixed()._update_data(force_update=True)

        timestamps = [start, start.add(hours=7), start.add(hours=15), start.add(hours=20)]
        energy_charts_data = EnergyChartsElecPrice(
            license_info="",
            unix_seconds=[int(timestamp.timestamp()) for timestamp in timestamps],
            price=[100.0] * len(timestamps),
            unit="EUR/MWh",
            deprecated=False,
        )
        with patch.object(provider, "_request_forecast", return_value=energy_charts_data):
            await provider._update_data(force_update=True)
        result = await provider.key_to_series(
            key="elecprice_marketprice_wh",
            start_datetime=start,
            end_datetime=start.add(hours=provider.config.prediction.hours),
            interval=to_duration("15 minutes"),
        )
        result_kwh = result * 1000
        rate_amt = 1.0 + percent_amt / 100.0
        assert result_kwh.iloc[0*4] == pytest.approx((0.1 + amt_kwh[0]) * rate_amt)
        assert result_kwh.iloc[7*4] == pytest.approx((0.1 + amt_kwh[1]) * rate_amt)
        assert result_kwh.iloc[15*4] == pytest.approx((0.1 + amt_kwh[2]) * rate_amt)
        assert result_kwh.iloc[20*4] == pytest.approx((0.1 + amt_kwh[3]) * rate_amt)

    @pytest.mark.parametrize(
        "status_code, exception",
        [(400, requests.exceptions.HTTPError), (500, requests.exceptions.HTTPError), (200, None)],
    )
    @patch("requests.get")
    def test_request_forecast_status_codes(
        self, mock_get, provider, sample_energycharts_json, status_code, exception
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

    @pytest.mark.asyncio
    @patch("requests.get")
    @patch("akkudoktoreos.core.cache.CacheFileStore")
    async def test_cache_integration(self, mock_cache, mock_get, provider, sample_energycharts_json):
        """Test caching of 8-day electricity price data."""
        # Mock response object
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = json.dumps(sample_energycharts_json)
        mock_get.return_value = mock_response

        # Mock cache object
        mock_cache_instance = mock_cache.return_value
        mock_cache_instance.get.return_value = None  # Simulate no cache

        await provider._update_data(force_update=True)
        mock_cache_instance.create.assert_called_once()
        mock_cache_instance.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_key_to_array_resampling(self, provider):
        """Test resampling of forecast data to NumPy array."""
        await provider.update_data(force_update=True)
        array = await provider.key_to_array(
            key="elecprice_marketprice_wh",
            start_datetime=provider.ems_start_datetime,
            end_datetime=provider.end_datetime,
            fill_method="ffill",
        )
        assert isinstance(array, np.ndarray)
        assert len(array) == provider.total_hours

    @patch("requests.get")
    def test_request_forecast_url_bidding_zone_is_value(self, mock_get, provider, sample_energycharts_json):
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
    def test_energycharts_development_forecast_data(self, provider):
        """Fetch data from real Energy-Charts server."""
        # Preset, as this is usually done by update_data()
        provider.ems_start_datetime = to_datetime("2024-10-26 00:00:00")

        energy_charts_data = provider._request_forecast()

        with FILE_TESTDATA_ELECPRICE_ENERGYCHARTS_JSON.open(
            "w", encoding="utf-8", newline="\n"
        ) as f_out:
            json.dump(energy_charts_data, f_out, indent=4)
