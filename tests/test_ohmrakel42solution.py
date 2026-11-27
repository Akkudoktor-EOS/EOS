"""The solution of the device by device optimizer in the types EOS, endpoints and adapters use.

The mapping of the controls to operation modes and factors is a table (see
``optimization.solution``); it is tested in both directions and for the combinations it cannot
carry. A plan read back from the series and from the instructions equals the plan that was
written, and simulating it gives the same objective, also with several batteries and vehicles.
The thermal plan holds temperature targets and limits per slot; the planned heat is a column.
"""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Optional

import numpy as np
import pytest
from test_requestfixtures import (  # noqa: F401  (fake_measurement_fixture registers the fixture)
    fake_measurement_fixture,
    resolve_task,
)
from test_planningrequestdevices import MANY_DEVICES, SOC
from test_planningrequestthermal import DEVICES as THERMAL_DEVICES
from test_planningrequestthermal import (
    THERMAL,
    thermal_world_fixture,  # noqa: F401
)

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.emplan import DDBCInstruction, FRBCInstruction
from akkudoktoreos.devices.devicesabc import ApplianceOperationMode, BatteryOperationMode
from akkudoktoreos.optimization.kernel import Plan, simulate
from akkudoktoreos.optimization.objective import Objective
from akkudoktoreos.optimization.planner import run_planning
from akkudoktoreos.optimization.search import DeviceSearch, Violation, repair_plan
from akkudoktoreos.thermal.thermalmodels import ThermalComfortBand
from akkudoktoreos.utils.datetimeutil import to_datetime
from akkudoktoreos.optimization.solution import (
    battery_controls,
    battery_operation,
    plan_from_frames,
    plan_from_instructions,
    thermal_plans_of,
    vehicle_level,
    vehicle_operation,
)

Mode = BatteryOperationMode

# (grid charge, discharge ceiling, PV charge, export) -> (mode, factor): the table of the module.
TABLE = [
    ((0.0, 0.0, 0.0, 0.0), (Mode.IDLE, 1.0)),
    ((0.0, 0.0, 1.0, 0.0), (Mode.NON_EXPORT, 1.0)),
    ((0.0, 0.3, 1.0, 0.0), (Mode.SELF_CONSUMPTION, 0.3)),
    ((0.0, 1.0, 1.0, 0.0), (Mode.SELF_CONSUMPTION, 1.0)),
    ((0.25, 0.0, 1.0, 0.0), (Mode.FORCED_CHARGE, 0.25)),
    ((0.0, 1.0, 0.0, 0.5), (Mode.GRID_SUPPORT_EXPORT, 0.5)),
]


@pytest.mark.parametrize(("controls", "expected"), TABLE)
def test_the_mapping_table_both_ways(
    controls: tuple[float, float, float, float], expected: tuple[str, float]
) -> None:
    assert battery_operation(*controls) == expected
    assert battery_controls(*expected) == controls


@pytest.mark.parametrize(
    "controls",
    [
        (0.0, 0.5, 0.0, 0.0),  # discharging with PV charging off
        (0.0, 1.0, 1.0, 0.5),  # export with PV charging on
        (0.0, 0.5, 1.0, 0.5),  # export below the full discharge ceiling
        (0.5, 0.5, 1.0, 0.0),  # grid charging together with discharging
        (0.5, 0.0, 1.0, 0.5),  # grid charging together with export
        (0.5, 0.0, 0.0, 0.0),  # grid charging without PV charging
    ],
)
def test_combinations_no_mode_carries_are_errors(
    controls: tuple[float, float, float, float],
) -> None:
    with pytest.raises(ValueError):
        battery_operation(*controls)


def test_modes_the_optimizer_does_not_use_are_errors() -> None:
    for mode in (Mode.PEAK_SHAVING, Mode.OUTAGE_SUPPLY):
        with pytest.raises(ValueError, match="not used"):
            battery_controls(mode, 1.0)
        with pytest.raises(ValueError, match="not used"):
            vehicle_level(mode, 1.0)
    assert vehicle_operation(0.0) == (Mode.IDLE, 1.0)
    assert vehicle_operation(0.75) == (Mode.GRID_SUPPORT_IMPORT, 0.75)
    assert vehicle_level(Mode.GRID_SUPPORT_IMPORT, 0.75) == 0.75
    assert vehicle_level(Mode.IDLE, 1.0) == 0.0


