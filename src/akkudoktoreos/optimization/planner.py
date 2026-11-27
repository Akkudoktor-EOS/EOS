"""One planning run: search from the last plan or from scratch, then all representations.

``run_planning`` is the CPU bound part of an energy management run. It is synchronous and
meant for an executor. A previous plan (``WarmStart``) is moved by the elapsed slots and
improved; devices an event concerns (``changed_devices``) are planned first. A plan that
violates the allowed control values, or a control no operation mode carries, is an error with
the cause, never replaced by another plan; the exception is the heat plan of a thermal device,
which never cancels a run (see ``_hold_valid_heat``).
"""

from dataclasses import dataclass, replace
from typing import Any, Optional, Sequence

import numpy as np
from loguru import logger

from akkudoktoreos.devices.models.thermal import clip_heat_plan
from akkudoktoreos.optimization.kernel.problem import Plan
from akkudoktoreos.optimization.kernel.simulate import SimulationResult, simulate
from akkudoktoreos.optimization.objective import Objective
from akkudoktoreos.optimization.request.task import PlanningTask
from akkudoktoreos.optimization.request.thermal import (
    THERMAL_HEAT_PLAN_FAILED,
    THERMAL_NOT_PLANNED,
    THERMAL_PROTECTION_VIOLATED,
)
from akkudoktoreos.optimization.search import (
    DeviceRef,
    DeviceSearch,
    SearchResult,
    check_plan,
    shift_plan,
)
from akkudoktoreos.optimization.solution import PlanningOutcome, build_outcome, terminal_value_of
from akkudoktoreos.optimization.solutionmodel import PlanningWarning
from akkudoktoreos.utils.datetimeutil import DateTime


@dataclass(frozen=True, eq=False)
class WarmStart:
    """The plan of the last run, kept to start the next one from.

    Attributes:
        plan: The plan.
        start: Start of its slot 0.
        interval_seconds: Slot duration [s].
        signature: Devices and number of slots the plan belongs to.
    """

    plan: Plan
    start: DateTime
    interval_seconds: int
    signature: tuple[Any, ...]


def signature_of(task: PlanningTask) -> tuple[Any, ...]:
    """Return what a plan must match to be reused: devices, slots and slot duration."""
    return (
        task.battery_ids,
        task.vehicle_ids,
        task.appliance_ids,
        tuple(info.source_id for info in task.thermal),
        task.problem.slots,
        task.grid.interval_seconds,
    )


def warm_start_of(task: PlanningTask, plan: Plan) -> WarmStart:
    """Return the warm start the next run can use."""
    return WarmStart(plan, task.grid.start, task.grid.interval_seconds, signature_of(task))


def devices_of(task: PlanningTask, ids: Sequence[str]) -> list[DeviceRef]:
    """Return the search references of device ids.

    The id of a heat source this task left out (warning ``thermal_not_planned``) is dropped.

    Raises:
        ValueError: For an id that is no planned device.
    """
    known: dict[str, DeviceRef] = {}
    for kind, names in (
        ("battery", task.battery_ids),
        ("vehicle", task.vehicle_ids),
        ("appliance", task.appliance_ids),
        ("thermal", tuple(info.source_id for info in task.thermal)),
    ):
        for index, name in enumerate(names):
            known[name] = DeviceRef(kind, index)
    left_out = {name for w in task.warnings if w.code == THERMAL_NOT_PLANNED for name in w.devices}
    ids = [name for name in ids if name not in left_out]
    unknown = [name for name in ids if name not in known]
    if unknown:
        raise ValueError(
            f"Unknown devices in changed_devices: {sorted(unknown)}; planned devices are "
            f"{sorted(known)}."
        )
    return [known[name] for name in dict.fromkeys(ids)]


