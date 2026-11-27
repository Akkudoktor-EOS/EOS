"""Terminal value with a lookahead: the finite tail solved backwards once.

Beyond the planning horizon the forecasts continue for some hours (the tail). A Bellman
recursion over a grid of stored-energy states finds, per state at the end of the horizon, the
best cash flow the battery can earn in the tail plus the continuation value after it. The
slot physics is the kernel's own (``optimization.kernel.core.run_plan`` on a one-slot
problem), so the lookahead and the plan simulation cannot drift apart.

The curve is neither monotone nor clamped: an empty battery can earn money by charging at
negative prices and selling later.
"""

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from akkudoktoreos.devices.models import inverter as inv
from akkudoktoreos.devices.models.inverter import (
    InverterParams,
    pack_inverters,
    reference_dc_to_ac_efficiency,
)
from akkudoktoreos.devices.models.jit import njit
from akkudoktoreos.devices.models.storage import StorageParams, pack_storages
from akkudoktoreos.optimization.kernel import core
from akkudoktoreos.optimization.kernel.selfconsumption import load_table
from akkudoktoreos.optimization.solutionmodel import TailDiagnostics, TailPlanSlot
from akkudoktoreos.optimization.terminalvalue import ValueCurve, interpolate

# A tail action: (PV charging allowed, local discharge allowed, grid charge level, export level).
TailAction = tuple[int, int, float, float]


def tail_actions(
    charge_rates: list[float], export_rates: list[float], direct_marketing: bool
) -> list[TailAction]:
    """Return the actions the lookahead chooses from in every slot.

    PV charging with or without discharge, grid charging at each level; with direct marketing
    additionally no PV charging (to keep headroom) and battery export at each level.
    """
    actions: list[TailAction] = [(1, 0, 0.0, 0.0), (1, 1, 0.0, 0.0)]
    actions += [(1, 0, rate, 0.0) for rate in charge_rates if rate > 0]
    if direct_marketing:
        actions += [(0, 0, 0.0, 0.0), (0, 1, 0.0, 0.0)]
        actions += [(0, 0, rate, 0.0) for rate in charge_rates if rate > 0]
        actions += [(dc, 1, 0.0, rate) for dc in (0, 1) for rate in export_rates if rate > 0]
    return actions


@njit
def _scratch() -> tuple:
    """Buffers of the one-slot problem the lookahead simulates, in the order ``_step`` unpacks them."""
    return (
        np.empty(1),  # stored energy
        np.empty(1),  # PV
        np.empty(1),  # load
        np.empty(1),  # import price
        np.empty(1),  # feed-in tariff
        np.zeros((1, 1)),  # grid charge level
        np.zeros((1, 1)),  # discharge ceiling
        np.zeros((1, 1)),  # PV charge permission
        np.zeros((1, 1)),  # export level
        np.zeros((0, 1)),  # vehicle charge
        np.zeros((0, 1), dtype=np.int64),  # appliance starts
        np.ones((1, 1)),  # storage available
        np.zeros((0, 1)),  # appliance run energy
        np.zeros(0, dtype=np.int64),  # appliance run slots
        np.zeros((0, 1)),  # pinned appliance load
        np.zeros((core.N_SERIES, 1)),  # series of the slot
        np.zeros((1, core.N_DEVICE, 1)),  # device columns of the slot
        np.zeros(core.N_TOTALS),  # totals of the slot
    )