def assert_same_plan(read: Plan, written: Plan) -> None:
    """Every control of two plans is equal."""
    for name in (
        "battery_grid_charge",
        "battery_discharge_limit",
        "battery_pv_charge",
        "battery_export",
        "vehicle_charge",
        "appliance_starts",
        "thermal_heat_w",
    ):
        np.testing.assert_array_equal(getattr(read, name), getattr(written, name), err_msg=name)


def random_plan(task: Any, seed: int) -> Plan:
    """A random plan on the allowed values: levels of all devices, appliance starts, heat."""
    rng = np.random.RandomState(seed)
    problem = task.problem
    plan = Plan.idle(problem)
    for battery in range(len(problem.batteries)):
        plan.battery_grid_charge[battery] = rng.choice([0.0, 0.0, 0.25, 0.5, 1.0], problem.slots)
        plan.battery_discharge_limit[battery] = rng.choice([0.0, 0.5, 1.0], problem.slots)
        plan.battery_export[battery] = rng.choice([0.0, 0.0, 0.5, 1.0], problem.slots)
        # As in the search, an export level comes with the full discharge ceiling.
        plan.battery_discharge_limit[battery] = np.where(
            plan.battery_export[battery] > 0.0, 1.0, plan.battery_discharge_limit[battery]
        )
    for vehicle in range(len(problem.vehicles)):
        plan.vehicle_charge[vehicle] = rng.choice([0.0, 0.5, 1.0], problem.slots)
    return repair_plan(problem, plan, task.space, task.search)


@pytest.mark.asyncio
async def test_a_plan_read_back_equals_the_plan_that_was_written(
    config_eos: ConfigEOS, fake_measurement: Any
) -> None:
    # Direct marketing makes export levels part of the plans; PV charging stays always on.
    devices = {
        **MANY_DEVICES,
        "batteries": {
            key: {**value, "pv_charge_switchable": False}
            for key, value in MANY_DEVICES["batteries"].items()
        },
    }
    forecasts = {
        "feedintariff": {"direct_marketing_enabled": True},
        "optimization": {"algorithm": "OHMRAKEL42"},
    }
    task = await resolve_task(config_eos, devices, SOC, extra=forecasts)
    problem = task.problem
    assert problem.direct_marketing
    # Positive tariffs in the afternoon so that export is allowed there.
    objective = Objective(problem, task.objective)
    seen: set[str] = set()
    for seed in range(12):
        plan = random_plan(task, seed)
        outcome = _outcome(task, plan)
        solution = outcome.optimization_solution.solution.to_dataframe()
        prediction = outcome.optimization_solution.prediction.to_dataframe()
        from_series = plan_from_frames(task, solution, prediction)
        from_instructions = plan_from_instructions(task, outcome.energy_management_plan)
        assert_same_plan(from_series, plan)
        assert_same_plan(from_instructions, plan)
        # The same objective from the plan that was read back.
        written = objective.evaluate(plan, simulate(problem, plan)).total_eur
        read = objective.evaluate(from_instructions, simulate(problem, from_instructions)).total_eur
        assert abs(written - read) <= 1e-9
        seen.update(
            mode
            for index in range(len(task.battery_ids))
            for mode in _modes(outcome, task.battery_ids[index])
        )
    # The random plans use every mode of the table.
    assert {Mode.NON_EXPORT, Mode.SELF_CONSUMPTION, Mode.FORCED_CHARGE} <= seen


def _outcome(task: Any, plan: Plan) -> Any:
    from akkudoktoreos.optimization.objective import Objective as _Objective
    from akkudoktoreos.optimization.solution import build_outcome

    problem = task.problem
    result = simulate(problem, plan)
    breakdown = _Objective(problem, task.objective).evaluate(plan, result)
    return build_outcome(task, plan, result, breakdown, breakdown, {}, warm_started=False)


def _modes(outcome: Any, device: str) -> set[str]:
    frame = outcome.optimization_solution.solution.to_dataframe()
    return {
        mode
        for mode in BatteryOperationMode
        if (frame[f"{device}_{mode.lower()}_op_mode"] == 1.0).any()
    }


