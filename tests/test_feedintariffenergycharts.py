# ruff: noqa: S101

import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pytest
import requests

from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.prediction.elecfeefixed import ElecFeeFixed
from akkudoktoreos.prediction.elecpriceenergycharts import (
    EnergyChartsElecPrice,
)
from akkudoktoreos.prediction.feedintariffenergycharts import FeedInTariffEnergyCharts
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")

FILE_TESTDATA_ELECPRICE_ENERGYCHARTS_JSON = DIR_TESTDATA.joinpath(
    "elecpriceforecast_energycharts.json"
)


@pytest.fixture
def provider(config_eos):
    config_eos.merge_settings_from_dict(
        {
            "feedintariff": {
                "provider": "FeedInTariffEnergyCharts",
                "energycharts": {"bidding_zone": "AT"},
            },
        }
    )
    provider = FeedInTariffEnergyCharts()
    provider.highest_orig_datetime = None
    assert provider.enabled()
    provider._db_reset_state()
    return provider



@pytest.fixture
def elecfee_provider(config_eos):
    """Fixture to create a ElecFeeFixed instance."""
    config_eos.merge_settings_from_dict(
        {
            "elecfee": {
                "provider": "ElecFeeFixed",
            },
        }
    )
    provider = ElecFeeFixed()
    assert provider.enabled()
    provider._db_reset_state()
    return provider


@pytest.fixture
def sample_energycharts_json():
    with FILE_TESTDATA_ELECPRICE_ENERGYCHARTS_JSON.open(
        "r", encoding="utf-8", newline=None
    ) as f_res:
        return json.load(f_res)


