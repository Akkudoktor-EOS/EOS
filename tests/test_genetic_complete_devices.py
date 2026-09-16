"""Profiles, cycle windows and deadlines on the GENETIC slot grid."""

from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from akkudoktoreos.devices.devicesabc import (
    ConsumerDeadlinePolicy,
    ConsumerScheduleMode,
)
from akkudoktoreos.devices.genetic.battery import Battery, ElectricVehicleParameters
from akkudoktoreos.devices.genetic.homeappliance import (
    HomeAppliance,
    HomeApplianceParameters,
    resample_power_to_slot_energy,
)
from akkudoktoreos.devices.settings.batterysettings import BatteriesCommonSettings
from akkudoktoreos.devices.settings.homeappliancesettings import (
    HomeApplianceCommonSettings,
)
from akkudoktoreos.utils.datetimeutil import to_datetime


def appliance(*, slots: int = 192, slot_h: float = 0.25, **kwargs: Any) -> HomeAppliance:
    return HomeAppliance(
        HomeApplianceParameters.model_validate(
            {
                "device_id": "washer",
                "load_profile_power_w": [1200.0, 600.0, 300.0],
                "load_profile_interval_seconds": 600,
                **kwargs,
            }
        ),
        optimization_hours=48,
        prediction_hours=slots,
        slot_duration_h=slot_h,
    )


def test_noninteger_resampling_conserves_real_energy() -> None:
    # 10-minute 1.2kW/0.6kW/0.3kW phases: 350Wh, delivered in two 15min slots.
    device = appliance()
    np.testing.assert_allclose(device.run_energy_wh, [250.0, 100.0])
    device.build_load_curve([3, 12])
    assert device.get_load_curve().sum() == pytest.approx(700)
    np.testing.assert_allclose(device.get_load_curve()[3:5], [250.0, 100.0])
    assert device.run_slots == 2


@pytest.mark.parametrize("input_s,slot_s", [(600, 900), (1200, 900), (900, 3600), (3600, 900)])
def test_profile_energy_independent_of_slot_grid(input_s: int, slot_s: int) -> None:
    result = resample_power_to_slot_energy([400.0, 1600.0, 200.0], input_s, slot_s)
    assert result.sum() == pytest.approx(2200 * input_s / 3600)


@pytest.mark.parametrize("profile", [[], [-1], [float("nan")], [float("inf")]])
def test_invalid_profiles_rejected_in_parameters_and_settings(profile: list[float]) -> None:
    for model in (HomeApplianceParameters, HomeApplianceCommonSettings):
        with pytest.raises(ValidationError):
            model.model_validate({"device_id": "bad", "load_profile_power_w": profile})


@pytest.mark.parametrize(
    "fields", [{"duration_h": 2}, {"consumption_wh": 500}, {"duration_h": 2, "consumption_wh": 500}]
)
def test_profile_cannot_silently_override_explicit_flat_definition(fields: dict[str, int]) -> None:
    for model in (HomeApplianceParameters, HomeApplianceCommonSettings):
        with pytest.raises(ValidationError, match="Conflicting"):
            model.model_validate({"device_id": "bad", "load_profile_power_w": [100.0], **fields})


def test_settings_profile_roundtrip_and_legacy_defaults() -> None:
    defaults = HomeApplianceCommonSettings(device_id="legacy")
    assert defaults.to_genetic_param().consumption_wh == 3000
    assert defaults.to_genetic0_param().duration_h == 3
    settings = HomeApplianceCommonSettings.model_validate(
        {
            "device_id": "washer",
            "load_profile_power_w": [1200.0, 600.0],
            "load_profile_interval_seconds": 600,
            "schedule_mode": "DAILY",
            "num_cycles": 2,
            "min_cycle_gap_h": 1,
            "time_windows": {"windows": [{"start_time": "07:00", "duration": "12 hours"}]},
        }
    )
    restored = HomeApplianceCommonSettings.model_validate_json(settings.model_dump_json())
    params = restored.to_genetic_param()
    assert params.load_profile_power_w == [1200.0, 600.0]
    assert params.consumption_wh is None and params.duration_h is None
    assert params.num_cycles == 2 and params.min_cycle_gap_h == 1
    assert params.schedule_mode == ConsumerScheduleMode.DAILY
    assert params.shared_time_windows is not None
    with pytest.raises(ValueError, match="GENETIC"):
        restored.to_genetic0_param()


