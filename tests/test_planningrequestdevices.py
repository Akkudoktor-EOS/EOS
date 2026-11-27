"""The planning request takes over every configured battery, vehicle, inverter and appliance.

Several of each are resolved at once; the inverter belongs to its battery by ``battery_id``;
the grid connection limits, the search levels per battery, the vehicle targets and the plug-in
times reach the task. The task runs through the search without a violation. Incomplete or
inconsistent configurations and forecasts are errors that name the cause.
"""

from typing import Any

import numpy as np
import pytest
from test_requestfixtures import (
    BATTERY,
    DISHWASHER,
    VEHICLE,
    ems_eos,
    fake_measurement_fixture,  # noqa: F401  (registers the fixture)
    forecast_series,
    resolve_task,
)

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.optimization.kernel import Plan, simulate
from akkudoktoreos.optimization.objective import Objective
from akkudoktoreos.optimization.planner import run_planning
from akkudoktoreos.optimization.request import PlanningRequest, RuntimeForecasts
from akkudoktoreos.optimization.search import DeviceSearch, check_plan
from akkudoktoreos.utils.datetimeutil import to_datetime

MANY_DEVICES: dict[str, Any] = {
    "batteries": {
        "bat1": {
            **BATTERY,
            "discharge_rates": [0.5, 1.0],
            "adapter_supports_discharge_limit": True,
            "grid_export_rates": [],
            "pv_charge_switchable": True,
            "levelized_cost_of_storage_amt_kwh": 0.02,
        },
        "bat2": {
            "profile_adjustment": {"nominal_capacity_wh": 5000, "max_charge_power_w": 3000},
            "charge_rates": [0.0, 0.5, 1.0],
            "discharge_rates": [],
            "levelized_cost_of_storage_amt_kwh": 0.05,
        },
    },
    "electric_vehicles": {
        "ev1": {**VEHICLE, "min_soc_max_duration_h": 6.0},
        "ev2": {
            "profile_adjustment": {"nominal_capacity_wh": 40000, "max_charge_power_w": 11000},
            "charge_rates": [0.0, 0.5, 1.0],
            "min_soc_percentage": 60,
        },
    },
    "inverters": {
        "inv1": {"battery_id": "bat1", "profile_adjustment": {"nominal_power_w": 8000}},
        "inv2": {"battery_id": "bat2", "profile_adjustment": {"nominal_power_w": 5000}},
        "inv3": {"profile_adjustment": {"nominal_power_w": 3000}},
    },
    "home_appliances": {
        "dishwasher": DISHWASHER,
        "washer": {"consumption_wh": 900, "duration_h": 1},
    },
    "grid_connection": {
        "import_limit_w": 9000,
        "export_limit_w": 6000,
        "import_limit_penalty_eur_per_kwh": 20.0,
    },
}
SOC = {"bat1": 40, "bat2": 70, "ev1": 20, "ev2": 30}


