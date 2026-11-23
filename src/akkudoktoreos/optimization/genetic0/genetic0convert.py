"""Solution conversion of GENETIC0 (ADR 0003, 0006).

Turns the result of a run into the optimization solution and the energy management plan, from
the optimization parameters and the result alone: the start, time zone, device IDs and
prediction series come from the parameters' presentation data, not from the configuration,
the predictions or the EMS.
"""

from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from akkudoktoreos.core.emplan import (
    DDBCInstruction,
    EnergyManagementPlan,
    FRBCInstruction,
)
from akkudoktoreos.core.pydantic import PydanticDateTimeDataFrame
from akkudoktoreos.core.types import (
    FillMethod,
)
from akkudoktoreos.devices.devicesabc import (
    ApplianceOperationMode,
    BatteryOperationMode,
)
from akkudoktoreos.optimization.genetic0.genetic0parameters import Genetic0Parameters
from akkudoktoreos.optimization.genetic0.genetic0request import (
    Genetic0InverterRequest,
)
from akkudoktoreos.optimization.genetic0.genetic0solution import Genetic0Solution
from akkudoktoreos.optimization.optimization import OptimizationSolution
from akkudoktoreos.utils.datetimeutil import to_datetime


async def convert(
    parameters: Genetic0Parameters, result: Genetic0Solution
) -> tuple[OptimizationSolution, EnergyManagementPlan]:
    """Convert the result of a run into the optimization solution and the plan.

    Args:
        parameters: The optimization parameters of the run.
        result: The result of the run.

    Returns:
        The optimization solution and the energy management plan.
    """
    return await optimization_solution(parameters, result), energy_management_plan(
        parameters, result
    )


def _battery_operation_from_solution(
    parameters: Genetic0Parameters,
    result: Genetic0Solution,
    ac_charge: float,
    dc_charge: float,
    discharge_allowed: bool,
) -> tuple[BatteryOperationMode, float]:
    """Maps low-level solution to a representative operation mode and factor.

    Args:
        ac_charge (float): Allowed AC-side charging power (relative units).
        dc_charge (float): Allowed DC-side charging power (relative units).
        discharge_allowed (bool): Whether discharging is permitted.

    Returns:
        tuple[BatteryOperationMode, float]: A tuple containing
            - `BatteryOperationMode`: the representative high-level operation mode.
            - `float`: the operation factor corresponding to the active signal.

    Notes:
        - The mapping prioritizes AC charge > DC charge > discharge.
        - Multiple strategies can produce the same low-level signals; this function
          returns a representative mode based on a defined priority order.
    """
    # (0,0,0) → Nothing allowed
    if ac_charge <= 0.0 and dc_charge <= 0.0 and not discharge_allowed:
        return BatteryOperationMode.IDLE, 1.0

    # (0,0,1) → Discharge only
    if ac_charge <= 0.0 and dc_charge <= 0.0 and discharge_allowed:
        return BatteryOperationMode.PEAK_SHAVING, 1.0

    # (ac>0,0,0) → AC charge only
    if ac_charge > 0.0 and dc_charge <= 0.0 and not discharge_allowed:
        return BatteryOperationMode.GRID_SUPPORT_IMPORT, ac_charge

    # (0,dc>0,0) → DC charge only
    if ac_charge <= 0.0 and dc_charge > 0.0 and not discharge_allowed:
        return BatteryOperationMode.NON_EXPORT, dc_charge

    # (ac>0,dc>0,0) → Both charge paths, no discharge
    if ac_charge > 0.0 and dc_charge > 0.0 and not discharge_allowed:
        return BatteryOperationMode.FORCED_CHARGE, ac_charge

    # (ac>0,0,1) → AC charge + discharge - does not make sense
    if ac_charge > 0.0 and dc_charge <= 0.0 and discharge_allowed:
        raise ValueError(
            f"Illegal state: ac_charge: {ac_charge} and discharge_allowed: {discharge_allowed}"
        )

    # (0,dc>0,1) → DC charge + discharge
    if ac_charge <= 0.0 and dc_charge > 0.0 and discharge_allowed:
        return BatteryOperationMode.SELF_CONSUMPTION, dc_charge

    # (ac>0,dc>0,1) → Fully flexible - does not make sense
    if ac_charge > 0.0 and dc_charge > 0.0 and discharge_allowed:
        raise ValueError(
            f"Illegal state: ac_charge: {ac_charge} and discharge_allowed: {discharge_allowed}"
        )

    # Fallback → safe idle
    return BatteryOperationMode.IDLE, 1.0


