"""Translate retained optimization contracts to and from OHMRAKEL42 planning."""

import math
from asyncio import to_thread
from typing import Any

import numpy as np

from akkudoktoreos.devices.appliancescheduling import ApplianceTimes
from akkudoktoreos.devices.models.appliance import ApplianceParams
from akkudoktoreos.devices.models.inverter import InverterParams
from akkudoktoreos.devices.models.storage import DEFAULT_CHARGE_RATES, StorageParams
from akkudoktoreos.devices.settings.homeappliancesettings import HomeApplianceCommonSettings
from akkudoktoreos.optimization.kernel.problem import PlanningProblem
from akkudoktoreos.optimization.objective import (
    ObjectiveSettings,
    TerminalValueSettings,
    VehicleTarget,
)
from akkudoktoreos.optimization.planner import run_planning
from akkudoktoreos.optimization.request.grid import build_time_grid, slice_forecasts
from akkudoktoreos.optimization.request.request import PlanningRequest, RuntimeForecasts
from akkudoktoreos.optimization.request.task import PlanningTask
from akkudoktoreos.optimization.search.settings import (
    ApplianceSchedule,
    SearchSettings,
    SearchSpace,
)
from akkudoktoreos.optimization.solution import PlanningOutcome, appliance_energy, appliance_running
from akkudoktoreos.server.rest.optimizecompatmodels import (
    ConfigApplianceParameters,
    ConfigBatteryParameters,
    ConfigInverterParameters,
    ConfigOptimizationParameters,
    ConfigOptimizationRequest,
    ConfigOptimizationResponse,
    ConfigSimulationResult,
    ConfiguredVehicleResult,
    ConfigVehicleParameters,
    HourlyForecast,
    HourlyOptimizationRequest,
    HourlyOptimizationResponse,
    HourlySimulationResult,
    HourlyStorage,
)
from akkudoktoreos.utils.datetimeutil import DateTime


def _rounded(value: float) -> int:
    """Round half up to an int (the legacy response fields are integers; 99.9 must not be 99)."""
    return int(math.floor(value + 0.5))


class ConfiguredCompatibilityPlanningRequest(PlanningRequest):
    """Resolve the stored configuration for the legacy single-battery/vehicle API."""

    @classmethod
    def from_compatibility_request(
        cls, request: ConfigOptimizationRequest
    ) -> "ConfiguredCompatibilityPlanningRequest":
        """Translate runtime overrides, leaving the historical genome fields unused."""
        return cls(
            soc=request.soc,
            forecasts=RuntimeForecasts.model_validate(
                request.forecasts.model_dump(exclude={"temperature_forecast"})
            ),
        )

    async def resolve(self) -> PlanningTask:
        """Check the configured devices before planning or dispatching a compatibility run."""
        devices = self.config.devices
        for group in ("batteries", "electric_vehicles", "inverters"):
            if len(getattr(devices, group) or {}) > 1:
                raise ValueError(
                    f"/v1/optimize supports at most one device in devices.{group}. "
                    "Use /v1/energy-management/run for multiple devices."
                )
        return await super().resolve()


def _storage(device: HourlyStorage, *, vehicle: bool = False) -> StorageParams:
    if device.min_soc_percentage > device.max_soc_percentage:
        raise ValueError(f"{device.device_id}: minimum SoC exceeds maximum SoC.")
    rates = tuple(device.charge_rates) if device.charge_rates else DEFAULT_CHARGE_RATES
    if not all(0 <= rate <= 1 for rate in rates):
        raise ValueError(f"{device.device_id}: charge rates must be finite factors in [0, 1].")
    charge_power = (
        device.capacity_wh if device.max_charge_power_w is None else device.max_charge_power_w
    )
    discharge_power = device.max_discharge_power_w
    if discharge_power is None:
        discharge_power = charge_power
    return StorageParams(
        capacity_wh=device.capacity_wh,
        initial_soc_pct=device.initial_soc_percentage,
        min_soc_pct=0 if vehicle else device.min_soc_percentage,
        max_soc_pct=device.max_soc_percentage,
        charging_efficiency=device.charging_efficiency,
        discharging_efficiency=device.discharging_efficiency,
        max_charge_power_w=charge_power,
        max_discharge_power_w=0 if vehicle else discharge_power,
        charge_rates=rates,
        is_vehicle=vehicle,
    )


