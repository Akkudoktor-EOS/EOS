"""Genetic algorithm optimisation solution."""

from typing import Any, Optional

import pandas as pd
from loguru import logger
from pydantic import AliasChoices, ConfigDict, Field, field_validator

from akkudoktoreos.core.coreabc import (
    ConfigMixin,
)
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
from akkudoktoreos.devices.genetic.battery import Battery
from akkudoktoreos.optimization.genetic.geneticdevices import GeneticParametersBaseModel
from akkudoktoreos.optimization.optimization import OptimizationSolution
from akkudoktoreos.prediction.prediction import get_prediction
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration
from akkudoktoreos.utils.utils import NumpyEncoder


class DeviceOptimizeResult(GeneticParametersBaseModel):
    device_id: str = Field(
        json_schema_extra={"description": "ID of device", "examples": ["device1"]}
    )
    hours: int = Field(
        gt=0,
        json_schema_extra={"description": "Number of hours in the simulation.", "examples": [24]},
    )


class ElectricVehicleResult(DeviceOptimizeResult):
    """Result class containing information related to the electric vehicle's charging and discharging behavior."""

    device_id: str = Field(
        json_schema_extra={"description": "ID of electric vehicle", "examples": ["ev1"]}
    )
    charge_array: list[float] = Field(
        json_schema_extra={
            "description": "Hourly charging status (0 for no charging, 1 for charging)."
        }
    )
    discharge_array: list[int] = Field(
        json_schema_extra={
            "description": "Hourly discharging status (0 for no discharging, 1 for discharging)."
        }
    )
    discharging_efficiency: float = Field(
        json_schema_extra={"description": "The discharge efficiency as a float.."}
    )
    capacity_wh: int = Field(
        json_schema_extra={"description": "Capacity of the EV’s battery in watt-hours."}
    )
    charging_efficiency: float = Field(
        json_schema_extra={"description": "Charging efficiency as a float.."}
    )
    max_charge_power_w: int = Field(
        json_schema_extra={"description": "Maximum charging power in watts."}
    )
    soc_wh: float = Field(
        json_schema_extra={
            "description": "State of charge of the battery in watt-hours at the start of the simulation."
        }
    )
    initial_soc_percentage: int = Field(
        json_schema_extra={
            "description": "State of charge at the start of the simulation in percentage."
        }
    )

    @field_validator("discharge_array", "charge_array", mode="before")
    def convert_numpy(cls, field: Any) -> Any:
        return NumpyEncoder.convert_numpy(field)[0]


