import os

import matplotlib
import numpy as np

from modules.class_akku import *
from modules.class_ems import *
from modules.class_haushaltsgeraet import *
from modules.class_heatpump import *
from modules.class_inverter import *
from modules.class_load import *
from modules.class_load_container import *
from modules.class_pv_forecast import *
from modules.class_sommerzeit import *
from modules.visualize import *

matplotlib.use("Agg")  # Setzt das Backend auf Agg
import random
from datetime import datetime

from deap import algorithms, base, creator, tools

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import *


def isfloat(num):
    try:
        float(num)
        return True
    except:
        return False


def differential_evolution(
    population,
    toolbox,
    cxpb,
    mutpb,
    ngen,
    stats=None,
    halloffame=None,
    verbose=__debug__,
):
    """Differential Evolution Algorithm"""

    # Evaluate the entire population
    fitnesses = list(map(toolbox.evaluate, population))
    for ind, fit in zip(population, fitnesses):
        ind.fitness.values = fit

    if halloffame is not None:
        halloffame.update(population)

    logbook = tools.Logbook()
    logbook.header = ["gen", "nevals"] + (stats.fields if stats else [])

    for gen in range(ngen):
        # Generate the next generation by mutation and recombination
        for i, target in enumerate(population):
            a, b, c = random.sample([ind for ind in population if ind != target], 3)
            mutant = toolbox.clone(a)
            for k in range(len(mutant)):
                mutant[k] = c[k] + mutpb * (a[k] - b[k])  # Mutation step
                if random.random() < cxpb:  # Recombination step
                    mutant[k] = target[k]

            # Evaluate the mutant
            mutant.fitness.values = toolbox.evaluate(mutant)

            # Replace if mutant is better
            if mutant.fitness > target.fitness:
                population[i] = mutant

        # Update hall of fame
        if halloffame is not None:
            halloffame.update(population)

        # Gather all the fitnesses in one list and print the stats
        record = stats.compile(population) if stats else {}
        logbook.record(gen=gen, nevals=len(population), **record)
        if verbose:
            print(logbook.stream)

    return population, logbook


