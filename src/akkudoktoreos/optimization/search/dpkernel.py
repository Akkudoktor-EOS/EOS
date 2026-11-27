"""Compiled planning of one storage or of a pair of storages against the rest of the house.

The slot physics is the kernel's own (``kernel.core.run_plan`` on a one-slot problem): in each
slot the other devices keep their planned controls and their states of charge of the current
plan, only the planned storages vary. The result is therefore exact for the slot at the controls it tries; it is an
approximation only in that the later states of the other devices are not followed (an effect the
guard of the search sees).

``solve_single`` runs a backward dynamic programme over a grid of states of charge and a forward
pass from the true state. ``solve_pair`` runs a forward label search over the product of two
state grids inside a corridor around the current trajectories.
"""

import math
from typing import Any

import numpy as np

from akkudoktoreos.devices.models import thermal as thm
from akkudoktoreos.devices.models.jit import njit
from akkudoktoreos.optimization.kernel import core
from akkudoktoreos.optimization.terminalvalue import interpolate

# Entries of the ``kern`` tuple.
K_CONSTANTS_FIRST = 0
K_CONSTANTS_REST = 1
K_LOAD_AXIS = 2
K_LOAD_LEVELS = 3
K_LOAD_VALUES = 4
K_S_FIELDS = 5
K_S_TABLES = 6
K_S_RATES = 7
K_I_FIELDS = 8
K_CURVE_X = 9
K_CURVE_Y = 10

# Entries of the ``one`` tuple (arrays of one slot).
O_PV = 0
O_LOAD = 1
O_PRICE = 2
O_TARIFF = 3
O_AVAILABLE = 4
O_RUN_ENERGY = 5
O_RUN_SLOTS = 6
O_PINNED = 7
O_GRID = 8
O_DISCHARGE = 9
O_PV_CHARGE = 10
O_EXPORT = 11
O_VEHICLE = 12
O_STARTS = 13
O_SERIES = 14
O_DEVICE = 15
O_TOTALS = 16


@njit
def make_one_slot_arrays(storages: int, batteries: int) -> tuple[Any, ...]:
    """Allocate the arrays of the one-slot problem (appliances are folded into the load)."""
    vehicles = storages - batteries
    return (
        np.zeros(1),
        np.zeros(1),
        np.zeros(1),
        np.zeros(1),
        np.ones((storages, 1)),
        np.zeros((0, 1)),
        np.zeros(0, dtype=np.int64),
        np.zeros((0, 1)),
        np.zeros((batteries, 1)),
        np.zeros((batteries, 1)),
        np.zeros((batteries, 1)),
        np.zeros((batteries, 1)),
        np.zeros((vehicles, 1)),
        np.zeros((0, 1), dtype=np.int64),
        np.zeros((core.N_SERIES, 1)),
        np.zeros((storages, core.N_DEVICE, 1)),
        np.zeros(core.N_TOTALS),
    )


@njit
def _load_slot(
    slot: int,
    pv: np.ndarray,
    load: np.ndarray,
    price: np.ndarray,
    tariff: np.ndarray,
    available: np.ndarray,
    plan_grid: np.ndarray,
    plan_discharge: np.ndarray,
    plan_pv: np.ndarray,
    plan_export: np.ndarray,
    plan_vehicle: np.ndarray,
    one: tuple[Any, ...],
) -> None:
    """Copy the data and the planned controls of ``slot`` into the one-slot arrays."""
    one[O_PV][0] = pv[slot]
    one[O_LOAD][0] = load[slot]
    one[O_PRICE][0] = price[slot]
    one[O_TARIFF][0] = tariff[slot]
    for storage in range(available.shape[0]):
        one[O_AVAILABLE][storage, 0] = available[storage, slot]
    for battery in range(plan_grid.shape[0]):
        one[O_GRID][battery, 0] = plan_grid[battery, slot]
        one[O_DISCHARGE][battery, 0] = plan_discharge[battery, slot]
        one[O_PV_CHARGE][battery, 0] = plan_pv[battery, slot]
        one[O_EXPORT][battery, 0] = plan_export[battery, slot]
    for vehicle in range(plan_vehicle.shape[0]):
        one[O_VEHICLE][vehicle, 0] = plan_vehicle[vehicle, slot]