def test_cycle_and_shared_windows_intersect_after_completed_cycle() -> None:
    settings = HomeApplianceCommonSettings.model_validate(
        {
            "device_id": "washer",
            "load_profile_power_w": [1200.0],
            "load_profile_interval_seconds": 1800,
            "schedule_mode": "DAILY",
            "cycle_time_windows": {
                "windows": [
                    {"start_time": "06:00", "duration": "2 hours", "value": 0},
                    {"start_time": "18:00", "duration": "2 hours", "value": 1},
                ]
            },
            "time_windows": {"windows": [{"start_time": "18:30", "duration": "1 hour"}]},
        }
    )
    device = HomeAppliance(settings.to_genetic_param(), 48, 192, 0.25)
    device.set_completed_cycles(1)
    zero = to_datetime("2026-09-16T00:00:00+02:00")
    assert device.remaining_cycle_indices == [1]
    assert device.allowed_start_slots(
        slot0_datetime=zero, earliest_slot=0, horizon_end_slot=96, cycle_index=1
    ) == [74, 75, 76]
    assert (
        device.allowed_start_slots(
            slot0_datetime=zero, earliest_slot=0, horizon_end_slot=96, cycle_index=0
        )
        == []
    )
    # Next-day DAILY lookup remains possible using absolute configured cycle IDs.
    assert device.allowed_start_slots(
        slot0_datetime=zero, earliest_slot=96, horizon_end_slot=192, cycle_index=1
    ) == [170, 171, 172]


def test_legacy_multiple_cycles_use_slots_and_physical_idle_gap() -> None:
    device = appliance(slots=96, num_cycles=2, min_cycle_gap_h=1)
    assert device.set_starting_times([4, 4]) == [4, 10]
    assert device.get_load_curve().sum() == pytest.approx(700)


def test_touching_cycle_windows_preserve_union() -> None:
    device = appliance(
        time_windows={
            "windows": [
                {"start_time": "08:00", "duration": "15 minutes", "value": 0},
                {"start_time": "08:15", "duration": "15 minutes", "value": 0},
            ]
        }
    )
    assert device.allowed_start_slots(
        slot0_datetime=to_datetime("2026-09-16T00:00:00+02:00"),
        earliest_slot=0,
        horizon_end_slot=96,
        cycle_index=0,
    ) == [32]


def test_absolute_bounds_round_inward_on_quarter_hour_grid() -> None:
    device = appliance(
        earliest_start_datetime="2026-09-16T08:01:00+02:00",
        deadline_datetime="2026-09-16T09:01:00+02:00",
        deadline_policy="STRICT",
    )
    zero = to_datetime("2026-09-16T00:00:00+02:00")
    assert device.allowed_start_slots(
        slot0_datetime=zero, earliest_slot=0, horizon_end_slot=96
    ) == [33, 34]
    assert not device.deadline_missed([34], zero)
    assert device.deadline_missed([35], zero)


def test_best_effort_relaxes_only_deadline_not_window_or_earliest() -> None:
    device = appliance(
        deadline_datetime="2026-09-16T08:00:00+02:00",
        shared_time_windows={"windows": [{"start_time": "09:00", "duration": "1 hour"}]},
    )
    zero = to_datetime("2026-09-16T00:00:00+02:00")
    assert device.allowed_start_slots(
        slot0_datetime=zero, earliest_slot=0, horizon_end_slot=96
    ) == [36]
    assert device.deadline_relaxed and device.deadline_missed([36], zero)
    device.deadline_policy = ConsumerDeadlinePolicy.STRICT
    assert (
        device.allowed_start_slots(slot0_datetime=zero, earliest_slot=0, horizon_end_slot=96) == []
    )
    assert not device.deadline_relaxed