def hourly_task(parameters: HourlyOptimizationRequest, start: DateTime) -> PlanningTask:
    """Build an hourly task exclusively from the legacy POST body and chosen start hour."""
    battery, ev, inverter, appliance = (
        parameters.pv_battery,
        parameters.ev,
        parameters.inverter,
        parameters.dishwasher,
    )
    if inverter is None:
        raise ValueError("The hourly optimizer requires an inverter in the request.")
    battery_id = battery.device_id if battery else None
    if inverter.battery_id != battery_id:
        raise ValueError(
            "The request inverter battery_id must match the request battery device_id."
        )
    ids = [item.device_id for item in (battery, ev, inverter, appliance) if item is not None]
    if len(ids) != len(set(ids)):
        raise ValueError("Device IDs in the request must be unique.")

    supplied = parameters.ems
    first = start.hour
    hours = len(supplied.pv_forecast_wh)
    if hours <= first:
        raise ValueError(
            f"The hourly forecasts must cover start_hour {first} and at least one slot."
        )
    # The historical request arrays use hour-of-day indexing from midnight.
    grid = build_time_grid(
        interval_seconds=3600,
        horizon_hours=hours - first,
        tail_horizon_hours=0,
        prediction_hours=hours,
        start=start,
        observation=start,
    )
    # An hour-of-day index cannot distinguish the repeated autumn hour or represent
    # the missing spring hour when that causes the request and elapsed indexes to differ.
    end = start.add(hours=grid.control_slots)
    if grid.first != first or start.utcoffset() != end.utcoffset():
        raise ValueError(
            "The requested start hour is ambiguous on this daylight-saving transition. "
            "Use /v1/energy-management/run for a timestamped plan."
        )
    tariffs = supplied.feed_in_tariff_per_wh
    if isinstance(tariffs, float):
        tariffs = [tariffs] * hours
    forecasts = slice_forecasts(
        grid,
        np.asarray(supplied.pv_forecast_wh),
        np.asarray(supplied.total_load),
        np.asarray(supplied.electricity_price_per_wh),
        np.asarray(tariffs),
    )
    appliances: tuple[ApplianceParams, ...] = ()
    space = SearchSpace()
    if appliance is not None:
        settings = HomeApplianceCommonSettings(
            device_id=appliance.device_id,
            consumption_wh=appliance.consumption_wh,
            duration_h=appliance.duration_h,
            num_cycles=1,
            time_windows=appliance.time_windows,
        )
        times = ApplianceTimes(settings, 1.0, grid.control_slots)
        starts = times.allowed_start_slots(
            slot0=start, earliest_slot=0, horizon_end_slot=min(24 - first, grid.control_slots)
        )
        if not starts:
            raise ValueError(f"{appliance.device_id}: no hourly start fits its time windows.")
        appliances = (ApplianceParams(tuple(times.run_energy_wh)),)
        space = SearchSpace((ApplianceSchedule((np.asarray(starts, dtype=np.int64),)),))

    return PlanningTask(
        problem=PlanningProblem(
            slot_duration_h=1.0,
            pv_wh=forecasts.pv_wh,
            load_wh=forecasts.load_wh,
            import_price_per_wh=forecasts.price_per_wh,
            feed_in_tariff_per_wh=forecasts.tariff_per_wh,
            batteries=(_storage(battery),) if battery else (),
            vehicles=(_storage(ev, vehicle=True),) if ev else (),
            inverters=(
                InverterParams(
                    max_power_w=inverter.max_power_wh,
                    battery_index=0 if battery else None,
                    dc_to_ac_efficiency=inverter.dc_to_ac_efficiency,
                    ac_to_dc_efficiency=inverter.ac_to_dc_efficiency,
                    max_ac_charge_power_w=inverter.max_ac_charge_power_w,
                ),
            ),
            appliances=appliances,
        ),
        objective=ObjectiveSettings(
            terminal_value=TerminalValueSettings(fixed_price_per_wh=supplied.price_per_wh_battery),
            vehicle_targets=(VehicleTarget(ev.min_soc_percentage),) if ev else (),
        ),
        search=SearchSettings(discharge_levels=(1.0,), pv_charge_switchable=False),
        space=space,
        grid=grid,
        forecasts=forecasts,
        battery_ids=(battery.device_id,) if battery else (),
        vehicle_ids=(ev.device_id,) if ev else (),
        inverter_ids=(inverter.device_id,),
        appliance_ids=(appliance.device_id,) if appliance else (),
        appliance_pinned_end_slots=(0,) if appliance else (),
    )


