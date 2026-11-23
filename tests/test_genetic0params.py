"""GENETIC0 parameter preparation: time alignment, tariff coverage, and measured SoC."""

import json
import math
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import pandas as pd
import pendulum
import pytest
import pytest_asyncio

from akkudoktoreos.core.coreabc import get_ems, get_measurement
from akkudoktoreos.optimization.genetic0 import genetic0prepare
from akkudoktoreos.optimization.genetic0.genetic0parameters import (
    Genetic0Parameters,
    parameters_from_dict,
    parameters_to_dict,
)
from akkudoktoreos.optimization.genetic0.genetic0request import (
    Genetic0Request,
)
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration


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
        with patch.object(genetic0prepare, "get_prediction", return_value=prediction):
            parameters = await genetic0prepare.prepare_request(
                get_ems().start_datetime, get_ems().observation_datetime
            )

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
    async def test_dvhub_tariff_short_of_the_control_horizon_is_carried_forward(self,
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
        with patch.object(genetic0prepare, "get_prediction", return_value=prediction):
            parameters = await genetic0prepare.prepare_request(
                get_ems().start_datetime, get_ems().observation_datetime
            )

        prediction.update_data.assert_awaited_once()
        assert config_eos.feedintariff.provider == "FeedInTariffDvhubOnline"
        tariffs = parameters.ems.feed_in_tariff_per_wh
        assert isinstance(tariffs, list)
        published = [(4 * hour + 1.5) / 1_000_000 for hour in range(published_hours)]
        assert tariffs[:published_hours] == pytest.approx(published)
        if published_hours < 24:
            # The run is not cancelled: the last known tariff is carried forward (step 3b).
            assert "Missing feed-in tariff within the control horizon" in caplog.text
            assert tariffs[published_hours:] == pytest.approx(
                [published[-1]] * (48 - published_hours)
            )
        else:
            # A covered control horizon is used as it is.
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
        with patch.object(genetic0prepare, "get_prediction", return_value=prediction):
            parameters = await genetic0prepare.prepare_request(
                get_ems().start_datetime, get_ems().observation_datetime
            )

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
        with patch.object(genetic0prepare, "get_prediction", return_value=prediction):
            parameters = await genetic0prepare.prepare_request(
                get_ems().start_datetime, get_ems().observation_datetime
            )

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
        with patch.object(genetic0prepare, "get_prediction", return_value=prediction):
            parameters = await genetic0prepare.prepare_request(
                get_ems().start_datetime, get_ems().observation_datetime
            )

        assert parameters is not None
        assert parameters.pv_battery is not None
        assert parameters.pv_battery.initial_soc_percentage == 42


class TestGenetic0DemoData:
    """No demo devices: devices that are not configured are left out (step 3b)."""

    @pytest.mark.asyncio
    async def test_unconfigured_devices_are_left_out_and_not_written(self, config_eos):
        config_eos.merge_settings_from_dict(
            {
                "prediction": {"hours": 48},
                "optimization": {"genetic0": {"horizon_hours": 24}},
                "elecfee": {"provider": None},
                "devices": {
                    "max_batteries": 1,
                    "max_electric_vehicles": 1,
                    "max_inverters": 1,
                    "max_home_appliances": 1,
                    "batteries": None,
                    "electric_vehicles": None,
                    "inverters": None,
                    "home_appliances": {},
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
        with patch.object(genetic0prepare, "get_prediction", return_value=prediction):
            parameters = await genetic0prepare.prepare_request(
                get_ems().start_datetime, get_ems().observation_datetime
            )

        assert (
            parameters.pv_battery,
            parameters.ev,
            parameters.inverter,
            parameters.dishwasher,
        ) == (None, None, None, None)
        devices = config_eos.devices
        assert (
            devices.batteries,
            devices.electric_vehicles,
            devices.inverters,
            devices.home_appliances or None,
        ) == (None, None, None, None)

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
            with patch.object(genetic0prepare, "get_prediction", return_value=prediction):
                parameters = await genetic0prepare.prepare_request(
                    get_ems().start_datetime, get_ems().observation_datetime
                )
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
        "device, method, field, device_id",
        [
            ("battery", "battery_request", "pv_battery", "storage"),
            ("electric vehicle", "electric_vehicle_request", "ev", "car"),
            ("inverter", "inverter_request", "inverter", "hybrid"),
        ],
    )
    async def test_device_that_cannot_be_prepared_is_left_out(
        self, config_eos, monkeypatch, device, method, field, device_id
    ):
        """No demo device replaces a configured device that cannot be prepared."""
        from loguru import logger


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
        def broken(*args, **kwargs):
            raise RuntimeError("conversion failed")

        # The conversion of the settings belongs to GENETIC0's preparation (ADR 0004).
        monkeypatch.setattr(genetic0prepare, method, broken)
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
            with patch.object(genetic0prepare, "get_prediction", return_value=prediction):
                parameters = await genetic0prepare.prepare_request(
                    get_ems().start_datetime, get_ems().observation_datetime
                )
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
            request_parameters, start, start, None, overrides
        )
        assert parameters.request is request_parameters
        assert parameters.start_hour == 10
        assert (parameters.settings.generations, parameters.settings.seed) == (generations, seed)
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
            request_parameters, start, start, None, {"individuals": 3, "generations": 12}
        )
        assert (parameters.settings.generations, parameters.settings.seed) == (12, 5)
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
        monkeypatch.setattr(genetic0prepare, "prepare_request", legacy_prepare)
        monkeypatch.setattr(
            genetic0prepare.Genetic0Parameters,
            "from_request",
            classmethod(lambda cls, **fields: SimpleNamespace(**fields)),
        )
        start = to_datetime("2026-01-14T10:00:00", in_timezone="Europe/Berlin")
        observation = start.add(minutes=7)
        parameters = await genetic0prepare.prepare(start, observation, warm_start)
        legacy_prepare.assert_awaited_once_with(start, observation, start_solution)
        assert parameters.request is request


class TestGenetic0Presentation:
    """The data the solution conversion presents, read once before the run."""

    @pytest.mark.asyncio
    async def test_window_and_missing_values_survive_the_replay_file(self, config_eos, monkeypatch):
        config_eos.merge_settings_from_dict(
            {"general": {"timezone_override": "Europe/Berlin"}, "prediction": {"hours": 48}}
        )
        windows = []

        async def key_to_array(*, key, start_datetime, end_datetime, interval, fill_method):
            windows.append((key, start_datetime, end_datetime, fill_method))
            hours = int((end_datetime - start_datetime).total_seconds() // 3600)
            values = np.arange(hours, dtype=float)
            values[1] = np.nan
            return values

        prediction = SimpleNamespace(
            record_keys=["pvforecast_ac_power", "feed_in_tariff_wh"], key_to_array=key_to_array
        )
        monkeypatch.setattr(genetic0prepare, "get_prediction", lambda: prediction)
        # 09:00 UTC is 10:00 in Berlin: the conversion counts from the later local hour.
        start = to_datetime("2026-01-14T09:00:00+00:00")
        settings = genetic0prepare._run_settings(None)

        presentation = await genetic0prepare._presentation(start, settings, False)

        assert [(key, fill) for key, _, _, fill in windows] == [
            ("pvforecast_ac_power", "linear"),
            ("feed_in_tariff_wh", "ffill"),
        ]
        assert {(end - begin).total_seconds() for _, begin, end, _ in windows} == {
            to_duration("38 hours").total_seconds()
        }
        assert set(presentation.prediction_series) == {"pvforecast_ac_power", "feed_in_tariff_wh"}
        assert presentation.timezone == "Europe/Berlin"
        series = presentation.prediction_series["pvforecast_ac_power"]
        assert len(series) == 38 and math.isnan(series[1])

        request = Genetic0Request.model_validate(
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
        parameters = Genetic0Parameters.from_request(
            request=request, start_hour=start.hour, settings=settings, presentation=presentation
        )
        data = json.loads(json.dumps(parameters_to_dict(parameters)))
        assert data["presentation"]["prediction_series"]["pvforecast_ac_power"][1] is None

        restored = parameters_from_dict(data).presentation
        assert restored.start_datetime == presentation.start_datetime
        assert restored.start_datetime.timezone_name == "Europe/Berlin"
        restored_series = restored.prediction_series["pvforecast_ac_power"]
        assert math.isnan(restored_series[1])
        assert restored_series[2:] == series[2:]

    def test_unconfigured_prediction_hours_fall_back_to_48(self, monkeypatch):
        config = Mock()
        config.prediction.hours = None
        config.devices.electric_vehicles = None
        config.devices.batteries = None
        config.optimization.genetic0.penalties = {}
        monkeypatch.setattr(genetic0prepare, "get_config", lambda: config)
        errors: list[str] = []
        monkeypatch.setattr(
            genetic0prepare.logger, "error", lambda message, *args: errors.append(message)
        )
        assert genetic0prepare._run_settings(None).prediction_hours == 48
        assert errors == ["GENETIC0: prediction.hours not configured, using {}."]


class TestGenetic0AlwaysValid:
    """The preparation never fails, never cancels and never writes the configuration (step 3b)."""

    def _configure(self, config_eos) -> None:
        config_eos.merge_settings_from_dict(
            {
                "general": {"timezone_override": "UTC"},
                "prediction": {"hours": 48},
                "optimization": {"genetic0": {"horizon_hours": 24}},
                "elecfee": {"provider": None},
                "devices": {
                    "max_batteries": 0,
                    "max_electric_vehicles": 0,
                    "max_inverters": 0,
                    "max_home_appliances": 0,
                },
            }
        )
        get_ems().set_start_datetime(to_datetime("2026-08-01T10:00:00+00:00", in_timezone="UTC"))

    @pytest.mark.asyncio
    async def test_missing_predictions_give_fallback_values(self, config_eos, caplog):
        self._configure(config_eos)
        providers_before = config_eos.model_dump(
            include={"weather", "load", "pvforecast", "elecprice", "elecfee", "general"}
        )
        prediction = Mock(
            update_data=AsyncMock(side_effect=RuntimeError("synthetic update failure")),
            key_to_array=AsyncMock(side_effect=KeyError("no provider")),
            key_to_raw_series=AsyncMock(side_effect=KeyError("no provider")),
        )
        with patch.object(genetic0prepare, "get_prediction", return_value=prediction):
            parameters = await genetic0prepare.prepare_request(
                get_ems().start_datetime, get_ems().observation_datetime
            )

        ems = parameters.ems
        assert ems.pv_forecast_wh == [0.0] * 48
        assert ems.electricity_price_per_wh == [genetic0prepare.FALLBACK_PRICE_PER_WH] * 48
        assert ems.feed_in_tariff_per_wh == [genetic0prepare.FALLBACK_FEED_IN_TARIFF_PER_WH] * 48
        assert ems.total_load == pytest.approx([3000.0 * 1000.0 / 8760.0] * 48)
        assert parameters.temperature_forecast is None
        # Nothing written back, no provider switched.
        assert config_eos.model_dump(
            include={"weather", "load", "pvforecast", "elecprice", "elecfee", "general"}
        ) == providers_before
        assert prediction.update_data.await_count == 1  # no retries
        assert "using the stored predictions" in caplog.text

    @pytest.mark.asyncio
    async def test_gaps_are_carried_forward(self, config_eos):
        self._configure(config_eos)
        price = np.full(48, np.nan)
        price[12:20] = np.arange(8, dtype=float) / 1000
        load = np.full(48, 500.0)
        load[30:] = np.nan

        async def read_array(*, key, **kwargs):
            return {
                "elecprice_marketprice_wh": price,
                "loadforecast_power_w": load,
            }.get(key, np.full(48, 1.0))

        tariff = pd.Series(
            0.00008, index=pd.date_range("2026-08-01T00:00:00Z", periods=72 * 4, freq="15min")
        )
        prediction = Mock(
            update_data=AsyncMock(),
            key_to_array=AsyncMock(side_effect=read_array),
            key_to_raw_series=AsyncMock(return_value=tariff),
        )
        with patch.object(genetic0prepare, "get_prediction", return_value=prediction):
            parameters = await genetic0prepare.prepare_request(
                get_ems().start_datetime, get_ems().observation_datetime
            )

        prices = parameters.ems.electricity_price_per_wh
        assert prices[:12] == [0.0] * 12  # leading gap: the first known price
        assert prices[12:20] == pytest.approx(list(np.arange(8) / 1000))
        assert prices[20:] == pytest.approx([0.007] * 28)  # carried forward
        assert parameters.ems.total_load[30:] == [500.0] * 18

    @pytest.mark.asyncio
    async def test_prepare_never_raises_without_any_input(self, config_eos, monkeypatch):
        self._configure(config_eos)
        prediction = Mock(
            update_data=AsyncMock(side_effect=RuntimeError("down")),
            key_to_array=AsyncMock(side_effect=RuntimeError("down")),
            key_to_raw_series=AsyncMock(side_effect=RuntimeError("down")),
            record_keys=["pvforecast_ac_power"],
        )
        monkeypatch.setattr(genetic0prepare, "get_prediction", lambda: prediction)
        start = get_ems().start_datetime

        parameters = await genetic0prepare.prepare(start, start, None)

        assert parameters.request.ems.pv_forecast_wh == [0.0] * 48
        assert parameters.presentation.prediction_series == {}


class TestGenetic0Fallbacks:
    """Each fallback of the preparation gives a valid value and logs an error (step 3b)."""

    @pytest.fixture
    def errors(self, monkeypatch) -> list[str]:
        logged: list[str] = []
        monkeypatch.setattr(
            genetic0prepare.logger, "error", lambda message, *args: logged.append(message)
        )
        return logged

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "measured, percentage, logged",
        [
            (0.42, 42, False),
            (math.nan, 0, True),
            (1.5, 0, True),
            (-0.1, 0, True),
            (KeyError("no reading"), 0, True),
        ],
        ids=["valid", "nan", "above-one", "negative", "missing"],
    )
    async def test_state_of_charge(self, monkeypatch, errors, measured, percentage, logged):
        nearest = AsyncMock(side_effect=[measured] if isinstance(measured, Exception) else None)
        if not isinstance(measured, Exception):
            nearest.return_value = measured
        monkeypatch.setattr(genetic0prepare, "_nearest_soc_factor", nearest)
        start = to_datetime("2026-01-14T10:00:00+00:00")
        assert await genetic0prepare._initial_soc_percentage("battery", "soc", start) == percentage
        assert bool(errors) == logged

    @pytest.mark.asyncio
    async def test_battery_without_cost_of_storage(self, monkeypatch, errors):
        battery_params = SimpleNamespace(initial_soc_percentage=None)
        battery = Mock(
            device_id="battery1",
            levelized_cost_of_storage_amt_kwh=None,
            measurement_key_soc_factor="soc",
            resolve_current_operating_parameters_or_defaults=AsyncMock(return_value=({}, None)),
        )
        monkeypatch.setattr(genetic0prepare, "battery_request", Mock(return_value=battery_params))
        config = Mock()
        config.devices.max_batteries = 1
        config.devices.batteries = {"battery1": battery}
        monkeypatch.setattr(genetic0prepare, "get_config", lambda: config)
        monkeypatch.setattr(genetic0prepare, "_initial_soc_percentage", AsyncMock(return_value=33))
        start = to_datetime("2026-01-14T10:00:00+00:00")

        params, lcos = await genetic0prepare._battery(start)

        assert (params, lcos) == (battery_params, 0.0)
        assert battery_params.initial_soc_percentage == 33
        assert battery.levelized_cost_of_storage_amt_kwh is None  # not written back
        assert errors == [
            "GENETIC0: Battery '{}' has no levelized cost of storage; using 0 per kWh."
        ]

    def test_unset_algorithm_settings(self, monkeypatch, errors):
        config = Mock()
        config.prediction.hours = 48
        config.devices.electric_vehicles = None
        config.devices.batteries = None
        config.optimization.genetic0.horizon_hours = 24
        config.optimization.genetic0.individuals = None
        config.optimization.genetic0.generations = None
        config.optimization.genetic0.seed = None
        config.optimization.genetic0.penalties = {}
        monkeypatch.setattr(genetic0prepare, "get_config", lambda: config)
        settings = genetic0prepare._run_settings(None)
        assert (settings.individuals, settings.generations) == (300, 400)
        assert len(errors) == 2
        assert config.optimization.genetic0.individuals is None  # not written back

    @pytest.mark.asyncio
    async def test_short_and_incomplete_forecasts(self, config_eos, errors):
        TestGenetic0AlwaysValid()._configure(config_eos)
        pv = np.full(48, 1000.0)
        pv[5:8] = np.nan

        async def read_array(*, key, **kwargs):
            if key == "pvforecast_ac_power":
                return pv
            if key == "loadforecast_power_w":
                return np.full(10, 400.0)  # does not cover the run
            return np.full(48, 0.0002)

        tariff = pd.Series(
            0.00008, index=pd.date_range("2026-08-01T00:00:00Z", periods=72 * 4, freq="15min")
        )
        prediction = Mock(
            update_data=AsyncMock(),
            key_to_array=AsyncMock(side_effect=read_array),
            key_to_raw_series=AsyncMock(return_value=tariff),
        )
        with patch.object(genetic0prepare, "get_prediction", return_value=prediction):
            parameters = await genetic0prepare.prepare_request(
                get_ems().start_datetime, get_ems().observation_datetime
            )

        assert parameters.ems.pv_forecast_wh[5:8] == [0.0, 0.0, 0.0]
        assert parameters.ems.pv_forecast_wh[8] == 1000.0
        assert parameters.ems.total_load == pytest.approx([3000.0 * 1000.0 / 8760.0] * 48)
        assert "GENETIC0: Prediction '{}' does not cover the run." in errors
        assert "GENETIC0: PV forecast incomplete; using 0 Wh for the missing hours." in errors

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failing_builds", [1, 2])
    async def test_invalid_parameters_fall_back(self, config_eos, monkeypatch, errors, failing_builds):
        TestGenetic0AlwaysValid()._configure(config_eos)
        prediction = Mock(
            update_data=AsyncMock(),
            key_to_array=AsyncMock(return_value=np.full(48, 0.0002)),
            key_to_raw_series=AsyncMock(
                return_value=pd.Series(
                    0.00008,
                    index=pd.date_range("2026-08-01T00:00:00Z", periods=72 * 4, freq="15min"),
                )
            ),
        )
        monkeypatch.setattr(genetic0prepare, "get_prediction", lambda: prediction)
        real = genetic0prepare.Genetic0EnergyManagementRequest
        calls = {"count": 0}

        def build(**kwargs):
            calls["count"] += 1
            if calls["count"] <= failing_builds:
                raise ValueError("synthetic invalid parameters")
            return real(**kwargs)

        monkeypatch.setattr(genetic0prepare, "Genetic0EnergyManagementRequest", build)
        parameters = await genetic0prepare.prepare_request(
            get_ems().start_datetime, get_ems().observation_datetime, start_solution=[1.0, 2.0]
        )

        assert parameters.pv_battery is None and parameters.start_solution is None
        assert parameters.temperature_forecast is None
        if failing_builds == 2:
            assert parameters.ems.pv_forecast_wh == [0.0] * 48
            assert parameters.ems.total_load == pytest.approx([3000.0 * 1000.0 / 8760.0] * 48)
        assert len([message for message in errors if "are invalid" in message]) == failing_builds


class TestGenetic0HomeApplianceShown:
    """The solution shows the home appliance the preparation put into the request (step 3b)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "dishwasher, max_home_appliances, automatic, enabled",
        [
            (object(), None, True, True),  # default max: the configured appliance is shown
            (None, None, True, False),  # nothing configured: nothing invented
            (object(), None, False, False),  # a client's request: configured count, as before
            (object(), 1, False, True),
        ],
    )
    async def test_decision(
        self, config_eos, monkeypatch, dishwasher, max_home_appliances, automatic, enabled
    ):
        config_eos.devices.max_home_appliances = max_home_appliances
        request = SimpleNamespace(dishwasher=dishwasher)
        monkeypatch.setattr(genetic0prepare, "prepare_request", AsyncMock(return_value=request))
        # The stand-in request has no forecasts to derive the simulation's dataclasses from.
        monkeypatch.setattr(
            genetic0prepare.Genetic0Parameters,
            "from_request",
            classmethod(lambda cls, **fields: SimpleNamespace(**fields)),
        )
        monkeypatch.setattr(
            genetic0prepare, "get_prediction", lambda: SimpleNamespace(record_keys=[])
        )
        start = to_datetime("2026-01-14T10:00:00", in_timezone="Europe/Berlin")
        if automatic:
            parameters = await genetic0prepare.prepare(start, start, None)
        else:
            parameters = await genetic0prepare.prepare_from_request(
                cast(Any, request), start, start, None
            )
        assert parameters.presentation.home_appliance_enabled is enabled

    @pytest.mark.asyncio
    async def test_conversion_gets_the_decision(self):
        from akkudoktoreos.optimization.genetic0.genetic0optimizer import (
            Genetic0Optimizer,
        )

        result = Mock(
            optimization_solution=AsyncMock(return_value="solution"),
            energy_management_plan=Mock(return_value="plan"),
        )
        parameters = SimpleNamespace(presentation=SimpleNamespace(home_appliance_enabled=True))
        converted = await Genetic0Optimizer().convert(cast(Any, parameters), result)
        assert converted == ("solution", "plan")
        result.optimization_solution.assert_awaited_once_with(home_appliance_enabled=True)
        result.energy_management_plan.assert_called_once_with(home_appliance_enabled=True)