@njit
def _step(
    buffers: tuple,
    state: float,
    slot: int,
    prices: np.ndarray,
    loads: np.ndarray,
    pvs: np.ndarray,
    tariffs: np.ndarray,
    action: np.ndarray,
    constants: np.ndarray,
    load_axis: np.ndarray,
    load_levels: np.ndarray,
    load_values: np.ndarray,
    s_fields: np.ndarray,
    s_tables: np.ndarray,
    s_rates: np.ndarray,
    i_fields: np.ndarray,
    curve_x: np.ndarray,
    curve_y: np.ndarray,
) -> float:
    """Apply one tail action in one slot from one stored-energy state; return the next state.

    The only place where the slot physics of the lookahead is set up, for the backward
    recursion and the forward walk alike. The slot results stay in ``buffers`` (series, device
    columns, totals).
    """
    (
        soc,
        pv1,
        load1,
        price1,
        tariff1,
        grid_charge,
        discharge_limit,
        pv_charge,
        export,
        vehicle_charge,
        starts,
        s_available,
        run_energy,
        run_slots,
        pinned,
        series,
        device,
        totals,
    ) = buffers
    max_ac = i_fields[0, inv.F_MAX_AC_CHARGE_W]
    ac_enabled = i_fields[0, inv.F_AC_TO_DC_EFFICIENCY] > 0.0 and (
        math.isnan(max_ac) or max_ac > 0.0
    )
    ac_rate = action[2]
    export_rate = action[3]
    soc[0] = state
    pv1[0] = pvs[slot]
    load1[0] = loads[slot]
    price1[0] = prices[slot]
    tariff1[0] = tariffs[slot]
    grid_charge[0, 0] = ac_rate
    # PV charging is allowed where the battery may charge at all.
    pv_charge[0, 0] = 1.0 if (ac_rate > 0.0 and ac_enabled) else action[0]
    discharge_limit[0, 0] = action[1] if (export_rate == 0.0 or tariffs[slot] > 0.0) else 0.0
    export[0, 0] = export_rate
    core.run_plan(
        0,
        soc,
        constants,
        pv1,
        load1,
        price1,
        tariff1,
        load_axis,
        load_levels,
        load_values,
        s_fields,
        s_tables,
        s_rates,
        s_available,
        1,
        i_fields,
        curve_x,
        curve_y,
        run_energy,
        run_slots,
        pinned,
        grid_charge,
        discharge_limit,
        pv_charge,
        export,
        vehicle_charge,
        starts,
        series,
        device,
        totals,
    )
    return soc[0]


@njit
def solve_tail(
    prices: np.ndarray,
    loads: np.ndarray,
    pvs: np.ndarray,
    tariffs: np.ndarray,
    states: np.ndarray,
    continuation_values: np.ndarray,
    actions: np.ndarray,
    constants: np.ndarray,
    load_axis: np.ndarray,
    load_levels: np.ndarray,
    load_values: np.ndarray,
    s_fields: np.ndarray,
    s_tables: np.ndarray,
    s_rates: np.ndarray,
    i_fields: np.ndarray,
    curve_x: np.ndarray,
    curve_y: np.ndarray,
    values_out: np.ndarray,
    operating_out: np.ndarray,
    continuation_out: np.ndarray,
    value_table: np.ndarray,
) -> None:
    """Solve the tail backwards; write values, operating and continuation part per state.

    ``continuation_values`` are the continuation credit per state at the end of the tail; the
    results hold the situation at the start of the tail (the end of the horizon).
    ``value_table`` (slots + 1 rows) keeps the value per state at the start of every slot, the
    last row being the continuation; ``replay_tail`` walks the tail forwards with it.
    """
    slots = prices.shape[0]
    count = states.shape[0]
    operating = np.zeros(count)
    continuation = continuation_values.copy()
    best = np.empty(count)
    best_operating = np.empty(count)
    best_continuation = np.empty(count)
    buffers = _scratch()
    totals = buffers[17]
    next_states = np.empty(count)
    rewards = np.empty(count)
    value_table[slots, :] = continuation_values
    for slot in range(slots - 1, -1, -1):
        for i in range(count):
            best[i] = -np.inf
            best_operating[i] = 0.0
            best_continuation[i] = 0.0
        for action in range(actions.shape[0]):
            for i in range(count):
                next_states[i] = _step(
                    buffers,
                    states[i],
                    slot,
                    prices,
                    loads,
                    pvs,
                    tariffs,
                    actions[action],
                    constants,
                    load_axis,
                    load_levels,
                    load_values,
                    s_fields,
                    s_tables,
                    s_rates,
                    i_fields,
                    curve_x,
                    curve_y,
                )
                rewards[i] = totals[core.TO_REVENUE] - totals[core.TO_COST]
            for i in range(count):
                candidate_operating = rewards[i] + interpolate(
                    states, operating, next_states[i], False
                )
                candidate_continuation = interpolate(states, continuation, next_states[i], False)
                candidate = candidate_operating + candidate_continuation
                if candidate > best[i]:
                    best[i] = candidate
                    best_operating[i] = candidate_operating
                    best_continuation[i] = candidate_continuation
        for i in range(count):
            operating[i] = best_operating[i]
            continuation[i] = best_continuation[i]
        for i in range(count):
            values_out[i] = best[i]
            value_table[slot, i] = best[i]
    for i in range(count):
        operating_out[i] = operating[i]
        continuation_out[i] = continuation[i]


