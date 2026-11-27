"""Which control values a plan may contain: the check and the repair of a plan.

``check_plan`` returns every violation of the allowed values; the search never outputs a plan
with one. ``repair_plan`` maps any plan onto the allowed values (used for start plans).

Allowed values
    Battery: grid charge level 0 or one of its ``charge_rates`` (only where the inverter can
    charge from the grid); discharge ceiling 0 or one of ``discharge_levels``; PV charging 0 or
    1 (always 1 if it is not switchable, except with export); export 0 or one of
    ``export_levels`` (direct marketing, positive tariff only), always with PV charging off and
    the discharge ceiling 1 (the operation mode GRID_SUPPORT_EXPORT: PV surplus and battery
    feed in side by side); otherwise PV charging off only without discharging and grid charging;
    grid charging only with PV charging on and never together with discharging or export. The
    operation modes carry no other combination.
    Vehicle: charge level 0 or one of its ``charge_rates``, 0 while it is not plugged in, and
    no charging in a slot that starts with the battery full.
    Thermal device: the heat power of a slot is not negative, and 0 where the device must not run
    (a heat above the operating table is an error of the simulation); the electricity of a
    running slot, the energy of a start (the ramp of the run share) included, stays within
    ``max_electric_power_w``.
    Appliance: every cycle starts in one of its allowed slots with the minimum gap to the
    previous cycle; -1 only where no such slot exists.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from akkudoktoreos.devices.models.thermal import (
    ELECTRIC_LIMIT_TOLERANCE_W,
    RUN_TOLERANCE_W,
    clip_heat_plan,
)
from akkudoktoreos.optimization.kernel.problem import Plan, PlanningProblem
from akkudoktoreos.optimization.kernel.simulate import simulate
from akkudoktoreos.optimization.search.actions import (
    ac_charge_possible,
    normalize_discharge,
    positive_levels,
)
from akkudoktoreos.optimization.search.settings import SearchSettings, SearchSpace

TOLERANCE = 1e-9
THERMAL_TOLERANCE_W = 1e-6


@dataclass(frozen=True)
class Violation:
    """One value of a plan that is not allowed.

    Attributes:
        device: ``"battery"``, ``"vehicle"``, ``"thermal"`` or ``"appliance"``.
        index: Index of the device.
        slot: Slot (or cycle for an appliance) of the value.
        reason: What is wrong.
    """

    device: str
    index: int
    slot: int
    reason: str


def _allowed_levels(
    problem: PlanningProblem, settings: SearchSettings, battery: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    unit = problem.batteries[battery]
    grid = positive_levels(unit.charge_rates) if ac_charge_possible(problem, battery) else ()
    can_discharge = unit.max_discharge_power_w > 0.0
    discharge = settings.discharge_levels_of(battery) if can_discharge else ()
    export = (
        settings.export_levels_of(battery) if problem.direct_marketing and can_discharge else ()
    )
    return (
        np.array((0.0,) + grid),
        np.array((0.0,) + tuple(sorted(discharge))),
        np.array((0.0,) + tuple(sorted(export))),
    )


def _on_levels(values: np.ndarray, levels: np.ndarray) -> np.ndarray:
    """Return where ``values`` equals one of ``levels``."""
    return np.min(np.abs(values[:, None] - levels[None, :]), axis=1) <= TOLERANCE


def _snap(values: np.ndarray, levels: np.ndarray) -> np.ndarray:
    """Return ``values`` moved to the nearest level (the lower one on a tie)."""
    return levels[np.argmin(np.abs(values[:, None] - levels[None, :]), axis=1)]


def check_plan(
    problem: PlanningProblem,
    plan: Plan,
    space: Optional[SearchSpace] = None,
    settings: Optional[SearchSettings] = None,
) -> list[Violation]:
    """Return all violations of the allowed control values (empty for a valid plan)."""
    space = space or SearchSpace.default(problem)
    settings = settings or SearchSettings()
    plan.validate(problem)
    found: list[Violation] = []
    tariff = problem.feed_in_tariff_per_wh

    for battery in range(len(problem.batteries)):
        grid_levels, discharge_levels, export_levels = _allowed_levels(problem, settings, battery)
        grid = np.asarray(plan.battery_grid_charge[battery], dtype=float)
        discharge = np.asarray(plan.battery_discharge_limit[battery], dtype=float)
        pv = np.asarray(plan.battery_pv_charge[battery], dtype=float)
        export = np.asarray(plan.battery_export[battery], dtype=float)
        pv_ok = np.abs(pv - 1.0) <= TOLERANCE
        if settings.pv_switchable_of(battery):
            pv_ok |= np.abs(pv) <= TOLERANCE
        pv_ok |= (export > 0.0) & (np.abs(pv) <= TOLERANCE)
        export_ok = _on_levels(export, export_levels) & ((export <= 0.0) | (tariff > 0.0))
        checks = (
            ("grid charge level not allowed", ~_on_levels(grid, grid_levels)),
            (
                "discharge ceiling not allowed",
                ~_on_levels(discharge, discharge_levels) & ~(export > 0.0),
            ),
            ("PV charge value not allowed", ~pv_ok),
            ("export level not allowed", ~export_ok),
            ("grid charging together with discharging", (grid > 0.0) & (discharge > 0.0)),
            ("grid charging together with export", (grid > 0.0) & (export > 0.0)),
            ("grid charging without PV charging", (grid > 0.0) & (pv <= 0.5)),
            (
                "PV charging off together with discharging",
                (pv <= 0.5) & (discharge > 0.0) & ~(export > 0.0),
            ),
            ("export with PV charging on", (export > 0.0) & (pv > 0.5)),
            (
                "export with a discharge ceiling below 1",
                (export > 0.0) & (np.abs(discharge - 1.0) > TOLERANCE),
            ),
        )
        for reason, bad in checks:
            found.extend(Violation("battery", battery, int(s), reason) for s in np.nonzero(bad)[0])

    # A heat the simulation cannot take is reported before simulating; the planner repairs it.
    if broken := [
        Violation("thermal", device, int(slot), "heat not finite or above the table")
        for device, unit in enumerate(problem.thermal)
        for slot in np.flatnonzero(unit.unsupported_heat(plan.thermal_heat_w[device]))
    ]:
        return found + broken
    result = simulate(problem, plan)
    batteries = len(problem.batteries)
    available = np.asarray(problem.kernel_arrays()["storage_available"])
    for vehicle, unit in enumerate(problem.vehicles):
        charge = np.asarray(plan.vehicle_charge[vehicle], dtype=float)
        levels = np.array((0.0,) + positive_levels(unit.charge_rates))
        bad = ~_on_levels(charge, levels)
        found.extend(
            Violation("vehicle", vehicle, int(s), "charge level not allowed")
            for s in np.nonzero(bad)[0]
        )
        away = (charge > 0.0) & (available[batteries + vehicle] <= 0.0)
        found.extend(
            Violation("vehicle", vehicle, int(s), "charging while away")
            for s in np.nonzero(away)[0]
        )
        full_wh = unit.max_soc_pct / 100.0 * unit.capacity_wh
        full = (charge > 0.0) & (result.soc_wh[batteries + vehicle] >= full_wh - 1e-6)
        found.extend(
            Violation("vehicle", vehicle, int(s), "charging at full state of charge")
            for s in np.nonzero(full)[0]
        )

    for device, heat_pump in enumerate(problem.thermal):
        heat = np.asarray(plan.thermal_heat_w[device], dtype=float)
        blocked = np.asarray(heat_pump.blocked, dtype=bool)
        found.extend(
            Violation("thermal", device, int(slot), "heat while blocked")
            for slot in np.nonzero(blocked & (heat > THERMAL_TOLERANCE_W))[0]
        )
        found.extend(
            Violation("thermal", device, int(slot), "heat below zero")
            for slot in np.nonzero(heat < -THERMAL_TOLERANCE_W)[0]
        )
        assert result.thermal is not None  # noqa: S101
        # The electricity of a running slot, the energy of a start included, stays within the
        # power limit of the source (what the operating table alone does not guarantee).
        power = result.thermal.electric_wh[device] / heat_pump.slot_hours
        over = (heat > RUN_TOLERANCE_W) & (
            power > heat_pump.max_electric_power_w + ELECTRIC_LIMIT_TOLERANCE_W
        )
        found.extend(
            Violation("thermal", device, int(s), "electric power above the limit")
            for s in np.nonzero(over)[0]
        )

    for index, schedule in enumerate(space.appliances):
        starts = plan.appliance_starts[index]
        count, windows = schedule.runnable(problem.appliance_cycles[index])
        previous: Optional[int] = None
        for cycle in range(problem.appliance_cycles[index]):
            start = int(starts[cycle])
            if cycle >= count:
                if start >= 0:
                    found.append(Violation("appliance", index, cycle, "cycle cannot run"))
                continue
            if start < 0:
                found.append(Violation("appliance", index, cycle, "cycle not run"))
                continue
            options = windows[cycle]
            if previous is not None:
                options = options[options >= previous + schedule.min_gap_slots]
            if start not in options:
                found.append(Violation("appliance", index, cycle, "start slot not allowed"))
            previous = start
        for cycle in range(problem.appliance_cycles[index], starts.shape[0]):
            if int(starts[cycle]) >= 0:
                found.append(Violation("appliance", index, cycle, "start beyond the cycles"))
    return found


def repair_plan(
    problem: PlanningProblem,
    plan: Plan,
    space: Optional[SearchSpace] = None,
    settings: Optional[SearchSettings] = None,
) -> Plan:
    """Return ``plan`` mapped onto the allowed control values."""
    space = space or SearchSpace.default(problem)
    settings = settings or SearchSettings()
    plan.validate(problem)
    repaired = Plan(
        battery_grid_charge=np.array(plan.battery_grid_charge, dtype=float),
        battery_discharge_limit=np.array(plan.battery_discharge_limit, dtype=float),
        battery_pv_charge=np.array(plan.battery_pv_charge, dtype=float),
        battery_export=np.array(plan.battery_export, dtype=float),
        vehicle_charge=np.array(plan.vehicle_charge, dtype=float),
        appliance_starts=np.array(plan.appliance_starts, dtype=np.int64),
        thermal_heat_w=np.array(plan.thermal_heat_w, dtype=float),
    )
    for device, heat_pump in enumerate(problem.thermal):
        repaired.thermal_heat_w[device] = clip_heat_plan(heat_pump, repaired.thermal_heat_w[device])
    tariff = problem.feed_in_tariff_per_wh
    for battery in range(len(problem.batteries)):
        grid_levels, discharge_levels, export_levels = _allowed_levels(problem, settings, battery)
        grid = _snap(repaired.battery_grid_charge[battery], grid_levels)
        discharge = _snap(repaired.battery_discharge_limit[battery], discharge_levels)
        export = _snap(repaired.battery_export[battery], export_levels)
        export = np.where(tariff > 0.0, export, 0.0)
        pv = np.where(repaired.battery_pv_charge[battery] > 0.5, 1.0, 0.0)
        if not settings.pv_switchable_of(battery):
            pv = np.ones_like(pv)
        charging = grid > 0.0
        # Export goes with the full discharge ceiling and PV charging off, discharging without
        # export with PV charging on: the operation modes carry no other combination.
        discharge = np.where(export > 0.0, 1.0, discharge)
        pv = np.where(discharge > 0.0, 1.0, pv)
        pv = np.where(export > 0.0, 0.0, pv)
        repaired.battery_grid_charge[battery] = grid
        repaired.battery_discharge_limit[battery] = np.where(charging, 0.0, discharge)
        repaired.battery_export[battery] = np.where(charging, 0.0, export)
        repaired.battery_pv_charge[battery] = np.where(charging, 1.0, pv)
    available = np.asarray(problem.kernel_arrays()["storage_available"])
    batteries = len(problem.batteries)
    for vehicle, unit in enumerate(problem.vehicles):
        levels = np.array((0.0,) + positive_levels(unit.charge_rates))
        charge = _snap(repaired.vehicle_charge[vehicle], levels)
        repaired.vehicle_charge[vehicle] = np.where(
            available[batteries + vehicle] > 0.0, charge, 0.0
        )
    if problem.vehicles:
        result = simulate(problem, repaired)
        for vehicle, unit in enumerate(problem.vehicles):
            full_wh = unit.max_soc_pct / 100.0 * unit.capacity_wh
            full = result.soc_wh[batteries + vehicle] >= full_wh - 1e-6
            repaired.vehicle_charge[vehicle] = np.where(full, 0.0, repaired.vehicle_charge[vehicle])
    for index, schedule in enumerate(space.appliances):
        count, windows = schedule.runnable(problem.appliance_cycles[index])
        previous = None
        for cycle in range(problem.appliance_cycles[index]):
            if cycle >= count:
                repaired.appliance_starts[index, cycle] = -1
                continue
            options = windows[cycle]
            if previous is not None:
                options = options[options >= previous + schedule.min_gap_slots]
            wanted = int(repaired.appliance_starts[index, cycle])
            if wanted < 0:
                wanted = int(options[0])
            pick = int(options[np.argmin(np.abs(options - wanted))])
            repaired.appliance_starts[index, cycle] = pick
            previous = pick
        repaired.appliance_starts[index, problem.appliance_cycles[index] :] = -1
    return normalize_discharge(problem, repaired, settings)
