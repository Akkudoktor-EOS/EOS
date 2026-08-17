# ruff: noqa: S101

from unittest.mock import patch

from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.prediction.elecpriceenergycharts import EnergyChartsElecPrice
from akkudoktoreos.prediction.elecpricesmard import ElecPriceSMARD
from akkudoktoreos.prediction.feedintariffsmard import FeedInTariffSMARD
from akkudoktoreos.utils.datetimeutil import to_datetime


def test_feed_in_tariff_smard_reuses_raw_smard_market_prices(config_eos):
    """The feed-in provider delegates to SMARD and stores no import-price components."""
    config_eos.merge_settings_from_dict(
        {
            "elecprice": {"provider": "ElecPriceSMARD"},
            "feedintariff": {
                "direct_marketing_enabled": True,
                "provider": "FeedInTariffSMARD",
            },
        }
    )
    get_ems().set_start_datetime(
        to_datetime("2026-08-01 00:00:00", in_timezone="Europe/Berlin")
    )
    provider = FeedInTariffSMARD()
    data = EnergyChartsElecPrice(
        license_info="CC BY 4.0 Bundesnetzagentur | SMARD.de",
        unix_seconds=[1785535200],
        price=[169.44],
        unit="EUR/MWh",
        deprecated=False,
    )

    with patch.object(ElecPriceSMARD, "_request_forecast", return_value=data) as request:
        result = provider._request_forecast(start_date="2026-08-01", force_update=True)

    assert provider.enabled()
    assert result is data
    assert provider._parse_data(result).iloc[0] == 169.44 / 1_000_000
    request.assert_called_once_with(start_date="2026-08-01", force_update=True)
