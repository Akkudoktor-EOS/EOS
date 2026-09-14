"""Direct marketing must use the IMPORTED feed-in revenue series.

Regression: with ``feedintariff.provider = FeedInTariffImport`` and direct
marketing enabled, ``prepare_optimization_parameters`` silently replaced the
operator-pushed revenue series with ``elecprice_marketprice_wh`` because the
provider was missing from ``MARKET_PRICE_FEED_IN_TARIFF_PROVIDERS``. External
EMS bridges push the resolved END-CUSTOMER import price into elecprice, so the
GA then saw feed-in == import price in every slot — a world where battery
arbitrage can never pay — and correctly converged to never cycling the battery.
"""

from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pytest

from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticparams import (
    MARKET_PRICE_FEED_IN_TARIFF_PROVIDERS,
    GeneticOptimizationParameters,
)


def test_feedintariff_import_counts_as_market_price_provider():
    assert "FeedInTariffImport" in MARKET_PRICE_FEED_IN_TARIFF_PROVIDERS


def test_native_market_providers_still_present():
    for provider in ("FeedInTariffAkkudoktor", "FeedInTariffEnergyCharts", "FeedInTariffTibber"):
        assert provider in MARKET_PRICE_FEED_IN_TARIFF_PROVIDERS


@pytest.fixture
def prepare_tariffs(config_eos):
    """Prepare real parameters from deterministic provider series without devices."""
    def prepare(provider, revenues, interval=3600, direct_marketing=True):
        config_eos.merge_settings_from_dict(
            {
                "prediction": {"hours": 24},
                "optimization": {
                    "horizon_hours": 24,
                    "tail_horizon_hours": 0,
                    "interval": interval,
                },
                "feedintariff": {
                    "provider": provider,
                    "direct_marketing_enabled": direct_marketing,
                },
                "devices": {
                    "max_batteries": 0,
                    "max_electric_vehicles": 0,
                    "max_inverters": 0,
                    "home_appliances": [],
                },
            }
        )
        from akkudoktoreos.utils.datetimeutil import to_datetime

        start = to_datetime("2026-08-01T00:00:00+00:00").start_of("day")
        slots = 24 * 3600 // interval
        index = pd.date_range(start=start, periods=slots, freq=f"{interval}s")
        prices = [0.0002, 0.0003] * (slots // 2)
        feed_in = (revenues * slots)[:slots] if revenues else []
        series = {
            "pvforecast_ac_power": pd.Series(0.0, index=index),
            "loadforecast_power_w": pd.Series(100.0, index=index),
            "weather_temp_air": pd.Series(20.0, index=index),
            "elecprice_marketprice_wh": pd.Series(prices, index=index),
            "feed_in_tariff_wh": pd.Series(feed_in, index=index[:len(feed_in)], dtype=float),
        }
        prediction = Mock()
        prediction.key_to_series.side_effect = lambda key, **kwargs: series[key]
        ems = Mock(start_datetime=start)
        ems.genetic_solution.return_value = None
        with (
            patch("akkudoktoreos.optimization.genetic.geneticparams.get_ems", return_value=ems),
            patch.object(GeneticOptimizationParameters, "prediction", prediction),
        ):
            parameters = GeneticOptimizationParameters.prepare()
        assert parameters is not None
        return parameters, feed_in, prices

    return prepare


@pytest.mark.parametrize("interval", [3600, 900])
@pytest.mark.parametrize("provider", sorted(MARKET_PRICE_FEED_IN_TARIFF_PROVIDERS))
@pytest.mark.parametrize("revenues", [[0.00007], [0.0], [-0.00005], [0.0001, -0.00005]])
def test_provider_revenues_survive_preparation_and_optimization(
    prepare_tariffs, provider, revenues, interval
):
    parameters, expected, prices = prepare_tariffs(provider, revenues, interval)
    assert parameters.ems.einspeiseverguetung_euro_pro_wh == expected
    adjusted = GeneticOptimization()._parameters_for_config(parameters)
    assert adjusted.ems.einspeiseverguetung_euro_pro_wh == expected
    assert adjusted.ems.strompreis_euro_pro_wh == prices
    assert parameters.ems.einspeiseverguetung_euro_pro_wh == expected


def test_missing_imported_revenues_remain_missing(prepare_tariffs):
    parameters, _, _ = prepare_tariffs("FeedInTariffImport", [])
    adjusted = GeneticOptimization()._parameters_for_config(parameters)
    assert np.isnan(adjusted.ems.einspeiseverguetung_euro_pro_wh).all()


@pytest.mark.parametrize("provider", ["FeedInTariffFixed", "FeedInTariffSMARD"])
def test_legacy_fallback_warns(prepare_tariffs, caplog, provider):
    parameters, _, prices = prepare_tariffs(provider, [0.00007])
    assert parameters.ems.einspeiseverguetung_euro_pro_wh == prices
    assert "falling back to elecprice_marketprice_wh as feed-in revenue" in caplog.text
    assert provider in caplog.text


def test_direct_marketing_disabled_keeps_fixed_revenues(prepare_tariffs, caplog):
    parameters, expected, _ = prepare_tariffs(
        "FeedInTariffFixed", [0.00007], direct_marketing=False
    )
    adjusted = GeneticOptimization()._parameters_for_config(parameters)
    assert adjusted.ems.einspeiseverguetung_euro_pro_wh == expected
    assert "falling back" not in caplog.text
