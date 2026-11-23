"""Solution conversion of GENETIC (ADR 0003, 0006).

Turns the result of a run into the optimization solution and the energy management plan, from
the optimization parameters (run start, time zone) and the result (genes, simulation result and
the forecast and device parameters it echoes) only.
"""

from typing import Optional, cast

import numpy as np
import pandas as pd
from loguru import logger

from akkudoktoreos.core.emplan import (
    DDBCInstruction,
    EnergyManagementPlan,
    FRBCInstruction,
)
from akkudoktoreos.core.pydantic import PydanticDateTimeDataFrame
from akkudoktoreos.devices.devicesabc import (
    ApplianceOperationMode,
    BatteryOperationMode,
)
from akkudoktoreos.optimization.genetic.geneticparameters import GeneticParameters
from akkudoktoreos.optimization.genetic.geneticrequest import (
    GeneticInverterRequest,
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticresult import GeneticResult
from akkudoktoreos.optimization.optimization import OptimizationSolution
from akkudoktoreos.utils.datetimeutil import to_datetime


async def convert(
    parameters: GeneticParameters, result: GeneticResult
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


def _battery_device_id(result: GeneticResult) -> str:
    """Device ID of the run's battery."""
    parameters = result.request
    if parameters is not None and parameters.pv_battery is not None:
        return parameters.pv_battery.device_id
    return "battery1"


def _ev_device_id(result: GeneticResult) -> str:
    """Device ID of the run's electric vehicle."""
    parameters = result.request
    if parameters is not None and parameters.ev is not None:
        return parameters.ev.device_id
    return "ev1"


def _battery_operation_from_solution(
    ac_charge: float,
    dc_charge: float,
    discharge_allowed: bool,
    battery_grid_export_allowed: bool = False,
    battery_grid_export_factor: float = 1.0,
) -> tuple[BatteryOperationMode, float]:
    """Maps low-level solution to a representative operation mode and factor.

    Args:
        ac_charge (float): Allowed AC-side charging power (relative units).
        dc_charge (float): Allowed DC-side charging power (relative units).
        discharge_allowed (bool): Whether discharging to local load is permitted.
        battery_grid_export_allowed (bool): Whether discharge into the grid is permitted.
        battery_grid_export_factor (float): Export level as factor of the rated
            discharge power ]0.0 ... 1.0]. Becomes the operation factor of
            GRID_SUPPORT_EXPORT.

    Returns:
        tuple[BatteryOperationMode, float]: A tuple containing
            - `BatteryOperationMode`: the representative high-level operation mode.
            - `float`: the operation factor corresponding to the active signal.

    Notes:
        - Explicit grid export is separate from local-load discharge.
        - The mapping prioritizes export > AC charge > DC charge > discharge.
        - Multiple strategies can produce the same low-level signals; this function
          returns a representative mode based on a defined priority order.
    """
    # (0,0,0) → Nothing allowed
    if (
        ac_charge <= 0.0
        and dc_charge <= 0.0
        and not discharge_allowed
        and not battery_grid_export_allowed
    ):
        return BatteryOperationMode.IDLE, 1.0

    if battery_grid_export_allowed:
        if ac_charge > 0.0 or dc_charge > 0.0:
            raise ValueError(
                "Illegal state: battery_grid_export_allowed cannot be combined with charging"
            )
        return BatteryOperationMode.GRID_SUPPORT_EXPORT, min(
            max(float(battery_grid_export_factor), 0.0), 1.0
        )

    # (0,0,1) -> Discharge for local load only: serve local demand, no grid
    # charging and no export.
    if ac_charge <= 0.0 and dc_charge <= 0.0 and discharge_allowed:
        return BatteryOperationMode.SELF_CONSUMPTION, 1.0

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


def _inverter_parameters_list(
    parameters: Optional[GeneticOptimizationParameters],
) -> list[GeneticInverterRequest]:
    """Return the inverter parameters the solution was computed with, as a list.

    Args:
        parameters: The forecast and device parameters the result echoes.

    Returns:
        The inverter, or no inverter.
    """
    if parameters is not None and parameters.inverter is not None:
        return [parameters.inverter]
    return []


def _soc_clamped_operation_factors(
    result: GeneticResult,
    ac_charge: float,
    dc_charge: float,
    discharge_allowed: bool,
    soc_pct: float,
    battery_grid_export_allowed: bool = False,
    slot_fraction: float = 1.0,
) -> tuple[float, float, bool, bool]:
    """Clamp raw genetic gene values by the battery's actual SOC at that hour.

    The raw gene values represent the optimizer's *intent* and are stored
    verbatim in the ``genetic_*`` solution columns.  This method derives
    the *effective* values that can physically be executed given the
    battery's state of charge, used for the ``battery1_*_op_*`` columns
    and for ``energy_management_plan`` instructions.

    Clamping rules:
      - AC charge factor: scaled down proportionally when the battery
        headroom (max_soc − current_soc) is smaller than what the
        commanded factor would store in one optimization slot.  Set to 0 when full.
      - DC charge factor (PV): zeroed when battery is at or above max SOC
        (the inverter curtails automatically, but this makes intent clear).
      - Discharge: blocked when SOC is at or below min SOC.
      - Battery grid export: blocked when SOC is at or below min SOC.

    ``slot_fraction`` is the part of the slot the factors apply to:
    ``first_slot_fraction`` for the running first slot, else 1.0. The
    headroom check of the AC charge factor uses only that part of the slot.
    """
    parameters = result.request
    bat_list = []
    if parameters is not None and parameters.pv_battery is not None:
        bat_list = [parameters.pv_battery]
    if not bat_list:
        return ac_charge, dc_charge, discharge_allowed, battery_grid_export_allowed

    bat = bat_list[0]
    min_soc = float(bat.min_soc_percentage)
    max_soc = float(bat.max_soc_percentage)
    capacity_wh = float(bat.capacity_wh)
    ch_eff = float(bat.charging_efficiency)
    headroom_wh = max(0.0, (max_soc - soc_pct) / 100.0 * capacity_wh)

    # --- AC charge: scale to available headroom ---
    effective_ac = ac_charge
    if effective_ac > 0.0:
        if headroom_wh <= 0.0:
            effective_ac = 0.0
        else:
            inv_list = _inverter_parameters_list(parameters)
            ac_to_dc_eff = float(inv_list[0].ac_to_dc_efficiency) if inv_list else 1.0
            max_ac_cp_w = (
                float(inv_list[0].max_ac_charge_power_w)
                if inv_list and inv_list[0].max_ac_charge_power_w is not None
                else float(cast(float, bat.max_charge_power_w))
            )
            # Energy storable in one optimization slot, not per hour: scale the
            # power [W] by the slot duration (1.0 hourly, 0.25 at 15 min).
            slot_duration_h = float(result.interval_seconds) / 3600.0 * slot_fraction
            max_dc_per_slot_wh = (
                effective_ac * max_ac_cp_w * slot_duration_h * ac_to_dc_eff * ch_eff
            )
            if max_dc_per_slot_wh > headroom_wh:
                effective_ac = effective_ac * (headroom_wh / max_dc_per_slot_wh)

    # --- DC charge (PV): zero when battery is full ---
    effective_dc = dc_charge
    if effective_dc > 0.0 and headroom_wh <= 0.0:
        effective_dc = 0.0

    # --- Discharge: block at min SOC ---
    effective_dis = discharge_allowed and (soc_pct > min_soc)
    effective_grid_export = battery_grid_export_allowed and (soc_pct > min_soc)

    return effective_ac, effective_dc, effective_dis, effective_grid_export


async def optimization_solution(
    parameters: GeneticParameters, result: GeneticResult
) -> OptimizationSolution:
    """Provide the result of a run as a general optimization solution.

    The battery modes are controlled by the grid control triggers:

    - ac_charge: charge from grid
    - discharge_allowed: discharge to local load
    - battery_grid_export_allowed: discharge to grid

    The following battery modes are supported:

    - SELF_CONSUMPTION: discharge_allowed == 1 and ac_charge == 0 (with or without dc_charge)
    - GRID_SUPPORT_EXPORT: battery_grid_export_allowed == 1
    - GRID_SUPPORT_IMPORT: ac_charge > 0 and discharge_allowed == 0 or 1

    Args:
        parameters: The optimization parameters of the run (run start, time zone).
        result: The result of the run.

    Returns:
        The optimization solution.
    """
    start_datetime = result.start_solution_datetime or parameters.settings.start_datetime
    # New controls use the run-relative control horizon; old payloads retain a midnight prefix.
    # entries indexed by slot (slot 0 == 00:00 local). Index this serializer
    # by slot too. At the default interval of 3600 s slots_per_hour == 1 and
    # this is the established hourly behaviour.
    interval_s = result.interval_seconds
    start_local = start_datetime.in_timezone(cast(str, parameters.settings.timezone))
    start_day_slot = (
        0
        if result.controls_start_at_now
        else int((start_local - start_local.start_of("day")).total_seconds() // interval_s)
    )
    # power [W] -> energy per slot [Wh]: multiply by the slot duration in hours.

    # --- Create index based on list length and interval ---
    # Ensure we only use the minimum of results and commands if differing
    periods = min(len(result.result.costs_per_hour), len(result.ac_charge) - start_day_slot)
    time_index = pd.date_range(
        start=start_datetime,
        periods=periods,
        freq=f"{interval_s}s",
    )
    n_points = len(time_index)
    end_datetime = start_datetime.add(seconds=interval_s * n_points)

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
            # result starts at start_day_slot
            "load_energy_wh": result.result.load_wh_per_hour[:n_points],
            "grid_feedin_energy_wh": result.result.grid_feed_in_wh_per_hour[:n_points],
            "grid_consumption_energy_wh": result.result.grid_consumption_wh_per_hour[:n_points],
            "costs_amt": result.result.costs_per_hour[:n_points],
            "revenue_amt": result.result.revenue_per_hour[:n_points],
            "losses_energy_wh": result.result.losses_per_hour[:n_points],
        },
        index=time_index,
    )

    if result.request.pv_battery is not None:
        # Add battery data
        battery_device_id = _battery_device_id(result)
        solution[f"{battery_device_id}_soc_factor"] = [
            v / 100
            for v in result.result.battery_soc_per_hour[
                :n_points
            ]  # result starts at start_day_slot
        ]
        operation: dict[str, list[float]] = {
            "genetic_ac_charge_factor": [],
            "genetic_dc_charge_factor": [],
            "genetic_discharge_allowed_factor": [],
            "genetic_battery_grid_export_allowed_factor": [],
        }
        # ac_charge, dc_charge, discharge_allowed start at hour 0 of start day
        for hour_idx, rate in enumerate(result.ac_charge):
            if hour_idx < start_day_slot:
                continue
            if hour_idx >= start_day_slot + n_points:
                break
            ac_charge_hour = result.ac_charge[hour_idx]
            dc_charge_hour = result.dc_charge[hour_idx]
            discharge_allowed_hour = bool(result.discharge_allowed[hour_idx])
            battery_grid_export_allowed_hour = (
                bool(result.battery_grid_export_allowed[hour_idx])
                if hour_idx < len(result.battery_grid_export_allowed)
                else False
            )
            # Solutions written before graded export carry no factor array; they
            # exported at full power wherever the signal was set.
            battery_grid_export_factor_hour = (
                float(result.battery_grid_export_factor[hour_idx])
                if hour_idx < len(result.battery_grid_export_factor)
                else (1.0 if battery_grid_export_allowed_hour else 0.0)
            )

            # Raw genetic gene values — optimizer intent, stored verbatim
            operation["genetic_ac_charge_factor"].append(ac_charge_hour)
            operation["genetic_dc_charge_factor"].append(dc_charge_hour)
            operation["genetic_discharge_allowed_factor"].append(float(discharge_allowed_hour))
            operation["genetic_battery_grid_export_allowed_factor"].append(
                battery_grid_export_factor_hour
            )

            # SOC-clamped effective values — what can physically be executed at
            # this hour given the expected battery state of charge.
            result_idx = hour_idx - start_day_slot
            soc_h_pct = (
                result.result.battery_soc_per_hour[result_idx]
                if result_idx < len(result.result.battery_soc_per_hour)
                else 0.0
            )
            eff_ac, eff_dc, eff_dis, eff_grid_export = _soc_clamped_operation_factors(
                result,
                ac_charge_hour,
                dc_charge_hour,
                discharge_allowed_hour,
                soc_h_pct,
                battery_grid_export_allowed_hour,
                result.first_slot_fraction if result_idx == 0 else 1.0,
            )
            operation_mode, operation_mode_factor = _battery_operation_from_solution(
                eff_ac, eff_dc, eff_dis, eff_grid_export, battery_grid_export_factor_hour
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
                error_msg = f"instruction {key} has invalid length {len(operation[key])} - expected {n_points}"
                logger.error(error_msg)
                raise ValueError(error_msg)
            solution[key] = operation[key]

    # Add EV battery solution
    # eautocharge_hours_float start at hour 0 of start day
    # result.EAuto_SoC_pro_Stunde start at start_datetime.hour
    if result.ev_obj:
        ev_device_id = _ev_device_id(result)
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
                if hour_idx < start_day_slot:
                    continue
                if hour_idx >= start_day_slot + n_points:
                    break
                operation["genetic_ev_charge_factor"].append(rate)
                operation_mode, operation_mode_factor = _battery_operation_from_solution(
                    rate, 0.0, False
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

    # Add home appliance data, one block of columns per device. Per-device
    # energy arrays start at start_day_slot, like the other result arrays.
    for device_id, energy_wh in result.result.home_appliance_energy_wh.items():
        solution[f"{device_id}_energy_wh"] = energy_wh[:n_points]
        operation = {
            f"{device_id}_run_op_mode": [],
            f"{device_id}_run_op_factor": [],
            f"{device_id}_off_op_mode": [],
            f"{device_id}_off_op_factor": [],
        }
        running_slots = result.result.home_appliance_running.get(device_id, [])
        for hour_idx, energy in enumerate(solution[f"{device_id}_energy_wh"]):
            running = (
                running_slots[hour_idx]
                if hour_idx < len(running_slots)
                else bool(energy and energy > 0.0)
            )
            if running:
                operation[f"{device_id}_run_op_mode"].append(1.0)
                operation[f"{device_id}_run_op_factor"].append(1.0)
                operation[f"{device_id}_off_op_mode"].append(0.0)
                operation[f"{device_id}_off_op_factor"].append(0.0)
            else:
                operation[f"{device_id}_run_op_mode"].append(0.0)
                operation[f"{device_id}_run_op_factor"].append(0.0)
                operation[f"{device_id}_off_op_mode"].append(1.0)
                operation[f"{device_id}_off_op_factor"].append(1.0)
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
    if result.controls_start_at_now:
        snapshot = result.request.ems
        prediction["pvforecast_ac_energy_wh"] = snapshot.pv_forecast_wh[:n_points]
        prediction["loadforecast_energy_wh"] = snapshot.total_load[:n_points]
        prediction["elec_price_amt_kwh"] = (
            np.asarray(snapshot.electricity_price_per_wh[:n_points]) * 1000
        )
        tariffs = snapshot.feed_in_tariff_per_wh
        prediction["feed_in_tariff_amt_kwh"] = (
            np.asarray(tariffs[:n_points]) * 1000 if isinstance(tariffs, list) else tariffs * 1000
        )
    if result.controls_start_at_now and result.request.temperature_forecast is not None:
        prediction["weather_air_temp_celcius"] = result.request.temperature_forecast[:n_points]

    optimization_solution = OptimizationSolution(
        id=f"optimization-genetic@{to_datetime(as_string=True)}",
        generated_at=to_datetime(),
        comment="Optimization solution derived from GeneticSolution.",
        valid_from=start_datetime,
        valid_until=end_datetime,
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
    parameters: GeneticParameters, result: GeneticResult
) -> EnergyManagementPlan:
    """Provide the result of a run as an energy management plan.

    Args:
        parameters: The optimization parameters of the run (run start, time zone).
        result: The result of the run.

    Returns:
        The energy management plan.
    """
    start_datetime = result.start_solution_datetime or parameters.settings.start_datetime
    # Index by slot, not hour (mirrors optimization_solution). At the default
    # interval of 3600 s this reduces to the start hour-of-day.
    interval_s = result.interval_seconds
    start_local = start_datetime.in_timezone(cast(str, parameters.settings.timezone))
    start_day_slot = (
        0
        if result.controls_start_at_now
        else int((start_local - start_local.start_of("day")).total_seconds() // interval_s)
    )
    plan = EnergyManagementPlan(
        id=f"plan-genetic@{to_datetime(as_string=True)}",
        generated_at=to_datetime(),
        instructions=[],
        comment="Energy management plan derived from GeneticSolution.",
    )

    if result.request.pv_battery is not None:
        # Add battery instructions (fill rate based control)
        last_operation_mode: Optional[str] = None
        last_operation_mode_factor: Optional[float] = None
        resource_id = _battery_device_id(result)
        # ac_charge, dc_charge, discharge_allowed start at hour 0 of start day
        logger.debug("BAT: {} - {}", resource_id, result.ac_charge[start_day_slot:])
        for hour_idx, rate in enumerate(result.ac_charge):
            if hour_idx < start_day_slot:
                continue
            # Derive SOC-clamped effective factors so that FRBCInstruction
            # operation_mode_factor reflects what can physically be executed,
            # while the raw genetic gene values are preserved in the solution
            # dataframe (genetic_*_factor columns).
            result_idx = hour_idx - start_day_slot
            soc_h_pct = (
                result.result.battery_soc_per_hour[result_idx]
                if result_idx < len(result.result.battery_soc_per_hour)
                else 0.0
            )
            battery_grid_export_allowed_hour = (
                bool(result.battery_grid_export_allowed[hour_idx])
                if hour_idx < len(result.battery_grid_export_allowed)
                else False
            )
            eff_ac, eff_dc, eff_dis, eff_grid_export = _soc_clamped_operation_factors(
                result,
                result.ac_charge[hour_idx],
                result.dc_charge[hour_idx],
                bool(result.discharge_allowed[hour_idx]),
                soc_h_pct,
                battery_grid_export_allowed_hour,
                result.first_slot_fraction if result_idx == 0 else 1.0,
            )
            operation_mode, operation_mode_factor = _battery_operation_from_solution(
                eff_ac,
                eff_dc,
                eff_dis,
                eff_grid_export,
                result.battery_grid_export_factor[hour_idx]
                if hour_idx < len(result.battery_grid_export_factor)
                else 1.0,
            )
            if (
                operation_mode == last_operation_mode
                and operation_mode_factor == last_operation_mode_factor
            ):
                # Skip, we already added the instruction
                continue
            last_operation_mode = operation_mode
            last_operation_mode_factor = operation_mode_factor
            execution_time = start_datetime.add(seconds=interval_s * (hour_idx - start_day_slot))
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
    # eautocharge_hours_float start at hour 0 of start day
    if result.ev_obj:
        resource_id = _ev_device_id(result)
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
            logger.debug("EV: {} - {}", resource_id, result.ev_charge_hours_float[start_day_slot:])
            for hour_idx, rate in enumerate(result.ev_charge_hours_float):
                if hour_idx < start_day_slot:
                    continue
                operation_mode, operation_mode_factor = _battery_operation_from_solution(
                    rate, 0.0, False
                )
                if (
                    operation_mode == last_operation_mode
                    and operation_mode_factor == last_operation_mode_factor
                ):
                    # Skip, we already added the instruction
                    continue
                last_operation_mode = operation_mode
                last_operation_mode_factor = operation_mode_factor
                execution_time = start_datetime.add(
                    seconds=interval_s * (hour_idx - start_day_slot)
                )
                plan.add_instruction(
                    FRBCInstruction(
                        resource_id=resource_id,
                        execution_time=execution_time,
                        actuator_id=resource_id,
                        operation_mode_id=operation_mode,
                        operation_mode_factor=operation_mode_factor,
                    )
                )

    # Add home appliance instructions (demand driven based control), one
    # stream of instructions per device. A new instruction is only emitted on
    # a transition between OFF (energy == 0) and RUN (energy > 0); a mere
    # power change within a running profile does not add an instruction.
    for resource_id, energy_wh in result.result.home_appliance_energy_wh.items():
        last_state: Optional[bool] = None
        for hours, energy in enumerate(energy_wh):
            # hours starts at start_datetime with 0
            if energy is None:
                raise ValueError(
                    f"Unexpected value {energy} in home_appliance_energy_wh[{resource_id}]"
                )
            running_slots = result.result.home_appliance_running.get(resource_id, [])
            running = running_slots[hours] if hours < len(running_slots) else energy > 0.0
            if last_state is None or running != last_state:
                appliance_mode = (
                    ApplianceOperationMode.RUN if running else ApplianceOperationMode.OFF
                )
                execution_time = start_datetime.add(seconds=interval_s * hours)
                plan.add_instruction(
                    DDBCInstruction(
                        resource_id=resource_id,
                        execution_time=execution_time,
                        actuator_id=resource_id,
                        operation_mode_id=appliance_mode,
                        operation_mode_factor=1.0,
                    )
                )
                last_state = running

    if not plan.instructions:
        plan.valid_from = start_datetime
    return plan
