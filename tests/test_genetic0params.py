"""GENETIC0 parameter preparation: time alignment, tariff coverage, and measured SoC."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import pandas as pd
import pendulum
import pytest
import pytest_asyncio

from akkudoktoreos.core.coreabc import get_ems, get_measurement
from akkudoktoreos.optimization.genetic0 import genetic0prepare
from akkudoktoreos.optimization.genetic0.genetic0request import (
    Genetic0Request,
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
        with patch.object(Genetic0Request, "prediction", prediction):
            parameters = await Genetic0Request.prepare()

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
        with patch.object(Genetic0Request, "prediction", prediction):
            parameters = await Genetic0Request.prepare()

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
                    "batteries": {
                        "storage": {"profile_adjustment": {"nominal_capacity_wh": 2000, "max_charge_power_w": 1000}}
                    },
                    "inverters": {"inverter": {"battery_id": "storage", "profile_adjustment": {"nominal_power_w": 1000}}},
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
        with patch.object(Genetic0Request, "prediction", prediction):
            parameters = await Genetic0Request.prepare()

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
                    "batteries": {
                        "storage": {"profile_adjustment": {"nominal_capacity_wh": 2000, "max_charge_power_w": 1000}}
                    },
                    "inverters": {"inverter": {"battery_id": "storage", "profile_adjustment": {"nominal_power_w": 1000}}},
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
        with patch.object(Genetic0Request, "prediction", prediction):
            parameters = await Genetic0Request.prepare()

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
                    "batteries": {
                        "storage": {"profile_adjustment": {"nominal_capacity_wh": 2000, "max_charge_power_w": 1000}}
                    },
                    "inverters": {"inverter": {"battery_id": "storage", "profile_adjustment": {"nominal_power_w": 1000}}},
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
        with patch.object(Genetic0Request, "prediction", prediction):
            parameters = await Genetic0Request.prepare()

        assert parameters is not None
        assert parameters.pv_battery is not None
        assert parameters.pv_battery.initial_soc_percentage == 42


class TestGenetic0DemoData:
    """Demo device data used when no battery / electric vehicle is configured."""

    @pytest.mark.asyncio
    async def test_demo_devices_use_nominal_capacity(self, config_eos):
        config_eos.merge_settings_from_dict(
            {
                "prediction": {"hours": 48},
                "optimization": {"genetic0": {"horizon_hours": 24}},
                "elecfee": {"provider": None},
                "devices": {
                    "max_batteries": 1,
                    "max_electric_vehicles": 1,
                    "max_inverters": 0,
                    "max_home_appliances": 0,
                    "batteries": None,
                    "electric_vehicles": None,
                },
            }
        )
        get_ems().set_start_datetime(to_datetime("2026-08-01T14:00:00+00:00"))
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
        with patch.object(Genetic0Request, "prediction", prediction):
            parameters = await Genetic0Request.prepare()

        assert parameters is not None
        # The demo capacities must reach the profile adjustment of the settings;
        # the generic default profile provides no capacity of its own.
        assert config_eos.devices.batteries["battery1"].profile_adjustment.nominal_capacity_wh == 8000
        assert (
            config_eos.devices.electric_vehicles["ev1"].profile_adjustment.nominal_capacity_wh
            == 50000
        )
        assert parameters.pv_battery is not None
        assert parameters.pv_battery.capacity_wh == 8000
        assert parameters.ev is not None
        assert parameters.ev.capacity_wh == 50000

    @pytest.mark.asyncio
    async def test_battery_configuration_error_uses_defaults_not_demo_data(self, config_eos):
        """A misconfigured battery keeps its configuration, with defaults for the rest."""
        from loguru import logger

        config_eos.merge_settings_from_dict(
            {
                "prediction": {"hours": 48},
                "optimization": {"genetic0": {"horizon_hours": 24}},
                "elecfee": {"provider": None},
                "devices": {
                    "max_batteries": 1,
                    "max_electric_vehicles": 0,
                    "max_inverters": 0,
                    "max_home_appliances": 0,
                    # generic-lfp provides no capacity and the adjustment none either.
                    "batteries": {"storage": {"min_soc_percentage": 20}},
                    "electric_vehicles": None,
                },
            }
        )
        get_ems().set_start_datetime(to_datetime("2026-08-01T14:00:00+00:00"))
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
        messages: list[str] = []
        sink_id = logger.add(
            lambda message: messages.append(message.record["message"]), level="ERROR"
        )
        try:
            with patch.object(Genetic0Request, "prediction", prediction):
                parameters = await Genetic0Request.prepare()
        finally:
            logger.remove(sink_id)
        assert parameters is not None
        assert parameters.pv_battery is not None
        # The configured battery, not the demo battery: its own SoC minimum.
        assert parameters.pv_battery.device_id == "storage"
        assert parameters.pv_battery.min_soc_percentage == 20
        assert parameters.pv_battery.capacity_wh == 8000
        assert any(
            "Battery 'storage'" in m and "nominal capacity 8000 Wh" in m for m in messages
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "settings_class, method, field, device_id",
        [
            ("BatteriesCommonSettings", "to_genetic0_pv_bat_param", "pv_battery", "storage"),
            ("BatteriesCommonSettings", "to_genetic0_ev_bat_param", "ev", "car"),
            ("InverterCommonSettings", "to_genetic0_param", "inverter", "hybrid"),
        ],
    )
    async def test_device_that_cannot_be_prepared_is_left_out(
        self, config_eos, monkeypatch, settings_class, method, field, device_id
    ):
        """No demo device replaces a configured device that cannot be prepared."""
        from loguru import logger

        from akkudoktoreos.devices.settings import batterysettings, invertersettings

        config_eos.merge_settings_from_dict(
            {
                "prediction": {"hours": 48},
                "optimization": {"genetic0": {"horizon_hours": 24}},
                "elecfee": {"provider": None},
                "devices": {
                    "max_batteries": 1,
                    "max_electric_vehicles": 1,
                    "max_inverters": 1,
                    "max_home_appliances": 0,
                    "batteries": {
                        "storage": {"profile_adjustment": {"nominal_capacity_wh": 5000}}
                    },
                    "electric_vehicles": {
                        "car": {"profile_adjustment": {"nominal_capacity_wh": 40000}}
                    },
                    "inverters": {
                        "hybrid": {
                            "battery_id": "storage",
                            "profile_adjustment": {"nominal_power_w": 6000},
                        }
                    },
                },
            }
        )
        configured = config_eos.devices.model_dump(exclude_computed_fields=True)
        module = batterysettings if settings_class == "BatteriesCommonSettings" else invertersettings

        def broken(self, *args, **kwargs):
            raise RuntimeError("conversion failed")

        monkeypatch.setattr(getattr(module, settings_class), method, broken)
        get_ems().set_start_datetime(to_datetime("2026-08-01T14:00:00+00:00"))
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
        messages: list[str] = []
        sink_id = logger.add(
            lambda message: messages.append(message.record["message"]), level="ERROR"
        )
        try:
            with patch.object(Genetic0Request, "prediction", prediction):
                parameters = await Genetic0Request.prepare()
        finally:
            logger.remove(sink_id)
        assert parameters is not None
        assert getattr(parameters, field) is None
        assert any(f"'{device_id}'" in m and "left out" in m for m in messages)
        assert not any("demo" in m for m in messages)
        # The other devices are prepared, the configuration is unchanged.
        for other in {"pv_battery", "ev", "inverter"} - {field}:
            assert getattr(parameters, other) is not None
        assert config_eos.devices.model_dump(exclude_computed_fields=True) == configured


class TestGenetic0PrepareOverrides:
    """Run-time overrides reach the parameters; unknown keys are logged and ignored."""

    @pytest.fixture
    def request_parameters(self) -> Genetic0Request:
        return Genetic0Request.model_validate(
            {
                "ems": {
                    "pv_prognose_wh": [0.0, 100.0],
                    "gesamtlast": [100.0, 100.0],
                    "strompreis_euro_pro_wh": [0.0003, 0.0003],
                    "einspeiseverguetung_euro_pro_wh": 0.00008,
                    "preis_euro_pro_wh_akku": 0.0,
                },
                "pv_akku": None,
                "eauto": None,
                "inverter": None,
            }
        )

    @pytest.fixture
    def errors(self, monkeypatch) -> list[str]:
        logged: list[str] = []
        monkeypatch.setattr(
            genetic0prepare.logger, "error", lambda message, *args: logged.append(message)
        )
        return logged

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "overrides, generations, seed",
        [
            (None, 77, 5),
            ({}, 77, 5),
            ({"generations": None, "seed": None}, 77, 5),
            ({"generations": 12}, 12, 5),
            ({"generations": 12, "seed": 42}, 12, 42),
        ],
    )
    async def test_known_overrides_replace_the_settings(
        self, config_eos, request_parameters, errors, overrides, generations, seed
    ):
        config_eos.merge_settings_from_dict(
            {"optimization": {"genetic0": {"generations": 77, "seed": 5}}}
        )
        start = to_datetime("2026-01-14T10:00:00", in_timezone="Europe/Berlin")
        parameters = await genetic0prepare.prepare_from_request(
            request_parameters, start, None, overrides
        )
        assert parameters.request is request_parameters
        assert parameters.start_hour == 10
        assert (parameters.generations, parameters.seed) == (generations, seed)
        assert errors == []

    @pytest.mark.asyncio
    async def test_unknown_override_is_logged_and_ignored(
        self, config_eos, request_parameters, errors
    ):
        config_eos.merge_settings_from_dict(
            {"optimization": {"genetic0": {"generations": 77, "seed": 5}}}
        )
        start = to_datetime("2026-01-14T10:00:00", in_timezone="Europe/Berlin")
        parameters = await genetic0prepare.prepare_from_request(
            request_parameters, start, None, {"individuals": 3, "generations": 12}
        )
        assert (parameters.generations, parameters.seed) == (12, 5)
        assert errors == ["GENETIC0: Unknown override '{}' ignored."]


class TestGenetic0PrepareWarmStart:
    """The start solution of the previous run seeds the next one."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "warm_start, start_solution",
        [
            (None, None),
            (SimpleNamespace(start_solution=None), None),
            (SimpleNamespace(start_solution=[]), None),
            (SimpleNamespace(start_solution=[1.0, 0.0, 2.0]), [1.0, 0.0, 2.0]),
        ],
        ids=["no-previous-run", "no-start-solution", "empty-start-solution", "start-solution"],
    )
    async def test_previous_start_solution_reaches_the_preparation(
        self, config_eos, monkeypatch, warm_start, start_solution
    ):
        request = Mock()
        legacy_prepare = AsyncMock(return_value=request)
        monkeypatch.setattr(Genetic0Request, "prepare", legacy_prepare)
        start = to_datetime("2026-01-14T10:00:00", in_timezone="Europe/Berlin")
        parameters = await genetic0prepare.prepare(start, warm_start)
        legacy_prepare.assert_awaited_once_with(start_solution=start_solution)
        assert parameters.request is request