async def optimize_hourly(
    parameters: HourlyOptimizationRequest, start: DateTime
) -> tuple[HourlyOptimizationResponse, PlanningOutcome]:
    """Run OHMRAKEL42 without publishing request-local devices to the EMS or adapters."""
    outcome = await to_thread(run_planning, hourly_task(parameters, start))
    return HourlyOptimizationResponse(
        **_controls(outcome, prefix=start.hour),
        result=HourlySimulationResult(**_series(outcome)),
    ), outcome


def _controls(outcome: PlanningOutcome, prefix: int = 0) -> dict:
    task, plan, result = outcome.task, outcome.plan, outcome.result
    slots = task.problem.slots

    def battery_values(array: np.ndarray, *, binary: bool = False) -> list:
        values = array[0] if task.battery_ids else np.zeros(slots)
        if binary:
            return [0] * prefix + (values > 0).astype(int).tolist()
        return [0.0] * prefix + values.tolist()

    ev_charge = None
    ev_result = None
    if task.vehicle_ids:
        ev = task.problem.vehicles[0]
        ev_charge = [0.0] * prefix + plan.vehicle_charge[0].tolist()
        ev_result = ConfiguredVehicleResult(
            device_id=task.vehicle_ids[0],
            hours=len(ev_charge),
            charge_array=ev_charge,
            discharge_array=[0] * len(ev_charge),
            discharging_efficiency=ev.discharging_efficiency,
            charging_efficiency=ev.charging_efficiency,
            capacity_wh=_rounded(ev.capacity_wh),
            max_charge_power_w=_rounded(ev.max_charge_power_w),
            max_discharge_power_w=ev.max_discharge_power_w,
            soc_wh=float(result.final_soc_wh[len(task.battery_ids)]),
            initial_soc_percentage=_rounded(ev.initial_soc_pct),
        )
    washingstart = None
    if task.appliance_ids:
        washingstart = next((int(v) + prefix for v in plan.appliance_starts[0] if v >= 0), None)
    return dict(
        ac_charge=battery_values(plan.battery_grid_charge),
        dc_charge=battery_values(plan.battery_pv_charge),
        discharge_allowed=battery_values(plan.battery_discharge_limit, binary=True),
        ev_charge_hours_float=ev_charge,
        ev_obj=ev_result,
        washingstart=washingstart,
    )


