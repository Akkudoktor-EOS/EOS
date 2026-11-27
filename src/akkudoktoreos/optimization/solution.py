"""Solution of the device by device optimizer in the types EOS, endpoints and adapters use.

From a ``PlanningTask``, the best ``Plan`` and its simulation this module builds the
``OptimizationSolution`` (time series per slot), the ``EnergyManagementPlan`` (instructions) and
one ``ThermalPlan`` per heat source. A plan can be read back from the series and from the
instructions without loss (``plan_from_frames``, ``plan_from_instructions``).

Controls and operation modes
----------------------------
The plan decides per battery and slot a grid charge level ``g``, a discharge ceiling ``d``, PV
charging ``p`` (0 or 1) and an export level ``e``; the existing operation modes carry them as:

====================  ======================  ======================  ==========
mode                  grid, discharge, PV     export                  factor
====================  ======================  ======================  ==========
IDLE                  ``g=0, d=0, p=0``       ``e=0``                 1.0
NON_EXPORT            ``g=0, d=0, p=1``       ``e=0``                 1.0
SELF_CONSUMPTION      ``g=0, d>0, p=1``       ``e=0``                 ``d``
FORCED_CHARGE         ``g>0, d=0, p=1``       ``e=0``                 ``g``
GRID_SUPPORT_EXPORT   ``g=0, d=1, p=0``       ``e>0``                 ``e``
====================  ======================  ======================  ==========

SELF_CONSUMPTION is the discharge ceiling as factor of the rated discharge power (the battery
covers at most this share of the maximum load power, never more than the load). A vehicle
charges with GRID_SUPPORT_IMPORT and the charge level as factor (level 0: IDLE); an appliance
is RUN while a cycle or a run in progress occupies the slot, else OFF.

GRID_SUPPORT_EXPORT means PV charging off: the PV surplus and the battery feed in side by side
(the former implementation refuses export together with charging as well). Not expressible:
discharging without export with PV charging switched off (``p=0`` with ``d>0``, ``e=0``; the
former implementation maps it to SELF_CONSUMPTION, so the output could not tell it apart) and
export with a discharge ceiling below 1. The search never chooses them (its action
sets and its check, ``optimization.search``); if such a plan reaches this module anyway, a
``ValueError`` names the slot as a safeguard; no mode is guessed.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional

import numpy as np
import pandas as pd

from akkudoktoreos.core.emplan import DDBCInstruction, EnergyManagementPlan, FRBCInstruction
from akkudoktoreos.core.pydantic import PydanticDateTimeDataFrame
from akkudoktoreos.devices.devicesabc import ApplianceOperationMode, BatteryOperationMode
from akkudoktoreos.optimization.kernel.problem import Plan
from akkudoktoreos.optimization.kernel.simulate import SimulationResult
from akkudoktoreos.optimization.objective import ObjectiveBreakdown
from akkudoktoreos.optimization.optimization import OptimizationSolution
from akkudoktoreos.optimization.request.task import PlanningTask
from akkudoktoreos.optimization.solutionmodel import (
    Ohmrakel42Solution,
    PlanningWarning,
    StorageControlLimits,
    TailDiagnostics,
    TailPlanSlot,
    TerminalValueCurveReport,
    TerminalValueReport,
)
from akkudoktoreos.optimization.tailvalue import tail_diagnostics, tail_plan
from akkudoktoreos.optimization.terminalvalue import ValueCurve
from akkudoktoreos.thermal.thermalmodels import TemperatureInterval, ThermalPlan
from akkudoktoreos.utils.datetimeutil import to_datetime

LEVEL_TOLERANCE = 1e-9

# Code of the warning for a vehicle whose charge target the plan does not reach.
VEHICLE_TARGET_UNREACHABLE = "vehicle_target_unreachable"
# Shortfall of a vehicle target below which it counts as reached [percentage points].
VEHICLE_TARGET_TOLERANCE_PCT = 0.1
PLAN_SOURCE = "EOS OHMRAKEL42 optimizer"


# ---------------------------------------------------------------------------------------
# The mapping of controls to operation modes


def battery_operation(
    grid_charge: float, discharge_limit: float, pv_charge: float, export: float
) -> tuple[BatteryOperationMode, float]:
    """Return the operation mode and factor of the controls of one battery and slot.

    Raises:
        ValueError: For a combination the operation modes cannot carry (see the module
            documentation) or an illegal one (grid charging with discharging or export).
    """
    g, d, p, e = grid_charge, discharge_limit, pv_charge, export
    pv_on = p > 0.5
    if g > 0.0:
        if d > 0.0 or e > 0.0 or not pv_on:
            raise ValueError(
                f"Grid charging together with discharging, export or no PV charging is illegal "
                f"(g={g}, d={d}, p={p}, e={e})."
            )
        return BatteryOperationMode.FORCED_CHARGE, g
    if e > 0.0:
        if pv_on or abs(d - 1.0) > LEVEL_TOLERANCE:
            raise ValueError(
                "No operation mode carries export with PV charging on or with a "
                f"discharge ceiling below 1 (d={d}, p={p}, e={e})."
            )
        return BatteryOperationMode.GRID_SUPPORT_EXPORT, e
    if d > 0.0:
        if not pv_on:
            raise ValueError(
                "No operation mode carries discharging with PV charging switched off "
                f"(d={d}); disable pv_charge_switchable of the battery."
            )
        return BatteryOperationMode.SELF_CONSUMPTION, d
    if pv_on:
        return BatteryOperationMode.NON_EXPORT, 1.0
    return BatteryOperationMode.IDLE, 1.0


def battery_controls(mode: str, factor: float) -> tuple[float, float, float, float]:
    """Return ``(grid charge, discharge ceiling, PV charge, export)`` of a mode and factor.

    The inverse of ``battery_operation``.

    Raises:
        ValueError: For a mode the optimizer does not use.
    """
    if mode == BatteryOperationMode.IDLE:
        return 0.0, 0.0, 0.0, 0.0
    if mode == BatteryOperationMode.NON_EXPORT:
        return 0.0, 0.0, 1.0, 0.0
    if mode == BatteryOperationMode.SELF_CONSUMPTION:
        return 0.0, float(factor), 1.0, 0.0
    if mode == BatteryOperationMode.FORCED_CHARGE:
        return float(factor), 0.0, 1.0, 0.0
    if mode == BatteryOperationMode.GRID_SUPPORT_EXPORT:
        return 0.0, 1.0, 0.0, float(factor)
    raise ValueError(f"The operation mode {mode!r} is not used for batteries of this optimizer.")


def vehicle_operation(level: float) -> tuple[BatteryOperationMode, float]:
    """Return the operation mode and factor of the charge level of a vehicle."""
    if level > 0.0:
        return BatteryOperationMode.GRID_SUPPORT_IMPORT, level
    return BatteryOperationMode.IDLE, 1.0


def vehicle_level(mode: str, factor: float) -> float:
    """Return the charge level of a vehicle's operation mode (the inverse)."""
    if mode == BatteryOperationMode.GRID_SUPPORT_IMPORT:
        return float(factor)
    if mode == BatteryOperationMode.IDLE:
        return 0.0
    raise ValueError(f"The operation mode {mode!r} is not used for vehicles of this optimizer.")


