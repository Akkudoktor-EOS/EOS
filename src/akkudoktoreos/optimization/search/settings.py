"""Inputs of the device by device search that are not part of the planning problem.

``SearchSettings`` holds the decided control levels and the effort limits; ``ApplianceSchedule``
holds, per household appliance, the start slots a cycle may use (computed from windows and
deadlines by the caller).
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from akkudoktoreos.optimization.kernel.problem import PlanningProblem


@dataclass(frozen=True)
class SearchSettings:
    """Control levels and effort limits of the search.

    Attributes:
        discharge_levels: Discharge ceilings a battery may use, as factor of its rated
            discharge power (0 = no discharging is always allowed). ``(1.0,)`` is the plain
            on/off discharging of the former optimizers.
        export_levels: Battery export levels (direct marketing only).
        pv_charge_switchable: A battery may be told not to charge from PV. If False, PV
            charging is always on.
        battery_discharge_levels: Discharge ceilings per battery (plan order); an empty tuple
            for a battery (or a missing entry) takes ``discharge_levels``.
        battery_export_levels: Export levels per battery; same rule, with ``export_levels``.
        battery_pv_switchable: PV charging switchable per battery; a missing entry takes
            ``pv_charge_switchable``.
        soc_nodes: Least number of nodes of the state of charge grid of the single device
            planning.
        soc_nodes_max: Most nodes. The grid is refined while the planning stays below
            ``dp_transitions`` slot simulations, so short problems are solved nearly exactly.
        dp_transitions: Effort of one single device planning: simulated (slot, node, action)
            transitions that decide the number of nodes.
        pair_levels: Number of levels per control that the pair moves choose from (thinned out
            of the full lists).
        pair_cells: Cells of the state grid of a pair move per storage.
        pair_corridor: Half width of the corridor around the current trajectory in cells.
        pair_moves: Kinds of pair moves: ``"ee"`` (two vehicles), ``"eb"`` (vehicle and
            battery), ``"bb"`` (two batteries). Default: ``"eb"``; with the exact full charge and the
            vehicle starts it closes most of the gap to GENETIC in the cases with vehicle and
            battery (stage 7b measurements), the others cost time and brought nothing.
        thermal_order: Where the thermal devices stand in a pass: ``"first"``, ``"last"``, or
            ``"firstlast"`` (before and after the other devices, as the study found best).
        start_orders: Orders of the devices (``batteries_first``, ``vehicles_first``) that a
            search from scratch starts with; the best result counts. With a given plan the
            first one is used for the start from that plan.
        rule_starts: From scratch, also start from the empty plan with every vehicle charging in
            its cheapest slots (one more start per order, run in parallel).
        warm_start_compare: A search from a given plan (warm start, event) also runs the full
            search from scratch and takes the better plan, so a stale warm start cannot hold
            the result above the cold one. False: the given plan is the only start (faster).
        warm_start_keep_margin_eur: The plan from the given plan is kept unless the search
            from scratch is cheaper by more than this [EUR]; keeps the plan calm from run to
            run.
        secure_targets: The start plans from scratch first reach every full charge target of a
            battery that can be reached (``secure_full_charge``).
        refine_factor: After the starts the best plan is searched again with ``dp_transitions``
            times this factor (a finer state of charge grid); 1 for no refinement.
        random_starts: Further starts from the vehicle rule plan with noisy slot prices (fixed
            seeds, so the result is reproducible).
        random_start_noise: Relative standard deviation of that noise.
        vehicle_frozen_view: A vehicle is also planned against batteries that do not discharge;
            the guard takes that plan if it is better.
        max_passes: Maximum number of passes over the devices.
        time_budget_s: Stop starting new moves after this time; None for no limit.
        min_improvement_eur: A move is accepted only if the objective falls by more than this.
        appliance_joint_limit: Cycles of one appliance are searched jointly while the
            number of combinations is at most this.
    """

    discharge_levels: tuple[float, ...] = tuple(round(j / 10.0, 10) for j in range(1, 11))
    export_levels: tuple[float, ...] = (0.5, 1.0)
    pv_charge_switchable: bool = True
    battery_discharge_levels: tuple[tuple[float, ...], ...] = ()
    battery_export_levels: tuple[tuple[float, ...], ...] = ()
    battery_pv_switchable: tuple[bool, ...] = ()
    soc_nodes: int = 15
    soc_nodes_max: int = 301
    dp_transitions: int = 30_000
    pair_levels: int = 2
    pair_cells: int = 20
    pair_corridor: int = 1
    pair_moves: tuple[str, ...] = ("eb",)
    thermal_order: str = "firstlast"
    start_orders: tuple[str, ...] = ("batteries_first", "vehicles_first")
    rule_starts: bool = True
    vehicle_frozen_view: bool = True
    warm_start_compare: bool = True
    warm_start_keep_margin_eur: float = 0.01
    secure_targets: bool = True
    refine_factor: int = 4
    random_starts: int = 4
    random_start_noise: float = 0.3
    max_passes: int = 8
    time_budget_s: Optional[float] = None
    min_improvement_eur: float = 1e-9
    appliance_joint_limit: int = 400

    def __post_init__(self) -> None:
        """Validate the levels."""
        for name in ("discharge_levels", "export_levels"):
            levels = getattr(self, name)
            if any(not 0.0 < level <= 1.0 for level in levels):
                raise ValueError(f"{name} must lie in (0, 1].")
        for name in ("battery_discharge_levels", "battery_export_levels"):
            for levels in getattr(self, name):
                if any(not 0.0 < level <= 1.0 for level in levels):
                    raise ValueError(f"{name} must lie in (0, 1].")
        if self.soc_nodes < 3 or self.pair_cells < 2 or self.pair_corridor < 1:
            raise ValueError("soc_nodes, pair_cells and pair_corridor are too small.")
        if not self.start_orders or set(self.start_orders) - {"batteries_first", "vehicles_first"}:
            raise ValueError("start_orders must name batteries_first and/or vehicles_first.")
        if self.thermal_order not in ("first", "last", "firstlast"):
            raise ValueError("thermal_order must be first, last or firstlast.")
        if self.warm_start_keep_margin_eur < 0.0:
            raise ValueError("warm_start_keep_margin_eur must not be negative.")
        unknown = set(self.pair_moves) - {"ee", "eb", "bb"}
        if unknown:
            raise ValueError(f"Unknown pair moves {sorted(unknown)}.")

    def discharge_levels_of(self, battery: int) -> tuple[float, ...]:
        """Return the discharge ceilings of a battery, ascending (its own list or the common one)."""
        own = self.battery_discharge_levels
        return tuple(sorted(own[battery] if battery < len(own) else self.discharge_levels))

    def export_levels_of(self, battery: int) -> tuple[float, ...]:
        """Return the export levels of a battery, ascending (its own list or the common one)."""
        own = self.battery_export_levels
        return tuple(sorted(own[battery] if battery < len(own) else self.export_levels))

    def pv_switchable_of(self, battery: int) -> bool:
        """Return whether PV charging of a battery may be switched off."""
        own = self.battery_pv_switchable
        return own[battery] if battery < len(own) else self.pv_charge_switchable


@dataclass(frozen=True, eq=False)
class ApplianceSchedule:
    """Start slots a household appliance may use.

    Attributes:
        allowed_starts: Per cycle the allowed start slots, ascending. A cycle without any
            allowed slot is not run (start -1).
        min_gap_slots: Minimum distance between the starts of consecutive cycles of this
            appliance.
    """

    allowed_starts: tuple[np.ndarray, ...]
    min_gap_slots: int = 0

    def runnable(self, cycles: int) -> tuple[int, tuple[np.ndarray, ...]]:
        """Return how many cycles can run and, per cycle, the start slots that allow it.

        Cycles are placed greedily at their earliest allowed slots with the minimum gap; the
        number placed is the number of cycles that must run (later ones have no feasible slot
        and are not run). Per runnable cycle the returned slots are those from which all later
        runnable cycles still fit.
        """
        count = 0
        previous: Optional[int] = None
        for cycle in range(cycles):
            allowed = np.asarray(self.allowed_starts[cycle], dtype=np.int64)
            if previous is not None:
                allowed = allowed[allowed >= previous + self.min_gap_slots]
            if allowed.size == 0:
                break
            previous = int(allowed[0])
            count += 1
        windows: list[np.ndarray] = []
        latest: Optional[int] = None
        for cycle in range(count - 1, -1, -1):
            allowed = np.asarray(self.allowed_starts[cycle], dtype=np.int64)
            if latest is not None:
                allowed = allowed[allowed <= latest - self.min_gap_slots]
            windows.append(allowed)
            latest = int(allowed[-1])
        return count, tuple(reversed(windows))

    @classmethod
    def anywhere(
        cls, problem: PlanningProblem, appliance: int, min_gap_slots: Optional[int] = None
    ) -> "ApplianceSchedule":
        """Allow every start at which the cycle still fits into the horizon.

        The default gap is the length of the cycle, so cycles do not overlap.
        """
        length = len(problem.appliances[appliance].run_energy_wh)
        slots = np.arange(0, max(problem.slots - length + 1, 0), dtype=np.int64)
        gap = length if min_gap_slots is None else min_gap_slots
        cycles = problem.appliance_cycles[appliance]
        return cls(tuple(slots for _ in range(cycles)), gap)


@dataclass(frozen=True, eq=False)
class SearchSpace:
    """What the search may choose besides the levels: appliance start slots.

    Attributes:
        appliances: One schedule per appliance of the problem.
    """

    appliances: tuple[ApplianceSchedule, ...] = field(default_factory=tuple)

    @classmethod
    def default(cls, problem: PlanningProblem) -> "SearchSpace":
        """Return a space that lets every appliance start anywhere."""
        return cls(
            tuple(ApplianceSchedule.anywhere(problem, a) for a in range(len(problem.appliances)))
        )
