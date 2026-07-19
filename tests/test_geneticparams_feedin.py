"""Direct marketing must use the IMPORTED feed-in revenue series.

Regression: with ``feedintariff.provider = FeedInTariffImport`` and direct
marketing enabled, ``prepare_optimization_parameters`` silently replaced the
operator-pushed revenue series with ``elecprice_marketprice_wh`` because the
provider was missing from ``MARKET_PRICE_FEED_IN_TARIFF_PROVIDERS``. External
EMS bridges push the resolved END-CUSTOMER import price into elecprice, so the
GA then saw feed-in == import price in every slot — a world where battery
arbitrage can never pay — and correctly converged to never cycling the battery.
"""

from akkudoktoreos.optimization.genetic.geneticparams import (
    MARKET_PRICE_FEED_IN_TARIFF_PROVIDERS,
)


def test_feedintariff_import_counts_as_market_price_provider():
    assert "FeedInTariffImport" in MARKET_PRICE_FEED_IN_TARIFF_PROVIDERS


def test_native_market_providers_still_present():
    for provider in ("FeedInTariffAkkudoktor", "FeedInTariffEnergyCharts", "FeedInTariffTibber"):
        assert provider in MARKET_PRICE_FEED_IN_TARIFF_PROVIDERS
