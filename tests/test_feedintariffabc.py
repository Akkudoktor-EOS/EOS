"""Tests for feed in tariff prediction abstract/base classes."""

from typing import Optional
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from akkudoktoreos.prediction.feedintariffabc import (
    FeedInTariffDataRecord,
    FeedInTariffProvider,
)
from akkudoktoreos.utils.datetimeutil import to_datetime


class _FeedInTariffProviderForTest(FeedInTariffProvider):
    """Minimal concrete subclass to exercise the abstract FeedInTariffProvider base class."""

    @classmethod
    def provider_id(cls) -> str:
        return "FeedInTariffProviderForTest"

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        """No-op update.

        Not exercised by the apply_fees() tests below - they build
        raw_price_amt_wh directly and never call update_data() on this
        provider - but FeedInTariffProvider declares _update_data as abstract,
        so a concrete subclass must implement it to be instantiable at all.
        """
        return None


class TestFeedInTariffDataRecord:
    """Tests for FeedInTariffDataRecord model."""

    def test_tariff_kwh_computed_from_wh(self):
        """Test that the kWh tariff is the Wh tariff scaled by 1000."""
        record = FeedInTariffDataRecord(feed_in_tariff_wh=0.0003)
        assert record.feed_in_tariff_wh == 0.0003
        assert record.feed_in_tariff_kwh is not None
        assert abs(record.feed_in_tariff_kwh - 0.3) < 1e-9

    def test_tariff_kwh_none_when_wh_none(self):
        """Test that the kWh tariff is None when the underlying Wh tariff is unset."""
        record = FeedInTariffDataRecord()
        assert record.feed_in_tariff_wh is None
        assert record.feed_in_tariff_kwh is None

    def test_tariff_kwh_zero_when_wh_zero(self):
        """Test that a genuine zero Wh tariff computes to a zero kWh tariff, not None."""
        record = FeedInTariffDataRecord(feed_in_tariff_wh=0.0)
        assert record.feed_in_tariff_kwh == 0.0


@pytest.fixture
def provider(monkeypatch, config_eos):
    """Fixture to create a concrete FeedInTariffProvider instance for testing apply_fees()."""
    monkeypatch.setenv("EOS_FEEDINTARIFF__FEEDINTARIFF_PROVIDER", "FeedInTariffProviderForTest")

    _FeedInTariffProviderForTest.reset_instance()
    return _FeedInTariffProviderForTest()


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


class TestFeedInTariffProvider:
    """Tests for the FeedInTariffProvider base class itself (via a minimal subclass)."""

    def test_provider_id(self, provider):
        """Test provider ID returns correct value."""
        assert provider.provider_id() == "FeedInTariffProviderForTest"

    def test_singleton_instance(self, provider):
        """Test that the concrete provider behaves as a singleton."""
        another_instance = _FeedInTariffProviderForTest()
        assert provider is another_instance

    def test_invalid_provider(self, provider, monkeypatch):
        """Test requesting an unsupported provider."""
        monkeypatch.setenv("EOS_FEEDINTARIFF__FEEDINTARIFF_PROVIDER", "<invalid>")
        provider.config.reset_settings()
        assert not provider.enabled()


class TestFeedInTariffProviderApplyFeesValidation:
    """Tests for input validation in FeedInTariffProvider.apply_fees()."""

    @pytest.mark.asyncio
    async def test_apply_fees_empty_series_raises(self, provider):
        """Test that an empty raw price series is rejected outright."""
        empty_series = pd.Series([], dtype=float)
        with pytest.raises(ValueError, match="must not be empty"):
            await provider.apply_fees(empty_series)

    @pytest.mark.asyncio
    async def test_apply_fees_single_entry_series_raises(self, provider):
        """Test that a single-entry series has no interval to derive and is rejected."""
        start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
        series = pd.Series([0.0003], index=pd.DatetimeIndex([start_dt]))
        with pytest.raises(ValueError, match="at least two entries"):
            await provider.apply_fees(series)

    @pytest.mark.asyncio
    async def test_apply_fees_non_uniform_interval_raises(self, provider):
        """Test that a series whose timestamps are not evenly spaced is rejected."""
        start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
        idx = pd.DatetimeIndex(
            [start_dt, start_dt.add(minutes=15), start_dt.add(minutes=50)]
        )
        series = pd.Series([0.0003, 0.00031, 0.00032], index=idx)
        with pytest.raises(ValueError, match="uniform interval"):
            await provider.apply_fees(series)


