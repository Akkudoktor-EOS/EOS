"""Action sets of the single device planning: the control values a device may take per slot.

An action is one row of four numbers ``(grid charge level, discharge ceiling, PV charging,
export level)`` for a battery and ``(charge level, 0, 0, 0)`` for a vehicle. The set follows the
decided control levels.

Battery
    Off (no PV charging, no discharging); PV charging only; grid charging at every level; a
    discharge ceiling at every level; with direct marketing export at every export level (with
    the full discharge ceiling); and "fill the grid limit", the largest grid charge level that
    keeps the import below the connection limit. Grid charging never comes with discharging.
    PV charging is off only in the first action: discharging, grid charging and export always
    come with PV charging on, because the operation modes carry no other combination.
Vehicle
    Every charge level while it is plugged in, and "fill the grid limit".

The action table can omit discharge ceilings that cover the same forecast load as the largest
level. Finished plans also use the largest equivalent level, leaving room to cover higher
actual demand while preserving deliberate limits on forecast discharge.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from akkudoktoreos.optimization.kernel.problem import Plan, PlanningProblem
from akkudoktoreos.optimization.kernel.simulate import SimulationResult, simulate
from akkudoktoreos.optimization.objective import Objective
from akkudoktoreos.optimization.search.settings import SearchSettings

COL_GRID = 0
COL_DISCHARGE = 1
COL_PV = 2
COL_EXPORT = 3
N_COLUMNS = 4


@dataclass(frozen=True, eq=False)
class ActionTable:
    """The actions of one device for every slot.

    Attributes:
        values: ``(slots, actions, 4)`` control values.
        valid: ``(slots, actions)`` 1 where the action is worth trying in the slot.
        extra: ``(slots, actions)`` cost of the action that the simulation does not report
            (the break-even penalty of grid charging) [EUR].
    """

    values: np.ndarray
    valid: np.ndarray
    extra: np.ndarray


def thin(levels: tuple[float, ...], count: int) -> tuple[float, ...]:
    """Return ``count`` levels spread evenly over the levels; the largest one stays."""
    ordered = tuple(sorted(levels))
    if count >= len(ordered):
        return ordered
    picks = {max(round((j + 1) * len(ordered) / count) - 1, 0) for j in range(count)}
    return tuple(ordered[i] for i in sorted(picks))


def positive_levels(rates: tuple[float, ...]) -> tuple[float, ...]:
    """Return the positive charge levels of a storage, ascending."""
    return tuple(sorted({float(rate) for rate in rates if rate > 0.0}))


def ac_charge_possible(problem: PlanningProblem, battery: int) -> bool:
    """Return whether the inverter of the battery can charge it from the grid."""
    for inverter in problem.inverters:
        if inverter.battery_index == battery:
            return inverter.ac_to_dc_efficiency > 0.0 and (
                inverter.max_ac_charge_power_w is None or inverter.max_ac_charge_power_w > 0.0
            )
    return False


def slot_scale(problem: PlanningProblem) -> np.ndarray:
    """Return the part of each slot that is simulated (below 1 only for slot 0)."""
    scale = np.ones(problem.slots)
    if problem.slots:
        scale[0] = problem.first_slot_fraction
    return scale


def _fill_level(
    levels: tuple[float, ...], energy_per_level_wh: np.ndarray | float, headroom_wh: np.ndarray
) -> np.ndarray:
    """Return per slot the largest level whose grid energy fits the headroom, 0 for none."""
    fill = np.zeros(headroom_wh.shape[0])
    for level in levels:
        fill = np.where(level * energy_per_level_wh <= headroom_wh, level, fill)
    return fill


def neutral_plan(
    plan: Plan,
    *,
    battery: Optional[int] = None,
    vehicle: Optional[int] = None,
) -> Plan:
    """Return a copy of ``plan`` in which one device does nothing.

    A battery then neither charges from the grid, exports nor discharges.
    """
    copy = plan.copy()
    if vehicle is not None:
        copy.vehicle_charge[vehicle] = 0.0
    if battery is not None:
        copy.battery_grid_charge[battery] = 0.0
        copy.battery_export[battery] = 0.0
        copy.battery_discharge_limit[battery] = 0.0
    return copy


def normalize_discharge(
    problem: PlanningProblem,
    plan: Plan,
    settings: SearchSettings,
    reference: Optional[SimulationResult] = None,
) -> Plan:
    """Raise positive discharge ceilings where the largest level delivers the same energy.

    Probe one battery at a time from the original state of charge of each slot. Matching
    its raw withdrawal and DC and AC delivery preserves its state and the power available
    to downstream inverters. Zero ceilings and export directives retain their meaning.
    """
    candidate = plan
    for battery in range(len(problem.batteries)):
        levels = settings.discharge_levels_of(battery)
        if not levels:
            continue
        ceiling = plan.battery_discharge_limit[battery]
        eligible = (ceiling > 0.0) & (ceiling < levels[-1]) & (plan.battery_export[battery] == 0.0)
        if not eligible.any():
            continue
        if reference is None:
            reference = simulate(problem, plan)
        trial = candidate.copy()
        trial.battery_discharge_limit[battery, eligible] = levels[-1]
        result = simulate(problem, trial, slot_soc_wh=reference.soc_wh)
        equivalent = (
            eligible
            & (result.discharge_raw_wh[battery] == reference.discharge_raw_wh[battery])
            & (result.discharge_dc_wh[battery] == reference.discharge_dc_wh[battery])
            & (
                result.battery_discharge_ac_wh[battery]
                == reference.battery_discharge_ac_wh[battery]
            )
        )
        if equivalent.any():
            trial.battery_discharge_limit[battery] = np.where(equivalent, levels[-1], ceiling)
            candidate = trial
    return candidate


def battery_actions(
    problem: PlanningProblem,
    objective: Objective,
    settings: SearchSettings,
    plan: Plan,
    battery: int,
    levels_count: Optional[int] = None,
) -> ActionTable:
    """Build the action table of a battery against the current plan of the other devices.

    Args:
        problem: The planning task.
        objective: The objective (for the break-even penalty of grid charging).
        settings: Control levels.
        plan: The current plan; the other devices stay as they are.
        battery: Index of the battery.
        levels_count: Thin each list of levels to this many entries (pair moves); None keeps all.
    """
    slots = problem.slots
    unit = problem.batteries[battery]
    scale = slot_scale(problem)
    slot_h = problem.slot_duration_h
    inverter = next(i for i in problem.inverters if i.battery_index == battery)

    grid_levels = positive_levels(unit.charge_rates) if ac_charge_possible(problem, battery) else ()
    can_discharge = unit.max_discharge_power_w > 0.0 and unit.capacity_wh > 0.0
    discharge_levels = settings.discharge_levels_of(battery) if can_discharge else ()
    export_levels = (
        settings.export_levels_of(battery) if problem.direct_marketing and can_discharge else ()
    )
    all_grid_levels = grid_levels
    if levels_count is not None:
        grid_levels = thin(grid_levels, levels_count)
        discharge_levels = thin(discharge_levels, levels_count)
        export_levels = thin(export_levels, levels_count)
    pv_switchable = settings.pv_switchable_of(battery)

    # (grid, discharge, pv, export), plus the index of the discharge level for the mask.
    rows: list[tuple[float, float, float, float]] = []
    discharge_rank: list[int] = []  # -1 where the row is no discharge row
    export_row: list[bool] = []

    def add(row: tuple[float, float, float, float], rank: int = -1, export: bool = False) -> None:
        rows.append(row)
        discharge_rank.append(rank)
        export_row.append(export)

    if pv_switchable:
        add((0.0, 0.0, 0.0, 0.0))
    add((0.0, 0.0, 1.0, 0.0))
    for level in grid_levels:
        add((level, 0.0, 1.0, 0.0))
    for rank, level in enumerate(discharge_levels):
        add((0.0, level, 1.0, 0.0), rank)
    for level in export_levels:
        add((0.0, 1.0, 0.0, level), export=True)

    count = len(rows) + 1  # the last column is "fill the grid limit"
    values = np.zeros((slots, count, N_COLUMNS))
    values[:, : count - 1, :] = np.asarray(rows)
    valid = np.ones((slots, count), dtype=np.uint8)
    extra = np.zeros((slots, count))
    cost_per_level = objective.break_even_cost_per_level(battery)
    for index, row in enumerate(rows):
        extra[:, index] = row[COL_GRID] * cost_per_level

    # For a single battery with constant discharge efficiency, prune ceilings that can
    # always supply the request. The worst inverter efficiency gives a conservative bound.
    # With multiple batteries, a move may change the state of batteries serving load first.
    if (
        discharge_levels
        and len(problem.batteries) == 1
        and levels_count is None
        and unit.discharging_efficiency_curve is None
    ):
        baseline = simulate(problem, neutral_plan(plan, battery=battery))
        need_wh = baseline.grid_import_wh
        efficiency = min(
            (efficiency for _, efficiency in inverter.dc_to_ac_efficiency_curve or ()),
            default=inverter.dc_to_ac_efficiency,
        )
        guaranteed_ac_wh = (
            unit.max_discharge_power_w * slot_h * scale * (unit.discharging_efficiency * efficiency)
        )
        for index, rank in enumerate(discharge_rank):
            if rank >= 0:
                limiting = discharge_levels[rank] * guaranteed_ac_wh <= need_wh
                valid[:, index] = (need_wh > 0.0) & (limiting | (rank == len(discharge_levels) - 1))
    for index, is_export in enumerate(export_row):
        if is_export:
            valid[:, index] = problem.feed_in_tariff_per_wh > 0.0

    fill = count - 1
    valid[:, fill] = 0
    if grid_levels and problem.grid.import_limit_w is not None:
        baseline = simulate(problem, neutral_plan(plan, battery=battery))
        headroom = problem.grid.import_limit_w * slot_h * scale - baseline.grid_import_wh
        per_level: np.ndarray | float = (
            unit.max_charge_power_w * slot_h * scale / max(inverter.ac_to_dc_efficiency, 1e-9)
        )
        if inverter.max_ac_charge_power_w is not None:
            per_level = np.minimum(per_level, inverter.max_ac_charge_power_w * slot_h * scale)
        level_fill = _fill_level(all_grid_levels, per_level, headroom)
        listed = np.isin(level_fill, np.asarray(grid_levels))
        values[:, fill, COL_GRID] = level_fill
        values[:, fill, COL_PV] = 1.0
        extra[:, fill] = level_fill * cost_per_level
        valid[:, fill] = (level_fill > 0.0) & ~listed
    return ActionTable(values=values, valid=valid, extra=extra)


def vehicle_actions(
    problem: PlanningProblem, plan: Plan, vehicle: int, levels_count: Optional[int] = None
) -> ActionTable:
    """Build the action table of a vehicle: its charge levels while it is plugged in."""
    slots = problem.slots
    unit = problem.vehicles[vehicle]
    scale = slot_scale(problem)
    slot_h = problem.slot_duration_h
    all_levels = positive_levels(unit.charge_rates)
    levels = all_levels if levels_count is None else thin(all_levels, levels_count)
    count = 1 + len(levels) + 1
    values = np.zeros((slots, count, N_COLUMNS))
    valid = np.zeros((slots, count), dtype=np.uint8)
    valid[:, 0] = 1
    available = (
        np.asarray(problem.kernel_arrays()["storage_available"])[len(problem.batteries) + vehicle]
        > 0.0
    )
    for index, level in enumerate(levels):
        values[:, 1 + index, COL_GRID] = level
        valid[:, 1 + index] = available
    fill = count - 1
    if levels and problem.grid.import_limit_w is not None:
        baseline = simulate(problem, neutral_plan(plan, vehicle=vehicle))
        headroom = problem.grid.import_limit_w * slot_h * scale - baseline.grid_import_wh
        level_fill = _fill_level(all_levels, unit.max_charge_power_w * slot_h * scale, headroom)
        values[:, fill, COL_GRID] = level_fill
        valid[:, fill] = (level_fill > 0.0) & available & ~np.isin(level_fill, np.asarray(levels))
    return ActionTable(values=values, valid=valid, extra=np.zeros((slots, count)))