@pytest.mark.asyncio
async def test_all_batteries_vehicles_and_inverters_are_taken_over(
    config_eos: ConfigEOS, fake_measurement: Any
) -> None:
    task = await resolve_task(config_eos, MANY_DEVICES, SOC)
    problem = task.problem
    assert task.battery_ids == ("bat1", "bat2")
    assert task.vehicle_ids == ("ev1", "ev2")
    assert task.inverter_ids == ("inv1", "inv2", "inv3")
    assert task.appliance_ids == ("dishwasher", "washer")
    assert [b.capacity_wh for b in problem.batteries] == [8000.0, 5000.0]
    assert [b.max_charge_power_w for b in problem.batteries] == [5000.0, 3000.0]
    assert [b.initial_soc_pct for b in problem.batteries] == [40.0, 70.0]
    assert [b.lcos_per_kwh for b in problem.batteries] == [0.02, 0.05]
    assert [v.capacity_wh for v in problem.vehicles] == [60000.0, 40000.0]
    assert [v.initial_soc_pct for v in problem.vehicles] == [20.0, 30.0]
    assert all(v.is_vehicle and v.min_soc_pct == 0.0 for v in problem.vehicles)
    assert [i.battery_index for i in problem.inverters] == [0, 1, None]
    assert [i.max_power_w for i in problem.inverters] == [8000.0, 5000.0, 3000.0]
    assert len(problem.appliances) == 2
    # Targets and deadlines: 6 h from now for the first vehicle, the horizon end for the second.
    targets = task.objective.vehicle_targets
    assert [(t.target_soc_pct, t.deadline_slot) for t in targets] == [(80.0, 6), (60.0, None)]
    # The grid connection and the price of exceeding the import limit.
    assert problem.grid.import_limit_w == 9000.0 and problem.grid.export_limit_w == 6000.0
    assert task.objective.import_limit_eur_per_kwh == 20.0
    # Discharge levels and PV switching per battery.
    assert task.search.battery_discharge_levels == ((0.5, 1.0), (1.0,))
    assert task.search.battery_pv_switchable == (True, False)
    assert task.search.discharge_levels_of(0) == (0.5, 1.0)
    assert task.search.export_levels_of(0) == ()
    assert task.search.export_levels_of(1) == (0.25, 0.5, 0.75, 1.0)
    assert task.search.discharge_levels_of(1) == (1.0,)
    assert task.search.pv_switchable_of(0) and not task.search.pv_switchable_of(1)


@pytest.mark.asyncio
async def test_the_task_runs_through_the_search_without_a_violation(
    config_eos: ConfigEOS, fake_measurement: Any
) -> None:
    task = await resolve_task(config_eos, MANY_DEVICES, SOC)
    problem = task.problem
    search = DeviceSearch(problem, Objective(problem, task.objective), task.space, task.search)
    result = search.solve()
    assert result.violations == []
    assert check_plan(problem, result.plan, task.space, task.search) == []
    assert result.objective.total_eur <= result.start_objective.total_eur
    assert np.isfinite(simulate(problem, Plan.idle(problem)).total_balance_eur)
    # Every appliance cycle that has to run is placed in one of its allowed slots.
    for index, schedule in enumerate(task.space.appliances):
        starts = [int(s) for s in result.plan.appliance_starts[index] if s >= 0]
        assert len(starts) == len(schedule.allowed_starts)
        for start, allowed in zip(starts, schedule.allowed_starts):
            assert start in allowed


@pytest.mark.asyncio
async def test_vehicle_plug_in_times_are_taken_from_the_request(
    config_eos: ConfigEOS, fake_measurement: Any
) -> None:
    devices = {k: MANY_DEVICES[k] for k in ("batteries", "electric_vehicles", "inverters")}
    window = [slot < 10 for slot in range(24)]
    series = forecast_series("optimize_input_1.json", 3600)
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "devices": devices,
            "optimization": {"genetic": {"horizon_hours": 24, "tail_horizon_hours": 0}},
        }
    )
    ems_eos.set_start_datetime(
        to_datetime("2026-09-16T10:00:00+02:00", in_timezone="Europe/Berlin"), 3600
    )
    task = await PlanningRequest(
        soc={"bat1": 50, "bat2": 50},
        forecasts=RuntimeForecasts(**series),
        vehicle_available={"ev2": window},
    ).resolve()
    assert task.problem.vehicles[0].available is None
    assert task.problem.vehicles[1].available == tuple(window)
    with pytest.raises(ValueError, match="need 24 values"):
        await PlanningRequest(
            soc={"bat1": 50, "bat2": 50},
            forecasts=RuntimeForecasts(**series),
            vehicle_available={"ev2": [True] * 5},
        ).resolve()


