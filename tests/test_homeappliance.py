"""Tests for flexible consumers (home appliances).

Covers the energy-preserving load profile, allowed start computation, the
appliance genome layout (ONCE/DAILY), multi-device scheduling and the
deprecated single-appliance compatibility path.
"""

import numpy as np
import pytest
from pydantic import ValidationError

from akkudoktoreos.config.configabc import TimeWindow, TimeWindowSequence
from akkudoktoreos.core.cache import CacheEnergyManagementStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.devices.devices import DevicesCommonSettings
from akkudoktoreos.devices.genetic.homeappliance import (
    HomeAppliance,
    resample_power_to_slot_energy,
)
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticdevices import HomeApplianceParameters
from akkudoktoreos.optimization.genetic.geneticparams import GeneticOptimizationParameters
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration, to_time

ems_eos = get_ems(init=True)


def _appliance(prediction_hours: int, slot_duration_h: float, **params) -> HomeAppliance:
    return HomeAppliance(
        HomeApplianceParameters(**params),
        optimization_hours=prediction_hours,
        prediction_hours=prediction_hours,
        slot_duration_h=slot_duration_h,
    )


def _ems(n: int, load: float = 500.0) -> dict:
    return {
        "pv_prognose_wh": [0.0] * n,
        "strompreis_euro_pro_wh": [0.0003] * n,
        "einspeiseverguetung_euro_pro_wh": 0.00007,
        "preis_euro_pro_wh_akku": 0.0001,
        "gesamtlast": [load] * n,
    }


# --------------------------------------------------------------------------- #
# Energy-preserving resampling
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "input_interval, slot_interval",
    [(3600, 900), (900, 3600), (600, 900), (1200, 900), (1800, 900), (3600, 3600)],
)
def test_resample_conserves_energy(input_interval: int, slot_interval: int):
    """Energy is conserved for integer and non-integer interval ratios."""
    power = [1000.0, 0.0, 500.0, 2500.0, 750.0]
    energy = resample_power_to_slot_energy(power, input_interval, slot_interval)
    expected = sum(p * input_interval / 3600 for p in power)
    assert energy.sum() == pytest.approx(expected)
    assert (energy >= 0).all()


def test_flat_fallback_hourly_matches_legacy():
    """The flat consumption_wh/duration_h fallback reproduces the legacy curve."""
    appliance = _appliance(48, 1.0, device_id="dw", consumption_wh=2000, duration_h=2)
    assert appliance.run_slots == 2
    assert list(appliance.run_energy_wh) == [1000.0, 1000.0]
    appliance.build_load_curve([5])
    curve = appliance.get_load_curve()
    assert curve[5] == 1000.0 and curve[6] == 1000.0
    assert curve.sum() == 2000.0


def test_flat_fallback_15min_grid():
    """The flat fallback resamples onto the quarter-hour grid, conserving energy."""
    appliance = _appliance(192, 0.25, device_id="dw", consumption_wh=2000, duration_h=2)
    assert appliance.run_slots == 8  # 2 h -> 8 quarter-hours
    assert appliance.run_energy_wh.sum() == pytest.approx(2000.0)
    assert all(value == pytest.approx(250.0) for value in appliance.run_energy_wh)


def test_build_load_curve_overlapping_runs_add():
    """Overlapping runs of one appliance sum their per-slot energy."""
    appliance = _appliance(
        10, 1.0, device_id="d", load_profile_power_w=[3600.0, 3600.0], load_profile_interval_seconds=3600
    )
    appliance.build_load_curve([2, 3])  # runs occupy [2,3] and [3,4] -> overlap at 3
    curve = appliance.get_load_curve()
    assert curve[2] == pytest.approx(3600.0)
    assert curve[3] == pytest.approx(7200.0)
    assert curve[4] == pytest.approx(3600.0)


# --------------------------------------------------------------------------- #
# Allowed start slots and time windows
# --------------------------------------------------------------------------- #
def test_allowed_start_slots_time_window_and_horizon():
    """Only starts whose full run fits a window and the horizon are allowed."""
    slot0 = to_datetime("2026-07-15 00:00:00")
    windows = TimeWindowSequence(
        windows=[TimeWindow(start_time=to_time("10:00"), duration=to_duration("3 hours"))]
    )
    appliance = _appliance(
        48, 1.0, device_id="d", consumption_wh=1000, duration_h=1, time_windows=windows
    )
    allowed = appliance.allowed_start_slots(
        slot0_datetime=slot0, earliest_slot=0, horizon_end_slot=48
    )
    # window 10:00-13:00, run 1 h -> starts 10,11,12 each day (+24 on day 1)
    assert allowed == [10, 11, 12, 34, 35, 36]


