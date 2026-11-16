# ruff: noqa: S101
"""The feed-in market price can follow the raw series of the electricity price provider."""

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.prediction.elecpriceakkudoktorml import ElecPriceAkkudoktorML
from akkudoktoreos.prediction.feedintariffenergycharts import (
    FeedInTariffEnergyCharts,
    FeedInTariffEnergyChartsCommonSettings,
)
from akkudoktoreos.utils.datetimeutil import to_datetime

START = to_datetime("2026-07-10 00:00:00", in_timezone="Europe/Berlin")
HOURS = 96
RAW_KEY = "elecprice_marketprice_raw_wh"
FEED_RAW_KEY = "feed_in_tariff_raw_wh"


def _raw_series(start, end, resolution="15min", offset=0.0):
    idx = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq=resolution, inclusive="left")
    x = np.arange(len(idx))
    return pd.Series((60.0 + 25.0 * np.sin(x / 9.0) + offset) / 1e6, index=idx)  # EUR/Wh


@pytest.fixture
def providers(config_eos):
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": HOURS},
            "feedintariff": {
                "provider": "FeedInTariffEnergyCharts",
                "direct_marketing_enabled": True,
                "energycharts": {"follow_elecprice": True},
            },
            "elecprice": {"provider": "ElecPriceAkkudoktorML"},
        }
    )
    get_ems().set_start_datetime(START)
    elec = ElecPriceAkkudoktorML()
    elec._db_reset_state()
    feed = FeedInTariffEnergyCharts()
    feed.highest_orig_datetime = None
    feed._db_reset_state()
    return elec, feed


async def _update_feed(feed):
    with patch.object(
        type(feed),
        "_request_forecast",
        side_effect=AssertionError("own market price fetch must not run"),
    ):
        await feed.update_data(force_enable=True, force_update=True)


@pytest.mark.asyncio
async def test_feed_in_raw_equals_elecprice_raw_for_every_slot(providers):
    elec, feed = providers
    end = START + pd.Timedelta(hours=HOURS)
    raw = _raw_series(START - pd.Timedelta(hours=48), end)
    await elec.key_from_series(RAW_KEY, raw)
    # stale values of an earlier own forecast must be replaced
    await feed.key_from_series(FEED_RAW_KEY, _raw_series(START, end, offset=40.0))
    await _update_feed(feed)
    got = await feed.key_to_raw_series(
        FEED_RAW_KEY, start_datetime=START - pd.Timedelta(hours=48), end_datetime=end
    )
    assert len(got) == len(raw)
    assert np.allclose(got.to_numpy(), raw.to_numpy(), atol=1e-12)
    assert (got.index == raw.index).all()


@pytest.mark.asyncio
async def test_gross_feed_in_is_derived_from_the_mirrored_raw_series(providers):
    elec, feed = providers
    end = START + pd.Timedelta(hours=HOURS)
    raw = _raw_series(START - pd.Timedelta(hours=48), end)
    await elec.key_from_series(RAW_KEY, raw)
    await _update_feed(feed)
    gross = await feed.key_to_raw_series(
        "feed_in_tariff_wh", start_datetime=START, end_datetime=end
    )
    expected = raw[raw.index >= pd.Timestamp(START)]
    assert len(gross) == len(expected)
    assert np.allclose(gross.to_numpy(), expected.to_numpy(), atol=1e-12)  # no fees configured


@pytest.mark.asyncio
async def test_mirror_is_off_by_default(providers, config_eos):
    elec, feed = providers
    assert FeedInTariffEnergyChartsCommonSettings().follow_elecprice is False
    config_eos.merge_settings_from_dict(
        {"feedintariff": {"energycharts": {"follow_elecprice": False}}}
    )
    await elec.key_from_series(
        RAW_KEY, _raw_series(START - pd.Timedelta(hours=48), START + pd.Timedelta(hours=HOURS))
    )
    assert await feed._mirror_elecprice_raw() is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("price_resolution", "old_resolution"), [("15min", "1h"), ("1h", "15min")]
)
async def test_mirror_replaces_values_of_another_resolution(
    providers, price_resolution, old_resolution
):
    """Own values of another resolution must not survive between the mirrored slots."""
    elec, feed = providers
    first = START - pd.Timedelta(hours=48)
    end = START + pd.Timedelta(hours=HOURS)
    raw = _raw_series(first, end, resolution=price_resolution)
    await elec.key_from_series(RAW_KEY, raw)
    await feed.key_from_series(
        FEED_RAW_KEY, _raw_series(first, end, resolution=old_resolution, offset=40.0)
    )
    await _update_feed(feed)
    got = await feed.key_to_raw_series(FEED_RAW_KEY, start_datetime=first, end_datetime=end)
    assert len(got) == len(raw)
    assert (got.index == raw.index).all()
    assert np.allclose(got.to_numpy(), raw.to_numpy(), atol=1e-12)


@pytest.mark.asyncio
async def test_mirror_is_skipped_for_a_gap_in_the_forecast_horizon(providers):
    elec, feed = providers
    end = START + pd.Timedelta(hours=HOURS)
    raw = _raw_series(START - pd.Timedelta(hours=48), end)
    gap = (raw.index >= pd.Timestamp(START) + pd.Timedelta(hours=20)) & (
        raw.index < pd.Timestamp(START) + pd.Timedelta(hours=22)
    )
    await elec.key_from_series(RAW_KEY, raw.where(~gap))
    assert await feed._mirror_elecprice_raw() is False


