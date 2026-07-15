"""Tests for the native quarter-hour Tibber feed-in tariff provider."""

import json
from unittest.mock import Mock, patch

import pytest

from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.optimization.genetic.geneticparams import (
    MARKET_PRICE_FEED_IN_TARIFF_PROVIDERS,
)
from akkudoktoreos.prediction.elecpricetibber import TibberGraphQLResponse
from akkudoktoreos.prediction.feedintarifftibber import FeedInTariffTibber
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration


def _point(starts_at: str, energy: float, total: float = 0.40) -> dict[str, object]:
    return {"startsAt": starts_at, "energy": energy, "total": total}


def _payload(points: list[dict[str, object]]) -> dict[str, object]:
    return {
        "data": {
            "viewer": {
                "homes": [
                    {
                        "id": "home-1",
                        "currentSubscription": {
                            "priceInfo": {"today": points[:4], "tomorrow": points[4:]},
                            "priceInfoRange": {"nodes": points},
                        },
                    }
                ]
            }
        }
    }


@pytest.fixture
def quarter_hour_points():
    return [
        _point(f"2026-07-15T0{index // 4}:{(index % 4) * 15:02d}:00+02:00", 0.10 + index / 100)
        for index in range(8)
    ]


@pytest.fixture
def provider(config_eos):
    FeedInTariffTibber.reset_instance()
    config_eos.merge_settings_from_dict(
        {
            "elecprice": {"tibber": {"access_token": "token-123", "home_id": "home-1"}},
            "feedintariff": {
                "direct_marketing_enabled": True,
                "provider": "FeedInTariffTibber",
            },
            "prediction": {"hours": 2},
        }
    )
    value = FeedInTariffTibber()
    value.highest_orig_datetime = None
    value.records.clear()
    get_ems().set_start_datetime(
        to_datetime("2026-07-15T00:00:00+02:00", in_timezone="Europe/Berlin")
    )
    return value


def test_provider_is_registered_and_used_for_direct_marketing(provider, config_eos):
    assert provider.enabled()
    assert "FeedInTariffTibber" in config_eos.feedintariff.providers
    assert "FeedInTariffTibber" in MARKET_PRICE_FEED_IN_TARIFF_PROVIDERS


def test_parse_uses_energy_component_at_native_quarter_hour_resolution(
    provider, quarter_hour_points
):
    response = TibberGraphQLResponse.model_validate(_payload(quarter_hour_points))

    series = provider._parse_data(response)

    assert series.tolist() == pytest.approx([(0.10 + index / 100) / 1000 for index in range(8)])
    assert series.index.to_series().diff().dropna().dt.total_seconds().unique().tolist() == [900.0]


@patch("requests.post")
def test_request_is_strictly_quarter_hourly_and_requests_energy(
    mock_post, provider, quarter_hour_points
):
    response = Mock()
    response.content = json.dumps(_payload(quarter_hour_points)).encode()
    response.raise_for_status = Mock()
    mock_post.return_value = response

    provider._request_forecast(force_update=True)

    query = mock_post.call_args.kwargs["json"]["query"]
    assert "priceInfo(resolution: QUARTER_HOURLY)" in " ".join(query.split())
    assert "priceInfoRange(resolution: QUARTER_HOURLY" in " ".join(query.split())
    assert "energy" in query
    assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer token-123"


def test_update_keeps_four_distinct_prices_per_hour(provider, quarter_hour_points, monkeypatch):
    response = TibberGraphQLResponse.model_validate(_payload(quarter_hour_points))
    monkeypatch.setattr(provider, "_request_forecast", lambda **_: response)

    provider._update_data(force_update=True)

    start = to_datetime("2026-07-15T00:00:00+02:00", in_timezone="Europe/Berlin")
    prices = provider.key_to_array(
        key="feed_in_tariff_wh",
        start_datetime=start,
        end_datetime=start + to_duration("2 hours"),
        interval=to_duration("15 minutes"),
        fill_method="ffill",
    )
    assert prices.tolist() == pytest.approx([(0.10 + index / 100) / 1000 for index in range(8)])


def test_update_rejects_hourly_tibber_data(provider, monkeypatch):
    hourly = [
        _point("2026-07-15T00:00:00+02:00", 0.10),
        _point("2026-07-15T01:00:00+02:00", 0.11),
    ]
    response = TibberGraphQLResponse.model_validate(_payload(hourly))
    monkeypatch.setattr(provider, "_request_forecast", lambda **_: response)

    with pytest.raises(ValueError, match="requires native 15-minute prices"):
        provider._update_data(force_update=True)
