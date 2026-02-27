"""Genetic algorithm."""

import random
import time
from typing import Any, Optional

import numpy as np
from deap import algorithms, base, creator, tools
from loguru import logger
from numpydantic import NDArray, Shape
from pydantic import ConfigDict, Field

from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.devices.genetic.battery import Battery
from akkudoktoreos.devices.genetic.homeappliance import HomeAppliance
from akkudoktoreos.devices.genetic.inverter import Inverter
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticEnergyManagementParameters,
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticsolution import (
    GeneticSimulationResult,
    GeneticSolution,
)
from akkudoktoreos.optimization.optimizationabc import OptimizationBase


class GeneticSimulation(PydanticBaseModel):
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
            "description": "An array of floats representing the electricity price in euros per watt-hour for different time intervals."
        },
    )
    elect_revenue_per_hour_arr: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        json_schema_extra={
            "description": "An array of floats representing the feed-in compensation in euros per watt-hour."
        },
    )

    battery: Optional[Battery] = Field(default=None, json_schema_extra={"description": "TBD."})
    ev: Optional[Battery] = Field(default=None, json_schema_extra={"description": "TBD."})
    home_appliance: Optional[HomeAppliance] = Field(
        default=None, json_schema_extra={"description": "TBD."}
    )
    inverter: Optional[Inverter] = Field(default=None, json_schema_extra={"description": "TBD."})

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
        parameters: GeneticEnergyManagementParameters,
        optimization_hours: int,
        prediction_hours: int,
        ev: Optional[Battery] = None,
        home_appliance: Optional[HomeAppliance] = None,
        inverter: Optional[Inverter] = None,
    ) -> None:
        self.optimization_hours = optimization_hours
        self.prediction_hours = prediction_hours
        self.load_energy_array = np.array(parameters.gesamtlast, float)
        self.pv_prediction_wh = np.array(parameters.pv_prognose_wh, float)
        self.elect_price_hourly = np.array(parameters.strompreis_euro_pro_wh, float)
        self.elect_revenue_per_hour_arr = (
            parameters.einspeiseverguetung_euro_pro_wh
            if isinstance(parameters.einspeiseverguetung_euro_pro_wh, list)
            else np.full(
                len(self.load_energy_array), parameters.einspeiseverguetung_euro_pro_wh, float
            )
        )
        if inverter:
            self.battery = inverter.battery
        else:
            self.battery = None
        self.ev = ev
        self.home_appliance = home_appliance
        self.inverter = inverter
        self.ac_charge_hours = np.full(self.prediction_hours, 0.0)
        self.dc_charge_hours = np.full(self.prediction_hours, 0.0)
        self.bat_discharge_hours = np.full(self.prediction_hours, 0.0)
        self.ev_charge_hours = np.full(self.prediction_hours, 0.0)
        self.ev_discharge_hours = np.full(self.prediction_hours, 0.0)
        self.home_appliance_start_hour = None
        """Prepare simulation runs."""
        self.load_energy_array = np.array(parameters.gesamtlast, float)
        self.pv_prediction_wh = np.array(parameters.pv_prognose_wh, float)
        self.elect_price_hourly = np.array(parameters.strompreis_euro_pro_wh, float)
        self.elect_revenue_per_hour_arr = (
            parameters.einspeiseverguetung_euro_pro_wh
            if isinstance(parameters.einspeiseverguetung_euro_pro_wh, list)
            else np.full(
                len(self.load_energy_array), parameters.einspeiseverguetung_euro_pro_wh, float
            )
        )

    def reset(self) -> None:
        if self.ev:
            self.ev.reset()
        if self.battery:
            self.battery.reset()
        self.home_appliance_start_hour = None

    def simulate(self, start_hour: int) -> dict[str, Any]:
        """Simulate energy usage and costs for the given start hour.

        akku_soc_pro_stunde begin of the hour, initial hour state!
        last_wh_pro_stunde integral of last hour (end state)
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
                missing.append("Battery Discharge Hours")
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

        if home_appliance_fast and self.home_appliance_start_hour:
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
                    loaded_energy_ev, verluste_eauto = ev_fast.charge_energy(
                        wh=None, hour=hour, charge_factor=ev_charge_hours_fast[hour]
                    )
                    consumption += loaded_energy_ev
                    losses_wh_per_hour[hour_idx] += verluste_eauto

            # Process inverter logic
            energy_feedin_grid_actual = energy_consumption_grid_actual = losses = eigenverbrauch = (
                0.0
            )

            if inverter_fast:
                energy_produced = pv_prediction_wh_fast[hour]
                (
                    energy_feedin_grid_actual,
                    energy_consumption_grid_actual,
                    losses,
                    eigenverbrauch,
                ) = inverter_fast.process_energy(energy_produced, consumption, hour)

            # AC PV Battery Charge
            if battery_fast:
                soc_per_hour[hour_idx] = battery_fast.current_soc_percentage()  # save begin state
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
                        # Inverter AC→DC conversion losses
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


class GeneticOptimization(OptimizationBase):
    """GENETIC algorithm to solve energy optimization."""

    def __init__(
        self,
        verbose: bool = False,
        fixed_seed: Optional[int] = None,
    ):
        """Initialize the optimization problem with the required parameters."""
        self.opti_param: dict[str, Any] = {}
        self.fixed_eauto_hours = (
            self.config.prediction.hours - self.config.optimization.horizon_hours
        )
        self.ev_possible_charge_values: list[float] = [1.0]
        # Separate charge-level list for battery AC charging (independent of EV rates).
        # Populated from parameters.pv_akku.charge_rates in optimierung_ems.
        self.bat_possible_charge_values: list[float] = [1.0]
        self.verbose = verbose
        self.fix_seed = fixed_seed
        self.optimize_ev = True
        self.optimize_dc_charge = False
        self.fitness_history: dict[str, Any] = {}

        # Set a fixed seed for random operations if provided or in debug mode
        if self.fix_seed is not None:
            random.seed(self.fix_seed)
        elif logger.level == "DEBUG":
            self.fix_seed = random.randint(1, 100000000000)  # noqa: S311
            random.seed(self.fix_seed)

        # Create Simulation
        self.simulation = GeneticSimulation()

    def decode_charge_discharge(
        self, discharge_hours_bin: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Decode the input array into ac_charge, dc_charge, and discharge arrays."""
        discharge_hours_bin_np = np.array(discharge_hours_bin)
        # Battery AC charge uses its own charge-level list (bat_possible_charge_values).
        len_bat = len(self.bat_possible_charge_values)

        # Categorization (using battery charge levels):
        # Idle:       0 .. len_bat-1
        # Discharge:  len_bat .. 2*len_bat - 1
        # AC Charge:  2*len_bat .. 3*len_bat - 1  (maps to bat_possible_charge_values)
        # DC optional: 3*len_bat (not allowed), 3*len_bat + 1 (allowed)

        # Idle states
        idle_mask = (discharge_hours_bin_np >= 0) & (discharge_hours_bin_np < len_bat)

        # Discharge states
        discharge_mask = (discharge_hours_bin_np >= len_bat) & (
            discharge_hours_bin_np < 2 * len_bat
        )

        # AC states
        ac_mask = (discharge_hours_bin_np >= 2 * len_bat) & (discharge_hours_bin_np < 3 * len_bat)
        ac_indices = (discharge_hours_bin_np[ac_mask] - 2 * len_bat).astype(int)

        # DC states (if enabled)
        if self.optimize_dc_charge:
            dc_not_allowed_state = 3 * len_bat
            dc_allowed_state = 3 * len_bat + 1
            dc_charge = np.where(discharge_hours_bin_np == dc_allowed_state, 1, 0)
        else:
            dc_charge = np.ones_like(discharge_hours_bin_np, dtype=float)

        # Generate the result arrays
        discharge = np.zeros_like(discharge_hours_bin_np, dtype=int)
        discharge[discharge_mask] = 1  # Set Discharge states to 1

        ac_charge = np.zeros_like(discharge_hours_bin_np, dtype=float)
        ac_charge[ac_mask] = [self.bat_possible_charge_values[i] for i in ac_indices]

        # Idle is just 0, already default.

        return ac_charge, dc_charge, discharge

    def mutate(self, individual: list[int]) -> tuple[list[int]]:
        """Custom mutation function for the individual."""
        # Calculate the number of states using battery charge levels
        len_bat = len(self.bat_possible_charge_values)
        if self.optimize_dc_charge:
            total_states = 3 * len_bat + 2
        else:
            total_states = 3 * len_bat

        # 1. Mutating the charge_discharge part
        charge_discharge_part = individual[: self.config.prediction.hours]
        (charge_discharge_mutated,) = self.toolbox.mutate_charge_discharge(charge_discharge_part)

        # Instead of a fixed clamping to 0..8 or 0..6 dynamically:
        charge_discharge_mutated = np.clip(charge_discharge_mutated, 0, total_states - 1)
        individual[: self.config.prediction.hours] = charge_discharge_mutated

        # 2. Mutating the EV charge part, if active
        if self.optimize_ev:
            ev_charge_part = individual[
                self.config.prediction.hours : self.config.prediction.hours * 2
            ]
            (ev_charge_part_mutated,) = self.toolbox.mutate_ev_charge_index(ev_charge_part)
            ev_charge_part_mutated[self.config.prediction.hours - self.fixed_eauto_hours :] = [
                0
            ] * self.fixed_eauto_hours
            individual[self.config.prediction.hours : self.config.prediction.hours * 2] = (
                ev_charge_part_mutated
            )

        # 3. Mutating the appliance start time, if applicable
        if self.opti_param["home_appliance"] > 0:
            appliance_part = [individual[-1]]
            (appliance_part_mutated,) = self.toolbox.mutate_hour(appliance_part)
            individual[-1] = appliance_part_mutated[0]

        return (individual,)

    # Method to create an individual based on the conditions
    def create_individual(self) -> list[int]:
        # Start with discharge states for the individual
        individual_components = [
            self.toolbox.attr_discharge_state() for _ in range(self.config.prediction.hours)
        ]

        # Add EV charge index values if optimize_ev is True
        if self.optimize_ev:
            individual_components += [
                self.toolbox.attr_ev_charge_index() for _ in range(self.config.prediction.hours)
            ]

        # Add the start time of the household appliance if it's being optimized
        if self.opti_param["home_appliance"] > 0:
            individual_components += [self.toolbox.attr_int()]

        return creator.Individual(individual_components)

    def merge_individual(
        self,
        discharge_hours_bin: np.ndarray,
        eautocharge_hours_index: Optional[np.ndarray],
        washingstart_int: Optional[int],
    ) -> list[int]:
        """Merge the individual components back into a single solution list.

        Parameters:
            discharge_hours_bin (np.ndarray): Binary discharge hours.
            eautocharge_hours_index (Optional[np.ndarray]): EV charge hours as integers, or None.
            washingstart_int (Optional[int]): Dishwasher start time as integer, or None.

        Returns:
            list[int]: The merged individual solution as a list of integers.
        """
        # Start with the discharge hours
        individual = discharge_hours_bin.tolist()

        # Add EV charge hours if applicable
        if self.optimize_ev and eautocharge_hours_index is not None:
            individual.extend(eautocharge_hours_index.tolist())
        elif self.optimize_ev:
            # Falls optimize_ev aktiv ist, aber keine EV-Daten vorhanden sind, fügen wir Nullen hinzu
            individual.extend([0] * self.config.prediction.hours)

        # Add dishwasher start time if applicable
        if self.opti_param.get("home_appliance", 0) > 0 and washingstart_int is not None:
            individual.append(washingstart_int)
        elif self.opti_param.get("home_appliance", 0) > 0:
            # Falls ein Haushaltsgerät optimiert wird, aber kein Startzeitpunkt vorhanden ist
            individual.append(0)

        return individual

    def split_individual(
        self, individual: list[int]
    ) -> tuple[np.ndarray, Optional[np.ndarray], Optional[int]]:
        """Split the individual solution into its components.

        Components:
        1. Discharge hours (binary as int NumPy array),
        2. Electric vehicle charge hours (float as int NumPy array, if applicable),
        3. Dishwasher start time (integer if applicable).
        """
        # Discharge hours as a NumPy array of ints
        discharge_hours_bin = np.array(individual[: self.config.prediction.hours], dtype=int)

        # EV charge hours as a NumPy array of ints (if optimize_ev is True)
        eautocharge_hours_index = (
            # append ev charging states to individual
            np.array(
                individual[self.config.prediction.hours : self.config.prediction.hours * 2],
                dtype=int,
            )
            if self.optimize_ev
            else None
        )

        # Washing machine start time as an integer (if applicable)
        washingstart_int = (
            int(individual[-1])
            if self.opti_param and self.opti_param.get("home_appliance", 0) > 0
            else None
        )

        return discharge_hours_bin, eautocharge_hours_index, washingstart_int

    def setup_deap_environment(self, opti_param: dict[str, Any], start_hour: int) -> None:
        """Set up the DEAP environment with fitness and individual creation rules."""
        self.opti_param = opti_param

        # Remove existing definitions if any
        for attr in ["FitnessMin", "Individual"]:
            if attr in creator.__dict__:
                del creator.__dict__[attr]

        creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
        creator.create("Individual", list, fitness=creator.FitnessMin)

        self.toolbox = base.Toolbox()
        # Battery state space uses bat_possible_charge_values; EV index space uses ev_possible_charge_values.
        len_bat = len(self.bat_possible_charge_values)
        len_ev = len(self.ev_possible_charge_values)

        # Total battery/discharge states:
        # Idle:      len_bat states
        # Discharge: len_bat states
        # AC-Charge: len_bat states  (maps to bat_possible_charge_values)
        # With DC: + 2 additional states
        if self.optimize_dc_charge:
            total_states = 3 * len_bat + 2
        else:
            total_states = 3 * len_bat

        # State space: 0 .. (total_states - 1)
        self.toolbox.register("attr_discharge_state", random.randint, 0, total_states - 1)

        # EV attributes (separate index space)
        if self.optimize_ev:
            self.toolbox.register(
                "attr_ev_charge_index",
                random.randint,
                0,
                len_ev - 1,
            )

        # Household appliance start time
        self.toolbox.register("attr_int", random.randint, start_hour, 23)

        self.toolbox.register("individual", self.create_individual)
        self.toolbox.register("population", tools.initRepeat, list, self.toolbox.individual)
        self.toolbox.register("mate", tools.cxTwoPoint)

        # Mutation operator for battery charge/discharge states
        self.toolbox.register(
            "mutate_charge_discharge", tools.mutUniformInt, low=0, up=total_states - 1, indpb=0.2
        )

        # Mutation operator for EV states (separate index space)
        self.toolbox.register(
            "mutate_ev_charge_index",
            tools.mutUniformInt,
            low=0,
            up=len_ev - 1,
            indpb=0.2,
        )

        # Mutation for household appliance
        self.toolbox.register("mutate_hour", tools.mutUniformInt, low=start_hour, up=23, indpb=0.2)

        # Custom mutate function remains unchanged
        self.toolbox.register("mutate", self.mutate)
        self.toolbox.register("select", tools.selTournament, tournsize=3)

    def evaluate_inner(self, individual: list[int]) -> dict[str, Any]:
        """Simulates the energy management system (EMS) using the provided individual solution.

        This is an internal function.
        """
        self.simulation.reset()
        discharge_hours_bin, eautocharge_hours_index, washingstart_int = self.split_individual(
            individual
        )

        if self.opti_param.get("home_appliance", 0) > 0 and washingstart_int:
            # Set start hour for appliance
            self.simulation.home_appliance_start_hour = washingstart_int

        ac_charge_hours, dc_charge_hours, discharge = self.decode_charge_discharge(
            discharge_hours_bin
        )

        self.simulation.bat_discharge_hours = discharge
        # Set DC charge hours only if DC optimization is enabled
        if self.optimize_dc_charge:
            self.simulation.dc_charge_hours = dc_charge_hours
        else:
            self.simulation.dc_charge_hours = np.full(self.config.prediction.hours, 1)
        self.simulation.ac_charge_hours = ac_charge_hours

        if eautocharge_hours_index is not None:
            eautocharge_hours_float = np.array(
                [self.ev_possible_charge_values[i] for i in eautocharge_hours_index],
                float,
            )
            # discharge is set to 0 by default
            self.simulation.ev_charge_hours = eautocharge_hours_float
        else:
            # discharge is set to 0 by default
            self.simulation.ev_charge_hours = np.full(self.config.prediction.hours, 0)

        # Do the simulation and return result.
        return self.simulation.simulate(self.ems.start_datetime.hour)

    def evaluate(
        self,
        individual: list[int],
        parameters: GeneticOptimizationParameters,
        start_hour: int,
        worst_case: bool,
    ) -> tuple[float]:
        """Evaluate the fitness score of a single individual in the DEAP genetic algorithm.

        This method runs a simulation based on the provided individual genome and
        optimization parameters. The resulting performance is converted into a
        fitness score compatible with DEAP (i.e., returned as a 1-tuple).

        Args:
            individual (list[int]):
                The genome representing one candidate solution.
            parameters (GeneticOptimizationParameters):
                Optimization parameters that influence simulation behavior,
                constraints, and scoring logic.
            start_hour (int):
                The simulation start hour (0–23 or domain-specific).
                Used to initialize time-based scheduling or constraints.
            worst_case (bool):
                If True, evaluates the solution under worst-case assumptions
                (e.g., pessimistic forecasts or boundary conditions).
                If False, uses nominal assumptions.

        Returns:
            tuple[float]:
                A single-element tuple containing the computed fitness score.
                Lower score is better: "FitnessMin".

        Raises:
            ValueError: If input arguments are invalid or the individual structure
                is not compatible with the simulation.
            RuntimeError: If the simulation fails or cannot produce results.

        Notes:
            The resulting score should match DEAP's expected format: a tuple, even
            if only a single scalar fitness value is returned.
        """
        try:
            simulation_result = self.evaluate_inner(individual)
        except Exception as e:
            # Return bad fitness score ("FitnessMin") in case of an exception
            return (100000.0,)

        gesamtbilanz = simulation_result["Gesamtbilanz_Euro"] * (-1.0 if worst_case else 1.0)

        # EV 100% & charge not allowed
        if self.optimize_ev:
            discharge_hours_bin, eautocharge_hours_index, washingstart_int = self.split_individual(
                individual
            )

            eauto_soc_per_hour = np.array(
                simulation_result.get("EAuto_SoC_pro_Stunde", [])
            )  # Beispielkey

            if eauto_soc_per_hour is None or eautocharge_hours_index is None:
                raise ValueError("eauto_soc_per_hour or eautocharge_hours_index is None")
            min_length = min(eauto_soc_per_hour.size, eautocharge_hours_index.size)
            eauto_soc_per_hour_tail = eauto_soc_per_hour[-min_length:]
            eautocharge_hours_index_tail = eautocharge_hours_index[-min_length:]

            # Mask
            invalid_charge_mask = (eauto_soc_per_hour_tail == 100) & (
                eautocharge_hours_index_tail > 0
            )

            if np.any(invalid_charge_mask):
                invalid_indices = np.where(invalid_charge_mask)[0]
                if len(invalid_indices) > 1:
                    eautocharge_hours_index_tail[invalid_indices[1:]] = 0

                eautocharge_hours_index[-min_length:] = eautocharge_hours_index_tail.tolist()

                adjusted_individual = self.merge_individual(
                    discharge_hours_bin, eautocharge_hours_index, washingstart_int
                )

                individual[:] = adjusted_individual

        # New check: Activate discharge when battery SoC is 0
        # battery_soc_per_hour = np.array(
        #     o.get("akku_soc_pro_stunde", [])
        # )  # Example key for battery SoC

        # if battery_soc_per_hour is not None:
        #     if battery_soc_per_hour is None or discharge_hours_bin is None:
        #         raise ValueError("battery_soc_per_hour or discharge_hours_bin is None")
        #     min_length = min(battery_soc_per_hour.size, discharge_hours_bin.size)
        #     battery_soc_per_hour_tail = battery_soc_per_hour[-min_length:]
        #     discharge_hours_bin_tail = discharge_hours_bin[-min_length:]
        #     len_ac = len(self.config.optimization.ev_available_charge_rates_percent)

        #     # # Find hours where battery SoC is 0
        #     # zero_soc_mask = battery_soc_per_hour_tail == 0
        #     # discharge_hours_bin_tail[zero_soc_mask] = (
        #     #     len_ac + 2
        #     # )  # Activate discharge for these hours

        #     # When Battery SoC then set the Discharge randomly to 0 or 1. otherwise it's very
        #     # unlikely to get a state where a battery can store energy for a longer time
        #     # Find hours where battery SoC is 0
        #     zero_soc_mask = battery_soc_per_hour_tail == 0
        #     # discharge_hours_bin_tail[zero_soc_mask] = (
        #     # len_ac + 2
        #     # )  # Activate discharge for these hours
        #     set_to_len_ac_plus_2 = np.random.rand() < 0.5  # True mit 50% Wahrscheinlichkeit

        #     # Werte setzen basierend auf der zufälligen Entscheidung
        #     value_to_set = len_ac + 2 if set_to_len_ac_plus_2 else 0
        #     discharge_hours_bin_tail[zero_soc_mask] = value_to_set

        #     # Merge the updated discharge_hours_bin back into the individual
        #     adjusted_individual = self.merge_individual(
        #         discharge_hours_bin, eautocharge_hours_index, washingstart_int
        #     )
        #     individual[:] = adjusted_individual

        # More metrics
        individual.extra_data = (  # type: ignore[attr-defined]
            simulation_result["Gesamtbilanz_Euro"],
            simulation_result["Gesamt_Verluste"],
            parameters.eauto.min_soc_percentage - self.simulation.ev.current_soc_percentage()
            if parameters.eauto and self.simulation.ev
            else 0,
        )

        # Adjust total balance with battery value and penalties for unmet SOC
        if self.simulation.battery:
            battery_energy_content = self.simulation.battery.current_energy_content()
            # Apply DC→AC inverter efficiency to residual battery value
            # (stored DC energy must pass through inverter to be usable as AC)
            if self.simulation.inverter:
                battery_energy_content *= self.simulation.inverter.dc_to_ac_efficiency
            restwert_akku = battery_energy_content * parameters.ems.preis_euro_pro_wh_akku
            gesamtbilanz += -restwert_akku

        # --- AC charging break-even penalty ---
        # Penalise AC charging decisions that cannot be economically justified given the
        # round-trip losses (AC→DC charge conversion, battery internal, DC→AC discharge
        # conversion) and the best available future electricity prices.
        #
        # Key insight: energy already stored in the battery (from PV, zero grid cost) covers
        # the most expensive future hours first.  AC charging from the grid only makes sense
        # for the hours that remain uncovered, and only when the discharge price exceeds
        # P_charge / η_round_trip.
        #
        # This penalty does not double-count the simulation result – it amplifies the "bad
        # decision" signal so that the genetic algorithm converges faster away from
        # unprofitable charging regions.
        if (
            self.simulation.battery
            and self.simulation.inverter
            and self.simulation.ac_charge_hours is not None
            and self.simulation.elect_price_hourly is not None
            and self.simulation.load_energy_array is not None
        ):
            inv = self.simulation.inverter
            bat = self.simulation.battery

            # Full round-trip efficiency: 1 Wh drawn from grid → η Wh delivered to AC load
            round_trip_eff = (
                inv.ac_to_dc_efficiency
                * bat.charging_efficiency
                * bat.discharging_efficiency
                * inv.dc_to_ac_efficiency
            )

            if round_trip_eff > 0:
                ac_charge_arr = self.simulation.ac_charge_hours
                prices_arr = self.simulation.elect_price_hourly
                load_arr = self.simulation.load_energy_array
                n = len(prices_arr)

                # Usable AC energy already in battery from prior PV charging (zero grid cost).
                # This covers the most expensive future hours first, pushing AC charging demand
                # to cheaper hours where the break-even hurdle may not be met.
                initial_soc_wh = (bat.initial_soc_percentage / 100.0) * bat.capacity_wh
                free_ac_wh = (
                    max(0.0, initial_soc_wh - bat.min_soc_wh)
                    * bat.discharging_efficiency
                    * inv.dc_to_ac_efficiency
                )

                # Configurable penalty multiplier (default 1 = economic loss in €)
                try:
                    ac_penalty_factor = float(
                        self.config.optimization.genetic.penalties["ac_charge_break_even"]
                    )
                except Exception:
                    ac_penalty_factor = 1.0

                for hour in range(start_hour, min(len(ac_charge_arr), n)):
                    ac_factor = ac_charge_arr[hour]
                    if ac_factor <= 0.0:
                        continue

                    charge_price = prices_arr[hour]
                    if charge_price <= 0:
                        continue

                    # Price that a future discharge hour must reach to break even
                    break_even_price = charge_price / round_trip_eff

                    # Build list of (price, load_wh) for all future hours in the horizon
                    future = [
                        (float(prices_arr[h]), float(load_arr[h])) for h in range(hour + 1, n)
                    ]
                    # Sort descending by price so we "use" the most expensive hours first
                    future.sort(key=lambda x: -x[0])

                    # Consume free PV energy against the highest-price future hours.
                    # The first uncovered (partially or fully) hour defines the best
                    # price still available for the new AC charge.
                    remaining_free = free_ac_wh
                    best_uncovered_price = 0.0
                    for fp, fl in future:
                        if remaining_free >= fl:
                            # Entire expensive hour is already covered by free PV energy
                            remaining_free -= fl
                        else:
                            # First hour not (fully) covered: this is where new charge goes
                            best_uncovered_price = fp
                            break

                    if best_uncovered_price < break_even_price:
                        # AC charging at this hour is economically unjustified.
                        # Penalty = excess cost per Wh × DC energy requested this hour.
                        dc_wh = bat.max_charge_power_w * ac_factor
                        ac_wh = dc_wh / max(inv.ac_to_dc_efficiency, 1e-9)
                        excess_cost_per_wh = break_even_price - best_uncovered_price
                        gesamtbilanz += ac_wh * excess_cost_per_wh * ac_penalty_factor

        if self.optimize_ev and parameters.eauto and self.simulation.ev:
            try:
                penalty = self.config.optimization.genetic.penalties["ev_soc_miss"]
            except:
                # Use default
                penalty = 10
                logger.error(
                    "Penalty function parameter `ev_soc_miss` not configured, using {}.", penalty
                )
            ev_soc_percentage = self.simulation.ev.current_soc_percentage()
            if (
                ev_soc_percentage < parameters.eauto.min_soc_percentage
                or ev_soc_percentage > parameters.eauto.max_soc_percentage
            ):
                gesamtbilanz += (
                    abs(parameters.eauto.min_soc_percentage - ev_soc_percentage) * penalty
                )

        return (gesamtbilanz,)

    def optimize(
        self,
        start_solution: Optional[list[float]] = None,
        ngen: int = 200,
    ) -> tuple[Any, dict[str, list[Any]]]:
        """Run the optimization process using a genetic algorithm.

        @TODO: optimize() ngen default (200) is different from optimierung_ems() ngen default (400).
        """
        # Set the number of inviduals in a generation
        try:
            individuals = self.config.optimization.genetic.individuals
            if individuals is None:
                raise
        except:
            individuals = 300
            logger.error("Individuals not configured. Using {}.", individuals)

        population = self.toolbox.population(n=individuals)
        hof = tools.HallOfFame(1)
        stats = tools.Statistics(lambda ind: ind.fitness.values)
        stats.register("min", np.min)
        stats.register("avg", np.mean)
        stats.register("max", np.max)

        logger.debug("Start optimize: {}", start_solution)

        # Insert the start solution into the population if provided
        if start_solution is not None:
            for _ in range(10):
                population.insert(0, creator.Individual(start_solution))

        # Run the evolutionary algorithm
        pop, log = algorithms.eaMuPlusLambda(
            population,
            self.toolbox,
            mu=100,
            lambda_=150,
            cxpb=0.6,
            mutpb=0.4,
            ngen=ngen,
            stats=stats,
            halloffame=hof,
            verbose=self.verbose,
        )

        # Store fitness history
        self.fitness_history = {
            "gen": log.select("gen"),  # Generation numbers (X-axis)
            "avg": log.select("avg"),  # Average fitness for each generation (Y-axis)
            "max": log.select("max"),  # Maximum fitness for each generation (Y-axis)
            "min": log.select("min"),  # Minimum fitness for each generation (Y-axis)
        }

        member: dict[str, list[float]] = {"bilanz": [], "verluste": [], "nebenbedingung": []}
        for ind in population:
            if hasattr(ind, "extra_data"):
                extra_value1, extra_value2, extra_value3 = ind.extra_data
                member["bilanz"].append(extra_value1)
                member["verluste"].append(extra_value2)
                member["nebenbedingung"].append(extra_value3)

        return hof[0], member

    def optimierung_ems(
        self,
        parameters: GeneticOptimizationParameters,
        start_hour: Optional[int] = None,
        worst_case: bool = False,
        ngen: Optional[int] = None,
    ) -> GeneticSolution:
        """Perform EMS (Energy Management System) optimization and visualize results."""
        if start_hour is None:
            start_hour = self.ems.start_datetime.hour
        # Start hour has to be in sync with energy management
        if start_hour != self.ems.start_datetime.hour:
            raise ValueError(
                f"Start hour not synced. EMS {self.ems.start_datetime.hour} vs. GENETIC {start_hour}."
            )

        # Set the number of generations
        generations = ngen
        if generations is None:
            try:
                generations = self.config.optimization.genetic.generations
            except:
                generations = 400
                logger.error("Generations not configured. Using {}.", generations)

        einspeiseverguetung_euro_pro_wh = np.full(
            self.config.prediction.hours, parameters.ems.einspeiseverguetung_euro_pro_wh
        )

        self.simulation.reset()

        # Initialize PV and EV batteries
        akku: Optional[Battery] = None
        if parameters.pv_akku:
            akku = Battery(
                parameters.pv_akku,
                prediction_hours=self.config.prediction.hours,
            )
            akku.set_charge_per_hour(np.full(self.config.prediction.hours, 0))

        eauto: Optional[Battery] = None
        if parameters.eauto:
            eauto = Battery(
                parameters.eauto,
                prediction_hours=self.config.prediction.hours,
            )
            eauto.set_charge_per_hour(np.full(self.config.prediction.hours, 1))
            self.optimize_ev = (
                parameters.eauto.min_soc_percentage - parameters.eauto.initial_soc_percentage >= 0
            )
            # electrical vehicle charge rates
            if parameters.eauto.charge_rates is not None:
                self.ev_possible_charge_values = parameters.eauto.charge_rates
            elif (
                self.config.devices.electric_vehicles
                and self.config.devices.electric_vehicles[0]
                and self.config.devices.electric_vehicles[0].charge_rates is not None
            ):
                self.ev_possible_charge_values = self.config.devices.electric_vehicles[
                    0
                ].charge_rates
            else:
                warning_msg = "No charge rates provided for electric vehicle - using default."
                logger.warning(warning_msg)
                self.ev_possible_charge_values = [
                    0.0,
                    0.1,
                    0.2,
                    0.3,
                    0.4,
                    0.5,
                    0.6,
                    0.7,
                    0.8,
                    0.9,
                    1.0,
                ]
        else:
            self.optimize_ev = False

        # Battery AC charge rates — use the battery's configured charge_rates so the
        # optimizer can select partial AC charge power (e.g. 10 %, 50 %, 100 %) instead
        # of always forcing full power.  Falls back to [1.0] when not configured.
        if parameters.pv_akku and parameters.pv_akku.charge_rates:
            self.bat_possible_charge_values = [
                r for r in parameters.pv_akku.charge_rates if r > 0.0
            ] or [1.0]
        elif (
            self.config.devices.batteries
            and self.config.devices.batteries[0]
            and self.config.devices.batteries[0].charge_rates
        ):
            self.bat_possible_charge_values = [
                r for r in self.config.devices.batteries[0].charge_rates if r > 0.0
            ] or [1.0]
        else:
            self.bat_possible_charge_values = [1.0]
        logger.debug("Battery AC charge levels: {}", self.bat_possible_charge_values)

        # Initialize household appliance if applicable
        dishwasher = (
            HomeAppliance(
                parameters=parameters.dishwasher,
                optimization_hours=self.config.optimization.horizon_hours,
                prediction_hours=self.config.prediction.hours,
            )
            if parameters.dishwasher is not None
            else None
        )

        # Initialize the inverter and energy management system
        inverter: Optional[Inverter] = None
        if parameters.inverter:
            inverter = Inverter(
                parameters.inverter,
                battery=akku,
            )

        # Prepare device simulation
        self.simulation.prepare(
            parameters=parameters.ems,
            optimization_hours=self.config.optimization.horizon_hours,
            prediction_hours=self.config.prediction.hours,
            inverter=inverter,  # battery is part of inverter
            ev=eauto,
            home_appliance=dishwasher,
        )

        # Setup the DEAP environment and optimization process
        self.setup_deap_environment({"home_appliance": 1 if dishwasher else 0}, start_hour)
        self.toolbox.register(
            "evaluate",
            lambda ind: self.evaluate(ind, parameters, start_hour, worst_case),
        )

        start_time = time.time()
        start_solution, extra_data = self.optimize(parameters.start_solution, ngen=generations)
        elapsed_time = time.time() - start_time
        logger.debug(f"Time evaluate inner: {elapsed_time:.4f} sec.")

        # Perform final evaluation on the best solution
        simulation_result = self.evaluate_inner(start_solution)

        # Prepare results
        discharge_hours_bin, eautocharge_hours_index, washingstart_int = self.split_individual(
            start_solution
        )
        # home appliance may have choosen a different appliance start hour
        if self.simulation.home_appliance:
            washingstart_int = self.simulation.home_appliance_start_hour

        eautocharge_hours_float = (
            [self.ev_possible_charge_values[i] for i in eautocharge_hours_index]
            if eautocharge_hours_index is not None
            else None
        )

        # Simulation may have changed something, use simulation values
        ac_charge_hours = self.simulation.ac_charge_hours
        if ac_charge_hours is None:
            ac_charge_hours = []
        else:
            ac_charge_hours = ac_charge_hours.tolist()
        dc_charge_hours = self.simulation.dc_charge_hours
        if dc_charge_hours is None:
            dc_charge_hours = []
        else:
            dc_charge_hours = dc_charge_hours.tolist()
        discharge = self.simulation.bat_discharge_hours
        if discharge is None:
            discharge = []
        else:
            discharge = discharge.tolist()

        # Visualize the results
        visualize = {
            "ac_charge": ac_charge_hours,
            "dc_charge": dc_charge_hours,
            "discharge_allowed": discharge,
            "eautocharge_hours_float": eautocharge_hours_float,
            "result": simulation_result,
            "eauto_obj": self.simulation.ev.to_dict() if self.simulation.ev else None,
            "start_solution": start_solution,
            "spuelstart": washingstart_int,
            "extra_data": extra_data,
            "fitness_history": self.fitness_history,
            "fixed_seed": self.fix_seed,
        }
        from akkudoktoreos.utils.visualize import prepare_visualize

        prepare_visualize(parameters, visualize, start_hour=start_hour)

        return GeneticSolution(
            **{
                "ac_charge": ac_charge_hours,
                "dc_charge": dc_charge_hours,
                "discharge_allowed": discharge,
                "eautocharge_hours_float": eautocharge_hours_float,
                "result": GeneticSimulationResult(**simulation_result),
                "eauto_obj": self.simulation.ev,
                "start_solution": start_solution,
                "washingstart": washingstart_int,
            }
        )
