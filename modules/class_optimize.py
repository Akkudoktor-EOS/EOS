import os
import sys
import random
import string
from datetime import datetime

import numpy as np
import matplotlib
matplotlib.use('Agg')  # Set the backend to Agg
import matplotlib.pyplot as plt
from pprint import pprint
from flask import Flask, jsonify, request, send_from_directory
from deap import base, creator, tools, algorithms

from modules.class_akku import *
from modules.class_ems import *
from modules.class_heatpump import *
from modules.class_haushaltsgeraet import *
from modules.class_inverter import *
from modules.class_load import *
from modules.class_load_container import *
from modules.class_pv_forecast import *
from modules.class_sommerzeit import *
from modules.visualize import *
from config import *

# Add the parent directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def isfloat(num):
    """Check if a string or value can be converted to a float."""
    try:
        float(num)
        return True
    except ValueError:  # Catch only conversion errors
        return False

def differential_evolution(population, toolbox, cxpb, mutpb, ngen, stats=None, halloffame=None, verbose=__debug__):
    """
    Differential Evolution Algorithm.
    
    Parameters:
    population : list
        List of individuals in the population.
    toolbox : object
        DEAP toolbox that defines necessary methods (evaluate, clone, etc.).
    cxpb : float
        Crossover probability.
    mutpb : float
        Mutation probability.
    ngen : int
        Number of generations.
    stats : object, optional
        Object to log statistics of the evolutionary process.
    halloffame : object, optional
        Object that keeps track of the best individuals.
    verbose : bool, optional
        If True, print the evolutionary process log.
    
    Returns:
    population : list
        Final population after evolution.
    logbook : object
        Logbook with statistics for each generation.
    """
    
    # Evaluate the entire initial population
    fitnesses = list(map(toolbox.evaluate, population))
    for ind, fit in zip(population, fitnesses):
        ind.fitness.values = fit  # Assign fitness to each individual

    # Update hall of fame with initial population if it exists
    if halloffame is not None:
        halloffame.update(population)
    
    # Logbook to record statistics
    logbook = tools.Logbook()
    logbook.header = ['gen', 'nevals'] + (stats.fields if stats else [])

    # Evolutionary loop for each generation
    for gen in range(ngen):
        # Generate next generation through mutation and recombination
        for i, target in enumerate(population):
            # Randomly select three distinct individuals for mutation
            a, b, c = random.sample([ind for ind in population if ind != target], 3)
            mutant = toolbox.clone(a)  # Clone the first individual to create a mutant

            # Mutation step: mutate each gene in the mutant individual
            for k in range(len(mutant)):
                mutant[k] = c[k] + mutpb * (a[k] - b[k])  # Apply mutation formula
                if random.random() < cxpb:  # Crossover step (recombination)
                    mutant[k] = target[k]  # Crossover with the target individual
            
            # Evaluate the fitness of the mutated individual
            mutant.fitness.values = toolbox.evaluate(mutant)
            
            # Replace the target individual with the mutant if it has better fitness
            if mutant.fitness > target.fitness:
                population[i] = mutant

        # Update hall of fame with the current population if it exists
        if halloffame is not None:
            halloffame.update(population)

        # Gather statistics for the current generation
        record = stats.compile(population) if stats else {}
        logbook.record(gen=gen, nevals=len(population), **record)

        # Print logbook statistics if verbose mode is enabled
        if verbose:
            print(logbook.stream)
    
    # Return the final population and logbook
    return population, logbook