def appliance_running(task: PlanningTask, plan: Plan, index: int) -> np.ndarray:
    """Return per slot whether an appliance runs: a cycle of the plan or a run in progress."""
    slots = task.problem.slots
    length = len(task.problem.appliances[index].run_energy_wh)
    running = np.zeros(slots, dtype=bool)
    running[: task.appliance_pinned_end_slots[index]] = True
    for start in plan.appliance_starts[index]:
        if start >= 0:
            running[int(start) : int(start) + length] = True
    return running


def appliance_starts_from_running(task: PlanningTask, index: int, running: np.ndarray) -> list[int]:
    """Return the cycle starts that give a running mask (the inverse of ``appliance_running``).

    The slots of a run in progress are not cycles. A stretch of running slots is split into
    runs of the length of the profile; since runs cannot overlap, the split is unique.

    Raises:
        ValueError: If a stretch is no whole number of runs.
    """
    length = len(task.problem.appliances[index].run_energy_wh)
    mask = np.array(running, dtype=bool)
    mask[: task.appliance_pinned_end_slots[index]] = False
    starts: list[int] = []
    slot = 0
    while slot < len(mask):
        if not mask[slot]:
            slot += 1
            continue
        end = slot
        while end < len(mask) and mask[end]:
            end += 1
        count, rest = divmod(end - slot, length)
        if rest:
            raise ValueError(
                f"The running slots {slot}..{end - 1} of appliance {task.appliance_ids[index]} "
                f"are no whole number of runs of {length} slots."
            )
        starts.extend(slot + length * k for k in range(count))
        slot = end
    return starts


# ---------------------------------------------------------------------------------------
# The serializable solution


