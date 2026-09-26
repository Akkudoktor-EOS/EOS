"""Tests for the dvhub.online feed-in tariff provider."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests

from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.prediction.feedintariffdvhubonline import (
    FeedInTariffDvhubOnline,
    FeedInTariffDvhubOnlineCommonSettings,
)
from akkudoktoreos.utils.datetimeutil import to_datetime

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
        {"feedintariff": {"provider": "FeedInTariffDvhubOnline"}, "prediction": {"hours": 2}}
    )
    get_ems().set_start_datetime(to_datetime("2026-07-19T12:00:00+00:00"))
    provider = FeedInTariffDvhubOnline()
    provider.highest_orig_datetime = None
    assert provider.enabled()
    provider._db_reset_state()
    return provider


class TestFeedInTariffDvhubOnline:
    def test_provider_id_registered(self, provider, config_eos):
        assert provider.provider_id() == "FeedInTariffDvhubOnline"
        assert provider.enabled()
        assert "FeedInTariffDvhubOnline" in config_eos.feedintariff.providers

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

    @pytest.mark.asyncio
    async def test_update_stores_only_published_slots(self, provider, monkeypatch):
        monkeypatch.setattr(provider, "_request_forecast", lambda **_: SAMPLE)

        await provider._update_data(force_update=True)

        stored = await provider.key_to_raw_series(key="feed_in_tariff_wh")
        assert len(stored) == 2
        assert stored.tolist() == pytest.approx([42.5 / 1_000_000, -5.69 / 1_000_000])
        assert provider.highest_orig_datetime == to_datetime(SAMPLE["data"][1]["ts"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [requests.HTTPError("400 Bad Request"), ValueError("invalid JSON"), RuntimeError("bug")],
    )
    async def test_update_propagates_failure_despite_existing_history(
        self, provider, monkeypatch, error
    ):
        previous = to_datetime(SAMPLE["data"][0]["ts"])
        provider.highest_orig_datetime = previous

        def fail(**kwargs):
            raise error

        monkeypatch.setattr(provider, "_request_forecast", fail)
        with pytest.raises(type(error), match=str(error)):
            await provider._update_data(force_update=True)
        assert provider.highest_orig_datetime == previous

    @pytest.mark.asyncio
    async def test_update_rejects_empty_response_with_existing_history(self, provider, monkeypatch):
        provider.highest_orig_datetime = to_datetime(SAMPLE["data"][0]["ts"])
        monkeypatch.setattr(provider, "_request_forecast", lambda **_: {"data": []})

        with pytest.raises(ValueError, match="No dvhub.online feed-in tariff data"):
            await provider._update_data(force_update=True)

    @pytest.mark.asyncio
    async def test_update_does_not_mark_failed_storage_as_fresh(self, provider, monkeypatch):
        previous = to_datetime(SAMPLE["data"][0]["ts"])
        provider.highest_orig_datetime = previous
        monkeypatch.setattr(provider, "_request_forecast", lambda **_: SAMPLE)
        monkeypatch.setattr(
            provider, "key_from_series", AsyncMock(side_effect=RuntimeError("storage failed"))
        )

        with pytest.raises(RuntimeError, match="storage failed"):
            await provider._update_data(force_update=True)
        assert provider.highest_orig_datetime == previous


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