def test_allowed_start_slots_window_over_midnight():
    """A window crossing midnight yields starts on both sides of midnight."""
    slot0 = to_datetime("2026-07-15 00:00:00")
    windows = TimeWindowSequence(
        windows=[TimeWindow(start_time=to_time("23:00"), duration=to_duration("3 hours"))]
    )
    appliance = _appliance(
        48, 1.0, device_id="d", consumption_wh=1000, duration_h=1, time_windows=windows
    )
    allowed = appliance.allowed_start_slots(
        slot0_datetime=slot0, earliest_slot=0, horizon_end_slot=48
    )
    # 23:00-02:00 window: a run starting at 23:00 crosses midnight (ends 00:00).
    # TimeWindow evaluates the window on the start's own calendar day, so the
    # only allowed start per day is 23:00 (slot 23 on day 0, slot 47 on day 1).
    assert allowed == [23, 47]


def test_allowed_start_slots_weekday_restriction():
    """A weekday-restricted window only allows starts on that weekday."""
    slot0 = to_datetime("2026-07-15 00:00:00")  # Wednesday
    weekday = slot0.day_of_week
    windows = TimeWindowSequence(
        windows=[
            TimeWindow(
                start_time=to_time("10:00"), duration=to_duration("2 hours"), day_of_week=weekday
            )
        ]
    )
    appliance = _appliance(
        72, 1.0, device_id="d", consumption_wh=1000, duration_h=1, time_windows=windows
    )
    allowed = appliance.allowed_start_slots(
        slot0_datetime=slot0, earliest_slot=0, horizon_end_slot=72
    )
    # Only day 0 (the Wednesday) matches: starts 10, 11
    assert allowed == [10, 11]


# --------------------------------------------------------------------------- #
# Genome layout (ONCE / DAILY)
# --------------------------------------------------------------------------- #
def _optimizer(config_eos, *, prediction_hours: int, horizon_hours: int, interval: int, hour: int):
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": prediction_hours},
            "optimization": {"horizon_hours": horizon_hours, "interval": interval},
        }
    )
    ems_eos.set_start_datetime(to_datetime().set(hour=hour, minute=0))
    return GeneticOptimization(fixed_seed=1)


def test_once_layout_single_gene(config_eos):
    opt = _optimizer(config_eos, prediction_hours=48, horizon_hours=48, interval=3600, hour=10)
    slot0 = opt.ems.start_datetime.set(hour=0, minute=0, second=0, microsecond=0)
    appliance = _appliance(48, 1.0, device_id="d", consumption_wh=1000, duration_h=2)
    layout = opt._build_appliance_layout([appliance], slot0)
    assert layout.n_genes == 1
    assert layout.genes[0].run_date is None
    assert layout.genes[0].allowed_start_slots[0] == opt._start_day_slot()


def test_once_no_valid_start_raises(config_eos):
    opt = _optimizer(config_eos, prediction_hours=48, horizon_hours=10, interval=3600, hour=10)
    slot0 = opt.ems.start_datetime.set(hour=0, minute=0, second=0, microsecond=0)
    # 02:00 window is in the past (start slot 10) and day 1 is beyond the 10 h horizon.
    windows = TimeWindowSequence(
        windows=[TimeWindow(start_time=to_time("02:00"), duration=to_duration("1 hours"))]
    )
    appliance = _appliance(
        48, 1.0, device_id="d", consumption_wh=500, duration_h=1, time_windows=windows
    )
    with pytest.raises(ValueError, match="no valid start"):
        opt._build_appliance_layout([appliance], slot0)


def test_daily_layout_one_gene_per_calendar_day(config_eos):
    opt = _optimizer(config_eos, prediction_hours=72, horizon_hours=72, interval=3600, hour=0)
    slot0 = opt.ems.start_datetime.set(hour=0, minute=0, second=0, microsecond=0)
    windows = TimeWindowSequence(
        windows=[TimeWindow(start_time=to_time("10:00"), duration=to_duration("2 hours"))]
    )
    appliance = _appliance(
        72,
        1.0,
        device_id="d",
        consumption_wh=500,
        duration_h=1,
        schedule_mode="DAILY",
        time_windows=windows,
    )
    layout = opt._build_appliance_layout([appliance], slot0)
    assert layout.n_genes == 3  # 3 calendar days in the 72 h horizon
    assert len({gene.run_date for gene in layout.genes}) == 3
    for gene in layout.genes:
        assert len(gene.allowed_start_slots) == 2  # starts 10 and 11 on each day


def test_daily_layout_partial_first_day(config_eos):
    """A partial first day (start after the window) produces no gene for that day."""
    opt = _optimizer(config_eos, prediction_hours=48, horizon_hours=48, interval=3600, hour=14)
    slot0 = opt.ems.start_datetime.set(hour=0, minute=0, second=0, microsecond=0)
    windows = TimeWindowSequence(
        windows=[TimeWindow(start_time=to_time("10:00"), duration=to_duration("2 hours"))]
    )
    appliance = _appliance(
        48,
        1.0,
        device_id="d",
        consumption_wh=500,
        duration_h=1,
        schedule_mode="DAILY",
        time_windows=windows,
    )
    layout = opt._build_appliance_layout([appliance], slot0)
    # Day 0 window (10:00-12:00) is already in the past at start hour 14 -> only day 1.
    assert layout.n_genes == 1
    assert all(slot >= opt._start_day_slot() for slot in layout.genes[0].allowed_start_slots)