def controls_of(task: PlanningTask, plan: Plan) -> dict[str, list[float]]:
    """Return the plan as the serializable table of ``Ohmrakel42Solution.controls``."""
    controls: dict[str, list[float]] = {}
    for index, device in enumerate(task.battery_ids):
        controls[f"{device}_grid_charge"] = plan.battery_grid_charge[index].tolist()
        controls[f"{device}_discharge_limit"] = plan.battery_discharge_limit[index].tolist()
        controls[f"{device}_pv_charge"] = plan.battery_pv_charge[index].tolist()
        controls[f"{device}_export"] = plan.battery_export[index].tolist()
    for index, device in enumerate(task.vehicle_ids):
        controls[f"{device}_charge"] = plan.vehicle_charge[index].tolist()
    for index, device in enumerate(task.appliance_ids):
        controls[f"{device}_starts"] = [float(s) for s in plan.appliance_starts[index]]
    for index, info in enumerate(task.thermal):
        controls[f"{info.source_id}_heat_w"] = plan.thermal_heat_w[index].tolist()
    return controls


@dataclass(frozen=True, eq=False)
class PlanningOutcome:
    """Everything one planning produces, ready to publish.

    Attributes:
        task: The planning task.
        plan: The best plan.
        result: Its simulation.
        breakdown: Its objective.
        solution: The native solution.
        optimization_solution: The series per slot.
        energy_management_plan: The instructions.
        thermal_plans: One plan per heat source.
    """

    task: PlanningTask
    plan: Plan
    result: SimulationResult
    breakdown: ObjectiveBreakdown
    solution: Ohmrakel42Solution
    optimization_solution: OptimizationSolution
    energy_management_plan: EnergyManagementPlan
    thermal_plans: tuple[ThermalPlan, ...]


# ---------------------------------------------------------------------------------------
# Series per slot


def _mode_columns(
    frame: dict[str, list[float]], device: str, modes: list[tuple[str, float]], all_modes: Any
) -> None:
    """Add the ``<device>_<mode>_op_mode`` and ``_op_factor`` columns of every mode."""
    for mode in all_modes:
        frame[f"{device}_{mode.lower()}_op_mode"] = [1.0 if m == mode else 0.0 for m, _ in modes]
        frame[f"{device}_{mode.lower()}_op_factor"] = [f if m == mode else 0.0 for m, f in modes]


def battery_modes(task: PlanningTask, plan: Plan, index: int) -> list[tuple[str, float]]:
    """Return the operation mode and factor per slot of a battery."""
    modes: list[tuple[str, float]] = []
    for slot in range(task.problem.slots):
        try:
            modes.append(
                battery_operation(
                    float(plan.battery_grid_charge[index, slot]),
                    float(plan.battery_discharge_limit[index, slot]),
                    float(plan.battery_pv_charge[index, slot]),
                    float(plan.battery_export[index, slot]),
                )
            )
        except ValueError as exc:
            raise ValueError(f"Battery {task.battery_ids[index]}, slot {slot}: {exc}") from exc
    return modes


def vehicle_modes(plan: Plan, index: int) -> list[tuple[str, float]]:
    """Return the operation mode and factor per slot of a vehicle."""
    return [vehicle_operation(float(level)) for level in plan.vehicle_charge[index]]


def appliance_energy(task: PlanningTask, plan: Plan, index: int) -> np.ndarray:
    """Return the energy of one appliance per slot [Wh]: cycles of the plan and a run in progress."""
    params = task.problem.appliances[index]
    slots = task.problem.slots
    energy = np.zeros(slots)
    if params.pinned_load_wh is not None:
        length = min(slots, len(params.pinned_load_wh))
        energy[:length] += np.asarray(params.pinned_load_wh[:length])
    profile = np.asarray(params.run_energy_wh)
    for start in plan.appliance_starts[index]:
        if start >= 0:
            end = min(int(start) + len(profile), slots)
            energy[int(start) : end] += profile[: end - int(start)]
    return energy


