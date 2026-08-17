"""Tests for the shared price prediction base class (PricePredictionProviderBase).

Covers the logic that lives in `priceabc.py` itself - the forecasting helpers,
`_apply_fees` plumbing (index normalization, fee fetch/fallback, zero-fill), and
`_store_gross_series` wiring via the `_raw_key`/`_gross_key`/`_fee_keys`/
`_compute_gross` hooks - independent of any concrete provider's fee formula.

Provider-specific tests (the actual `_compute_gross` formula for electricity
price vs. feed-in tariff, and end-to-end behavior with a real fee provider)
belong in `test_elecpriceabc.py` / `test_feedintariffabc.py` /
`test_elecpricenergycharts.py` instead.
"""

from typing import List, Optional
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from pydantic import Field

from akkudoktoreos.prediction.predictionabc import PredictionRecord
from akkudoktoreos.prediction.priceabc import PricePredictionProviderBase
from akkudoktoreos.utils.datetimeutil import to_datetime


class _PriceProviderForTest(PricePredictionProviderBase):
    """Minimal concrete subclass to exercise PricePredictionProviderBase directly.

    Implements `_compute_gross` with the same add-then-percent formula as
    ElecPriceProvider, but that choice is incidental here - these tests target
    the shared plumbing in `_apply_fees`/`_store_gross_series`, not the formula
    itself, so any well-defined formula would do.
    """

    records: List[PredictionRecord] = Field(
        default_factory=list,
        json_schema_extra={"description": "List of PredictionRecord records"},
    )

    @classmethod
    def provider_id(cls) -> str:
        return "PriceProviderForTest"

    def enabled(self) -> bool:
        return True

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        """No-op update.

        Not exercised by the tests below - they either build raw price series
        directly or mock `key_to_raw_series`/`key_from_series` - but
        `PredictionProvider` declares `_update_data` as abstract, so a concrete
        subclass must implement it to be instantiable at all.
        """
        return None

    @property
    def _raw_key(self) -> str:
        return "test_price_raw_wh"

    @property
    def _gross_key(self) -> str:
        return "test_price_wh"

    @property
    def _fee_keys(self) -> list[str]:
        return ["test_fee_amt_wh", "test_fee_percent_amt"]

    def _compute_gross(self, raw_amt_wh: pd.Series, df_fee: pd.DataFrame) -> pd.Series:
        return (
            (raw_amt_wh + df_fee["test_fee_amt_wh"])
            * (100.0 + df_fee["test_fee_percent_amt"])
            / 100.0
        )


@pytest.fixture
def provider(config_eos):
    """Fixture to create a concrete PricePredictionProviderBase instance for testing."""
    _PriceProviderForTest.reset_instance()
    return _PriceProviderForTest()


def _patch_keys_to_dataframe(monkeypatch, provider, df_fee: pd.DataFrame) -> AsyncMock:
    """Monkeypatch Prediction.keys_to_dataframe to return fixed fee data.

    `_apply_fees` requires a real fee provider to already be registered and
    have generated data in the prediction registry for keys_to_dataframe to
    return anything - which we sidestep here by mocking the call directly,
    so `_apply_fees` can be tested in isolation.

    provider.prediction is a pydantic model with validate_assignment enabled,
    so assigning directly onto the *instance* (`provider.prediction.keys_to_dataframe
    = mock`) is rejected by pydantic - keys_to_dataframe is a real method, not
    a declared field. Patching the *class* method instead is plain attribute
    replacement and bypasses pydantic's __setattr__ validation.
    """
    mock = AsyncMock(return_value=df_fee)
    monkeypatch.setattr(type(provider.prediction), "keys_to_dataframe", mock)
    return mock


class TestPricePredictionProviderBase:
    """Tests for the base class itself (via a minimal concrete subclass)."""

    def test_provider_id(self, provider):
        """Test provider ID returns correct value."""
        assert provider.provider_id() == "PriceProviderForTest"

    def test_singleton_instance(self, provider):
        """Test that the concrete provider behaves as a singleton."""
        another_instance = _PriceProviderForTest()
        assert provider is another_instance


