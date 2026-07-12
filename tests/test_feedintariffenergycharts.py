# ruff: noqa: S101

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.prediction.elecpriceenergycharts import EnergyChartsElecPrice
from akkudoktoreos.prediction.feedintariffenergycharts import FeedInTariffEnergyCharts
from akkudoktoreos.utils.datetimeutil import to_datetime

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")

FILE_TESTDATA_ELECPRICE_ENERGYCHARTS_JSON = DIR_TESTDATA.joinpath(
    "elecpriceforecast_energycharts.json"
)


@pytest.fixture
def sample_energycharts_json():
    with FILE_TESTDATA_ELECPRICE_ENERGYCHARTS_JSON.open(
        "r", encoding="utf-8", newline=None
    ) as f_res:
        return json.load(f_res)


@pytest.fixture
def provider(config_eos):
    config_eos.merge_settings_from_dict(
        {
            "elecprice": {
                "charges_kwh": 0.21,
                "energycharts": {"bidding_zone": "DE-LU"},
            },
            "feedintariff": {
                "provider": "FeedInTariffEnergyCharts",
                "provider_settings": {
                    "FeedInTariffEnergyCharts": {"bidding_zone": "AT"},
                },
            },
        }
    )
    provider = FeedInTariffEnergyCharts()
    provider.highest_orig_datetime = None
    provider.records.clear()
    assert provider.enabled()
    return provider


def test_provider_is_available(config_eos):
    assert "FeedInTariffEnergyCharts" in config_eos.feedintariff.providers


def test_parse_data_uses_raw_market_price(provider, sample_energycharts_json):
    energy_charts_data = EnergyChartsElecPrice.model_validate(sample_energycharts_json)

    series = provider._parse_data(energy_charts_data)

    assert series.iloc[0] == pytest.approx(sample_energycharts_json["price"][0] / 1_000_000)


@patch("requests.get")
def test_request_forecast_uses_feedintariff_bidding_zone(
    mock_get, provider, sample_energycharts_json
):
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = json.dumps(sample_energycharts_json)
    mock_get.return_value = mock_response
    get_ems().set_start_datetime(to_datetime("2024-12-11 00:00:00", in_timezone="Europe/Berlin"))

    provider._request_forecast(start_date="2024-12-10", force_update=True)

    actual_url = mock_get.call_args[0][0]
    assert "bzn=AT" in actual_url
