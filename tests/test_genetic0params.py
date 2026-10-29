"""GENETIC0 parameter preparation: time alignment, tariff coverage, and measured SoC."""

from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import pandas as pd
import pendulum
import pytest
import pytest_asyncio

from akkudoktoreos.core.coreabc import get_ems, get_measurement
from akkudoktoreos.optimization.genetic0.genetic0params import (
    Genetic0OptimizationParameters,
)
from akkudoktoreos.utils.datetimeutil import to_datetime


class TestGenetic0ParameterTimeAlignment:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "start",
        [
            # Normal day: 23 elapsed hours since local midnight.
            pendulum.datetime(2026, 10, 24, 23, tz="Europe/Berlin"),
            # Fall back (CEST -> CET): 24 elapsed hours since local midnight.
            pendulum.datetime(2026, 10, 25, 23, tz="Europe/Berlin"),
            # Spring forward (CET -> CEST): 22 elapsed hours since local midnight.
            pendulum.datetime(2026, 3, 29, 23, tz="Europe/Berlin"),
        ],
        ids=["normal", "fall-back", "spring-forward"],
    )
    async def test_parameter_arrays_start_hour_aligned_on_dst_days(self, config_eos, start):
        config_eos.merge_settings_from_dict(
            {
                "general": {"timezone_override": "Europe/Berlin"},
                "prediction": {"hours": 48},
                "optimization": {"genetic0": {"horizon_hours": 24}},
                "feedintariff": {"provider": "FeedInTariffDvhubOnline"},
                "elecfee": {"provider": None},
                "devices": {
                    "max_batteries": 0,
                    "max_electric_vehicles": 0,
                    "max_inverters": 0,
                    "max_home_appliances": 0,
                },
            }
        )
        get_ems().set_start_datetime(start)
        ems_start = get_ems().start_datetime
        assert ems_start.hour == 23

        # The tariff is only published from the start slot on, as a live
        # provider would do late in the evening.
        prices = pd.Series(
            np.full(24 * 4, 0.0001),
            index=pd.date_range(ems_start, periods=24 * 4, freq="15min"),
        )
        array_starts = []

        async def read_array(**kwargs):
            array_starts.append(kwargs["start_datetime"])
            return np.full(48, 1.0)

        prediction = Mock(
            update_data=AsyncMock(),
            key_to_array=AsyncMock(side_effect=read_array),
            key_to_raw_series=AsyncMock(return_value=prices),
        )
        with patch.object(Genetic0OptimizationParameters, "prediction", prediction):
            parameters = await Genetic0OptimizationParameters.prepare()

        assert parameters is not None
        # Index ``start.hour`` of every array is the start slot.
        assert array_starts
        for array_start in array_starts:
            assert array_start.add(hours=ems_start.hour) == ems_start
        tariffs = parameters.ems.feed_in_tariff_per_wh
        assert isinstance(tariffs, list)
        assert np.isfinite(tariffs[ems_start.hour : ems_start.hour + 24]).all()