@pytest.mark.asyncio
async def test_the_searched_plan_has_all_representations(
    config_eos: ConfigEOS, fake_measurement: Any
) -> None:
    task = await resolve_task(config_eos, MANY_DEVICES, SOC)
    # The search of this task may switch PV charging off; use a task without that.
    devices = {
        **MANY_DEVICES,
        "batteries": {
            key: {**value, "pv_charge_switchable": False}
            for key, value in MANY_DEVICES["batteries"].items()
        },
    }
    task = await resolve_task(config_eos, devices, SOC)
    outcome = run_planning(task)
    frame = outcome.optimization_solution.solution.to_dataframe()
    slots = task.problem.slots
    assert len(frame) == slots
    # One mode per slot and device, the factors in range, SoC as factor of the capacity.
    for device in (*task.battery_ids, *task.vehicle_ids):
        active = sum(frame[f"{device}_{mode.lower()}_op_mode"] for mode in BatteryOperationMode)
        assert bool((active == 1.0).all())  # type: ignore[union-attr]
        assert ((frame[f"{device}_soc_factor"] >= 0) & (frame[f"{device}_soc_factor"] <= 1)).all()
    for device in task.appliance_ids:
        assert (frame[f"{device}_run_op_mode"] + frame[f"{device}_off_op_mode"] == 1.0).all()
        assert frame[f"{device}_energy_wh"].sum() > 0
    # The figures are those of the simulation; the solution holds the objective and the plan.
    np.testing.assert_allclose(frame["grid_consumption_energy_wh"], outcome.result.grid_import_wh)
    solution = outcome.solution
    assert solution.objective["total_eur"] == pytest.approx(outcome.breakdown.total_eur)
    assert solution.objective["total_eur"] <= solution.start_objective_eur + 1e-9
    assert solution.battery_ids == ["bat1", "bat2"]
    assert len(solution.controls["bat1_discharge_limit"]) == slots
    # The instructions: every device has its own, in the types the adapters read.
    resources = {
        instruction.resource_id for instruction in outcome.energy_management_plan.instructions
    }
    assert resources == {"bat1", "bat2", "ev1", "ev2", "dishwasher", "washer"}
    for instruction in outcome.energy_management_plan.instructions:
        assert isinstance(instruction, (FRBCInstruction, DDBCInstruction))
        assert 0.0 <= instruction.operation_mode_factor <= 1.0
        if isinstance(instruction, DDBCInstruction):
            assert instruction.operation_mode_id in (
                ApplianceOperationMode.RUN,
                ApplianceOperationMode.OFF,
            )
    # The plan read back from the instructions simulates to the same objective.
    read = plan_from_instructions(task, outcome.energy_management_plan)
    assert_same_plan(read, outcome.plan)


@pytest.mark.asyncio
async def test_a_battery_with_switchable_pv_charging_reports_the_gap(
    config_eos: ConfigEOS, fake_measurement: Any
) -> None:
    task = await resolve_task(config_eos, MANY_DEVICES, SOC)
    plan = Plan.idle(task.problem)
    plan.battery_pv_charge[0, 3] = 0.0  # discharge allowed but PV charging off
    result = simulate(task.problem, plan)
    breakdown = Objective(task.problem, task.objective).evaluate(plan, result)
    from akkudoktoreos.optimization.solution import build_outcome

    with pytest.raises(ValueError, match="Battery bat1, slot 3.*PV charging switched off"):
        build_outcome(task, plan, result, breakdown, breakdown, {}, warm_started=False)


