"""Represent a verified fleet result using EOS solution and plan contracts."""

import uuid

import numpy as np
import pandas as pd

from akkudoktoreos.core.emplan import EnergyManagementPlan, FRBCInstruction
from akkudoktoreos.core.pydantic import PydanticDateTimeDataFrame
from akkudoktoreos.devices.devicesabc import BatteryOperationMode
from akkudoktoreos.optimization.fleet import FleetSolution
from akkudoktoreos.optimization.optimization import OptimizationSolution
from akkudoktoreos.utils.datetimeutil import to_datetime


def fleet_representations(
    result: FleetSolution,
) -> tuple[OptimizationSolution, EnergyManagementPlan]:
    """Build both public representations before EMS publishes either one."""
    request = result.parameters
    if result.status not in ("optimal", "feasible") or request is None:
        raise ValueError("No verified fleet plan available")
    start = to_datetime(request.start_datetime)
    n = len(result.import_w)
    dt = request.interval_seconds / 3600
    end = start.add(seconds=n * request.interval_seconds)
    generated = to_datetime()
    identifier = str(uuid.uuid4())
    index = pd.date_range(start=start, periods=n, freq=f"{request.interval_seconds}s")
    imports, exports = np.array(result.import_w), np.array(result.export_w)
    costs = imports * dt * np.array(request.import_price_eur_per_kwh) / 1000
    revenues = exports * dt * np.array(request.export_price_eur_per_kwh) / 1000
    losses = np.zeros(n)
    load = np.array(request.load_w) * dt
    solution: dict[str, np.ndarray | list[float]] = {
        "grid_energy_wh": (imports - exports) * dt,
        "grid_consumption_energy_wh": imports * dt,
        "grid_feedin_energy_wh": exports * dt,
        "curtailed_pv_energy_wh": np.array(result.curtailed_pv_w) * dt,
        "costs_amt": costs,
        "revenue_amt": revenues,
    }
    prediction = pd.DataFrame(
        {
            "pv_energy_wh": np.array(request.pv_w) * dt,
            "loadforecast_energy_wh": np.array(request.load_w) * dt,
            "elec_price_amt_kwh": request.import_price_eur_per_kwh,
            "feed_in_tariff_amt_kwh": request.export_price_eur_per_kwh,
        },
        index=index,
    )
    plan = EnergyManagementPlan(
        id=f"plan-fleet@{identifier}",
        generated_at=generated,
        instructions=[],
        comment=f"FLEET {result.status}; relative gap={result.relative_gap}",
    )
    for device in request.devices:
        schedule = result.devices[device.device_id]
        charge, discharge = np.array(schedule.charge_w), np.array(schedule.discharge_w)
        losses += dt * (
            charge * (1 - device.charge_efficiency)
            + discharge * (1 / device.discharge_efficiency - 1)
        )
        load += charge * dt
        solution[f"{device.device_id}_soc_factor"] = (
            np.array(schedule.energy_wh[:-1]) / device.capacity_wh
        )
        solution[f"{device.device_id}_energy_wh"] = (charge - discharge) * dt
        solution[f"{device.device_id}_charge_power_w"] = charge
        solution[f"{device.device_id}_discharge_power_w"] = discharge
        modes, factors = [], []
        for c, d in zip(charge, discharge):
            if c > 1e-5:
                mode, factor = BatteryOperationMode.FORCED_CHARGE, c / device.max_charge_power_w
            elif d > 1e-5:
                mode, factor = (
                    BatteryOperationMode.FORCED_DISCHARGE,
                    d / device.max_discharge_power_w,
                )
            else:
                mode, factor = BatteryOperationMode.IDLE, 0.0
            modes.append(mode)
            factors.append(min(1.0, max(0.0, float(factor))))
        for mode in BatteryOperationMode:
            solution[f"{device.device_id}_{mode.lower()}_op_mode"] = [
                float(m == mode) for m in modes
            ]
            solution[f"{device.device_id}_{mode.lower()}_op_factor"] = [
                f if m == mode else 0.0 for m, f in zip(modes, factors)
            ]
        previous = None
        # A final IDLE instruction prevents a stale last-slot setpoint persisting.
        for t, (mode, factor) in enumerate(
            zip(modes + [BatteryOperationMode.IDLE], factors + [0.0])
        ):
            if previous == (mode, factor) and t != n:
                continue
            plan.add_instruction(
                FRBCInstruction(
                    resource_id=device.device_id,
                    actuator_id=device.device_id,
                    execution_time=start.add(seconds=t * request.interval_seconds),
                    operation_mode_id=mode,
                    operation_mode_factor=factor,
                )
            )
            previous = (mode, factor)
    solution["losses_energy_wh"] = losses
    solution["load_energy_wh"] = load
    plan.valid_from, plan.valid_until = start, end
    return OptimizationSolution(
        id=f"solution-fleet@{identifier}",
        generated_at=generated,
        valid_from=start,
        valid_until=end,
        comment=plan.comment,
        total_losses_energy_wh=float(losses.sum()),
        total_revenues_amt=float(revenues.sum()),
        total_costs_amt=float(costs.sum()),
        fitness_score={float(result.objective_eur or 0)},
        prediction=PydanticDateTimeDataFrame.from_dataframe(prediction),
        solution=PydanticDateTimeDataFrame.from_dataframe(pd.DataFrame(solution, index=index)),
    ), plan
