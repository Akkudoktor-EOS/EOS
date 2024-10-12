import random
from typing import Any, Optional, Tuple

import numpy as np
from deap import algorithms, base, creator, tools
from pydantic import BaseModel, Field, model_validator
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
from akkudoktoreos.config import moegliche_ladestroeme_in_prozent
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

    @model_validator(mode="after")
    def validate_list_length(self) -> Self:
        arr_length = len(self.ems.pv_prognose_wh)
        if arr_length != len(self.temperature_forecast):
            raise ValueError("Input lists have different lenghts")
        return self


class EAutoResult(BaseModel):
    """ "This object contains information related to the electric vehicle and its charging and discharging behavior"""

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
    """This object contains the results of the simulation and provides insights into various parameters over the entire forecast period"""

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

    discharge_hours_bin: list[int] = Field(
        description="An array that indicates for each hour of the forecast period whether energy is discharged from the battery or not. The values are either `0` (no discharge) or `1` (discharge)."
    )
    eautocharge_hours_float: list[float] = Field(
        description="An array of binary values (0 or 1) that indicates whether the EV will be charged in a certain hour."
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
        prediction_hours: int = 48,
        strafe: float = 10,
        optimization_hours: int = 24,
        verbose: bool = False,
        fixed_seed: Optional[int] = None,
    ):
        """Initialize the optimization problem with the required parameters."""
        self.prediction_hours = prediction_hours
        self.strafe = strafe
        self.opti_param = None
        self.fixed_eauto_hours = prediction_hours - optimization_hours
        self.possible_charge_values = moegliche_ladestroeme_in_prozent
        self.verbose = verbose
        self.fix_seed = fixed_seed

        # Set a fixed seed for random operations if provided
        if fixed_seed is not None:
            random.seed(fixed_seed)

    def split_individual(
        self, individual: list[float]
    ) -> Tuple[list[int], list[float], Optional[int]]:
        """
        Split the individual solution into its components:
        1. Discharge hours (binary),
        2. Electric vehicle charge hours (float),
        3. Dishwasher start time (integer if applicable).
        """
        discharge_hours_bin = individual[: self.prediction_hours]
        eautocharge_hours_float = individual[self.prediction_hours : self.prediction_hours * 2]
        spuelstart_int = (
            individual[-1]
            if self.opti_param and self.opti_param.get("haushaltsgeraete", 0) > 0
            else None
        )
        return discharge_hours_bin, eautocharge_hours_float, spuelstart_int

    def setup_deap_environment(self, opti_param: dict[str, Any], start_hour: int) -> None:
        """
        Set up the DEAP environment with fitness and individual creation rules.
        """
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
        self.toolbox.register("attr_bool", random.randint, 0, 1)
        self.toolbox.register("attr_float", random.uniform, 0, 1)
        self.toolbox.register("attr_int", random.randint, start_hour, 23)

        # Register individual creation method based on household appliance parameter
        if opti_param["haushaltsgeraete"] > 0:
            self.toolbox.register(
                "individual",
                lambda: creator.Individual(
                    [self.toolbox.attr_bool() for _ in range(self.prediction_hours)]
                    + [self.toolbox.attr_float() for _ in range(self.prediction_hours)]
                    + [self.toolbox.attr_int()]
                ),
            )
        else:
            self.toolbox.register(
                "individual",
                lambda: creator.Individual(
                    [self.toolbox.attr_bool() for _ in range(self.prediction_hours)]
                    + [self.toolbox.attr_float() for _ in range(self.prediction_hours)]
                ),
            )

        # Register population, mating, mutation, and selection functions
        self.toolbox.register("population", tools.initRepeat, list, self.toolbox.individual)
        self.toolbox.register("mate", tools.cxTwoPoint)
        self.toolbox.register("mutate", tools.mutFlipBit, indpb=0.1)
        self.toolbox.register("select", tools.selTournament, tournsize=3)

    def evaluate_inner(
        self, individual: list[float], ems: EnergieManagementSystem, start_hour: int
    ) -> dict[str, Any]:
        """
        Internal evaluation function that simulates the energy management system (EMS)
        using the provided individual solution.
        """
        ems.reset()
        discharge_hours_bin, eautocharge_hours_float, spuelstart_int = self.split_individual(
            individual
        )
        if self.opti_param.get("haushaltsgeraete", 0) > 0:
            ems.set_haushaltsgeraet_start(spuelstart_int, global_start_hour=start_hour)

        ems.set_akku_discharge_hours(discharge_hours_bin)
        eautocharge_hours_float[self.prediction_hours - self.fixed_eauto_hours :] = [
            0.0
        ] * self.fixed_eauto_hours
        ems.set_eauto_charge_hours(eautocharge_hours_float)
        return ems.simuliere(start_hour)

    def evaluate(
        self,
        individual: list[float],
        ems: EnergieManagementSystem,
        parameters: OptimizationParameters,
        start_hour: int,
        worst_case: bool,
    ) -> Tuple[float]:
        """
        Evaluate the fitness of an individual solution based on the simulation results.
        """
        try:
            o = self.evaluate_inner(individual, ems, start_hour)
        except Exception as e:
            return (100000.0,)  # Return a high penalty in case of an exception

        gesamtbilanz = o["Gesamtbilanz_Euro"] * (-1.0 if worst_case else 1.0)
        discharge_hours_bin, eautocharge_hours_float, _ = self.split_individual(individual)
        max_ladeleistung = np.max(moegliche_ladestroeme_in_prozent)

        # Penalty for not discharging
        gesamtbilanz += sum(
            0.01 for i in range(self.prediction_hours) if discharge_hours_bin[i] == 0.0
        )

        # Penalty for charging the electric vehicle during restricted hours
        gesamtbilanz += sum(
            self.strafe
            for i in range(self.prediction_hours - self.fixed_eauto_hours, self.prediction_hours)
            if eautocharge_hours_float[i] != 0.0
        )

        # Penalty for exceeding maximum charge power
        gesamtbilanz += sum(
            self.strafe * 10
            for ladeleistung in eautocharge_hours_float
            if ladeleistung > max_ladeleistung
        )

        # Penalty for not meeting the minimum SOC (State of Charge) requirement
        if parameters.eauto.min_soc_prozent - ems.eauto.ladezustand_in_prozent() <= 0.0:
            gesamtbilanz += sum(
                self.strafe for ladeleistung in eautocharge_hours_float if ladeleistung != 0.0
            )

        individual.extra_data = (
            o["Gesamtbilanz_Euro"],
            o["Gesamt_Verluste"],
            parameters.eauto.min_soc_prozent - ems.eauto.ladezustand_in_prozent(),
        )

        # Adjust total balance with battery value and penalties for unmet SOC
        restwert_akku = ems.akku.aktueller_energieinhalt() * parameters.ems.preis_euro_pro_wh_akku
        gesamtbilanz += (
            max(
                0,
                (parameters.eauto.min_soc_prozent - ems.eauto.ladezustand_in_prozent())
                * self.strafe,
            )
            - restwert_akku
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
            lambda_=200,
            cxpb=0.5,
            mutpb=0.3,
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
        ngen: int = 400,
    ) -> dict[str, Any]:
        """
        Perform EMS (Energy Management System) optimization and visualize results.
        """
        # Initialize PV and EV batteries
        akku = PVAkku(
            parameters.pv_akku,
            hours=self.prediction_hours,
        )
        akku.set_charge_per_hour(np.full(self.prediction_hours, 1))

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

        # Visualize the results
        visualisiere_ergebnisse(
            parameters.ems.gesamtlast,
            parameters.ems.pv_prognose_wh,
            parameters.ems.strompreis_euro_pro_wh,
            o,
            discharge_hours_bin,
            eautocharge_hours_float,
            parameters.temperature_forecast,
            start_hour,
            self.prediction_hours,
            ems.einspeiseverguetung_euro_pro_wh_arr,
            extra_data=extra_data,
        )

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
            element_list[0] = None
            # Change the NaN to None (JSON)
            element_list = [
                None if isinstance(x, (int, float)) and np.isnan(x) else x for x in element_list
            ]

            # Assign the modified list back to the dictionary
            o[key] = element_list

        # Return final results as a dictionary
        return {
            "discharge_hours_bin": discharge_hours_bin,
            "eautocharge_hours_float": eautocharge_hours_float,
            "result": o,
            "eauto_obj": ems.eauto.to_dict(),
            "start_solution": start_solution,
            "spuelstart": spuelstart_int,
            # "simulation_data": o,
        }