class TestGenetic0TariffCoverage:
    """Tariff arrays indexed from UTC midnight, independent of the host timezone."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("published_hours", [20, 24])
    async def test_dvhub_tariff_requires_real_control_horizon(self,
        config_eos, published_hours, caplog
    ):
        config_eos.merge_settings_from_dict(
            {
                "general": {"timezone_override": "UTC"},
                "prediction": {"hours": 48},
                "optimization": {"genetic0": {"horizon_hours": 24}},
                "feedintariff": {"provider": "FeedInTariffDvhubOnline"},
                "elecfee": {"provider": None},
                "devices": {
                    "max_batteries": 0,
                    "max_electric_vehicles": 0,
                    "max_inverters": 0,
                    "max_home_appliances": 0,
                },
            }
        )
        start = to_datetime("2026-08-01T00:00:00+00:00", in_timezone="UTC")
        get_ems().set_start_datetime(start)
        prices = pd.Series(
            np.arange(published_hours * 4, dtype=float) / 1_000_000,
            index=pd.date_range(start, periods=published_hours * 4, freq="15min"),
        )

        async def read_array(**kwargs):
            return np.full(48, 1.0)

        prediction = Mock(
            update_data=AsyncMock(),
            key_to_array=AsyncMock(side_effect=read_array),
            key_to_raw_series=AsyncMock(return_value=prices),
        )
        with patch.object(Genetic0OptimizationParameters, "prediction", prediction):
            parameters = await Genetic0OptimizationParameters.prepare()

        prediction.update_data.assert_awaited_once()
        assert config_eos.feedintariff.provider == "FeedInTariffDvhubOnline"
        if published_hours < 24:
            assert parameters is None
            assert "Missing feed-in tariff within the GENETIC0 control horizon" in caplog.text
        else:
            assert parameters is not None
            tariffs = parameters.ems.feed_in_tariff_per_wh
            assert isinstance(tariffs, list)
            assert tariffs[:24] == pytest.approx(
                [(4 * hour + 1.5) / 1_000_000 for hour in range(24)]
            )
            assert np.isnan(tariffs[24:]).all()


class TestGenetic0MeasuredSOC:
    @pytest_asyncio.fixture
    async def isolated_measurement(self, config_eos):
        """Keep synthetic records out of the process-wide measurement singleton."""
        measurement = get_measurement()
        await measurement.delete_by_datetime(None, None)
        try:
            yield measurement
        finally:
            await measurement.delete_by_datetime(None, None)

    @pytest.mark.asyncio
    async def test_soc_read_late_in_slot_beats_previous_reading(self, config_eos, isolated_measurement):
        config_eos.merge_settings_from_dict(
            {
                "prediction": {"hours": 48},
                "optimization": {"genetic0": {"horizon_hours": 24}},
                "elecfee": {"provider": None},
                "devices": {
                    "max_batteries": 1,
                    "max_electric_vehicles": 0,
                    "max_inverters": 1,
                    "max_home_appliances": 0,
                    "batteries": {"storage": {"capacity_wh": 2000, "max_charge_power_w": 1000}},
                    "inverters": {"inverter": {"battery_id": "storage", "max_power_w": 1000}},
                },
            }
        )
        # A run at 14:40 belongs to the 14:00 slot. The previous run (13:40) is closer
        # to the slot start than the reading taken now.
        run_time = to_datetime("2026-08-01T14:40:00+00:00")
        get_ems().set_start_datetime(run_time)
        measurement = isolated_measurement
        await measurement.update_value(run_time.subtract(hours=1), "storage-soc-factor", 0.10)
        await measurement.update_value(run_time, "storage-soc-factor", 0.42)

        tariff = pd.Series(
            0.00008, index=pd.date_range("2026-08-01T00:00:00Z", periods=72 * 4, freq="15min")
        )

        async def read_array(**kwargs):
            return np.full(48, 0.0003)

        prediction = Mock(
            update_data=AsyncMock(),
            key_to_array=AsyncMock(side_effect=read_array),
            key_to_raw_series=AsyncMock(return_value=tariff),
        )
        with patch.object(Genetic0OptimizationParameters, "prediction", prediction):
            parameters = await Genetic0OptimizationParameters.prepare()

        assert parameters is not None
        assert parameters.pv_battery is not None
        assert parameters.pv_battery.initial_soc_percentage == 42

    @pytest.mark.asyncio
    async def test_meter_only_record_does_not_mask_soc(self, config_eos, isolated_measurement):
        config_eos.merge_settings_from_dict(
            {
                "prediction": {"hours": 48},
                "optimization": {"genetic0": {"horizon_hours": 24}},
                "elecfee": {"provider": None},
                "measurement": {"load_emr_keys": ["load_meter"]},
                "devices": {
                    "max_batteries": 1,
                    "max_electric_vehicles": 0,
                    "max_inverters": 1,
                    "max_home_appliances": 0,
                    "batteries": {"storage": {"capacity_wh": 2000, "max_charge_power_w": 1000}},
                    "inverters": {"inverter": {"battery_id": "storage", "max_power_w": 1000}},
                },
            }
        )
        run_time = to_datetime("2026-08-01T14:40:00+00:00")
        get_ems().set_start_datetime(run_time)
        measurement = isolated_measurement
        # SoC through the measurement API at 13:30, the adapter's meter reading at the
        # slot start (14:00); the run at 14:40 must still find the SoC.
        await measurement.update_value(run_time.subtract(minutes=70), "storage-soc-factor", 0.42)
        await measurement.update_value(get_ems().start_datetime, "load_meter", 1234.5)

        tariff = pd.Series(
            0.00008, index=pd.date_range("2026-08-01T00:00:00Z", periods=72 * 4, freq="15min")
        )

        async def read_array(**kwargs):
            return np.full(48, 0.0003)

        prediction = Mock(
            update_data=AsyncMock(),
            key_to_array=AsyncMock(side_effect=read_array),
            key_to_raw_series=AsyncMock(return_value=tariff),
        )
        with patch.object(Genetic0OptimizationParameters, "prediction", prediction):
            parameters = await Genetic0OptimizationParameters.prepare()

        assert parameters is not None
        assert parameters.pv_battery is not None
        assert parameters.pv_battery.initial_soc_percentage == 42

    @pytest.mark.asyncio
    async def test_older_missing_value_does_not_spoil_current_soc(self, config_eos, isolated_measurement):
        config_eos.merge_settings_from_dict(
            {
                "prediction": {"hours": 48},
                "optimization": {"genetic0": {"horizon_hours": 24}},
                "elecfee": {"provider": None},
                "devices": {
                    "max_batteries": 1,
                    "max_electric_vehicles": 0,
                    "max_inverters": 1,
                    "max_home_appliances": 0,
                    "batteries": {"storage": {"capacity_wh": 2000, "max_charge_power_w": 1000}},
                    "inverters": {"inverter": {"battery_id": "storage", "max_power_w": 1000}},
                },
            }
        )
        run_time = to_datetime("2026-08-01T14:40:00+00:00")
        get_ems().set_start_datetime(run_time)
        measurement = isolated_measurement
        # An older reading stored as pandas NA (e.g. a JSON null in a Float64 series).
        await measurement.update_value(run_time.subtract(hours=1), "storage-soc-factor", pd.NA)
        await measurement.update_value(run_time, "storage-soc-factor", 0.42)

        tariff = pd.Series(
            0.00008, index=pd.date_range("2026-08-01T00:00:00Z", periods=72 * 4, freq="15min")
        )

        async def read_array(**kwargs):
            return np.full(48, 0.0003)

        prediction = Mock(
            update_data=AsyncMock(),
            key_to_array=AsyncMock(side_effect=read_array),
            key_to_raw_series=AsyncMock(return_value=tariff),
        )
        with patch.object(Genetic0OptimizationParameters, "prediction", prediction):
            parameters = await Genetic0OptimizationParameters.prepare()

        assert parameters is not None
        assert parameters.pv_battery is not None
        assert parameters.pv_battery.initial_soc_percentage == 42
