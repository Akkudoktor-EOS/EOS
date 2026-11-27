"""Device by device search: passes over the devices, pair moves, guard, start plans, events.

The search starts from a plan and improves it by moves. A move re-plans one device (or a pair)
against the plans of the others. The plan of a move is built with an approximate evaluation
(``moves``), but it is taken only if the exact objective of the whole plan falls: that is the
guard. The objective of the current plan therefore never rises, and the result is never worse
than the start plan. Everything is deterministic.
"""

import itertools
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Optional, Sequence

import numpy as np
from loguru import logger

from akkudoktoreos.optimization.kernel.problem import Plan, PlanningProblem
from akkudoktoreos.optimization.kernel.simulate import SimulationResult, simulate
from akkudoktoreos.optimization.objective import Objective, ObjectiveBreakdown
from akkudoktoreos.optimization.search import appliances
from akkudoktoreos.optimization.search.actions import normalize_discharge
from akkudoktoreos.optimization.search.checks import Violation, check_plan, repair_plan
from akkudoktoreos.optimization.search.moves import MoveContext
from akkudoktoreos.optimization.search.settings import SearchSettings, SearchSpace
from akkudoktoreos.optimization.search.warmstart import (
    empty_plan,
    secure_full_charge,
    vehicle_rule_plan,
)

STOP_CONVERGED = "converged"
STOP_MAX_PASSES = "max_passes"
STOP_TIME = "time"


@dataclass(frozen=True)
class DeviceRef:
    """A device of the problem.

    Attributes:
        kind: ``"battery"``, ``"vehicle"``, ``"thermal"`` or ``"appliance"``.
        index: Index in the respective list of the problem.
    """

    kind: str
    index: int


@dataclass
class SearchStats:
    """What a search did.

    Attributes:
        passes: Passes over the devices, summed over the starts.
        accepted: Accepted moves by kind (``battery``, ``vehicle``, ``thermal``, ``appliance``,
            ``pair_*``).
        rejected: Moves the guard refused, by kind.
        unchanged: Moves that returned the current plan, by kind (``thermal_failed``: the heat
            plan could not be computed, see ``heatplan.plan_heat``; the held plan stays).
        pruned: Rejected moves whose first changed slot already looked worse than the current
            plan in the partial simulation, by kind (the exact total was not computed).
        clipped_slots: Slots in which the cut back of a heat plan to the electric power limit
            changed the heat the linear program proposed.
        excess_eur: Sum over the rejected moves that were simulated completely of how far their
            exact objective lay above the held plan [EUR], by kind.
        excess_max_eur: Largest such excess, by kind.
        seconds: Time by phase: ``start``, ``battery``, ``vehicle``, ``thermal``,
            ``appliance``, ``pair``, ``guard``, ``event``, ``total``.
        full_simulations: Simulations of a whole plan.
        partial_simulations: Simulations from the first changed slot on.
        stop: Why the search of the best start ended: ``converged``, ``max_passes`` or ``time``.
        starts: Number of starts (orders of the devices) that were searched.
        winner: ``"warm"`` if the plan comes from the given start plan, ``"cold"`` if it comes
            from a search from scratch (the only value without a start plan).
    """

    passes: int = 0
    accepted: dict[str, int] = field(default_factory=dict)
    rejected: dict[str, int] = field(default_factory=dict)
    unchanged: dict[str, int] = field(default_factory=dict)
    pruned: dict[str, int] = field(default_factory=dict)
    excess_eur: dict[str, float] = field(default_factory=dict)
    excess_max_eur: dict[str, float] = field(default_factory=dict)
    clipped_slots: int = 0
    seconds: dict[str, float] = field(default_factory=dict)
    full_simulations: int = 0
    partial_simulations: int = 0
    stop: str = STOP_CONVERGED
    starts: int = 1
    winner: str = "cold"