# Columns of the trace ``replay_tail`` writes per slot.
RP_ACTION = 0
RP_SOC_START_WH = 1
RP_SOC_END_WH = 2
RP_REWARD = 3
RP_REMAINING = 4
RP_IMPORT = 5
RP_EXPORT = 6
RP_CHARGE = 7
RP_DISCHARGE = 8
RP_COLUMNS = 9


@njit
def replay_tail(
    prices: np.ndarray,
    loads: np.ndarray,
    pvs: np.ndarray,
    tariffs: np.ndarray,
    states: np.ndarray,
    value_table: np.ndarray,
    actions: np.ndarray,
    constants: np.ndarray,
    load_axis: np.ndarray,
    load_levels: np.ndarray,
    load_values: np.ndarray,
    s_fields: np.ndarray,
    s_tables: np.ndarray,
    s_rates: np.ndarray,
    i_fields: np.ndarray,
    curve_x: np.ndarray,
    curve_y: np.ndarray,
    start_state_wh: float,
    trace: np.ndarray,
    candidates: np.ndarray,
) -> None:
    """Walk the tail forwards from one state, choosing the action the recursion chose.

    Per slot every action is simulated by ``_step`` and valued as its reward plus the value of
    the next state in ``value_table``; the first best action wins, as in the backward
    recursion. ``trace`` gets one row per slot (``RP_*`` columns), ``candidates`` the value of
    every action per slot. Nothing here feeds back into the search.
    """
    buffers = _scratch()
    series = buffers[15]
    device = buffers[16]
    totals = buffers[17]
    state = start_state_wh
    for slot in range(prices.shape[0]):
        best = -np.inf
        for action in range(actions.shape[0]):
            next_state = _step(
                buffers,
                state,
                slot,
                prices,
                loads,
                pvs,
                tariffs,
                actions[action],
                constants,
                load_axis,
                load_levels,
                load_values,
                s_fields,
                s_tables,
                s_rates,
                i_fields,
                curve_x,
                curve_y,
            )
            reward = totals[core.TO_REVENUE] - totals[core.TO_COST]
            remaining = interpolate(states, value_table[slot + 1], next_state, False)
            candidate = reward + remaining
            candidates[slot, action] = candidate
            if candidate > best:
                best = candidate
                trace[slot, RP_ACTION] = action
                trace[slot, RP_SOC_START_WH] = state
                trace[slot, RP_SOC_END_WH] = next_state
                trace[slot, RP_REWARD] = reward
                trace[slot, RP_REMAINING] = remaining
                trace[slot, RP_IMPORT] = series[core.SR_IMPORT, 0]
                trace[slot, RP_EXPORT] = series[core.SR_FEED_IN, 0]
                trace[slot, RP_CHARGE] = device[0, core.DV_CHARGE_STORED, 0]
                trace[slot, RP_DISCHARGE] = device[0, core.DV_DISCHARGE_RAW, 0]
        state = trace[slot, RP_SOC_END_WH]


