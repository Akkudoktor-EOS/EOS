"""Tests for electricity price prediction abstract/base classes.

Shared `_apply_fees`/`_store_gross_series` plumbing (empty/short-series
validation, non-uniform-spacing fallback, missing-fee-row zero-fill, key
wiring, singleton mechanics) lives in `PricePredictionProviderBase` and is
exercised generically in `test_priceabc.py` - it is not re-tested here. This
module covers what's genuinely specific to `ElecPriceProvider`: the data
record model, config-driven provider identity, and the consumption-fee
formula in `_compute_gross`.
"""

from typing import Optional
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from akkudoktoreos.prediction.elecpriceabc import ElecPriceDataRecord, ElecPriceProvider
from akkudoktoreos.utils.datetimeutil import to_datetime


class _ElecPriceProviderForTest(ElecPriceProvider):
    """Minimal concrete subclass to exercise the abstract ElecPriceProvider base class."""

    @classmethod
    def provider_id(cls) -> str:
        return "ElecPriceProviderForTest"

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        """No-op update.

        Not exercised by the apply_fees() tests below - they build
        raw_price_amt_wh directly and never call update_data() on this
        provider - but ElecPriceProvider declares _update_data as abstract,
        so a concrete subclass must implement it to be instantiable at all.
        """
        return None


class TestElecPriceDataRecord:
    """Tests for ElecPriceDataRecord model."""

    def test_marketprice_kwh_computed_from_wh(self):
        """Test that the kWh price is the Wh price scaled by 1000."""
        record = ElecPriceDataRecord(elecprice_marketprice_wh=0.0003)
        assert record.elecprice_marketprice_wh == 0.0003
        assert record.elecprice_marketprice_kwh is not None
        assert abs(record.elecprice_marketprice_kwh - 0.3) < 1e-9

    def test_marketprice_kwh_none_when_wh_none(self):
        """Test that the kWh price is None when the underlying Wh price is unset."""
        record = ElecPriceDataRecord()
        assert record.elecprice_marketprice_wh is None
        assert record.elecprice_marketprice_kwh is None

    def test_marketprice_kwh_zero_when_wh_zero(self):
        """Test that a genuine zero Wh price computes to a zero kWh price, not None."""
        record = ElecPriceDataRecord(elecprice_marketprice_wh=0.0)
        assert record.elecprice_marketprice_kwh == 0.0


@pytest.fixture
def provider(monkeypatch, config_eos):
    """Fixture to create a concrete ElecPriceProvider instance for testing apply_fees()."""
    monkeypatch.setenv("EOS_ELECPRICE__ELECPRICE_PROVIDER", "ElecPriceProviderForTest")

    _ElecPriceProviderForTest.reset_instance()
    return _ElecPriceProviderForTest()


def _patch_keys_to_dataframe(monkeypatch, provider, df_elecfee: pd.DataFrame) -> AsyncMock:
    """Monkeypatch Prediction.keys_to_dataframe to return fixed fee data.

    apply_fees() requires a real fee provider to already be registered and
    have generated data in the prediction registry for keys_to_dataframe to
    return anything - which we sidestep here by mocking the call directly,
    so apply_fees() can be tested in isolation.

    provider.prediction is a pydantic model with validate_assignment enabled,
    so assigning directly onto the *instance* (`provider.prediction.keys_to_dataframe
    = mock`) is rejected by pydantic - keys_to_dataframe is a real method, not
    a declared field. Patching the *class* method instead is plain attribute
    replacement and bypasses pydantic's __setattr__ validation.
    """
    mock = AsyncMock(return_value=df_elecfee)
    monkeypatch.setattr(type(provider.prediction), "keys_to_dataframe", mock)
    return mock


