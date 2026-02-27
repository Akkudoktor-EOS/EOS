"""Genetic algorithm optimisation solution."""

from typing import Any, Optional

import pandas as pd
from loguru import logger
from pydantic import Field, field_validator

from akkudoktoreos.core.coreabc import (
    ConfigMixin,
    get_ems,
    get_prediction,
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

    Last_Wh_pro_Stunde: list[float] = Field(json_schema_extra={"description": "TBD"})
    EAuto_SoC_pro_Stunde: list[float] = Field(
        json_schema_extra={"description": "The state of charge of the EV for each hour."}
    )
    Einnahmen_Euro_pro_Stunde: list[float] = Field(
        json_schema_extra={
            "description": "The revenue from grid feed-in or other sources in euros per hour."
        }
    )
    Gesamt_Verluste: float = Field(
        json_schema_extra={"description": "The total losses in watt-hours over the entire period."}
    )
    Gesamtbilanz_Euro: float = Field(
        json_schema_extra={"description": "The total balance of revenues minus costs in euros."}
    )
    Gesamteinnahmen_Euro: float = Field(
        json_schema_extra={"description": "The total revenues in euros."}
    )
    Gesamtkosten_Euro: float = Field(json_schema_extra={"description": "The total costs in euros."})
    Home_appliance_wh_per_hour: list[Optional[float]] = Field(
        json_schema_extra={
            "description": "The energy consumption of a household appliance in watt-hours per hour."
        }
    )
    Kosten_Euro_pro_Stunde: list[float] = Field(
        json_schema_extra={"description": "The costs in euros per hour."}
    )
    Netzbezug_Wh_pro_Stunde: list[float] = Field(
        json_schema_extra={"description": "The grid energy drawn in watt-hours per hour."}
    )
    Netzeinspeisung_Wh_pro_Stunde: list[float] = Field(
        json_schema_extra={"description": "The energy fed into the grid in watt-hours per hour."}
    )
    Verluste_Pro_Stunde: list[float] = Field(
        json_schema_extra={"description": "The losses in watt-hours per hour."}
    )
    akku_soc_pro_stunde: list[float] = Field(
        json_schema_extra={
            "description": "The state of charge of the battery (not the EV) in percentage per hour."
        }
    )
    Electricity_price: list[float] = Field(
        json_schema_extra={"description": "Used Electricity Price, including predictions"}
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


class GeneticSolution(ConfigMixin, GeneticParametersBaseModel):
    """**Note**: The first value of "Last_Wh_per_hour", "Netzeinspeisung_Wh_per_hour", and "Netzbezug_Wh_per_hour", will be set to null in the JSON output and represented as NaN or None in the corresponding classes' data returns. This approach is adopted to ensure that the current hour's processing remains unchanged."""

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

    def _soc_clamped_operation_factors(
        self,
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
        """
        bat_list = self.config.devices.batteries
        if not bat_list:
            return ac_charge, dc_charge, discharge_allowed

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
                inv_list = self.config.devices.inverters
                ac_to_dc_eff = float(inv_list[0].ac_to_dc_efficiency) if inv_list else 1.0
                max_ac_cp_w = (
                    float(inv_list[0].max_ac_charge_power_w)
                    if inv_list
                    else float(bat.max_charge_power_w)
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
        start_datetime = get_ems().start_datetime
        start_day_hour = start_datetime.in_timezone(self.config.general.timezone).hour
        interval_hours = 1
        power_to_energy_per_interval_factor = 1.0

        # --- Create index based on list length and interval ---
        # Ensure we only use the minimum of results and commands if differing
        periods = min(len(self.result.Kosten_Euro_pro_Stunde), len(self.ac_charge) - start_day_hour)
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
                "load_energy_wh": self.result.Last_Wh_pro_Stunde[:n_points],
                "grid_feedin_energy_wh": self.result.Netzeinspeisung_Wh_pro_Stunde[:n_points],
                "grid_consumption_energy_wh": self.result.Netzbezug_Wh_pro_Stunde[:n_points],
                "costs_amt": self.result.Kosten_Euro_pro_Stunde[:n_points],
                "revenue_amt": self.result.Einnahmen_Euro_pro_Stunde[:n_points],
                "losses_energy_wh": self.result.Verluste_Pro_Stunde[:n_points],
            },
            index=time_index,
        )

        # Add battery data
        solution["battery1_soc_factor"] = [
            v / 100
            for v in self.result.akku_soc_pro_stunde[:n_points]  # result starts at start_day_hour
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

            # Raw genetic gene values — optimizer intent, stored verbatim
            operation["genetic_ac_charge_factor"].append(ac_charge_hour)
            operation["genetic_dc_charge_factor"].append(dc_charge_hour)
            operation["genetic_discharge_allowed_factor"].append(float(discharge_allowed_hour))

            # SOC-clamped effective values — what can physically be executed at
            # this hour given the expected battery state of charge.
            result_idx = hour_idx - start_day_hour
            soc_h_pct = (
                self.result.akku_soc_pro_stunde[result_idx]
                if result_idx < len(self.result.akku_soc_pro_stunde)
                else 0.0
            )
            eff_ac, eff_dc, eff_dis = self._soc_clamped_operation_factors(
                ac_charge_hour, dc_charge_hour, discharge_allowed_hour, soc_h_pct
            )
            operation_mode, operation_mode_factor = self._battery_operation_from_solution(
                eff_ac, eff_dc, eff_dis
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
            if len(operation[key]) != n_points:
                error_msg = f"instruction {key} has invalid length {len(operation[key])} - expected {n_points}"
                logger.error(error_msg)
                raise ValueError(error_msg)
            solution[key] = operation[key]

        # Add EV battery solution
        # eautocharge_hours_float start at hour 0 of start day
        # result.EAuto_SoC_pro_Stunde start at start_datetime.hour
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
                    v / 100 for v in self.result.EAuto_SoC_pro_Stunde[:n_points]
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
            total_losses_energy_wh=self.result.Gesamt_Verluste,
            total_revenues_amt=self.result.Gesamteinnahmen_Euro,
            total_costs_amt=self.result.Gesamtkosten_Euro,
            fitness_score={
                self.result.Gesamtkosten_Euro,
            },
            prediction=PydanticDateTimeDataFrame.from_dataframe(prediction),
            solution=PydanticDateTimeDataFrame.from_dataframe(solution),
        )

        return optimization_solution

    def energy_management_plan(self) -> EnergyManagementPlan:
        """Provide the genetic solution as an energy management plan."""
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
            # Derive SOC-clamped effective factors so that FRBCInstruction
            # operation_mode_factor reflects what can physically be executed,
            # while the raw genetic gene values are preserved in the solution
            # dataframe (genetic_*_factor columns).
            result_idx = hour_idx - start_day_hour
            soc_h_pct = (
                self.result.akku_soc_pro_stunde[result_idx]
                if result_idx < len(self.result.akku_soc_pro_stunde)
                else 0.0
            )
            eff_ac, eff_dc, eff_dis = self._soc_clamped_operation_factors(
                self.ac_charge[hour_idx],
                self.dc_charge[hour_idx],
                bool(self.discharge_allowed[hour_idx]),
                soc_h_pct,
            )
            operation_mode, operation_mode_factor = self._battery_operation_from_solution(
                eff_ac, eff_dc, eff_dis
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