@njit
def _set_action(
    one: tuple[Any, ...],
    storage: int,
    batteries: int,
    action: np.ndarray,
) -> None:
    """Put the controls of an action into the one-slot arrays for ``storage``."""
    if storage < batteries:
        one[O_GRID][storage, 0] = action[0]
        one[O_DISCHARGE][storage, 0] = action[1]
        one[O_PV_CHARGE][storage, 0] = action[2]
        one[O_EXPORT][storage, 0] = action[3]
    else:
        one[O_VEHICLE][storage - batteries, 0] = action[0]


@njit
def _run_slot(
    kern: tuple[Any, ...],
    slot: int,
    soc: np.ndarray,
    one: tuple[Any, ...],
    batteries: int,
    import_penalty_per_wh: float,
) -> float:
    """Simulate one slot; return its balance plus the penalty of the import above the limit."""
    core.run_plan(
        0,
        soc,
        kern[K_CONSTANTS_FIRST] if slot == 0 else kern[K_CONSTANTS_REST],
        one[O_PV],
        one[O_LOAD],
        one[O_PRICE],
        one[O_TARIFF],
        kern[K_LOAD_AXIS],
        kern[K_LOAD_LEVELS],
        kern[K_LOAD_VALUES],
        kern[K_S_FIELDS],
        kern[K_S_TABLES],
        kern[K_S_RATES],
        one[O_AVAILABLE],
        batteries,
        kern[K_I_FIELDS],
        kern[K_CURVE_X],
        kern[K_CURVE_Y],
        one[O_RUN_ENERGY],
        one[O_RUN_SLOTS],
        one[O_PINNED],
        one[O_GRID],
        one[O_DISCHARGE],
        one[O_PV_CHARGE],
        one[O_EXPORT],
        one[O_VEHICLE],
        one[O_STARTS],
        one[O_SERIES],
        one[O_DEVICE],
        one[O_TOTALS],
    )
    totals = one[O_TOTALS]
    return totals[core.TO_BALANCE] + totals[core.TO_IMPORT_EXCESS] * import_penalty_per_wh


@njit
def _usable(
    action: np.ndarray, state: float, low_wh: float, high_wh: float, is_vehicle: bool
) -> bool:
    """Return False for an action that cannot change anything in this state (or must not)."""
    if is_vehicle:
        return action[0] <= 0.0 or state < high_wh - 1e-6
    if state >= high_wh - 1e-6 and action[0] > 0.0:
        return False
    if state <= low_wh + 1e-6 and (action[1] > 0.0 or action[3] > 0.0):
        return False
    return True


