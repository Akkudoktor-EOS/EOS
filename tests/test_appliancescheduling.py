"""Cover appliance scheduling, load profiles, running cycles and deadlines."""

from typing import Any, Optional

import numpy as np
import pytest

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.devices.appliancescheduling import (
    ApplianceTimes,
    estimate_daily_load,
    plan_appliance,
    resample_power_to_slot_energy,
)
from akkudoktoreos.devices.devicesabc import ConsumerDeadlinePolicy
from akkudoktoreos.devices.settings.homeappliancesettings import HomeApplianceCommonSettings
from akkudoktoreos.utils.datetimeutil import to_datetime

SLOT0 = to_datetime("2026-09-16T10:00:00+02:00", in_timezone="Europe/Berlin")
OBSERVATION = to_datetime("2026-09-16T10:20:00+02:00", in_timezone="Europe/Berlin")

CASES: dict[str, dict[str, Any]] = {
    "flat_unconstrained": {"settings": {"consumption_wh": 1500, "duration_h": 2}},
    "night_window_across_midnight": {
        "settings": {
            "consumption_wh": 1500,
            "duration_h": 2,
            "cycle_time_windows": {
                "windows": [{"start_time": "22:00", "duration": "8 hours", "value": 0}]
            },
        }
    },
    "two_cycles_with_windows_and_gap_15min": {
        "interval": 900,
        "settings": {
            "load_profile_power_w": [600.0, 2200.0, 2200.0, 900.0],
            "load_profile_interval_seconds": 1800,
            "min_cycle_gap_h": 1,
            "cycle_time_windows": {
                "windows": [
                    {"start_time": "07:00", "duration": "5 hours", "value": 0},
                    {"start_time": "17:00", "duration": "6 hours", "value": 1},
                ]
            },
        },
    },
    "shared_window": {
        "settings": {
            "consumption_wh": 2000,
            "duration_h": 3,
            "time_windows": {"windows": [{"start_time": "12:00", "duration": "6 hours"}]},
        }
    },
    "earliest_start": {
        "settings": {
            "consumption_wh": 1500,
            "duration_h": 2,
            "earliest_start_datetime": "2026-09-16T14:30:00+02:00",
        }
    },
    "deadline_feasible_strict": {
        "settings": {
            "consumption_wh": 1500,
            "duration_h": 2,
            "deadline_datetime": "2026-09-16T20:00:00+02:00",
            "deadline_policy": "STRICT",
        }
    },
    "deadline_missed_best_effort": {
        "settings": {
            "consumption_wh": 1500,
            "duration_h": 4,
            "deadline_datetime": "2026-09-16T12:00:00+02:00",
            "deadline_policy": "BEST_EFFORT",
        }
    },
    "deadline_missed_strict": {
        "settings": {
            "consumption_wh": 1500,
            "duration_h": 4,
            "deadline_datetime": "2026-09-16T12:00:00+02:00",
            "deadline_policy": "STRICT",
        },
        "error": "no valid start",
    },
    "daily_two_cycles_one_done": {
        "horizon": 40,
        "settings": {
            "consumption_wh": 1200,
            "duration_h": 1,
            "num_cycles": 2,
            "schedule_mode": "DAILY",
            "min_cycle_gap_h": 2,
            "cycle_time_windows": {
                "windows": [
                    {"start_time": "06:00", "duration": "6 hours", "value": 0},
                    {"start_time": "16:00", "duration": "6 hours", "value": 1},
                ]
            },
        },
        "completed": 1,
    },
    "daily_last_day_incomplete": {
        "horizon": 48,
        "settings": {
            "consumption_wh": 1200,
            "duration_h": 1,
            "num_cycles": 2,
            "schedule_mode": "DAILY",
            "cycle_time_windows": {
                "windows": [
                    {"start_time": "06:00", "duration": "6 hours", "value": 0},
                    {"start_time": "16:00", "duration": "6 hours", "value": 1},
                ]
            },
        },
        "error": "no complete cycle schedule",
    },
    "daily_flat_estimate": {
        "horizon": 24,
        "prediction": 72,
        "settings": {"consumption_wh": 1000, "duration_h": 2, "schedule_mode": "DAILY"},
    },
    "once_run_in_progress": {
        "settings": {
            "consumption_wh": 2400,
            "duration_h": 2,
            "min_cycle_gap_h": 1,
            "num_cycles": 2,
        },
        "run_started": "2026-09-16T09:30:00+02:00",
    },
    "daily_run_in_progress": {
        "horizon": 48,
        "settings": {
            "consumption_wh": 2400,
            "duration_h": 2,
            "schedule_mode": "DAILY",
        },
        "run_started": "2026-09-16T09:30:00+02:00",
    },
}