class GeneticSimulationResult(GeneticParametersBaseModel):
    """This object contains the results of the simulation and provides insights into various parameters over the entire forecast period."""

    model_config = ConfigDict(populate_by_name=True)

    load_wh_per_hour: list[float] = Field(
        validation_alias=AliasChoices("Last_Wh_pro_Stunde", "load_wh_per_hour"),
        json_schema_extra={"description": "The load in watt-hours per hour."},
    )
    ev_soc_per_hour: list[float] = Field(
        validation_alias=AliasChoices("EAuto_SoC_pro_Stunde", "ev_soc_per_hour"),
        json_schema_extra={"description": "The state of charge of the EV for each hour."},
    )
    revenue_per_hour: list[float] = Field(
        validation_alias=AliasChoices("Einnahmen_Euro_pro_Stunde", "revenue_per_hour"),
        json_schema_extra={
            "description": "The revenue from grid feed-in or other sources per hour."
        },
    )
    total_losses: float = Field(
        validation_alias=AliasChoices("Gesamt_Verluste", "total_losses"),
        json_schema_extra={"description": "The total losses in watt-hours over the entire period."},
    )
    total_balance: float = Field(
        validation_alias=AliasChoices("Gesamtbilanz_Euro", "total_balance"),
        json_schema_extra={"description": "The total balance of revenues minus costs."},
    )
    total_revenue: float = Field(
        validation_alias=AliasChoices("Gesamteinnahmen_Euro", "total_revenue"),
        json_schema_extra={"description": "The total revenues."},
    )
    total_costs: float = Field(
        validation_alias=AliasChoices("Gesamtkosten_Euro", "total_costs"),
        json_schema_extra={"description": "The total costs."},
    )
    home_appliance_wh_per_hour: list[Optional[float]] = Field(
        validation_alias=AliasChoices("Home_appliance_wh_per_hour", "home_appliance_wh_per_hour"),
        json_schema_extra={
            "description": "The energy consumption of a household appliance in watt-hours per hour."
        },
    )
    costs_per_hour: list[float] = Field(
        validation_alias=AliasChoices("Kosten_Euro_pro_Stunde", "costs_per_hour"),
        json_schema_extra={"description": "The costs per hour."},
    )
    grid_consumption_wh_per_hour: list[float] = Field(
        validation_alias=AliasChoices("Netzbezug_Wh_pro_Stunde", "grid_consumption_wh_per_hour"),
        json_schema_extra={"description": "The grid energy drawn in watt-hours per hour."},
    )
    grid_feed_in_wh_per_hour: list[float] = Field(
        validation_alias=AliasChoices("Netzeinspeisung_Wh_pro_Stunde", "grid_feed_in_wh_per_hour"),
        json_schema_extra={"description": "The energy fed into the grid in watt-hours per hour."},
    )
    losses_per_hour: list[float] = Field(
        validation_alias=AliasChoices("Verluste_Pro_Stunde", "losses_per_hour"),
        json_schema_extra={"description": "The losses in watt-hours per hour."},
    )
    battery_soc_per_hour: list[float] = Field(
        validation_alias=AliasChoices("akku_soc_pro_stunde", "battery_soc_per_hour"),
        json_schema_extra={
            "description": "The state of charge of the battery (not the EV) in percentage per hour."
        },
    )
    electricity_price: list[float] = Field(
        validation_alias=AliasChoices("Electricity_price", "electricity_price"),
        json_schema_extra={"description": "Used Electricity Price, including predictions"},
    )

    @field_validator(
        "load_wh_per_hour",
        "grid_feed_in_wh_per_hour",
        "battery_soc_per_hour",
        "grid_consumption_wh_per_hour",
        "costs_per_hour",
        "revenue_per_hour",
        "ev_soc_per_hour",
        "losses_per_hour",
        "home_appliance_wh_per_hour",
        "electricity_price",
        mode="before",
    )
    def convert_numpy(cls, field: Any) -> Any:
        return NumpyEncoder.convert_numpy(field)[0]