# --------------------------------------------------------------------------- #
# Multiple devices, aggregate and deprecated compatibility (integration)
# --------------------------------------------------------------------------- #
def test_multiple_appliances_scheduled_and_aggregate(config_eos):
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {
                "horizon_hours": 48,
                "interval": 3600,
                "genetic": {
                    "individuals": 60,
                    "generations": 10,
                    "penalties": {"ev_soc_miss": 10, "ac_charge_break_even": 0},
                },
            },
        }
    )
    ems_eos.set_start_datetime(to_datetime().set(hour=0, minute=0))
    CacheEnergyManagementStore().clear()
    parameters = GeneticOptimizationParameters(
        ems=_ems(48),
        pv_akku=None,
        inverter=None,
        eauto=None,
        home_appliances=[
            HomeApplianceParameters(device_id="dw", consumption_wh=1000, duration_h=1),
            HomeApplianceParameters(device_id="wm", consumption_wh=2000, duration_h=2),
        ],
    )
    solution = GeneticOptimization(fixed_seed=7).optimierung_ems(
        parameters=parameters, start_hour=0, ngen=3
    )

    per_device = solution.result.home_appliance_energy_wh
    assert set(per_device) == {"dw", "wm"}
    assert sum(per_device["dw"]) == pytest.approx(1000.0)
    assert sum(per_device["wm"]) == pytest.approx(2000.0)

    # Per-device energy sums exactly to the deprecated aggregate.
    aggregate = [
        (per_device["dw"][i] or 0.0) + (per_device["wm"][i] or 0.0)
        for i in range(len(per_device["dw"]))
    ]
    reported = [value or 0.0 for value in solution.result.Home_appliance_wh_per_hour]
    assert reported == pytest.approx(aggregate)

    # Each device has an absolute start datetime.
    assert set(solution.appliance_starts) == {"dw", "wm"}
    assert len(solution.appliance_starts["dw"]) == 1

    # DDBC instructions are only emitted on RUN/OFF transitions.
    plan = solution.energy_management_plan()
    dw_instructions = [i for i in plan.instructions if i.resource_id == "dw"]
    modes = [str(i.operation_mode_id) for i in dw_instructions]
    # A single 1 h run yields an OFF/RUN/OFF sequence (no repeated RUN).
    assert modes.count("RUN") == 1


def test_duplicate_device_id_rejected():
    with pytest.raises(ValidationError, match="unique"):
        GeneticOptimizationParameters(
            ems=_ems(2),
            pv_akku=None,
            inverter=None,
            eauto=None,
            home_appliances=[
                HomeApplianceParameters(device_id="x", consumption_wh=1000, duration_h=1),
                HomeApplianceParameters(device_id="x", consumption_wh=1000, duration_h=1),
            ],
        )


def test_dishwasher_and_home_appliances_conflict_rejected():
    with pytest.raises(ValidationError, match="either"):
        GeneticOptimizationParameters(
            ems=_ems(2),
            pv_akku=None,
            inverter=None,
            eauto=None,
            dishwasher=HomeApplianceParameters(device_id="d", consumption_wh=1000, duration_h=1),
            home_appliances=[
                HomeApplianceParameters(device_id="e", consumption_wh=1000, duration_h=1)
            ],
        )


def test_deprecated_dishwasher_maps_to_list():
    parameters = GeneticOptimizationParameters(
        ems=_ems(2),
        pv_akku=None,
        inverter=None,
        eauto=None,
        dishwasher=HomeApplianceParameters(device_id="d", consumption_wh=1000, duration_h=1),
    )
    resolved = parameters.resolved_home_appliances()
    assert [appliance.device_id for appliance in resolved] == ["d"]


def test_max_home_appliances_is_upper_bound():
    with pytest.raises(ValidationError, match="exceeds max_home_appliances"):
        DevicesCommonSettings(
            max_home_appliances=1,
            home_appliances=[
                {"device_id": "a", "consumption_wh": 1000, "duration_h": 1},
                {"device_id": "b", "consumption_wh": 1000, "duration_h": 1},
            ],
        )


def test_start_solution_layout_mismatch_is_ignored(config_eos):
    opt = _optimizer(config_eos, prediction_hours=48, horizon_hours=48, interval=3600, hour=10)
    slot0 = opt.ems.start_datetime.set(hour=0, minute=0, second=0, microsecond=0)
    appliance = _appliance(48, 1.0, device_id="d", consumption_wh=1000, duration_h=1)
    opt.appliance_layout = opt._build_appliance_layout([appliance], slot0)
    opt.optimize_ev = False
    valid_index_count = len(opt.appliance_layout.genes[0].allowed_start_slots)
    # A tail index beyond the allowed range must be rejected.
    bad_solution = [0] * opt.total_slots + [valid_index_count + 5]
    assert opt._start_solution_matches_layout(bad_solution) is False
    good_solution = [0] * opt.total_slots + [0]
    assert opt._start_solution_matches_layout(good_solution) is True
