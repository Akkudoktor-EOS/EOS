"""Start plans: empty, and the previous plan moved forward in time (rolling planning)."""

from typing import Optional

import numpy as np

from akkudoktoreos.optimization.kernel.problem import Plan, PlanningProblem
from akkudoktoreos.optimization.kernel.simulate import simulate
from akkudoktoreos.optimization.objective import Objective
from akkudoktoreos.optimization.search.actions import ac_charge_possible, positive_levels
from akkudoktoreos.optimization.search.checks import repair_plan
from akkudoktoreos.optimization.search.settings import SearchSettings, SearchSpace


def empty_plan(
    problem: PlanningProblem,
    space: Optional[SearchSpace] = None,
    settings: Optional[SearchSettings] = None,
) -> Plan:
    """Return the plan without any decision: batteries discharge and charge from PV, nothing else.

    Each appliance cycle starts in the earliest allowed slot.
    """
    return repair_plan(problem, Plan.idle(problem), space, settings)


def shift_plan(
    problem: PlanningProblem,
    previous: Plan,
    shift: int,
    space: Optional[SearchSpace] = None,
    settings: Optional[SearchSettings] = None,
) -> Plan:
    """Move a previous plan ``shift`` slots into the past and fill the end.

    Slot ``shift`` of the previous plan becomes slot 0; slots after its end take the values of
    the empty plan. Appliance cycles that had started before ``shift`` are over (their running
    remainder is the ``pinned_load`` of the new problem); the others keep their start, moved by
    ``shift`` and then repaired onto the allowed slots of the new problem.

    Args:
        problem: The new planning task (same slot length, horizon starting ``shift`` later).
        previous: The plan of the previous planning.
        shift: Number of slots between the two planning times.
        space: Appliance start slots of the new task.
        settings: Control levels.
    """
    if shift < 0:
        raise ValueError("shift must not be negative.")
    plan = Plan.idle(problem)
    slots = problem.slots
    previous_slots = max(
        (
            getattr(previous, name).shape[1]
            for name in (
                "battery_grid_charge",
                "vehicle_charge",
                "thermal_heat_w",
            )
            if getattr(previous, name).size
        ),
        default=0,
    )
    keep = max(min(slots, previous_slots - shift), 0)
    if keep > 0:
        for name in (
            "battery_grid_charge",
            "battery_discharge_limit",
            "battery_pv_charge",
            "battery_export",
            "vehicle_charge",
            "thermal_heat_w",
        ):
            target = getattr(plan, name)
            source = np.asarray(getattr(previous, name))
            if target.shape[0] == source.shape[0]:
                target[:, :keep] = source[:, shift : shift + keep]
    for appliance in range(min(plan.appliance_starts.shape[0], previous.appliance_starts.shape[0])):
        remaining = [int(s) - shift for s in previous.appliance_starts[appliance] if s >= shift]
        for cycle, start in enumerate(remaining[: plan.appliance_starts.shape[1]]):
            plan.appliance_starts[appliance, cycle] = start
    return repair_plan(problem, plan, space, settings)


