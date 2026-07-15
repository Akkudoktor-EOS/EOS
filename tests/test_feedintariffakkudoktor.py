import json
from unittest.mock import Mock, patch

import pytest

from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.optimization.genetic.geneticparams import (
    MARKET_PRICE_FEED_IN_TARIFF_PROVIDERS,
)
from akkudoktoreos.prediction.elecpriceakkudoktor import AkkudoktorElecPrice
from akkudoktoreos.prediction.feedintariffakkudoktor import FeedInTariffAkkudoktor
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration


@pytest.fixture
def provider(config_eos):
    config_eos.merge_settings_from_dict(
        {
            "elecprice": {"charges_kwh": 0.30},
            "feedintariff": {"provider": "FeedInTariffAkkudoktor"},
        }
    )
    value = FeedInTariffAkkudoktor()
    value.highest_orig_datetime = None
    value.records.clear()
    assert value.enabled()
    return value


@pytest.fixture
def response_data():
    return {
        "meta": {
            "start_timestamp": "1733871600",
            "end_timestamp": "1733958000",
            "start": "2024-12-11T00:00:00+01:00",
            "end": "2024-12-12T00:00:00+01:00",
        },
        "values": [
            {
                "start_timestamp": 1733871600,
                "end_timestamp": 1733875200,
                "start": "2024-12-11T00:00:00+01:00",
                "end": "2024-12-11T01:00:00+01:00",
                "marketprice": 100.0,
                "unit": "Eur/MWh",
                "marketpriceEurocentPerKWh": 10.0,
            },
            {
                "start_timestamp": 1733875200,
                "end_timestamp": 1733878800,
                "start": "2024-12-11T01:00:00+01:00",
                "end": "2024-12-11T02:00:00+01:00",
                "marketprice": 200.0,
                "unit": "Eur/MWh",
                "marketpriceEurocentPerKWh": 20.0,
            },
        ],
    }


def test_provider_is_available(config_eos):
    assert "FeedInTariffAkkudoktor" in config_eos.feedintariff.providers
    assert "FeedInTariffAkkudoktor" in MARKET_PRICE_FEED_IN_TARIFF_PROVIDERS


def test_parse_data_uses_raw_market_price_without_import_charges(provider, response_data):
    data = AkkudoktorElecPrice.model_validate(response_data)
    series = provider._parse_data(data)
    assert series.iloc[0] == pytest.approx(0.0001)


def test_hourly_prices_are_held_constant_on_quarter_hour_grid(provider, response_data):
    data = AkkudoktorElecPrice.model_validate(response_data)
    provider.key_from_series("feed_in_tariff_wh", provider._parse_data(data))
    start = to_datetime("2024-12-11 00:00:00", in_timezone="Europe/Berlin")

    values = provider.key_to_array(
        key="feed_in_tariff_wh",
        start_datetime=start,
        end_datetime=start + to_duration("2 hours"),
        interval=to_duration("15 minutes"),
        fill_method="ffill",
    )

    assert values.tolist() == pytest.approx([0.0001] * 4 + [0.0002] * 4)


@patch("requests.get")
def test_request_uses_akkudoktor_prices_endpoint(mock_get, provider, response_data):
    response = Mock()
    response.content = json.dumps(response_data)
    response.raise_for_status = Mock()
    mock_get.return_value = response
    get_ems().set_start_datetime(to_datetime("2024-12-11 00:00:00", in_timezone="Europe/Berlin"))

    provider._request_forecast(force_update=True)

    url = mock_get.call_args[0][0]
    assert url.startswith("https://api.akkudoktor.net/prices?")
    assert "tz=Europe/Berlin" in url
    assert mock_get.call_args.kwargs["timeout"] == (5, 20)