def configuration_error(name: str) -> Any:
    """Return ``(devices, soc, expected message)`` of a configuration that must be rejected."""
    devices = {k: dict(v) for k, v in MANY_DEVICES.items()}
    if name == "unknown_battery":
        devices["inverters"] = {
            **devices["inverters"],
            "inv1": {"battery_id": "nobattery", "profile_adjustment": {"nominal_power_w": 8000}},
        }
        return devices, SOC, "refers to battery 'nobattery'"
    if name == "battery_without_inverter":
        devices["inverters"] = {"inv1": devices["inverters"]["inv1"]}
        return devices, SOC, "Battery bat2 has no inverter"
    if name == "two_inverters_one_battery":
        devices["inverters"] = {
            **devices["inverters"],
            "inv4": {"battery_id": "bat1", "profile_adjustment": {"nominal_power_w": 2000}},
        }
        return devices, SOC, "more than one inverter"
    if name == "no_inverter":
        devices["batteries"] = {}
        devices["electric_vehicles"] = {}
        devices["inverters"] = {}
        return devices, {}, "Configure an inverter"
    if name == "soc_for_unknown_device":
        return devices, {**SOC, "ghost": 50}, "SoC supplied for unconfigured devices"
    if name == "duplicate_ids":
        devices["home_appliances"] = {"bat1": DISHWASHER}
        return devices, SOC, "must be unique"
    raise KeyError(name)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "unknown_battery",
        "battery_without_inverter",
        "two_inverters_one_battery",
        "no_inverter",
        "soc_for_unknown_device",
        "duplicate_ids",
    ],
)
async def test_configuration_errors_are_named(
    name: str, config_eos: ConfigEOS, fake_measurement: Any
) -> None:
    devices, soc, message = configuration_error(name)
    with pytest.raises(ValueError, match=message):
        await resolve_task(config_eos, devices, soc)


@pytest.mark.asyncio
async def test_missing_or_short_forecasts_are_errors(
    config_eos: ConfigEOS, fake_measurement: Any
) -> None:
    devices = {k: MANY_DEVICES[k] for k in ("batteries", "inverters")}
    soc = {"bat1": 40, "bat2": 70}
    series = forecast_series("optimize_input_1.json", 3600)
    short = {key: values[:20] for key, values in series.items()}
    with pytest.raises(ValueError, match="does not cover"):
        await resolve_task(config_eos, devices, soc, forecasts=short)
    with_gap = {key: list(values) for key, values in series.items()}
    with_gap["pv_forecast_wh"][14] = float("nan")
    with pytest.raises(ValueError, match="Missing or invalid PV within the control horizon"):
        await resolve_task(config_eos, devices, soc, forecasts=with_gap)
    negative = {key: list(values) for key, values in series.items()}
    negative["total_load"][12] = -5.0
    with pytest.raises(ValueError, match="nonnegative"):
        await resolve_task(config_eos, devices, soc, forecasts=negative)


@pytest.mark.asyncio
async def test_an_unknown_soc_starts_at_zero_percent(
    config_eos: ConfigEOS, fake_measurement: Any
) -> None:
    devices = {k: MANY_DEVICES[k] for k in ("batteries", "inverters")}
    task = await resolve_task(config_eos, devices, {"bat1": 33.125})
    assert [b.initial_soc_pct for b in task.problem.batteries] == [33.125, 0.0]
    # A fresh measurement is used, a stale one is not.
    now = ems_eos.observation_datetime
    fake_measurement.histories["bat2-soc-factor"] = [(now.subtract(seconds=60), 0.615125)]
    task = await resolve_task(config_eos, devices, {"bat1": 33})
    assert task.problem.batteries[1].initial_soc_pct == pytest.approx(61.5125)
    fake_measurement.histories["bat2-soc-factor"] = [(now.subtract(seconds=3000), 0.61)]
    task = await resolve_task(config_eos, devices, {"bat1": 33})
    assert task.problem.batteries[1].initial_soc_pct == 0.0
    # Each fallback is reported, a supplied or measured state of charge is not.
    assert [(w.code, w.devices) for w in task.warnings] == [("battery_soc_missing", ["bat2"])]
    assert "0 %" in task.warnings[0].message
    assert run_planning(task).solution.warnings == list(task.warnings)
    fake_measurement.histories["bat2-soc-factor"] = [(now.subtract(seconds=60), 0.5)]
    assert (await resolve_task(config_eos, devices, {"bat1": 33})).warnings == ()


