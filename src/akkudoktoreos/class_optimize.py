import random
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from deap import algorithms, base, creator, tools

from akkudoktoreos.class_akku import PVAkku
from akkudoktoreos.class_ems import EnergieManagementSystem
from akkudoktoreos.class_haushaltsgeraet import Homeappliance
from akkudoktoreos.class_inverter import Wechselrichter
from akkudoktoreos.config import possible_ev_charge_currents
from akkudoktoreos.visualize import visualisiere_ergebnisse


class optimization_problem:
    def __init__(
        self,
        prediction_hours: int = 48,
        penalty: float = 10,
        optimization_hours: int = 24,
        verbose: bool = False,
        fixed_seed: Optional[int] = None,
    ):
        """Initialize the optimization problem with the required parameters."""
        self.prediction_hours = prediction_hours
        self.penalty = penalty
        self.opti_param = None
        self.fixed_eauto_hours = prediction_hours - optimization_hours
        self.possible_charge_values = possible_ev_charge_currents
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
        """
        Decode the input array `discharge_hours_bin` into three separate arrays for AC charging, DC charging, and discharge.
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
        """
        Custom mutation function for the individual. This function mutates different parts of the individual:
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
        if self.opti_param["home_appliance"] > 0:
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
        if self.opti_param["home_appliance"] > 0:
            individual_components += [self.toolbox.attr_int()]

        return creator.Individual(individual_components)

    def split_individual(
        self, individual: List[float]
    ) -> Tuple[List[int], List[float], Optional[int]]:
        """
        Split the individual solution into its components:
        1. Discharge hours (-1 (Charge),0 (Nothing),1 (Discharge)),
        2. Electric vehicle charge hours (possible_charge_values),
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
            if self.opti_param and self.opti_param.get("home_appliance", 0) > 0
            else None
        )
        return discharge_hours_bin, eautocharge_hours_float, spuelstart_int

    def setup_deap_environment(self, opti_param: Dict[str, Any], start_hour: int) -> None:
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
        if self.optimize_dc_charge:
            self.toolbox.register("attr_discharge_state", random.randint, 0, 8)
        else:
            self.toolbox.register("attr_discharge_state", random.randint, 0, 6)

        if self.optimize_ev:
            self.toolbox.register(
                "attr_ev_charge_index", random.randint, 0, len(possible_ev_charge_currents) - 1
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
            up=len(possible_ev_charge_currents) - 1,
            indpb=0.2,
        )
        # - Start hour mutation for household devices
        self.toolbox.register("mutate_hour", tools.mutUniformInt, low=start_hour, up=23, indpb=0.2)

        # Register custom mutation function
        self.toolbox.register("mutate", self.mutate)

        self.toolbox.register("select", tools.selTournament, tournsize=3)

    def evaluate_inner(
        self, individual: List[float], ems: EnergieManagementSystem, start_hour: int
    ) -> Dict[str, Any]:
        """
        Internal evaluation function that simulates the energy management system (EMS)
        using the provided individual solution.
        """
        ems.reset()
        discharge_hours_bin, eautocharge_hours_index, spuelstart_int = self.split_individual(
            individual
        )
        if self.opti_param.get("home_appliance", 0) > 0:
            ems.set_home_appliance_start(spuelstart_int, global_start_hour=start_hour)

        ac, dc, discharge = self.decode_charge_discharge(discharge_hours_bin)

        ems.set_akku_discharge_hours(discharge)
        # Set DC charge hours only if DC optimization is enabled
        if self.optimize_dc_charge:
            ems.set_akku_dc_charge_hours(dc)
        ems.set_akku_ac_charge_hours(ac)

        if self.optimize_ev:
            eautocharge_hours_float = [
                possible_ev_charge_currents[i] for i in eautocharge_hours_index
            ]
            ems.set_ev_charge_hours(eautocharge_hours_float)
        else:
            ems.set_ev_charge_hours(np.full(self.prediction_hours, 0))
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
        except Exception as e:
            return (100000.0,)  # Return a high penalty in case of an exception

        gesamtbilanz = o["Gesamtbilanz_Euro"] * (-1.0 if worst_case else 1.0)

        discharge_hours_bin, eautocharge_hours_float, _ = self.split_individual(individual)

        # Small Penalty for not discharging
        gesamtbilanz += sum(
            0.01 for i in range(self.prediction_hours) if discharge_hours_bin[i] == 0.0
        )

        # Penalty for not meeting the minimum SOC (State of Charge) requirement
        # if parameter["eauto_min_soc"] - ems.eauto.ladezustand_in_prozent() <= 0.0 and  self.optimize_ev:
        #     gesamtbilanz += sum(
        #         self.penalty for ladeleistung in eautocharge_hours_float if ladeleistung != 0.0
        #     )

        individual.extra_data = (
            o["Gesamtbilanz_Euro"],
            o["Gesamt_Verluste"],
            parameter["eauto_min_soc"] - ems.eauto.ladezustand_in_prozent(),
        )

        # Adjust total balance with battery value and penalties for unmet SOC

        restwert_akku = ems.akku.aktueller_energieinhalt() * parameter["preis_euro_pro_wh_akku"]
        # print(ems.akku.aktueller_energieinhalt()," * ", parameter["preis_euro_pro_wh_akku"] , " ", restwert_akku, " ", gesamtbilanz)
        gesamtbilanz += -restwert_akku
        # print(gesamtbilanz)
        if self.optimize_ev:
            gesamtbilanz += max(
                0,
                (parameter["eauto_min_soc"] - ems.eauto.ladezustand_in_prozent()) * self.penalty,
            )

        return (gesamtbilanz,)

    def optimize(
        self, start_solution: Optional[List[float]] = None, ngen: int = 400
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
        parameter: Optional[Dict[str, Any]] = None,
        start_hour: Optional[int] = None,
        worst_case: bool = False,
        startdate: Optional[Any] = None,  # startdate is not used!
        *,
        ngen: int = 600,
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

        self.optimize_ev = True
        if parameter["eauto_min_soc"] - parameter["eauto_soc"] < 0:
            self.optimize_ev = False

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
            Homeappliance(
                hours=self.prediction_hours,
                verbrauch_wh=parameter["home_appliance"],
                dauer_h=parameter["home_appliance_duration"],
            )
            if parameter["home_appliance_duration"] > 0
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
            home_appliance=spuelmaschine,
            wechselrichter=wr,
        )

        # Setup the DEAP environment and optimization process
        self.setup_deap_environment({"home_appliance": 1 if spuelmaschine else 0}, start_hour)
        self.toolbox.register(
            "evaluate",
            lambda ind: self.evaluate(ind, ems, parameter, start_hour, worst_case),
        )
        start_solution, extra_data = self.optimize(parameter["start_solution"], ngen=ngen)  #

        # Perform final evaluation on the best solution
        o = self.evaluate_inner(start_solution, ems, start_hour)
        discharge_hours_bin, eautocharge_hours_float, spuelstart_int = self.split_individual(
            start_solution
        )
        if self.optimize_ev:
            eautocharge_hours_float = [
                possible_ev_charge_currents[i] for i in eautocharge_hours_float
            ]

        ac_charge, dc_charge, discharge = self.decode_charge_discharge(discharge_hours_bin)
        # Visualize the results
        visualisiere_ergebnisse(
            parameter["gesamtlast"],
            parameter["pv_forecast"],
            parameter["strompreis_euro_pro_wh"],
            o,
            ac_charge,
            dc_charge,
            discharge,
            parameter["temperature_forecast"],
            start_hour,
            self.prediction_hours,
            einspeiseverguetung_euro_pro_wh,
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
            "E-Auto_SoC_pro_Stunde",
            "Verluste_Pro_Stunde",
            "home_appliance_wh_per_hour",
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
        return {
            "ac_charge": ac_charge.tolist(),
            "dc_charge": dc_charge.tolist(),
            "discharge_allowed": discharge.tolist(),
            "eautocharge_hours_float": eautocharge_hours_float,
            "result": o,
            "eauto_obj": ems.eauto.to_dict(),
            "start_solution": start_solution,
            "spuelstart": spuelstart_int,
            "simulation_data": o,
        }