@pytest.mark.asyncio
async def test_failed_mirror_falls_back_to_the_own_series(providers):
    elec, feed = providers
    end = START + pd.Timedelta(hours=HOURS)
    await elec.key_from_series(RAW_KEY, _raw_series(START - pd.Timedelta(hours=48), end))
    with patch.object(type(feed), "key_from_series", side_effect=RuntimeError("disk full")):
        assert await feed._mirror_elecprice_raw() is False


@pytest.mark.asyncio
async def test_feed_in_smard_zone_follows_the_smard_region(config_eos):
    from akkudoktoreos.prediction.elecpricesmard import ElecPriceSMARD
    from akkudoktoreos.prediction.feedintariffsmard import FeedInTariffSMARD

    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": HOURS},
            "feedintariff": {"provider": "FeedInTariffSMARD", "smard": {"follow_elecprice": True}},
            "elecprice": {"provider": "ElecPriceSMARD", "smard": {"region": "AT"}},
        }
    )
    get_ems().set_start_datetime(START)
    elec = ElecPriceSMARD()
    elec._db_reset_state()
    feed = FeedInTariffSMARD()
    feed.highest_orig_datetime = None
    feed._db_reset_state()
    await elec.key_from_series(
        RAW_KEY, _raw_series(START - pd.Timedelta(hours=48), START + pd.Timedelta(hours=HOURS))
    )
    assert feed._bidding_zone() == elec.exchange_price_zone() == "AT"
    assert await feed._mirror_elecprice_raw() is True  # same non-DE zone
    config_eos.merge_settings_from_dict({"elecprice": {"smard": {"region": "DE"}}})
    assert feed._bidding_zone() == "DE-LU"


@pytest.mark.asyncio
async def test_mirror_is_skipped_for_non_exchange_electricity_price_provider(providers, config_eos):
    elec, feed = providers
    config_eos.merge_settings_from_dict({"elecprice": {"provider": "ElecPriceFixed"}})
    assert await feed._mirror_elecprice_raw() is False


@pytest.mark.asyncio
async def test_mirror_falls_back_when_series_is_missing_or_too_short(providers):
    elec, feed = providers
    assert await feed._mirror_elecprice_raw() is False  # nothing stored
    await elec.key_from_series(
        RAW_KEY, _raw_series(START - pd.Timedelta(hours=48), START + pd.Timedelta(hours=30))
    )
    assert await feed._mirror_elecprice_raw() is False  # does not cover the horizon


@pytest.mark.asyncio
async def test_mirror_is_skipped_when_bidding_zones_differ(providers, config_eos):
    elec, feed = providers
    await elec.key_from_series(
        RAW_KEY, _raw_series(START - pd.Timedelta(hours=48), START + pd.Timedelta(hours=HOURS))
    )
    config_eos.merge_settings_from_dict({"feedintariff": {"energycharts": {"bidding_zone": "AT"}}})
    assert await feed._mirror_elecprice_raw() is False
    config_eos.merge_settings_from_dict(
        {"feedintariff": {"energycharts": {"bidding_zone": "DE-LU"}}}
    )
    assert await feed._mirror_elecprice_raw() is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "elec_provider", ["ElecPriceEnergyCharts", "ElecPriceSMARD", "ElecPriceAkkudoktor"]
)
async def test_mirror_follows_other_exchange_price_providers(providers, config_eos, elec_provider):
    """Every day-ahead provider of the zone DE-LU can be followed, not only the model provider."""
    from akkudoktoreos.prediction.elecpriceakkudoktor import ElecPriceAkkudoktor
    from akkudoktoreos.prediction.elecpriceenergycharts import ElecPriceEnergyCharts
    from akkudoktoreos.prediction.elecpricesmard import ElecPriceSMARD

    _, feed = providers
    config_eos.merge_settings_from_dict({"elecprice": {"provider": elec_provider}})
    elec = {
        "ElecPriceEnergyCharts": ElecPriceEnergyCharts,
        "ElecPriceSMARD": ElecPriceSMARD,
        "ElecPriceAkkudoktor": ElecPriceAkkudoktor,
    }[elec_provider]()
    elec._db_reset_state()
    end = START + pd.Timedelta(hours=HOURS)
    raw = _raw_series(START - pd.Timedelta(hours=48), end, resolution="1h")  # e.g. hourly
    await elec.key_from_series(RAW_KEY, raw)
    await _update_feed(feed)
    got = await feed.key_to_raw_series(
        FEED_RAW_KEY, start_datetime=START - pd.Timedelta(hours=48), end_datetime=end
    )
    assert (got.index == raw.index).all() and np.allclose(got.to_numpy(), raw.to_numpy())


@pytest.mark.asyncio
async def test_feed_in_smard_follows_with_its_own_setting(config_eos):
    from akkudoktoreos.prediction.feedintariffsmard import FeedInTariffSMARD

    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": HOURS},
            "feedintariff": {"provider": "FeedInTariffSMARD"},
            "elecprice": {"provider": "ElecPriceAkkudoktorML"},
        }
    )
    get_ems().set_start_datetime(START)
    elec = ElecPriceAkkudoktorML()
    elec._db_reset_state()
    feed = FeedInTariffSMARD()
    feed.highest_orig_datetime = None
    feed._db_reset_state()
    await elec.key_from_series(
        RAW_KEY, _raw_series(START - pd.Timedelta(hours=48), START + pd.Timedelta(hours=HOURS))
    )
    assert await feed._mirror_elecprice_raw() is False  # off by default
    config_eos.merge_settings_from_dict({"feedintariff": {"smard": {"follow_elecprice": True}}})
    assert await feed._mirror_elecprice_raw() is True