def shifted_start(task: PlanningTask, previous: Optional[WarmStart]) -> Optional[Plan]:
    """Return the previous plan moved to the slot grid of the task, None when it cannot be used."""
    if previous is None:
        return None
    if previous.signature != signature_of(task):
        logger.info("Warm start ignored: the devices, horizon or slot duration changed.")
        return None
    elapsed = (task.grid.start - previous.start).total_seconds()
    if elapsed < 0:
        logger.warning("Warm start ignored: it starts after this run ({}).", previous.start)
        return None
    shift = int(elapsed // task.grid.interval_seconds)
    if shift >= task.problem.slots:
        logger.info("Warm start ignored: all {} slots have elapsed.", task.problem.slots)
        return None
    return shift_plan(task.problem, previous.plan, shift, task.space, task.search)


def run_planning(
    task: PlanningTask,
    previous: Optional[WarmStart] = None,
    changed_devices: Sequence[str] = (),
    plan_ttl_seconds: int = 1800,
) -> PlanningOutcome:
    """Plan a task.

    A heat plan the solver could not compute leaves the heat plan as it was (the plan held) and
    the solution carries a ``thermal_heat_plan_failed`` warning; the other devices are planned.

    Args:
        task: The planning task.
        previous: The plan of the last run, moved by the elapsed slots as start plan.
        changed_devices: Ids of devices an event concerns; they are planned first.
        plan_ttl_seconds: Lease of the thermal plans [s].

    Raises:
        ValueError: For an unknown device id, an electrical plan that violates the allowed
            control values, or a control no operation mode carries.
    """
    affected = devices_of(task, changed_devices)
    return _plan(task, previous, affected, plan_ttl_seconds)


def _hold_valid_heat(
    task: PlanningTask,
    objective: Objective,
    search: DeviceSearch,
    found: SearchResult,
    start: Optional[Plan],
) -> tuple[SearchResult, list[str]]:
    """Replace the heat plan of every thermal device that violates the allowed values.

    The preparation of a heat plan never cancels a run. For a device with a violation the heat
    plan is replaced by the first valid one of: the heat plan held from the last run (``start``,
    already moved and repaired), the repaired heat plan of the search (``clip_heat_plan``), no
    heat. The result carries the objective of the replaced plan.

    Returns:
        ``(result, descriptions)``: the result with the replaced plan (violations of other devices
        stay, the caller reports them) and one description per replaced device.
    """
    problem = task.problem
    plan = found.plan.copy()
    held: list[str] = []
    devices = sorted({v.index for v in found.violations if v.device == "thermal"})
    for device in devices:
        unit = problem.thermal[device]
        candidates = [] if start is None else [start.thermal_heat_w[device]]
        candidates.append(clip_heat_plan(unit, found.plan.thermal_heat_w[device]))
        candidates.append(np.zeros(problem.slots))
        for heat in candidates:
            plan.thermal_heat_w[device] = heat
            if not any(
                v.device == "thermal" and v.index == device
                for v in check_plan(problem, plan, search.space, search.settings)
            ):
                break
        held.append(f"{unit.name}: {sum(v.index == device for v in found.violations)} violations")
        logger.warning(
            "The heat plan of {} violates the allowed values ({}); it is replaced by a valid one.",
            unit.name,
            found.violations[0].reason,
        )
    result = simulate(problem, plan)
    return (
        replace(
            found,
            plan=plan,
            objective=objective.evaluate(plan, result),
            violations=check_plan(problem, plan, search.space, search.settings),
        ),
        held,
    )


def _protection_warnings(task: PlanningTask, result: SimulationResult) -> list[PlanningWarning]:
    """Return a warning for every zone whose planned room falls below its protection minimum.

    A heating block is hard and the protection minimum only a price, so a long block at frost
    can leave the room below it. The plan keeps the block and names the first slot at whose
    start the room is below the minimum (``thermal.comfort_hours`` measures the same way).
    """
    warnings: list[PlanningWarning] = []
    for device, info in enumerate(task.thermal if result.thermal else ()):
        rooms = np.asarray(result.thermal.temperature_c[device])[1:, 0]  # type: ignore[union-attr]
        band = np.minimum(np.arange(1, rooms.size + 1), task.problem.slots - 1)
        limit = info.params.protection_min_c[band]  # type: ignore[index]
        below = np.flatnonzero(rooms < limit)
        if below.size:
            first = int(below[0]) + 1
            warnings.append(
                PlanningWarning(
                    code=THERMAL_PROTECTION_VIOLATED,
                    message=(
                        f"The room of zone {info.zone_id} falls below its protection minimum "
                        f"{limit[below[0]]:.1f} C from slot {first} "
                        f"({task.grid.slot_start(first).isoformat()}) on, lowest "
                        f"{rooms.min():.1f} C; heating blocks stay in force."
                    ),
                    devices=[info.source_id],
                )
            )
    return warnings


def _plan(
    task: PlanningTask,
    previous: Optional[WarmStart],
    affected: Sequence[DeviceRef],
    plan_ttl_seconds: int,
) -> PlanningOutcome:
    """Search one task and build its representations (see ``run_planning``)."""
    problem = task.problem
    objective = Objective(problem, task.objective)
    search = DeviceSearch(problem, objective, task.space, task.search)
    start = shifted_start(task, previous)
    found = search.solve(start, affected=affected)
    held: list[str] = []
    if any(violation.device == "thermal" for violation in found.violations):
        found, held = _hold_valid_heat(task, objective, search, found, start)
    if found.violations:
        first = found.violations[0]
        raise ValueError(
            f"The plan violates the allowed values ({len(found.violations)} times), first: "
            f"{first.device} {first.index}, slot {first.slot}: {first.reason}."
        )
    result = simulate(problem, found.plan)
    stats = found.stats
    statistics: dict[str, Any] = {
        "passes": stats.passes,
        "starts": stats.starts,
        "winner": stats.winner,
        "stop": stats.stop,
        "accepted": dict(stats.accepted),
        "rejected": dict(stats.rejected),
        "unchanged": dict(stats.unchanged),
        "pruned": dict(stats.pruned),
        "excess_eur": {kind: round(value, 6) for kind, value in stats.excess_eur.items()},
        "excess_max_eur": {kind: round(value, 6) for kind, value in stats.excess_max_eur.items()},
        "full_simulations": stats.full_simulations,
        "partial_simulations": stats.partial_simulations,
        "clipped_slots": stats.clipped_slots,
        "seconds": round(stats.seconds.get("total", 0.0), 3),
    }
    failed = stats.unchanged.get("thermal_failed", 0)
    task = replace(task, warnings=task.warnings + tuple(_protection_warnings(task, result)))
    if held:
        task = replace(
            task,
            warnings=task.warnings
            + (
                PlanningWarning(
                    code=THERMAL_HEAT_PLAN_FAILED,
                    message=(
                        "The heat plan violated the allowed values and was replaced by the last "
                        "valid heat plan (or no heat): " + ", ".join(held) + "."
                    ),
                    devices=[info.source_id for info in task.thermal],
                ),
            ),
        )
    if failed:
        task = replace(
            task,
            warnings=task.warnings
            + (
                PlanningWarning(
                    code=THERMAL_HEAT_PLAN_FAILED,
                    message=(
                        f"The heat plan could not be computed {failed} times during the search "
                        "(solver failure, see the log); the heat plan of the other results was kept."
                    ),
                    devices=[info.source_id for info in task.thermal],
                ),
            ),
        )
    return build_outcome(
        task,
        found.plan,
        result,
        found.objective,
        found.start_objective,
        statistics,
        warm_started=start is not None,
        plan_ttl_seconds=plan_ttl_seconds,
        terminal_value=terminal_value_of(
            task,
            found.objective,
            objective.terminal_energy_wh(result.final_soc_wh),
            objective.curve,
        ),
    )