@pytest.mark.asyncio
async def test_an_unknown_vehicle_soc_is_reported_like_a_battery_soc(
    config_eos: ConfigEOS, fake_measurement: Any
) -> None:
    devices = vehicle_devices(CHARGER_11KW)
    task = await resolve_task(config_eos, devices, {"bat1": 50})
    assert task.problem.vehicles[0].initial_soc_pct == 0.0
    assert [(w.code, w.devices) for w in task.warnings] == [("vehicle_soc_missing", ["ev1"])]
    task = await resolve_task(config_eos, devices, {"bat1": 50, "ev1": 30})
    assert task.warnings == ()


@pytest.mark.asyncio
async def test_model_limits_identify_the_reference_for_control_factors(
    config_eos: ConfigEOS, fake_measurement: Any
) -> None:
    devices = {k: MANY_DEVICES[k] for k in ("batteries", "electric_vehicles", "inverters")}
    task = await resolve_task(config_eos, devices, {**SOC, "ev1": 47.90643})
    limits = run_planning(task).solution.storage_limits

    assert set(limits) == set(task.battery_ids + task.vehicle_ids)
    assert limits["ev1"].initial_soc_percentage == 47.90643
    assert limits["ev1"].max_discharge_power_w == 0
    assert limits["ev1"].max_charge_power_w == task.problem.vehicles[0].max_charge_power_w
    assert limits["bat1"].max_discharge_power_w == task.problem.batteries[0].max_discharge_power_w
    assert limits["bat1"].min_soc_percentage == task.problem.batteries[0].min_soc_pct


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("deadline", "expected", "warned"),
    [
        ({"min_soc_deadline_datetime": "2026-09-16T10:20:00+02:00"}, 1, False),  # in slot 0
        ({"min_soc_deadline_datetime": "2026-09-16T08:00:00+02:00"}, 1, True),  # passed
        ({"min_soc_max_duration_h": 0.5}, 1, True),  # less than a slot (3600 s)
        ({"min_soc_max_duration_h": 1.5}, 1, False),
        ({"min_soc_deadline_datetime": "2026-09-16T13:00:00+02:00"}, 3, False),
        ({"min_soc_max_duration_h": 24.0}, None, False),  # at the horizon end
    ],
)
async def test_a_deadline_in_the_first_slot_is_judged_at_the_end_of_that_slot(
    deadline: dict[str, Any],
    expected: Any,
    warned: bool,
    config_eos: ConfigEOS,
    fake_measurement: Any,
) -> None:
    """The state at the start of slot 0 cannot change, so such a deadline moves to slot 1."""
    devices = {
        "batteries": {"bat1": BATTERY},
        "electric_vehicles": {"ev1": {**VEHICLE, **deadline}},
        "inverters": {
            "inv1": {"battery_id": "bat1", "profile_adjustment": {"nominal_power_w": 8000}}
        },
    }
    messages: list[str] = []

    async def resolve() -> Any:
        return await resolve_task(config_eos, devices, {"bat1": 50, "ev1": 30})

    from loguru import logger

    handler = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    try:
        task = await resolve()
    finally:
        logger.remove(handler)
    target = task.objective.vehicle_targets[0]
    assert target is not None and target.deadline_slot == expected
    assert any("ev1" in m and "as early as possible" in m for m in messages) is warned
    adjusted = [w for w in task.warnings if w.code == "vehicle_deadline_adjusted"]
    assert [w.devices for w in adjusted] == ([["ev1"]] if warned else [])
    assert all("as early as possible" in w.message for w in adjusted)