def vehicle_rule_plan(
    problem: PlanningProblem,
    objective: Objective,
    space: Optional[SearchSpace] = None,
    settings: Optional[SearchSettings] = None,
    noise: float = 0.0,
    seed: int = 0,
) -> Optional[Plan]:
    """Return the empty plan with every vehicle charging in its cheapest slots, or None.

    A vehicle with a charge target takes its highest charge level in the slots with the lowest
    import price (while plugged in and before the deadline) until the target energy is covered.
    This is a second kind of start for the search: the single device planning of a vehicle
    against a battery plan that discharges into the vehicle sees charging as nearly free and
    spreads it thinly; a start with the cheap, concentrated charge ends in another local optimum.
    None if no vehicle has a target that can be reached.
    """
    plan = empty_plan(problem, space, settings)
    batteries = len(problem.batteries)
    available = np.asarray(problem.kernel_arrays()["storage_available"])
    price = np.asarray(problem.import_price_per_wh, dtype=float)
    any_rule = False
    rng = np.random.default_rng(seed)
    for index, unit in enumerate(problem.vehicles):
        wanted = objective.vehicle_target(index)
        rates = sorted(rate for rate in unit.charge_rates if rate > 0.0)
        if wanted is None or not rates:
            continue
        target_pct, deadline = wanted
        need_wh = max(target_pct - unit.initial_soc_pct, 0.0) / 100.0 * unit.capacity_wh
        slots = np.nonzero(available[batteries + index] > 0.0)[0]
        if deadline is not None:
            slots = slots[slots <= deadline]
        top = rates[-1]
        order_price = (
            price * (1.0 + noise * rng.standard_normal(price.shape[0])) if noise else price
        )
        per_slot_wh = (
            top * unit.max_charge_power_w * problem.slot_duration_h * unit.charging_efficiency
        )
        if need_wh <= 0.0 or per_slot_wh <= 0.0 or not slots.size:
            continue
        for slot in slots[np.argsort(order_price[slots], kind="stable")]:
            if need_wh <= 0.0:
                break
            level = (
                top
                if need_wh >= per_slot_wh
                else min(r for r in rates if r * per_slot_wh / top >= need_wh)
            )
            plan.vehicle_charge[index, slot] = level
            need_wh -= level * per_slot_wh / top
            any_rule = True
    return repair_plan(problem, plan, space, settings) if any_rule else None


def secure_full_charge(
    problem: PlanningProblem,
    objective: Objective,
    plan: Plan,
    space: Optional[SearchSpace] = None,
    settings: Optional[SearchSettings] = None,
    candidate_slots: int = 16,
    rounds: int = 12,
) -> Plan:
    """Return ``plan`` with every reachable full charge target of a battery reached.

    A hard target (the battery must have been full once before its deadline) is worth far more
    than any saving, but reaching the exact target is narrow: a charge level that does not fit
    into the room left is dropped, so only some combinations of levels (and the PV surplus,
    which fills what fits) end exactly at full. A start plan that misses the target can leave
    the search without a move that finds such a combination. Here the plan is simulated and, one
    grid charge at a time (in the cheapest slots before the deadline, at every level), the one
    that brings the highest state of charge up to the deadline (then the state at its end) the
    highest is added (the battery then holds its charge from that slot to the deadline),
    until the target is reached or no charge helps. The search then optimizes the cost from a
    plan that satisfies the target. The plan is returned unchanged if nothing is to be done.
    """
    slots = problem.slots
    secured = plan
    price = np.asarray(problem.import_price_per_wh, dtype=float)
    for battery in range(len(problem.batteries)):
        target = objective.full_charge_target(battery)
        if target is None or not ac_charge_possible(problem, battery):
            continue
        target_pct, deadline = target
        last = min(deadline, slots)
        levels = positive_levels(problem.batteries[battery].charge_rates)

        def reach(candidate: Plan, battery: int = battery, last: int = last) -> tuple[float, float]:
            """Highest state of charge up to the deadline, and the state at its end."""
            result = simulate(problem, candidate)
            states = result.soc_pct[battery][: last + 1]
            highest, end = float(states.max()), float(states[-1])
            if deadline >= slots:  # the state after the last slot counts as well
                capacity = problem.batteries[battery].capacity_wh
                end = float(result.final_soc_wh[battery]) / capacity * 100.0
                highest = max(highest, end)
            return highest, end

        current = reach(secured)
        for _ in range(rounds):
            if current[0] >= target_pct - 1e-6:
                break
            free = [
                slot
                for slot in np.argsort(price[:last], kind="stable")
                if secured.battery_grid_charge[battery, slot] == 0.0
            ][:candidate_slots]
            best: Optional[tuple[tuple[float, float], Plan]] = None
            for slot in free:
                for level in levels:
                    trial = secured.copy()
                    trial.battery_grid_charge[battery, slot] = level
                    # The battery holds its charge from here to the deadline.
                    trial.battery_discharge_limit[battery, slot:last] = 0.0
                    trial.battery_export[battery, slot:last] = 0.0
                    trial.battery_pv_charge[battery, slot] = 1.0
                    value = reach(trial)
                    if best is None or value > best[0]:
                        best = (value, trial)
            if best is None or best[0] <= current:
                break
            current, secured = best
    return repair_plan(problem, secured, space, settings) if secured is not plan else plan
