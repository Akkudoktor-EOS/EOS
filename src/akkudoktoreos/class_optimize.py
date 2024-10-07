import random
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from deap import algorithms, base, creator, tools

from akkudoktoreos.class_akku import PVAkku
from akkudoktoreos.class_ems import EnergieManagementSystem
from akkudoktoreos.class_haushaltsgeraet import Haushaltsgeraet
from akkudoktoreos.class_inverter import Wechselrichter
from akkudoktoreos.config import moegliche_ladestroeme_in_prozent
from akkudoktoreos.visualize import visualisiere_ergebnisse


def isfloat(num: Any) -> bool:
    """Check if a given input can be converted to float."""
    try:
        float(num)
        return True
    except ValueError:
        return False


class optimization_problem:
    def __init__(
        self,
        prediction_hours: int = 24,
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
        self, individual: List[float]
    ) -> Tuple[List[int], List[float], Optional[int]]:
        """
        Split the individual solution into its components:
        1. Discharge hours (binary),
        2. Electric vehicle charge hours (float),
        3. Dishwasher start time (integer if applicable).
        """
        discharge_hours_bin = individual[: self.prediction_hours]
        eautocharge_hours_float = individual[
            self.prediction_hours : self.prediction_hours * 2
        ]
        spuelstart_int = (
            individual[-1]
            if self.opti_param and self.opti_param.get("haushaltsgeraete", 0) > 0
            else None
        )
        return discharge_hours_bin, eautocharge_hours_float, spuelstart_int

    def setup_deap_environment(
        self, opti_param: Dict[str, Any], start_hour: int
    ) -> None:
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
        self.toolbox.register(
            "population", tools.initRepeat, list, self.toolbox.individual
        )
        self.toolbox.register("mate", tools.cxTwoPoint)
        self.toolbox.register("mutate", tools.mutFlipBit, indpb=0.1)
        self.toolbox.register("select", tools.selTournament, tournsize=3)

    def evaluate_inner(
        self, individual: List[float], ems: EnergieManagementSystem, start_hour: int
    ) -> Dict[str, Any]:
        """
        Internal evaluation function that simulates the energy management system (EMS)
        using the provided individual solution.
        """
        ems.reset()
        discharge_hours_bin, eautocharge_hours_float, spuelstart_int = (
            self.split_individual(individual)
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
        individual: List[float],
        ems: EnergieManagementSystem,
        parameter: Dict[str, Any],
        start_hour: int,
        worst_case: bool,
    ) -> Tuple[float]:
        """
        Evaluate the fitness of an individual solution based on the simulation results.
        """
        try:
            o = self.evaluate_inner(individual, ems, start_hour)
        except Exception:
            return (100000.0,)  # Return a high penalty in case of an exception

        gesamtbilanz = o["Gesamtbilanz_Euro"] * (-1.0 if worst_case else 1.0)
        discharge_hours_bin, eautocharge_hours_float, _ = self.split_individual(
            individual
        )
        max_ladeleistung = np.max(moegliche_ladestroeme_in_prozent)

        # Penalty for not discharging
        gesamtbilanz += sum(
            0.01 for i in range(self.prediction_hours) if discharge_hours_bin[i] == 0.0
        )

        # Penalty for charging the electric vehicle during restricted hours
        gesamtbilanz += sum(
            self.strafe
            for i in range(
                self.prediction_hours - self.fixed_eauto_hours, self.prediction_hours
            )
            if eautocharge_hours_float[i] != 0.0
        )

        # Penalty for exceeding maximum charge power
        gesamtbilanz += sum(
            self.strafe * 10
            for ladeleistung in eautocharge_hours_float
            if ladeleistung > max_ladeleistung
        )

        # Penalty for not meeting the minimum SOC (State of Charge) requirement
        if parameter["eauto_min_soc"] - ems.eauto.ladezustand_in_prozent() <= 0.0:
            gesamtbilanz += sum(
                self.strafe
                for ladeleistung in eautocharge_hours_float
                if ladeleistung != 0.0
            )

        individual.extra_data = (
            o["Gesamtbilanz_Euro"],
            o["Gesamt_Verluste"],
            parameter["eauto_min_soc"] - ems.eauto.ladezustand_in_prozent(),
        )

        # Adjust total balance with battery value and penalties for unmet SOC
        restwert_akku = (
            ems.akku.aktueller_energieinhalt() * parameter["preis_euro_pro_wh_akku"]
        )
        gesamtbilanz += (
            max(
                0,
                (parameter["eauto_min_soc"] - ems.eauto.ladezustand_in_prozent())
                * self.strafe,
            )
            - restwert_akku
        )

        return (gesamtbilanz,)

    def optimize(
        self, start_solution: Optional[List[float]] = None
    ) -> Tuple[Any, Dict[str, List[Any]]]:
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
            ngen=400,
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
        parameter: Optional[Dict[str, Any]] = None,
        start_hour: Optional[int] = None,
        worst_case: bool = False,
        startdate: Optional[Any] = None,  # startdate is not used!
    ) -> Dict[str, Any]:
        """
        Perform EMS (Energy Management System) optimization and visualize results.
        """
        einspeiseverguetung_euro_pro_wh = np.full(
            self.prediction_hours, parameter["einspeiseverguetung_euro_pro_wh"]
        )

        # Initialize PV and EV batteries
        akku = PVAkku(
            kapazitaet_wh=parameter["pv_akku_cap"],
            hours=self.prediction_hours,
            start_soc_prozent=parameter["pv_soc"],
            min_soc_prozent=parameter["min_soc_prozent"],
            max_ladeleistung_w=5000,
        )
        akku.set_charge_per_hour(np.full(self.prediction_hours, 1))

        eauto = PVAkku(
            kapazitaet_wh=parameter["eauto_cap"],
            hours=self.prediction_hours,
            lade_effizienz=parameter["eauto_charge_efficiency"],
            entlade_effizienz=1.0,
            max_ladeleistung_w=parameter["eauto_charge_power"],
            start_soc_prozent=parameter["eauto_soc"],
        )
        eauto.set_charge_per_hour(np.full(self.prediction_hours, 1))

        # Initialize household appliance if applicable
        spuelmaschine = (
            Haushaltsgeraet(
                hours=self.prediction_hours,
                verbrauch_wh=parameter["haushaltsgeraet_wh"],
                dauer_h=parameter["haushaltsgeraet_dauer"],
            )
            if parameter["haushaltsgeraet_dauer"] > 0
            else None
        )

        # Initialize the inverter and energy management system
        wr = Wechselrichter(10000, akku)
        ems = EnergieManagementSystem(
            gesamtlast=parameter["gesamtlast"],
            pv_prognose_wh=parameter["pv_forecast"],
            strompreis_euro_pro_wh=parameter["strompreis_euro_pro_wh"],
            einspeiseverguetung_euro_pro_wh=einspeiseverguetung_euro_pro_wh,
            eauto=eauto,
            haushaltsgeraet=spuelmaschine,
            wechselrichter=wr,
        )

        # Setup the DEAP environment and optimization process
        self.setup_deap_environment(
            {"haushaltsgeraete": 1 if spuelmaschine else 0}, start_hour
        )
        self.toolbox.register(
            "evaluate",
            lambda ind: self.evaluate(ind, ems, parameter, start_hour, worst_case),
        )
        start_solution, extra_data = self.optimize(parameter["start_solution"])

        # Perform final evaluation on the best solution
        o = self.evaluate_inner(start_solution, ems, start_hour)
        discharge_hours_bin, eautocharge_hours_float, spuelstart_int = (
            self.split_individual(start_solution)
        )

        # Visualize the results
        visualisiere_ergebnisse(
            parameter["gesamtlast"],
            parameter["pv_forecast"],
            parameter["strompreis_euro_pro_wh"],
            o,
            discharge_hours_bin,
            eautocharge_hours_float,
            parameter["temperature_forecast"],
            start_hour,
            self.prediction_hours,
            einspeiseverguetung_euro_pro_wh,
            extra_data=extra_data,
        )

        # Return final results as a dictionary
        return {
            "discharge_hours_bin": discharge_hours_bin,
            "eautocharge_hours_float": eautocharge_hours_float,
            "result": o,
            "eauto_obj": ems.eauto.to_dict(),
            "start_solution": start_solution,
            "spuelstart": spuelstart_int,
            "simulation_data": o,
        }