class TestElecPriceProvider:
    """Tests for the ElecPriceProvider base class itself (via a minimal subclass).

    Only config-driven behavior is tested here - `enabled()` wiring against
    `config.elecprice.provider` specifically. Provider-identity/singleton
    mechanics themselves come from PredictionMixin and are covered generically
    in test_priceabc.py.
    """

    def test_provider_id(self, provider):
        """Test provider ID returns correct value."""
        assert provider.provider_id() == "ElecPriceProviderForTest"

    def test_invalid_provider(self, provider, monkeypatch):
        """Test requesting an unsupported provider."""
        monkeypatch.setenv("EOS_ELECPRICE__ELECPRICE_PROVIDER", "<invalid>")
        provider.config.reset_settings()
        assert not provider.enabled()


class TestElecPriceProviderApplyFees:
    """Tests for ElecPriceProvider._compute_gross(), via _apply_fees(), with keys_to_dataframe() mocked.

    Only the consumption-fee formula is under test here. Input validation,
    non-uniform-spacing handling, and missing-fee-row zero-fill are shared
    `_apply_fees` plumbing, already covered generically in test_priceabc.py
    against PricePredictionProviderBase directly.
    """

    @pytest.mark.asyncio
    async def test_apply_fees_combines_amt_and_percent(self, provider, monkeypatch):
        """Test combined price = (raw + per-Wh fee) * (100 + percent fee) / 100."""
        start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
        idx = pd.DatetimeIndex([start_dt.add(minutes=15 * i) for i in range(4)])

        raw_price_amt_wh = pd.Series([0.0001, 0.0002, 0.0003, 0.0004], index=idx, name="raw_price")
        df_elecfee = pd.DataFrame(
            {
                "elecfee_consumption_amt_wh": [0.000288, 0.000288, 0.00034, 0.00034],
                "elecfee_consumption_percent_amt": [19.0, 19.0, 19.0, 19.0],
            },
            index=idx,
        )

        mock = _patch_keys_to_dataframe(monkeypatch, provider, df_elecfee)

        result = await provider._apply_fees(raw_price_amt_wh)

        assert mock.await_count == 1
        assert mock.await_args
        called_kwargs = mock.await_args.kwargs

        # Verifies _fee_keys resolves to the real consumption-fee key names,
        # not just that *some* keys get passed through (already covered
        # generically in test_priceabc.py).
        assert set(called_kwargs["keys"]) == {
            "elecfee_consumption_amt_wh",
            "elecfee_consumption_percent_amt",
        }
        assert called_kwargs["start_datetime"] == start_dt
        assert called_kwargs["boundary"] == "context"
        assert called_kwargs["align_to_interval"] is True

        assert result.name == "raw_price"
        assert len(result) == 4
        assert not result.isna().any()

        expected = [
            (0.0001 + 0.000288) * (100.0 + 19.0) / 100.0,
            (0.0002 + 0.000288) * (100.0 + 19.0) / 100.0,
            (0.0003 + 0.00034) * (100.0 + 19.0) / 100.0,
            (0.0004 + 0.00034) * (100.0 + 19.0) / 100.0,
        ]
        for i, exp in enumerate(expected):
            assert abs(result.iloc[i] - exp) < 1e-9, (
                f"interval {i}: expected {exp}, got {result.iloc[i]}"
            )

    @pytest.mark.asyncio
    async def test_apply_fees_zero_percent_fee_passes_amt_fee_through(self, provider, monkeypatch):
        """Test that with a 0% surcharge, the result is raw price plus the per-Wh fee only."""
        start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
        idx = pd.DatetimeIndex([start_dt.add(minutes=15 * i) for i in range(4)])

        raw_price_amt_wh = pd.Series([0.0002] * 4, index=idx)
        df_elecfee = pd.DataFrame(
            {
                "elecfee_consumption_amt_wh": [0.0003] * 4,
                "elecfee_consumption_percent_amt": [0.0] * 4,
            },
            index=idx,
        )
        _patch_keys_to_dataframe(monkeypatch, provider, df_elecfee)

        result = await provider._apply_fees(raw_price_amt_wh)

        expected = 0.0002 + 0.0003
        for i in range(4):
            assert abs(result.iloc[i] - expected) < 1e-9