@njit
def solve_single(
    kern: tuple[Any, ...],
    storage: int,
    batteries: int,
    pv: np.ndarray,
    load: np.ndarray,
    price: np.ndarray,
    tariff: np.ndarray,
    available: np.ndarray,
    soc_path: np.ndarray,
    plan_grid: np.ndarray,
    plan_discharge: np.ndarray,
    plan_pv: np.ndarray,
    plan_export: np.ndarray,
    plan_vehicle: np.ndarray,
    nodes: np.ndarray,
    value_end: np.ndarray,
    values: np.ndarray,
    valid: np.ndarray,
    extra: np.ndarray,
    low_wh: float,
    high_wh: float,
    deadline: int,
    penalty_target_wh: float,
    penalty_per_wh: float,
    import_penalty_per_wh: float,
    soc_start: float,
    flag_target_wh: float,
    flag_last: int,
    chosen: np.ndarray,
) -> float:
    """Plan one storage; write the chosen action per slot into ``chosen`` and return its value.

    Args:
        kern: Constant arrays of the kernel, see ``K_*``.
        storage: Index of the storage (batteries first).
        batteries: Number of batteries.
        pv: PV energy per slot [Wh].
        load: Base plus appliance load per slot [Wh].
        price: Import price per slot [EUR/Wh].
        tariff: Feed-in tariff per slot [EUR/Wh].
        available: Plug-in state per storage and slot.
        soc_path: State of charge of all storages at the start of each slot [Wh], current plan.
        plan_grid: Current plan, see ``Plan``; the rows of the planned storage are overwritten.
        plan_discharge: Current plan.
        plan_pv: Current plan.
        plan_export: Current plan.
        plan_vehicle: Current plan.
        nodes: States of charge of the grid, ascending [Wh].
        value_end: Value of each node after the last slot (negative credit plus penalties).
        values: Action table ``(slots, actions, 4)``.
        valid: Validity of the actions ``(slots, actions)``.
        extra: Cost of the actions not in the simulation ``(slots, actions)``.
        low_wh: Lowest usable state of charge [Wh].
        high_wh: Highest state of charge [Wh].
        deadline: Slot at whose start a vehicle must have reached its target; -1 for none.
        penalty_target_wh: Target energy of the vehicle [Wh].
        penalty_per_wh: Penalty of a missing Wh at the deadline [EUR].
        import_penalty_per_wh: Penalty of import above the limit [EUR/Wh].
        soc_start: State of charge at the start of the horizon [Wh].
        flag_target_wh: Full charge target of a battery [Wh], 0 for none. The penalty
            ``penalty_per_wh`` applies to the shortfall of the highest state of charge reached
            up to ``flag_last`` (as the objective counts it), tracked with the extra state
            "target reached so far". A state still short of the target at ``flag_last`` stands in
            for the highest state (never above it).
        flag_last: Last slot start (position) that counts for the full charge, at most ``slots``.
        chosen: Result ``(slots, 4)``.

    Returns:
        The value (cost to go) of the chosen path from the true start state.
    """
    slots = pv.shape[0]
    count = nodes.shape[0]
    actions = values.shape[1]
    storages = soc_path.shape[0]
    is_vehicle = storage >= batteries
    one = make_one_slot_arrays(storages, batteries)
    soc = np.zeros(storages)
    value = np.zeros((slots + 1, count))
    # Layer of the full charge not yet reached (only up to position ``flag_last``).
    flagged = flag_target_wh > 0.0 and flag_last > 0
    open_value = np.zeros((slots + 1, count))
    for i in range(count):
        value[slots, i] = value_end[i]
    if flagged and flag_last >= slots:
        for i in range(count):
            open_value[slots, i] = (
                value_end[i] + max(flag_target_wh - nodes[i], 0.0) * penalty_per_wh
            )
    for slot in range(slots - 1, -1, -1):
        # The deadline penalty applies to the state at the start of the deadline slot.
        only_idle = True
        for action in range(1, actions):
            if valid[slot, action] != 0:
                only_idle = False
                break
        if is_vehicle and only_idle:
            for i in range(count):
                value[slot, i] = value[slot + 1, i]
            if slot == deadline:
                for i in range(count):
                    value[slot, i] += max(penalty_target_wh - nodes[i], 0.0) * penalty_per_wh
            continue
        _load_slot(
            slot,
            pv,
            load,
            price,
            tariff,
            available,
            plan_grid,
            plan_discharge,
            plan_pv,
            plan_export,
            plan_vehicle,
            one,
        )
        if flagged and slot + 1 == flag_last and flag_last < slots:
            for i in range(count):
                open_value[slot + 1, i] = (
                    value[slot + 1, i] + max(flag_target_wh - nodes[i], 0.0) * penalty_per_wh
                )
        track = flagged and slot < flag_last
        for i in range(count):
            best = math.inf
            best_open = math.inf
            for action in range(actions):
                if valid[slot, action] == 0:
                    continue
                if not _usable(values[slot, action], nodes[i], low_wh, high_wh, is_vehicle):
                    continue
                for s in range(storages):
                    soc[s] = soc_path[s, slot]
                soc[storage] = nodes[i]
                _set_action(one, storage, batteries, values[slot, action])
                cost = _run_slot(kern, slot, soc, one, batteries, import_penalty_per_wh)
                total = (
                    cost
                    + extra[slot, action]
                    + interpolate(nodes, value[slot + 1], soc[storage], False)
                )
                if total < best:
                    best = total
                if track:
                    if soc[storage] >= flag_target_wh - 1e-6:
                        total_open = total
                    else:
                        total_open = (
                            cost
                            + extra[slot, action]
                            + interpolate(nodes, open_value[slot + 1], soc[storage], False)
                        )
                    if total_open < best_open:
                        best_open = total_open
            value[slot, i] = best
            if track:
                open_value[slot, i] = best_open
        if slot == deadline:
            for i in range(count):
                value[slot, i] += max(penalty_target_wh - nodes[i], 0.0) * penalty_per_wh

    # Forward pass from the true state: all actions are evaluated again at the actual state.
    state = soc_start
    total_value = 0.0
    reached = (not flagged) or soc_start >= flag_target_wh - 1e-6
    for slot in range(slots):
        _load_slot(
            slot,
            pv,
            load,
            price,
            tariff,
            available,
            plan_grid,
            plan_discharge,
            plan_pv,
            plan_export,
            plan_vehicle,
            one,
        )
        best = math.inf
        best_action = 0
        best_state = state
        best_cost = 0.0
        for action in range(actions):
            if valid[slot, action] == 0:
                continue
            if not _usable(values[slot, action], state, low_wh, high_wh, is_vehicle):
                continue
            for s in range(storages):
                soc[s] = soc_path[s, slot]
            soc[storage] = state
            _set_action(one, storage, batteries, values[slot, action])
            cost = _run_slot(kern, slot, soc, one, batteries, import_penalty_per_wh)
            cost += extra[slot, action]
            if reached or slot >= flag_last:
                total = cost + interpolate(nodes, value[slot + 1], soc[storage], False)
            elif soc[storage] >= flag_target_wh - 1e-6:
                total = cost + interpolate(nodes, value[slot + 1], soc[storage], False)
            else:
                total = cost + interpolate(nodes, open_value[slot + 1], soc[storage], False)
            if total < best:
                best = total
                best_action = action
                best_state = soc[storage]
                best_cost = cost
        for column in range(4):
            chosen[slot, column] = values[slot, best_action, column]
        state = best_state
        if state >= flag_target_wh - 1e-6:
            reached = True
        total_value += best_cost
    return total_value


