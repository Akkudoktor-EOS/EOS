import random
from typing import Any, Optional, Tuple

import numpy as np
from deap import algorithms, base, creator, tools
from pydantic import BaseModel, Field, field_validator, model_validator
from typing_extensions import Self

from akkudoktoreos.class_akku import EAutoParameters, PVAkku, PVAkkuParameters
from akkudoktoreos.class_ems import (
    EnergieManagementSystem,
    EnergieManagementSystemParameters,
)
from akkudoktoreos.class_haushaltsgeraet import (
    Haushaltsgeraet,
    HaushaltsgeraetParameters,
)
from akkudoktoreos.class_inverter import Wechselrichter, WechselrichterParameters
from akkudoktoreos.class_visualize import prepare_visualize
from akkudoktoreos.config import AppConfig
from akkudoktoreos.visualize import visualisiere_ergebnisse


class OptimizationParameters(BaseModel):
    ems: EnergieManagementSystemParameters
    pv_akku: PVAkkuParameters
    wechselrichter: WechselrichterParameters = WechselrichterParameters()
    eauto: EAutoParameters
    spuelmaschine: Optional[HaushaltsgeraetParameters] = None
    temperature_forecast: list[float] = Field(
        "An array of floats representing the temperature forecast in degrees Celsius for different time intervals."
    )
    start_solution: Optional[list[float]] = Field(
        None, description="Can be `null` or contain a previous solution (if available)."
    )
    extra_data: Optional[dict[str, list[Any]]] = None

    @model_validator(mode="after")
    def validate_list_length(self) -> Self:
        arr_length = len(self.ems.pv_prognose_wh)
        if arr_length != len(self.temperature_forecast):
            raise ValueError("Input lists have different lenghts")
        return self

    @field_validator("start_solution")
    def validate_start_solution(
        cls, start_solution: Optional[list[float]]
    ) -> Optional[list[float]]:
        if start_solution is not None and len(start_solution) < 2:
            raise ValueError("Requires at least two values.")
        return start_solution


class EAutoResult(BaseModel):
    """This object contains information related to the electric vehicle and its charging and discharging behavior."""

    charge_array: list[float] = Field(
        description="Indicates for each hour whether the EV is charging (`0` for no charging, `1` for charging)."
    )
    discharge_array: list[int] = Field(
        description="Indicates for each hour whether the EV is discharging (`0` for no discharging, `1` for discharging)."
    )
    entlade_effizienz: float = Field(description="The discharge efficiency as a float.")
    hours: int = Field("Amount of hours the simulation is done for.")
    kapazitaet_wh: int = Field("The capacity of the EV’s battery in watt-hours.")
    lade_effizienz: float = Field("The charging efficiency as a float.")
    max_ladeleistung_w: int = Field(description="The maximum charging power of the EV in watts.")
    soc_wh: float = Field(
        description="The state of charge of the battery in watt-hours at the start of the simulation."
    )
    start_soc_prozent: int = Field(
        description="The state of charge of the battery in percentage at the start of the simulation."
    )