class optimization_problem:
    def __init__(self, prediction_hours=24, strafe=10, optimization_hours=24):
        self.prediction_hours = prediction_hours
        self.strafe = strafe
        self.opti_param = None
        self.fixed_eauto_hours = prediction_hours - optimization_hours
        self.possible_charge_values = moegliche_ladestroeme_in_prozent

    def split_individual(self, individual):
        """
        Splits the given individual into the following parameters:
        - Discharge parameters (discharge_hours_bin)
        - Charge parameters (eautocharge_hours_float)
        - Household devices (spuelstart_int, if applicable)
        """
        # Extract discharge and charge parameters directly from the individual
        discharge_hours_bin = individual[:self.prediction_hours]  # First 24 values are Booleans (discharging)
        eautocharge_hours_float = individual[self.prediction_hours:self.prediction_hours * 2]  # Next 24 values are floats (charging)

        spuelstart_int = None
        if self.opti_param and self.opti_param.get("haushaltsgeraete", 0) > 0:
            spuelstart_int = individual[-1]  # Last value is the start time for household devices

        return discharge_hours_bin, eautocharge_hours_float, spuelstart_int

    def setup_deap_environment(self, opti_param, start_hour):
        """
        Sets up the DEAP environment for genetic optimization, registering
        fitness, individual creation, mutation, and selection mechanisms.
        """
        self.opti_param = opti_param

        # Remove previously created types in DEAP if they exist
        if "FitnessMin" in creator.__dict__:
            del creator.FitnessMin
        if "Individual" in creator.__dict__:
            del creator.Individual

        # Create new types for fitness and individuals
        creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
        creator.create("Individual", list, fitness=creator.FitnessMin)

        # Define toolbox parameters
        self.toolbox = base.Toolbox()
        self.toolbox.register("attr_bool", random.randint, 0, 1)
        self.toolbox.register("attr_float", random.uniform, 0, 1)  # For continuous values between 0 and 1 (e.g., EV charge power)
        # self.toolbox.register("attr_choice", random.choice, self.possible_charge_values)  # For discrete charge levels
        self.toolbox.register("attr_int", random.randint, start_hour, 23)

        # Household appliances setup
        if opti_param["haushaltsgeraete"] > 0:
            def create_individual():
                attrs = [self.toolbox.attr_bool() for _ in range(self.prediction_hours)]  # 24 bools for discharge
                attrs += [self.toolbox.attr_float() for _ in range(self.prediction_hours)]  # 24 floats for charging
                attrs.append(self.toolbox.attr_int())  # Start time for household devices
                return creator.Individual(attrs)
        else:
            def create_individual():
                attrs = [self.toolbox.attr_bool() for _ in range(self.prediction_hours)]  # 24 bools for discharge
                attrs += [self.toolbox.attr_float() for _ in range(self.prediction_hours)]  # 24 floats for charging
                return creator.Individual(attrs)

        # Register creation, mating, mutation, and selection functions in DEAP's toolbox
        self.toolbox.register("individual", create_individual)
        self.toolbox.register("population", tools.initRepeat, list, self.toolbox.individual)
        self.toolbox.register("mate", tools.cxTwoPoint)
        self.toolbox.register("mutate", tools.mutFlipBit, indpb=0.1)
        # self.toolbox.register("mutate", mutate_choice, self.possible_charge_values, indpb=0.1)
        # self.toolbox.register("mutate", tools.mutUniformInt, low=0, up=len(self.possible_charge_values)-1, indpb=0.1)
        self.toolbox.register("select", tools.selTournament, tournsize=3)

    def evaluate_inner(self, individual, ems, start_hour):
        """
        Internal evaluation function: applies the individual's parameters to the EMS and runs a simulation.
        """
        ems.reset()
        discharge_hours_bin, eautocharge_hours_float, spuelstart_int = self.split_individual(individual)

        # Set household devices start time if applicable
        if self.opti_param["haushaltsgeraete"] > 0:
            ems.set_haushaltsgeraet_start(spuelstart_int, global_start_hour=start_hour)

        # Set battery discharge schedule
        ems.set_akku_discharge_hours(discharge_hours_bin)

        # Set fixed values for the last 'x' hours
        for i in range(self.prediction_hours - self.fixed_eauto_hours, self.prediction_hours):
            eautocharge_hours_float[i] = 0.0  # Set the last 'x' hours to fixed value

        # Set EV charging schedule
        ems.set_eauto_charge_hours(eautocharge_hours_float)

        # Run simulation
        o = ems.simuliere(start_hour)

        return o

    def evaluate(self, individual, ems, parameter, start_hour, worst_case):
        """
        Fitness function that evaluates the given individual by applying it to the EMS and calculating penalties and rewards.
        """
        try:
            o = self.evaluate_inner(individual, ems, start_hour)
        except Exception:
            return (100000.0,)

        # Calculate total balance in euros
        gesamtbilanz = o["Gesamtbilanz_Euro"]
        if worst_case:
            gesamtbilanz *= -1.0

        discharge_hours_bin, eautocharge_hours_float, spuelstart_int = self.split_individual(individual)
        max_ladeleistung = np.max(self.possible_charge_values)

        # Penalty for exceeding charging power limits
        strafe_ueberschreitung = 0.0
        for ladeleistung in eautocharge_hours_float:
            if ladeleistung > max_ladeleistung:
                strafe_ueberschreitung += self.strafe * 10  # Penalty is proportional to the violation

        # Penalty for unused discharge
        for i in range(self.prediction_hours):
            if discharge_hours_bin[i] == 0.0:
                gesamtbilanz += 0.01  # Small penalty for each unused discharge hour

        # Penalty for charging the EV in restricted hours
        for i in range(self.prediction_hours - self.fixed_eauto_hours, self.prediction_hours):
            if eautocharge_hours_float[i] != 0.0:
                gesamtbilanz += self.strafe  # Penalty for charging during fixed hours

        # Check if minimum state of charge (SoC) for the EV is reached
        final_soc = ems.eauto.ladezustand_in_prozent()
        if (parameter['eauto_min_soc'] - final_soc) <= 0.0:
            for i in range(self.prediction_hours):
                if eautocharge_hours_float[i] != 0.0:
                    gesamtbilanz += self.strafe  # Penalty for not meeting minimum SoC

        # Record extra data for the individual
        eauto_roi = parameter['eauto_min_soc'] - final_soc
        individual.extra_data = (o["Gesamtbilanz_Euro"], o["Gesamt_Verluste"], eauto_roi)

        # Calculate residual energy in the battery
        restenergie_akku = ems.akku.aktueller_energieinhalt()
        restwert_akku = restenergie_akku * parameter["preis_euro_pro_wh_akku"]

        # Final penalties and fitness calculation
        strafe = max(0, (parameter['eauto_min_soc'] - final_soc) * self.strafe)
        gesamtbilanz += strafe - restwert_akku + strafe_ueberschreitung

        return (gesamtbilanz,)

    # Genetic Algorithm for Optimization
    def optimize(self, start_solution=None):
        """
        Runs the genetic algorithm to optimize the solution.
        :param start_solution: Optional starting solution to inject into the population.
        :return: Best solution (hall of fame) and member statistics.
        """
        population = self.toolbox.population(n=300)
        hof = tools.HallOfFame(1)  # Hall of Fame to store the best solution
        
        # Statistics object to track the optimization progress
        stats = tools.Statistics(lambda ind: ind.fitness.values)
        stats.register("avg", np.mean)
        stats.register("min", np.min)
        stats.register("max", np.max)

        print("Start solution:", start_solution)

        # Inject starting solution into population if provided
        if start_solution is not None and start_solution != -1:
            for _ in range(3):
                population.insert(0, creator.Individual(start_solution))
        
        # Execute the genetic algorithm (Mu + Lambda strategy)
        algorithms.eaMuPlusLambda(population, self.toolbox, mu=100, lambda_=200, cxpb=0.5, mutpb=0.3, ngen=400, stats=stats, halloffame=hof, verbose=True)

        # Collect extra data (if exists) from the individuals in the population
        member = {"bilanz": [], "verluste": [], "nebenbedingung": []}
        for ind in population:
            if hasattr(ind, 'extra_data'):
                extra_value1, extra_value2, extra_value3 = ind.extra_data
                member["bilanz"].append(extra_value1)
                member["verluste"].append(extra_value2)
                member["nebenbedingung"].append(extra_value3)

        return hof[0], member

    def optimierung_ems(self, parameter=None, start_hour=None, worst_case=False, startdate=None):
        """
        Main EMS (Energy Management System) optimization function.
        Initializes various system components like PV forecast, battery, and household devices.
        Runs the optimizer and visualizes results.
        :param parameter: Dictionary containing all necessary system parameters
        :param start_hour: Starting hour for the simulation
        :param worst_case: Flag to handle worst-case scenario optimization
        :param startdate: Optional start date for simulation
        :return: Dictionary with optimization results
        """
        # Set date ranges for prediction based on start date or current date
        if startdate is None:
            date = (datetime.now().date() + timedelta(hours=self.prediction_hours)).strftime("%Y-%m-%d")
            date_now = datetime.now().strftime("%Y-%m-%d")
        else:
            date = (startdate + timedelta(hours=self.prediction_hours)).strftime("%Y-%m-%d")
            date_now = startdate.strftime("%Y-%m-%d")

        # Initialize key parameters for the optimization process
        akku_size = parameter['pv_akku_cap']  # Battery capacity (Wh)
        einspeiseverguetung_euro_pro_wh = np.full(self.prediction_hours, parameter["einspeiseverguetung_euro_pro_wh"])  # Feed-in tariff (€/Wh)

        # Create battery and e-vehicle objects
        akku = PVAkku(kapazitaet_wh=akku_size, hours=self.prediction_hours, start_soc_prozent=parameter["pv_soc"], max_ladeleistung_w=5000)
        discharge_array = np.full(self.prediction_hours, 1)  # Array defining discharge schedule
        akku.set_charge_per_hour(discharge_array)

        laden_moeglich = np.full(self.prediction_hours, 1)  # Array defining charging schedule
        eauto = PVAkku(kapazitaet_wh=parameter["eauto_cap"], hours=self.prediction_hours, lade_effizienz=parameter["eauto_charge_efficiency"], entlade_effizienz=1.0, max_ladeleistung_w=parameter["eauto_charge_power"], start_soc_prozent=parameter["eauto_soc"])
        eauto.set_charge_per_hour(laden_moeglich)

        min_soc_eauto = parameter['eauto_min_soc']
        start_params = parameter['start_solution']

        # Initialize household device if duration > 0
        if parameter["haushaltsgeraet_dauer"] > 0:
            spuelmaschine = Haushaltsgeraet(hours=self.prediction_hours, verbrauch_kwh=parameter["haushaltsgeraet_wh"], dauer_h=parameter["haushaltsgeraet_dauer"])
            spuelmaschine.set_startzeitpunkt(start_hour)
        else:
            spuelmaschine = None

        # PV and Temperature Forecast
        pv_forecast = parameter['pv_forecast']
        temperature_forecast = parameter['temperature_forecast']

        # Electricity prices for the specified date
        specific_date_prices = parameter["strompreis_euro_pro_wh"]

        # Initialize components like inverter and EMS
        wr = Wechselrichter(10000, akku)
        ems = EnergieManagementSystem(gesamtlast=parameter["gesamtlast"], pv_prognose_wh=pv_forecast, strompreis_euro_pro_wh=specific_date_prices, einspeiseverguetung_euro_pro_wh=einspeiseverguetung_euro_pro_wh, eauto=eauto, haushaltsgeraet=spuelmaschine, wechselrichter=wr)
        o = ems.simuliere(start_hour)

        # Initialize optimizer parameters
        opti_param = {"haushaltsgeraete": 0}
        if spuelmaschine:
            opti_param["haushaltsgeraete"] = 1

        self.setup_deap_environment(opti_param, start_hour)

        def evaluate_wrapper(individual):
            """Wrapper for evaluating individuals."""
            return self.evaluate(individual, ems, parameter, start_hour, worst_case)

        self.toolbox.register("evaluate", evaluate_wrapper)

        # Run optimization
        start_solution, extra_data = self.optimize(start_params)
        best_solution = start_solution
        o = self.evaluate_inner(best_solution, ems, start_hour)

        # Process the best solution and split it into relevant components
        eauto = ems.eauto.to_dict()
        discharge_hours_bin, eautocharge_hours_float, spuelstart_int = self.split_individual(best_solution)

        # Visualize the results
        visualisiere_ergebnisse(parameter["gesamtlast"], pv_forecast, specific_date_prices, o, discharge_hours_bin, eautocharge_hours_float, temperature_forecast, start_hour, self.prediction_hours, einspeiseverguetung_euro_pro_wh, extra_data=extra_data)
        os.system("cp visualisierungsergebnisse.pdf ~/")

        # Return the final results of the simulation
        return {
            "discharge_hours_bin": discharge_hours_bin,
            "eautocharge_hours_float": eautocharge_hours_float,
            "result": o,
            "eauto_obj": eauto,
            "start_solution": best_solution,
            "spuelstart": spuelstart_int,
            "simulation_data": o
        }
