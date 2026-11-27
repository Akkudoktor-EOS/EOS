"""Moves that re-plan one device or a pair of storages against the rest of the house.

A move builds the action tables and the state grid, runs the compiled planner of ``dpkernel``
(a thermal device: the linear program of ``heatplan``) and returns a candidate plan. Whether the candidate is taken is decided by the guard of the
search with the exact objective, not here.
"""

from collections.abc import Mapping
from typing import Any, Optional

import numpy as np
from loguru import logger

from akkudoktoreos.optimization.kernel.problem import Plan, PlanningProblem
from akkudoktoreos.optimization.kernel.simulate import SimulationResult
from akkudoktoreos.optimization.objective import Objective
from akkudoktoreos.optimization.search import dpkernel, heatplan
from akkudoktoreos.optimization.search.actions import (
    ActionTable,
    battery_actions,
    vehicle_actions,
)
from akkudoktoreos.optimization.search.settings import SearchSettings

# Geometry columns of a storage in a pair move.
G_LOW = 0
G_WIDTH = 1
G_HIGH = 2
G_TARGET = 3
G_PENALTY = 4
G_VEHICLE = 5


class MoveContext:
    """The constant data of the compiled planners, prepared once per problem."""

    def __init__(self, problem: PlanningProblem, objective: Objective, settings: SearchSettings):
        """Collect the kernel arrays and the one-slot constants.

        Args:
            problem: The planning task.
            objective: The objective of the task.
            settings: Control levels and effort limits.
        """
        self.problem = problem
        self.objective = objective
        self.settings = settings
        data = problem.kernel_arrays()
        constants_rest = np.array(data["constants"], dtype=float)
        constants_rest[1] = 1.0  # first_slot_fraction applies to slot 0 only
        self.kern: tuple[Any, ...] = (
            data["constants"],
            constants_rest,
            data["load_axis"],
            data["load_levels"],
            data["load_values"],
            data["storage_fields"],
            data["storage_tables"],
            data["storage_rates"],
            data["inverter_fields"],
            data["curve_x"],
            data["curve_y"],
        )
        self.available = np.ascontiguousarray(data["storage_available"])
        self.batteries = len(problem.batteries)
        self.import_penalty_per_wh = objective.settings.import_limit_eur_per_kwh / 1000.0
        self.clipped_slots = 0

    # ------------------------------------------------------------------ helpers

    def _plan_arrays(self, plan: Plan) -> tuple[np.ndarray, ...]:
        return (
            np.ascontiguousarray(plan.battery_grid_charge, dtype=float),
            np.ascontiguousarray(plan.battery_discharge_limit, dtype=float),
            np.ascontiguousarray(plan.battery_pv_charge, dtype=float),
            np.ascontiguousarray(plan.battery_export, dtype=float),
            np.ascontiguousarray(plan.vehicle_charge, dtype=float),
        )

    def _load(self, sim: SimulationResult, without_thermal: Optional[int] = None) -> np.ndarray:
        """Return the load of the house: base, appliances and the thermal devices' electricity.

        ``without_thermal`` leaves out one thermal device (the one that is being planned).
        """
        load = self.problem.load_wh + sim.appliance_wh
        if sim.thermal is not None:
            for device in range(sim.thermal.electric_wh.shape[0]):
                if device != without_thermal:
                    load = load + sim.thermal.electric_wh[device]
        return np.ascontiguousarray(load)

    def storage_range(self, storage: int, sim: SimulationResult) -> Optional[tuple[float, float]]:
        """Return the lowest and highest state of charge a storage can reach, None if it is fixed."""
        problem = self.problem
        if storage < self.batteries:
            unit = problem.batteries[storage]
            low = unit.min_soc_pct / 100.0 * unit.capacity_wh
            low = min(low, float(sim.soc_wh[storage, 0]))
        else:
            unit = problem.vehicles[storage - self.batteries]
            low = float(sim.soc_wh[storage, 0])
        high = unit.max_soc_pct / 100.0 * unit.capacity_wh
        if unit.capacity_wh <= 0.0 or high - low < 1.0:
            return None
        return low, high

    def target_penalty(self, storage: int) -> tuple[int, float, float]:
        """Return ``(deadline, target_wh, penalty_per_wh)`` of the target of a storage.

        A vehicle's charge target; for a battery the periodic full charge, whose "highest state of
        charge before the deadline" is approximated by the state at the deadline. The deadline is
        -1 for the end of the horizon, and the penalty per Wh is 0 where there is no target.
        """
        miss = self.objective.settings
        if storage < self.batteries:
            full = self.objective.full_charge_target(storage)
            capacity = self.problem.batteries[storage].capacity_wh
            if full is None or capacity <= 0.0:
                return -1, 0.0, 0.0
            pct, deadline = full
            return (
                deadline,
                pct / 100.0 * capacity,
                miss.full_charge_miss_eur_per_pct * 100.0 / capacity,
            )
        index = storage - self.batteries
        target = self.objective.vehicle_target(index)
        capacity = self.problem.vehicles[index].capacity_wh
        if target is None or capacity <= 0.0:
            return -1, 0.0, 0.0
        target_pct, vehicle_deadline = target
        per_wh = miss.vehicle_miss_eur_per_pct * 100.0 / capacity
        return (
            -1 if vehicle_deadline is None else int(vehicle_deadline),
            target_pct / 100.0 * capacity,
            per_wh,
        )

    def actions(self, plan: Plan, storage: int, thinned: bool) -> ActionTable:
        """Return the action table of a storage."""
        count = self.settings.pair_levels if thinned else None
        if storage < self.batteries:
            return battery_actions(
                self.problem, self.objective, self.settings, plan, storage, count
            )
        return vehicle_actions(self.problem, plan, storage - self.batteries, count)

    def terminal_energy(self, final_soc_wh: np.ndarray, states: dict[int, float]) -> float:
        """Return the negative terminal credit of the final states, with ``states`` replaced."""
        final = np.array(final_soc_wh, dtype=float)
        for storage, value in states.items():
            final[storage] = value
        return -self.objective.terminal_credit(final)

    # ------------------------------------------------------------------ single storage

    def plan_single(self, plan: Plan, sim: SimulationResult, storage: int) -> Optional[Plan]:
        """Re-plan one storage by dynamic programming over its state of charge.

        Returns the candidate plan, or None if the storage cannot change anything.
        """
        bounds = self.storage_range(storage, sim)
        if bounds is None:
            return None
        low, high = bounds
        settings = self.settings
        deadline, target_wh, per_wh = self.target_penalty(storage)
        slots = self.problem.slots
        # The full charge of a battery is counted as the objective counts it: the highest state
        # of charge up to the deadline, with the extra state "target reached so far".
        flag_target_wh, flag_last = 0.0, 0
        if storage < self.batteries and target_wh > 0.0 and per_wh > 0.0:
            flag_target_wh = target_wh
            flag_last = slots if deadline < 0 or deadline >= slots else deadline
            deadline, target_wh = -1, 0.0
        table = self.actions(plan, storage, thinned=False)
        effort = settings.dp_transitions // max(int(table.valid.sum()), 1)
        count = int(min(max(effort, settings.soc_nodes), settings.soc_nodes_max))
        nodes = np.linspace(low, high, count)
        if deadline != -1 and low < target_wh < high:
            nodes = np.unique(np.append(nodes, target_wh))
        if flag_target_wh > 0.0 and low < flag_target_wh < high:
            nodes = np.unique(np.append(nodes, flag_target_wh))
        value_end = np.zeros(nodes.shape[0])
        for index, node in enumerate(nodes):
            if storage < self.batteries:
                value_end[index] = self.terminal_energy(sim.final_soc_wh, {storage: float(node)})
            if target_wh > 0.0 and (deadline == -1 or deadline >= slots):
                value_end[index] += max(target_wh - node, 0.0) * per_wh
        # A deadline at or after the end of the horizon is a penalty of the final state.
        dp_deadline = deadline if 0 < deadline < slots else -1
        grid, discharge, pv_charge, export, vehicle = self._plan_arrays(plan)
        chosen = np.zeros((slots, 4))
        dpkernel.solve_single(
            self.kern,
            storage,
            self.batteries,
            self.problem.pv_wh,
            self._load(sim),
            self.problem.import_price_per_wh,
            self.problem.feed_in_tariff_per_wh,
            self.available,
            np.ascontiguousarray(sim.soc_wh),
            grid,
            discharge,
            pv_charge,
            export,
            vehicle,
            nodes,
            value_end,
            table.values,
            table.valid,
            table.extra,
            low,
            high,
            dp_deadline,
            target_wh,
            per_wh,
            self.import_penalty_per_wh,
            float(sim.soc_wh[storage, 0]),
            flag_target_wh,
            flag_last,
            chosen,
        )
        return self._with_rows(plan, {storage: chosen})

    # ------------------------------------------------------------------ thermal device

    def plan_thermal(
        self,
        plan: Plan,
        sim: SimulationResult,
        device: int,
        time_limit_s: Optional[float] = None,
    ) -> Optional[Plan]:
        """Re-plan a thermal device as the exact solution of a linear program.

        The cost curve of the house at a dozen heat levels is computed against the current plans
        of the other devices (once per slot and level, the start at the top level), with the
        consequences of the changed load followed to the end of the horizon, and ``heatplan.plan_heat`` solves
        the heat against it with the dynamics of the building, the comfort band and the end
        value of the heat. Whether the candidate is taken is decided by the guard of the search.
        ``time_limit_s`` bounds the solver (the rest of the time budget of the search).
        ``clipped_slots`` is set to the number of slots the cut back to the electric power
        limit changed.

        Returns:
            The candidate plan, or None if the device can never run, the simulation has no
            thermal result or the program fails.
        """
        problem = self.problem
        unit = problem.thermal[device]
        levels, counts = heatplan.heat_levels(unit)
        grid, discharge, pv_charge, export, vehicle = self._plan_arrays(plan)
        soc_path = np.ascontiguousarray(sim.soc_wh)
        if sim.thermal is None:  # no thermal result to plan against: no candidate
            logger.warning("The heat plan of device {} has no simulation to plan against.", device)
            return None
        final_soc = np.zeros((problem.slots, levels.shape[1], 2, soc_path.shape[0]))
        curve = dpkernel.thermal_cost_curve(
            self.kern,
            self.batteries,
            problem.pv_wh,
            self._load(sim),
            problem.import_price_per_wh,
            problem.feed_in_tariff_per_wh,
            self.available,
            soc_path,
            grid,
            discharge,
            pv_charge,
            export,
            vehicle,
            unit.arrays(),
            levels,
            counts,
            self.import_penalty_per_wh,
            np.ascontiguousarray(sim.thermal.electric_wh[device]),
            final_soc,
        )
        self._add_end_value(curve, final_soc)
        comfort_price, protection_price = self.objective.thermal_prices()
        found = heatplan.plan_heat(
            unit,
            levels,
            counts,
            curve,
            comfort_price=comfort_price,
            protection_price=protection_price,
            heat_value_per_wh=self.objective.heat_value_per_wh(device),
            time_limit_s=time_limit_s,
        )
        if found is None:
            return None
        heat, self.clipped_slots = found
        if unit.unsupported_heat(heat).any():  # the simulation cannot take it: no candidate
            logger.warning("The heat plan of device {} is not finite or above the table.", device)
            return None
        candidate = plan.copy()
        candidate.thermal_heat_w[device] = heat
        return candidate

    def _add_end_value(self, curve: np.ndarray, final_soc: np.ndarray) -> None:
        """Add the end value of the battery energy to the computed entries of a heat cost curve."""
        computed = np.isfinite(curve)
        curve[computed] -= self.objective.terminal_credit_many(final_soc[computed])

    # ------------------------------------------------------------------ pair of storages

    def plan_pair(
        self, plan: Plan, sim: SimulationResult, first: int, second: int
    ) -> Optional[Plan]:
        """Re-plan two storages together with labels on the product of their state grids."""
        ranges = [self.storage_range(first, sim), self.storage_range(second, sim)]
        if ranges[0] is None or ranges[1] is None:
            return None
        settings = self.settings
        slots = self.problem.slots
        members = (first, second)
        geometry = np.zeros((2, 6))
        deadlines = np.full(2, -1, dtype=np.int64)
        final_penalty = [False, False]  # the vehicle's target is checked after the last slot
        for row, storage in enumerate(members):
            low, high = ranges[row]  # type: ignore[misc]
            geometry[row, G_LOW] = low
            geometry[row, G_HIGH] = high
            geometry[row, G_WIDTH] = (high - low) / settings.pair_cells
            if storage >= self.batteries:
                deadline, target_wh, per_wh = self.target_penalty(storage)
                geometry[row, G_VEHICLE] = 1.0
                geometry[row, G_TARGET] = target_wh
                geometry[row, G_PENALTY] = per_wh
                deadlines[row] = deadline if 0 < deadline < slots else -1
                final_penalty[row] = target_wh > 0.0 and (deadline == -1 or deadline >= slots)
        tables = [self.actions(plan, storage, thinned=True) for storage in members]
        grid, discharge, pv_charge, export, vehicle = self._plan_arrays(plan)
        width = 2 * settings.pair_corridor + 1
        labels = width * width
        cost = np.full((slots + 1, labels), np.inf)
        state_a = np.zeros((slots + 1, labels))
        state_b = np.zeros((slots + 1, labels))
        back_label = np.zeros((slots + 1, labels), dtype=np.int64)
        back_a = np.zeros((slots + 1, labels), dtype=np.int64)
        back_b = np.zeros((slots + 1, labels), dtype=np.int64)
        dpkernel.solve_pair(
            self.kern,
            first,
            second,
            self.batteries,
            self.problem.pv_wh,
            self._load(sim),
            self.problem.import_price_per_wh,
            self.problem.feed_in_tariff_per_wh,
            self.available,
            np.ascontiguousarray(sim.soc_wh),
            np.ascontiguousarray(sim.final_soc_wh),
            grid,
            discharge,
            pv_charge,
            export,
            vehicle,
            tables[0].values,
            tables[0].valid,
            tables[0].extra,
            tables[1].values,
            tables[1].valid,
            tables[1].extra,
            geometry,
            settings.pair_corridor,
            deadlines,
            self.import_penalty_per_wh,
            cost,
            state_a,
            state_b,
            back_label,
            back_a,
            back_b,
        )
        best_label, best_total = -1, np.inf
        for label in range(labels):
            if not np.isfinite(cost[slots, label]):
                continue
            finals = {first: float(state_a[slots, label]), second: float(state_b[slots, label])}
            total = cost[slots, label] + self.terminal_energy(sim.final_soc_wh, finals)
            for row, storage in enumerate(members):
                if final_penalty[row]:
                    missing = max(geometry[row, G_TARGET] - finals[storage], 0.0)
                    total += missing * geometry[row, G_PENALTY]
            if total < best_total:
                best_total, best_label = total, label
        if best_label < 0:
            return None
        rows = {first: np.zeros((slots, 4)), second: np.zeros((slots, 4))}
        label = best_label
        for slot in range(slots, 0, -1):
            rows[first][slot - 1] = tables[0].values[slot - 1, back_a[slot, label]]
            rows[second][slot - 1] = tables[1].values[slot - 1, back_b[slot, label]]
            label = int(back_label[slot, label])
        return self._with_rows(plan, rows)

    # ------------------------------------------------------------------ candidate plans

    def _with_rows(self, plan: Plan, rows: Mapping[int, np.ndarray]) -> Plan:
        """Return a copy of ``plan`` with the chosen rows of the given storages."""
        candidate = plan.copy()
        for storage, chosen in rows.items():
            if storage < self.batteries:
                candidate.battery_grid_charge[storage] = chosen[:, 0]
                candidate.battery_discharge_limit[storage] = chosen[:, 1]
                candidate.battery_pv_charge[storage] = chosen[:, 2]
                candidate.battery_export[storage] = chosen[:, 3]
            else:
                candidate.vehicle_charge[storage - self.batteries] = chosen[:, 0]
        return candidate