class TestFeedInTariffProviderApplyFees:
    """Tests for FeedInTariffProvider.apply_fees(), with keys_to_dataframe() mocked."""

    @pytest.mark.asyncio
    async def test_apply_fees_combines_amt_and_percent(self, provider, monkeypatch):
        """Test combined tariff = raw * (100 - percent fee) / 100 - per-Wh fee."""
        start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
        idx = pd.DatetimeIndex([start_dt.add(minutes=15 * i) for i in range(4)])

        raw_price_amt_wh = pd.Series([0.0001, 0.0002, 0.0003, 0.0004], index=idx, name="raw_price")
        df_elecfee = pd.DataFrame(
            {
                "elecfee_feedin_amt_wh": [0.000288, 0.000288, 0.00034, 0.00034],
                "elecfee_feedin_percent_amt": [19.0, 19.0, 19.0, 19.0],
            },
            index=idx,
        )

        mock = _patch_keys_to_dataframe(monkeypatch, provider, df_elecfee)

        result = await provider.apply_fees(raw_price_amt_wh)

        assert mock.await_count == 1
        assert mock.await_args
        called_kwargs = mock.await_args.kwargs

        assert set(called_kwargs["keys"]) == {
            "elecfee_feedin_amt_wh",
            "elecfee_feedin_percent_amt",
        }
        assert called_kwargs["start_datetime"] == start_dt
        assert called_kwargs["boundary"] == "context"
        assert called_kwargs["align_to_interval"] is True

        assert result.name == "raw_price"
        assert len(result) == 4
        assert not result.isna().any()

        expected = [
            0.0001 * (100.0 - 19.0) / 100.0 - 0.000288,
            0.0002 * (100.0 - 19.0) / 100.0 - 0.000288,
            0.0003 * (100.0 - 19.0) / 100.0 - 0.00034,
            0.0004 * (100.0 - 19.0) / 100.0 - 0.00034,
        ]
        for i, exp in enumerate(expected):
            assert abs(result.iloc[i] - exp) < 1e-9, (
                f"interval {i}: expected {exp}, got {result.iloc[i]}"
            )

    @pytest.mark.asyncio
    async def test_apply_fees_zero_percent_fee_subtracts_amt_fee_only(self, provider, monkeypatch):
        """Test that with a 0% deduction, the result is raw tariff minus the per-Wh fee only."""
        start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
        idx = pd.DatetimeIndex([start_dt.add(minutes=15 * i) for i in range(4)])

        raw_price_amt_wh = pd.Series([0.0002] * 4, index=idx)
        df_elecfee = pd.DataFrame(
            {
                "elecfee_feedin_amt_wh": [0.0003] * 4,
                "elecfee_feedin_percent_amt": [0.0] * 4,
            },
            index=idx,
        )
        _patch_keys_to_dataframe(monkeypatch, provider, df_elecfee)

        result = await provider.apply_fees(raw_price_amt_wh)

        expected = 0.0002 - 0.0003
        for i in range(4):
            assert abs(result.iloc[i] - expected) < 1e-9

    @pytest.mark.asyncio
    async def test_apply_fees_missing_fee_rows_filled_with_zero(self, provider, monkeypatch):
        """Test that timestamps not covered by the fee data get a zero fee, not NaN."""
        start_dt = to_datetime("2024-01-01 00:00:00", in_timezone="Europe/Berlin")
        idx_full = pd.DatetimeIndex([start_dt.add(minutes=15 * i) for i in range(4)])
        # Fee data only covers the first two of the four raw price timestamps.
        idx_partial = idx_full[:2]

        raw_price_amt_wh = pd.Series([0.0001, 0.0001, 0.0001, 0.0001], index=idx_full)
        df_elecfee = pd.DataFrame(
            {
                "elecfee_feedin_amt_wh": [0.000288, 0.000288],
                "elecfee_feedin_percent_amt": [19.0, 19.0],
            },
            index=idx_partial,
        )
        _patch_keys_to_dataframe(monkeypatch, provider, df_elecfee)

        result = await provider.apply_fees(raw_price_amt_wh)

        assert not result.isna().any()

        # Covered timestamps: fee applied.
        expected_covered = 0.0001 * (100.0 - 19.0) / 100.0 - 0.000288
        assert abs(result.iloc[0] - expected_covered) < 1e-9
        assert abs(result.iloc[1] - expected_covered) < 1e-9

        # Uncovered timestamps: fee treated as zero, so the raw tariff passes through
        # raw * (100 - 0) / 100 - 0 == raw.
        assert abs(result.iloc[2] - 0.0001) < 1e-9
        assert abs(result.iloc[3] - 0.0001) < 1e-9