def solution_frames(
    task: PlanningTask, plan: Plan, result: SimulationResult
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return the solution frame and the prediction frame of a plan, one row per slot.

    The solution frame holds the figures of the house, per device the state of charge, the
    operation modes (``<id>_<mode>_op_mode`` and ``_op_factor`` like the former optimizer) and
    the appliance energy. The prediction frame holds the forecasts and, per heat source, the
    planned heat and electric power and the room temperature.
    """
    grid = task.grid
    slots = task.problem.slots
    index = pd.date_range(start=grid.start, periods=slots, freq=f"{grid.interval_seconds}s")
    frame: dict[str, Any] = {
        "load_energy_wh": result.load_wh.tolist(),
        "grid_feedin_energy_wh": result.grid_export_wh.tolist(),
        "grid_consumption_energy_wh": result.grid_import_wh.tolist(),
        "costs_amt": result.cost_eur.tolist(),
        "revenue_amt": result.revenue_eur.tolist(),
        "losses_energy_wh": result.losses_wh.tolist(),
    }
    for number, device in enumerate(task.battery_ids):
        frame[f"{device}_soc_factor"] = (result.soc_pct[number] / 100.0).tolist()
        _mode_columns(frame, device, battery_modes(task, plan, number), BatteryOperationMode)
    for number, device in enumerate(task.vehicle_ids):
        frame[f"{device}_soc_factor"] = (result.vehicle_soc_pct[number] / 100.0).tolist()
        frame[f"{device}_energy_wh"] = result.charge_raw_wh[len(task.battery_ids) + number].tolist()
        _mode_columns(frame, device, vehicle_modes(plan, number), BatteryOperationMode)
    for number, device in enumerate(task.appliance_ids):
        frame[f"{device}_energy_wh"] = appliance_energy(task, plan, number).tolist()
        running = appliance_running(task, plan, number)
        modes = [
            (ApplianceOperationMode.RUN if run else ApplianceOperationMode.OFF, 1.0)
            for run in running
        ]
        for mode in (ApplianceOperationMode.RUN, ApplianceOperationMode.OFF):
            frame[f"{device}_{mode.lower()}_op_mode"] = [
                1.0 if m == mode else 0.0 for m, _ in modes
            ]
            frame[f"{device}_{mode.lower()}_op_factor"] = [
                1.0 if m == mode else 0.0 for m, _ in modes
            ]
    # Old solution entities remain usable when their unqualified device is unique.
    if len(task.battery_ids) <= 1:

        def battery_column(values: np.ndarray) -> list[float]:
            return values[0].tolist() if task.battery_ids else [0.0] * slots

        frame["genetic_ac_charge_factor"] = battery_column(plan.battery_grid_charge)
        frame["genetic_dc_charge_factor"] = battery_column(plan.battery_pv_charge)
        frame["genetic_discharge_allowed_factor"] = battery_column(
            (plan.battery_discharge_limit > 0).astype(float)
        )
        frame["genetic_battery_grid_export_allowed_factor"] = battery_column(
            (plan.battery_export > 0).astype(float)
        )
    if len(task.vehicle_ids) <= 1:
        frame["genetic_ev_charge_factor"] = (
            plan.vehicle_charge[0].tolist() if task.vehicle_ids else [0.0] * slots
        )
    solution = pd.DataFrame(frame, index=index)
    solution.insert(0, "date_time", index)

    forecasts = task.forecasts
    control = forecasts.control_slots
    prediction: dict[str, Any] = {
        "pvforecast_ac_energy_wh": forecasts.pv_wh[:control].tolist(),
        "loadforecast_energy_wh": forecasts.load_wh[:control].tolist(),
        "elec_price_amt_kwh": (forecasts.price_per_wh[:control] * 1000).tolist(),
        "feed_in_tariff_amt_kwh": (forecasts.tariff_per_wh[:control] * 1000).tolist(),
    }
    if task.thermal:
        prediction["weather_air_temp_celcius"] = task.thermal[0].params.outdoor_c.tolist()
    thermal = result.thermal
    for number, info in enumerate(task.thermal):
        hours = info.params.slot_hours
        prediction[f"{info.source_id}_planned_heat_w"] = plan.thermal_heat_w[number].tolist()
        if thermal is not None:
            prediction[f"{info.source_id}_planned_electric_w"] = (
                thermal.electric_wh[number] / hours
            ).tolist()
            prediction[f"{info.source_id}_room_temperature_c"] = thermal.temperature_c[number][
                :slots, 0
            ].tolist()
    predictions = pd.DataFrame(prediction, index=index)
    predictions.insert(0, "date_time", index)
    return solution, predictions


def optimization_solution_of(
    task: PlanningTask, plan: Plan, result: SimulationResult, breakdown: ObjectiveBreakdown
) -> OptimizationSolution:
    """Return the plan as the general optimization solution."""
    solution, prediction = solution_frames(task, plan, result)
    end = task.grid.slot_start(task.problem.slots)
    return OptimizationSolution(
        id=f"optimization-ohmrakel42@{to_datetime(as_string=True)}",
        generated_at=to_datetime(),
        comment="OHMRAKEL42 optimization solution.",
        valid_from=task.grid.start,
        valid_until=end,
        total_losses_energy_wh=float(result.total_losses_wh),
        total_revenues_amt=float(result.total_revenue_eur),
        total_costs_amt=float(result.total_cost_eur),
        fitness_score={float(breakdown.total_eur)},
        prediction=PydanticDateTimeDataFrame.from_dataframe(prediction),
        solution=PydanticDateTimeDataFrame.from_dataframe(solution),
    )


# ---------------------------------------------------------------------------------------
# Instructions


def energy_management_plan_of(task: PlanningTask, plan: Plan) -> EnergyManagementPlan:
    """Return the plan as instructions: a new one only where the mode or factor changes."""
    start = task.grid.start
    interval = task.grid.interval_seconds
    result = EnergyManagementPlan(
        id=f"plan-ohmrakel42@{to_datetime(as_string=True)}",
        generated_at=to_datetime(),
        instructions=[],
        comment="OHMRAKEL42 energy management plan.",
    )

    def add(resource: str, modes: list[tuple[str, float]], kind: type) -> None:
        last: Optional[tuple[str, float]] = None
        for slot, (mode, factor) in enumerate(modes):
            if (mode, factor) == last:
                continue
            last = (mode, factor)
            result.add_instruction(
                kind(
                    resource_id=resource,
                    execution_time=start.add(seconds=interval * slot),
                    actuator_id=resource,
                    operation_mode_id=mode,
                    operation_mode_factor=factor,
                )
            )

    for number, device in enumerate(task.battery_ids):
        add(device, battery_modes(task, plan, number), FRBCInstruction)
    for number, device in enumerate(task.vehicle_ids):
        add(device, vehicle_modes(plan, number), FRBCInstruction)
    for number, device in enumerate(task.appliance_ids):
        running = appliance_running(task, plan, number)
        modes: list[tuple[str, float]] = [
            (ApplianceOperationMode.RUN if run else ApplianceOperationMode.OFF, 1.0)
            for run in running
        ]
        add(device, modes, DDBCInstruction)
    if not result.instructions:
        result.valid_from = start
    return result


# ---------------------------------------------------------------------------------------
# Thermal plans


# Margin below the planned room temperature of a slot from which an executor heats [K].
PLAN_MARGIN_K = 0.2


def thermal_plans_of(
    task: PlanningTask, plan: Plan, result: SimulationResult, ttl_seconds: int
) -> tuple[ThermalPlan, ...]:
    """Return one ``ThermalPlan`` per heat source: temperature targets and limits per slot.

    The target of a slot is the planned room temperature at its END, held within the comfort
    band the model holds it against: that of the FOLLOWING slot (the plan preheats for it, and lets
    the room fall towards a lower one). The limits are the band of the slot, widened where the
    target lies outside of it. In a barred slot the heat pump does not run, so there is no lower
    bound for the target (the room temperature without heat) and ``heating_allowed`` is False.
    The device regulates towards the target during the slot, so the planned temperature at the
    start (which the room has already) would never ask it to heat or to lower the temperature. The
    heat and electric power are forecast columns of the solution, not set points.

    With a weighted device (``ThermalParams.tiered``: a shortfall below the comfort minimum is
    priced by presence probability) the plan lets the room fall below the minimum on purpose; the
    executor heats when the room is below ``min_temperature_c`` and would fight the plan if that
    stayed at the comfort minimum. The target then follows the planned room temperature (not
    lifted to the minimum, never below the protection minimum) and the lower limit lies
    ``PLAN_MARGIN_K`` below the lowest planned temperature of the slot, never below protection.
    """
    thermal = result.thermal
    plans: list[ThermalPlan] = []
    if thermal is None:
        return ()
    now = task.grid.observation  # the run moment: the plan is issued for it
    slots = task.problem.slots
    for number, info in enumerate(task.thermal):
        room = thermal.temperature_c[number][1 : slots + 1, 0]  # at the end of each slot
        intervals = []
        for slot in range(slots):
            band = info.bands[slot]
            following = info.bands[min(slot + 1, slots - 1)]  # holds the room at the slot end
            barred = info.params.heat_range_w(slot) is None  # the heat pump must not run
            lowest = -np.inf if barred else following.min_temperature_c
            protection = float(info.params.protection_min_c[min(slot + 1, slots - 1)])  # type: ignore[index]
            if info.params.tiered and not barred:
                lowest = protection
            target = float(min(max(room[slot], lowest), following.max_temperature_c))
            floor = min(band.min_temperature_c, target)
            if info.params.tiered:
                planned = min(thermal.temperature_c[number][slot, 0], room[slot])
                floor = max(min(floor, planned - PLAN_MARGIN_K), protection)
            intervals.append(
                TemperatureInterval(
                    min_temperature_c=min(floor, target),
                    target_temperature_c=target,
                    max_temperature_c=max(band.max_temperature_c, target),
                    start_datetime=task.grid.slot_start(slot),
                    end_datetime=task.grid.slot_start(slot + 1),
                    heating_allowed=not barred,
                )
            )
        last_end = intervals[-1].end_datetime
        valid_until = min(now + timedelta(seconds=ttl_seconds), last_end)
        plans.append(
            ThermalPlan(
                zone_id=info.zone_id,
                plan_id=f"{info.source_id}@{now.to_iso8601_string()}",
                source=PLAN_SOURCE,
                revision=0,
                basis_control_revision=0,
                issued_at=now,
                valid_until=valid_until,
                intervals=tuple(intervals),
            )
        )
    return tuple(plans)


# ---------------------------------------------------------------------------------------
# Reading a plan back


def _plan_from_modes(
    task: PlanningTask,
    battery_modes_by_id: dict[str, list[tuple[str, float]]],
    vehicle_modes_by_id: dict[str, list[tuple[str, float]]],
    running_by_id: dict[str, np.ndarray],
    heat_by_id: dict[str, np.ndarray],
) -> Plan:
    plan = Plan.idle(task.problem)
    for number, device in enumerate(task.battery_ids):
        for slot, (mode, factor) in enumerate(battery_modes_by_id[device]):
            g, d, p, e = battery_controls(mode, factor)
            plan.battery_grid_charge[number, slot] = g
            plan.battery_discharge_limit[number, slot] = d
            plan.battery_pv_charge[number, slot] = p
            plan.battery_export[number, slot] = e
    for number, device in enumerate(task.vehicle_ids):
        for slot, (mode, factor) in enumerate(vehicle_modes_by_id[device]):
            plan.vehicle_charge[number, slot] = vehicle_level(mode, factor)
    for number, device in enumerate(task.appliance_ids):
        starts = appliance_starts_from_running(task, number, running_by_id[device])
        plan.appliance_starts[number, :] = -1
        if len(starts) > plan.appliance_starts.shape[1]:
            raise ValueError(f"Appliance {device} has more runs than planned cycles.")
        plan.appliance_starts[number, : len(starts)] = starts
    for number, info in enumerate(task.thermal):
        plan.thermal_heat_w[number] = heat_by_id[info.source_id]
    return plan


def plan_from_frames(
    task: PlanningTask, solution: pd.DataFrame, prediction: Optional[pd.DataFrame] = None
) -> Plan:
    """Read the plan back from the solution frame (and the planned heat of the prediction frame)."""
    slots = task.problem.slots

    def modes_of(device: str, all_modes: Any) -> list[tuple[str, float]]:
        modes: list[tuple[str, float]] = []
        for slot in range(slots):
            active = [
                (mode, float(solution[f"{device}_{mode.lower()}_op_factor"].iloc[slot]))
                for mode in all_modes
                if solution[f"{device}_{mode.lower()}_op_mode"].iloc[slot] == 1.0
            ]
            if len(active) != 1:
                raise ValueError(
                    f"{device}, slot {slot}: exactly one operation mode must be active."
                )
            modes.append(active[0])
        return modes

    running = {
        device: solution[f"{device}_run_op_mode"].to_numpy() == 1.0 for device in task.appliance_ids
    }
    heat: dict[str, np.ndarray] = {}
    for info in task.thermal:
        if prediction is None:
            raise ValueError("The prediction frame carries the planned heat of the heat sources.")
        heat[info.source_id] = prediction[f"{info.source_id}_planned_heat_w"].to_numpy(dtype=float)
    return _plan_from_modes(
        task,
        {d: modes_of(d, BatteryOperationMode) for d in task.battery_ids},
        {d: modes_of(d, BatteryOperationMode) for d in task.vehicle_ids},
        running,
        heat,
    )


def plan_from_instructions(
    task: PlanningTask,
    instructions: EnergyManagementPlan,
    heat: Optional[dict[str, np.ndarray]] = None,
) -> Plan:
    """Read the plan back from the instructions: the last one at or before a slot holds."""
    slots = task.problem.slots
    starts = [task.grid.slot_start(slot).timestamp() for slot in range(slots)]

    def per_slot(device: str) -> list[tuple[str, float]]:
        own = sorted(
            (i for i in instructions.instructions if i.resource_id == device),
            key=lambda i: i.execution_time.timestamp(),
        )
        if not own:
            raise ValueError(f"The plan has no instruction for {device}.")
        modes: list[tuple[str, float]] = []
        for moment in starts:
            held = [i for i in own if i.execution_time.timestamp() <= moment + 1e-6]
            if not held:
                raise ValueError(f"No instruction for {device} at the start of the plan.")
            chosen = held[-1]
            if not isinstance(chosen, (FRBCInstruction, DDBCInstruction)):
                raise ValueError(f"Unexpected instruction type for {device}.")
            modes.append((str(chosen.operation_mode_id), float(chosen.operation_mode_factor)))
        return modes

    running = {
        device: np.array([mode == ApplianceOperationMode.RUN for mode, _ in per_slot(device)])
        for device in task.appliance_ids
    }
    heat_by_id = {
        info.source_id: np.asarray((heat or {}).get(info.source_id, np.zeros(slots)), dtype=float)
        for info in task.thermal
    }
    return _plan_from_modes(
        task,
        {d: per_slot(d) for d in task.battery_ids},
        {d: per_slot(d) for d in task.vehicle_ids},
        running,
        heat_by_id,
    )


# ---------------------------------------------------------------------------------------
# All representations


def vehicle_target_warnings(task: PlanningTask, result: SimulationResult) -> list[PlanningWarning]:
    """Return a warning for each vehicle whose charge target the plan does not reach.

    The plan charges as much as it can (the shortfall is priced in the objective); a target that
    is out of reach (deadline too close, passed, not plugged in long enough, more energy than
    the charger delivers) is reported with the state of charge the plan reaches at the deadline
    and the shortfall. The state is that at the start of the deadline slot, or after the last
    slot without a deadline, as the objective judges it.
    """
    warnings: list[PlanningWarning] = []
    targets = task.objective.vehicle_targets
    for index, vehicle_id in enumerate(task.vehicle_ids):
        target = targets[index] if index < len(targets) else None
        unit = task.problem.vehicles[index]
        if target is None or target.target_soc_pct <= unit.initial_soc_pct:
            continue
        series = result.vehicle_soc_pct[index]
        deadline = target.deadline_slot
        if deadline is not None and 0 <= deadline - result.start_slot < len(series):
            reached = float(series[deadline - result.start_slot])
            when = f"by {task.grid.slot_start(deadline)}"
        else:
            reached = (
                float(result.final_soc_wh[len(task.battery_ids) + index])
                / (unit.capacity_wh)
                * 100.0
            )
            when = "by the end of the horizon"
        shortfall = target.target_soc_pct - reached
        if shortfall > VEHICLE_TARGET_TOLERANCE_PCT:
            warnings.append(
                PlanningWarning(
                    code=VEHICLE_TARGET_UNREACHABLE,
                    message=(
                        f"The target of {vehicle_id} ({target.target_soc_pct:.1f} %) is not "
                        f"reached {when}: the plan charges as much as possible and reaches "
                        f"{reached:.1f} % (short by {shortfall:.1f} percentage points)."
                    ),
                    devices=[vehicle_id],
                )
            )
    return warnings


def terminal_value_of(
    task: PlanningTask,
    breakdown: ObjectiveBreakdown,
    terminal_energy_wh: float,
    curve: Optional[ValueCurve],
) -> Optional[TerminalValueReport]:
    """Report the valuation of the battery energy at the control end and the tail after it.

    The tail plan is derived forwards from the value curve the search already used; the report
    changes neither the plan nor the objective.
    """
    if not task.battery_ids:
        return None
    mode = task.objective.terminal_value.mode
    slot_h = task.grid.slot_duration_h
    control_hours = task.problem.slots * slot_h
    requested = task.grid.requested_tail_slots * slot_h
    effective = task.forecasts.tail_slots * slot_h
    value_curve: Optional[TerminalValueCurveReport] = None
    operating: Optional[float] = None
    continuation: Optional[float] = None
    plan: list[TailPlanSlot] = []
    diagnostics: Optional[TailDiagnostics] = None
    if curve is not None and not curve.empty:
        value_curve = TerminalValueCurveReport(
            energy_wh=curve.energy_wh.tolist(),
            value_euro=curve.value_eur.tolist(),
            operating_value_euro=curve.operating_eur.tolist(),
            continuation_value_euro=curve.continuation_eur.tolist(),
            marginal_euro_per_kwh=(
                1000 * np.diff(curve.value_eur) / np.diff(curve.energy_wh)
            ).tolist(),
            residual_energy_wh=curve.residual_energy_wh,
            window_slots=task.objective.terminal_value.window_slots,
        )
        if curve.operating_eur.size:
            operating = float(np.interp(terminal_energy_wh, curve.energy_wh, curve.operating_eur))
        if curve.continuation_eur.size:
            continuation = float(
                np.interp(terminal_energy_wh, curve.energy_wh, curve.continuation_eur)
            )
        plan = tail_plan(curve, terminal_energy_wh, control_hours)
        diagnostics = tail_diagnostics(curve)
    reasons: list[str] = []
    if requested <= 0:
        reasons.append("No tail requested (tail_horizon_hours is 0).")
    elif effective <= 0:
        reasons.append(
            f"No tail: the forecasts end with the control horizon (requested {requested:g} h); "
            "the continuation value starts there."
        )
    elif effective < requested:
        reasons.append(
            f"Tail shortened: requested {requested:g} h, effective {effective:g} h "
            "(forecasts end earlier); the continuation value starts at the tail end."
        )
    return TerminalValueReport(
        control_horizon_hours=control_hours,
        requested_tail_hours=requested,
        effective_tail_hours=effective,
        tail_end_hour=control_hours + effective,
        continuation_mode=mode,
        mode="TAIL" if operating is not None else mode,
        curve=value_curve,
        continuation_curve=value_curve if mode == "AUTO" and operating is None else None,
        tail_operating_euro=operating if operating is not None else 0.0,
        continuation_value_euro=continuation if continuation is not None else 0.0,
        battery_energy_wh=terminal_energy_wh,
        credited_euro=breakdown.terminal_value_eur,
        tail_diagnostics=diagnostics,
        tail_plan=plan,
        reason=" ".join(reasons),
    )


def build_outcome(
    task: PlanningTask,
    plan: Plan,
    result: SimulationResult,
    breakdown: ObjectiveBreakdown,
    start_breakdown: ObjectiveBreakdown,
    statistics: dict[str, Any],
    *,
    warm_started: bool,
    terminal_value: Optional[TerminalValueReport] = None,
    plan_ttl_seconds: int = 1800,
) -> PlanningOutcome:
    """Build the solution, the series, the instructions and the thermal plans of a plan.

    All representations exist before any is published; an error (for example a control no
    operation mode carries) raises here.
    """
    parts = {
        "balance_eur": breakdown.balance_eur,
        "terminal_value_eur": breakdown.terminal_value_eur,
        "vehicle_target_penalty_eur": breakdown.vehicle_target_penalty_eur,
        "full_charge_penalty_eur": breakdown.full_charge_penalty_eur,
        "ac_break_even_penalty_eur": breakdown.ac_break_even_penalty_eur,
        "import_limit_penalty_eur": breakdown.import_limit_penalty_eur,
        "comfort_penalty_eur": breakdown.comfort_penalty_eur,
        "protection_penalty_eur": breakdown.protection_penalty_eur,
        "heat_terminal_eur": breakdown.heat_terminal_eur,
        "total_eur": breakdown.total_eur,
    }
    optimization_solution = optimization_solution_of(task, plan, result, breakdown)
    instructions = energy_management_plan_of(task, plan)
    thermal = thermal_plans_of(task, plan, result, plan_ttl_seconds)
    grid_charge_limit = {
        task.battery_ids[inverter.battery_index]: (
            inverter.max_ac_charge_power_w * inverter.ac_to_dc_efficiency
        )
        for inverter in task.problem.inverters
        if inverter.battery_index is not None and inverter.max_ac_charge_power_w is not None
    }
    solution = Ohmrakel42Solution(
        id=f"ohmrakel42@{to_datetime(as_string=True)}",
        generated_at=to_datetime(),
        start_datetime=task.grid.start,
        observation_datetime=task.grid.observation,
        interval_seconds=task.grid.interval_seconds,
        first_slot_fraction=task.grid.first_slot_fraction,
        battery_ids=list(task.battery_ids),
        vehicle_ids=list(task.vehicle_ids),
        appliance_ids=list(task.appliance_ids),
        thermal_ids=[info.source_id for info in task.thermal],
        energy_management_plan_id=instructions.id,
        optimization_solution_id=optimization_solution.id,
        storage_limits={
            device_id: StorageControlLimits(
                capacity_wh=storage.capacity_wh,
                initial_soc_percentage=storage.initial_soc_pct,
                min_soc_percentage=storage.min_soc_pct,
                max_charge_power_w=storage.max_charge_power_w,
                max_discharge_power_w=0.0 if storage.is_vehicle else storage.max_discharge_power_w,
                max_grid_charge_power_dc_w=grid_charge_limit.get(device_id),
                discharging_efficiency=storage.discharging_efficiency,
            )
            for device_id, storage in zip(
                task.battery_ids + task.vehicle_ids, task.problem.batteries + task.problem.vehicles
            )
        },
        controls=controls_of(task, plan),
        objective={k: float(v) for k, v in parts.items()},
        start_objective_eur=float(start_breakdown.total_eur),
        statistics=statistics,
        warm_started=warm_started,
        terminal_value=terminal_value,
        warnings=[*task.warnings, *vehicle_target_warnings(task, result)],
    )
    return PlanningOutcome(
        task=task,
        plan=plan,
        result=result,
        breakdown=breakdown,
        solution=solution,
        optimization_solution=optimization_solution,
        energy_management_plan=instructions,
        thermal_plans=thermal,
    )