@dataclass(frozen=True, eq=False)
class SearchResult:
    """The outcome of a search.

    Attributes:
        plan: The best plan found.
        objective: Its objective with all parts.
        start_objective: Objective of the start plan (after mapping onto the allowed values).
        stats: Effort and acceptance statistics.
        trace: Objective after the start and after every accepted move (never rising).
        violations: Violations of the allowed values in ``plan`` (empty by construction).
    """

    plan: Plan
    objective: ObjectiveBreakdown
    start_objective: ObjectiveBreakdown
    stats: SearchStats
    trace: list[float]
    violations: list[Violation]


def _first_changed_slot(old: Plan, new: Plan) -> Optional[int]:
    """Return the first slot in which two plans differ, None if they are equal."""
    width = max(
        getattr(old, name).shape[1]
        for name in ("battery_grid_charge", "vehicle_charge", "thermal_heat_w")
        if getattr(old, name).ndim == 2
    )
    changed = np.zeros(width, dtype=bool)
    for name in (
        "battery_grid_charge",
        "battery_discharge_limit",
        "battery_pv_charge",
        "battery_export",
        "vehicle_charge",
        "thermal_heat_w",
    ):
        before, after = getattr(old, name), getattr(new, name)
        if before.size:
            changed |= np.any(before != after, axis=0)
    first: Optional[int] = int(np.argmax(changed)) if changed.any() else None
    for row in np.argwhere(old.appliance_starts != new.appliance_starts):
        starts = [
            int(s)
            for s in (old.appliance_starts[tuple(row)], new.appliance_starts[tuple(row)])
            if s >= 0
        ]
        if starts:
            first = min(starts) if first is None else min(first, min(starts))
        elif first is None:
            first = 0
    return first


@dataclass
class _Outcome:
    """The result of the search of one start."""

    plan: Plan
    sim: SimulationResult
    breakdown: ObjectiveBreakdown
    start: ObjectiveBreakdown
    trace: list[float]
    stats: SearchStats