@pytest.mark.asyncio
async def test_a_vehicle_with_its_deadline_in_the_first_slot_charges_at_once(
    config_eos: ConfigEOS, fake_measurement: Any
) -> None:
    """Minimal case of the review: 60 kWh, 30 % to 80 %, 11 kW, departure within slot 0."""
    vehicle = {
        **VEHICLE,
        "profile_adjustment": {"nominal_capacity_wh": 60000, "max_charge_power_w": 11000},
        "charge_rates": [0.0, 1.0],
        "min_soc_deadline_datetime": "2026-09-16T10:20:00+02:00",
    }
    devices = {
        "batteries": {"bat1": BATTERY},
        "electric_vehicles": {"ev1": vehicle},
        "inverters": {
            "inv1": {"battery_id": "bat1", "profile_adjustment": {"nominal_power_w": 8000}}
        },
    }
    task = await resolve_task(config_eos, devices, {"bat1": 50, "ev1": 30})
    outcome = run_planning(task)
    assert outcome.plan.vehicle_charge[0, 0] > 0.0  # it charges in slot 0 instead of not at all


CHARGER_11KW = {
    **VEHICLE,
    "profile_adjustment": {"nominal_capacity_wh": 60000, "max_charge_power_w": 11000},
    "charge_rates": [0.0, 1.0],
}


def vehicle_devices(vehicle: dict[str, Any]) -> dict[str, Any]:
    """A house battery and one vehicle."""
    return {
        "batteries": {"bat1": BATTERY},
        "electric_vehicles": {"ev1": vehicle},
        "inverters": {
            "inv1": {"battery_id": "bat1", "profile_adjustment": {"nominal_power_w": 8000}}
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "deadline",
    [
        {"min_soc_max_duration_h": 2.0},  # 30 kWh needed, two slots of 11 kW at most
        {"min_soc_deadline_datetime": "2026-09-16T08:00:00+02:00"},  # passed
        {"min_soc_max_duration_h": 0.5},  # shorter than a slot
    ],
)
async def test_a_vehicle_target_out_of_reach_is_charged_as_far_as_possible_and_reported(
    deadline: dict[str, Any], config_eos: ConfigEOS, fake_measurement: Any
) -> None:
    devices = vehicle_devices({**CHARGER_11KW, **deadline})
    task = await resolve_task(config_eos, devices, {"bat1": 50, "ev1": 30})
    outcome = run_planning(task)
    assert outcome.plan.vehicle_charge[0, 0] > 0.0  # it still charges, as early as it can
    target = task.objective.vehicle_targets[0]
    assert target is not None and target.deadline_slot is not None
    reached = float(outcome.result.vehicle_soc_pct[0, target.deadline_slot])
    assert reached < 80.0 - 1.0
    unreachable = [w for w in outcome.solution.warnings if w.code == "vehicle_target_unreachable"]
    assert len(unreachable) == 1
    assert unreachable[0].devices == ["ev1"]
    assert f"reaches {reached:.1f} %" in unreachable[0].message
    assert f"short by {80.0 - reached:.1f} percentage points" in unreachable[0].message
    assert outcome.solution.objective["vehicle_target_penalty_eur"] > 0.0


@pytest.mark.asyncio
async def test_a_target_without_deadline_that_the_horizon_cannot_deliver_is_reported(
    config_eos: ConfigEOS, fake_measurement: Any
) -> None:
    slow = {
        **CHARGER_11KW,
        "profile_adjustment": {"nominal_capacity_wh": 60000, "max_charge_power_w": 500},
    }
    task = await resolve_task(config_eos, vehicle_devices(slow), {"bat1": 50, "ev1": 30})
    warnings = run_planning(task).solution.warnings
    assert [w.code for w in warnings] == ["vehicle_target_unreachable"]
    assert "by the end of the horizon" in warnings[0].message


@pytest.mark.asyncio
async def test_a_reachable_target_gives_no_warning(
    config_eos: ConfigEOS, fake_measurement: Any
) -> None:
    for deadline in ({}, {"min_soc_max_duration_h": 6.0}):
        devices = vehicle_devices({**CHARGER_11KW, **deadline})
        task = await resolve_task(config_eos, devices, {"bat1": 50, "ev1": 30})
        assert run_planning(task).solution.warnings == []
    # A target at or below the state of charge is not open at all.
    task = await resolve_task(
        config_eos,
        vehicle_devices({**CHARGER_11KW, "min_soc_percentage": 20}),
        {"bat1": 50, "ev1": 30},
    )
    assert run_planning(task).solution.warnings == []