@njit
def solve_pair(
    kern: tuple[Any, ...],
    storage_a: int,
    storage_b: int,
    batteries: int,
    pv: np.ndarray,
    load: np.ndarray,
    price: np.ndarray,
    tariff: np.ndarray,
    available: np.ndarray,
    soc_path: np.ndarray,
    soc_final: np.ndarray,
    plan_grid: np.ndarray,
    plan_discharge: np.ndarray,
    plan_pv: np.ndarray,
    plan_export: np.ndarray,
    plan_vehicle: np.ndarray,
    values_a: np.ndarray,
    valid_a: np.ndarray,
    extra_a: np.ndarray,
    values_b: np.ndarray,
    valid_b: np.ndarray,
    extra_b: np.ndarray,
    geometry: np.ndarray,
    corridor: int,
    deadlines: np.ndarray,
    import_penalty_per_wh: float,
    label_cost: np.ndarray,
    label_a: np.ndarray,
    label_b: np.ndarray,
    back_label: np.ndarray,
    back_action_a: np.ndarray,
    back_action_b: np.ndarray,
) -> None:
    """Search the joint plan of two storages with labels in a corridor of state cells.

    The state of a label is the exact pair of states of charge; a cell of the grid keeps the
    cheapest label. The corridor is ``corridor`` cells around the current trajectories.

    Args:
        soc_final: State of charge of all storages after the last slot of the current plan [Wh].
        geometry: ``(2, 6)`` rows per storage: low [Wh], cell width [Wh], high [Wh], target [Wh],
            penalty per missing Wh [EUR], is-vehicle flag; plus the cell of each slot start in
            ``deadlines``.
        deadlines: ``(2,)`` deadline slots of the two storages, -1 for none.
        label_cost: Result ``(slots + 1, labels)``, inf where a label does not exist.
        label_a: State of the first storage per label [Wh].
        label_b: State of the second storage per label [Wh].
        back_label: Index of the label before.
        back_action_a: Action index of the first storage that led to the label.
        back_action_b: Action index of the second storage.
    """
    slots = pv.shape[0]
    storages = soc_path.shape[0]
    width = 2 * corridor + 1
    one = make_one_slot_arrays(storages, batteries)
    soc = np.zeros(storages)
    vehicle_a = geometry[0, 5] != 0.0
    vehicle_b = geometry[1, 5] != 0.0
    for slot in range(slots + 1):
        for label in range(width * width):
            label_cost[slot, label] = math.inf
    label_cost[0, corridor * width + corridor] = 0.0
    label_a[0, corridor * width + corridor] = soc_path[storage_a, 0]
    label_b[0, corridor * width + corridor] = soc_path[storage_b, 0]
    for slot in range(slots):
        _load_slot(
            slot,
            pv,
            load,
            price,
            tariff,
            available,
            plan_grid,
            plan_discharge,
            plan_pv,
            plan_export,
            plan_vehicle,
            one,
        )
        # Cells of the next slot's current trajectory.
        if slot + 1 < slots:
            next_a = soc_path[storage_a, slot + 1]
            next_b = soc_path[storage_b, slot + 1]
        else:
            next_a = soc_final[storage_a]
            next_b = soc_final[storage_b]
        for label in range(width * width):
            base_cost = label_cost[slot, label]
            if math.isinf(base_cost):
                continue
            state_a = label_a[slot, label]
            state_b = label_b[slot, label]
            if slot == deadlines[0]:
                base_cost += max(geometry[0, 3] - state_a, 0.0) * geometry[0, 4]
            if slot == deadlines[1]:
                base_cost += max(geometry[1, 3] - state_b, 0.0) * geometry[1, 4]
            for action_a in range(values_a.shape[1]):
                if valid_a[slot, action_a] == 0 or not _usable(
                    values_a[slot, action_a], state_a, geometry[0, 0], geometry[0, 2], vehicle_a
                ):
                    continue
                for action_b in range(values_b.shape[1]):
                    if valid_b[slot, action_b] == 0 or not _usable(
                        values_b[slot, action_b],
                        state_b,
                        geometry[1, 0],
                        geometry[1, 2],
                        vehicle_b,
                    ):
                        continue
                    for s in range(storages):
                        soc[s] = soc_path[s, slot]
                    soc[storage_a] = state_a
                    soc[storage_b] = state_b
                    _set_action(one, storage_a, batteries, values_a[slot, action_a])
                    _set_action(one, storage_b, batteries, values_b[slot, action_b])
                    cost = _run_slot(kern, slot, soc, one, batteries, import_penalty_per_wh)
                    cost += base_cost + extra_a[slot, action_a] + extra_b[slot, action_b]
                    new_a = soc[storage_a]
                    new_b = soc[storage_b]
                    cell_a = int((new_a - geometry[0, 0]) / geometry[0, 1] + 0.5)
                    cell_b = int((new_b - geometry[1, 0]) / geometry[1, 1] + 0.5)
                    center_a = int((next_a - geometry[0, 0]) / geometry[0, 1] + 0.5)
                    center_b = int((next_b - geometry[1, 0]) / geometry[1, 1] + 0.5)
                    row = cell_a - center_a + corridor
                    column = cell_b - center_b + corridor
                    if row < 0 or row >= width or column < 0 or column >= width:
                        continue
                    target = row * width + column
                    if cost < label_cost[slot + 1, target]:
                        label_cost[slot + 1, target] = cost
                        label_a[slot + 1, target] = new_a
                        label_b[slot + 1, target] = new_b
                        back_label[slot + 1, target] = label
                        back_action_a[slot + 1, target] = action_a
                        back_action_b[slot + 1, target] = action_b


