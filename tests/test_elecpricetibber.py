"""Tests for the Tibber electricity price provider."""

import json
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pytest

from akkudoktoreos.core.cache import CacheFileStore
from akkudoktoreos.prediction.elecprice import ElecPriceCommonSettings
from akkudoktoreos.prediction.elecpricetibber import (
    ElecPriceTibber,
    ElecPriceTibberCommonSettings,
    TibberGraphQLResponse,
)
from akkudoktoreos.utils.datetimeutil import to_datetime


class _FakeEms:
    start_datetime = to_datetime("2026-07-09T00:00:00+00:00")


def _price(starts_at: str, total: float) -> dict[str, object]:
    return {"startsAt": starts_at, "total": total}


def _tibber_payload(
    prices: list[dict[str, object]],
    *,
    home_id: str = "home-1",
    include_other_home: bool = False,
) -> dict[str, object]:
    homes: list[dict[str, object]] = []
    if include_other_home:
        homes.append(
            {
                "id": "other-home",
                "currentSubscription": {
                    "priceInfo": {"today": [_price("2026-07-09T00:00:00+00:00", 0.999)]}
                },
            }
        )

    homes.append(
        {
            "id": home_id,
            "currentSubscription": {
                "priceInfo": {"today": prices[:2], "tomorrow": prices[2:]},
                "priceInfoRange": {"nodes": prices},
            },
        }
    )
    return {"data": {"viewer": {"homes": homes}}}


@pytest.fixture
def provider(config_eos):
    """Create a fresh Tibber electricity price provider."""
    ElecPriceTibber.reset_instance()
    config_eos.elecprice = ElecPriceCommonSettings(
        provider="ElecPriceTibber",
        tibber=ElecPriceTibberCommonSettings(access_token="token-123", home_id="home-1"),
    )
    config_eos.prediction.hours = 6
    provider = ElecPriceTibber()
    provider.records.clear()
    return provider


@pytest.fixture
def tibber_provider(provider, monkeypatch):
    """Create a Tibber provider with a deterministic EMS start time."""
    monkeypatch.setattr("akkudoktoreos.core.coreabc.get_ems", lambda: _FakeEms())
    return provider


@pytest.fixture
def cache_store():
    """Create a cache store for tests that touch cached methods."""
    return CacheFileStore()


@pytest.fixture
def tibber_response_dict():
    """Sample Tibber GraphQL response."""
    return _tibber_payload(
        [
            _price("2026-07-07T01:00:00.000+02:00", 0.2970716),
            _price("2026-07-07T00:00:00.000+02:00", 0.3109662),
            _price("2026-07-08T00:00:00.000+02:00", 0.30468),
        ],
        include_other_home=True,
    )


@pytest.fixture
def tibber_response(tibber_response_dict):
    """Validated sample Tibber GraphQL response."""
    return TibberGraphQLResponse.model_validate(tibber_response_dict)


def test_provider_id(provider):
    """Provider ID is stable."""
    assert provider.provider_id() == "ElecPriceTibber"


def test_enabled_only_for_configured_provider(provider, config_eos):
    """Provider is enabled only when configured as active elecprice provider."""
    assert provider.enabled()

    config_eos.elecprice.provider = "ElecPriceFixed"

    assert not provider.enabled()


def test_config_structure_accepts_tibber_settings():
    """The requested nested Tibber config structure is accepted."""
    settings = ElecPriceCommonSettings.model_validate(
        {
            "provider": "ElecPriceTibber",
            "tibber": {
                "access_token": "token-123",
                "home_id": "home-1",
            },
        }
    )

    assert settings.provider == "ElecPriceTibber"
    assert settings.tibber.access_token == "token-123"
    assert settings.tibber.home_id == "home-1"


def test_missing_access_token_raises(provider, config_eos):
    """A Tibber access token is required before making requests."""
    config_eos.elecprice.tibber.access_token = None

    with pytest.raises(ValueError, match="Tibber access_token is required"):
        provider._request_forecast(force_update=True)


def test_select_home_uses_first_subscription_when_home_id_is_omitted(
    provider, config_eos, tibber_response
):
    """If no home id is configured, the first subscribed Tibber home is used."""
    config_eos.elecprice.tibber.home_id = None

    home = provider._select_home(tibber_response)

    assert home.id == "other-home"


def test_graphql_errors_raise(provider):
    """GraphQL errors are surfaced as ValueError."""
    with pytest.raises(ValueError, match="Tibber GraphQL error"):
        provider._validate_data(json.dumps({"errors": [{"message": "Authentication failed"}]}))