@pytest.mark.asyncio
async def test_the_thermal_plan_holds_targets_and_limits(
    config_eos: ConfigEOS, thermal_world: dict[str, Any]
) -> None:
    task = await resolve_task(config_eos, THERMAL_DEVICES, {"bat1": 50}, extra=THERMAL)
    outcome = run_planning(task)
    assert len(outcome.thermal_plans) == 1
    plan = outcome.thermal_plans[0]
    info = task.thermal[0]
    assert plan.zone_id == "living" and plan.plan_id.startswith("hp1@")
    assert len(plan.intervals) == task.problem.slots
    # the target of a slot is the planned room temperature at its end
    room = outcome.result.thermal.temperature_c[0][1 : task.problem.slots + 1, 0]  # type: ignore[union-attr]
    slots = task.problem.slots
    for slot, interval in enumerate(plan.intervals):
        band, following = info.bands[slot], info.bands[min(slot + 1, slots - 1)]
        protection = float(info.params.protection_min_c[min(slot + 1, slots - 1)])  # type: ignore[index]
        # expected cost: the plan may fall short of the minimum on purpose, never below protection
        assert protection <= interval.min_temperature_c <= band.min_temperature_c
        assert interval.max_temperature_c == band.max_temperature_c
        assert (
            interval.min_temperature_c
            <= interval.target_temperature_c
            <= interval.max_temperature_c
        )
        assert interval.target_temperature_c == pytest.approx(
            min(max(room[slot], protection), following.max_temperature_c)
        )
    # Planned heat and electric power are forecast columns, the plan reads back from them.
    prediction = outcome.optimization_solution.prediction.to_dataframe()
    heat = prediction["hp1_planned_heat_w"].to_numpy()
    electric = prediction["hp1_planned_electric_w"].to_numpy()
    np.testing.assert_allclose(heat, outcome.plan.thermal_heat_w[0])
    assert (electric[heat > 0] > 0).all() and heat.max() > 0
    assert "hp1_room_temperature_c" in prediction.columns
    solution = outcome.optimization_solution.solution.to_dataframe()
    read = plan_from_frames(task, solution, prediction)
    assert_same_plan(read, outcome.plan)
    # The heat is not a set point: the instructions carry no thermal device.
    assert "hp1" not in {i.resource_id for i in outcome.energy_management_plan.instructions}
    assert outcome.solution.controls["hp1_heat_w"] == outcome.plan.thermal_heat_w[0].tolist()


def thermal_plan_of_rooms(
    rooms: list[float],
    bands: list[tuple[float, float, float]],
    blocked: Optional[list[bool]] = None,
    tiered: bool = False,
) -> Any:
    """The thermal plan of a heat source whose room temperatures at the slot ends are given."""
    slots = len(rooms)
    barred = blocked or [False] * slots
    start = to_datetime("2026-11-24T16:00:00+01:00")
    grid = SimpleNamespace(observation=start, slot_start=lambda slot: start.add(minutes=15 * slot))
    info = SimpleNamespace(
        zone_id="living",
        source_id="hp1",
        bands=tuple(
            ThermalComfortBand(
                min_temperature_c=low, target_temperature_c=mid, max_temperature_c=high
            )
            for low, mid, high in bands
        ),
        params=SimpleNamespace(
            heat_range_w=lambda slot: None if barred[slot] else (400.0, 3000.0),
            tiered=tiered,
            protection_min_c=np.full(slots, 16.0),
        ),
    )
    task = SimpleNamespace(grid=grid, problem=SimpleNamespace(slots=slots), thermal=(info,))
    temperatures = np.array([[20.0, 20.0]] + [[room, room] for room in rooms])
    result = SimpleNamespace(thermal=SimpleNamespace(temperature_c=[temperatures]))
    (plan,) = thermal_plans_of(task, None, result, 600)  # type: ignore[arg-type]
    return plan


def test_the_target_of_a_slot_before_a_band_change_is_held_against_the_next_band() -> None:
    # At most 20 °C until 16:30, at least 21 °C afterwards: the model heats to 21 °C in slot 1.
    before, after = (19.0, 19.5, 20.0), (21.0, 21.5, 22.0)
    plan = thermal_plan_of_rooms([19.5, 21.0, 21.5], [before, before, after])
    ahead = plan.intervals[1]
    assert ahead.target_temperature_c == pytest.approx(21.0)
    # the interval stays valid: the limit is widened to the planned target, the other kept
    assert ahead.min_temperature_c == 19.0 and ahead.max_temperature_c == 21.0
    assert plan.intervals[0].max_temperature_c == 20.0
    assert plan.intervals[2].min_temperature_c == 21.0


