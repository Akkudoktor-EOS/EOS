"""Tests for the Tibber electricity price provider."""

import json
from unittest.mock import Mock, patch

import pytest

from akkudoktoreos.core.cache import CacheFileStore
from akkudoktoreos.prediction.elecprice import ElecPriceCommonSettings
from akkudoktoreos.prediction.elecpricetibber import (
    ElecPriceTibber,
    ElecPriceTibberCommonSettings,
    TibberGraphQLResponse,
)
from akkudoktoreos.utils.datetimeutil import to_datetime


@pytest.fixture
def provider(config_eos):
    """Create a fresh Tibber electricity price provider."""
    ElecPriceTibber.reset_instance()
    config_eos.elecprice = ElecPriceCommonSettings(
        provider="ElecPriceTibber",
        tibber=ElecPriceTibberCommonSettings(access_token="token-123", home_id="home-1"),
    )
    return ElecPriceTibber()


@pytest.fixture
def cache_store():
    """Create a cache store for tests that touch cached methods."""
    return CacheFileStore()


@pytest.fixture
def tibber_response_dict():
    """Sample Tibber GraphQL response."""
    return {
        "data": {
            "viewer": {
                "homes": [
                    {
                        "id": "other-home",
                        "currentSubscription": {
                            "priceInfo": {
                                "today": [
                                    {
                                        "startsAt": "2026-07-07T00:00:00.000+02:00",
                                        "total": 0.999,
                                        "energy": 0.111,
                                        "tax": 0.888,
                                    }
                                ],
                                "tomorrow": [],
                            }
                        },
                    },
                    {
                        "id": "home-1",
                        "currentSubscription": {
                            "priceInfo": {
                                "today": [
                                    {
                                        "startsAt": "2026-07-07T01:00:00.000+02:00",
                                        "total": 0.2970716,
                                        "energy": 0.10922,
                                        "tax": 0.1878516,
                                    },
                                    {
                                        "startsAt": "2026-07-07T00:00:00.000+02:00",
                                        "total": 0.3109662,
                                        "energy": 0.12098,
                                        "tax": 0.1899862,
                                    },
                                ],
                                "tomorrow": [
                                    {
                                        "startsAt": "2026-07-08T00:00:00.000+02:00",
                                        "total": 0.30468,
                                        "energy": 0.1162,
                                        "tax": 0.18848,
                                    }
                                ],
                            }
                        },
                    },
                ]
            }
        }
    }


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


def test_missing_home_id_raises(provider, config_eos, tibber_response):
    """A Tibber home id is required for selecting prices."""
    config_eos.elecprice.tibber.home_id = None

    with pytest.raises(ValueError, match="Tibber home_id is required"):
        provider._select_home(tibber_response)


def test_graphql_errors_raise(provider):
    """GraphQL errors are surfaced as ValueError."""
    response = TibberGraphQLResponse.model_validate(
        {"errors": [{"message": "Authentication failed"}]}
    )

    with pytest.raises(ValueError, match="Tibber GraphQL error"):
        provider._select_home(response)


def test_unknown_home_id_raises(provider, config_eos, tibber_response):
    """Configured home id must exist in the Tibber response."""
    config_eos.elecprice.tibber.home_id = "missing-home"

    with pytest.raises(ValueError, match="Tibber home_id not found"):
        provider._select_home(tibber_response)


def test_parse_data_combines_sorts_and_converts_total(provider, tibber_response):
    """Today and tomorrow prices are sorted and converted from EUR/kWh to EUR/Wh."""
    series = provider._parse_data(tibber_response)

    assert list(series.index) == [
        to_datetime("2026-07-07T00:00:00.000+02:00", in_timezone="Europe/Berlin"),
        to_datetime("2026-07-07T01:00:00.000+02:00", in_timezone="Europe/Berlin"),
        to_datetime("2026-07-08T00:00:00.000+02:00", in_timezone="Europe/Berlin"),
    ]
    assert series.iloc[0] == pytest.approx(0.0003109662)
    assert series.iloc[1] == pytest.approx(0.0002970716)
    assert series.iloc[2] == pytest.approx(0.00030468)


def test_update_data_stores_elecprice_marketprice_wh(provider, tibber_response):
    """Parsed Tibber totals are stored in EOS records."""
    with patch.object(provider, "_request_forecast", return_value=tibber_response):
        provider.update_data(force_enable=True, force_update=True)

    series = provider.key_to_series("elecprice_marketprice_wh")

    assert len(series) == 3
    assert series.iloc[0] == pytest.approx(0.0003109662)
    assert series.iloc[1] == pytest.approx(0.0002970716)
    assert series.iloc[2] == pytest.approx(0.00030468)


def test_total_conversion_exact_example(provider):
    """Tibber total 0.311 EUR/kWh is stored as 0.000311 EUR/Wh."""
    response = TibberGraphQLResponse.model_validate(
        {
            "data": {
                "viewer": {
                    "homes": [
                        {
                            "id": "home-1",
                            "currentSubscription": {
                                "priceInfo": {
                                    "today": [
                                        {
                                            "startsAt": "2026-07-07T00:00:00.000+02:00",
                                            "total": 0.311,
                                        }
                                    ],
                                    "tomorrow": [],
                                }
                            },
                        }
                    ]
                }
            }
        }
    )

    series = provider._parse_data(response)

    assert series.iloc[0] == pytest.approx(0.000311)


def test_empty_tomorrow_stores_only_today_and_warns(provider):
    """An empty tomorrow list does not create fake values."""
    response = TibberGraphQLResponse.model_validate(
        {
            "data": {
                "viewer": {
                    "homes": [
                        {
                            "id": "home-1",
                            "currentSubscription": {
                                "priceInfo": {
                                    "today": [
                                        {
                                            "startsAt": "2026-07-07T00:00:00.000+02:00",
                                            "total": 0.3109662,
                                        },
                                        {
                                            "startsAt": "2026-07-07T01:00:00.000+02:00",
                                            "total": 0.2970716,
                                        },
                                    ],
                                    "tomorrow": [],
                                }
                            },
                        }
                    ]
                }
            }
        }
    )

    with patch("akkudoktoreos.prediction.elecpricetibber.logger.warning") as mock_warning:
        series = provider._parse_data(response)

    assert len(series) == 2
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
    assert "total" in kwargs["json"]["query"]
    assert kwargs["timeout"] == 30
