"""Genetic algorithm optimisation solution."""

from typing import Any, Optional

import pandas as pd
from loguru import logger
from pydantic import Field, field_validator

from akkudoktoreos.config.config import get_config
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
    device_id: str = Field(description="ID of device", examples=["device1"])
    hours: int = Field(gt=0, description="Number of hours in the simulation.", examples=[24])


class ElectricVehicleResult(DeviceOptimizeResult):
    """Result class containing information related to the electric vehicle's charging and discharging behavior."""

    device_id: str = Field(description="ID of electric vehicle", examples=["ev1"])
    charge_array: list[float] = Field(
        description="Hourly charging status (0 for no charging, 1 for charging)."
    )
    discharge_array: list[int] = Field(
        description="Hourly discharging status (0 for no discharging, 1 for discharging)."
    )
    discharging_efficiency: float = Field(description="The discharge efficiency as a float..")
    capacity_wh: int = Field(description="Capacity of the EV’s battery in watt-hours.")
    charging_efficiency: float = Field(description="Charging efficiency as a float..")
    max_charge_power_w: int = Field(description="Maximum charging power in watts.")
    soc_wh: float = Field(
        description="State of charge of the battery in watt-hours at the start of the simulation."
    )
    initial_soc_percentage: int = Field(
        description="State of charge at the start of the simulation in percentage."
    )

    @field_validator("discharge_array", "charge_array", mode="before")
    def convert_numpy(cls, field: Any) -> Any:
        return NumpyEncoder.convert_numpy(field)[0]


class GeneticSimulationResult(GeneticParametersBaseModel):
    """This object contains the results of the simulation and provides insights into various parameters over the entire forecast period."""

    Last_Wh_pro_Stunde: list[float] = Field(description="TBD")
    EAuto_SoC_pro_Stunde: list[float] = Field(
        description="The state of charge of the EV for each hour."
    )
    Einnahmen_Euro_pro_Stunde: list[float] = Field(
        description="The revenue from grid feed-in or other sources in euros per hour."
    )
    Gesamt_Verluste: float = Field(
        description="The total losses in watt-hours over the entire period."
    )
    Gesamtbilanz_Euro: float = Field(
        description="The total balance of revenues minus costs in euros."
    )
    Gesamteinnahmen_Euro: float = Field(description="The total revenues in euros.")
    Gesamtkosten_Euro: float = Field(description="The total costs in euros.")
    Home_appliance_wh_per_hour: list[Optional[float]] = Field(
        description="The energy consumption of a household appliance in watt-hours per hour."
    )
    Kosten_Euro_pro_Stunde: list[float] = Field(description="The costs in euros per hour.")
    Netzbezug_Wh_pro_Stunde: list[float] = Field(
        description="The grid energy drawn in watt-hours per hour."
    )
    Netzeinspeisung_Wh_pro_Stunde: list[float] = Field(
        description="The energy fed into the grid in watt-hours per hour."
    )
    Verluste_Pro_Stunde: list[float] = Field(description="The losses in watt-hours per hour.")
    akku_soc_pro_stunde: list[float] = Field(
        description="The state of charge of the battery (not the EV) in percentage per hour."
    )
    Electricity_price: list[float] = Field(
        description="Used Electricity Price, including predictions"
    )

    @field_validator(
        "Last_Wh_pro_Stunde",
        "Netzeinspeisung_Wh_pro_Stunde",
        "akku_soc_pro_stunde",
        "Netzbezug_Wh_pro_Stunde",
        "Kosten_Euro_pro_Stunde",
        "Einnahmen_Euro_pro_Stunde",
        "EAuto_SoC_pro_Stunde",
        "Verluste_Pro_Stunde",
        "Home_appliance_wh_per_hour",
        "Electricity_price",
        mode="before",
    )
    def convert_numpy(cls, field: Any) -> Any:
        return NumpyEncoder.convert_numpy(field)[0]