def build_tail_value_curve(
    *,
    battery: StorageParams,
    inverter: InverterParams,
    slot_duration_h: float,
    prices_per_wh: np.ndarray,
    load_wh: np.ndarray,
    pv_wh: np.ndarray,
    feed_in_per_wh: np.ndarray,
    continuation: ValueCurve,
    charge_rates: list[float],
    export_rates: list[float],
    direct_marketing: bool,
    grid_points: int = 101,
) -> ValueCurve:
    """Solve the finite tail once, backwards in time.

    Args:
        battery: The (combined) house battery.
        inverter: Its inverter.
        slot_duration_h: Slot duration [h].
        prices_per_wh: Import prices of the tail [EUR/Wh].
        load_wh: Base load of the tail, including expected appliance load [Wh].
        pv_wh: PV generation of the tail [Wh].
        feed_in_per_wh: Feed-in tariff of the tail [EUR/Wh].
        continuation: The curve valid after the tail (concave proxy).
        charge_rates: Grid charge levels the lookahead may choose.
        export_rates: Battery export levels (direct marketing only).
        direct_marketing: Whether the battery may export.
        grid_points: Number of states of the stored-energy grid.

    Returns:
        The tail value curve over usable AC energy, with operating and continuation parts.

    Raises:
        ValueError: If the series differ in length or are not finite.
    """
    arrays = [
        np.ascontiguousarray(a, dtype=float)
        for a in (prices_per_wh, load_wh, pv_wh, feed_in_per_wh)
    ]
    if len({len(a) for a in arrays}) != 1 or any(not np.isfinite(a).all() for a in arrays):
        raise ValueError("Tail forecasts must have equal lengths and contain only finite values")
    storage = (battery,)
    fields, tables, rates, _ = pack_storages(storage, slot_duration_h, 1)
    i_fields, curve_x, curve_y = pack_inverters((inverter,))
    axis, levels, values = load_table()
    capacity = battery.capacity_wh
    min_soc_wh = battery.min_soc_pct / 100 * capacity
    max_soc_wh = battery.max_soc_pct / 100 * capacity
    states = np.linspace(min_soc_wh, max_soc_wh, grid_points)
    reference = reference_dc_to_ac_efficiency(inverter, slot_duration_h)
    usable = (states - min_soc_wh) * battery.discharging_efficiency * reference
    continuation_values = np.array([continuation.value(e) for e in usable])
    actions = np.array(tail_actions(charge_rates, export_rates, direct_marketing), dtype=float)
    constants = np.array([slot_duration_h, 1.0, 1.0 if direct_marketing else 0.0, np.nan, np.nan])
    result_values = np.zeros(grid_points)
    result_operating = np.zeros(grid_points)
    result_continuation = np.zeros(grid_points)
    value_table = np.zeros((len(arrays[0]) + 1, grid_points))
    trace: Any = None
    if len(arrays[0]) == 0:
        result_values = continuation_values.copy()
        result_continuation = continuation_values.copy()
    else:
        solve_tail(
            arrays[0],
            arrays[1],
            arrays[2],
            arrays[3],
            states,
            continuation_values,
            actions,
            constants,
            axis,
            levels,
            values,
            fields,
            tables,
            rates,
            i_fields,
            curve_x,
            curve_y,
            result_values,
            result_operating,
            result_continuation,
            value_table,
        )
        trace = TailTrace(
            arrays=(arrays[0], arrays[1], arrays[2], arrays[3]),
            states=states,
            value_table=value_table,
            actions=actions,
            constants=constants,
            kernel_args=(axis, levels, values, fields, tables, rates, i_fields, curve_x, curve_y),
            capacity_wh=capacity,
            min_soc_wh=min_soc_wh,
            conversion=battery.discharging_efficiency * reference,
            slot_duration_h=slot_duration_h,
            direct_marketing=direct_marketing,
        )
    return ValueCurve(
        energy_wh=usable,
        value_eur=result_values,
        operating_eur=result_operating,
        continuation_eur=result_continuation,
        clamp_at_zero=False,
        trace=trace,
    )


@dataclass(frozen=True, eq=False)
class TailTrace:
    """What the backward recursion leaves behind so the tail path can be walked forwards.

    Attributes:
        arrays: Prices [EUR/Wh], load [Wh], PV [Wh] and feed-in tariff [EUR/Wh] of the tail.
        states: Stored-energy grid [Wh].
        value_table: Value per state at the start of every slot (last row: continuation).
        actions: The action table the recursion chose from.
        constants: Kernel constants of the one-slot problem.
        kernel_args: Load table, storage and inverter arrays in the order the kernel takes them.
        capacity_wh: Capacity of the battery [Wh].
        min_soc_wh: Lower stored-energy limit [Wh].
        conversion: Usable AC energy per Wh stored above the limit.
        slot_duration_h: Slot duration [h].
        direct_marketing: Whether the battery may export.
    """

    arrays: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]
    states: np.ndarray
    value_table: np.ndarray
    actions: np.ndarray
    constants: np.ndarray
    kernel_args: tuple[np.ndarray, ...]
    capacity_wh: float
    min_soc_wh: float
    conversion: float
    slot_duration_h: float
    direct_marketing: bool


def action_name(action: TailAction) -> str:
    """Return the name of a tail action as the former optimizer reported it."""
    dc, discharge, ac_rate, export_rate = action
    if export_rate > 0:
        return "BATTERY_EXPORT"
    if ac_rate > 0:
        return "GRID_CHARGE"
    if dc and discharge:
        return "SELF_CONSUMPTION"
    if dc:
        return "PV_CHARGE_ONLY"
    if discharge:
        return "DISCHARGE_ONLY"
    return "HOLD"