def test_unknown_home_id_raises(provider, config_eos, tibber_response):
    """Configured home id must exist in the Tibber response."""
    config_eos.elecprice.tibber.home_id = "missing-home"

    with pytest.raises(ValueError, match="Tibber home_id not found"):
        provider._select_home(tibber_response)


def test_parse_data_combines_sorts_and_converts_total(provider, tibber_response):
    """Today, tomorrow, and history prices are sorted and converted to EUR/Wh."""
    series = provider._parse_data(tibber_response)

    assert list(series.index) == [
        to_datetime("2026-07-07T00:00:00.000+02:00", in_timezone="Europe/Berlin"),
        to_datetime("2026-07-07T01:00:00.000+02:00", in_timezone="Europe/Berlin"),
        to_datetime("2026-07-08T00:00:00.000+02:00", in_timezone="Europe/Berlin"),
    ]
    assert series.iloc[0] == pytest.approx(0.0003109662)
    assert series.iloc[1] == pytest.approx(0.0002970716)
    assert series.iloc[2] == pytest.approx(0.00030468)


def test_tibber_hourly_series_averages_quarter_hour_prices(provider):
    """Quarter-hour Tibber prices are averaged to hourly EOS prices."""
    index = pd.date_range("2026-07-09T00:00:00+00:00", periods=8, freq="15min")
    series = pd.Series([0.10, 0.30, 0.50, 0.70, 1.0, 1.4, 1.8, 2.2], index=index)

    hourly = provider._hourly_series(series)

    assert hourly.tolist() == pytest.approx([0.40, 1.60])


def test_empty_tomorrow_stores_only_today_and_warns(provider):
    """An empty tomorrow list does not create fake values before forecasting."""
    response = TibberGraphQLResponse.model_validate(
        _tibber_payload([_price("2026-07-07T00:00:00.000+02:00", 0.3109662)])
    )

    with patch("akkudoktoreos.prediction.elecpricetibber.logger.warning") as mock_warning:
        series = provider._parse_data(response)

    assert len(series) == 1
    mock_warning.assert_called_once_with("Tibber tomorrow prices not available yet")


@patch("requests.post")
def test_request_forecast_uses_tibber_graphql_api(
    mock_post,
    provider,
    tibber_response_dict,
    cache_store,
):
    """Request uses Tibber URL, bearer token, and GraphQL query body."""
    cache_store.clear(clear_all=True)
    mock_response = Mock()
    mock_response.content = json.dumps(tibber_response_dict).encode()
    mock_post.return_value = mock_response

    response = provider._request_forecast(force_update=True)

    assert isinstance(response, TibberGraphQLResponse)
    mock_post.assert_called_once()

    _, kwargs = mock_post.call_args
    assert mock_post.call_args.args[0] == "https://api.tibber.com/v1-beta/gql"
    assert kwargs["headers"]["Authorization"] == "Bearer token-123"
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert "query" in kwargs["json"]
    assert "TibberPriceInfo" in kwargs["json"]["query"]
    assert "priceInfoRange" in kwargs["json"]["query"]
    assert "total" in kwargs["json"]["query"]
    assert kwargs["timeout"] == 30


def test_tibber_update_extrapolates_missing_hours_with_seasonal_history(
    tibber_provider, monkeypatch
):
    """Missing Tibber future hours are forecast from seasonal price history."""
    data = TibberGraphQLResponse.model_validate(
        _tibber_payload(
            [
                _price("2026-07-09T00:00:00+00:00", 0.30),
                _price("2026-07-09T01:00:00+00:00", 0.42),
                _price("2026-07-09T02:00:00+00:00", 0.36),
            ]
        )
    )
    monkeypatch.setattr(tibber_provider, "_request_forecast", lambda **_: data)
    monkeypatch.setattr(
        tibber_provider,
        "_predict_ets",
        lambda history, seasonal_periods, hours: np.full(hours, 0.0005),
    )

    history = pd.Series(
        data=np.linspace(0.0002, 0.0004, 169),
        index=pd.date_range("2026-07-01T23:00:00+00:00", periods=169, freq="1h"),
    )
    tibber_provider.key_from_series("elecprice_marketprice_wh", history)

    tibber_provider._update_data(force_update=True)

    prices = tibber_provider.key_to_array(
        key="elecprice_marketprice_wh",
        start_datetime=to_datetime("2026-07-09T00:00:00+00:00"),
        end_datetime=to_datetime("2026-07-09T06:00:00+00:00"),
        fill_method="ffill",
    )

    assert prices.tolist() == pytest.approx([0.0003, 0.00042, 0.00036, 0.0005, 0.0005, 0.0005])