class GeneticSolution(GeneticParametersBaseModel):
    """**Note**: The first value of "Last_Wh_per_hour", "Netzeinspeisung_Wh_per_hour", and "Netzbezug_Wh_per_hour", will be set to null in the JSON output and represented as NaN or None in the corresponding classes' data returns. This approach is adopted to ensure that the current hour's processing remains unchanged."""

    ac_charge: list[float] = Field(
        description="Array with AC charging values as relative power (0.0-1.0), other values set to 0."
    )
    dc_charge: list[float] = Field(
        description="Array with DC charging values as relative power (0-1), other values set to 0."
    )
    discharge_allowed: list[int] = Field(
        description="Array with discharge values (1 for discharge, 0 otherwise)."
    )
    eautocharge_hours_float: Optional[list[float]] = Field(description="TBD")
    result: GeneticSimulationResult
    eauto_obj: Optional[ElectricVehicleResult]
    start_solution: Optional[list[float]] = Field(
        default=None,
        description="An array of binary values (0 or 1) representing a possible starting solution for the simulation.",
    )
    washingstart: Optional[int] = Field(
        default=None,
        description="Can be `null` or contain an object representing the start of washing (if applicable).",
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
            tuple[BatteryOperationMode, float]:
                A tuple containing:
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

        config = get_config()
        start_datetime = get_ems().start_datetime
        interval_hours = 1

        # --- Create index based on list length and interval ---
        n_points = len(self.result.Kosten_Euro_pro_Stunde)
        time_index = pd.date_range(
            start=start_datetime,
            periods=n_points,
            freq=f"{interval_hours}h",
        )
        end_datetime = start_datetime.add(hours=n_points)

        # Fill data into dataframe with correct column names
        # - load_energy_wh: Load of all energy consumers in wh"
        # - grid_energy_wh: Grid energy feed in (negative) or consumption (positive) in wh"
        # - pv_prediction_energy_wh: PV energy prediction (positive) in wh"
        # - elec_price_prediction_amt_kwh: Electricity price prediction in money per kwh"
        # - costs_amt: Costs in money amount"
        # - revenue_amt: Revenue in money amount"
        # - losses_energy_wh: Energy losses in wh"
        # - <device-id>_<operation>_op_mode: Operation mode of the device (1.0 when active)."
        # - <device-id>_<operation>_op_factor: Operation mode factor of the device."
        # - <device-id>_soc_factor: State of charge of a battery/ electric vehicle device as factor of total capacity."
        # - <device-id>_energy_wh: Energy consumption (positive) of a device in wh."

        data = pd.DataFrame(
            {
                "date_time": time_index,
                "load_energy_wh": self.result.Last_Wh_pro_Stunde,
                "grid_feedin_energy_wh": self.result.Netzeinspeisung_Wh_pro_Stunde,
                "grid_consumption_energy_wh": self.result.Netzbezug_Wh_pro_Stunde,
                "elec_price_prediction_amt_kwh": [v * 1000 for v in self.result.Electricity_price],
                "costs_amt": self.result.Kosten_Euro_pro_Stunde,
                "revenue_amt": self.result.Einnahmen_Euro_pro_Stunde,
                "losses_energy_wh": self.result.Verluste_Pro_Stunde,
            },
            index=time_index,
        )

        # Add battery data
        data["battery1_soc_factor"] = [v / 100 for v in self.result.akku_soc_pro_stunde]
        operation: dict[str, list[float]] = {}
        for hour, rate in enumerate(self.ac_charge):
            if hour >= n_points:
                break
            operation_mode, operation_mode_factor = self._battery_operation_from_solution(
                self.ac_charge[hour], self.dc_charge[hour], bool(self.discharge_allowed[hour])
            )
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
            data[key] = operation[key]

        # Add EV battery data
        if self.eauto_obj:
            if self.eautocharge_hours_float is None:
                # Electric vehicle is full enough. No load times.
                data[f"{self.eauto_obj.device_id}_soc_factor"] = [
                    self.eauto_obj.initial_soc_percentage / 100.0
                ] * n_points
                # operation modes
                operation_mode = BatteryOperationMode.IDLE
                for mode in BatteryOperationMode:
                    mode_key = f"{self.eauto_obj.device_id}_{mode.lower()}_op_mode"
                    factor_key = f"{self.eauto_obj.device_id}_{mode.lower()}_op_factor"
                    if mode == operation_mode:
                        data[mode_key] = [1.0] * n_points
                        data[factor_key] = [1.0] * n_points
                    else:
                        data[mode_key] = [0.0] * n_points
                        data[factor_key] = [0.0] * n_points
            else:
                data[f"{self.eauto_obj.device_id}_soc_factor"] = [
                    v / 100 for v in self.result.EAuto_SoC_pro_Stunde
                ]
                operation = {}
                for hour, rate in enumerate(self.eautocharge_hours_float):
                    if hour >= n_points:
                        break
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
                    data[key] = operation[key]

        # Add home appliance data
        if self.washingstart:
            data["homeappliance1_energy_wh"] = self.result.Home_appliance_wh_per_hour

        # Add important predictions that are not already available from the GenericSolution
        prediction = get_prediction()
        power_to_energy_per_interval_factor = 1.0
        if "pvforecast_ac_power" in prediction.record_keys:
            data["pv_prediction_energy_wh"] = (
                prediction.key_to_array(
                    key="pvforecast_ac_power",
                    start_datetime=start_datetime,
                    end_datetime=end_datetime,
                    interval=to_duration(f"{interval_hours} hours"),
                    fill_method="linear",
                )
                * power_to_energy_per_interval_factor
            ).tolist()
        if "weather_temp_air" in prediction.record_keys:
            data["weather_temp_air"] = (
                prediction.key_to_array(
                    key="weather_temp_air",
                    start_datetime=start_datetime,
                    end_datetime=end_datetime,
                    interval=to_duration(f"{interval_hours} hours"),
                    fill_method="linear",
                )
            ).tolist()

        solution = OptimizationSolution(
            id=f"optimization-genetic@{to_datetime(as_string=True)}",
            generated_at=to_datetime(),
            comment="Optimization solution derived from GeneticSolution.",
            valid_from=start_datetime,
            valid_until=start_datetime.add(hours=config.optimization.horizon_hours),
            total_losses_energy_wh=self.result.Gesamt_Verluste,
            total_revenues_amt=self.result.Gesamteinnahmen_Euro,
            total_costs_amt=self.result.Gesamtkosten_Euro,
            data=PydanticDateTimeDataFrame.from_dataframe(data),
        )

        return solution

    def energy_management_plan(self) -> EnergyManagementPlan:
        """Provide the genetic solution as an energy management plan."""
        from akkudoktoreos.core.ems import get_ems

        start_datetime = get_ems().start_datetime
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
        logger.debug("BAT: {} - {}", resource_id, self.ac_charge)
        for hour, rate in enumerate(self.ac_charge):
            operation_mode, operation_mode_factor = self._battery_operation_from_solution(
                self.ac_charge[hour], self.dc_charge[hour], bool(self.discharge_allowed[hour])
            )
            if (
                operation_mode == last_operation_mode
                and operation_mode_factor == last_operation_mode_factor
            ):
                # Skip, we already added the instruction
                continue
            last_operation_mode = operation_mode
            last_operation_mode_factor = operation_mode_factor
            execution_time = start_datetime.add(hours=hour)
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
                logger.debug("EV: {} - {}", resource_id, self.eauto_obj.charge_array)
                for hour, rate in enumerate(self.eautocharge_hours_float):
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
                    execution_time = start_datetime.add(hours=hour)
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
        if self.washingstart:
            resource_id = "homeappliance1"
            operation_mode = ApplianceOperationMode.RUN  # type: ignore[assignment]
            operation_mode_factor = 1.0
            execution_time = start_datetime.add(hours=self.washingstart)
            plan.add_instruction(
                DDBCInstruction(
                    resource_id=resource_id,
                    execution_time=execution_time,
                    actuator_id=resource_id,
                    operation_mode_id=operation_mode,
                    operation_mode_factor=operation_mode_factor,
                )
            )

        return plan
