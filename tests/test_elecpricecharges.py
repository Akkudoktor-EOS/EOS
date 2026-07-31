"""Tests for the electricity price charge component model and application logic.

See issue #392: model the German electricity price build-up
(spot + grid fees + concession + electricity tax + surcharges + VAT) via an
ordered list of fixed/percent charge components.
"""

import pytest

from akkudoktoreos.prediction.elecprice import (
    ElecPriceChargeComponent,
    ElecPriceCommonSettings,
)


def _apply(settings: ElecPriceCommonSettings, market_price_wh: float) -> float:
    """Apply the configured charges to a per-Wh market price."""
    return settings.apply_charges(market_price_wh)


class TestElecPriceChargeComponent:
    def test_no_charges_is_noop(self):
        settings = ElecPriceCommonSettings(charges=None)
        assert _apply(settings, 0.0002) == pytest.approx(0.0002)

    def test_empty_charges_is_noop(self):
        settings = ElecPriceCommonSettings(charges=[])
        assert _apply(settings, 0.0002) == pytest.approx(0.0002)

    def test_single_fixed_charge(self):
        # 0.10 EUR/kWh added to a spot price of 0.20 EUR/kWh
        settings = ElecPriceCommonSettings(
            charges=[ElecPriceChargeComponent(type="fixed", amount=0.10)]
        )
        # market price is per Wh
        result = _apply(settings, 0.20 / 1000)
        assert result == pytest.approx(0.30 / 1000)

    def test_percent_default_basis_is_everything(self):
        # spot 0.20 + fixed 0.10 = 0.30, then 19% VAT on everything -> 0.357
        settings = ElecPriceCommonSettings(
            charges=[
                ElecPriceChargeComponent(name="fee", type="fixed", amount=0.10),
                ElecPriceChargeComponent(name="vat", type="percent", amount=0.19),
            ]
        )
        result = _apply(settings, 0.20 / 1000)
        assert result == pytest.approx(0.357 / 1000)

    def test_percent_explicit_basis(self):
        # percent applies only to the referenced "fee" component (not the market)
        settings = ElecPriceCommonSettings(
            charges=[
                ElecPriceChargeComponent(name="fee", type="fixed", amount=0.10),
                ElecPriceChargeComponent(
                    name="surcharge", type="percent", amount=0.5, basis=["fee"]
                ),
            ]
        )
        # spot 0.20 + fee 0.10 + 0.5*0.10 = 0.35
        result = _apply(settings, 0.20 / 1000)
        assert result == pytest.approx(0.35 / 1000)

    def test_percent_explicit_basis_market(self):
        # percent applies only to the market price
        settings = ElecPriceCommonSettings(
            charges=[
                ElecPriceChargeComponent(name="fee", type="fixed", amount=0.10),
                ElecPriceChargeComponent(
                    name="onmarket", type="percent", amount=0.5, basis=["market"]
                ),
            ]
        )
        # spot 0.20 + fee 0.10 + 0.5*0.20 = 0.40
        result = _apply(settings, 0.20 / 1000)
        assert result == pytest.approx(0.40 / 1000)

    def test_multiple_percents_processed_in_order(self):
        # spot 0.20, then +10% -> 0.22, then +19% on everything -> 0.2618
        settings = ElecPriceCommonSettings(
            charges=[
                ElecPriceChargeComponent(name="p1", type="percent", amount=0.10),
                ElecPriceChargeComponent(name="vat", type="percent", amount=0.19),
            ]
        )
        result = _apply(settings, 0.20 / 1000)
        assert result == pytest.approx(0.2618 / 1000)

    def test_german_build_up_from_issue(self):
        # Example from issue #392 (values in EUR/kWh), VAT 19% on everything.
        settings = ElecPriceCommonSettings(
            charges=[
                ElecPriceChargeComponent(name="netzentgelt", type="fixed", amount=0.1153),
                ElecPriceChargeComponent(name="konzession", type="fixed", amount=0.018),
                ElecPriceChargeComponent(name="stromsteuer", type="fixed", amount=0.0205),
                ElecPriceChargeComponent(name="umlagen", type="fixed", amount=0.0158),
                ElecPriceChargeComponent(name="vat", type="percent", amount=0.19),
            ]
        )
        spot = 0.10
        net = spot + 0.1153 + 0.018 + 0.0205 + 0.0158
        gross = net * 1.19
        result = _apply(settings, spot / 1000)
        assert result == pytest.approx(gross / 1000)

    def test_percent_unknown_basis_name_raises(self):
        settings = ElecPriceCommonSettings(
            charges=[
                ElecPriceChargeComponent(
                    name="vat", type="percent", amount=0.19, basis=["nope"]
                ),
            ]
        )
        with pytest.raises(ValueError):
            _apply(settings, 0.20 / 1000)
