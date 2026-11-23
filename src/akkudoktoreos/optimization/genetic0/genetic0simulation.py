"""GENETIC0 simulation of one individual over the horizon."""

from typing import Any, Optional

import numpy as np
from loguru import logger
from numpydantic import NDArray, Shape
from pydantic import ConfigDict, Field

from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.optimization.genetic0.devices.genetic0battery import Genetic0Battery
from akkudoktoreos.optimization.genetic0.devices.genetic0homeappliance import (
    Genetic0HomeAppliance,
)
from akkudoktoreos.optimization.genetic0.devices.genetic0inverter import (
    Genetic0Inverter,
)
from akkudoktoreos.optimization.genetic0.genetic0request import (
    Genetic0EnergyManagementParameters,
)


class Genetic0Simulation(PydanticBaseModel):
    """Device simulation for GENETIC optimization algorithm."""

    # Disable validation on assignment to speed up simulation runs.
    model_config = ConfigDict(
        validate_assignment=False,
    )

    start_hour: int = Field(
        default=0,
        ge=0,
        le=23,
        json_schema_extra={"description": "Starting hour on day for optimizations."},
    )

    optimization_hours: Optional[int] = Field(
        default=24,
        ge=0,
        json_schema_extra={"description": "Number of hours into the future for optimizations."},
    )

    prediction_hours: Optional[int] = Field(
        default=48,
        ge=0,
        json_schema_extra={"description": "Number of hours into the future for predictions"},
    )

    load_energy_array: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        json_schema_extra={
            "description": "An array of floats representing the total load (consumption) in watts for different time intervals."
        },
    )
    pv_prediction_wh: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        json_schema_extra={
            "description": "An array of floats representing the forecasted photovoltaic output in watts for different time intervals."
        },
    )
    elect_price_hourly: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        json_schema_extra={
            "description": "An array of floats representing the electricity price per watt-hour for different time intervals."
        },
    )
    elect_revenue_per_hour_arr: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        json_schema_extra={
            "description": "An array of floats representing the feed-in compensation per watt-hour."
        },
    )

    battery: Optional[Genetic0Battery] = Field(
        default=None, json_schema_extra={"description": "TBD."}
    )
    ev: Optional[Genetic0Battery] = Field(default=None, json_schema_extra={"description": "TBD."})
    home_appliance: Optional[Genetic0HomeAppliance] = Field(
        default=None, json_schema_extra={"description": "TBD."}
    )
    inverter: Optional[Genetic0Inverter] = Field(
        default=None, json_schema_extra={"description": "TBD."}
    )

    ac_charge_hours: Optional[NDArray[Shape["*"], float]] = Field(
        default=None, json_schema_extra={"description": "TBD"}
    )
    dc_charge_hours: Optional[NDArray[Shape["*"], float]] = Field(
        default=None, json_schema_extra={"description": "TBD"}
    )
    bat_discharge_hours: Optional[NDArray[Shape["*"], float]] = Field(
        default=None, json_schema_extra={"description": "TBD"}
    )
    ev_charge_hours: Optional[NDArray[Shape["*"], float]] = Field(
        default=None, json_schema_extra={"description": "TBD"}
    )
    ev_discharge_hours: Optional[NDArray[Shape["*"], float]] = Field(
        default=None, json_schema_extra={"description": "TBD"}
    )
    home_appliance_start_hour: Optional[int] = Field(
        default=None,
        json_schema_extra={"description": "Home appliance start hour - None denotes no start."},
    )

    def prepare(
        self,
        parameters: Genetic0EnergyManagementParameters,
        optimization_hours: int,
        prediction_hours: int,
        ev: Optional[Genetic0Battery] = None,
        home_appliance: Optional[Genetic0HomeAppliance] = None,
        inverter: Optional[Genetic0Inverter] = None,
    ) -> None:
        """Prepare simulation runs.

        Populate internal arrays and device references used during simulation.
        """
        self.optimization_hours = optimization_hours
        self.prediction_hours = prediction_hours

        # Load arrays from provided EMS parameters
        self.load_energy_array = np.array(parameters.total_load, float)
        self.pv_prediction_wh = np.array(parameters.pv_forecast_wh, float)
        self.elect_price_hourly = np.array(parameters.electricity_price_per_wh, float)
        self.elect_revenue_per_hour_arr = (
            np.asarray(parameters.feed_in_tariff_per_wh, dtype=float)
            if isinstance(parameters.feed_in_tariff_per_wh, list)
            else np.full(len(self.load_energy_array), parameters.feed_in_tariff_per_wh, float)
        )

        # Associate devices
        if inverter:
            self.battery = inverter.battery
        else:
            self.battery = None
        self.ev = ev
        self.home_appliance = home_appliance
        self.inverter = inverter

        # Initialize per-hour action arrays for the prediction horizon
        self.ac_charge_hours = np.full(self.prediction_hours, 0.0)
        self.dc_charge_hours = np.full(self.prediction_hours, 0.0)
        self.bat_discharge_hours = np.full(self.prediction_hours, 0.0)
        self.ev_charge_hours = np.full(self.prediction_hours, 0.0)
        self.ev_discharge_hours = np.full(self.prediction_hours, 0.0)
        self.home_appliance_start_hour = None

    def reset(self) -> None:
        if self.ev:
            self.ev.reset()
        if self.battery:
            self.battery.reset()
        self.home_appliance_start_hour = None

    def simulate(self, start_hour: int) -> dict[str, Any]:
        """Simulate energy usage and costs for the given start hour.

        battery_soc_per_hour begin of the hour, initial hour state!
        load_wh_per_hour integral of last hour (end state)
        """
        # Remember start hour
        self.start_hour = start_hour

        # Provide fast (3x..5x) local read access (vs. self.xxx) for repetitive read access
        load_energy_array_fast = self.load_energy_array
        ev_charge_hours_fast = self.ev_charge_hours
        ev_discharge_hours_fast = self.ev_discharge_hours
        ac_charge_hours_fast = self.ac_charge_hours
        dc_charge_hours_fast = self.dc_charge_hours
        bat_discharge_hours_fast = self.bat_discharge_hours
        elect_price_hourly_fast = self.elect_price_hourly
        elect_revenue_per_hour_arr_fast = self.elect_revenue_per_hour_arr
        pv_prediction_wh_fast = self.pv_prediction_wh
        battery_fast = self.battery
        ev_fast = self.ev
        home_appliance_fast = self.home_appliance
        inverter_fast = self.inverter

        # Check for simulation integrity (in a way that mypy understands)
        if (
            load_energy_array_fast is None
            or pv_prediction_wh_fast is None
            or elect_price_hourly_fast is None
            or ev_charge_hours_fast is None
            or ac_charge_hours_fast is None
            or dc_charge_hours_fast is None
            or elect_revenue_per_hour_arr_fast is None
            or bat_discharge_hours_fast is None
            or ev_discharge_hours_fast is None
        ):
            missing = []
            if load_energy_array_fast is None:
                missing.append("Load Energy Array")
            if pv_prediction_wh_fast is None:
                missing.append("PV Prediction Wh")
            if elect_price_hourly_fast is None:
                missing.append("Electricity Price Hourly")
            if ev_charge_hours_fast is None:
                missing.append("EV Charge Hours")
            if ac_charge_hours_fast is None:
                missing.append("AC Charge Hours")
            if dc_charge_hours_fast is None:
                missing.append("DC Charge Hours")
            if elect_revenue_per_hour_arr_fast is None:
                missing.append("Electricity Revenue Per Hour")
            if bat_discharge_hours_fast is None:
                missing.append("Genetic0Battery Discharge Hours")
            if ev_discharge_hours_fast is None:
                missing.append("EV Discharge Hours")
            msg = ", ".join(missing)
            logger.error("Mandatory data missing - %s", msg)
            raise ValueError(f"Mandatory data missing: {msg}")

        if not (
            len(load_energy_array_fast)
            == len(pv_prediction_wh_fast)
            == len(elect_price_hourly_fast)
        ):
            error_msg = f"Array sizes do not match: Load Curve = {len(load_energy_array_fast)}, PV Forecast = {len(pv_prediction_wh_fast)}, Electricity Price = {len(elect_price_hourly_fast)}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        end_hour = len(load_energy_array_fast)
        total_hours = end_hour - start_hour

        # Pre-allocate arrays for the results, optimized for speed
        loads_energy_per_hour = np.full((total_hours), np.nan)
        feedin_energy_per_hour = np.full((total_hours), np.nan)
        consumption_energy_per_hour = np.full((total_hours), np.nan)
        costs_per_hour = np.full((total_hours), np.nan)
        revenue_per_hour = np.full((total_hours), np.nan)
        losses_wh_per_hour = np.full((total_hours), np.nan)
        electricity_price_per_hour = np.full((total_hours), np.nan)

        # Set initial state
        if battery_fast:
            # Pre-allocate arrays for the results, optimized for speed
            soc_per_hour = np.full((total_hours), np.nan)

            soc_per_hour[0] = battery_fast.current_soc_percentage()

            # Determine AC charging availability from inverter parameters
            if inverter_fast:
                ac_to_dc_eff_fast = inverter_fast.ac_to_dc_efficiency
                dc_to_ac_eff_fast = inverter_fast.dc_to_ac_efficiency
                max_ac_charge_w_fast = inverter_fast.max_ac_charge_power_w
            else:
                ac_to_dc_eff_fast = 1.0
                dc_to_ac_eff_fast = 1.0
                max_ac_charge_w_fast = None

            ac_charging_possible = ac_to_dc_eff_fast > 0 and (
                max_ac_charge_w_fast is None or max_ac_charge_w_fast > 0
            )

            # If AC charging is disabled via inverter, zero out AC charge hours
            if not ac_charging_possible:
                ac_charge_hours_fast = np.zeros_like(ac_charge_hours_fast)

            # Fill the charge array of the battery
            dc_charge_hours_fast[0:start_hour] = 0
            dc_charge_hours_fast[end_hour:] = 0
            ac_charge_hours_fast[0:start_hour] = 0
            ac_charge_hours_fast[end_hour:] = 0
            battery_fast.charge_array = np.where(
                ac_charge_hours_fast != 0, ac_charge_hours_fast, dc_charge_hours_fast
            )
            # Fill the discharge array of the battery
            bat_discharge_hours_fast[0:start_hour] = 0
            bat_discharge_hours_fast[end_hour:] = 0
            battery_fast.discharge_array = bat_discharge_hours_fast
        else:
            # Default return if no battery is available
            soc_per_hour = np.full((total_hours), 0)
            ac_to_dc_eff_fast = 1.0
            dc_to_ac_eff_fast = 1.0
            max_ac_charge_w_fast = None
            ac_charging_possible = False

        if ev_fast:
            # Pre-allocate arrays for the results, optimized for speed
            soc_ev_per_hour = np.full((total_hours), np.nan)

            soc_ev_per_hour[0] = ev_fast.current_soc_percentage()
            # Fill the charge array of the ev
            ev_charge_hours_fast[0:start_hour] = 0
            ev_charge_hours_fast[end_hour:] = 0
            ev_fast.charge_array = ev_charge_hours_fast
            # Fill the discharge array of the ev
            ev_discharge_hours_fast[0:start_hour] = 0
            ev_discharge_hours_fast[end_hour:] = 0
            ev_fast.discharge_array = ev_discharge_hours_fast
        else:
            # Default return if no electric vehicle is available
            soc_ev_per_hour = np.full((total_hours), 0)

        if home_appliance_fast and self.home_appliance_start_hour is not None:
            home_appliance_enabled = True
            # Pre-allocate arrays for the results, optimized for speed
            home_appliance_wh_per_hour = np.full((total_hours), np.nan)

            self.home_appliance_start_hour = home_appliance_fast.set_starting_time(
                self.home_appliance_start_hour, start_hour
            )
        else:
            home_appliance_enabled = False
            # Default return if no home appliance is available
            home_appliance_wh_per_hour = np.full((total_hours), 0)

        for hour in range(start_hour, end_hour):
            hour_idx = hour - start_hour

            # Accumulate loads and PV generation
            consumption = load_energy_array_fast[hour]
            losses_wh_per_hour[hour_idx] = 0.0

            # Home appliances
            if home_appliance_enabled:
                ha_load = home_appliance_fast.get_load_for_hour(hour)  # type: ignore[union-attr]
                consumption += ha_load
                home_appliance_wh_per_hour[hour_idx] = ha_load

            # E-Auto handling
            if ev_fast:
                soc_ev_per_hour[hour_idx] = ev_fast.current_soc_percentage()  # save begin state
                if ev_charge_hours_fast[hour] > 0:
                    loaded_energy_ev, ev_charge_losses = ev_fast.charge_energy(
                        wh=None, hour=hour, charge_factor=ev_charge_hours_fast[hour]
                    )
                    consumption += loaded_energy_ev
                    losses_wh_per_hour[hour_idx] += ev_charge_losses

            # Save battery SOC before inverter processing = true begin-of-interval state.
            # Must be recorded here (before DC charge/discharge) so the displayed SOC at
            # timestamp T reflects what the battery actually had at the START of interval T,
            # not the post-DC result. Consistent with the EV SOC convention above.
            if battery_fast:
                soc_per_hour[hour_idx] = battery_fast.current_soc_percentage()

            # Process inverter logic
            energy_feedin_grid_actual = energy_consumption_grid_actual = losses = (
                self_consumption
            ) = 0.0

            if inverter_fast:
                energy_produced = pv_prediction_wh_fast[hour]
                (
                    energy_feedin_grid_actual,
                    energy_consumption_grid_actual,
                    losses,
                    self_consumption,
                ) = inverter_fast.process_energy(energy_produced, consumption, hour)

            # AC PV Battery Charge
            if battery_fast:
                hour_ac_charge = ac_charge_hours_fast[hour]
                if hour_ac_charge > 0.0 and ac_charging_possible:
                    # Cap charge factor by max_ac_charge_power_w if set
                    effective_charge_factor = hour_ac_charge
                    if max_ac_charge_w_fast is not None and battery_fast.max_charge_power_w > 0:
                        # DC power = max_charge_power_w * factor
                        # AC power = DC power / ac_to_dc_eff
                        # AC power must be <= max_ac_charge_power_w
                        max_dc_factor = (
                            max_ac_charge_w_fast * ac_to_dc_eff_fast
                        ) / battery_fast.max_charge_power_w
                        effective_charge_factor = min(effective_charge_factor, max_dc_factor)

                    if effective_charge_factor > 0:
                        battery_charged_energy_actual, battery_losses_actual = (
                            battery_fast.charge_energy(
                                None, hour, charge_factor=effective_charge_factor
                            )
                        )

                        # DC energy entering the battery (before battery internal efficiency)
                        dc_energy = battery_charged_energy_actual + battery_losses_actual
                        # AC energy consumed from grid (accounts for AC→DC conversion loss)
                        ac_energy = dc_energy / ac_to_dc_eff_fast
                        # Genetic0Inverter AC→DC conversion losses
                        inverter_charge_losses = ac_energy - dc_energy

                        consumption += ac_energy
                        energy_consumption_grid_actual += ac_energy
                        losses_wh_per_hour[hour_idx] += (
                            battery_losses_actual + inverter_charge_losses
                        )

            # Update hourly arrays
            feedin_energy_per_hour[hour_idx] = energy_feedin_grid_actual
            consumption_energy_per_hour[hour_idx] = energy_consumption_grid_actual
            losses_wh_per_hour[hour_idx] += losses
            loads_energy_per_hour[hour_idx] = consumption
            hourly_electricity_price = elect_price_hourly_fast[hour]
            electricity_price_per_hour[hour_idx] = hourly_electricity_price

            # Financial calculations
            costs_per_hour[hour_idx] = energy_consumption_grid_actual * hourly_electricity_price
            revenue_per_hour[hour_idx] = (
                energy_feedin_grid_actual * elect_revenue_per_hour_arr_fast[hour]
            )

        total_cost = np.nansum(costs_per_hour)
        total_losses = np.nansum(losses_wh_per_hour)
        total_revenue = np.nansum(revenue_per_hour)

        # Prepare output dictionary
        return {
            "Last_Wh_pro_Stunde": loads_energy_per_hour,
            "Netzeinspeisung_Wh_pro_Stunde": feedin_energy_per_hour,
            "Netzbezug_Wh_pro_Stunde": consumption_energy_per_hour,
            "Kosten_Euro_pro_Stunde": costs_per_hour,
            "akku_soc_pro_stunde": soc_per_hour,
            "Einnahmen_Euro_pro_Stunde": revenue_per_hour,
            "Gesamtbilanz_Euro": total_cost - total_revenue,  # Fitness score ("FitnessMin")
            "EAuto_SoC_pro_Stunde": soc_ev_per_hour,
            "Gesamteinnahmen_Euro": total_revenue,
            "Gesamtkosten_Euro": total_cost,
            "Verluste_Pro_Stunde": losses_wh_per_hour,
            "Gesamt_Verluste": total_losses,
            "Home_appliance_wh_per_hour": home_appliance_wh_per_hour,
            "Electricity_price": electricity_price_per_hour,
        }