def test_overnight_window_uses_opening_date_and_weekday() -> None:
    device = appliance(
        shared_time_windows={
            "windows": [
                {
                    "start_time": "23:00",
                    "duration": "3 hours",
                    "date": "2026-09-15",
                    "day_of_week": 1,
                }
            ]
        }
    )
    zero = to_datetime("2026-09-16T00:00:00+02:00")
    assert device.allowed_start_slots(
        slot0_datetime=zero, earliest_slot=0, horizon_end_slot=96
    ) == list(range(7))


@pytest.mark.parametrize("date,expected", [("2026-03-29", 8), ("2026-10-25", 16)])
def test_dst_elapsed_slots_and_local_window(date: str, expected: int) -> None:
    zero = to_datetime(f"{date}T00:00:00", in_timezone="Europe/Berlin")
    device = appliance(
        shared_time_windows={"windows": [{"start_time": "03:00", "duration": "30 minutes"}]}
    )
    assert device.allowed_start_slots(
        slot0_datetime=zero, earliest_slot=0, horizon_end_slot=100
    ) == [expected]
    assert device.run_end_datetime(expected, zero).hour == 3


@pytest.mark.parametrize("starts", [[-1], [191], [192]])
def test_complete_curve_does_not_silently_truncate_runs(starts: list[int]) -> None:
    with pytest.raises(ValueError, match="complete"):
        appliance().build_load_curve(starts)


def test_ev_deadlines_roundtrip_without_changing_slot_physics() -> None:
    settings = BatteriesCommonSettings.model_validate(
        {
            "device_id": "car",
            "capacity_wh": 10000,
            "charging_efficiency": 0.8,
            "max_charge_power_w": 4000,
            "min_soc_deadline_datetime": "2026-09-16T07:30:00+02:00",
            "min_soc_max_duration_h": 2.5,
        }
    )
    params = settings.to_genetic_ev_bat_param()
    assert params.min_soc_max_duration_h == 2.5
    assert params.min_soc_deadline_datetime is not None
    assert params.min_soc_deadline_datetime.hour == 7
    battery = Battery(params, prediction_hours=16, slot_duration_h=0.25)
    battery.charge_array[0] = 1
    charged, losses = battery.charge_energy(2000, hour=0)
    assert charged == pytest.approx(800)
    assert losses == pytest.approx(200)
    assert "min_soc_deadline_datetime" not in settings.to_genetic0_ev_bat_param().model_dump()


@pytest.mark.parametrize("duration", [0.0, -1.0, float("inf"), float("nan")])
def test_ev_invalid_departure_durations_rejected(duration: float) -> None:
    for model in (ElectricVehicleParameters, BatteriesCommonSettings):
        with pytest.raises(ValidationError):
            model.model_validate(
                {"device_id": "car", "capacity_wh": 10000, "min_soc_max_duration_h": duration}
            )


def test_completed_cycles_parameter_initializes_remaining_global_ids() -> None:
    device = appliance(num_cycles=3, completed_cycles=2)
    assert device.completed_cycles == 2
    assert device.remaining_cycle_indices == [2]
    assert device.num_remaining_cycles == 1
    done = appliance(num_cycles=3, completed_cycles=3)
    assert done.remaining_cycle_indices == []
    with pytest.raises(ValidationError, match="completed_cycles"):
        appliance(num_cycles=2, completed_cycles=3)


def test_best_effort_multiple_cycles_retain_room_for_joint_gap_repair() -> None:
    device = appliance(
        num_cycles=2,
        min_cycle_gap_h=1,
        deadline_datetime="2026-09-16T08:00:00+02:00",
        shared_time_windows={"windows": [{"start_time": "09:00", "duration": "3 hours"}]},
    )
    zero = to_datetime("2026-09-16T00:00:00+02:00")
    assert device.allowed_start_slots(
        slot0_datetime=zero, earliest_slot=0, horizon_end_slot=96, cycle_index=0
    ) == list(range(36, 47))
    assert device.deadline_relaxed
    device.build_load_curve([36, 42])
    assert device.get_load_curve().sum() == pytest.approx(700)