def test_the_target_before_a_lower_band_coasts_down() -> None:
    day, night = (20.7, 21.0, 21.5), (16.0, 16.3, 21.5)
    plan = thermal_plan_of_rooms([20.8, 17.0, 16.5], [day, day, night])
    assert plan.intervals[1].target_temperature_c == pytest.approx(17.0)
    assert plan.intervals[1].min_temperature_c == pytest.approx(17.0)


def test_a_planned_shortfall_is_not_fought_by_the_executor() -> None:
    # Expected cost: the plan lets the room fall to 19.5 °C against a minimum of 20.7 °C. The
    # target and the lower limit follow the plan (a margin below it), never the protection minimum.
    band = (20.7, 21.0, 24.0)
    plan = thermal_plan_of_rooms([20.4, 19.5, 17.0, 15.0], [band] * 4, tiered=True)
    assert [row.target_temperature_c for row in plan.intervals] == pytest.approx(
        [20.4, 19.5, 17.0, 16.0]
    )
    assert plan.intervals[1].min_temperature_c == pytest.approx(19.5 - 0.2)
    assert plan.intervals[1].min_temperature_c <= plan.intervals[1].target_temperature_c
    assert all(row.min_temperature_c >= 16.0 for row in plan.intervals)
    # without the expected cost the minimum is still asked for
    fixed = thermal_plan_of_rooms([20.4, 19.5, 17.0, 15.0], [band] * 4)
    assert all(row.target_temperature_c >= 20.7 for row in fixed.intervals)


def test_a_planned_room_above_the_minimum_keeps_the_band_for_the_executor() -> None:
    band = (20.7, 21.0, 24.0)
    plan = thermal_plan_of_rooms([21.5, 21.8, 22.0], [band] * 3, tiered=True)
    # the room starts at 20 °C in this plan, so the first slot may not ask for more than that
    assert [row.min_temperature_c for row in plan.intervals] == pytest.approx([19.8, 20.7, 20.7])


def test_a_barred_slot_does_not_ask_for_heat_and_says_so() -> None:
    # 19 °C in the room, at least 21 °C wanted, but the heat pump must not run in slot 1.
    band = (21.0, 21.5, 23.0)
    plan = thermal_plan_of_rooms([19.5, 18.9, 19.0], [band] * 3, blocked=[False, True, False])
    barred = plan.intervals[1]
    assert barred.heating_allowed is False
    assert barred.target_temperature_c == pytest.approx(18.9)
    assert barred.min_temperature_c <= barred.target_temperature_c <= barred.max_temperature_c
    # an unbarred slot below its minimum still asks for the minimum, and says heating is allowed
    assert plan.intervals[0].heating_allowed and plan.intervals[0].target_temperature_c == 21.0
    assert plan.intervals[2].heating_allowed


def test_without_band_change_and_bar_the_thermal_plan_is_as_before() -> None:
    band = (20.0, 21.0, 23.0)
    rooms = [
        20.5,
        22.0,
        24.0,
        19.0,
    ]  # within the band, within, above the maximum, below the minimum
    plan = thermal_plan_of_rooms(rooms, [band] * 4)
    for interval, room in zip(plan.intervals, rooms):
        assert (interval.min_temperature_c, interval.max_temperature_c) == (20.0, 23.0)
        assert interval.target_temperature_c == pytest.approx(min(max(room, 20.0), 23.0))
        assert interval.heating_allowed


def inject_violation(monkeypatch: pytest.MonkeyPatch, violation: Violation) -> None:
    """Let every search of a problem with a heat pump report ``violation``."""
    real_solve = DeviceSearch.solve

    def solve(self: DeviceSearch, *args: Any, **kwargs: Any) -> Any:
        found = real_solve(self, *args, **kwargs)
        return replace(found, violations=[violation]) if self.problem.thermal else found

    monkeypatch.setattr(DeviceSearch, "solve", solve)