class DeviceSearch:
    """The search of one planning problem."""

    def __init__(
        self,
        problem: PlanningProblem,
        objective: Objective,
        space: Optional[SearchSpace] = None,
        settings: Optional[SearchSettings] = None,
    ):
        """Prepare the search.

        Args:
            problem: The planning task.
            objective: The objective built for the problem.
            space: Appliance start slots; default: anywhere.
            settings: Control levels and effort limits.
        """
        if objective.problem is not problem:
            raise ValueError("The objective belongs to another problem.")
        self.problem = problem
        self.objective = objective
        self.space = space or SearchSpace.default(problem)
        self.settings = settings or SearchSettings()
        if len(self.space.appliances) != len(problem.appliances):
            raise ValueError("The search space needs one schedule per appliance.")
        self._moves = MoveContext(problem, objective, self.settings)
        self._plan: Plan
        self._sim: SimulationResult
        self._breakdown: ObjectiveBreakdown
        self._version = 0
        self._tried: dict[tuple[str, int, int], int] = {}
        self._trace: list[float] = []
        self._stats = SearchStats()
        self._deadline: Optional[float] = None
        self._order_name = "batteries_first"

    # ------------------------------------------------------------------ public

    def solve(
        self,
        start: Optional[Plan] = None,
        *,
        affected: Sequence[DeviceRef] = (),
    ) -> SearchResult:
        """Search for a better plan.

        Args:
            start: Start plan; default the empty plan. A plan with values outside the allowed
                set is first mapped onto it. Pass the previous plan moved forward by
                ``shift_plan`` for a rolling planning.
            affected: Devices an event concerns (e.g. a vehicle that leaves earlier): they are
                re-planned first, with their pair moves, before the normal passes run.

        Returns:
            The best plan, its objective, the statistics and the trace.
        """
        clock = time.perf_counter()
        settings = self.settings
        deadline = None if settings.time_budget_s is None else clock + settings.time_budget_s
        warm = None if start is None else repair_plan(self.problem, start, self.space, settings)
        # Every start is an independent search (the compiled planners release the interpreter
        # lock), so they run in parallel threads; the result does not depend on the timing.
        # From scratch every order of the devices is a start of its own and the best result
        # counts. A given plan (warm start, event) is one more start next to the full set of
        # cold starts, unless ``warm_start_compare`` is off: then it is the only one.
        orders = settings.start_orders
        starts: list[tuple[Plan, str, Sequence[DeviceRef]]] = []
        if warm is not None:
            if settings.secure_targets:
                warm = secure_full_charge(self.problem, self.objective, warm, self.space, settings)
            starts.append((warm, orders[0], affected))
        cold: list[tuple[Plan, str, Sequence[DeviceRef]]] = []
        if warm is None or settings.warm_start_compare:
            cold_affected = affected if warm is None else ()
            plan = empty_plan(self.problem, self.space, settings)
            if settings.secure_targets:
                plan = secure_full_charge(self.problem, self.objective, plan, self.space, settings)
            cold = [(plan, order, cold_affected) for order in orders]
            if settings.rule_starts:
                rule = vehicle_rule_plan(self.problem, self.objective, self.space, settings)
                if rule is not None and settings.secure_targets:
                    rule = secure_full_charge(
                        self.problem, self.objective, rule, self.space, settings
                    )
                if rule is not None:
                    cold += [(rule, order, cold_affected) for order in orders]
                    for number in range(settings.random_starts):
                        jittered = vehicle_rule_plan(
                            self.problem,
                            self.objective,
                            self.space,
                            settings,
                            noise=settings.random_start_noise,
                            seed=number,
                        )
                        if jittered is not None:
                            cold.append((jittered, orders[number % len(orders)], cold_affected))
        starts += cold
        searches = [self] + [
            DeviceSearch(self.problem, self.objective, self.space, settings) for _ in starts[1:]
        ]
        if len(starts) == 1:
            outcomes = [self._search_from(*starts[0], deadline)]
        else:
            with ThreadPoolExecutor(max_workers=len(starts)) as pool:
                futures = [
                    pool.submit(search._search_from, begin, order, ev, deadline)
                    for search, (begin, order, ev) in zip(searches, starts)
                ]
                outcomes = [future.result() for future in futures]
        warm_outcome = outcomes[0] if warm is not None else None
        cold_outcomes = outcomes[1:] if warm is not None else outcomes
        # A candidate whose plan fails the check of the allowed values (the check the planner
        # makes at the end) never wins while another one passes it.
        best, best_valid = self._cheapest_valid(cold_outcomes or outcomes)
        if cold_outcomes and settings.refine_factor > 1:
            # A finer state of charge grid for the best plan: the coarse grid of the starts
            # misses improvements the finer one finds, so the best plan is searched again.
            fine = replace(
                settings,
                dp_transitions=settings.dp_transitions * settings.refine_factor,
                refine_factor=1,
            )
            refiners = [
                DeviceSearch(self.problem, self.objective, self.space, fine) for _ in orders
            ]
            with ThreadPoolExecutor(max_workers=len(refiners)) as pool:
                futures = [
                    pool.submit(search._search_from, best.plan, order, (), deadline)
                    for search, order in zip(refiners, orders)
                ]
                refined = [future.result() for future in futures]
            outcomes += refined
            best, best_valid = self._cheapest_valid(cold_outcomes + refined)
        winner = "cold"
        if warm_outcome is not None:
            if not cold_outcomes:
                best, winner = warm_outcome, "warm"
            elif not self._passes_check(warm_outcome):
                logger.warning(
                    "The plan of the warm start fails the check of the allowed values and is "
                    "discarded; the plan of the search from scratch is used."
                )
            elif (
                not best_valid
                or warm_outcome.breakdown.total_eur
                <= best.breakdown.total_eur + settings.warm_start_keep_margin_eur
            ):
                # The warm start keeps the plan calm: it wins unless the best cold plan is
                # cheaper by more than the margin (or fails the check).
                best, winner = warm_outcome, "warm"
        stats = SearchStats(stop=best.stats.stop, starts=len(outcomes), winner=winner)
        for outcome in outcomes:
            stats.passes += outcome.stats.passes
            stats.clipped_slots += outcome.stats.clipped_slots
            stats.full_simulations += outcome.stats.full_simulations
            stats.partial_simulations += outcome.stats.partial_simulations
            for name in ("accepted", "rejected", "unchanged", "pruned", "excess_eur", "seconds"):
                target = getattr(stats, name)
                for key, value in getattr(outcome.stats, name).items():
                    target[key] = target.get(key, 0) + value
            for key, value in outcome.stats.excess_max_eur.items():
                stats.excess_max_eur[key] = max(stats.excess_max_eur.get(key, 0.0), value)
        # Emit the largest equivalent discharge ceilings for the winning plan.
        final = normalize_discharge(self.problem, best.plan, settings, best.sim)
        if final is not best.plan:
            sim = simulate(self.problem, final)
            best = _Outcome(
                final,
                sim,
                self.objective.evaluate(final, sim),
                best.start,
                best.trace,
                best.stats,
            )
        stats.seconds["total"] = time.perf_counter() - clock
        self._plan, self._sim, self._breakdown = best.plan, best.sim, best.breakdown
        self._trace, self._stats = best.trace, stats
        return SearchResult(
            plan=best.plan,
            objective=best.breakdown,
            start_objective=best.start,
            stats=stats,
            trace=list(best.trace),
            violations=check_plan(self.problem, best.plan, self.space, settings),
        )

    def _passes_check(self, outcome: "_Outcome") -> bool:
        """Return whether the plan of an outcome has no violation of the allowed values."""
        return not check_plan(self.problem, outcome.plan, self.space, self.settings)

    def _cheapest_valid(self, candidates: Sequence["_Outcome"]) -> tuple["_Outcome", bool]:
        """Return the cheapest candidate that passes the check, and whether one does.

        If none passes, the cheapest one is returned (the caller reports its violations).
        """
        ordered = sorted(candidates, key=lambda outcome: outcome.breakdown.total_eur)
        for outcome in ordered:
            if self._passes_check(outcome):
                return outcome, True
        return ordered[0], False

    def _search_from(
        self, plan: Plan, order: str, affected: Sequence[DeviceRef], deadline: Optional[float]
    ) -> "_Outcome":
        """Search from ``plan`` with one order of the devices (state of this instance only)."""
        self._stats = SearchStats()
        self._deadline = deadline
        self._order_name = order
        self._tried = {}
        self._trace = []
        self._adopt(plan)
        start = self._breakdown
        self._run(affected)
        return _Outcome(
            self._plan, self._sim, self._breakdown, start, list(self._trace), self._stats
        )

    def _run(self, affected: Sequence[DeviceRef]) -> None:
        """Improve the current plan: the event phase, then passes until nothing improves."""
        if affected:
            event_clock = time.perf_counter()
            self._event_phase(affected)
            self._add_time("event", time.perf_counter() - event_clock)
        self._stats.stop = STOP_MAX_PASSES
        for _ in range(self.settings.max_passes):
            if self._out_of_time():
                self._stats.stop = STOP_TIME
                break
            self._stats.passes += 1
            changed = self._single_pass()
            if not changed:
                changed = self._pair_pass()
            if not changed:
                self._stats.stop = STOP_TIME if self._out_of_time() else STOP_CONVERGED
                break

    # ------------------------------------------------------------------ bookkeeping

    def _add_time(self, phase: str, seconds: float) -> None:
        self._stats.seconds[phase] = self._stats.seconds.get(phase, 0.0) + seconds

    def _count(self, table: dict[str, int], kind: str) -> None:
        table[kind] = table.get(kind, 0) + 1

    def _out_of_time(self) -> bool:
        return self._deadline is not None and time.perf_counter() >= self._deadline

    def _adopt(self, plan: Plan) -> None:
        """Make ``plan`` the current plan (full simulation and objective)."""
        self._plan = plan
        self._sim = simulate(self.problem, plan)
        self._stats.full_simulations += 1
        self._breakdown = self.objective.evaluate(plan, self._sim)
        self._version += 1
        self._trace.append(self._breakdown.total_eur)

    def _partial_total(self, plan: Plan, slot: int) -> float:
        """Return the objective of ``plan`` from ``slot`` on, starting from the current state."""
        thermal = None if self._sim.thermal is None else self._sim.thermal.state_at(slot)
        result = simulate(self.problem, plan, slot, self._sim.soc_wh[:, slot], thermal)
        self._stats.partial_simulations += 1
        earlier = self._sim.soc_pct[:, :slot]
        return self.objective.evaluate(plan, result, earlier).total_eur

    def _consider(self, candidate: Plan, kind: str) -> bool:
        """The guard: take the candidate if the exact objective falls; return whether it was taken.

        Every move of every device, the heat plan of the linear program included, goes through
        here and is taken only if the exact objective of the whole plan falls by more than
        ``min_improvement_eur``. The objective of the accepted plans is therefore strictly
        decreasing and bounded below (the exact objective of any plan is finite and the plans
        are bounded), so the sequence of plans converges and cannot return to an earlier plan:
        a two-cycle between the heat plan and the electric plans is impossible. No holding price
        or round limit is needed for that; the time budget only stops the search early.
        """
        clock = time.perf_counter()
        eps = self.settings.min_improvement_eur
        first = _first_changed_slot(self._plan, candidate)
        accepted = False
        if first is None:
            self._count(self._stats.unchanged, kind)
        else:
            promising = True
            if first > 0:
                promising = (
                    self._partial_total(candidate, first)
                    < self._partial_total(self._plan, first) - eps
                )
            if promising:
                result = simulate(self.problem, candidate)
                self._stats.full_simulations += 1
                breakdown = self.objective.evaluate(candidate, result)
                if breakdown.total_eur < self._breakdown.total_eur - eps:
                    self._plan, self._sim, self._breakdown = candidate, result, breakdown
                    self._version += 1
                    self._trace.append(breakdown.total_eur)
                    accepted = True
                else:
                    excess = breakdown.total_eur - self._breakdown.total_eur
                    self._stats.excess_eur[kind] = self._stats.excess_eur.get(kind, 0.0) + excess
                    self._stats.excess_max_eur[kind] = max(
                        self._stats.excess_max_eur.get(kind, excess), excess
                    )
            else:
                self._count(self._stats.pruned, kind)
            self._count(self._stats.accepted if accepted else self._stats.rejected, kind)
        self._add_time("guard", time.perf_counter() - clock)
        return accepted

    # ------------------------------------------------------------------ moves

    def _storage_of(self, ref: DeviceRef) -> int:
        """Return the storage index of a battery or vehicle."""
        return ref.index if ref.kind == "battery" else len(self.problem.batteries) + ref.index

    def _device_move(self, ref: DeviceRef) -> bool:
        """Re-plan one device; return whether the plan changed."""
        key = (ref.kind, ref.index, -1)
        if self._tried.get(key) == self._version:
            return False
        clock = time.perf_counter()
        before = self._version
        if ref.kind == "appliance":
            self._appliance_move(ref.index)
        elif ref.kind == "thermal":
            remaining = (
                None if self._deadline is None else max(self._deadline - time.perf_counter(), 0.0)
            )
            self._moves.clipped_slots = 0
            candidate = self._moves.plan_thermal(
                self._plan, self._sim, ref.index, time_limit_s=remaining
            )
            self._stats.clipped_slots += self._moves.clipped_slots
            self._add_time("thermal", time.perf_counter() - clock)
            if candidate is not None:
                # A rejected heat plan leaves the plan held: the heat plan stays as it was.
                self._consider(candidate, "thermal")
            elif self.problem.thermal[ref.index].heat_limit_w.any():
                self._count(self._stats.unchanged, "thermal_failed")
        else:
            candidate = self._moves.plan_single(self._plan, self._sim, self._storage_of(ref))
            self._add_time(ref.kind, time.perf_counter() - clock)
            if candidate is not None:
                self._consider(candidate, ref.kind)
            if (
                ref.kind == "vehicle"
                and self.settings.vehicle_frozen_view
                and self.problem.batteries
            ):
                self._vehicle_frozen_move(ref)
        if ref.kind == "appliance":
            self._add_time("appliance", time.perf_counter() - clock)
        changed = self._version != before
        if not changed:
            self._tried[key] = self._version
        return changed

    def _vehicle_frozen_move(self, ref: DeviceRef) -> None:
        """Re-plan a vehicle as if the batteries did not discharge, and offer the result to the guard.

        Planned against a battery that discharges into it, charging looks nearly free (the later
        loss of that energy is not followed), and the plan spreads thinly over the night. Seen
        without the discharge the vehicle takes the cheap hours and the PV surplus; the guard
        decides with the exact objective, the batteries then follow in their own move.
        """
        clock = time.perf_counter()
        frozen = self._plan.copy()
        frozen.battery_discharge_limit[:] = 0.0
        frozen_sim = simulate(self.problem, frozen)
        self._stats.full_simulations += 1
        candidate = self._moves.plan_single(frozen, frozen_sim, self._storage_of(ref))
        self._add_time("vehicle", time.perf_counter() - clock)
        if candidate is None:
            return
        merged = self._plan.copy()
        merged.vehicle_charge[ref.index] = candidate.vehicle_charge[ref.index]
        self._consider(merged, "vehicle_frozen")

    def _appliance_move(self, index: int) -> None:
        schedule = self.space.appliances[index]
        cycles = self.problem.appliance_cycles[index]
        current = self._plan.appliance_starts[index, :cycles]
        if appliances.joint_size(schedule, cycles) <= self.settings.appliance_joint_limit:
            self._try_rows(index, appliances.joint_rows(schedule, cycles))
            return
        for _ in range(2):
            moved = False
            for cycle in range(cycles):
                current = self._plan.appliance_starts[index, :cycles]
                rows = appliances.cycle_rows(schedule, current, cycle)
                moved |= self._try_rows(index, rows)
            if not moved:
                break

    def _try_rows(self, index: int, rows: list[np.ndarray]) -> bool:
        """Evaluate start rows of an appliance as a batch and take the best one if it is better."""
        if not rows:
            return False
        cycles = self.problem.appliance_cycles[index]
        plans = [self._plan]
        for row in rows:
            candidate = Plan(
                battery_grid_charge=self._plan.battery_grid_charge,
                battery_discharge_limit=self._plan.battery_discharge_limit,
                battery_pv_charge=self._plan.battery_pv_charge,
                battery_export=self._plan.battery_export,
                vehicle_charge=self._plan.vehicle_charge,
                appliance_starts=self._plan.appliance_starts.copy(),
                thermal_heat_w=self._plan.thermal_heat_w,
            )
            candidate.appliance_starts[index, :cycles] = row
            plans.append(candidate)
        guard_clock = time.perf_counter()
        totals = self.objective.evaluate_batch(plans).total_eur
        self._stats.full_simulations += len(plans)
        best = int(np.argmin(totals[1:])) + 1
        accepted = bool(totals[best] < totals[0] - self.settings.min_improvement_eur)
        if accepted:
            self._adopt(plans[best])
        self._count(self._stats.accepted if accepted else self._stats.rejected, "appliance")
        self._add_time("guard", time.perf_counter() - guard_clock)
        return accepted

    def _pair_move(self, kind: str, first: DeviceRef, second: DeviceRef) -> bool:
        """Re-plan two storages together, repeating while it pays (the corridor follows the plan)."""
        key = (kind, self._storage_of(first), self._storage_of(second))
        if self._tried.get(key) == self._version:
            return False
        changed = False
        for _ in range(3):
            if self._out_of_time():
                break
            clock = time.perf_counter()
            candidate = self._moves.plan_pair(
                self._plan, self._sim, self._storage_of(first), self._storage_of(second)
            )
            self._add_time("pair", time.perf_counter() - clock)
            if candidate is None or not self._consider(candidate, f"pair_{kind}"):
                break
            changed = True
        if not changed:
            self._tried[key] = self._version
        return changed

    # ------------------------------------------------------------------ passes

    def _order(self) -> list[DeviceRef]:
        """Return the devices in the order of this start; appliances always come last.

        ``batteries_first``: batteries by size, then vehicles by departure.
        ``vehicles_first``: the other way round. Neither is better on every case (sum over the
        8 scenarios: plant A 3.36 against 5.54 EUR, plant B 85.0 against 82.2 EUR), so from
        scratch both are searched.
        """
        problem = self.problem
        batteries = len(problem.batteries)
        available = np.asarray(problem.kernel_arrays()["storage_available"])

        def departure(vehicle: int) -> int:
            plugged = np.nonzero(available[batteries + vehicle] > 0.0)[0]
            return int(plugged[-1]) if plugged.size else -1

        vehicles = [
            DeviceRef("vehicle", v)
            for v in sorted(range(len(problem.vehicles)), key=lambda v: (departure(v), v))
        ]
        sizes = [
            DeviceRef("battery", b)
            for b in sorted(range(batteries), key=lambda b: (-problem.batteries[b].capacity_wh, b))
        ]
        first, second = (
            (sizes, vehicles) if self._order_name == "batteries_first" else (vehicles, sizes)
        )
        heat = [DeviceRef("thermal", t) for t in range(len(problem.thermal))]
        position = self.settings.thermal_order
        head = heat if position in ("first", "firstlast") else []
        tail = heat if position in ("last", "firstlast") else []
        appliances = [DeviceRef("appliance", a) for a in range(len(problem.appliances))]
        return head + first + second + tail + appliances

    def _single_pass(self) -> bool:
        changed = False
        for ref in self._order():
            if self._out_of_time():
                break
            changed |= self._device_move(ref)
        return changed

    def _pairs(self, kinds: tuple[str, ...]) -> list[tuple[str, DeviceRef, DeviceRef]]:
        problem = self.problem
        vehicles = [DeviceRef("vehicle", v) for v in range(len(problem.vehicles))]
        batteries = [DeviceRef("battery", b) for b in range(len(problem.batteries))]
        batteries.sort(key=lambda r: (-problem.batteries[r.index].capacity_wh, r.index))
        pairs: list[tuple[str, DeviceRef, DeviceRef]] = []
        if "ee" in kinds:
            pairs += [("ee", a, b) for a, b in itertools.combinations(vehicles, 2)]
        if "eb" in kinds:
            pairs += [("eb", v, b) for v in vehicles for b in batteries]
        if "bb" in kinds:
            pairs += [("bb", a, b) for a, b in itertools.combinations(batteries, 2)]
        return pairs

    def _pair_pass(self, only: Optional[set[DeviceRef]] = None) -> bool:
        changed = False
        for kind, first, second in self._pairs(self.settings.pair_moves):
            if only is not None and first not in only and second not in only:
                continue
            if self._out_of_time():
                break
            changed |= self._pair_move(kind, first, second)
        return changed

    def _event_phase(self, affected: Sequence[DeviceRef]) -> None:
        """Re-plan the devices an event concerns first, then their pair moves."""
        for ref in affected:
            if self._out_of_time():
                return
            self._device_move(ref)
        self._pair_pass(only=set(affected))