def _inverter_parameters(result: Genetic0Solution) -> Optional[Genetic0InverterRequest]:
    """Return the inverter parameters the solution was computed with."""
    return result.parameters.inverter


def _soc_clamped_operation_factors(
    parameters: Genetic0Parameters,
    result: Genetic0Solution,
    ac_charge: float,
    dc_charge: float,
    discharge_allowed: bool,
    soc_pct: float,
) -> tuple[float, float, bool]:
    """Clamp raw genetic gene values by the battery's actual SOC at that hour.

    The raw gene values represent the optimizer's *intent* and are stored
    verbatim in the ``genetic_*`` solution columns.  This method derives
    the *effective* values that can physically be executed given the
    battery's state of charge, used for the ``battery1_*_op_*`` columns
    and for ``energy_management_plan`` instructions.

    Clamping rules:
      - AC charge factor: scaled down proportionally when the battery
        headroom (max_soc − current_soc) is smaller than what the
        commanded factor would store in one hour.  Set to 0 when full.
      - DC charge factor (PV): zeroed when battery is at or above max SOC
        (the inverter curtails automatically, but this makes intent clear).
      - Discharge: blocked when SOC is at or below min SOC.

    The battery values are the optimizer parameters the solution was computed
    with (capacity, efficiency, power limit and SOC range as resolved from the
    battery profile), not the raw battery settings.
    """
    bat = result.parameters.pv_battery
    if bat is None:
        return ac_charge, dc_charge, discharge_allowed

    min_soc = float(bat.min_soc_percentage)
    max_soc = float(bat.max_soc_percentage)
    capacity_wh = float(bat.capacity_wh)
    ch_eff = float(bat.charging_efficiency)
    # Same fallback as the GENETIC0 battery simulation.
    bat_max_charge_power_w = (
        float(bat.max_charge_power_w) if bat.max_charge_power_w is not None else capacity_wh
    )
    headroom_wh = max(0.0, (max_soc - soc_pct) / 100.0 * capacity_wh)

    # --- AC charge: scale to available headroom ---
    effective_ac = ac_charge
    if effective_ac > 0.0:
        if headroom_wh <= 0.0:
            effective_ac = 0.0
        else:
            inv = _inverter_parameters(result)
            ac_to_dc_eff = float(inv.ac_to_dc_efficiency) if inv else 1.0
            max_ac_cp_w = (
                float(inv.max_ac_charge_power_w)
                if inv and inv.max_ac_charge_power_w is not None
                else bat_max_charge_power_w
            )
            max_dc_per_h_wh = effective_ac * max_ac_cp_w * ac_to_dc_eff * ch_eff
            if max_dc_per_h_wh > headroom_wh:
                effective_ac = effective_ac * (headroom_wh / max_dc_per_h_wh)

    # --- DC charge (PV): zero when battery is full ---
    effective_dc = dc_charge
    if effective_dc > 0.0 and headroom_wh <= 0.0:
        effective_dc = 0.0

    # --- Discharge: block at min SOC ---
    effective_dis = discharge_allowed and (soc_pct > min_soc)

    return effective_ac, effective_dc, effective_dis