class SimulationResult(BaseModel):
    """This object contains the results of the simulation and provides insights into various parameters over the entire forecast period."""

    Last_Wh_pro_Stunde: list[Optional[float]] = Field(description="TBD")
    EAuto_SoC_pro_Stunde: list[Optional[float]] = Field(
        description="The state of charge of the EV for each hour."
    )
    Einnahmen_Euro_pro_Stunde: list[Optional[float]] = Field(
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
    Haushaltsgeraet_wh_pro_stunde: list[Optional[float]] = Field(
        description="The energy consumption of a household appliance in watt-hours per hour."
    )
    Kosten_Euro_pro_Stunde: list[Optional[float]] = Field(
        description="The costs in euros per hour."
    )
    Netzbezug_Wh_pro_Stunde: list[Optional[float]] = Field(
        description="The grid energy drawn in watt-hours per hour."
    )
    Netzeinspeisung_Wh_pro_Stunde: list[Optional[float]] = Field(
        description="The energy fed into the grid in watt-hours per hour."
    )
    Verluste_Pro_Stunde: list[Optional[float]] = Field(
        description="The losses in watt-hours per hour."
    )
    akku_soc_pro_stunde: list[Optional[float]] = Field(
        description="The state of charge of the battery (not the EV) in percentage per hour."
    )


# class SimulationData(BaseModel):
#    """An object containing the simulated data."""
#
#    Last_Wh_pro_Stunde: list[Optional[float]] = Field(description="TBD")
#    EAuto_SoC_pro_Stunde: list[Optional[float]] = Field(
#        description="An array of floats representing the simulated state of charge of the electric car per hour.",
#    )
#    Einnahmen_Euro_pro_Stunde: list[Optional[float]] = Field(
#        description="An array of floats representing the simulated income in euros per hour."
#    )
#    Gesamt_Verluste: float = Field(description="The total simulated losses in watt-hours.")
#    Gesamtbilanz_Euro: float = Field(description="The total simulated balance in euros.")
#    Gesamteinnahmen_Euro: float = Field(description="The total simulated income in euros.")
#    Gesamtkosten_Euro: float = Field(description="The total simulated costs in euros.")
#    Haushaltsgeraet_wh_pro_stunde: list[Optional[float]] = Field(
#        description="An array of floats representing the simulated energy consumption of a household appliance in watt-hours per hour."
#    )
#    Kosten_Euro_pro_Stunde: list[Optional[float]] = Field(
#        description="An array of floats representing the simulated costs in euros per hour."
#    )
#    Netzbezug_Wh_pro_Stunde: list[Optional[float]] = Field(
#        description="An array of floats representing the simulated grid consumption in watt-hours per hour."
#    )
#    Netzeinspeisung_Wh_pro_Stunde: list[Optional[float]] = Field(
#        description="An array of floats representing the simulated grid feed-in in watt-hours per hour."
#    )
#    Verluste_Pro_Stunde: list[Optional[float]] = Field(
#        description="An array of floats representing the simulated losses per hour."
#    )
#    akku_soc_pro_stunde: list[Optional[float]] = Field(
#        description="An array of floats representing the simulated state of charge of the battery in percentage per hour."
#    )


class OptimizeResponse(BaseModel):
    """**Note**: The first value of "Last_Wh_pro_Stunde", "Netzeinspeisung_Wh_pro_Stunde" and "Netzbezug_Wh_pro_Stunde", will be set to null in the JSON output and represented as NaN or None in the corresponding classes' data returns. This approach is adopted to ensure that the current hour's processing remains unchanged."""

    ac_charge: list[float] = Field(
        description="Array with AC charging values as relative power (0-1), other values set to 0."
    )
    dc_charge: list[float] = Field(
        description="Array with DC charging values as relative power (0-1), other values set to 0."
    )
    discharge_allowed: list[int] = Field(
        description="Array with discharge values (1 for discharge, 0 otherwise)."
    )
    result: SimulationResult
    eauto_obj: EAutoResult
    start_solution: Optional[list[float]] = Field(
        None,
        description="An array of binary values (0 or 1) representing a possible starting solution for the simulation.",
    )
    spuelstart: Optional[int] = Field(
        None,
        description="Can be `null` or contain an object representing the start of washing (if applicable).",
    )
    # simulation_data: Optional[SimulationData] = None


class optimization_problem:
    def __init__(
        self,
        config: AppConfig,
        verbose: bool = False,
        fixed_seed: Optional[int] = None,
    ):
        """Initialize the optimization problem with the required parameters."""
        self._config = config
        self.prediction_hours = config.eos.prediction_hours
        self.strafe = config.eos.penalty
        self.opti_param = None
        self.fixed_eauto_hours = config.eos.prediction_hours - config.eos.optimization_hours
        self.possible_charge_values = config.eos.available_charging_rates_in_percentage
        self.verbose = verbose
        self.fix_seed = fixed_seed
        self.optimize_ev = True
        self.optimize_dc_charge = False

        # Set a fixed seed for random operations if provided
        if fixed_seed is not None:
            random.seed(fixed_seed)

    def decode_charge_discharge(
        self, discharge_hours_bin: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Decode the input array `discharge_hours_bin` into three separate arrays for AC charging, DC charging, and discharge.

        The function maps AC and DC charging values to relative power levels (0 to 1), while the discharge remains binary (0 or 1).

        Parameters:
        - discharge_hours_bin (np.ndarray): Input array with integer values representing the different states.
        The states are:
        0: No action ("idle")
        1: Discharge ("discharge")
        2-6: AC charging with different power levels ("ac_charge")
        7-8: DC charging Dissallowed/allowed ("dc_charge")

        Returns:
        - ac_charge (np.ndarray): Array with AC charging values as relative power (0-1), other values set to 0.
        - dc_charge (np.ndarray): Array with DC charging values as relative power (0-1), other values set to 0.
        - discharge (np.ndarray): Array with discharge values (1 for discharge, 0 otherwise).
        """
        # Convert the input list to a NumPy array, if it's not already
        discharge_hours_bin = np.array(discharge_hours_bin)

        # Create ac_charge array: Only consider values between 2 and 6 (AC charging power levels), set the rest to 0
        ac_charge = np.where(
            (discharge_hours_bin >= 2) & (discharge_hours_bin <= 6), discharge_hours_bin - 1, 0
        )
        ac_charge = ac_charge / 5.0  # Normalize AC charge to range between 0 and 1

        # Create dc_charge array: 7 = Not allowed (mapped to 0), 8 = Allowed (mapped to 1)
        # Create dc_charge array: Only if DC charge optimization is enabled
        if self.optimize_dc_charge:
            dc_charge = np.where(discharge_hours_bin == 8, 1, 0)
        else:
            dc_charge = np.ones_like(
                discharge_hours_bin
            )  # Set DC charge to 0 if optimization is disabled

        # Create discharge array: Only consider value 1 (Discharge), set the rest to 0 (binary output)
        discharge = np.where(discharge_hours_bin == 1, 1, 0)

        return ac_charge, dc_charge, discharge

    # Custom mutation function that applies type-specific mutations
    def mutate(self, individual):
        """Custom mutation function for the individual.

        This function mutates different parts of the individual:
        - Mutates the discharge and charge states (AC, DC, idle) using the split_charge_discharge method.
        - Mutates the EV charging schedule if EV optimization is enabled.
        - Mutates appliance start times if household appliances are part of the optimization.

        Parameters:
        - individual (list): The individual being mutated, which includes different optimization parameters.

        Returns:
        - (tuple): The mutated individual as a tuple (required by DEAP).
        """
        # Step 1: Mutate the charge/discharge states (idle, discharge, AC charge, DC charge)
        # Extract the relevant part of the individual for prediction hours, which represents the charge/discharge behavior.
        charge_discharge_part = individual[: self.prediction_hours]

        # Apply the mutation to the charge/discharge part
        (charge_discharge_mutated,) = self.toolbox.mutate_charge_discharge(charge_discharge_part)

        # Ensure that no invalid states are introduced during mutation (valid values: 0-8)
        if self.optimize_dc_charge:
            charge_discharge_mutated = np.clip(charge_discharge_mutated, 0, 8)
        else:
            charge_discharge_mutated = np.clip(charge_discharge_mutated, 0, 6)

        # Use split_charge_discharge to split the mutated array into AC charge, DC charge, and discharge components
        # ac_charge, dc_charge, discharge = self.split_charge_discharge(charge_discharge_mutated)

        # Optionally: You can process the split arrays further if needed, for example,
        # applying additional constraints or penalties, or keeping track of charging limits.

        # Reassign the mutated values back to the individual
        individual[: self.prediction_hours] = charge_discharge_mutated

        # Step 2: Mutate EV charging schedule if enabled
        if self.optimize_ev:
            # Extract the relevant part for EV charging schedule
            ev_charge_part = individual[self.prediction_hours : self.prediction_hours * 2]

            # Apply mutation on the EV charging schedule
            (ev_charge_part_mutated,) = self.toolbox.mutate_ev_charge_index(ev_charge_part)

            # Ensure the EV does not charge during fixed hours (set those hours to 0)
            ev_charge_part_mutated[self.prediction_hours - self.fixed_eauto_hours :] = [
                0
            ] * self.fixed_eauto_hours

            # Reassign the mutated EV charging part back to the individual
            individual[self.prediction_hours : self.prediction_hours * 2] = ev_charge_part_mutated

        # Step 3: Mutate appliance start times if household appliances are part of the optimization
        if self.opti_param["haushaltsgeraete"] > 0:
            # Extract the appliance part (typically a single value for the start hour)
            appliance_part = [individual[-1]]

            # Apply mutation on the appliance start hour
            (appliance_part_mutated,) = self.toolbox.mutate_hour(appliance_part)

            # Reassign the mutated appliance part back to the individual
            individual[-1] = appliance_part_mutated[0]

        return (individual,)

    # Method to create an individual based on the conditions
    def create_individual(self):
        # Start with discharge states for the individual
        individual_components = [
            self.toolbox.attr_discharge_state() for _ in range(self.prediction_hours)
        ]

        # Add EV charge index values if optimize_ev is True
        if self.optimize_ev:
            individual_components += [
                self.toolbox.attr_ev_charge_index() for _ in range(self.prediction_hours)
            ]

        # Add the start time of the household appliance if it's being optimized
        if self.opti_param["haushaltsgeraete"] > 0:
            individual_components += [self.toolbox.attr_int()]

        return creator.Individual(individual_components)

    def split_individual(
        self, individual: list[float]
    ) -> Tuple[list[int], list[float], Optional[int]]:
        """Split the individual solution into its components.

        Components:
        1. Discharge hours (binary),
        2. Electric vehicle charge hours (float),
        3. Dishwasher start time (integer if applicable).
        """
        discharge_hours_bin = individual[: self.prediction_hours]
        eautocharge_hours_float = (
            individual[self.prediction_hours : self.prediction_hours * 2]
            if self.optimize_ev
            else None
        )

        spuelstart_int = (
            individual[-1]
            if self.opti_param and self.opti_param.get("haushaltsgeraete", 0) > 0
            else None
        )
        return discharge_hours_bin, eautocharge_hours_float, spuelstart_int

    def setup_deap_environment(self, opti_param: dict[str, Any], start_hour: int) -> None:
        """Set up the DEAP environment with fitness and individual creation rules."""
        self.opti_param = opti_param

        # Remove existing FitnessMin and Individual classes from creator if present
        for attr in ["FitnessMin", "Individual"]:
            if attr in creator.__dict__:
                del creator.__dict__[attr]

        # Create new FitnessMin and Individual classes
        creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
        creator.create("Individual", list, fitness=creator.FitnessMin)

        # Initialize toolbox with attributes and operations
        self.toolbox = base.Toolbox()
        if self.optimize_dc_charge:
            self.toolbox.register("attr_discharge_state", random.randint, 0, 8)
        else:
            self.toolbox.register("attr_discharge_state", random.randint, 0, 6)

        if self.optimize_ev:
            self.toolbox.register(
                "attr_ev_charge_index",
                random.randint,
                0,
                len(self._config.eos.available_charging_rates_in_percentage) - 1,
            )
        self.toolbox.register("attr_int", random.randint, start_hour, 23)

        # Register individual creation function
        self.toolbox.register("individual", self.create_individual)

        # Register population, mating, mutation, and selection functions
        self.toolbox.register("population", tools.initRepeat, list, self.toolbox.individual)
        self.toolbox.register("mate", tools.cxTwoPoint)
        # self.toolbox.register("mutate", tools.mutFlipBit, indpb=0.1)
        # Register separate mutation functions for each type of value:
        # - Discharge state mutation (-5, 0, 1)
        if self.optimize_dc_charge:
            self.toolbox.register(
                "mutate_charge_discharge", tools.mutUniformInt, low=0, up=8, indpb=0.2
            )
        else:
            self.toolbox.register(
                "mutate_charge_discharge", tools.mutUniformInt, low=0, up=6, indpb=0.2
            )
        # - Float mutation for EV charging values
        self.toolbox.register(
            "mutate_ev_charge_index",
            tools.mutUniformInt,
            low=0,
            up=len(self._config.eos.available_charging_rates_in_percentage) - 1,
            indpb=0.2,
        )
        # - Start hour mutation for household devices
        self.toolbox.register("mutate_hour", tools.mutUniformInt, low=start_hour, up=23, indpb=0.2)

        # Register custom mutation function
        self.toolbox.register("mutate", self.mutate)

        self.toolbox.register("select", tools.selTournament, tournsize=3)

    def evaluate_inner(
        self, individual: list[float], ems: EnergieManagementSystem, start_hour: int
    ) -> dict[str, Any]:
        """Simulates the energy management system (EMS) using the provided individual solution.

        This is an internal function.
        """
        ems.reset()
        discharge_hours_bin, eautocharge_hours_index, spuelstart_int = self.split_individual(
            individual
        )
        if self.opti_param.get("haushaltsgeraete", 0) > 0:
            ems.set_haushaltsgeraet_start(spuelstart_int, global_start_hour=start_hour)

        ac, dc, discharge = self.decode_charge_discharge(discharge_hours_bin)

        ems.set_akku_discharge_hours(discharge)
        # Set DC charge hours only if DC optimization is enabled
        if self.optimize_dc_charge:
            ems.set_akku_dc_charge_hours(dc)
        ems.set_akku_ac_charge_hours(ac)

        if self.optimize_ev:
            eautocharge_hours_float = [
                self._config.eos.available_charging_rates_in_percentage[i]
                for i in eautocharge_hours_index
            ]
            ems.set_ev_charge_hours(eautocharge_hours_float)
        else:
            ems.set_ev_charge_hours(np.full(self.prediction_hours, 0))
        return ems.simuliere(start_hour)

    def evaluate(
        self,
        individual: list[float],
        ems: EnergieManagementSystem,
        parameters: OptimizationParameters,
        start_hour: int,
        worst_case: bool,
    ) -> Tuple[float]:
        """Evaluate the fitness of an individual solution based on the simulation results."""
        try:
            o = self.evaluate_inner(individual, ems, start_hour)
        except Exception as e:
            return (100000.0,)  # Return a high penalty in case of an exception

        gesamtbilanz = o["Gesamtbilanz_Euro"] * (-1.0 if worst_case else 1.0)

        discharge_hours_bin, eautocharge_hours_float, _ = self.split_individual(individual)

        # Small Penalty for not discharging
        gesamtbilanz += sum(
            0.01 for i in range(self.prediction_hours) if discharge_hours_bin[i] == 0.0
        )

        # Penalty for not meeting the minimum SOC (State of Charge) requirement
        # if parameters.eauto_min_soc_prozent - ems.eauto.ladezustand_in_prozent() <= 0.0 and  self.optimize_ev:
        #     gesamtbilanz += sum(
        #         self.strafe for ladeleistung in eautocharge_hours_float if ladeleistung != 0.0
        #     )

        individual.extra_data = (
            o["Gesamtbilanz_Euro"],
            o["Gesamt_Verluste"],
            parameters.eauto.min_soc_prozent - ems.eauto.ladezustand_in_prozent(),
        )

        # Adjust total balance with battery value and penalties for unmet SOC

        restwert_akku = ems.akku.aktueller_energieinhalt() * parameters.ems.preis_euro_pro_wh_akku
        # print(ems.akku.aktueller_energieinhalt()," * ", parameters.ems.preis_euro_pro_wh_akku , " ", restwert_akku, " ", gesamtbilanz)
        gesamtbilanz += -restwert_akku
        # print(gesamtbilanz)
        if self.optimize_ev:
            gesamtbilanz += max(
                0,
                (parameters.eauto.min_soc_prozent - ems.eauto.ladezustand_in_prozent())
                * self.strafe,
            )

        return (gesamtbilanz,)

    def optimize(
        self, start_solution: Optional[list[float]] = None, ngen: int = 400
    ) -> Tuple[Any, dict[str, list[Any]]]:
        """Run the optimization process using a genetic algorithm."""
        population = self.toolbox.population(n=300)
        hof = tools.HallOfFame(1)
        stats = tools.Statistics(lambda ind: ind.fitness.values)
        stats.register("min", np.min)

        if self.verbose:
            print("Start optimize:", start_solution)

        # Insert the start solution into the population if provided
        if start_solution not in [None, -1]:
            for _ in range(3):
                population.insert(0, creator.Individual(start_solution))

        # Run the evolutionary algorithm
        algorithms.eaMuPlusLambda(
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

        member = {"bilanz": [], "verluste": [], "nebenbedingung": []}
        for ind in population:
            if hasattr(ind, "extra_data"):
                extra_value1, extra_value2, extra_value3 = ind.extra_data
                member["bilanz"].append(extra_value1)
                member["verluste"].append(extra_value2)
                member["nebenbedingung"].append(extra_value3)

        return hof[0], member

    def optimierung_ems(
        self,
        parameters: OptimizationParameters,
        start_hour: Optional[int] = None,
        worst_case: bool = False,
        startdate: Optional[Any] = None,  # startdate is not used!
        *,
        ngen: int = 600,
    ) -> dict[str, Any]:
        """Perform EMS (Energy Management System) optimization and visualize results."""
        einspeiseverguetung_euro_pro_wh = np.full(
            self.prediction_hours, parameters.ems.einspeiseverguetung_euro_pro_wh
        )

        # Initialize PV and EV batteries
        akku = PVAkku(
            parameters.pv_akku,
            hours=self.prediction_hours,
        )
        akku.set_charge_per_hour(np.full(self.prediction_hours, 1))

        self.optimize_ev = True
        if parameters.eauto.min_soc_prozent - parameters.eauto.start_soc_prozent < 0:
            self.optimize_ev = False

        eauto = PVAkku(
            parameters.eauto,
            hours=self.prediction_hours,
        )
        eauto.set_charge_per_hour(np.full(self.prediction_hours, 1))

        # Initialize household appliance if applicable
        spuelmaschine = (
            Haushaltsgeraet(
                parameters=parameters.spuelmaschine,
                hours=self.prediction_hours,
            )
            if parameters.spuelmaschine is not None
            else None
        )

        # Initialize the inverter and energy management system
        wr = Wechselrichter(parameters.wechselrichter, akku)
        ems = EnergieManagementSystem(
            self._config.eos,
            parameters.ems,
            eauto=eauto,
            haushaltsgeraet=spuelmaschine,
            wechselrichter=wr,
        )

        # Setup the DEAP environment and optimization process
        self.setup_deap_environment({"haushaltsgeraete": 1 if spuelmaschine else 0}, start_hour)
        self.toolbox.register(
            "evaluate",
            lambda ind: self.evaluate(ind, ems, parameters, start_hour, worst_case),
        )
        start_solution, extra_data = self.optimize(parameters.start_solution, ngen=ngen)

        # Perform final evaluation on the best solution
        o = self.evaluate_inner(start_solution, ems, start_hour)
        discharge_hours_bin, eautocharge_hours_float, spuelstart_int = self.split_individual(
            start_solution
        )
        if self.optimize_ev:
            eautocharge_hours_float = [
                self._config.eos.available_charging_rates_in_percentage[i]
                for i in eautocharge_hours_float
            ]

        ac_charge, dc_charge, discharge = self.decode_charge_discharge(discharge_hours_bin)

        # List output keys where the first element needs to be changed to None
        keys_to_modify = [
            "Last_Wh_pro_Stunde",
            "Netzeinspeisung_Wh_pro_Stunde",
            "akku_soc_pro_stunde",
            "Netzbezug_Wh_pro_Stunde",
            "Kosten_Euro_pro_Stunde",
            "Einnahmen_Euro_pro_Stunde",
            "EAuto_SoC_pro_Stunde",
            "Verluste_Pro_Stunde",
            "Haushaltsgeraet_wh_pro_stunde",
        ]

        # Loop through each key in the list
        for key in keys_to_modify:
            # Convert the NumPy array to a list
            element_list = o[key].tolist()

            # Change the first value to None
            # element_list[0] = None
            # Change the NaN to None (JSON)
            element_list = [
                None if isinstance(x, (int, float)) and np.isnan(x) else x for x in element_list
            ]

            # Assign the modified list back to the dictionary
            o[key] = element_list

        # Return final results as a dictionary
        results = {
            "ac_charge": ac_charge.tolist(),
            "dc_charge": dc_charge.tolist(),
            "discharge_allowed": discharge.tolist(),
            "eautocharge_hours_float": eautocharge_hours_float,
            "result": o,
            "eauto_obj": ems.eauto.to_dict(),
            "start_solution": start_solution,
            "spuelstart": spuelstart_int,
            "extra_data": extra_data,
        }
        prepare_visualize(self._config, parameters, results)
        return results