@pytest.mark.asyncio
async def test_a_violation_of_an_electrical_device_stays_an_error(
    config_eos: ConfigEOS, thermal_world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    task = await resolve_task(config_eos, THERMAL_DEVICES, {"bat1": 50}, extra=THERMAL)
    inject_violation(monkeypatch, Violation("battery", 0, 2, "discharge level not allowed"))
    with pytest.raises(ValueError, match="violates the allowed values"):
        run_planning(task)


def test_the_action_table_has_no_combination_the_modes_cannot_carry() -> None:
    import test_searchscenarios as sc

    from akkudoktoreos.optimization.search import SearchSettings
    from akkudoktoreos.optimization.search.actions import battery_actions

    problem, objective = sc.build(sc.make_series("winter_clear"), "A")
    from akkudoktoreos.optimization.search import empty_plan

    settings = SearchSettings(pv_charge_switchable=True, export_levels=(0.5, 1.0))
    table = battery_actions(problem, objective, settings, empty_plan(problem), 0)
    grid, discharge, pv, export = (table.values[0, :, c] for c in range(4))
    assert (pv == 0.0).any()  # the IDLE action
    off = (pv == 0.0) & (export == 0.0)
    assert not ((discharge[off] > 0) | (grid[off] > 0)).any()
    exporting = export > 0
    assert (discharge[exporting] == 1.0).all() or not exporting.any()
    assert (pv[exporting] == 0.0).all()  # export: PV surplus and battery feed in side by side
    assert (pv[~exporting & (discharge > 0)] == 1.0).all()


@pytest.mark.parametrize(
    ("grid", "discharge", "pv", "export", "reason"),
    [
        (0.0, 0.5, 0.0, 0.0, "PV charging off together with discharging"),
        (0.0, 1.0, 1.0, 0.5, "export with PV charging on"),
        (0.0, 0.5, 1.0, 0.5, "export with a discharge ceiling below 1"),
    ],
)
def test_the_check_rejects_and_the_repair_fixes_what_the_modes_cannot_carry(
    grid: float, discharge: float, pv: float, export: float, reason: str
) -> None:
    import test_searchscenarios as sc

    from akkudoktoreos.optimization.search import SearchSettings, check_plan

    problem, _ = sc.build(sc.make_series("winter_clear"), "A")
    # Direct marketing with a positive tariff makes the export level allowed.
    problem = replace_problem(problem)
    settings = SearchSettings(pv_charge_switchable=True, export_levels=(0.5, 1.0))
    plan = Plan.idle(problem)
    plan.battery_grid_charge[0, 5] = grid
    plan.battery_discharge_limit[0, 5] = discharge
    plan.battery_pv_charge[0, 5] = pv
    plan.battery_export[0, 5] = export
    reasons = {v.reason for v in check_plan(problem, plan, None, settings)}
    assert reason in reasons
    repaired = repair_plan(problem, plan, None, settings)
    assert check_plan(problem, repaired, None, settings) == []
    # The repaired slot has a mode.
    battery_operation(
        float(repaired.battery_grid_charge[0, 5]),
        float(repaired.battery_discharge_limit[0, 5]),
        float(repaired.battery_pv_charge[0, 5]),
        float(repaired.battery_export[0, 5]),
    )


def replace_problem(problem: Any) -> Any:
    """The problem with direct marketing and a positive tariff, so that export is allowed."""
    from dataclasses import replace

    return replace(
        problem, direct_marketing=True, feed_in_tariff_per_wh=np.full(problem.slots, 0.0001)
    )


@pytest.mark.asyncio
async def test_switchable_pv_charging_gives_plans_the_modes_carry(
    config_eos: ConfigEOS, fake_measurement: Any
) -> None:
    # bat1 of MANY_DEVICES has switchable PV charging; with direct marketing, export is possible.
    extra = {"feedintariff": {"direct_marketing_enabled": True}}
    task = await resolve_task(config_eos, MANY_DEVICES, SOC, extra=extra)
    assert task.search.pv_switchable_of(0)
    outcome = run_planning(task)  # raises for a control no operation mode carries
    read = plan_from_instructions(task, outcome.energy_management_plan)
    assert_same_plan(read, outcome.plan)
    frame = outcome.optimization_solution.solution.to_dataframe()
    solution_read = plan_from_frames(
        task, frame, outcome.optimization_solution.prediction.to_dataframe()
    )
    assert_same_plan(solution_read, outcome.plan)
    objective = Objective(task.problem, task.objective)
    again = objective.evaluate(read, simulate(task.problem, read)).total_eur
    assert abs(again - outcome.breakdown.total_eur) <= 1e-9