class optimization_problem:
    def __init__(self, prediction_hours=24, strafe=10, optimization_hours=24):
        self.prediction_hours = prediction_hours  #
        self.strafe = strafe
        self.opti_param = None
        self.fixed_eauto_hours = prediction_hours - optimization_hours
        self.possible_charge_values = moegliche_ladestroeme_in_prozent

    def split_individual(self, individual):
        """
        Teilt das gegebene Individuum in die verschiedenen Parameter auf:
        - Entladeparameter (discharge_hours_bin)
        - Ladeparameter (eautocharge_hours_float)
        - Haushaltsgeräte (spuelstart_int, falls vorhanden)
        """
        # Extrahiere die Entlade- und Ladeparameter direkt aus dem Individuum
        discharge_hours_bin = individual[
            : self.prediction_hours
        ]  # Erste 24 Werte sind Bool (Entladen)
        eautocharge_hours_float = individual[
            self.prediction_hours : self.prediction_hours * 2
        ]  # Nächste 24 Werte sind Float (Laden)

        spuelstart_int = None
        if self.opti_param and self.opti_param.get("haushaltsgeraete", 0) > 0:
            spuelstart_int = individual[
                -1
            ]  # Letzter Wert ist Startzeit für Haushaltsgerät

        return discharge_hours_bin, eautocharge_hours_float, spuelstart_int

    def setup_deap_environment(self, opti_param, start_hour):
        self.opti_param = opti_param

        if "FitnessMin" in creator.__dict__:
            del creator.FitnessMin
        if "Individual" in creator.__dict__:
            del creator.Individual

        creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
        creator.create("Individual", list, fitness=creator.FitnessMin)

        # PARAMETER
        self.toolbox = base.Toolbox()
        self.toolbox.register("attr_bool", random.randint, 0, 1)
        self.toolbox.register(
            "attr_float", random.uniform, 0, 1
        )  # Für kontinuierliche Werte zwischen 0 und 1 (z.B. für E-Auto-Ladeleistung)
        # self.toolbox.register("attr_choice", random.choice, self.possible_charge_values)  # Für diskrete Ladeströme

        self.toolbox.register("attr_int", random.randint, start_hour, 23)

        ###################
        # Haushaltsgeraete
        # print("Haushalt:",opti_param["haushaltsgeraete"])
        if opti_param["haushaltsgeraete"] > 0:

            def create_individual():
                attrs = [
                    self.toolbox.attr_bool() for _ in range(self.prediction_hours)
                ]  # 24 Bool-Werte für Entladen
                attrs += [
                    self.toolbox.attr_float() for _ in range(self.prediction_hours)
                ]  # 24 Float-Werte für Laden
                attrs.append(self.toolbox.attr_int())  # Haushaltsgerät-Startzeit
                return creator.Individual(attrs)

        else:

            def create_individual():
                attrs = [
                    self.toolbox.attr_bool() for _ in range(self.prediction_hours)
                ]  # 24 Bool-Werte für Entladen
                attrs += [
                    self.toolbox.attr_float() for _ in range(self.prediction_hours)
                ]  # 24 Float-Werte für Laden
                return creator.Individual(attrs)

        self.toolbox.register(
            "individual", create_individual
        )  # tools.initCycle, creator.Individual, (self.toolbox.attr_bool,self.toolbox.attr_bool), n=self.prediction_hours+1)
        self.toolbox.register(
            "population", tools.initRepeat, list, self.toolbox.individual
        )
        self.toolbox.register("mate", tools.cxTwoPoint)
        self.toolbox.register("mutate", tools.mutFlipBit, indpb=0.1)

        # self.toolbox.register("mutate", mutate_choice, self.possible_charge_values, indpb=0.1)
        # self.toolbox.register("mutate", tools.mutUniformInt, low=0, up=len(self.possible_charge_values)-1, indpb=0.1)

        self.toolbox.register("select", tools.selTournament, tournsize=3)

    def evaluate_inner(self, individual, ems, start_hour):
        ems.reset()

        # print("Spuel:",self.opti_param)

        discharge_hours_bin, eautocharge_hours_float, spuelstart_int = (
            self.split_individual(individual)
        )

        # Haushaltsgeraete
        if self.opti_param["haushaltsgeraete"] > 0:
            ems.set_haushaltsgeraet_start(spuelstart_int, global_start_hour=start_hour)

        # discharge_hours_bin = np.full(self.prediction_hours,0)
        ems.set_akku_discharge_hours(discharge_hours_bin)

        # Setze die festen Werte für die letzten x Stunden
        for i in range(
            self.prediction_hours - self.fixed_eauto_hours, self.prediction_hours
        ):
            eautocharge_hours_float[i] = (
                0.0  # Setze die letzten x Stunden auf einen festen Wert (oder vorgegebenen Wert)
            )

        # print(eautocharge_hours_float)

        ems.set_eauto_charge_hours(eautocharge_hours_float)

        o = ems.simuliere(start_hour)

        return o

    # Fitness-Funktion (muss Ihre EnergieManagementSystem-Logik integrieren)
    def evaluate(self, individual, ems, parameter, start_hour, worst_case):
        try:
            o = self.evaluate_inner(individual, ems, start_hour)
        except:
            return (100000.0,)

        gesamtbilanz = o["Gesamtbilanz_Euro"]
        if worst_case:
            gesamtbilanz = gesamtbilanz * -1.0

        discharge_hours_bin, eautocharge_hours_float, spuelstart_int = (
            self.split_individual(individual)
        )
        max_ladeleistung = np.max(moegliche_ladestroeme_in_prozent)

        strafe_überschreitung = 0.0

        # Ladeleistung überschritten?
        for ladeleistung in eautocharge_hours_float:
            if ladeleistung > max_ladeleistung:
                # Berechne die Überschreitung
                überschreitung = ladeleistung - max_ladeleistung
                # Füge eine Strafe hinzu (z.B. 10 Einheiten Strafe pro Prozentpunkt Überschreitung)
                strafe_überschreitung += (
                    self.strafe * 10
                )  # Hier ist die Strafe proportional zur Überschreitung

        # Für jeden Discharge 0, eine kleine Strafe von 1 Cent, da die Lastvertelung noch fehlt. Also wenn es egal ist, soll er den Akku entladen lassen
        for i in range(0, self.prediction_hours):
            if (
                discharge_hours_bin[i] == 0.0
            ):  # Wenn die letzten x Stunden von einem festen Wert abweichen
                gesamtbilanz += 0.01  # Bestrafe den Optimierer

        # E-Auto nur die ersten self.fixed_eauto_hours
        for i in range(
            self.prediction_hours - self.fixed_eauto_hours, self.prediction_hours
        ):
            if (
                eautocharge_hours_float[i] != 0.0
            ):  # Wenn die letzten x Stunden von einem festen Wert abweichen
                gesamtbilanz += self.strafe  # Bestrafe den Optimierer

        # Überprüfung, ob der Mindest-SoC erreicht wird
        final_soc = (
            ems.eauto.ladezustand_in_prozent()
        )  # Nimmt den SoC am Ende des Optimierungszeitraums

        if (parameter["eauto_min_soc"] - ems.eauto.ladezustand_in_prozent()) <= 0.0:
            # print (parameter['eauto_min_soc']," " ,ems.eauto.ladezustand_in_prozent()," ",(parameter['eauto_min_soc']-ems.eauto.ladezustand_in_prozent()))
            for i in range(0, self.prediction_hours):
                if (
                    eautocharge_hours_float[i] != 0.0
                ):  # Wenn die letzten x Stunden von einem festen Wert abweichen
                    gesamtbilanz += self.strafe  # Bestrafe den Optimierer

        eauto_roi = parameter["eauto_min_soc"] - ems.eauto.ladezustand_in_prozent()
        individual.extra_data = (
            o["Gesamtbilanz_Euro"],
            o["Gesamt_Verluste"],
            eauto_roi,
        )

        restenergie_akku = ems.akku.aktueller_energieinhalt()
        restwert_akku = restenergie_akku * parameter["preis_euro_pro_wh_akku"]
        # print(restenergie_akku)
        # print(parameter["preis_euro_pro_wh_akku"])
        # print(restwert_akku)
        # print()
        strafe = 0.0
        strafe = max(
            0,
            (parameter["eauto_min_soc"] - ems.eauto.ladezustand_in_prozent())
            * self.strafe,
        )
        gesamtbilanz += strafe - restwert_akku + strafe_überschreitung
        # gesamtbilanz += o["Gesamt_Verluste"]/10000.0

        return (gesamtbilanz,)

    # Genetischer Algorithmus
    def optimize(self, start_solution=None):
        population = self.toolbox.population(n=300)
        hof = tools.HallOfFame(1)

        stats = tools.Statistics(lambda ind: ind.fitness.values)
        stats.register("avg", np.mean)
        stats.register("min", np.min)
        stats.register("max", np.max)

        print("Start:", start_solution)

        if start_solution is not None and start_solution != -1:
            population.insert(0, creator.Individual(start_solution))
            population.insert(1, creator.Individual(start_solution))
            population.insert(2, creator.Individual(start_solution))

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
            verbose=True,
        )
        # algorithms.eaSimple(population, self.toolbox, cxpb=0.3, mutpb=0.3, ngen=200,             stats=stats, halloffame=hof, verbose=True)
        # algorithms.eaMuCommaLambda(population, self.toolbox, mu=100, lambda_=200, cxpb=0.2, mutpb=0.4, ngen=300, stats=stats, halloffame=hof, verbose=True)
        # population, log = differential_evolution(population, self.toolbox, cxpb=0.2, mutpb=0.5, ngen=200, stats=stats, halloffame=hof, verbose=True)

        member = {"bilanz": [], "verluste": [], "nebenbedingung": []}
        for ind in population:
            if hasattr(ind, "extra_data"):
                extra_value1, extra_value2, extra_value3 = ind.extra_data
                member["bilanz"].append(extra_value1)
                member["verluste"].append(extra_value2)
                member["nebenbedingung"].append(extra_value3)

        return hof[0], member

    def optimierung_ems(
        self, parameter=None, start_hour=None, worst_case=False, startdate=None
    ):
        ############
        # Parameter
        ############
        if startdate == None:
            date = (
                datetime.now().date() + timedelta(hours=self.prediction_hours)
            ).strftime("%Y-%m-%d")
            date_now = datetime.now().strftime("%Y-%m-%d")
        else:
            date = (startdate + timedelta(hours=self.prediction_hours)).strftime(
                "%Y-%m-%d"
            )
            date_now = startdate.strftime("%Y-%m-%d")
        # print("Start_date:",date_now)

        akku_size = parameter["pv_akku_cap"]  # Wh

        einspeiseverguetung_euro_pro_wh = np.full(
            self.prediction_hours, parameter["einspeiseverguetung_euro_pro_wh"]
        )  # =  # € / Wh 7/(1000.0*100.0)
        discharge_array = np.full(
            self.prediction_hours, 1
        )  # np.array([1, 0, 1, 0, 1, 1, 1, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1, 0, 0, 0, 1, 1, 1, 1, 0, 1, 1, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0])   #
        akku = PVAkku(
            kapazitaet_wh=akku_size,
            hours=self.prediction_hours,
            start_soc_prozent=parameter["pv_soc"],
            max_ladeleistung_w=5000,
        )
        akku.set_charge_per_hour(discharge_array)

        laden_moeglich = np.full(
            self.prediction_hours, 1
        )  # np.array([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1, 1, 0, 0, 0, 0, 1, 1, 1, 1, 0, 1, 1, 0, 0])
        eauto = PVAkku(
            kapazitaet_wh=parameter["eauto_cap"],
            hours=self.prediction_hours,
            lade_effizienz=parameter["eauto_charge_efficiency"],
            entlade_effizienz=1.0,
            max_ladeleistung_w=parameter["eauto_charge_power"],
            start_soc_prozent=parameter["eauto_soc"],
        )
        eauto.set_charge_per_hour(laden_moeglich)
        min_soc_eauto = parameter["eauto_min_soc"]
        start_params = parameter["start_solution"]

        ###############
        # spuelmaschine
        ##############
        print(parameter)
        if parameter["haushaltsgeraet_dauer"] > 0:
            spuelmaschine = Haushaltsgeraet(
                hours=self.prediction_hours,
                verbrauch_kwh=parameter["haushaltsgeraet_wh"],
                dauer_h=parameter["haushaltsgeraet_dauer"],
            )
            spuelmaschine.set_startzeitpunkt(start_hour)  # Startet jetzt
        else:
            spuelmaschine = None

        ###############
        # PV Forecast
        ###############
        # PVforecast = PVForecast(filepath=os.path.join(r'test_data', r'pvprognose.json'))
        # PVforecast = PVForecast(prediction_hours = self.prediction_hours, url=pv_forecast_url)
        # #print("PVPOWER",parameter['pvpowernow'])
        # if isfloat(parameter['pvpowernow']):
        # PVforecast.update_ac_power_measurement(date_time=datetime.now(), ac_power_measurement=float(parameter['pvpowernow']))
        # #PVforecast.print_ac_power_and_measurement()
        pv_forecast = parameter[
            "pv_forecast"
        ]  # PVforecast.get_pv_forecast_for_date_range(date_now,date) #get_forecast_for_date(date)
        temperature_forecast = parameter[
            "temperature_forecast"
        ]  # PVforecast.get_temperature_for_date_range(date_now,date)

        ###############
        # Strompreise
        ###############
        specific_date_prices = parameter["strompreis_euro_pro_wh"]
        print(specific_date_prices)
        # print("https://api.akkudoktor.net/prices?start="+date_now+"&end="+date)

        wr = Wechselrichter(10000, akku)

        ems = EnergieManagementSystem(
            gesamtlast=parameter["gesamtlast"],
            pv_prognose_wh=pv_forecast,
            strompreis_euro_pro_wh=specific_date_prices,
            einspeiseverguetung_euro_pro_wh=einspeiseverguetung_euro_pro_wh,
            eauto=eauto,
            haushaltsgeraet=spuelmaschine,
            wechselrichter=wr,
        )
        o = ems.simuliere(start_hour)

        ###############
        # Optimizer Init
        ##############
        opti_param = {}
        opti_param["haushaltsgeraete"] = 0
        if spuelmaschine != None:
            opti_param["haushaltsgeraete"] = 1

        self.setup_deap_environment(opti_param, start_hour)

        def evaluate_wrapper(individual):
            return self.evaluate(individual, ems, parameter, start_hour, worst_case)

        self.toolbox.register("evaluate", evaluate_wrapper)
        start_solution, extra_data = self.optimize(start_params)
        best_solution = start_solution
        o = self.evaluate_inner(best_solution, ems, start_hour)
        eauto = ems.eauto.to_dict()
        spuelstart_int = None
        discharge_hours_bin, eautocharge_hours_float, spuelstart_int = (
            self.split_individual(best_solution)
        )

        print(parameter)
        print(best_solution)
        visualisiere_ergebnisse(
            parameter["gesamtlast"],
            pv_forecast,
            specific_date_prices,
            o,
            discharge_hours_bin,
            eautocharge_hours_float,
            temperature_forecast,
            start_hour,
            self.prediction_hours,
            einspeiseverguetung_euro_pro_wh,
            extra_data=extra_data,
        )
        os.system("cp visualisierungsergebnisse.pdf ~/")

        # 'Eigenverbrauch_Wh_pro_Stunde': eigenverbrauch_wh_pro_stunde,
        # 'Netzeinspeisung_Wh_pro_Stunde': netzeinspeisung_wh_pro_stunde,
        # 'Netzbezug_Wh_pro_Stunde': netzbezug_wh_pro_stunde,
        # 'Kosten_Euro_pro_Stunde': kosten_euro_pro_stunde,
        # 'akku_soc_pro_stunde': akku_soc_pro_stunde,
        # 'Einnahmen_Euro_pro_Stunde': einnahmen_euro_pro_stunde,
        # 'Gesamtbilanz_Euro': gesamtkosten_euro,
        # 'E-Auto_SoC_pro_Stunde':eauto_soc_pro_stunde,
        # 'Gesamteinnahmen_Euro': sum(einnahmen_euro_pro_stunde),
        # 'Gesamtkosten_Euro': sum(kosten_euro_pro_stunde),
        # "Verluste_Pro_Stunde":verluste_wh_pro_stunde,
        # "Gesamt_Verluste":sum(verluste_wh_pro_stunde),
        # "Haushaltsgeraet_wh_pro_stunde":haushaltsgeraet_wh_pro_stunde

        # print(eauto)
        return {
            "discharge_hours_bin": discharge_hours_bin,
            "eautocharge_hours_float": eautocharge_hours_float,
            "result": o,
            "eauto_obj": eauto,
            "start_solution": best_solution,
            "spuelstart": spuelstart_int,
            "simulation_data": o,
        }
