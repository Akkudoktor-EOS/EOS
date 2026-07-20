import json
from pathlib import Path

import pytest

from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.prediction.feedintarifffixed import FeedInTariffFixed
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")


@pytest.fixture
def provider(config_eos):
    """Fixture to create a ElecPriceProvider instance."""
    settings = {
        "feedintariff": {
            "provider": "FeedInTariffFixed",
            "provider_settings": {
                "FeedInTariffFixed": {
                    "feed_in_tariff_kwh": 0.078,
                },
            },
        }
    }
    config_eos.merge_settings_from_dict(settings)
    assert config_eos.feedintariff.provider == "FeedInTariffFixed"
    provider = FeedInTariffFixed()
    assert provider.enabled()
    return provider


# ------------------------------------------------
# General forecast
# ------------------------------------------------


def test_singleton_instance(provider):
    """Test that ElecPriceForecast behaves as a singleton."""
    another_instance = FeedInTariffFixed()
    assert provider is another_instance


def test_invalid_provider(provider, config_eos):
    """Test requesting an unsupported provider."""
    settings = {
        "feedintariff": {
            "provider": "<invalid>",
            "provider_settings": {
                "FeedInTariffFixed": {
                    "feed_in_tariff_kwh": 0.078,
                },
            },
        }
    }
    with pytest.raises(ValueError, match="not a valid feed in tariff provider"):
        config_eos.merge_settings_from_dict(settings)


# ------------------------------------------------
# Fixed feed in tariv values
# ------------------------------------------------


@pytest.mark.asyncio
async def test_update_data_fills_prediction_horizon(provider, config_eos):
    """Test that the fixed tariff fills the whole prediction horizon."""
    ems_eos = get_ems()
    start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
    ems_eos.set_start_datetime(start_dt)

    config_eos.optimization.interval = 3600
    config_eos.prediction.hours = 24

    await provider.update_data(force_enable=True, force_update=True)

    # One record per optimization interval over the whole horizon
    assert len(provider) == 24

    records = provider.records
    for i, record in enumerate(records):
        # Constant tariff (0.078 kWh = 0.000078 Wh) on interval boundaries
        assert abs(record.feed_in_tariff_wh - 0.000078) < 1e-9
        assert record.date_time.minute == 0
        assert record.date_time.second == 0
        assert compare_datetimes(record.date_time, start_dt.add(hours=i)).equal