def _configure(config_eos: ConfigEOS, interval: int, horizon: int, prediction: int) -> None:
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": prediction},
            "optimization": {
                "algorithm": "GENETIC",
                "genetic": {"interval_sec": interval, "horizon_hours": horizon},
            },
        }
    )


@pytest.mark.parametrize("name", list(CASES))
def test_schedule_respects_horizon_and_cycle_order(name: str, config_eos: ConfigEOS) -> None:
    case = CASES[name]
    interval = case.get("interval", 3600)
    horizon = case.get("horizon", 30)
    _configure(config_eos, interval, horizon, case.get("prediction", horizon + 24))
    slot_h = interval / 3600
    control_end = horizon * (3600 // interval)
    settings = HomeApplianceCommonSettings(device_id="appliance", **case["settings"])
    completed = case.get("completed", 0)
    started = to_datetime(case["run_started"]) if "run_started" in case else None

    new = ApplianceTimes(
        settings, slot_h, control_end, completed_cycles=completed, run_started=started
    )
    new.pin_run_in_progress(SLOT0, OBSERVATION)

    if case.get("error"):
        with pytest.raises(ValueError, match=case["error"]):
            plan_appliance(new, SLOT0)
        return

    planned = plan_appliance(new, SLOT0)
    assert planned.min_gap_slots == new.run_slots + int(np.ceil(new.min_cycle_gap_h / slot_h))
    firsts = [run.allowed_starts[0] for run in planned.runs]
    assert firsts == sorted(firsts)
    for run in planned.runs:
        assert all(0 <= slot <= control_end - new.run_slots for slot in run.allowed_starts)
    assert np.isfinite(new.run_energy_wh).all()
    assert np.isfinite(new.pinned_load).all()

    if name == "daily_flat_estimate":
        end = control_end + 24 * (3600 // interval)
        assert float(estimate_daily_load([planned], SLOT0, control_end, end).sum()) > 0.0


@pytest.mark.parametrize(
    ("power", "input_interval", "slot"),
    [
        ([2000.0], 7200.0, 900.0),
        ([200.0, 2000.0, 1800.0, 100.0], 600.0, 900.0),
        ([100.0, 900.0, 300.0], 1200.0, 900.0),
        ([500.0, 0.0, 500.0], 3600.0, 3600.0),
    ],
)
def test_resampling_conserves_energy(
    power: list[float], input_interval: float, slot: float
) -> None:
    got = resample_power_to_slot_energy(power, input_interval, slot)
    assert got.sum() == pytest.approx(sum(power) * input_interval / 3600.0)


def test_a_missed_deadline_places_the_runs_as_early_as_possible_together(
    config_eos: ConfigEOS,
) -> None:
    _configure(config_eos, 3600, 30, 54)
    settings = HomeApplianceCommonSettings(
        device_id="a",
        consumption_wh=1000,
        duration_h=2,
        num_cycles=2,
        min_cycle_gap_h=1,
        deadline_datetime=to_datetime("2026-09-16T11:00:00+02:00"),
        deadline_policy=ConsumerDeadlinePolicy.BEST_EFFORT,
    )
    times = ApplianceTimes(settings, 1.0, 30)
    times.pin_run_in_progress(SLOT0, SLOT0)
    planned = plan_appliance(times, SLOT0)
    starts = [run.allowed_starts for run in planned.runs]
    assert all(run.deadline_relaxed for run in planned.runs)
    assert starts == [(0,), (3,)]  # a run of 2 slots plus a gap of 1 slot


def test_a_stale_run_report_is_ignored(config_eos: ConfigEOS) -> None:
    _configure(config_eos, 3600, 24, 48)
    settings = HomeApplianceCommonSettings(device_id="a", consumption_wh=1000, duration_h=2)
    stale: Optional[Any] = to_datetime("2026-09-15T20:00:00+02:00")
    times = ApplianceTimes(settings, 1.0, 24, run_started=stale)
    times.pin_run_in_progress(SLOT0, OBSERVATION)
    assert not times.run_in_progress
    assert float(times.pinned_load.sum()) == 0.0