def _series(outcome: PlanningOutcome) -> dict:
    result = outcome.result
    return dict(
        load_wh_per_hour=result.load_wh.tolist(),
        ev_soc_per_hour=(
            result.vehicle_soc_pct[0]
            if result.vehicle_soc_pct.size
            else np.zeros_like(result.load_wh)
        ).tolist(),
        revenue_per_hour=result.revenue_eur.tolist(),
        total_losses=result.total_losses_wh,
        total_balance=result.total_balance_eur,
        total_revenue=result.total_revenue_eur,
        total_costs=result.total_cost_eur,
        home_appliance_wh_per_hour=result.appliance_wh.tolist(),
        costs_per_hour=result.cost_eur.tolist(),
        grid_consumption_wh_per_hour=result.grid_import_wh.tolist(),
        grid_feed_in_wh_per_hour=result.grid_export_wh.tolist(),
        losses_per_hour=result.losses_wh.tolist(),
        battery_soc_per_hour=(
            result.soc_pct[0] if result.soc_pct.size else np.zeros_like(result.load_wh)
        ).tolist(),
        electricity_price=result.import_price_per_wh.tolist(),
    )


def configured_response(
    outcome: PlanningOutcome, request: ConfigOptimizationRequest
) -> ConfigOptimizationResponse:
    """Represent the exact published plan in the configured API response."""
    task, plan, result = outcome.task, outcome.plan, outcome.result
    grid = task.grid
    starts: dict[str, list[DateTime]] = {}
    deadlines: dict[str, bool] = {}
    for i, name in enumerate(task.appliance_ids):
        slots = [int(slot) for slot in plan.appliance_starts[i] if slot >= 0]
        times = task.appliance_inputs[i].model
        planned = [grid.slot_start(slot) for slot in slots]
        if times.pinned_start_datetime is not None:
            planned.insert(0, times.pinned_start_datetime)
        starts[name] = planned
        if times.deadline_datetime is not None:
            deadlines[name] = times.deadline_missed(slots, grid.start)

    parameters = _configured_parameters(outcome, request)
    export = (
        plan.battery_export[0]
        if task.problem.direct_marketing and task.battery_ids
        else np.zeros(0)
    )
    return ConfigOptimizationResponse(
        **_controls(outcome),
        parameters=parameters,
        result=ConfigSimulationResult(
            **_series(outcome),
            home_appliance_energy_wh={
                name: appliance_energy(task, plan, i).tolist()
                for i, name in enumerate(task.appliance_ids)
            },
            home_appliance_running={
                name: appliance_running(task, plan, i).tolist()
                for i, name in enumerate(task.appliance_ids)
            },
            feed_in_tariff=result.feed_in_tariff_per_wh.tolist(),
        ),
        start_hour=grid.start.hour,
        battery_grid_export_allowed=(export > 0).astype(int).tolist(),
        battery_grid_export_factor=export.tolist(),
        appliance_starts=starts,
        appliance_deadline_missed=deadlines,
        terminal_value=outcome.solution.terminal_value,
        interval_seconds=grid.interval_seconds,
        first_slot_fraction=grid.first_slot_fraction,
    )


