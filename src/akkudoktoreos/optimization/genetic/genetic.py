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
        default=0, ge=0, le=23, description="Starting hour on day for optimizations."
    )

    optimization_hours: Optional[int] = Field(
        default=24, ge=0, description="Number of hours into the future for optimizations."
    )

    prediction_hours: Optional[int] = Field(
        default=48, ge=0, description="Number of hours into the future for predictions"
    )

    load_energy_array: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        description="An array of floats representing the total load (consumption) in watts for different time intervals.",
    )
    pv_prediction_wh: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        description="An array of floats representing the forecasted photovoltaic output in watts for different time intervals.",
    )
    elect_price_hourly: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        description="An array of floats representing the electricity price in euros per watt-hour for different time intervals.",
    )
    elect_revenue_per_hour_arr: Optional[NDArray[Shape["*"], float]] = Field(
        default=None,
        description="An array of floats representing the feed-in compensation in euros per watt-hour.",
    )

    battery: Optional[Battery] = Field(default=None, description="TBD.")
    ev: Optional[Battery] = Field(default=None, description="TBD.")
    home_appliance: Optional[HomeAppliance] = Field(default=None, description="TBD.")
    inverter: Optional[Inverter] = Field(default=None, description="TBD.")

    ac_charge_hours: Optional[NDArray[Shape["*"], float]] = Field(default=None, description="TBD")
    dc_charge_hours: Optional[NDArray[Shape["*"], float]] = Field(default=None, description="TBD")
    ev_charge_hours: Optional[NDArray[Shape["*"], float]] = Field(default=None, description="TBD")

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
        self.dc_charge_hours = np.full(self.prediction_hours, 1.0)
        self.ev_charge_hours = np.full(self.prediction_hours, 0.0)
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

    def set_akku_discharge_hours(self, ds: np.ndarray) -> None:
        if self.battery:
            self.battery.set_discharge_per_hour(ds)

    def set_akku_ac_charge_hours(self, ds: np.ndarray) -> None:
        self.ac_charge_hours = ds

    def set_akku_dc_charge_hours(self, ds: np.ndarray) -> None:
        self.dc_charge_hours = ds

    def set_ev_charge_hours(self, ds: np.ndarray) -> None:
        self.ev_charge_hours = ds

    def set_home_appliance_start(self, ds: int, global_start_hour: int = 0) -> None:
        if self.home_appliance:
            self.home_appliance.set_starting_time(ds, global_start_hour=global_start_hour)

    def reset(self) -> None:
        if self.ev:
            self.ev.reset()
        if self.battery:
            self.battery.reset()

    def simulate(self, start_hour: int) -> dict[str, Any]:
        """Simulate energy usage and costs for the given start hour.

        akku_soc_pro_stunde begin of the hour, initial hour state!
        last_wh_pro_stunde integral of last hour (end state)
        """
        # Remember start hour
        self.start_hour = start_hour

        # Check for simulation integrity
        required_attrs = [
            "load_energy_array",
            "pv_prediction_wh",
            "elect_price_hourly",
            "ev_charge_hours",
            "ac_charge_hours",
            "dc_charge_hours",
            "elect_revenue_per_hour_arr",
        ]
        missing_data = [
            attr.replace("_", " ").title() for attr in required_attrs if getattr(self, attr) is None
        ]

        if missing_data:
            logger.error("Mandatory data missing - %s", ", ".join(missing_data))
            raise ValueError(f"Mandatory data missing: {', '.join(missing_data)}")

        # Pre-fetch data
        load_energy_array = np.array(self.load_energy_array)
        pv_prediction_wh = np.array(self.pv_prediction_wh)
        elect_price_hourly = np.array(self.elect_price_hourly)
        ev_charge_hours = np.array(self.ev_charge_hours)
        ac_charge_hours = np.array(self.ac_charge_hours)
        dc_charge_hours = np.array(self.dc_charge_hours)
        elect_revenue_per_hour_arr = np.array(self.elect_revenue_per_hour_arr)

        # Fetch objects
        battery = self.battery
        ev = self.ev
        home_appliance = self.home_appliance
        inverter = self.inverter

        if not (len(load_energy_array) == len(pv_prediction_wh) == len(elect_price_hourly)):
            error_msg = f"Array sizes do not match: Load Curve = {len(load_energy_array)}, PV Forecast = {len(pv_prediction_wh)}, Electricity Price = {len(elect_price_hourly)}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        end_hour = len(load_energy_array)
        total_hours = end_hour - start_hour

        # Pre-allocate arrays for the results, optimized for speed
        loads_energy_per_hour = np.full((total_hours), np.nan)
        feedin_energy_per_hour = np.full((total_hours), np.nan)
        consumption_energy_per_hour = np.full((total_hours), np.nan)
        costs_per_hour = np.full((total_hours), np.nan)
        revenue_per_hour = np.full((total_hours), np.nan)
        soc_per_hour = np.full((total_hours), np.nan)
        soc_ev_per_hour = np.full((total_hours), np.nan)
        losses_wh_per_hour = np.full((total_hours), np.nan)
        home_appliance_wh_per_hour = np.full((total_hours), np.nan)
        electricity_price_per_hour = np.full((total_hours), np.nan)

        # Set initial state
        if battery:
            soc_per_hour[0] = battery.current_soc_percentage()
        if ev:
            soc_ev_per_hour[0] = ev.current_soc_percentage()

        for hour in range(start_hour, end_hour):
            hour_idx = hour - start_hour

            # save begin states
            if battery:
                soc_per_hour[hour_idx] = battery.current_soc_percentage()
            if ev:
                soc_ev_per_hour[hour_idx] = ev.current_soc_percentage()

            # Accumulate loads and PV generation
            consumption = load_energy_array[hour]
            losses_wh_per_hour[hour_idx] = 0.0

            # Home appliances
            if home_appliance:
                ha_load = home_appliance.get_load_for_hour(hour)
                consumption += ha_load
                home_appliance_wh_per_hour[hour_idx] = ha_load

            # E-Auto handling
            if ev and ev_charge_hours[hour] > 0:
                loaded_energy_ev, verluste_eauto = ev.charge_energy(
                    None, hour, relative_power=ev_charge_hours[hour]
                )
                consumption += loaded_energy_ev
                losses_wh_per_hour[hour_idx] += verluste_eauto

            # Process inverter logic
            energy_feedin_grid_actual = energy_consumption_grid_actual = losses = eigenverbrauch = (
                0.0
            )

            hour_ac_charge = ac_charge_hours[hour]
            hour_dc_charge = dc_charge_hours[hour]
            hourly_electricity_price = elect_price_hourly[hour]
            hourly_energy_revenue = elect_revenue_per_hour_arr[hour]

            if battery:
                battery.set_charge_allowed_for_hour(hour_dc_charge, hour)

            if inverter:
                energy_produced = pv_prediction_wh[hour]
                (
                    energy_feedin_grid_actual,
                    energy_consumption_grid_actual,
                    losses,
                    eigenverbrauch,
                ) = inverter.process_energy(energy_produced, consumption, hour)

            # AC PV Battery Charge
            if battery and hour_ac_charge > 0.0:
                battery.set_charge_allowed_for_hour(1, hour)
                battery_charged_energy_actual, battery_losses_actual = battery.charge_energy(
                    None, hour, relative_power=hour_ac_charge
                )

                total_battery_energy = battery_charged_energy_actual + battery_losses_actual
                consumption += total_battery_energy
                energy_consumption_grid_actual += total_battery_energy
                losses_wh_per_hour[hour_idx] += battery_losses_actual

            # Update hourly arrays
            feedin_energy_per_hour[hour_idx] = energy_feedin_grid_actual
            consumption_energy_per_hour[hour_idx] = energy_consumption_grid_actual
            losses_wh_per_hour[hour_idx] += losses
            loads_energy_per_hour[hour_idx] = consumption
            electricity_price_per_hour[hour_idx] = hourly_electricity_price

            # Financial calculations
            costs_per_hour[hour_idx] = energy_consumption_grid_actual * hourly_electricity_price
            revenue_per_hour[hour_idx] = energy_feedin_grid_actual * hourly_energy_revenue

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
            "Gesamtbilanz_Euro": total_cost - total_revenue,
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
        len_ac = len(self.ev_possible_charge_values)

        # Categorization:
        # Idle:       0 .. len_ac-1
        # Discharge:  len_ac .. 2*len_ac - 1
        # AC Charge:  2*len_ac .. 3*len_ac - 1
        # DC optional: 3*len_ac (not allowed), 3*len_ac + 1 (allowed)

        # Idle has no charge, Discharge has binary 1, AC Charge has corresponding values
        # Idle states
        idle_mask = (discharge_hours_bin_np >= 0) & (discharge_hours_bin_np < len_ac)

        # Discharge states
        discharge_mask = (discharge_hours_bin_np >= len_ac) & (discharge_hours_bin_np < 2 * len_ac)

        # AC states
        ac_mask = (discharge_hours_bin_np >= 2 * len_ac) & (discharge_hours_bin_np < 3 * len_ac)
        ac_indices = (discharge_hours_bin_np[ac_mask] - 2 * len_ac).astype(int)

        # DC states (if enabled)
        if self.optimize_dc_charge:
            dc_not_allowed_state = 3 * len_ac
            dc_allowed_state = 3 * len_ac + 1
            dc_charge = np.where(discharge_hours_bin_np == dc_allowed_state, 1, 0)
        else:
            dc_charge = np.ones_like(discharge_hours_bin_np, dtype=float)

        # Generate the result arrays
        discharge = np.zeros_like(discharge_hours_bin_np, dtype=int)
        discharge[discharge_mask] = 1  # Set Discharge states to 1

        ac_charge = np.zeros_like(discharge_hours_bin_np, dtype=float)
        ac_charge[ac_mask] = [self.ev_possible_charge_values[i] for i in ac_indices]

        # Idle is just 0, already default.

        return ac_charge, dc_charge, discharge

    def mutate(self, individual: list[int]) -> tuple[list[int]]:
        """Custom mutation function for the individual."""
        # Calculate the number of states
        len_ac = len(self.ev_possible_charge_values)
        if self.optimize_dc_charge:
            total_states = 3 * len_ac + 2
        else:
            total_states = 3 * len_ac

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
        len_ac = len(self.ev_possible_charge_values)

        # Total number of states without DC:
        # Idle: len_ac states
        # Discharge: len_ac states
        # AC-Charge: len_ac states
        # Total without DC: 3 * len_ac

        # With DC: + 2 states
        if self.optimize_dc_charge:
            total_states = 3 * len_ac + 2
        else:
            total_states = 3 * len_ac

        # State space: 0 .. (total_states - 1)
        self.toolbox.register("attr_discharge_state", random.randint, 0, total_states - 1)

        # EV attributes
        if self.optimize_ev:
            self.toolbox.register(
                "attr_ev_charge_index",
                random.randint,
                0,
                len_ac - 1,
            )

        # Household appliance start time
        self.toolbox.register("attr_int", random.randint, start_hour, 23)

        self.toolbox.register("individual", self.create_individual)
        self.toolbox.register("population", tools.initRepeat, list, self.toolbox.individual)
        self.toolbox.register("mate", tools.cxTwoPoint)

        # Mutation operator for charge/discharge states
        self.toolbox.register(
            "mutate_charge_discharge", tools.mutUniformInt, low=0, up=total_states - 1, indpb=0.2
        )

        # Mutation operator for EV states
        self.toolbox.register(
            "mutate_ev_charge_index",
            tools.mutUniformInt,
            low=0,
            up=len_ac - 1,
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
            self.simulation.set_home_appliance_start(
                washingstart_int, global_start_hour=self.ems.start_datetime.hour
            )

        ac, dc, discharge = self.decode_charge_discharge(discharge_hours_bin)

        self.simulation.set_akku_discharge_hours(discharge)
        # Set DC charge hours only if DC optimization is enabled
        if self.optimize_dc_charge:
            self.simulation.set_akku_dc_charge_hours(dc)
        self.simulation.set_akku_ac_charge_hours(ac)

        if eautocharge_hours_index is not None:
            eautocharge_hours_float = np.array(
                [self.ev_possible_charge_values[i] for i in eautocharge_hours_index],
                float,
            )
            self.simulation.set_ev_charge_hours(eautocharge_hours_float)
        else:
            self.simulation.set_ev_charge_hours(np.full(self.config.prediction.hours, 0))

        # Do the simulation and return result.
        return self.simulation.simulate(self.ems.start_datetime.hour)

    def evaluate(
        self,
        individual: list[int],
        parameters: GeneticOptimizationParameters,
        start_hour: int,
        worst_case: bool,
    ) -> tuple[float]:
        """Evaluate the fitness of an individual solution based on the simulation results."""
        try:
            o = self.evaluate_inner(individual)
        except Exception as e:
            return (100000.0,)  # Return a high penalty in case of an exception

        gesamtbilanz = o["Gesamtbilanz_Euro"] * (-1.0 if worst_case else 1.0)

        discharge_hours_bin, eautocharge_hours_index, washingstart_int = self.split_individual(
            individual
        )

        # EV 100% & charge not allowed
        if self.optimize_ev:
            eauto_soc_per_hour = np.array(o.get("EAuto_SoC_pro_Stunde", []))  # Beispielkey

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
            o["Gesamtbilanz_Euro"],
            o["Gesamt_Verluste"],
            parameters.eauto.min_soc_percentage - self.simulation.ev.current_soc_percentage()
            if parameters.eauto and self.simulation.ev
            else 0,
        )

        # Adjust total balance with battery value and penalties for unmet SOC
        if self.simulation.battery:
            restwert_akku = (
                self.simulation.battery.current_energy_content()
                * parameters.ems.preis_euro_pro_wh_akku
            )
            gesamtbilanz += -restwert_akku

        if self.optimize_ev:
            try:
                penalty = self.config.optimization.genetic.penalties["ev_soc_miss"]
            except:
                # Use default
                penalty = 10
                logger.error(
                    "Penalty function parameter `ev_soc_miss` not configured, using {}.", penalty
                )
            gesamtbilanz += max(
                0,
                (
                    parameters.eauto.min_soc_percentage
                    - self.simulation.ev.current_soc_percentage()
                    if parameters.eauto and self.simulation.ev
                    else 0
                )
                * penalty,
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
            akku.set_charge_per_hour(np.full(self.config.prediction.hours, 1))

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
            try:
                charge_rates = self.config.devices.electric_vehicles[0].charge_rates
                if charge_rates is None:
                    raise
            except:
                error_msg = "No charge rates provided for electric vehicle."
                logger.exception(error_msg)
                raise ValueError(error_msg)
            self.ev_possible_charge_values = charge_rates
        else:
            self.optimize_ev = False

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
            washingstart_int = self.simulation.home_appliance.get_appliance_start()

        eautocharge_hours_float = (
            [self.ev_possible_charge_values[i] for i in eautocharge_hours_index]
            if eautocharge_hours_index is not None
            else None
        )

        ac_charge, dc_charge, discharge = self.decode_charge_discharge(discharge_hours_bin)
        # Visualize the results
        visualize = {
            "ac_charge": ac_charge.tolist(),
            "dc_charge": dc_charge.tolist(),
            "discharge_allowed": discharge.tolist(),
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
                "ac_charge": ac_charge,
                "dc_charge": dc_charge,
                "discharge_allowed": discharge,
                "eautocharge_hours_float": eautocharge_hours_float,
                "result": GeneticSimulationResult(**simulation_result),
                "eauto_obj": self.simulation.ev,
                "start_solution": start_solution,
                "washingstart": washingstart_int,
            }
        )