class TestPricePredictionProviderBaseApplyFeesValidation:
    """Tests for input validation in PricePredictionProviderBase._apply_fees()."""

    @pytest.mark.asyncio
    async def test_apply_fees_empty_series_raises(self, provider):
        """Test that an empty raw price series is rejected outright."""
        empty_series = pd.Series([], dtype=float)
        with pytest.raises(ValueError, match="must not be empty"):
            await provider._apply_fees(empty_series)

    @pytest.mark.asyncio
    async def test_apply_fees_single_entry_series_raises(self, provider):
        """Test that a single-entry series has no interval to derive and is rejected."""
        start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
        series = pd.Series([0.0003], index=pd.DatetimeIndex([start_dt]))
        with pytest.raises(ValueError, match="at least two entries"):
            await provider._apply_fees(series)

    @pytest.mark.asyncio
    async def test_apply_fees_non_uniform_interval_warns(self, caplog, provider):
        """Test that a series whose timestamps are not evenly spaced falls back to
        a fixed 15-minute grid, with a warning, instead of raising."""
        start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
        idx = pd.DatetimeIndex(
            [start_dt, start_dt.add(minutes=15), start_dt.add(minutes=50)]
        )
        series = pd.Series([0.0003, 0.00031, 0.00032], index=idx)
        with caplog.at_level("WARNING"):
            await provider._apply_fees(series)
        assert "raw_price_amt_wh has non uniform spacing" in caplog.text


class TestPricePredictionProviderBaseApplyFees:
    """Tests for PricePredictionProviderBase._apply_fees(), with keys_to_dataframe() mocked.

    Uses the generic `_compute_gross` formula from `_PriceProviderForTest`
    (structurally identical to ElecPriceProvider's), since the point here is to
    verify the shared fetch/reindex/fill plumbing feeds `_compute_gross`
    correctly - not to re-verify any one provider's formula.
    """

    @pytest.mark.asyncio
    async def test_apply_fees_calls_compute_gross_with_fetched_fees(self, provider, monkeypatch):
        """Test combined price = (raw + amt fee) * (100 + percent fee) / 100."""
        start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
        idx = pd.DatetimeIndex([start_dt.add(minutes=15 * i) for i in range(4)])

        raw_price_amt_wh = pd.Series([0.0001, 0.0002, 0.0003, 0.0004], index=idx, name="raw_price")
        df_fee = pd.DataFrame(
            {
                "test_fee_amt_wh": [0.000288, 0.000288, 0.00034, 0.00034],
                "test_fee_percent_amt": [19.0, 19.0, 19.0, 19.0],
            },
            index=idx,
        )

        mock = _patch_keys_to_dataframe(monkeypatch, provider, df_fee)

        result = await provider._apply_fees(raw_price_amt_wh)

        assert mock.await_count == 1
        assert mock.await_args
        called_kwargs = mock.await_args.kwargs

        # The fee keys fetched must come from the `_fee_keys` hook, not be hardcoded.
        assert set(called_kwargs["keys"]) == {"test_fee_amt_wh", "test_fee_percent_amt"}
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
    async def test_apply_fees_missing_fee_provider_falls_back_to_zero(self, provider, monkeypatch):
        """Test that a KeyError from keys_to_dataframe (no fee provider configured)
        is treated as zero fees rather than propagating."""
        start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
        idx = pd.DatetimeIndex([start_dt.add(minutes=15 * i) for i in range(4)])
        raw_price_amt_wh = pd.Series([0.0002] * 4, index=idx)

        mock = AsyncMock(side_effect=KeyError("no fee provider configured"))
        monkeypatch.setattr(type(provider.prediction), "keys_to_dataframe", mock)

        result = await provider._apply_fees(raw_price_amt_wh)

        # Zero amt fee, zero percent fee -> raw price passes through unchanged.
        for i in range(4):
            assert abs(result.iloc[i] - 0.0002) < 1e-9

    @pytest.mark.asyncio
    async def test_apply_fees_missing_fee_rows_filled_with_zero(self, provider, monkeypatch):
        """Test that timestamps not covered by the fee data get a zero fee, not NaN."""
        start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
        idx_full = pd.DatetimeIndex([start_dt.add(minutes=15 * i) for i in range(4)])
        # Fee data only covers the first two of the four raw price timestamps.
        idx_partial = idx_full[:2]

        raw_price_amt_wh = pd.Series([0.0001, 0.0001, 0.0001, 0.0001], index=idx_full)
        df_fee = pd.DataFrame(
            {
                "test_fee_amt_wh": [0.000288, 0.000288],
                "test_fee_percent_amt": [19.0, 19.0],
            },
            index=idx_partial,
        )
        _patch_keys_to_dataframe(monkeypatch, provider, df_fee)

        result = await provider._apply_fees(raw_price_amt_wh)

        assert not result.isna().any()

        # Covered timestamps: fee applied.
        expected_covered = (0.0001 + 0.000288) * (100.0 + 19.0) / 100.0
        assert abs(result.iloc[0] - expected_covered) < 1e-9
        assert abs(result.iloc[1] - expected_covered) < 1e-9

        # Uncovered timestamps: fee treated as zero, so the raw price passes through
        # (raw + 0) * (100 + 0) / 100 == raw.
        assert abs(result.iloc[2] - 0.0001) < 1e-9
        assert abs(result.iloc[3] - 0.0001) < 1e-9