def _configured_parameters(
    outcome: PlanningOutcome, request: ConfigOptimizationRequest
) -> ConfigOptimizationParameters:
    task = outcome.task
    problem, grid, forecasts = task.problem, task.grid, task.forecasts
    pv, load, prices, tariffs = forecasts.midnight_series or (
        forecasts.pv_wh,
        forecasts.load_wh,
        forecasts.price_per_wh,
        forecasts.tariff_per_wh,
    )
    ems = HourlyForecast(
        pv_forecast_wh=pv.tolist(),
        total_load=load.tolist(),
        electricity_price_per_wh=prices.tolist(),
        feed_in_tariff_per_wh=tariffs.tolist(),
        price_per_wh_battery=task.objective.terminal_value.fixed_price_per_wh,
    )
    battery = None
    if task.battery_ids:
        storage = problem.batteries[0]
        battery_target = task.objective.full_charge_targets[0]
        battery = ConfigBatteryParameters(
            **_storage_parameters(task.battery_ids[0], storage),
            grid_export_rates=list(task.search.export_levels_of(0)),
            levelized_cost_of_storage_kwh=storage.lcos_per_kwh,
            full_charge_soc_percentage=_rounded(battery_target.target_soc_pct)
            if battery_target
            else None,
            full_charge_deadline_datetime=grid.slot_start(battery_target.deadline_slot)
            if battery_target
            else None,
        )
    vehicle = None
    if task.vehicle_ids:
        vehicle_target = task.objective.vehicle_targets[0]
        storage_data = _storage_parameters(task.vehicle_ids[0], problem.vehicles[0])
        if vehicle_target is not None:
            storage_data["min_soc_percentage"] = _rounded(vehicle_target.target_soc_pct)
        vehicle = ConfigVehicleParameters(
            **storage_data,
            min_soc_deadline_datetime=(
                grid.slot_start(vehicle_target.deadline_slot)
                if vehicle_target is not None and vehicle_target.deadline_slot is not None
                else None
            ),
        )

    inverter = problem.inverters[0]
    appliances = []
    for item in task.appliance_inputs:
        times = item.model
        config = times.settings
        appliances.append(
            ConfigApplianceParameters(
                device_id=config.device_id,
                consumption_wh=config.consumption_wh,
                duration_h=config.duration_h,
                num_cycles=times.num_cycles,
                completed_cycles=times.completed_cycles,
                run_started_datetime=times.pinned_start_datetime,
                min_cycle_gap_h=config.min_cycle_gap_h,
                time_windows=config.cycle_time_windows,
                shared_time_windows=config.time_windows,
                load_profile_power_w=config.load_profile_power_w,
                load_profile_interval_seconds=config.load_profile_interval_seconds,
                schedule_mode=config.schedule_mode,
                earliest_start_datetime=config.earliest_start_datetime,
                deadline_datetime=config.deadline_datetime,
                deadline_policy=config.deadline_policy,
            )
        )
    return ConfigOptimizationParameters(
        ems=ems,
        pv_battery=battery,
        inverter=ConfigInverterParameters(
            device_id=task.inverter_ids[0],
            battery_id=task.battery_ids[0] if task.battery_ids else None,
            max_power_wh=inverter.max_power_w,
            ac_to_dc_efficiency=inverter.ac_to_dc_efficiency,
            dc_to_ac_efficiency=inverter.dc_to_ac_efficiency,
            max_ac_charge_power_w=inverter.max_ac_charge_power_w,
            ac_to_dc_efficiency_curve=(
                list(inverter.ac_to_dc_efficiency_curve)
                if inverter.ac_to_dc_efficiency_curve is not None
                else None
            ),
            dc_to_ac_efficiency_curve=(
                list(inverter.dc_to_ac_efficiency_curve)
                if inverter.dc_to_ac_efficiency_curve is not None
                else None
            ),
            ac_charge_limits_total_charge=inverter.ac_charge_limits_total_charge,
            dc_to_ac_efficiency_reference_load_fraction=(
                inverter.dc_to_ac_efficiency_reference_load_fraction
            ),
        ),
        ev=vehicle,
        home_appliances=appliances,
        temperature_forecast=request.forecasts.temperature_forecast,
        forecast_interval_seconds=grid.interval_seconds,
    )


def _storage_parameters(device_id: str, storage: StorageParams) -> dict[str, Any]:
    return dict(
        device_id=device_id,
        capacity_wh=_rounded(storage.capacity_wh),
        charging_efficiency=storage.charging_efficiency,
        discharging_efficiency=storage.discharging_efficiency,
        max_charge_power_w=storage.max_charge_power_w,
        max_discharge_power_w=storage.max_discharge_power_w,
        initial_soc_percentage=_rounded(storage.initial_soc_pct),
        min_soc_percentage=_rounded(storage.min_soc_pct),
        max_soc_percentage=_rounded(storage.max_soc_pct),
        charge_rates=list(storage.charge_rates),
        charge_power_limit_w_by_soc=storage.charge_power_limit_w_by_soc,
        discharge_power_limit_w_by_soc=storage.discharge_power_limit_w_by_soc,
        charging_efficiency_curve=storage.charging_efficiency_curve,
        discharging_efficiency_curve=storage.discharging_efficiency_curve,
    )
