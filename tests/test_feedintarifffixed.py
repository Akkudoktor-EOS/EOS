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