class TestFeedInTariffEnergyCharts:

    def test_provider_is_available(self, config_eos):
        assert "FeedInTariffEnergyCharts" in config_eos.feedintariff.providers


    def test_parse_data_uses_raw_market_price(self, provider, sample_energycharts_json):
        energy_charts_data = EnergyChartsElecPrice.model_validate(sample_energycharts_json)

        series = provider._parse_data(energy_charts_data)

        assert series.iloc[0] == pytest.approx(sample_energycharts_json["price"][0] / 1_000_000)

    @pytest.mark.parametrize(
        ("now", "highest_orig_datetime", "resolution_seconds", "expected"),
        [
            ("2026-07-23T10:00:00+02:00", "2026-07-23T23:00:00+02:00", 15 * 60, False),
            ("2026-07-23T10:00:00+02:00", "2026-07-23T23:45:00+02:00", 15 * 60, True),
            ("2026-07-23T10:00:00+02:00", "2026-07-23T23:00:00+02:00", 60 * 60, True),
            ("2026-07-23T15:00:00+02:00", "2026-07-24T23:00:00+02:00", 15 * 60, False),
            ("2026-07-23T15:00:00+02:00", "2026-07-24T23:45:00+02:00", 15 * 60, True),
            (
                pd.Timestamp("2026-03-28 15:00:00", tz="Europe/Berlin"),
                "2026-03-29T23:45:00+02:00",
                15 * 60,
                True,
            ),
        ],
    )
    def test_published_horizon_requires_complete_last_interval(
        self, provider, now, highest_orig_datetime, resolution_seconds, expected
    ):
        provider.highest_orig_datetime = to_datetime(highest_orig_datetime)

        assert (
            provider._has_complete_published_horizon(
                now=pd.Timestamp(now), resolution_seconds=resolution_seconds
            )
            is expected
        )

    @pytest.mark.parametrize(
        ("old_minutes", "recent_minutes", "expected_seconds"),
        [
            (60, [15, 15, 15, 15], 900),
            (15, [60, 60, 60, 60], 3600),
            (60, [15, 15, 60, 15, 15], 900),
            (15, [60, 60, 120, 60, 60], 3600),
            (60, [15, 15, 15], 3600),
            (60, [15, 60, 15, 60, 15], 3600),
        ],
    )
    def test_coverage_resolution_handles_transitions_and_gaps(
        self, provider, old_minutes, recent_minutes, expected_seconds
    ):
        """A short consistent run overrides old history; ambiguous runs use its median."""
        transition = pd.Timestamp("2026-01-15 18:00", tz="Europe/Berlin")
        old_index = pd.date_range(
            start=transition - pd.Timedelta(days=1), end=transition, freq=f"{old_minutes}min"
        )
        recent_index = transition + pd.to_timedelta(np.cumsum(recent_minutes), unit="min")
        source = pd.Series(0.0001, index=old_index.append(recent_index))
        assert provider._coverage_resolution_seconds(source) == expected_seconds

    @pytest.mark.asyncio
    @pytest.mark.parametrize("host_timezone", ["UTC", "Europe/Berlin"])
    @pytest.mark.parametrize("history_interval_minutes", [15, 60])
    @pytest.mark.parametrize("recent_source_points", [None, 5])
    @pytest.mark.parametrize(
        ("now", "last_price", "interval_minutes", "needs_update"),
        [
            ("2026-01-15 13:59:59", "2026-01-15 23:00", 15, True),
            ("2026-01-15 13:59:59", "2026-01-15 23:45", 15, False),
            ("2026-01-15 13:59:59", "2026-01-15 23:00", 60, False),
            ("2026-01-15 14:00:00", "2026-01-16 23:00", 15, True),
            ("2026-01-15 14:00:00", "2026-01-16 23:45", 15, False),
            ("2026-01-15 14:00:00", "2026-01-16 23:00", 60, False),
            ("2026-03-28 14:00:00", "2026-03-29 23:45", 15, False),
            ("2026-03-28 14:00:00", "2026-03-29 23:30", 15, True),
            ("2026-10-24 14:00:00", "2026-10-25 23:45", 15, False),
            ("2026-10-24 14:00:00", "2026-10-25 23:30", 15, True),
        ],
    )
    async def test_update_data_uses_source_cadence_for_coverage(
        self,
        provider,
        set_other_timezone: Callable[[str], str],
        host_timezone,
        history_interval_minutes,
        recent_source_points,
        now,
        last_price,
        interval_minutes,
        needs_update,
    ):
        """Refresh incomplete original prices, independent of host zone or predicted tail."""
        set_other_timezone(host_timezone)
        fixed_now = pd.Timestamp(now, tz="Europe/Berlin")
        start = to_datetime(fixed_now, in_timezone="Europe/Berlin").start_of("day")
        last_original = to_datetime(
            pd.Timestamp(last_price, tz="Europe/Berlin"), in_timezone="Europe/Berlin"
        )
        get_ems().set_start_datetime(start)
        source_start = (
            start
            if recent_source_points is None
            else last_original.subtract(minutes=(recent_source_points - 1) * interval_minutes)
        )
        source_index = pd.date_range(
            start=source_start, end=last_original, freq=f"{interval_minutes}min"
        )
        history_index = pd.date_range(
            start=start.subtract(days=35),
            end=source_index[0],
            freq=f"{history_interval_minutes}min",
            inclusive="left",
        )
        await provider.key_from_series(
            "feed_in_tariff_raw_wh", pd.Series(0.0001, index=history_index.append(source_index))
        )
        provider.highest_orig_datetime = last_original

        # The predicted tail uses the same key and must not influence source coverage.
        predicted_minutes = 60 if interval_minutes == 15 else 15
        predicted_index = pd.date_range(
            start=last_original.add(minutes=predicted_minutes),
            periods=120,
            freq=f"{predicted_minutes}min",
        )
        await provider.key_from_series(
            "feed_in_tariff_raw_wh", pd.Series(0.00005, index=predicted_index)
        )

        published_end = start.add(days=1 if fixed_now.hour < 14 else 2)
        response_index = pd.date_range(
            start=start, end=published_end, freq=f"{interval_minutes}min", inclusive="left"
        )
        response = EnergyChartsElecPrice(
            license_info="",
            unix_seconds=[int(timestamp.timestamp()) for timestamp in response_index],
            price=[200.0] * len(response_index),
            unit="EUR/MWh",
            deprecated=False,
        )

        def predict(history: np.ndarray, hours: int, slots_per_hour: int = 1) -> np.ndarray:
            return np.full(hours, 0.00005)

        original_read = FeedInTariffEnergyCharts.key_to_raw_series
        source_reads = []

        async def read_source(self, *args, **kwargs):
            if "start_datetime" not in kwargs:
                source_reads.append(kwargs)
            return await original_read(self, *args, **kwargs)

        with (
            patch("akkudoktoreos.prediction.feedintariffenergycharts.pd", wraps=pd) as pandas,
            patch.object(provider, "_request_forecast", return_value=response) as request,
            patch.object(provider, "_predict", side_effect=predict),
            patch.object(FeedInTariffEnergyCharts, "key_to_raw_series", read_source),
        ):
            pandas.Timestamp.now.return_value = fixed_now
            await provider._update_data(force_update=False)

        assert len(source_reads) == (2 if needs_update else 1)
        if needs_update:
            request.assert_called_once_with(
                start_date=start.in_timezone(host_timezone).format("YYYY-MM-DD"),
                force_update=False,
            )
            assert pd.Timestamp(provider.highest_orig_datetime) == response_index[-1]
            fetched = await provider.key_to_raw_series(
                key="feed_in_tariff_raw_wh",
                start_datetime=last_original,
                end_datetime=published_end,
            )
            expected_index = response_index[response_index >= pd.Timestamp(last_original)].tz_convert(
                "UTC"
            )
            assert fetched.index.equals(expected_index)
            np.testing.assert_allclose(fetched.to_numpy(), 0.0002)
        else:
            request.assert_not_called()
            assert provider.highest_orig_datetime == last_original


    @patch("requests.get")
    def test_request_forecast_uses_feedintariff_bidding_zone(
        self, mock_get, provider, sample_energycharts_json
    ):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = json.dumps(sample_energycharts_json)
        mock_get.return_value = mock_response
        get_ems().set_start_datetime(to_datetime("2024-12-11 00:00:00", in_timezone="Europe/Berlin"))

        provider._request_forecast(start_date="2024-12-10", force_update=True)

        actual_url = mock_get.call_args[0][0]
        assert "bzn=AT" in actual_url

    @pytest.mark.asyncio
    async def test_update_data_keeps_quarter_hour_resolution(self, provider):
        start = to_datetime("2025-01-15 00:00:00", in_timezone="Europe/Berlin")
        get_ems().set_start_datetime(start)
        raw_slots = provider.config.prediction.hours * 2
        energy_charts_data = EnergyChartsElecPrice(
            license_info="",
            unix_seconds=[int(start.add(minutes=15 * i).timestamp()) for i in range(raw_slots)],
            price=[100.0] * raw_slots,
            unit="EUR/MWh",
            deprecated=False,
        )

        with patch.object(provider, "_request_forecast", return_value=energy_charts_data):
            await provider._update_data(force_update=True)

        result = await provider.key_to_raw_series(
            key="feed_in_tariff_raw_wh",
            start_datetime=start,
            end_datetime=start.add(hours=provider.config.prediction.hours),
        )
        assert len(result) == provider.config.prediction.hours * 4
        assert result.index.to_series().diff().dropna().dt.total_seconds().unique().tolist() == [900.0]

    @pytest.mark.asyncio
    async def test_repeated_updates_keep_ets_history_and_honor_force_update(self, provider):
        """A later update must retain ETS history and a forced update must fetch again."""
        start = to_datetime(in_timezone="Europe/Berlin").start_of("day")
        get_ems().set_start_datetime(start)
        provider.config.prediction.hours = 72

        raw_start = start.subtract(days=35)
        raw_end = start.add(days=2)
        raw_slots = int((raw_end - raw_start).total_seconds() // 900) + 1
        energy_charts_data = EnergyChartsElecPrice(
            license_info="",
            unix_seconds=[int(raw_start.add(minutes=15 * i).timestamp()) for i in range(raw_slots)],
            price=[50.0 + float(i % 96) for i in range(raw_slots)],
            unit="EUR/MWh",
            deprecated=False,
        )

        ets_history_lengths = []

        def fake_ets(history, seasonal_periods, hours):
            ets_history_lengths.append((len(history), seasonal_periods))
            return np.full(hours, 0.00005)

        with (
            patch.object(provider, "_request_forecast", return_value=energy_charts_data) as request,
            patch.object(FeedInTariffEnergyCharts, "_predict_ets", side_effect=fake_ets),
            patch.object(
                FeedInTariffEnergyCharts,
                "_predict_median",
                side_effect=AssertionError("median fallback must not be used"),
            ),
        ):
            await provider.update_data(force_enable=True, force_update=True)
            await provider.update_data(force_enable=True, force_update=False)

            # Raw prices already cover the Energy-Charts publication window, so the
            # second update reuses the retained 35-day history without another request.
            assert request.call_count == 1
            assert len(ets_history_lengths) == 2
            assert all(length > 2 * 168 * 4 for length, _ in ets_history_lengths)
            assert all(seasonal_periods == 168 * 4 for _, seasonal_periods in ets_history_lengths)

            await provider.update_data(force_enable=True, force_update=True)

        # force_update must bypass the provider's own "no update needed" decision.
        assert request.call_count == 2
        assert provider.historic_hours_min() == 24 * 35


    def test_request_forecast_retries_transient_errors(self, provider, sample_energycharts_json):
        """A transient timeout is retried; a later success is returned (Fix D)."""
        get_ems().set_start_datetime(to_datetime("2024-12-11 00:00:00", in_timezone="Europe/Berlin"))

        ok_response = Mock()
        ok_response.status_code = 200
        ok_response.content = json.dumps(sample_energycharts_json)
        ok_response.raise_for_status = Mock()

        with (
            patch("requests.get", side_effect=[requests.exceptions.ReadTimeout("t1"), ok_response]) as get_mock,
            patch("akkudoktoreos.prediction.feedintariffenergycharts.time.sleep", return_value=None),
        ):
            provider._request_forecast(start_date="2024-12-10", force_update=True)

        assert get_mock.call_count == 2

    @pytest.mark.asyncio
    async def test_update_data_falls_back_to_history_on_fetch_error(self, provider):
        """A transient fetch error must not abort the update when history exists (Fix A)."""
        start = to_datetime(in_timezone="Europe/Berlin").start_of("day")
        get_ems().set_start_datetime(start)
        provider.config.prediction.hours = 48

        raw_start = start.subtract(days=35)
        raw_slots = int((start.add(days=2) - raw_start).total_seconds() // 900) + 1
        energy_charts_data = EnergyChartsElecPrice(
            license_info="",
            unix_seconds=[int(raw_start.add(minutes=15 * i).timestamp()) for i in range(raw_slots)],
            price=[50.0 + float(i % 96) for i in range(raw_slots)],
            unit="EUR/MWh",
            deprecated=False,
        )

        def fake_predict(history, hours, slots_per_hour=1):
            return np.full(hours, 0.00005)

        with patch.object(provider, "_predict", side_effect=fake_predict):
            # First: successful update seeds history and highest_orig_datetime.
            with patch.object(provider, "_request_forecast", return_value=energy_charts_data):
                await provider.update_data(force_enable=True, force_update=True)
            assert provider.highest_orig_datetime is not None
            last_good = provider.highest_orig_datetime

            # Second: API times out. With existing history the update must NOT raise
            # and the retained history must be kept.
            with patch.object(
                provider, "_request_forecast", side_effect=requests.exceptions.ReadTimeout("boom")
            ):
                await provider.update_data(force_enable=True, force_update=True)

        # Fix A: the update did not abort (we got here) and the retained history is
        # unchanged, so downstream consumers still receive a feed-in tariff series.
        assert provider.highest_orig_datetime == last_good

    @pytest.mark.asyncio
    async def test_update_data_cold_start_fetch_error_raises(self, provider):
        """Without any history a fetch error stays fatal (cold start)."""
        start = to_datetime(in_timezone="Europe/Berlin").start_of("day")
        get_ems().set_start_datetime(start)
        assert provider.highest_orig_datetime is None

        with patch.object(
            provider, "_request_forecast", side_effect=requests.exceptions.ReadTimeout("boom")
        ):
            with pytest.raises(requests.exceptions.ReadTimeout):
                await provider.update_data(force_enable=True, force_update=True)

    @pytest.mark.asyncio
    async def test_update_data_no_fees_configured_defaults_gross_to_raw(self, provider):
        """Without an ElecFee provider configured, feed_in_tariff_wh must equal the raw series.

        _apply_fees() catches the KeyError from an absent ElecFee provider and
        defaults both fee components to 0, so gross should be indistinguishable
        from raw in that case.
        """
        start = to_datetime("2025-01-15 00:00:00", in_timezone="Europe/Berlin")
        get_ems().set_start_datetime(start)
        raw_slots = provider.config.prediction.hours * 4
        energy_charts_data = EnergyChartsElecPrice(
            license_info="",
            unix_seconds=[int(start.add(minutes=15 * i).timestamp()) for i in range(raw_slots)],
            price=[100.0] * raw_slots,
            unit="EUR/MWh",
            deprecated=False,
        )

        with patch.object(provider, "_request_forecast", return_value=energy_charts_data):
            await provider._update_data(force_update=True)

        raw = await provider.key_to_series(
            key="feed_in_tariff_raw_wh",
            start_datetime=start,
            end_datetime=start.add(hours=provider.config.prediction.hours),
            interval=to_duration("15 minutes"),
        )
        gross = await provider.key_to_series(
            key="feed_in_tariff_wh",
            start_datetime=start,
            end_datetime=start.add(hours=provider.config.prediction.hours),
            interval=to_duration("15 minutes"),
        )
        pd.testing.assert_series_equal(gross, raw, check_names=False)

    @pytest.mark.asyncio
    async def test_update_data_applies_feedin_fees(self, provider, config_eos):
        """feed_in_tariff_wh must reflect the configured feed-in fee deduction.

        Per _apply_fees(): gross = raw * (100 - percent_amt) / 100 - amt_wh,
        i.e. fees are deducted from what the producer receives, the inverse
        direction of the consumption-side markup.
        """
        feedin_amt_kwh = 0.001  # flat fee/kWh deducted from the feed-in payout
        feedin_percent_amt = 5.0  # percentage deducted from the feed-in payout
        config_eos.merge_settings_from_dict(
            {
                "elecfee": {
                    "provider": "ElecFeeFixed",
                    "elecfeefixed": {
                        "feedin_amt_kwh": {
                            "windows": [
                                {"start_time": "00:00", "duration": "24 hours", "value": feedin_amt_kwh},
                            ],
                        },
                        "feedin_percent_amt": {
                            "windows": [
                                {"start_time": "00:00", "duration": "24 hours", "value": feedin_percent_amt},
                            ],
                        },
                    },
                },
            }
        )
        start = to_datetime("2025-01-15 00:00:00", in_timezone="Europe/Berlin")
        get_ems().set_start_datetime(start)
        await ElecFeeFixed()._update_data(force_update=True)

        raw_slots = provider.config.prediction.hours * 4
        energy_charts_data = EnergyChartsElecPrice(
            license_info="",
            unix_seconds=[int(start.add(minutes=15 * i).timestamp()) for i in range(raw_slots)],
            price=[100.0] * raw_slots,  # 100 EUR/MWh = 0.1 EUR/kWh
            unit="EUR/MWh",
            deprecated=False,
        )
        with patch.object(provider, "_request_forecast", return_value=energy_charts_data):
            await provider._update_data(force_update=True)

        raw_result = await provider.key_to_series(
            key="feed_in_tariff_raw_wh",
            start_datetime=start,
            end_datetime=start.add(minutes=15),
            interval=to_duration("15 minutes"),
        )
        gross_result = await provider.key_to_series(
            key="feed_in_tariff_wh",
            start_datetime=start,
            end_datetime=start.add(minutes=15),
            interval=to_duration("15 minutes"),
        )
        raw_kwh = raw_result.iloc[0] * 1000
        gross_kwh = gross_result.iloc[0] * 1000
        assert raw_kwh == pytest.approx(0.1)
        expected_gross_kwh = raw_kwh * (100.0 - feedin_percent_amt) / 100.0 - feedin_amt_kwh
        assert gross_kwh == pytest.approx(expected_gross_kwh)

    @pytest.mark.asyncio
    async def test_update_data_covers_full_horizon_after_stale_fetch_outage(self, provider):
        """Regression test: needed_slots must include the gap when a fetch outage
        leaves highest_orig_datetime behind the current ems_start_datetime.

        Same bug/fix as ElecPriceEnergyCharts: `covered_slots` must be allowed
        to go negative when highest_orig_datetime is older than
        ems_start_datetime, rather than clamped to 0, or the predicted tail
        ends before ems_start_datetime + prediction.hours whenever an outage
        persists long enough for the two to diverge.
        """
        provider.config.prediction.hours = 48

        start = to_datetime("2026-01-15 00:00:00", in_timezone="Europe/Berlin")
        get_ems().set_start_datetime(start)

        raw_start = start.subtract(days=35)
        raw_slots = int((start - raw_start).total_seconds() // 900) + 1
        energy_charts_data = EnergyChartsElecPrice(
            license_info="",
            unix_seconds=[int(raw_start.add(minutes=15 * i).timestamp()) for i in range(raw_slots)],
            price=[50.0 + float(i % 96) for i in range(raw_slots)],
            unit="EUR/MWh",
            deprecated=False,
        )

        def fake_ets(history, seasonal_periods, hours):
            return np.full(hours, 0.00005)

        with (
            patch.object(provider, "_request_forecast", return_value=energy_charts_data),
            patch.object(FeedInTariffEnergyCharts, "_predict_ets", side_effect=fake_ets),
        ):
            await provider.update_data(force_enable=True, force_update=True)

        last_good = provider.highest_orig_datetime
        assert last_good is not None

        # Advance ems_start_datetime well past the last known data point, as
        # if a fetch outage has persisted for a while.
        outage_gap_hours = 20
        new_start = to_datetime(last_good).add(hours=outage_gap_hours)
        get_ems().set_start_datetime(new_start)

        with (
            patch.object(
                provider, "_request_forecast", side_effect=requests.exceptions.ReadTimeout("boom")
            ),
            patch.object(FeedInTariffEnergyCharts, "_predict_ets", side_effect=fake_ets),
        ):
            await provider.update_data(force_enable=True, force_update=True)

        assert provider.highest_orig_datetime == last_good

        horizon_end = new_start.add(hours=provider.config.prediction.hours)
        raw_result = await provider.key_to_series(
            key="feed_in_tariff_raw_wh",
            start_datetime=horizon_end.subtract(minutes=15),
            end_datetime=horizon_end,
            interval=to_duration("15 minutes"),
        )
        assert len(raw_result) == 1
        assert not raw_result.isna().any()
