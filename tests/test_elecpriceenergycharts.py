import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pytest
import requests
from loguru import logger

from akkudoktoreos.core.cache import CacheFileStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.prediction.elecfeefixed import ElecFeeFixed
from akkudoktoreos.prediction.elecpriceakkudoktor import (
    AkkudoktorElecPrice,
    AkkudoktorElecPriceValue,
    ElecPriceAkkudoktor,
)
from akkudoktoreos.prediction.elecpriceenergycharts import (
    ElecPriceEnergyCharts,
    EnergyChartsElecPrice,
)
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")

FILE_TESTDATA_ELECPRICE_ENERGYCHARTS_JSON = DIR_TESTDATA.joinpath(
    "elecpriceforecast_energycharts.json"
)


@pytest.fixture
def provider(config_eos):
    """Fixture to create a ElecPriceProvider instance."""
    config_eos.merge_settings_from_dict(
        {
            "elecprice": {
                "provider": "ElecPriceEnergyCharts",
                "energycharts": {"bidding_zone": "DE-LU"},
            },
        }
    )
    provider = ElecPriceEnergyCharts()
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
        input_data = json.load(f_res)
    """Fixture that returns sample forecast data report."""
    return input_data


@pytest.fixture
def cache_store():
    """A pytest fixture that creates a new CacheFileStore instance for testing."""
    return CacheFileStore()