class TestPricePredictionProviderBaseStoreGrossSeries:
    """Tests for PricePredictionProviderBase._store_gross_series() wiring.

    `key_to_raw_series`, `_apply_fees`, and `key_from_series` are mocked/spied
    individually so these tests check the *wiring* - the right keys and bounds
    flow through, in the right order - rather than the fee math (already
    covered by TestPricePredictionProviderBaseApplyFees) or requiring a real
    fee provider to be registered.
    """

    @pytest.mark.asyncio
    async def test_store_gross_series_uses_raw_and_gross_key_hooks(self, provider, monkeypatch):
        """Test that the raw series is read from `_raw_key` and the result is
        written to `_gross_key`, both sourced from the subclass hooks rather
        than hardcoded."""
        start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
        end_dt = start_dt.add(hours=1)
        idx = pd.DatetimeIndex([start_dt.add(minutes=15 * i) for i in range(4)])
        raw_series = pd.Series([0.0001, 0.0002, 0.0003, 0.0004], index=idx)
        gross_series = raw_series * 1.19  # arbitrary stand-in for the fee-applied result

        mock_key_to_raw_series = AsyncMock(return_value=raw_series)
        mock_apply_fees = AsyncMock(return_value=gross_series)
        mock_key_from_series = AsyncMock()
        # Patch on the class, not the instance: these are real methods, not
        # declared pydantic fields, and the model has validate_assignment
        # enabled, so instance-level setattr is rejected (see
        # _patch_keys_to_dataframe's docstring for the same issue).
        monkeypatch.setattr(type(provider), "key_to_raw_series", mock_key_to_raw_series)
        monkeypatch.setattr(type(provider), "_apply_fees", mock_apply_fees)
        monkeypatch.setattr(type(provider), "key_from_series", mock_key_from_series)

        await provider._store_gross_series(start_datetime=start_dt, end_datetime=end_dt)

        mock_key_to_raw_series.assert_awaited_once_with(
            key="test_price_raw_wh", start_datetime=start_dt, end_datetime=end_dt
        )
        mock_apply_fees.assert_awaited_once()
        assert mock_apply_fees.await_args
        (apply_fees_arg,) = mock_apply_fees.await_args.args
        assert apply_fees_arg is raw_series

        mock_key_from_series.assert_awaited_once_with("test_price_wh", gross_series)

    @pytest.mark.asyncio
    async def test_store_gross_series_without_bounds_defaults_to_none(self, provider, monkeypatch):
        """Test that omitting start_datetime/end_datetime forwards None, not an
        implicit "full history" value computed here - bound selection is the
        caller's responsibility, per `_store_gross_series`'s docstring."""
        idx = pd.DatetimeIndex(
            [to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")]
        )
        raw_series = pd.Series([0.0001], index=idx)

        mock_key_to_raw_series = AsyncMock(return_value=raw_series)
        mock_apply_fees = AsyncMock(return_value=raw_series)
        mock_key_from_series = AsyncMock()
        monkeypatch.setattr(type(provider), "key_to_raw_series", mock_key_to_raw_series)
        monkeypatch.setattr(type(provider), "_apply_fees", mock_apply_fees)
        monkeypatch.setattr(type(provider), "key_from_series", mock_key_from_series)

        await provider._store_gross_series()

        mock_key_to_raw_series.assert_awaited_once_with(
            key="test_price_raw_wh", start_datetime=None, end_datetime=None
        )
