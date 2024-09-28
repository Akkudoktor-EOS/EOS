import os
import sys
import random
from datetime import datetime, timedelta

import numpy as np

from deap import base, creator, tools, algorithms

from modules.class_akku import PVAkku
from modules.class_ems import EnergieManagementSystem
from modules.class_haushaltsgeraet import Haushaltsgeraet
from modules.class_inverter import Wechselrichter
from config import moegliche_ladestroeme_in_prozent
from modules.visualize import visualisiere_ergebnisse

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class optimization_problem:
    def __init__(self, prediction_hours=48, strafe=10, optimization_hours=24, verbose=False, fixed_seed=None):
        self.prediction_hours = prediction_hours
        self.strafe = strafe
        self.opti_param = None
        self.fixed_eauto_hours = prediction_hours - optimization_hours
        self.possible_charge_values = moegliche_ladestroeme_in_prozent
        self.verbose = verbose
        if fixed_seed is not None:
            random.seed(fixed_seed)

    def split_individual(self, individual):
        """Splits an individual into its components: discharge hours, EV charge hours, and appliance start."""
        discharge_hours_bin = individual[:self.prediction_hours]
        eautocharge_hours_float = individual[self.prediction_hours:self.prediction_hours * 2]  

        spuelstart_int = individual[-1] if self.opti_param.get("haushaltsgeraete", 0) > 0 else None

        return discharge_hours_bin, eautocharge_hours_float, spuelstart_int

    def setup_deap_environment(self, opti_param, start_hour):
        """Sets up the DEAP environment with the given optimization parameters."""
        self.opti_param = opti_param
        if "FitnessMin" in creator.__dict__:
            del creator.FitnessMin
        if "Individual" in creator.__dict__:
            del creator.Individual
        # Clear any previous fitness and individual definitions
        creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
        creator.create("Individual", list, fitness=creator.FitnessMin)

        self.toolbox = base.Toolbox()
        self.toolbox.register("attr_bool", random.randint, 0, 1)
        self.toolbox.register("attr_float", random.uniform, 0, 1)  
        self.toolbox.register("attr_int", random.randint, start_hour, 23)

        def create_individual():
            """Creates an individual based on the prediction hours and appliance start time."""
            attrs = [self.toolbox.attr_bool() for _ in range(self.prediction_hours)]  
            attrs += [self.toolbox.attr_float() for _ in range(self.prediction_hours)]  
            if opti_param["haushaltsgeraete"] > 0:
                attrs.append(self.toolbox.attr_int())  
            return creator.Individual(attrs)

        self.toolbox.register("individual", create_individual)
        self.toolbox.register("population", tools.initRepeat, list, self.toolbox.individual)
        self.toolbox.register("mate", tools.cxTwoPoint)
        self.toolbox.register("mutate", tools.mutFlipBit, indpb=0.1)
        self.toolbox.register("select", tools.selTournament, tournsize=3)

    def evaluate_inner(self, individual, ems, start_hour):
        """Performs inner evaluation of an individual's performance."""
        ems.reset()
        discharge_hours_bin, eautocharge_hours_float, spuelstart_int = self.split_individual(individual)

        if self.opti_param["haushaltsgeraete"] > 0:
            ems.set_haushaltsgeraet_start(spuelstart_int, global_start_hour=start_hour)

        ems.set_akku_discharge_hours(discharge_hours_bin)

        # Ensure fixed EV charging hours are set to 0.0
        eautocharge_hours_float[self.prediction_hours - self.fixed_eauto_hours:] = [0.0] * self.fixed_eauto_hours
        ems.set_eauto_charge_hours(eautocharge_hours_float)

        return ems.simuliere(start_hour)

    def evaluate(self, individual, ems, parameter, start_hour, worst_case):
        """
        Fitness function that evaluates the given individual by applying it to the EMS and calculating penalties and rewards.
        """
        try:
            evaluation_results = self.evaluate_inner(individual, ems, start_hour)
        except Exception:
            return (100000.0,)

        # Calculate total balance in euros
        gesamtbilanz = evaluation_results["Gesamtbilanz_Euro"]
        if worst_case:
            gesamtbilanz *= -1.0

        discharge_hours_bin, eautocharge_hours_float, spuelstart_int = self.split_individual(individual)
        max_ladeleistung = np.max(self.possible_charge_values)

        # Calculate penalties
        strafe_ueberschreitung = self.calculate_exceeding_penalty(eautocharge_hours_float, max_ladeleistung)
        gesamtbilanz += self.calculate_unused_discharge_penalty(discharge_hours_bin)
        gesamtbilanz += self.calculate_restricted_charging_penalty(eautocharge_hours_float, parameter)

        # Check minimum state of charge (SoC) for the EV
        final_soc = ems.eauto.ladezustand_in_prozent()
        if (parameter['eauto_min_soc'] - final_soc) > 0.0:
            gesamtbilanz += self.calculate_min_soc_penalty(eautocharge_hours_float, parameter, final_soc)

        # Record extra data for the individual
        eauto_roi = parameter['eauto_min_soc'] - final_soc
        individual.extra_data = (evaluation_results["Gesamtbilanz_Euro"], evaluation_results["Gesamt_Verluste"], eauto_roi)

        # Calculate residual energy in the battery
        restenergie_akku = ems.akku.aktueller_energieinhalt()
        restwert_akku = restenergie_akku * parameter["preis_euro_pro_wh_akku"]

        # Final penalties and fitness calculation
        strafe = max(0, (parameter['eauto_min_soc'] - final_soc) * self.strafe)
        gesamtbilanz += strafe - restwert_akku + strafe_ueberschreitung

        return (gesamtbilanz,)
    def calculate_exceeding_penalty(self, eautocharge_hours_float, max_ladeleistung):
        """Calculate penalties for exceeding charging power limits."""
        penalty = 0.0
        for ladeleistung in eautocharge_hours_float:
            if ladeleistung > max_ladeleistung:
                penalty += self.strafe * 10  # Penalty is proportional to the violation
        return penalty

    def calculate_unused_discharge_penalty(self, discharge_hours_bin):
        """Calculate penalty for unused discharge hours."""
        penalty = 0.0
        for hour in discharge_hours_bin:
            if hour == 0.0:
                penalty += 0.01  # Small penalty for each unused discharge hour
        return penalty

    def calculate_restricted_charging_penalty(self, eautocharge_hours_float, parameter):
        """Calculate penalty for charging the EV during restricted hours."""
        penalty = 0.0
        for i in range(self.prediction_hours - self.fixed_eauto_hours, self.prediction_hours):
            if eautocharge_hours_float[i] != 0.0:
                penalty += self.strafe  # Penalty for charging during fixed hours
        return penalty

    def calculate_min_soc_penalty(self, eautocharge_hours_float, parameter, final_soc):
        """Calculate penalty for not meeting the minimum state of charge (SoC)."""
        penalty = 0.0
        for hour in eautocharge_hours_float:
            if hour != 0.0:
                penalty += self.strafe  # Penalty for not meeting minimum SoC
        return penalty
    # Genetic Algorithm for Optimization
    


    # Example of how to use the callback in your optimization

    def optimize(self, start_solution=None, generations_no_improvement=20):
        population = self.toolbox.population(n=300)
        hof = tools.HallOfFame(1)
        
        stats = tools.Statistics(lambda ind: ind.fitness.values)
        stats.register("avg", np.mean)
        stats.register("min", np.min)
        stats.register("max", np.max)
        
        if self.verbose:
            print("Start solution:", start_solution)

        if start_solution is not None and start_solution != -1:
            starting_individual = creator.Individual(start_solution)
            population = [starting_individual] * 3 + population

        # Register the convergence callback
        convergence_count = 0
        convergence_last = float('inf')
        generations_no_improvement = 20

        # Run the genetic algorithm with 3 additional callback per generation
        for gen in range(1000):  # Define the number of generations
            population, logbook = algorithms.eaMuPlusLambda(
                population, self.toolbox,
                mu=100, lambda_=200,
                cxpb=0.5, mutpb=0.3,
                ngen=2, stats=stats,  # Run for 1 generation at a time
                halloffame=hof, verbose=False
            )
            # Retrieve statistics from the logbook (only one generation per loop)
            if len(logbook) > 0:
                gen_stats = logbook[-1]
                # Print generation stats if self.verbose is True
                if self.verbose:
                    print(f"Generation {gen}: {gen_stats}")

            # Call the convergence check after each generation
            
            best_fitness = max(ind.fitness.values[0] for ind in population)
            
            if best_fitness >= convergence_last:
                convergence_count += 1
                if convergence_count >= generations_no_improvement:
                    if self.verbose:
                        print(f"Convergence detected at generation {gen}. No improvement in the last {generations_no_improvement} generations.")
                    break
            else:
                convergence_count = 0
                convergence_last = best_fitness
        # Collect extra data (if exists) from the individuals in the population
        member = {"bilanz": [], "verluste": [], "nebenbedingung": []}
        for ind in population:
            if hasattr(ind, 'extra_data'):
                member["bilanz"].append(ind.extra_data[0])
                member["verluste"].append(ind.extra_data[1])
                member["nebenbedingung"].append(ind.extra_data[2])
        print(max(ind.fitness.values[0] for ind in population))

        # Return the best solution
        return hof[0], member
   
    def optimierung_ems(self, parameter=None, start_hour=None, worst_case=False, startdate=None):
        """Orchestrates the entire EMS optimization."""
        current_date = datetime.now()
        if startdate is None:
            date = (current_date + timedelta(hours=self.prediction_hours)).strftime("%Y-%m-%d")
            date_now = current_date.strftime("%Y-%m-%d")
        else:
            date = (startdate + timedelta(hours=self.prediction_hours)).strftime("%Y-%m-%d")
            date_now = startdate.strftime("%Y-%m-%d")

        # Initialize battery and EV objects
        akku = PVAkku(kapazitaet_wh=parameter['pv_akku_cap'], hours=self.prediction_hours, 
                      start_soc_prozent=parameter["pv_soc"], max_ladeleistung_w=5000)
        akku.set_charge_per_hour(np.ones(self.prediction_hours))

        eauto = PVAkku(kapazitaet_wh=parameter["eauto_cap"], hours=self.prediction_hours,
                       lade_effizienz=parameter["eauto_charge_efficiency"], max_ladeleistung_w=parameter["eauto_charge_power"], 
                       start_soc_prozent=parameter["eauto_soc"])
        eauto.set_charge_per_hour(np.ones(self.prediction_hours))

        # Household appliance initialization
        spuelmaschine = None
        if parameter["haushaltsgeraet_dauer"] > 0:
            spuelmaschine = Haushaltsgeraet(hours=self.prediction_hours, 
                                            verbrauch_kwh=parameter["haushaltsgeraet_wh"], 
                                            dauer_h=parameter["haushaltsgeraet_dauer"])
            spuelmaschine.set_startzeitpunkt(start_hour)

        ems = EnergieManagementSystem(
            gesamtlast=parameter["gesamtlast"],
            pv_prognose_wh=parameter['pv_forecast'],
            strompreis_euro_pro_wh=parameter["strompreis_euro_pro_wh"],
            einspeiseverguetung_euro_pro_wh=np.full(self.prediction_hours, parameter["einspeiseverguetung_euro_pro_wh"]),
            eauto=eauto,
            haushaltsgeraet=spuelmaschine,
            wechselrichter=Wechselrichter(10000, akku)
        )

        self.setup_deap_environment({"haushaltsgeraete": int(spuelmaschine is not None)}, start_hour)

        self.toolbox.register("evaluate", lambda ind: self.evaluate(ind, ems, parameter, start_hour, worst_case))
        start_solution, extra_data = self.optimize(parameter['start_solution'])
        best_solution = start_solution

        # Perform final evaluation and visualize results
        o = self.evaluate_inner(best_solution, ems, start_hour)
        discharge_hours_bin, eautocharge_hours_float, spuelstart_int = self.split_individual(best_solution)

        visualisiere_ergebnisse(parameter["gesamtlast"], parameter['pv_forecast'], parameter["strompreis_euro_pro_wh"], o, 
                                 discharge_hours_bin, eautocharge_hours_float, 
                                 parameter['temperature_forecast'], start_hour, self.prediction_hours, 
                                 parameter["strompreis_euro_pro_wh"], extra_data=extra_data)

        return {
            "discharge_hours_bin": discharge_hours_bin,
            "eautocharge_hours_float": eautocharge_hours_float,
            "result": o,
            "eauto_obj": ems.eauto.to_dict(),
            "start_solution": best_solution,
            "spuelstart": spuelstart_int,
            "simulation_data": o
        }