class TestElecPriceEnergyCharts:
    # ------------------------------------------------
    # General forecast
    # ------------------------------------------------

    def test_singleton_instance(self, provider):
        """Test that ElecPriceForecast behaves as a singleton."""
        another_instance = ElecPriceEnergyCharts()
        assert provider is another_instance

    def test_invalid_provider(self, provider, monkeypatch):
        """Test requesting an unsupported provider."""
        monkeypatch.setenv("EOS_ELECPRICE__ELECPRICE_PROVIDER", "<invalid>")
        provider.config.reset_settings()
        assert not provider.enabled()

    # ------------------------------------------------
    # EnergyCharts
    # ------------------------------------------------

    @patch("akkudoktoreos.prediction.elecpriceenergycharts.logger.error")
    def test_validate_data_invalid_format(self, mock_logger, provider):
        """Test validation for invalid Energy-Charts data."""
        invalid_data = '{"invalid": "data"}'
        with pytest.raises(ValueError):
            provider._validate_data(invalid_data)
        mock_logger.assert_called_once_with(mock_logger.call_args[0][0])

    @patch("requests.get")
    def test_request_forecast(self, mock_get, provider, sample_energycharts_json):
        """Test requesting forecast from Energy-Charts."""
        # Mock response object
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = json.dumps(sample_energycharts_json)
        mock_get.return_value = mock_response

        # Test function
        energy_charts_data = provider._request_forecast()

        assert isinstance(energy_charts_data, EnergyChartsElecPrice)
        assert energy_charts_data.unix_seconds[0] == 1733785200
        assert energy_charts_data.price[0] == 92.85

    @pytest.mark.asyncio
    @patch("requests.get")
    async def test_update_data(self, mock_get, provider, sample_energycharts_json, cache_store):
        """Test fetching forecast from Energy-Charts."""
        # Mock response object
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = json.dumps(sample_energycharts_json)
        mock_get.return_value = mock_response

        cache_store.clear(clear_all=True)

        # Call the method
        ems_eos = get_ems()
        ems_eos.set_start_datetime(to_datetime("2024-12-11 00:00:00", in_timezone="Europe/Berlin"))
        await provider.update_data(force_enable=True, force_update=True)

        # Assert: Verify the result is as expected
        mock_get.assert_called_once()
        assert (
            len(provider) == 73
        )  # we have 48 datasets in the api response, we want to know 48h into the future. The data we get has already 23h into the future so we need only 25h more. 48+25=73

        # Assert we get hours prioce values by resampling
        np_price_array = await provider.key_to_array(
            key="elecprice_marketprice_wh",
            start_datetime=provider.ems_start_datetime,
            end_datetime=provider.end_datetime,
            fill_method="ffill",
        )
        assert len(np_price_array) == provider.total_hours

    @pytest.mark.asyncio
    @pytest.mark.parametrize("host_timezone", ["UTC", "Europe/Berlin"])
    @pytest.mark.parametrize("history_interval_minutes", [15, 60])
    @pytest.mark.parametrize(
        ("now", "last_price", "interval_minutes", "needs_update"),
        [
            ("2026-01-15 13:59:59", "2026-01-15 23:00", 15, True),
            ("2026-01-15 13:59:59", "2026-01-15 23:45", 15, False),
            ("2026-01-15 13:59:59", "2026-01-15 23:00", 60, False),
            ("2026-01-15 14:00:00", "2026-01-16 23:00", 15, True),
            ("2026-01-15 14:00:00", "2026-01-16 23:45", 15, False),
            ("2026-01-15 14:00:00", "2026-01-16 23:00", 60, False),
            ("2026-01-15 14:00:00", "2026-01-15 23:45", 15, True),
            ("2026-03-28 14:00:00", "2026-03-29 23:45", 15, False),
            ("2026-03-28 14:00:00", "2026-03-29 23:30", 15, True),
            ("2026-10-24 14:00:00", "2026-10-25 23:45", 15, False),
            ("2026-10-24 14:00:00", "2026-10-25 23:30", 15, True),
        ],
    )
    async def test_update_data_refreshes_incomplete_published_intervals(
        self,
        provider: ElecPriceEnergyCharts,
        set_other_timezone: Callable[[str], str],
        host_timezone: str,
        history_interval_minutes: int,
        now: str,
        last_price: str,
        interval_minutes: int,
        needs_update: bool,
    ) -> None:
        """Fetch missing source intervals without refreshing an already complete day."""
        set_other_timezone(host_timezone)
        provider.config.merge_settings_from_dict(
            {"general": {"latitude": 52.52, "longitude": 13.405}}
        )
        fixed_now = pd.Timestamp(now, tz="Europe/Berlin")
        start = to_datetime(fixed_now, in_timezone="Europe/Berlin").start_of("day")
        last_original = to_datetime(
            pd.Timestamp(last_price, tz="Europe/Berlin"), in_timezone="Europe/Berlin"
        )
        get_ems().set_start_datetime(start)
        history_index = pd.date_range(
            start=start.subtract(days=35),
            end=start,
            freq=f"{history_interval_minutes}min",
            inclusive="left",
        )
        source_index = pd.date_range(
            start=start,
            end=last_original,
            freq=f"{interval_minutes}min",
        )
        await provider.key_from_series(
            "elecprice_marketprice_raw_wh",
            pd.Series(0.0001, index=history_index.append(source_index)),
        )
        provider.highest_orig_datetime = last_original

        # Predicted values share the raw key but must not determine source coverage.
        # Use enough slots at a different resolution to dominate an unbounded estimate.
        predicted_interval_minutes = 60 if interval_minutes == 15 else 15
        predicted_index = pd.date_range(
            start=last_original.add(minutes=predicted_interval_minutes),
            periods=120,
            freq=f"{predicted_interval_minutes}min",
        )
        await provider.key_from_series(
            "elecprice_marketprice_raw_wh", pd.Series(0.00005, index=predicted_index)
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

        with (
            patch("akkudoktoreos.prediction.elecpriceenergycharts.pd", wraps=pd) as pandas,
            patch.object(provider, "_request_forecast", return_value=response) as request,
            patch.object(provider, "_predict", side_effect=predict),
        ):
            pandas.Timestamp.now.return_value = fixed_now
            await provider._update_data(force_update=False)

        if needs_update:
            # Request dates use the host timezone; the publication boundary uses Berlin.
            request.assert_called_once_with(
                start_date=start.in_timezone(host_timezone).format("YYYY-MM-DD"),
                force_update=False,
            )
            assert pd.Timestamp(provider.highest_orig_datetime) == response_index[-1]
            fetched = await provider.key_to_raw_series(
                key="elecprice_marketprice_raw_wh",
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

    @pytest.mark.asyncio
    @patch("requests.get")
    async def test_update_data_with_incomplete_forecast(self, mock_get, caplog, provider):
        """Test `_update_data` with incomplete or missing forecast data (cold start, fatal)."""
        incomplete_data: dict = {
            "license_info": "",
            "unix_seconds": [],
            "price": [],
            "unit": "",
            "deprecated": False
        }
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = json.dumps(incomplete_data)
        mock_get.return_value = mock_response
        with caplog.at_level("WARNING"):
            with pytest.raises(ValueError, match="No Energy-Charts electricity price data available"):
                await provider._update_data(force_update=True)

    @pytest.mark.asyncio
    async def test_update_data_keeps_quarter_hour_resolution(self, provider):
        # Use a range that does not overlap the hourly fixture data used by the
        # neighbouring tests; the provider is a singleton by design.
        start = to_datetime("2025-01-15 00:00:00", in_timezone="Europe/Berlin")
        get_ems().set_start_datetime(start)
        provider.highest_orig_datetime = None
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
        result = await provider.key_to_series(
            key="elecprice_marketprice_wh",
            start_datetime=start,
            end_datetime=start.add(hours=provider.config.prediction.hours),
            interval=to_duration("15 minutes"),
        )
        assert len(result) == provider.config.prediction.hours * 4
        assert result.index.to_series().diff().dropna().dt.total_seconds().unique().tolist() == [900.0]

    @pytest.mark.asyncio
    async def test_update_data_adds_fees(self, provider, elecfee_provider, config_eos):
        """Build the gross retail price from market price and the matching Module 3 fee.

        Also verifies the raw market price series stays fee-free, since it's what
        ETS/median training relies on.
        """
        fixed_fees_amt_kwh: float = (
            0.0205  # electricity_tax
            + 0.0132  # concession_fee
            + 0.00446  # kwkg_levy
            + 0.01559  # section_19_levy
            + 0.00941  # offshore_grid_levy
        )
        amt_kwh: list[float] = [  # includes dynamic network fees
            0.0095 + fixed_fees_amt_kwh,
            0.0953 + fixed_fees_amt_kwh,
            0.1565 + fixed_fees_amt_kwh,
            0.0953 + fixed_fees_amt_kwh,
        ]
        percent_amt: float = 19.0  # VAT %

        config_eos.merge_settings_from_dict(
            {
                "prediction": {
                    "hours": 48,
                },
                "elecfee": {
                    "provider": "ElecFeeFixed",
                    "elecfeefixed": {
                        "consumption_amt_kwh": {
                            "windows": [
                                {"start_time": "00:00", "duration": "7 hours", "value": amt_kwh[0]},
                                {"start_time": "07:00", "duration": "8 hours", "value": amt_kwh[1]},
                                {"start_time": "15:00", "duration": "5 hours", "value": amt_kwh[2]},
                                {"start_time": "20:00", "duration": "4 hours", "value": amt_kwh[3]},
                            ],
                        },
                        "consumption_percent_amt": {
                            "windows": [
                                {"start_time": "00:00", "duration": "24 hours", "value": percent_amt},
                            ],
                        },
                    },
                },
            },
        )
        ems_eos = get_ems()
        start = to_datetime("2026-01-15 00:00:00", in_timezone="Europe/Berlin")
        ems_eos.set_start_datetime(start)

        # Create fees prediction
        await elecfee_provider._update_data(force_update=True)
        timestamps = [start, start.add(hours=7), start.add(hours=15), start.add(hours=20)]
        energy_charts_data = EnergyChartsElecPrice(
            license_info="",
            unix_seconds=[int(timestamp.timestamp()) for timestamp in timestamps],
            price=[100.0] * len(timestamps),
            unit="EUR/MWh",
            deprecated=False,
        )
        with patch.object(provider, "_request_forecast", return_value=energy_charts_data):
            await provider._update_data(force_update=True)

        # Raw series must stay pure market price, unaffected by fees, at every
        # timestamp - including the ones covered by the ETS/median-predicted tail.
        raw_result = await provider.key_to_series(
            key="elecprice_marketprice_raw_wh",
            start_datetime=start,
            end_datetime=start.add(hours=provider.config.prediction.hours),
            interval=to_duration("15 minutes"),
        )
        raw_result_kwh = raw_result * 1000

        slots_for_test = (0*4, 7*4, 15*4, 20*4)
        for slot in slots_for_test:
            assert raw_result_kwh.iloc[slot] == pytest.approx(0.1)

        result = await provider.key_to_series(
            key="elecprice_marketprice_wh",
            start_datetime=start,
            end_datetime=start.add(hours=provider.config.prediction.hours),
            interval=to_duration("15 minutes"),
        )
        result_kwh = result * 1000

        rate_amt = 1.0 + percent_amt / 100.0
        for idx, slot in enumerate(slots_for_test):
            assert result_kwh.iloc[slot] == pytest.approx((raw_result_kwh.iloc[slot] + amt_kwh[idx]) * rate_amt)

    @pytest.mark.asyncio
    async def test_update_data_applies_fees_to_predicted_tail(self, provider, elecfee_provider, config_eos):
        """Predicted timestamps beyond the fetched data must still get fees applied.

        Regression test for a bug where the ETS/median-extrapolated tail of the
        series was written to elecprice_marketprice_wh without ever going through
        apply_fees(), silently dropping VAT and all fee components for any
        timestamp past what Energy-Charts had actually published.
        """
        fixed_fees_amt_kwh: float = (
            0.0205  # electricity_tax
            + 0.0132  # concession_fee
            + 0.00446  # kwkg_levy
            + 0.01559  # section_19_levy
            + 0.00941  # offshore_grid_levy
        )
        amt_kwh: list[float] = [  # includes dynamic network fees
            0.0095 + fixed_fees_amt_kwh,
            0.0953 + fixed_fees_amt_kwh,
            0.1565 + fixed_fees_amt_kwh,
            0.0953 + fixed_fees_amt_kwh,
        ]
        percent_amt: float = 19.0  # VAT %
        config_eos.merge_settings_from_dict(
            {
                "prediction": {
                    "hours": 48,
                },
                "elecfee": {
                    "provider": "ElecFeeFixed",
                    "elecfeefixed": {
                        "consumption_amt_kwh": {
                            "windows": [
                                {"start_time": "00:00", "duration": "7 hours", "value": amt_kwh[0]},
                                {"start_time": "07:00", "duration": "8 hours", "value": amt_kwh[1]},
                                {"start_time": "15:00", "duration": "5 hours", "value": amt_kwh[2]},
                                {"start_time": "20:00", "duration": "4 hours", "value": amt_kwh[3]},
                            ],
                        },
                        "consumption_percent_amt": {
                            "windows": [
                                {"start_time": "00:00", "duration": "24 hours", "value": percent_amt},
                            ],
                        },
                    },
                },
                "elecprice": {
                    "provider": "ElecPriceEnergyCharts",
                },
            },
        )
        ems_eos = get_ems()
        start = to_datetime("2026-01-15 00:00:00", in_timezone="Europe/Berlin")
        ems_eos.set_start_datetime(start)
        await elecfee_provider._update_data(force_update=True)

        # Only 4 known market-price points, spanning just 20 hours of day 1.
        # With a 48h prediction horizon, everything from hour 21 onward has to
        # come from the median/ETS fallback rather than from the mocked API data.
        timestamps = [start, start.add(hours=7), start.add(hours=15), start.add(hours=20)]
        energy_charts_data = EnergyChartsElecPrice(
            license_info="",
            unix_seconds=[int(timestamp.timestamp()) for timestamp in timestamps],
            price=[100.0] * len(timestamps),  # 100 EUR/MWh = 0.1 EUR/kWh
            unit="EUR/MWh",
            deprecated=False,
        )
        with patch.object(provider, "_request_forecast", return_value=energy_charts_data):
            await provider._update_data(force_update=True)

        # Day 2, 06:00 - inside the predicted (non-fetched) range, and inside the
        # same 00:00-07:00 fee window as amt_kwh[0] on day 1.
        predicted_timestamp = start.add(hours=30)
        assert predicted_timestamp <= start.add(hours=provider.config.prediction.hours)

        raw_result = await provider.key_to_series(
            key="elecprice_marketprice_raw_wh",
            start_datetime=predicted_timestamp,
            end_datetime=predicted_timestamp.add(minutes=15),
            interval=to_duration("15 minutes"),
        )
        raw_result_kwh = raw_result * 1000
        # All four known market prices were equal (0.1 EUR/kWh); ETS on a flat
        # series should stay close to that, allowing for optimizer noise.
        assert raw_result_kwh.iloc[0] == pytest.approx(0.1, abs=0.01)

        result = await provider.key_to_series(
            key="elecprice_marketprice_wh",
            start_datetime=predicted_timestamp,
            end_datetime=predicted_timestamp.add(minutes=15),
            interval=to_duration("15 minutes"),
        )
        result_kwh = result * 1000
        rate_amt = 1.0 + percent_amt / 100.0
        # Derived from the actually-measured raw value above, not a hardcoded
        # 0.1, so this checks fee application on the real predicted price
        # rather than re-asserting what the ETS prediction should be.
        assert result_kwh.iloc[0] == pytest.approx((raw_result_kwh.iloc[0] + amt_kwh[0]) * rate_amt)

    @pytest.mark.asyncio
    async def test_update_data_covers_full_horizon_after_stale_fetch_outage(self, provider):
        """Regression test: needed_slots must include the gap when a fetch outage
        leaves highest_orig_datetime behind the current ems_start_datetime.

        Before the fix, `covered_slots` was clamped to 0 whenever
        highest_orig_datetime was older than ems_start_datetime, instead of
        being allowed to go negative. That left `needed_slots` at only
        `prediction.hours * slots_per_hour`, so the predicted tail only
        reached `highest_orig_datetime + prediction.hours` - ending before
        the actually-requested `ems_start_datetime + prediction.hours`
        whenever an outage persisted long enough for the two to diverge.
        """
        provider.config.prediction.hours = 48

        start = to_datetime("2026-01-15 00:00:00", in_timezone="Europe/Berlin")
        get_ems().set_start_datetime(start)

        # Seed enough 15-minute history for the weekly-ETS branch of _predict.
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
            patch.object(ElecPriceEnergyCharts, "_predict_ets", side_effect=fake_ets),
        ):
            await provider.update_data(force_enable=True, force_update=True)

        last_good = provider.highest_orig_datetime
        assert last_good is not None

        # Advance ems_start_datetime well past the last known data point, as
        # if a fetch outage has persisted for a while - highest_orig_datetime
        # is now *before* ems_start_datetime, not just close behind it.
        outage_gap_hours = 20
        new_start = to_datetime(last_good).add(hours=outage_gap_hours)
        get_ems().set_start_datetime(new_start)

        with (
            patch.object(
                provider, "_request_forecast", side_effect=requests.exceptions.ReadTimeout("boom")
            ),
            patch.object(ElecPriceEnergyCharts, "_predict_ets", side_effect=fake_ets),
        ):
            await provider.update_data(force_enable=True, force_update=True)

        # Fallback kept the stale history rather than raising (cold-start
        # fatality only applies when there's no history at all).
        assert provider.highest_orig_datetime == last_good

        # The predicted series must reach the end of the horizon measured
        # from the *current* ems_start_datetime - i.e. it must also backfill
        # the outage_gap_hours gap, not just prediction.hours beyond the
        # stale highest_orig_datetime.
        horizon_end = new_start.add(hours=provider.config.prediction.hours)
        raw_result = await provider.key_to_series(
            key="elecprice_marketprice_raw_wh",
            start_datetime=horizon_end.subtract(minutes=15),
            end_datetime=horizon_end,
            interval=to_duration("15 minutes"),
        )
        assert len(raw_result) == 1
        assert not raw_result.isna().any()

    @pytest.mark.parametrize(
        "status_code, exception",
        [(400, requests.exceptions.HTTPError), (500, requests.exceptions.HTTPError), (200, None)],
    )
    @patch("requests.get")
    def test_request_forecast_status_codes(
        self, mock_get, provider, sample_energycharts_json, status_code, exception
    ):
        """Test handling of various API status codes."""
        mock_response = Mock()
        mock_response.status_code = status_code
        mock_response.content = json.dumps(sample_energycharts_json)
        mock_response.raise_for_status.side_effect = (
            requests.exceptions.HTTPError if exception else None
        )
        mock_get.return_value = mock_response
        if exception:
            with pytest.raises(exception):
                provider._request_forecast()
        else:
            provider._request_forecast()

    @pytest.mark.asyncio
    @patch("requests.get")
    @patch("akkudoktoreos.core.cache.CacheFileStore")
    async def test_cache_integration(self, mock_cache, mock_get, provider, sample_energycharts_json):
        """Test caching of 8-day electricity price data."""
        # Mock response object
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = json.dumps(sample_energycharts_json)
        mock_get.return_value = mock_response

        # Mock cache object
        mock_cache_instance = mock_cache.return_value
        mock_cache_instance.get.return_value = None  # Simulate no cache

        await provider._update_data(force_update=True)
        mock_cache_instance.create.assert_called_once()
        mock_cache_instance.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_key_to_array_resampling(self, provider):
        """Test resampling of forecast data to NumPy array."""
        await provider.update_data(force_update=True)
        array = await provider.key_to_array(
            key="elecprice_marketprice_wh",
            start_datetime=provider.ems_start_datetime,
            end_datetime=provider.end_datetime,
            fill_method="ffill",
        )
        assert isinstance(array, np.ndarray)
        assert len(array) == provider.total_hours

    @patch("requests.get")
    def test_request_forecast_url_bidding_zone_is_value(self, mock_get, provider, sample_energycharts_json):
        """Test that the bidding zone in the API URL uses the enum *value* (e.g. 'DE-LU'),
        not the enum repr (e.g. 'EnergyChartsBiddingZones.DE_LU').

        Regression test for: bzn=EnergyChartsBiddingZones.DE_LU appearing in the URL
        instead of bzn=DE-LU, which caused a 400 Bad Request from the Energy-Charts API.
        """
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = json.dumps(sample_energycharts_json)
        mock_get.return_value = mock_response

        provider._request_forecast(force_update=True)

        assert mock_get.called, "requests.get was never called"
        actual_url: str = mock_get.call_args[0][0]

        # Extract the bzn= query parameter value from the URL
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(actual_url)
        query_params = parse_qs(parsed.query)

        assert "bzn" in query_params, f"'bzn' parameter missing from URL: {actual_url}"
        bzn_value = query_params["bzn"][0]

        # Must be the raw enum value, never contain a class name or dot notation
        assert "." not in bzn_value, (
            f"Bidding zone in URL looks like an enum repr: '{bzn_value}'. "
            f"Use .value when building the URL, not str(enum)."
        )
        assert bzn_value == provider.config.elecprice.energycharts.bidding_zone, (
            f"Expected bzn='{provider.config.elecprice.energycharts.bidding_zone}' "
            f"but got bzn='{bzn_value}' in URL: {actual_url}"
        )

    # ------------------------------------------------
    # Development Energy Charts
    # ------------------------------------------------

    @pytest.mark.skip(reason="For development only")
    def test_energycharts_development_forecast_data(self, provider):
        """Fetch data from real Energy-Charts server."""
        # Preset, as this is usually done by update_data()
        provider.ems_start_datetime = to_datetime("2024-10-26 00:00:00")

        energy_charts_data = provider._request_forecast()

        with FILE_TESTDATA_ELECPRICE_ENERGYCHARTS_JSON.open(
            "w", encoding="utf-8", newline="\n"
        ) as f_out:
            json.dump(energy_charts_data, f_out, indent=4)
