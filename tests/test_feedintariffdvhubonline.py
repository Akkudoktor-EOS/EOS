"""Tests for the dvhub.online feed-in tariff provider."""

import os
from unittest.mock import MagicMock, patch

import pytest

from akkudoktoreos.optimization.genetic.geneticparams import (
    MARKET_PRICE_FEED_IN_TARIFF_PROVIDERS,
)
from akkudoktoreos.prediction.feedintariffdvhubonline import (
    FeedInTariffDvhubOnline,
    FeedInTariffDvhubOnlineCommonSettings,
)

SAMPLE = {
    "data": [
        {"ts": "2026-07-19T12:00:00.000Z", "price": 42.5},   # EUR/MWh
        {"ts": "2026-07-19T12:15:00.000Z", "price": -5.69},  # negative slot
        {"ts": "bogus", "price": 10.0},                      # malformed -> skipped
        {"ts": "2026-07-19T12:30:00.000Z", "price": "x"},    # malformed -> skipped
    ]
}


@pytest.fixture
def provider(config_eos):
    config_eos.merge_settings_from_dict(
        {"feedintariff": {"provider": "FeedInTariffDvhubOnline"}}
    )
    return FeedInTariffDvhubOnline()


class TestFeedInTariffDvhubOnline:
    def test_provider_id_registered(self, provider, config_eos):
        assert provider.provider_id() == "FeedInTariffDvhubOnline"
        assert provider.enabled()
        assert "FeedInTariffDvhubOnline" in config_eos.feedintariff.providers

    def test_market_price_provider_for_direct_marketing(self):
        """direct_marketing must use this provider's series, never elecprice."""
        assert "FeedInTariffDvhubOnline" in MARKET_PRICE_FEED_IN_TARIFF_PROVIDERS

    def test_parse_data_eur_mwh_to_eur_wh(self, provider):
        series = provider._parse_data(SAMPLE)
        assert len(series) == 2  # malformed entries skipped
        assert series.iloc[0] == pytest.approx(42.5 / 1_000_000)
        assert series.iloc[1] == pytest.approx(-5.69 / 1_000_000)  # negatives kept

    def test_default_settings(self, provider):
        settings = provider._provider_settings()
        assert settings.base_url == "https://dvhub.online"
        assert settings.zone == "DE-LU"

    def test_request_shape_guard(self, provider):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"unexpected": True}
        with patch(
            "akkudoktoreos.prediction.feedintariffdvhubonline.requests.get",
            return_value=response,
        ):
            with pytest.raises(ValueError, match="response shape"):
                provider._request_forecast(
                    start_date="2026-07-19", end_date="2026-07-21", force_update=True
                )


@pytest.mark.skipif(
    os.environ.get("EOS_DVHUB_ONLINE_LIVE") != "1",
    reason="live API smoke — set EOS_DVHUB_ONLINE_LIVE=1 to run",
)
def test_live_api_smoke(provider):
    data = provider._request_forecast(
        start_date="2026-07-19", end_date="2026-07-20", force_update=True
    )
    series = provider._parse_data(data)
    assert not series.empty