@njit
def thermal_cost_curve(
    kern: tuple[Any, ...],
    batteries: int,
    pv: np.ndarray,
    load: np.ndarray,
    price: np.ndarray,
    tariff: np.ndarray,
    available: np.ndarray,
    soc_path: np.ndarray,
    plan_grid: np.ndarray,
    plan_discharge: np.ndarray,
    plan_pv: np.ndarray,
    plan_export: np.ndarray,
    plan_vehicle: np.ndarray,
    data: tuple[Any, ...],
    levels: np.ndarray,
    counts: np.ndarray,
    import_penalty_per_wh: float,
    current_wh: np.ndarray,
    final_soc: np.ndarray,
) -> np.ndarray:
    """Return the exact cost of every (slot, heat level, ramp) with the consequences followed [EUR].

    ``curve[slot, level, ramp]`` is the balance plus the import limit penalty of the slots from
    ``slot`` on if the heat pump runs this level in ``slot`` and everything else stays as in the
    current plan, the other slots of the heat pump included (``current_wh`` is its electricity in
    the current plan, ``load`` the load of the house with it). The later slots are simulated from
    the state of charge the change leaves, so the energy a battery gives to the heat pump costs what
    it costs later (import, end value); the simulation stops where the states are those of the
    current plan again. ``final_soc[slot, level, ramp]`` is the state of charge after the last
    slot, for the end value of the caller. ``ramp`` 0 is a slot after one at the same run
    share (no start energy), 1 a slot after a pause, computed for the top level only (the program
    prices the start with it). ``inf`` marks the entries not computed.

    Args:
        load: Load of the house including the electricity of the heat pump in the current plan.
        levels: ``(slots, levels)`` heat levels of every slot [W], ascending.
        counts: ``(slots,)`` number of levels of every slot.
        current_wh: Electricity of the heat pump per slot in the current plan [Wh].
        final_soc: ``(slots, levels, 2, storages)`` output, state of charge after the horizon.
    """
    slots = pv.shape[0]
    storages = soc_path.shape[0]
    one = make_one_slot_arrays(storages, batteries)
    soc = np.zeros(storages)
    curve = np.full((slots, levels.shape[1], 2), math.inf)
    # The current plan: cost of each slot and the states after it.
    base_cost = np.zeros(slots)
    after = np.zeros((slots, storages))
    for storage in range(storages):
        soc[storage] = soc_path[storage, 0]
    for slot in range(slots):
        _load_slot(
            slot,
            pv,
            load,
            price,
            tariff,
            available,
            plan_grid,
            plan_discharge,
            plan_pv,
            plan_export,
            plan_vehicle,
            one,
        )
        base_cost[slot] = _run_slot(kern, slot, soc, one, batteries, import_penalty_per_wh)
        for storage in range(storages):
            after[slot, storage] = soc[storage]
    base_tail = np.zeros(slots + 1)
    for slot in range(slots - 1, -1, -1):
        base_tail[slot] = base_tail[slot + 1] + base_cost[slot]
    for slot in range(slots):
        for level in range(counts[slot]):
            heat = levels[slot, level]
            for ramp in range(2):
                if ramp == 1 and level < counts[slot] - 1:
                    continue  # only the top level needs the start (its price per unit of ramp)
                previous = 0.0 if ramp == 1 else 1.0
                energy, _ = thm.thermal_electric_wh(data, slot, heat, previous)
                for storage in range(storages):
                    soc[storage] = soc_path[storage, slot]
                _load_slot(
                    slot,
                    pv,
                    load,
                    price,
                    tariff,
                    available,
                    plan_grid,
                    plan_discharge,
                    plan_pv,
                    plan_export,
                    plan_vehicle,
                    one,
                )
                one[O_LOAD][0] = load[slot] - current_wh[slot] + energy
                total = _run_slot(kern, slot, soc, one, batteries, import_penalty_per_wh)
                follow = slot + 1
                while follow < slots:
                    same = True
                    for storage in range(storages):
                        if abs(soc[storage] - after[follow - 1, storage]) > 1e-9:
                            same = False
                    if same:
                        break
                    _load_slot(
                        follow,
                        pv,
                        load,
                        price,
                        tariff,
                        available,
                        plan_grid,
                        plan_discharge,
                        plan_pv,
                        plan_export,
                        plan_vehicle,
                        one,
                    )
                    total += _run_slot(kern, follow, soc, one, batteries, import_penalty_per_wh)
                    follow += 1
                if follow < slots:
                    total += base_tail[follow]
                    for storage in range(storages):
                        soc[storage] = after[slots - 1, storage]
                curve[slot, level, ramp] = total
                for storage in range(storages):
                    final_soc[slot, level, ramp, storage] = soc[storage]
    return curve