def tail_plan(
    curve: ValueCurve, energy_wh: float, control_horizon_hours: float
) -> list[TailPlanSlot]:
    """Derive the optimal battery path of the tail from the value curve, forwards.

    No new optimization: the path follows the decisions the backward recursion already valued.
    It explains the lookahead and is never a control plan.

    Args:
        curve: A lookahead curve; others carry no trace and give an empty path.
        energy_wh: Usable AC energy in the battery at the end of the control horizon [Wh].
        control_horizon_hours: Length of the control horizon [h]; the first tail slot starts here.

    Returns:
        One record per tail slot.
    """
    trace: TailTrace | None = curve.trace
    if trace is None or len(trace.arrays[0]) == 0:
        return []
    prices, loads, pvs, tariffs = trace.arrays
    slots = len(prices)
    start = trace.min_soc_wh + (energy_wh / trace.conversion if trace.conversion > 0 else 0.0)
    start = float(np.clip(start, trace.states[0], trace.states[-1]))
    steps = np.zeros((slots, RP_COLUMNS))
    candidates = np.zeros((slots, trace.actions.shape[0]))
    replay_tail(
        prices,
        loads,
        pvs,
        tariffs,
        trace.states,
        trace.value_table,
        trace.actions,
        trace.constants,
        trace.kernel_args[0],
        trace.kernel_args[1],
        trace.kernel_args[2],
        trace.kernel_args[3],
        trace.kernel_args[4],
        trace.kernel_args[5],
        trace.kernel_args[6],
        trace.kernel_args[7],
        trace.kernel_args[8],
        start,
        steps,
        candidates,
    )
    table: list[TailAction] = [
        (int(a[0]), int(a[1]), float(a[2]), float(a[3])) for a in trace.actions
    ]
    names = [action_name(action) for action in table]
    plan: list[TailPlanSlot] = []
    for slot in range(slots):
        row = steps[slot]
        chosen = int(row[RP_ACTION])
        dc, discharge, ac_rate, export_rate = table[chosen]
        best = candidates[slot, chosen]
        other = [(candidates[slot, i], i) for i in range(len(table)) if names[i] != names[chosen]]
        alternative = max(other, default=None)
        plan.append(
            TailPlanSlot(
                slot=slot,
                hour_from_start=control_horizon_hours + slot * trace.slot_duration_h,
                action=names[chosen],
                alternative_action=names[alternative[1]] if alternative else "",
                decision_margin_euro=max(best - alternative[0], 0.0) if alternative else 0.0,
                soc_start_percentage=row[RP_SOC_START_WH] / trace.capacity_wh * 100,
                soc_end_percentage=row[RP_SOC_END_WH] / trace.capacity_wh * 100,
                pv_wh=float(pvs[slot]),
                load_wh=float(loads[slot]),
                grid_import_wh=row[RP_IMPORT],
                grid_export_wh=row[RP_EXPORT],
                battery_charge_wh=row[RP_CHARGE],
                battery_discharge_wh=row[RP_DISCHARGE],
                import_price_euro_per_kwh=float(prices[slot]) * 1000,
                feed_in_tariff_euro_per_kwh=float(tariffs[slot]) * 1000,
                slot_value_euro=row[RP_REWARD],
                remaining_value_euro=row[RP_REMAINING],
                ac_charge_factor=ac_rate,
                dc_charge_allowed=dc,
                discharge_allowed=discharge,
                battery_grid_export_factor=export_rate,
            )
        )
    return plan


def tail_diagnostics(curve: ValueCurve) -> TailDiagnostics | None:
    """Summarize the forecasts of the tail (None without a lookahead)."""
    trace: TailTrace | None = curve.trace
    if trace is None or len(trace.arrays[0]) == 0:
        return None
    prices, _, _, tariffs = trace.arrays
    return TailDiagnostics(
        slots=len(prices),
        slot_hours=trace.slot_duration_h,
        soc_grid_points=len(trace.states),
        min_import_price_euro_per_kwh=float(np.min(prices)) * 1000,
        max_import_price_euro_per_kwh=float(np.max(prices)) * 1000,
        min_feed_in_tariff_euro_per_kwh=float(np.min(tariffs)) * 1000,
        max_feed_in_tariff_euro_per_kwh=float(np.max(tariffs)) * 1000,
        negative_import_price_slots=int(np.count_nonzero(prices < 0.0)),
        positive_battery_export_slots=(
            int(np.count_nonzero(tariffs > 0.0)) if trace.direct_marketing else 0
        ),
    )


__all__ = [
    "TailAction",
    "TailTrace",
    "action_name",
    "build_tail_value_curve",
    "replay_tail",
    "solve_tail",
    "tail_actions",
    "tail_diagnostics",
    "tail_plan",
]
