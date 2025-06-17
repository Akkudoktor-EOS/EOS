"""Genetic algorithm optimisation solution."""

from typing import Any, Optional

import pandas as pd
from loguru import logger
from pendulum import today
from pydantic import Field, field_validator

from akkudoktoreos.config.config import get_config
from akkudoktoreos.core.emplan import (
    DDBCInstruction,
    EnergyManagementPlan,
    FRBCInstruction,
)
from akkudoktoreos.core.pydantic import PydanticDateTimeDataFrame
from akkudoktoreos.devices.genetic.battery import Battery
from akkudoktoreos.optimization.geneticdevices import GeneticParametersBaseModel
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

    def optimization_solution(self) -> OptimizationSolution:
        """Provide the genetic solution as a general optimization solution."""
        config = get_config()
        start_datetime = to_datetime(today(), to_maxtime=False)
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
        # - <device-id>_operation_mode_id: Operation mode id of the device."
        # - <device-id>_operation_mode_factor: Operation mode factor of the device."
        # - <device-id>_soc_factor: State of charge of a battery/ electric vehicle device as factor of total capacity."
        # - <device-id>_energy_wh: Energy consumption (positive) of a device in wh."

        data = pd.DataFrame(
            {
                "date_time": time_index,
                "load_energy_wh": self.result.Last_Wh_pro_Stunde,
                "grid_energy_wh": [
                    a - b
                    for a, b in zip(
                        self.result.Netzbezug_Wh_pro_Stunde,
                        self.result.Netzeinspeisung_Wh_pro_Stunde,
                    )
                ],
                "elec_price_prediction_amt_kwh": [v * 1000 for v in self.result.Electricity_price],
                "costs_amt": self.result.Kosten_Euro_pro_Stunde,
                "revenue_amt": self.result.Einnahmen_Euro_pro_Stunde,
                "losses_energy_wh": self.result.Verluste_Pro_Stunde,
            },
            index=time_index,
        )

        # Add battery data
        data["battery1_soc_factor"] = [v / 100 for v in self.result.akku_soc_pro_stunde]

        # Add EV battery data
        if self.eauto_obj and self.eautocharge_hours_float:
            data[f"{self.eauto_obj.device_id}_soc_factor"] = [
                v / 100 for v in self.result.EAuto_SoC_pro_Stunde
            ]

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
        start_of_day = to_datetime(today(), to_maxtime=False)
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
            # default
            operation_mode = "IDLE"
            operation_mode_factor = 0.0
            if self.discharge_allowed[hour]:
                operation_mode = "ALLOW_DISCHARGE"
                operation_mode_factor = 1.0
            elif self.ac_charge[hour] > 0.0:
                operation_mode = "CHARGE"
                operation_mode_factor = self.ac_charge[hour]
            if (
                operation_mode == last_operation_mode
                and operation_mode_factor == last_operation_mode_factor
            ):
                # Skip, we already added the instruction
                continue
            last_operation_mode = operation_mode
            last_operation_mode_factor = operation_mode_factor
            execution_time = start_of_day.add(hours=hour)
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
        if self.eauto_obj and self.eautocharge_hours_float:
            last_operation_mode = None
            last_operation_mode_factor = None
            resource_id = self.eauto_obj.device_id
            logger.debug("EV: {} - {}", resource_id, self.eauto_obj.charge_array)
            for hour, rate in enumerate(self.eautocharge_hours_float):
                # default
                if rate > 0.0:
                    operation_mode = "CHARGE"
                    operation_mode_factor = rate
                else:
                    operation_mode = "IDLE"
                    operation_mode_factor = 0.0
                if (
                    operation_mode == last_operation_mode
                    and operation_mode_factor == last_operation_mode_factor
                ):
                    # Skip, we already added the instruction
                    continue
                last_operation_mode = operation_mode
                last_operation_mode_factor = operation_mode_factor
                execution_time = start_of_day.add(hours=hour)
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
            operation_mode = "RUN"
            operation_mode_factor = 1.0
            execution_time = start_of_day.add(hours=self.washingstart)
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