class GeneticSolution(ConfigMixin, GeneticParametersBaseModel):
    """**Note**: The first value of "load_wh_per_hour", "grid_feed_in_wh_per_hour", and "grid_consumption_wh_per_hour", will be set to null in the JSON output and represented as NaN or None in the corresponding classes' data returns. This approach is adopted to ensure that the current hour's processing remains unchanged."""

    ac_charge: list[float] = Field(
        json_schema_extra={
            "description": "Array with AC charging values as relative power (0.0-1.0), other values set to 0."
        }
    )
    dc_charge: list[float] = Field(
        json_schema_extra={
            "description": "Array with DC charging values as relative power (0-1), other values set to 0."
        }
    )
    discharge_allowed: list[int] = Field(
        json_schema_extra={
            "description": "Array with discharge values (1 for discharge, 0 otherwise)."
        }
    )
    eautocharge_hours_float: Optional[list[float]] = Field(json_schema_extra={"description": "TBD"})
    result: GeneticSimulationResult
    eauto_obj: Optional[ElectricVehicleResult]
    start_solution: Optional[list[float]] = Field(
        default=None,
        json_schema_extra={
            "description": "An array of binary values (0 or 1) representing a possible starting solution for the simulation."
        },
    )
    washingstart: Optional[int] = Field(
        default=None,
        json_schema_extra={
            "description": "Can be `null` or contain an object representing the start of washing (if applicable)."
        },
    )

    @field_validator(
        "ac_charge",
        "dc_charge",
        "discharge_allowed",
        mode="before",
    )
    def convert_numpy(cls, field: Any) -> Any:
        return NumpyEncoder.convert_numpy(field)[0]

    @field_validator(
        "eauto_obj",
        mode="before",
    )
    def convert_eauto(cls, field: Any) -> Any:
        if isinstance(field, Battery):
            return ElectricVehicleResult(**field.to_dict())
        return field

    def _battery_operation_from_solution(
        self,
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

    def optimization_solution(self) -> OptimizationSolution:
        """Provide the genetic solution as a general optimization solution.

        The battery modes are controlled by the grid control triggers:
        - ac_charge: charge from grid
        - discharge_allowed: discharge to grid

        The following battery modes are supported:
        - SELF_CONSUMPTION:    ac_charge == 0 and discharge_allowed == 0
        - GRID_SUPPORT_EXPORT: ac_charge == 0 and discharge_allowed == 1
        - GRID_SUPPORT_IMPORT: ac_charge  > 0 and discharge_allowed == 0 or 1
        """
        from akkudoktoreos.core.ems import get_ems

        start_datetime = get_ems().start_datetime
        start_day_hour = start_datetime.in_timezone(self.config.general.timezone).hour
        interval_hours = 1
        power_to_energy_per_interval_factor = 1.0

        # --- Create index based on list length and interval ---
        # Ensure we only use the minimum of results and commands if differing
        periods = min(len(self.result.costs_per_hour), len(self.ac_charge) - start_day_hour)
        time_index = pd.date_range(
            start=start_datetime,
            periods=periods,
            freq=f"{interval_hours}h",
        )
        n_points = len(time_index)
        end_datetime = start_datetime.add(hours=n_points)

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
                "load_energy_wh": self.result.load_wh_per_hour[:n_points],
                "grid_feedin_energy_wh": self.result.grid_feed_in_wh_per_hour[:n_points],
                "grid_consumption_energy_wh": self.result.grid_consumption_wh_per_hour[:n_points],
                "costs_amt": self.result.costs_per_hour[:n_points],
                "revenue_amt": self.result.revenue_per_hour[:n_points],
                "losses_energy_wh": self.result.losses_per_hour[:n_points],
            },
            index=time_index,
        )

        # Add battery data
        solution["battery1_soc_factor"] = [
            v / 100
            for v in self.result.battery_soc_per_hour[:n_points]  # result starts at start_day_hour
        ]
        operation: dict[str, list[float]] = {
            "genetic_ac_charge_factor": [],
            "genetic_dc_charge_factor": [],
            "genetic_discharge_allowed_factor": [],
        }
        # ac_charge, dc_charge, discharge_allowed start at hour 0 of start day
        for hour_idx, rate in enumerate(self.ac_charge):
            if hour_idx < start_day_hour:
                continue
            if hour_idx >= start_day_hour + n_points:
                break
            ac_charge_hour = self.ac_charge[hour_idx]
            dc_charge_hour = self.dc_charge[hour_idx]
            discharge_allowed_hour = bool(self.discharge_allowed[hour_idx])
            operation_mode, operation_mode_factor = self._battery_operation_from_solution(
                ac_charge_hour, dc_charge_hour, discharge_allowed_hour
            )
            operation["genetic_ac_charge_factor"].append(ac_charge_hour)
            operation["genetic_dc_charge_factor"].append(dc_charge_hour)
            operation["genetic_discharge_allowed_factor"].append(discharge_allowed_hour)
            for mode in BatteryOperationMode:
                mode_key = f"battery1_{mode.lower()}_op_mode"
                factor_key = f"battery1_{mode.lower()}_op_factor"
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
        # result.ev_soc_per_hour start at start_datetime.hour
        if self.eauto_obj:
            if self.eautocharge_hours_float is None:
                # Electric vehicle is full enough. No load times.
                solution[f"{self.eauto_obj.device_id}_soc_factor"] = [
                    self.eauto_obj.initial_soc_percentage / 100.0
                ] * n_points
                solution["genetic_ev_charge_factor"] = [0.0] * n_points
                # operation modes
                operation_mode = BatteryOperationMode.IDLE
                for mode in BatteryOperationMode:
                    mode_key = f"{self.eauto_obj.device_id}_{mode.lower()}_op_mode"
                    factor_key = f"{self.eauto_obj.device_id}_{mode.lower()}_op_factor"
                    if mode == operation_mode:
                        solution[mode_key] = [1.0] * n_points
                        solution[factor_key] = [1.0] * n_points
                    else:
                        solution[mode_key] = [0.0] * n_points
                        solution[factor_key] = [0.0] * n_points
            else:
                solution[f"{self.eauto_obj.device_id}_soc_factor"] = [
                    v / 100 for v in self.result.ev_soc_per_hour[:n_points]
                ]
                operation = {
                    "genetic_ev_charge_factor": [],
                }
                for hour_idx, rate in enumerate(self.eautocharge_hours_float):
                    if hour_idx < start_day_hour:
                        continue
                    if hour_idx >= start_day_hour + n_points:
                        break
                    operation["genetic_ev_charge_factor"].append(rate)
                    operation_mode, operation_mode_factor = self._battery_operation_from_solution(
                        rate, 0.0, False
                    )
                    for mode in BatteryOperationMode:
                        mode_key = f"{self.eauto_obj.device_id}_{mode.lower()}_op_mode"
                        factor_key = f"{self.eauto_obj.device_id}_{mode.lower()}_op_factor"
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
        if self.config.devices.max_home_appliances and self.config.devices.max_home_appliances > 0:
            # Use config and not self.washingstart as washingstart may be None (no start)
            # even if configured to be started.

            # result starts at start_day_hour
            solution["homeappliance1_energy_wh"] = self.result.Home_appliance_wh_per_hour[:n_points]
            operation = {
                "homeappliance1_run_op_mode": [],
                "homeappliance1_run_op_factor": [],
                "homeappliance1_off_op_mode": [],
                "homeappliance1_off_op_factor": [],
            }
            for hour_idx, energy in enumerate(solution["homeappliance1_energy_wh"]):
                if energy > 0.0:
                    operation["homeappliance1_run_op_mode"].append(1.0)
                    operation["homeappliance1_run_op_factor"].append(1.0)
                    operation["homeappliance1_off_op_mode"].append(0.0)
                    operation["homeappliance1_off_op_factor"].append(0.0)
                else:
                    operation["homeappliance1_run_op_mode"].append(0.0)
                    operation["homeappliance1_run_op_factor"].append(0.0)
                    operation["homeappliance1_off_op_mode"].append(1.0)
                    operation["homeappliance1_off_op_factor"].append(1.0)
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
        pred = get_prediction()

        if "pvforecast_ac_power" in pred.record_keys:
            prediction["pvforecast_ac_energy_wh"] = (
                pred.key_to_array(
                    key="pvforecast_ac_power",
                    start_datetime=start_datetime,
                    end_datetime=end_datetime,
                    interval=to_duration(f"{interval_hours} hours"),
                    fill_method="linear",
                )
                * power_to_energy_per_interval_factor
            ).tolist()
        if "pvforecast_dc_power" in pred.record_keys:
            prediction["pvforecast_dc_energy_wh"] = (
                pred.key_to_array(
                    key="pvforecast_dc_power",
                    start_datetime=start_datetime,
                    end_datetime=end_datetime,
                    interval=to_duration(f"{interval_hours} hours"),
                    fill_method="linear",
                )
                * power_to_energy_per_interval_factor
            ).tolist()
        if "elecprice_marketprice_wh" in pred.record_keys:
            prediction["elec_price_amt_kwh"] = (
                pred.key_to_array(
                    key="elecprice_marketprice_wh",
                    start_datetime=start_datetime,
                    end_datetime=end_datetime,
                    interval=to_duration(f"{interval_hours} hours"),
                    fill_method="ffill",
                )
                * 1000
            ).tolist()
        if "feed_in_tariff_wh" in pred.record_keys:
            prediction["feed_in_tariff_amt_kwh"] = (
                pred.key_to_array(
                    key="feed_in_tariff_wh",
                    start_datetime=start_datetime,
                    end_datetime=end_datetime,
                    interval=to_duration(f"{interval_hours} hours"),
                    fill_method="linear",
                )
                * 1000
            ).tolist()
        if "weather_temp_air" in pred.record_keys:
            prediction["weather_air_temp_celcius"] = pred.key_to_array(
                key="weather_temp_air",
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                interval=to_duration(f"{interval_hours} hours"),
                fill_method="linear",
            ).tolist()
        if "loadforecast_power_w" in pred.record_keys:
            prediction["loadforecast_energy_wh"] = (
                pred.key_to_array(
                    key="loadforecast_power_w",
                    start_datetime=start_datetime,
                    end_datetime=end_datetime,
                    interval=to_duration(f"{interval_hours} hours"),
                    fill_method="linear",
                )
                * power_to_energy_per_interval_factor
            ).tolist()
        if "loadakkudoktor_std_power_w" in pred.record_keys:
            prediction["loadakkudoktor_std_energy_wh"] = (
                pred.key_to_array(
                    key="loadakkudoktor_std_power_w",
                    start_datetime=start_datetime,
                    end_datetime=end_datetime,
                    interval=to_duration(f"{interval_hours} hours"),
                    fill_method="linear",
                )
                * power_to_energy_per_interval_factor
            ).tolist()
        if "loadakkudoktor_mean_power_w" in pred.record_keys:
            prediction["loadakkudoktor_mean_energy_wh"] = (
                pred.key_to_array(
                    key="loadakkudoktor_mean_power_w",
                    start_datetime=start_datetime,
                    end_datetime=end_datetime,
                    interval=to_duration(f"{interval_hours} hours"),
                    fill_method="linear",
                )
                * power_to_energy_per_interval_factor
            ).tolist()

        optimization_solution = OptimizationSolution(
            id=f"optimization-genetic@{to_datetime(as_string=True)}",
            generated_at=to_datetime(),
            comment="Optimization solution derived from GeneticSolution.",
            valid_from=start_datetime,
            valid_until=start_datetime.add(hours=self.config.optimization.horizon_hours),
            total_losses_energy_wh=self.result.total_losses,
            total_revenues_amt=self.result.total_revenue,
            total_costs_amt=self.result.total_costs,
            fitness_score={
                self.result.total_costs,
            },
            prediction=PydanticDateTimeDataFrame.from_dataframe(prediction),
            solution=PydanticDateTimeDataFrame.from_dataframe(solution),
        )

        return optimization_solution

    def energy_management_plan(self) -> EnergyManagementPlan:
        """Provide the genetic solution as an energy management plan."""
        from akkudoktoreos.core.ems import get_ems

        start_datetime = get_ems().start_datetime
        start_day_hour = start_datetime.in_timezone(self.config.general.timezone).hour
        plan = EnergyManagementPlan(
            id=f"plan-genetic@{to_datetime(as_string=True)}",
            generated_at=to_datetime(),
            instructions=[],
            comment="Energy management plan derived from GeneticSolution.",
        )

        # Add battery instructions (fill rate based control)
        last_operation_mode: Optional[str] = None
        last_operation_mode_factor: Optional[float] = None
        resource_id = "battery1"
        # ac_charge, dc_charge, discharge_allowed start at hour 0 of start day
        logger.debug("BAT: {} - {}", resource_id, self.ac_charge[start_day_hour:])
        for hour_idx, rate in enumerate(self.ac_charge):
            if hour_idx < start_day_hour:
                continue
            operation_mode, operation_mode_factor = self._battery_operation_from_solution(
                self.ac_charge[hour_idx],
                self.dc_charge[hour_idx],
                bool(self.discharge_allowed[hour_idx]),
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
        # eautocharge_hours_float start at hour 0 of start day
        if self.eauto_obj:
            resource_id = self.eauto_obj.device_id
            if self.eautocharge_hours_float is None:
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
                logger.debug(
                    "EV: {} - {}", resource_id, self.eautocharge_hours_float[start_day_hour:]
                )
                for hour_idx, rate in enumerate(self.eautocharge_hours_float):
                    if hour_idx < start_day_hour:
                        continue
                    operation_mode, operation_mode_factor = self._battery_operation_from_solution(
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
        if self.config.devices.max_home_appliances and self.config.devices.max_home_appliances > 0:
            # Use config and not self.washingstart as washingstart may be None (no start)
            # even if configured to be started.
            resource_id = "homeappliance1"
            last_energy: Optional[float] = None
            for hours, energy in enumerate(self.result.Home_appliance_wh_per_hour):
                # hours starts at start_datetime with 0
                if energy is None:
                    raise ValueError(
                        f"Unexpected value {energy} in {self.result.Home_appliance_wh_per_hour}"
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
