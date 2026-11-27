"""Objective of a plan: the cost balance plus everything an algorithm must be steered by.

The simulation (``optimization.kernel``) reports what a plan does. The objective turns that
into one number in EUR, lower is better, split into named parts:

- ``balance_eur``: import cost, storage cost and lost feed-in revenue of the horizon
  (cost minus revenue).
- ``terminal_value_eur``: credit for the energy left in the house batteries, subtracted.
- ``vehicle_target_penalty_eur``: per vehicle, the shortfall of the target state of charge at
  its deadline (or at the end) in percent points times a price per point.
- ``full_charge_penalty_eur``: per battery, the shortfall of the highest state of charge
  reached before the full charge deadline, in percent points times a price per point.
- ``ac_break_even_penalty_eur``: grid charging in slots whose energy cannot earn back its
  round-trip losses at the best future price not already covered by the energy in the
  battery: AC energy times the price gap times a factor.
- ``import_limit_penalty_eur``: energy imported above the grid connection limit times a price.
- ``comfort_penalty_eur``: per thermal device, the Kelvin hours the room temperature lies below
  the comfort minimum or above the comfort maximum times a price.
- ``protection_penalty_eur``: the Kelvin hours below the protection minimum times a (higher)
  price, on top of the comfort penalty.
- ``heat_terminal_eur``: the end value of the stored heat, see ``ThermalObjectiveSettings``.

``Objective`` is built once per planning (the terminal value curve is expensive); evaluating a
plan is cheap and compiled, for one plan, a batch of plans, and a partial run from a slot.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

import numpy as np

from akkudoktoreos.devices.models.inverter import InverterParams, reference_dc_to_ac_efficiency
from akkudoktoreos.devices.models.jit import njit
from akkudoktoreos.devices.models.storage import StorageParams
from akkudoktoreos.devices.models.thermal import ThermalParams, base_electric_w
from akkudoktoreos.optimization.kernel import core
from akkudoktoreos.optimization.kernel.problem import Plan, PlanningProblem
from akkudoktoreos.optimization.kernel.simulate import (
    SimulationResult,
    ThermalResult,
    _kernel_arguments,
    _plan_arrays,
    initial_state,
    shared_thermal_load,
    simulate,
    simulate_thermal,
)
from akkudoktoreos.optimization.tailvalue import build_tail_value_curve
from akkudoktoreos.optimization.terminalvalue import (
    ValueCurve,
    build_continuation_curve,
    combine_batteries,
    interpolate,
    trailing_window,
)

# The default price of exceeding the import limit [EUR/kWh]. It has to exceed any gain from
# price arbitrage so that the limit behaves as a hard one; the largest price on the European
# intraday markets is 9.99 EUR/kWh, so 10 EUR/kWh is above every price spread there can be.
DEFAULT_IMPORT_LIMIT_PENALTY_EUR_PER_KWH = 10.0

# Parts of the objective, in the order of the compiled result array.
P_BALANCE = 0
P_TERMINAL = 1
P_VEHICLE = 2
P_FULL_CHARGE = 3
P_BREAK_EVEN = 4
P_IMPORT_LIMIT = 5
P_TOTAL = 6
N_PARTS = 7

# Price of one Kelvin hour outside of the comfort band [EUR/Kh]. It has to exceed what the heat
# that avoids it can cost so that the band behaves as a hard one. Sustained, one Kelvin hour needs
# at most the heat that warms the building by 1 K (2.8 kWh per K for the room and its mass in the
# test plants) plus the losses, at a coefficient of performance of 2 about 1.5 kWh of electricity,
# which at the highest price of the European intraday markets (9.99 EUR/kWh) costs 15 EUR. In
# addition the heat levels are discrete: to avoid a violation of 0.01 K the plan may have to run
# a whole slot at the minimum power (0.3 kWh of heat, about 0.04 EUR at 0.3 EUR/kWh), which at 10
# EUR/Kh would be worth a violation of 0.004 Kh. 100 EUR/Kh makes such residues below 0.001 Kh
# (a few hundredths of a Kelvin for one slot) and is still small next to the sums of a plan.
DEFAULT_COMFORT_EUR_PER_KH = 100.0
# The protection minimum (frost, damp) is an order of magnitude harder than the comfort band.
DEFAULT_PROTECTION_EUR_PER_KH = 1000.0


@dataclass(frozen=True)
class TerminalValueSettings:
    """How the energy left in the batteries is valued.

    Attributes:
        mode: ``"FIXED"``: ``fixed_price_per_wh`` for each usable AC Wh. ``"AUTO"``: concave
            curve from the trailing window of the forecasts; with a tail in the problem,
            refined by a lookahead over the tail. An empty curve falls back to the fixed price.
        fixed_price_per_wh: Price of stored energy [EUR/Wh] (fixed mode and fallback).
        lookahead: Refine the concave curve by the lookahead over the tail of the problem. With
            no tail data the result is the concave curve on the state grid, valued without the
            zero clamp and without the break-even penalty, as the former optimizer did.
        window_slots: Length of the trailing window of the concave curve.
        tail_charge_rates: Grid charge levels the lookahead may choose.
        tail_export_rates: Battery export levels the lookahead may choose.
        appliance_estimate_window_wh: Expected recurring appliance load aligned with the
            trailing window (one value per window slot); None for none.
        appliance_estimate_tail_wh: Expected recurring appliance load of the tail.
    """

    mode: Literal["FIXED", "AUTO"] = "FIXED"
    fixed_price_per_wh: float = 0.0
    lookahead: bool = False
    window_slots: int = 96
    tail_charge_rates: tuple[float, ...] = (1.0,)
    tail_export_rates: tuple[float, ...] = (1.0,)
    appliance_estimate_window_wh: Optional[np.ndarray] = field(default=None, compare=False)
    appliance_estimate_tail_wh: Optional[np.ndarray] = field(default=None, compare=False)


@dataclass(frozen=True)
class ThermalObjectiveSettings:
    """Prices of the thermal devices.

    The end value of the stored heat prices the heat that the building holds less than a
    reference state at the end of the horizon (``ThermalParams.reference_temperature_c``, the
    state that holds the target temperature) with the cost of making it up: the heat deficit
    divided by the mean coefficient of performance of the last 24 hours of the horizon times the
    mean import price (of the tail forecast if the problem has one, else of the horizon). More
    heat than the reference earns nothing, as the band does not allow more use for it, and the
    value is 0 where no heating load exists (the reference then needs no heat). Without it the
    plan would let the building cool down at the end for free.

    Attributes:
        comfort_eur_per_kh: Price of a Kelvin hour below the comfort minimum or above the
            comfort maximum [EUR/Kh].
        protection_eur_per_kh: Price of a Kelvin hour below the protection minimum, in addition
            to the comfort price [EUR/Kh].
        terminal_scale: Factor of the end value of the heat; 0 switches it off, 2 doubles it.
    """

    comfort_eur_per_kh: float = DEFAULT_COMFORT_EUR_PER_KH
    protection_eur_per_kh: float = DEFAULT_PROTECTION_EUR_PER_KH
    terminal_scale: float = 1.0


@dataclass(frozen=True)
class VehicleTarget:
    """Charge target of a vehicle.

    Attributes:
        target_soc_pct: State of charge to reach [%].
        deadline_slot: Slot at whose start the target is checked, None for the end of the
            horizon. Only a target above the initial state of charge is active.
    """

    target_soc_pct: float
    deadline_slot: Optional[int] = None


@dataclass(frozen=True)
class FullChargeTarget:
    """Periodic full charge of a battery: reach ``target_soc_pct`` once before the deadline."""

    target_soc_pct: float
    deadline_slot: int


@dataclass(frozen=True)
class ObjectiveSettings:
    """Weights and targets of the objective.

    Attributes:
        terminal_value: Valuation of the energy left in the batteries.
        vehicle_targets: One target (or None) per vehicle.
        full_charge_targets: One target (or None) per battery.
        vehicle_miss_eur_per_pct: Price of one percent point of missed vehicle target.
        full_charge_miss_eur_per_pct: Price of one percent point of missed full charge.
        ac_break_even_factor: Multiplier of the break-even penalty; 0 switches it off.
        import_limit_eur_per_kwh: Price of import above the grid connection limit.
        thermal: Prices of the thermal devices.
    """

    terminal_value: TerminalValueSettings = field(default_factory=TerminalValueSettings)
    vehicle_targets: tuple[Optional[VehicleTarget], ...] = ()
    full_charge_targets: tuple[Optional[FullChargeTarget], ...] = ()
    vehicle_miss_eur_per_pct: float = 10.0
    full_charge_miss_eur_per_pct: float = 10.0
    ac_break_even_factor: float = 1.0
    import_limit_eur_per_kwh: float = DEFAULT_IMPORT_LIMIT_PENALTY_EUR_PER_KWH
    thermal: ThermalObjectiveSettings = field(default_factory=ThermalObjectiveSettings)


@dataclass(frozen=True)
class ObjectiveBreakdown:
    """The parts of the objective of one plan [EUR]; ``total_eur`` is the sum to minimize."""

    balance_eur: float
    terminal_value_eur: float
    vehicle_target_penalty_eur: float
    full_charge_penalty_eur: float
    ac_break_even_penalty_eur: float
    import_limit_penalty_eur: float
    total_eur: float
    comfort_penalty_eur: float = 0.0
    protection_penalty_eur: float = 0.0
    heat_terminal_eur: float = 0.0


@dataclass(frozen=True, eq=False)
class ObjectiveBatch:
    """The parts of the objective of many plans; each array has one entry per plan."""

    balance_eur: np.ndarray
    terminal_value_eur: np.ndarray
    vehicle_target_penalty_eur: np.ndarray
    full_charge_penalty_eur: np.ndarray
    ac_break_even_penalty_eur: np.ndarray
    import_limit_penalty_eur: np.ndarray
    total_eur: np.ndarray
    comfort_penalty_eur: np.ndarray = field(default_factory=lambda: np.zeros(0))
    protection_penalty_eur: np.ndarray = field(default_factory=lambda: np.zeros(0))
    heat_terminal_eur: np.ndarray = field(default_factory=lambda: np.zeros(0))


# Columns of the per-battery array.
B_MIN_SOC_WH = 0
B_DISCHARGING_EFFICIENCY = 1
B_REFERENCE_EFFICIENCY = 2
B_AC_TO_DC = 3
B_CHARGING_EFFICIENCY = 4
B_MAX_CHARGE_W = 5
B_LCOS_PER_WH = 6
B_BREAK_EVEN_ACTIVE = 7
B_FULL_TARGET = 8  # NaN without full charge target
B_FULL_DEADLINE = 9
B_CAPACITY_WH = 10
N_BATTERY_COLUMNS = 11

# Columns of the per-vehicle array.
V_ACTIVE = 0
V_TARGET = 1
V_DEADLINE = 2  # -1 for the end of the horizon
V_CAPACITY_WH = 3
N_VEHICLE_COLUMNS = 4


@njit
def comfort_slot_cost(
    room: float,
    low: float,
    high: float,
    protection: float,
    hours: float,
    comfort_price: float,
    protection_price: float,
) -> float:
    """Return the penalty of one room temperature held for ``hours`` [EUR].

    Below ``low`` or above ``high`` the comfort price applies per Kelvin hour; below
    ``protection`` the protection price applies in addition.
    """
    cost = 0.0
    if room < low:
        cost += (low - room) * hours * comfort_price
    if room > high:
        cost += (room - high) * hours * comfort_price
    if room < protection:
        cost += (protection - room) * hours * protection_price
    return cost


@njit
def thermal_comfort_cost(
    temperatures: np.ndarray,
    start_slot: int,
    comfort_min: np.ndarray,
    comfort_max: np.ndarray,
    protection_min: np.ndarray,
    hours: np.ndarray,
    comfort_price: float,
    protection_price: float,
) -> float:
    """Return the penalty of a room temperature trajectory [EUR].

    The temperatures are those of ``run_thermal``; the one at the start of slot ``j`` is held
    against the bounds of slot ``j`` (the last slot's for the final one) for the hours of the
    slot before, as ``comfort_hours`` measures.
    """
    slots = comfort_min.shape[0]
    total = 0.0
    for index in range(1, temperatures.shape[0]):
        slot = start_slot + index
        band = slot if slot < slots else slots - 1
        total += comfort_slot_cost(
            temperatures[index, 0],
            comfort_min[band],
            comfort_max[band],
            protection_min[band],
            hours[start_slot + index - 1],
            comfort_price,
            protection_price,
        )
    return total


@njit
def heat_shortfall_eur(
    temperature: np.ndarray,
    reference: np.ndarray,
    capacity_wh_per_k: np.ndarray,
    value_per_wh: float,
) -> float:
    """Return the end value penalty of a final state: the heat below the reference, priced [EUR]."""
    missing = 0.0
    for state in range(temperature.shape[0]):
        if temperature[state] < reference[state]:
            missing += (reference[state] - temperature[state]) * capacity_wh_per_k[state]
    return missing * value_per_wh


def heat_value_per_wh(unit: ThermalParams, problem: PlanningProblem, scale: float) -> float:
    """Return the value of one Wh of stored heat at the end of the horizon [EUR/Wh thermal].

    Mean import price (tail forecast if present, else the horizon) divided by the mean
    coefficient of performance of the last 24 hours of the horizon, times ``scale``. 0 where the
    reference state needs no heat (no heating load) or no slot can run, and never negative.
    """
    if scale == 0.0 or unit.reference_heat_w <= 0.0:
        return 0.0
    prices = (
        problem.tail_import_price_per_wh
        if len(problem.tail_import_price_per_wh)
        else problem.import_price_per_wh
    )
    last = max(unit.slots - int(round(24.0 / problem.slot_duration_h)), 0)
    cops = []
    for slot in range(last, unit.slots):
        limits = unit.heat_range_w(slot)
        if limits is None:
            continue
        middle = 0.5 * (limits[0] + limits[1])
        electric = base_electric_w(
            unit.operating_heat_w, unit.operating_electric_w, unit.operating_count, slot, middle
        )
        if electric > 0.0:
            cops.append(middle / electric)
    if not cops:
        return 0.0
    return max(float(scale * np.mean(prices) / np.mean(cops)), 0.0)


@njit
def best_uncovered_prices(prices: np.ndarray, loads: np.ndarray, free_ac_wh: float) -> np.ndarray:
    """Return per slot the best future price whose load the stored energy does not cover.

    Energy already in the battery covers the most expensive future hours first; grid charging
    only makes sense for what remains. The first hour that is not fully covered defines the
    best price a new grid charge can earn.
    """
    count = prices.shape[0]
    best = np.zeros(count)
    for hour in range(count):
        if hour + 1 >= count:
            continue
        order = np.argsort(-prices[hour + 1 : count], kind="mergesort")
        remaining = free_ac_wh
        for position in range(order.shape[0]):
            future = hour + 1 + order[position]
            if remaining >= loads[future]:
                remaining -= loads[future]
            else:
                best[hour] = prices[future]
                break
    return best


@njit
def break_even_unit_cost(
    battery_columns: np.ndarray,
    battery: int,
    charge_price: float,
    best_uncovered: float,
    slot_hours: float,
    break_even_factor: float,
) -> float:
    """Return the break-even penalty of grid charging at level 1.0 in one slot [EUR].

    The penalty is linear in the charge level: AC energy of the slot times the gap between
    the price at which the stored energy breaks even and the best future price not already
    covered, times the factor. It is 0 where the slot cannot be penalised.
    """
    if battery_columns[battery, B_BREAK_EVEN_ACTIVE] == 0.0 or charge_price <= 0.0:
        return 0.0
    round_trip = (
        battery_columns[battery, B_AC_TO_DC]
        * battery_columns[battery, B_CHARGING_EFFICIENCY]
        * battery_columns[battery, B_DISCHARGING_EFFICIENCY]
        * battery_columns[battery, B_REFERENCE_EFFICIENCY]
    )
    if round_trip <= 0.0:
        return 0.0
    break_even_price = (
        charge_price / round_trip
        + battery_columns[battery, B_LCOS_PER_WH] / battery_columns[battery, B_REFERENCE_EFFICIENCY]
    )
    if best_uncovered >= break_even_price:
        return 0.0
    dc_wh = battery_columns[battery, B_MAX_CHARGE_W] * slot_hours
    ac_wh = dc_wh / max(battery_columns[battery, B_AC_TO_DC], 1e-9)
    return ac_wh * (break_even_price - best_uncovered) * break_even_factor


@njit
def objective_parts(
    start_slot: int,
    slots: int,
    slot_h: float,
    first_slot_fraction: float,
    balance: float,
    import_excess_wh: float,
    soc_pct: np.ndarray,
    earlier_soc_pct: np.ndarray,
    final_soc_wh: np.ndarray,
    plan_grid_charge: np.ndarray,
    batteries: int,
    battery_columns: np.ndarray,
    vehicle_columns: np.ndarray,
    prices: np.ndarray,
    best_prices: np.ndarray,
    curve_x: np.ndarray,
    curve_y: np.ndarray,
    curve_clamp: bool,
    fixed_price_per_wh: float,
    break_even_factor: float,
    vehicle_miss: float,
    full_charge_miss: float,
    import_limit_eur_per_kwh: float,
    parts: np.ndarray,
) -> None:
    """Compute the parts of the objective of one simulated plan into ``parts``.

    Args:
        start_slot: First simulated slot of the result.
        slots: Number of slots of the horizon.
        slot_h: Slot duration [h].
        first_slot_fraction: Part of slot 0 still ahead.
        balance: Cost minus revenue of the simulated slots [EUR].
        import_excess_wh: Import above the connection limit [Wh].
        soc_pct: Start-of-slot state of charge ``(storages, slots - start_slot)`` [%].
        earlier_soc_pct: Highest state of charge per battery before ``start_slot`` [%], ``-inf``
            if none (a full charge reached there still counts).
        final_soc_wh: State of charge after the last slot per storage [Wh].
        plan_grid_charge: Grid charge levels of the plan ``(batteries, slots)``.
        batteries: Number of batteries (the other storages are vehicles).
        battery_columns: Per-battery parameters, see ``B_*``.
        vehicle_columns: Per-vehicle parameters, see ``V_*``.
        prices: Import prices of the horizon [EUR/Wh].
        best_prices: Best uncovered future price per slot.
        curve_x: Terminal value curve breakpoints (usable AC energy); empty for the fixed value.
        curve_y: Terminal value curve credits.
        curve_clamp: Credit 0 for energy <= 0.
        fixed_price_per_wh: Fixed value of stored energy [EUR/Wh].
        break_even_factor: Multiplier of the break-even penalty.
        vehicle_miss: Price of one percent point of missed vehicle target.
        full_charge_miss: Price of one percent point of missed full charge.
        import_limit_eur_per_kwh: Price of import above the limit.
        parts: Result array of ``N_PARTS`` entries.
    """
    length = slots - start_slot

    # Credit for the energy left in the batteries.
    credit = 0.0
    if batteries > 0:
        energy = 0.0
        for battery in range(batteries):
            energy += (
                (final_soc_wh[battery] - battery_columns[battery, B_MIN_SOC_WH])
                * battery_columns[battery, B_DISCHARGING_EFFICIENCY]
                * battery_columns[battery, B_REFERENCE_EFFICIENCY]
            )
        if curve_x.shape[0] > 0:
            credit = interpolate(curve_x, curve_y, energy, curve_clamp)
        else:
            credit = energy * fixed_price_per_wh

    # Vehicle targets.
    vehicle_penalty = 0.0
    for vehicle in range(vehicle_columns.shape[0]):
        if vehicle_columns[vehicle, V_ACTIVE] == 0.0:
            continue
        storage = batteries + vehicle
        capacity = vehicle_columns[vehicle, V_CAPACITY_WH]
        final_pct = final_soc_wh[storage] / capacity * 100 if capacity > 0.0 else 0.0
        deadline = int(vehicle_columns[vehicle, V_DEADLINE])
        reached = final_pct
        if deadline >= 0:
            index = deadline - start_slot
            if index < length:
                reached = soc_pct[storage, max(index, 0)]
        target = vehicle_columns[vehicle, V_TARGET]
        if reached < target:
            vehicle_penalty += abs(target - reached) * vehicle_miss

    # Periodic full charge: the highest state of charge before the deadline counts.
    full_penalty = 0.0
    for battery in range(batteries):
        target = battery_columns[battery, B_FULL_TARGET]
        if np.isnan(target):
            continue
        index = int(battery_columns[battery, B_FULL_DEADLINE]) - start_slot
        last = min(max(index, 0) + 1, length)
        reached = earlier_soc_pct[battery]
        for position in range(last):
            if soc_pct[battery, position] > reached:
                reached = soc_pct[battery, position]
        if index >= length:
            capacity = battery_columns[battery, B_CAPACITY_WH]
            final_pct = final_soc_wh[battery] / capacity * 100 if capacity > 0.0 else 0.0
            if final_pct > reached:
                reached = final_pct
        if reached < target:
            full_penalty += (target - reached) * full_charge_miss

    # Grid charging that cannot earn back its losses.
    break_even = 0.0
    if break_even_factor != 0.0:
        for battery in range(batteries):
            if battery_columns[battery, B_BREAK_EVEN_ACTIVE] == 0.0:
                continue
            for slot in range(start_slot, slots):
                ac_factor = plan_grid_charge[battery, slot]
                if ac_factor <= 0.0:
                    continue
                scale = first_slot_fraction if slot == 0 else 1.0
                break_even += ac_factor * break_even_unit_cost(
                    battery_columns,
                    battery,
                    prices[slot],
                    best_prices[slot],
                    slot_h * scale,
                    break_even_factor,
                )

    limit_penalty = import_excess_wh / 1000.0 * import_limit_eur_per_kwh
    parts[P_BALANCE] = balance
    parts[P_TERMINAL] = credit
    parts[P_VEHICLE] = vehicle_penalty
    parts[P_FULL_CHARGE] = full_penalty
    parts[P_BREAK_EVEN] = break_even
    parts[P_IMPORT_LIMIT] = limit_penalty
    parts[P_TOTAL] = balance - credit + vehicle_penalty + full_penalty + break_even + limit_penalty


@njit
def evaluate_plans(
    soc_start: np.ndarray,
    constants: np.ndarray,
    pv: np.ndarray,
    load: np.ndarray,
    price: np.ndarray,
    tariff: np.ndarray,
    load_axis: np.ndarray,
    load_levels: np.ndarray,
    load_values: np.ndarray,
    s_fields: np.ndarray,
    s_tables: np.ndarray,
    s_rates: np.ndarray,
    s_available: np.ndarray,
    batteries: int,
    i_fields: np.ndarray,
    curve_x_inv: np.ndarray,
    curve_y_inv: np.ndarray,
    run_energy: np.ndarray,
    run_slots: np.ndarray,
    pinned: np.ndarray,
    plans_grid_charge: np.ndarray,
    plans_discharge_limit: np.ndarray,
    plans_pv_charge: np.ndarray,
    plans_export: np.ndarray,
    plans_vehicle_charge: np.ndarray,
    plans_starts: np.ndarray,
    battery_columns: np.ndarray,
    vehicle_columns: np.ndarray,
    best_prices: np.ndarray,
    curve_x: np.ndarray,
    curve_y: np.ndarray,
    curve_clamp: bool,
    fixed_price_per_wh: float,
    break_even_factor: float,
    vehicle_miss: float,
    full_charge_miss: float,
    import_limit_eur_per_kwh: float,
    parts: np.ndarray,
) -> None:
    """Simulate many plans from slot 0 and write the parts of their objective ``(plans, N_PARTS)``."""
    plans = plans_grid_charge.shape[0]
    slots = pv.shape[0]
    storages = s_fields.shape[0]
    series = np.empty((core.N_SERIES, slots))
    device = np.empty((storages, core.N_DEVICE, slots))
    totals = np.empty(core.N_TOTALS)
    soc = np.empty(storages)
    no_history = np.full(batteries, -np.inf)
    for plan in range(plans):
        for storage in range(storages):
            soc[storage] = soc_start[storage]
        core.run_plan(
            0,
            soc,
            constants,
            pv,
            load,
            price,
            tariff,
            load_axis,
            load_levels,
            load_values,
            s_fields,
            s_tables,
            s_rates,
            s_available,
            batteries,
            i_fields,
            curve_x_inv,
            curve_y_inv,
            run_energy,
            run_slots,
            pinned,
            plans_grid_charge[plan],
            plans_discharge_limit[plan],
            plans_pv_charge[plan],
            plans_export[plan],
            plans_vehicle_charge[plan],
            plans_starts[plan],
            series,
            device,
            totals,
        )
        objective_parts(
            0,
            slots,
            constants[core.C_SLOT_H],
            constants[core.C_FIRST_SLOT_FRACTION],
            totals[core.TO_BALANCE],
            totals[core.TO_IMPORT_EXCESS],
            device[:, core.DV_SOC_PCT],
            no_history,
            soc,
            plans_grid_charge[plan],
            batteries,
            battery_columns,
            vehicle_columns,
            price,
            best_prices,
            curve_x,
            curve_y,
            curve_clamp,
            fixed_price_per_wh,
            break_even_factor,
            vehicle_miss,
            full_charge_miss,
            import_limit_eur_per_kwh,
            parts[plan],
        )


class Objective:
    """The objective of a planning problem, prepared once and evaluated cheaply per plan."""

    def __init__(self, problem: PlanningProblem, settings: Optional[ObjectiveSettings] = None):
        """Prepare the terminal value curve and the lookup tables of the penalties.

        Args:
            problem: The planning task.
            settings: Weights and targets; defaults to fixed value 0 and no targets.
        """
        self.problem = problem
        self.settings = settings or ObjectiveSettings()
        batteries = len(problem.batteries)
        vehicles = len(problem.vehicles)
        settings = self.settings
        if settings.vehicle_targets and len(settings.vehicle_targets) != vehicles:
            raise ValueError("vehicle_targets needs one entry per vehicle.")
        if settings.full_charge_targets and len(settings.full_charge_targets) != batteries:
            raise ValueError("full_charge_targets needs one entry per battery.")
        slot_h = problem.slot_duration_h
        inverter_of = {i.battery_index: i for i in problem.inverters if i.battery_index is not None}

        columns = np.zeros((batteries, N_BATTERY_COLUMNS))
        free_ac_wh = 0.0
        reference_total = 1.0
        for index, battery in enumerate(problem.batteries):
            unit = inverter_of[index]
            reference = reference_dc_to_ac_efficiency(unit, slot_h)
            min_soc_wh = battery.min_soc_pct / 100 * battery.capacity_wh
            row = columns[index]
            row[B_MIN_SOC_WH] = min_soc_wh
            row[B_DISCHARGING_EFFICIENCY] = battery.discharging_efficiency
            row[B_REFERENCE_EFFICIENCY] = reference
            row[B_AC_TO_DC] = unit.ac_to_dc_efficiency
            row[B_CHARGING_EFFICIENCY] = battery.charging_efficiency
            row[B_MAX_CHARGE_W] = battery.max_charge_power_w
            row[B_LCOS_PER_WH] = battery.lcos_per_kwh / 1000.0
            ac_possible = unit.ac_to_dc_efficiency > 0 and (
                unit.max_ac_charge_power_w is None or unit.max_ac_charge_power_w > 0
            )
            row[B_BREAK_EVEN_ACTIVE] = 1.0 if ac_possible else 0.0
            row[B_CAPACITY_WH] = battery.capacity_wh
            target = settings.full_charge_targets[index] if settings.full_charge_targets else None
            row[B_FULL_TARGET] = np.nan if target is None else target.target_soc_pct
            row[B_FULL_DEADLINE] = 0 if target is None else target.deadline_slot
            initial_wh = battery.initial_soc_pct / 100.0 * battery.capacity_wh
            free_ac_wh += (
                max(0.0, initial_wh - min_soc_wh) * battery.discharging_efficiency * reference
            )
        self._battery_columns = columns
        self._has_full_charge = bool(np.any(~np.isnan(columns[:, B_FULL_TARGET])))

        vehicle_columns = np.zeros((vehicles, N_VEHICLE_COLUMNS))
        for index, vehicle in enumerate(problem.vehicles):
            wanted = settings.vehicle_targets[index] if settings.vehicle_targets else None
            row = vehicle_columns[index]
            row[V_CAPACITY_WH] = vehicle.capacity_wh
            row[V_DEADLINE] = -1
            if wanted is not None and wanted.target_soc_pct > vehicle.initial_soc_pct:
                row[V_ACTIVE] = 1.0
                row[V_TARGET] = wanted.target_soc_pct
                if wanted.deadline_slot is not None:
                    row[V_DEADLINE] = wanted.deadline_slot
        self._vehicle_columns = vehicle_columns

        # Terminal value: one combined battery; mode and curve are decided once.
        self._curve = ValueCurve()
        tail_in_use = False
        if batteries:
            combined, combined_inverter = combine_batteries(problem.batteries, problem.inverters)
            reference_total = reference_dc_to_ac_efficiency(combined_inverter, slot_h)
            if settings.terminal_value.mode == "AUTO":
                self._curve = self._build_curve(combined, combined_inverter, reference_total)
                tail_in_use = not self._curve.clamp_at_zero and not self._curve.empty
        self._heat_value = tuple(
            heat_value_per_wh(unit, problem, settings.thermal.terminal_scale)
            for unit in problem.thermal
        )
        self._break_even_best = np.zeros(problem.slots)
        if batteries and not tail_in_use:
            self._break_even_best = best_uncovered_prices(
                problem.import_price_per_wh, problem.load_wh, free_ac_wh
            )
        else:
            columns[:, B_BREAK_EVEN_ACTIVE] = 0.0

    def _build_curve(
        self, battery: StorageParams, inverter: InverterParams, reference: float
    ) -> ValueCurve:
        """Build the concave curve, refined by the tail lookahead where the problem has a tail."""
        problem = self.problem
        terminal = self.settings.terminal_value
        tail_slots = len(problem.tail_pv_wh)
        pv_full = np.concatenate([problem.pv_wh, problem.tail_pv_wh])
        load_full = np.concatenate([problem.load_wh, problem.tail_load_wh])
        price_full = np.concatenate([problem.import_price_per_wh, problem.tail_import_price_per_wh])
        tariff_full = np.concatenate(
            [problem.feed_in_tariff_per_wh, problem.tail_feed_in_tariff_per_wh]
        )
        end_slot = problem.slots + tail_slots
        max_energy_wh = (
            max(
                battery.max_soc_pct / 100 * battery.capacity_wh
                - battery.min_soc_pct / 100 * battery.capacity_wh,
                0.0,
            )
            * battery.discharging_efficiency
            * reference
        )
        base_window = trailing_window(load_full, end_slot, terminal.window_slots)
        estimate = terminal.appliance_estimate_window_wh
        window_load = base_window + (estimate if estimate is not None else 0.0)
        continuation = build_continuation_curve(
            prices_per_wh=trailing_window(price_full, end_slot, terminal.window_slots),
            load_wh=window_load,
            pv_wh=trailing_window(pv_full, end_slot, terminal.window_slots),
            feed_in_per_wh=trailing_window(tariff_full, end_slot, terminal.window_slots),
            max_energy_wh=max_energy_wh,
            lcos_per_kwh=battery.lcos_per_kwh,
            dc_to_ac_efficiency=reference,
            grid_export_allowed=problem.direct_marketing,
        )
        if terminal.lookahead:
            tail_estimate = terminal.appliance_estimate_tail_wh
            return build_tail_value_curve(
                battery=battery,
                inverter=inverter,
                slot_duration_h=problem.slot_duration_h,
                prices_per_wh=problem.tail_import_price_per_wh,
                load_wh=problem.tail_load_wh
                + (tail_estimate if tail_estimate is not None else 0.0),
                pv_wh=problem.tail_pv_wh,
                feed_in_per_wh=problem.tail_feed_in_tariff_per_wh,
                continuation=continuation,
                charge_rates=list(terminal.tail_charge_rates),
                export_rates=list(terminal.tail_export_rates),
                direct_marketing=problem.direct_marketing,
            )
        return continuation

    @property
    def curve(self) -> ValueCurve:
        """The terminal value curve; empty for the fixed value."""
        return self._curve

    def terminal_energy_wh(self, final_soc_wh: np.ndarray) -> float:
        """Return usable battery energy at the control horizon end, in AC Wh."""
        columns = self._battery_columns
        batteries = columns.shape[0]
        if batteries == 0:
            return 0.0
        energy = 0.0
        for battery in range(batteries):
            energy += (
                (final_soc_wh[battery] - columns[battery, B_MIN_SOC_WH])
                * columns[battery, B_DISCHARGING_EFFICIENCY]
                * columns[battery, B_REFERENCE_EFFICIENCY]
            )
        return float(energy)

    def terminal_credit(self, final_soc_wh: np.ndarray) -> float:
        """Return the credit of the usable battery energy at the end of the horizon."""
        energy = self.terminal_energy_wh(final_soc_wh)
        if self._curve.energy_wh.shape[0] > 0:
            return float(
                interpolate(
                    self._curve.energy_wh, self._curve.value_eur, energy, self._curve.clamp_at_zero
                )
            )
        return float(energy * self.settings.terminal_value.fixed_price_per_wh)

    def terminal_credit_many(self, final_soc_wh: np.ndarray) -> np.ndarray:
        """Return the credit of the battery energy for many final states, as ``terminal_credit``.

        Args:
            final_soc_wh: Final states of charge of all storages, shape ``(..., storages)``.

        Returns:
            The credit [EUR] per leading index.
        """
        columns = self._battery_columns
        batteries = columns.shape[0]
        shape = final_soc_wh.shape[:-1]
        if batteries == 0:
            return np.zeros(shape)
        energy = (
            (final_soc_wh[..., :batteries] - columns[:, B_MIN_SOC_WH])
            * columns[:, B_DISCHARGING_EFFICIENCY]
            * columns[:, B_REFERENCE_EFFICIENCY]
        ).sum(axis=-1)
        if self._curve.energy_wh.shape[0] > 0:
            credit = np.interp(energy, self._curve.energy_wh, self._curve.value_eur)
            if self._curve.clamp_at_zero:
                credit = np.where(energy <= 0.0, 0.0, credit)
            return np.asarray(credit)
        return np.asarray(energy * self.settings.terminal_value.fixed_price_per_wh)

    def heat_value_per_wh(self, device: int) -> float:
        """Return the end value of one Wh of heat stored in a thermal device [EUR/Wh]."""
        return self._heat_value[device]

    def thermal_terminal_eur(self, device: int, final_temperature_c: np.ndarray) -> float:
        """Return the end value penalty of the final temperatures of a thermal device [EUR]."""
        unit = self.problem.thermal[device]
        return float(
            heat_shortfall_eur(
                np.ascontiguousarray(final_temperature_c, dtype=float),
                unit.reference_temperature_c,
                unit.capacity_wh_per_k,
                self._heat_value[device],
            )
        )

    def thermal_prices(self) -> tuple[float, float]:
        """Return the comfort and the protection price [EUR/Kh]."""
        thermal = self.settings.thermal
        return thermal.comfort_eur_per_kh, thermal.protection_eur_per_kh

    def thermal_penalties(self, thermal: ThermalResult) -> tuple[float, float, float]:
        """Return the comfort, protection and end value parts of a thermal result [EUR].

        The comfort part is the cost of the Kelvin hours below the comfort minimum and above the
        comfort maximum, the protection part the cost of those below the protection minimum.
        """
        comfort_price, protection_price = self.thermal_prices()
        comfort = 0.0
        protection = 0.0
        terminal = 0.0
        for device, unit in enumerate(self.problem.thermal):
            comfort += comfort_price * float(thermal.under_kh[device] + thermal.over_kh[device])
            protection += protection_price * float(thermal.protection_kh[device])
            terminal += self.thermal_terminal_eur(device, thermal.temperature_c[device][-1])
        return comfort, protection, terminal

    def break_even_cost_per_level(self, battery: int) -> np.ndarray:
        """Return per slot the break-even penalty of grid charging at level 1.0 [EUR].

        The penalty of a plan is the sum over its grid charge slots of level times this value.
        """
        problem = self.problem
        costs = np.zeros(problem.slots)
        for slot in range(problem.slots):
            scale = problem.first_slot_fraction if slot == 0 else 1.0
            costs[slot] = break_even_unit_cost(
                self._battery_columns,
                battery,
                problem.import_price_per_wh[slot],
                self._break_even_best[slot],
                problem.slot_duration_h * scale,
                self.settings.ac_break_even_factor,
            )
        return costs

    def full_charge_target(self, battery: int) -> Optional[tuple[float, int]]:
        """Return ``(target_pct, deadline_slot)`` of the periodic full charge of a battery, else None."""
        row = self._battery_columns[battery]
        if np.isnan(row[B_FULL_TARGET]):
            return None
        return float(row[B_FULL_TARGET]), int(row[B_FULL_DEADLINE])

    def vehicle_target(self, vehicle: int) -> Optional[tuple[float, Optional[int]]]:
        """Return ``(target_pct, deadline_slot)`` of an active vehicle target, else None.

        The deadline is None for the end of the horizon; the shortfall in percent points is
        priced with ``ObjectiveSettings.vehicle_miss_eur_per_pct``.
        """
        row = self._vehicle_columns[vehicle]
        if row[V_ACTIVE] == 0.0:
            return None
        deadline = int(row[V_DEADLINE])
        return float(row[V_TARGET]), (None if deadline < 0 else deadline)

    def _parts_arguments(self) -> tuple[Any, ...]:
        settings = self.settings
        return (
            self._battery_columns,
            self._vehicle_columns,
            self._break_even_best,
            self._curve.energy_wh,
            self._curve.value_eur,
            self._curve.clamp_at_zero,
            settings.terminal_value.fixed_price_per_wh,
            settings.ac_break_even_factor,
            settings.vehicle_miss_eur_per_pct,
            settings.full_charge_miss_eur_per_pct,
            settings.import_limit_eur_per_kwh,
        )

    def evaluate(
        self, plan: Plan, result: SimulationResult, earlier_soc_pct: Optional[np.ndarray] = None
    ) -> ObjectiveBreakdown:
        """Return the parts of the objective of a simulated plan.

        ``result`` may come from a partial run; the parts then cover the slots from its
        ``start_slot`` on (a vehicle deadline before it is checked at the first slot).
        ``earlier_soc_pct`` is the start-of-slot state of charge of the batteries before the
        partial run ``(batteries, start_slot)`` [%]: a full charge reached there still counts.
        """
        problem = self.problem
        parts = np.zeros(N_PARTS)
        batteries = len(problem.batteries)
        # A strided view, like the one of the batch path, so both share one compiled function.
        stacked = np.empty((batteries + len(problem.vehicles), 1, result.soc_pct.shape[1]))
        stacked[:batteries, 0] = result.soc_pct
        stacked[batteries:, 0] = result.vehicle_soc_pct
        soc_pct = stacked[:, 0, :]
        earlier = np.full(batteries, -np.inf)
        if earlier_soc_pct is not None and self._has_full_charge and earlier_soc_pct.size:
            earlier = earlier_soc_pct.max(axis=1)
        call: tuple[Any, ...] = (
            result.start_slot,
            problem.slots,
            problem.slot_duration_h,
            problem.first_slot_fraction,
            result.total_balance_eur,
            result.total_import_excess_wh,
            soc_pct,
            earlier,
            result.final_soc_wh,
            np.ascontiguousarray(plan.battery_grid_charge),
            batteries,
            *self._parts_arguments()[:2],
            problem.import_price_per_wh,
            *self._parts_arguments()[2:],
            parts,
        )
        objective_parts(*call)
        total = float(parts[P_TOTAL])
        comfort = protection = terminal = 0.0
        if result.thermal is not None:
            comfort, protection, terminal = self.thermal_penalties(result.thermal)
            total += comfort + protection + terminal
        return ObjectiveBreakdown(
            balance_eur=float(parts[P_BALANCE]),
            terminal_value_eur=float(parts[P_TERMINAL]),
            vehicle_target_penalty_eur=float(parts[P_VEHICLE]),
            full_charge_penalty_eur=float(parts[P_FULL_CHARGE]),
            ac_break_even_penalty_eur=float(parts[P_BREAK_EVEN]),
            import_limit_penalty_eur=float(parts[P_IMPORT_LIMIT]),
            total_eur=total,
            comfort_penalty_eur=comfort,
            protection_penalty_eur=protection,
            heat_terminal_eur=terminal,
        )

    def evaluate_batch(self, plans: list[Plan]) -> ObjectiveBatch:
        """Simulate many plans from slot 0 in one compiled call and return their objective."""
        problem = self.problem
        count = len(plans)
        batteries = len(problem.batteries)
        slots = problem.slots
        arrays = [_plan_arrays(problem, plan) for plan in plans]
        cycles = max((a[5].shape[1] for a in arrays), default=1)

        def stack(position: int, rows: int) -> np.ndarray:
            if count == 0:
                return np.zeros((0, rows, slots))
            return np.ascontiguousarray(np.stack([a[position] for a in arrays]))

        starts = np.full((count, len(problem.appliances), max(cycles, 1)), -1, dtype=np.int64)
        for position, plan_arrays in enumerate(arrays):
            starts[position, :, : plan_arrays[5].shape[1]] = plan_arrays[5]
        parts = np.zeros((count, N_PARTS))
        load = shared_thermal_load(problem, plans)
        if load is None:
            return self._evaluate_each(plans)
        arguments = _kernel_arguments(problem, load)
        battery_columns, vehicle_columns, best, curve_x, curve_y, clamp, *rest = (
            self._parts_arguments()
        )
        call: tuple[Any, ...] = (
            initial_state(problem),
            *arguments,
            stack(0, batteries),
            stack(1, batteries),
            stack(2, batteries),
            stack(3, batteries),
            stack(4, len(problem.vehicles)),
            starts,
            battery_columns,
            vehicle_columns,
            best,
            curve_x,
            curve_y,
            clamp,
            *rest,
            parts,
        )
        evaluate_plans(*call)
        total = parts[:, P_TOTAL].copy()
        comfort = np.zeros(count)
        protection = np.zeros(count)
        terminal = np.zeros(count)
        if problem.thermal and count:
            # The plans share their heat plans, so their thermal parts are equal.
            shared = self.thermal_penalties(simulate_thermal(problem, plans[0]))
            comfort[:], protection[:], terminal[:] = shared
            total += sum(shared)
        return ObjectiveBatch(
            balance_eur=parts[:, P_BALANCE].copy(),
            terminal_value_eur=parts[:, P_TERMINAL].copy(),
            vehicle_target_penalty_eur=parts[:, P_VEHICLE].copy(),
            full_charge_penalty_eur=parts[:, P_FULL_CHARGE].copy(),
            ac_break_even_penalty_eur=parts[:, P_BREAK_EVEN].copy(),
            import_limit_penalty_eur=parts[:, P_IMPORT_LIMIT].copy(),
            total_eur=total,
            comfort_penalty_eur=comfort,
            protection_penalty_eur=protection,
            heat_terminal_eur=terminal,
        )

    def _evaluate_each(self, plans: list[Plan]) -> ObjectiveBatch:
        """Evaluate plans one by one (plans whose heat plans differ need their own load)."""
        parts = []
        for plan in plans:
            parts.append(self.evaluate(plan, simulate(self.problem, plan)))

        def column(name: str) -> np.ndarray:
            return np.array([getattr(part, name) for part in parts], dtype=float)

        return ObjectiveBatch(
            balance_eur=column("balance_eur"),
            terminal_value_eur=column("terminal_value_eur"),
            vehicle_target_penalty_eur=column("vehicle_target_penalty_eur"),
            full_charge_penalty_eur=column("full_charge_penalty_eur"),
            ac_break_even_penalty_eur=column("ac_break_even_penalty_eur"),
            import_limit_penalty_eur=column("import_limit_penalty_eur"),
            total_eur=column("total_eur"),
            comfort_penalty_eur=column("comfort_penalty_eur"),
            protection_penalty_eur=column("protection_penalty_eur"),
            heat_terminal_eur=column("heat_terminal_eur"),
        )