async def optimization_solution(
    parameters: Genetic0Parameters,
    result: Genetic0Solution,
) -> OptimizationSolution:
    """Provide the genetic solution as a general optimization solution.

    The battery modes are controlled by the grid control triggers:
    - ac_charge: charge from grid
    - discharge_allowed: discharge to grid

    The following battery modes are supported:
    - SELF_CONSUMPTION:    ac_charge == 0 and discharge_allowed == 0
    - GRID_SUPPORT_EXPORT: ac_charge == 0 and discharge_allowed == 1
    - GRID_SUPPORT_IMPORT: ac_charge  > 0 and discharge_allowed == 0 or 1

    Args:
        parameters: The optimization parameters of the run.
        result: The result of the run.

    Returns:
        The optimization solution.
    """
    start_datetime = parameters.presentation.start_datetime
    start_day_hour = to_datetime(start_datetime, in_timezone=parameters.presentation.timezone).hour
    interval_hours = 1
    power_to_energy_per_interval_factor = 1.0

    # --- Create index based on list length and interval ---
    # Ensure we only use the minimum of results and commands if differing
    periods = min(len(result.result.costs_per_hour), len(result.ac_charge) - start_day_hour)
    time_index = pd.date_range(
        start=start_datetime,
        periods=periods,
        freq=f"{interval_hours}h",
    )
    n_points = len(time_index)

    # Fill solution into dataframe with correct column names
    # - load_energy_wh: Load of all energy consumers in wh"
    # - grid_energy_wh: Grid energy feed in (negative) or consumption (positive) in wh"
    # - costs_amt: Costs in money amount"
    # - revenue_amt: Revenue in money amount"
    # - losses_energy_wh: Energy losses in wh"
    # - <device-id>_<operation>_op_mode: Operation mode of the device (1.0 when active)."
    # - <device-id>_<operation>_op_factor: Operation mode factor of the device."
    # - <device-id>_soc_factor: State of charge of a battery/ electric vehicle device as factor of total capacity."
    # - <device-id>_energy_wh: Energy consumption (positive) of a device in wh."

    solution = pd.DataFrame(
        {
            "date_time": time_index,
            # result starts at start_day_hour
            "load_energy_wh": result.result.load_wh_per_hour[:n_points],
            "grid_feedin_energy_wh": result.result.grid_feed_in_wh_per_hour[:n_points],
            "grid_consumption_energy_wh": result.result.grid_consumption_wh_per_hour[:n_points],
            "costs_amt": result.result.costs_per_hour[:n_points],
            "revenue_amt": result.result.revenue_per_hour[:n_points],
            "losses_energy_wh": result.result.losses_per_hour[:n_points],
        },
        index=time_index,
    )

    # Add battery data
    battery_device_id = parameters.presentation.battery_device_id
    solution[f"{battery_device_id}_soc_factor"] = [
        v / 100
        for v in result.result.battery_soc_per_hour[:n_points]  # result starts at start_day_hour
    ]
    operation: dict[str, list[float]] = {
        "genetic_ac_charge_factor": [],
        "genetic_dc_charge_factor": [],
        "genetic_discharge_allowed_factor": [],
    }
    # ac_charge, dc_charge, discharge_allowed start at hour 0 of start day
    for hour_idx, rate in enumerate(result.ac_charge):
        if hour_idx < start_day_hour:
            continue
        if hour_idx >= start_day_hour + n_points:
            break
        ac_charge_hour = result.ac_charge[hour_idx]
        dc_charge_hour = result.dc_charge[hour_idx]
        discharge_allowed_hour = bool(result.discharge_allowed[hour_idx])

        # Raw genetic gene values — optimizer intent, stored verbatim
        operation["genetic_ac_charge_factor"].append(ac_charge_hour)
        operation["genetic_dc_charge_factor"].append(dc_charge_hour)
        operation["genetic_discharge_allowed_factor"].append(float(discharge_allowed_hour))

        # SOC-clamped effective values — what can physically be executed at
        # this hour given the expected battery state of charge.
        result_idx = hour_idx - start_day_hour
        soc_h_pct = (
            result.result.battery_soc_per_hour[result_idx]
            if result_idx < len(result.result.battery_soc_per_hour)
            else 0.0
        )
        eff_ac, eff_dc, eff_dis = _soc_clamped_operation_factors(
            parameters, result, ac_charge_hour, dc_charge_hour, discharge_allowed_hour, soc_h_pct
        )
        operation_mode, operation_mode_factor = _battery_operation_from_solution(
            parameters, result, eff_ac, eff_dc, eff_dis
        )
        for mode in BatteryOperationMode:
            mode_key = f"{battery_device_id}_{mode.lower()}_op_mode"
            factor_key = f"{battery_device_id}_{mode.lower()}_op_factor"
            if mode_key not in operation.keys():
                operation[mode_key] = []
                operation[factor_key] = []
            if mode == operation_mode:
                operation[mode_key].append(1.0)
                operation[factor_key].append(operation_mode_factor)
            else:
                operation[mode_key].append(0.0)
                operation[factor_key].append(0.0)
    for key in operation.keys():
        if len(operation[key]) != n_points:
            error_msg = (
                f"instruction {key} has invalid length {len(operation[key])} - expected {n_points}"
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        solution[key] = operation[key]

    # Add EV battery solution
    # ev_charge_hours_float start at hour 0 of start day
    # result.ev_soc_per_hour start at start_datetime.hour
    if result.ev_obj:
        ev_device_id = parameters.presentation.ev_device_id
        if result.ev_charge_hours_float is None:
            # Electric vehicle is full enough. No load times.
            solution[f"{ev_device_id}_soc_factor"] = [
                result.ev_obj.initial_soc_percentage / 100.0
            ] * n_points
            solution["genetic_ev_charge_factor"] = [0.0] * n_points
            # operation modes
            operation_mode = BatteryOperationMode.IDLE
            for mode in BatteryOperationMode:
                mode_key = f"{ev_device_id}_{mode.lower()}_op_mode"
                factor_key = f"{ev_device_id}_{mode.lower()}_op_factor"
                if mode == operation_mode:
                    solution[mode_key] = [1.0] * n_points
                    solution[factor_key] = [1.0] * n_points
                else:
                    solution[mode_key] = [0.0] * n_points
                    solution[factor_key] = [0.0] * n_points
        else:
            solution[f"{ev_device_id}_soc_factor"] = [
                v / 100 for v in result.result.ev_soc_per_hour[:n_points]
            ]
            operation = {
                "genetic_ev_charge_factor": [],
            }
            for hour_idx, rate in enumerate(result.ev_charge_hours_float):
                if hour_idx < start_day_hour:
                    continue
                if hour_idx >= start_day_hour + n_points:
                    break
                operation["genetic_ev_charge_factor"].append(rate)
                operation_mode, operation_mode_factor = _battery_operation_from_solution(
                    parameters, result, rate, 0.0, False
                )
                for mode in BatteryOperationMode:
                    mode_key = f"{ev_device_id}_{mode.lower()}_op_mode"
                    factor_key = f"{ev_device_id}_{mode.lower()}_op_factor"
                    if mode_key not in operation.keys():
                        operation[mode_key] = []
                        operation[factor_key] = []
                    if mode == operation_mode:
                        operation[mode_key].append(1.0)
                        operation[factor_key].append(operation_mode_factor)
                    else:
                        operation[mode_key].append(0.0)
                        operation[factor_key].append(0.0)
            for key in operation.keys():
                if len(operation[key]) != n_points:
                    error_msg = f"instruction {key} has invalid length {len(operation[key])} - expected {n_points}"
                    logger.error(error_msg)
                    raise ValueError(error_msg)
                solution[key] = operation[key]

    # Add home appliance data
    if parameters.presentation.home_appliance_enabled:
        # Use the preparation's decision and not result.washingstart, as washingstart
        # may be None (no start)
        # even if configured to be started.
        homeappliance_device_id = parameters.presentation.home_appliance_device_id
        # result starts at start_day_hour
        solution[f"{homeappliance_device_id}_energy_wh"] = result.result.home_appliance_wh_per_hour[
            :n_points
        ]
        operation = {
            f"{homeappliance_device_id}_run_op_mode": [],
            f"{homeappliance_device_id}_run_op_factor": [],
            f"{homeappliance_device_id}_off_op_mode": [],
            f"{homeappliance_device_id}_off_op_factor": [],
        }
        for hour_idx, energy in enumerate(solution[f"{homeappliance_device_id}_energy_wh"]):
            if energy > 0.0:
                operation[f"{homeappliance_device_id}_run_op_mode"].append(1.0)
                operation[f"{homeappliance_device_id}_run_op_factor"].append(1.0)
                operation[f"{homeappliance_device_id}_off_op_mode"].append(0.0)
                operation[f"{homeappliance_device_id}_off_op_factor"].append(0.0)
            else:
                operation[f"{homeappliance_device_id}_run_op_mode"].append(0.0)
                operation[f"{homeappliance_device_id}_run_op_factor"].append(0.0)
                operation[f"{homeappliance_device_id}_off_op_mode"].append(1.0)
                operation[f"{homeappliance_device_id}_off_op_factor"].append(1.0)
        for key in operation.keys():
            if len(operation[key]) != n_points:
                error_msg = f"instruction {key} has invalid length {len(operation[key])} - expected {n_points}"
                logger.error(error_msg)
                raise ValueError(error_msg)
            solution[key] = operation[key]

    # Fill prediction into dataframe with correct column names
    # - pvforecast_ac_energy_wh_energy_wh: PV energy prediction (positive) in wh
    # - elec_price_amt_kwh: Electricity price prediction in money per kwh
    # - weather_temp_air_celcius: Temperature in °C"
    # - loadforecast_energy_wh: Load energy prediction in wh
    # - loadakkudoktor_std_energy_wh: Load energy standard deviation prediction in wh
    # - loadakkudoktor_mean_energy_wh: Load mean energy prediction in wh
    prediction = pd.DataFrame(
        {
            "date_time": time_index,
        },
        index=time_index,
    )

    prediction_specs: list[tuple[str, FillMethod, str, float]] = [
        (
            "pvforecast_ac_power",
            "linear",
            "pvforecast_ac_energy_wh",
            power_to_energy_per_interval_factor,
        ),
        (
            "pvforecast_dc_power",
            "linear",
            "pvforecast_dc_energy_wh",
            power_to_energy_per_interval_factor,
        ),
        (
            "elecprice_marketprice_wh",
            "ffill",
            "elec_price_amt_kwh",
            1000.0,
        ),
        (
            "feed_in_tariff_wh",
            "ffill",
            "feed_in_tariff_amt_kwh",
            1000.0,
        ),
        (
            "weather_temp_air",
            "linear",
            "weather_air_temp_celcius",
            1.0,
        ),
        (
            "loadforecast_power_w",
            "linear",
            "loadforecast_energy_wh",
            power_to_energy_per_interval_factor,
        ),
        (
            "loadakkudoktor_std_power_w",
            "linear",
            "loadakkudoktor_std_energy_wh",
            power_to_energy_per_interval_factor,
        ),
        (
            "loadakkudoktor_mean_power_w",
            "linear",
            "loadakkudoktor_mean_energy_wh",
            power_to_energy_per_interval_factor,
        ),
    ]

    # The series were read by the parameter preparation, for exactly these hours.
    for pred_key, _, pred_solution_key, pred_solution_factor in prediction_specs:
        series = parameters.presentation.prediction_series.get(pred_key)
        if series is None:
            continue
        array = np.asarray(series[:n_points], dtype=float)
        if array.size == n_points and array.size > 0 and not np.any(pd.isna(array)):
            prediction[pred_solution_key] = (array * pred_solution_factor).tolist()

    optimization_solution = OptimizationSolution(
        id=f"optimization-genetic@{to_datetime(as_string=True)}",
        generated_at=to_datetime(),
        comment="Optimization solution derived from GeneticSolution.",
        valid_from=start_datetime,
        valid_until=start_datetime.add(hours=parameters.settings.horizon_hours),
        total_losses_energy_wh=result.result.total_losses,
        total_revenues_amt=result.result.total_revenue,
        total_costs_amt=result.result.total_costs,
        fitness_score={
            result.result.total_costs,
        },
        prediction=PydanticDateTimeDataFrame.from_dataframe(prediction),
        solution=PydanticDateTimeDataFrame.from_dataframe(solution),
    )

    return optimization_solution


def energy_management_plan(
    parameters: Genetic0Parameters,
    result: Genetic0Solution,
) -> EnergyManagementPlan:
    """Provide the genetic solution as an energy management plan.

    Args:
        parameters: The optimization parameters of the run.
        result: The result of the run.

    Returns:
        The energy management plan.
    """
    start_datetime = parameters.presentation.start_datetime
    start_day_hour = to_datetime(start_datetime, in_timezone=parameters.presentation.timezone).hour
    plan = EnergyManagementPlan(
        id=f"plan-genetic@{to_datetime(as_string=True)}",
        generated_at=to_datetime(),
        instructions=[],
        comment="Energy management plan derived from GeneticSolution.",
    )

    # Add battery instructions (fill rate based control)
    last_operation_mode: Optional[str] = None
    last_operation_mode_factor: Optional[float] = None
    resource_id = parameters.presentation.battery_device_id
    # ac_charge, dc_charge, discharge_allowed start at hour 0 of start day
    logger.debug("BAT: {} - {}", resource_id, result.ac_charge[start_day_hour:])
    for hour_idx, rate in enumerate(result.ac_charge):
        if hour_idx < start_day_hour:
            continue
        # Derive SOC-clamped effective factors so that FRBCInstruction
        # operation_mode_factor reflects what can physically be executed,
        # while the raw genetic gene values are preserved in the solution
        # dataframe (genetic_*_factor columns).
        result_idx = hour_idx - start_day_hour
        soc_h_pct = (
            result.result.battery_soc_per_hour[result_idx]
            if result_idx < len(result.result.battery_soc_per_hour)
            else 0.0
        )
        eff_ac, eff_dc, eff_dis = _soc_clamped_operation_factors(
            parameters,
            result,
            result.ac_charge[hour_idx],
            result.dc_charge[hour_idx],
            bool(result.discharge_allowed[hour_idx]),
            soc_h_pct,
        )
        operation_mode, operation_mode_factor = _battery_operation_from_solution(
            parameters, result, eff_ac, eff_dc, eff_dis
        )
        if (
            operation_mode == last_operation_mode
            and operation_mode_factor == last_operation_mode_factor
        ):
            # Skip, we already added the instruction
            continue
        last_operation_mode = operation_mode
        last_operation_mode_factor = operation_mode_factor
        execution_time = start_datetime.add(hours=hour_idx - start_day_hour)
        plan.add_instruction(
            FRBCInstruction(
                resource_id=resource_id,
                execution_time=execution_time,
                actuator_id=resource_id,
                operation_mode_id=operation_mode,
                operation_mode_factor=operation_mode_factor,
            )
        )

    # Add EV battery instructions (fill rate based control)
    # ev_charge_hours_float start at hour 0 of start day
    if result.ev_obj:
        resource_id = parameters.presentation.ev_device_id
        if result.ev_charge_hours_float is None:
            # Electric vehicle is full enough. No load times.
            logger.debug("EV: {} - SoC >= min, no optimization", resource_id)
            plan.add_instruction(
                FRBCInstruction(
                    resource_id=resource_id,
                    execution_time=start_datetime,
                    actuator_id=resource_id,
                    operation_mode_id=BatteryOperationMode.IDLE,
                    operation_mode_factor=1.0,
                )
            )
        else:
            last_operation_mode = None
            last_operation_mode_factor = None
            logger.debug("EV: {} - {}", resource_id, result.ev_charge_hours_float[start_day_hour:])
            for hour_idx, rate in enumerate(result.ev_charge_hours_float):
                if hour_idx < start_day_hour:
                    continue
                operation_mode, operation_mode_factor = _battery_operation_from_solution(
                    parameters, result, rate, 0.0, False
                )
                if (
                    operation_mode == last_operation_mode
                    and operation_mode_factor == last_operation_mode_factor
                ):
                    # Skip, we already added the instruction
                    continue
                last_operation_mode = operation_mode
                last_operation_mode_factor = operation_mode_factor
                execution_time = start_datetime.add(hours=hour_idx - start_day_hour)
                plan.add_instruction(
                    FRBCInstruction(
                        resource_id=resource_id,
                        execution_time=execution_time,
                        actuator_id=resource_id,
                        operation_mode_id=operation_mode,
                        operation_mode_factor=operation_mode_factor,
                    )
                )

    # Add home appliance instructions (demand driven based control)
    if parameters.presentation.home_appliance_enabled:
        # Use the preparation's decision and not result.washingstart, as washingstart
        # may be None (no start)
        # even if configured to be started.
        resource_id = parameters.presentation.home_appliance_device_id
        last_energy: Optional[float] = None
        for hours, energy in enumerate(result.result.home_appliance_wh_per_hour):
            # hours starts at start_datetime with 0
            if energy is None:
                raise ValueError(
                    f"Unexpected value {energy} in {result.result.home_appliance_wh_per_hour}"
                )
            if last_energy is None or energy != last_energy:
                if energy > 0.0:
                    operation_mode = ApplianceOperationMode.RUN  # type: ignore[assignment]
                else:
                    operation_mode = ApplianceOperationMode.OFF  # type: ignore[assignment]
                operation_mode_factor = 1.0
                execution_time = start_datetime.add(hours=hours)
                plan.add_instruction(
                    DDBCInstruction(
                        resource_id=resource_id,
                        execution_time=execution_time,
                        actuator_id=resource_id,
                        operation_mode_id=operation_mode,
                        operation_mode_factor=operation_mode_factor,
                    )
                )
                last_energy = energy

    return plan
