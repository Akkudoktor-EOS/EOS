from flask import Flask, jsonify, request
import numpy as np
from  modules.class_load import *
from  modules.class_ems import *
from  modules.class_pv_forecast import *
from modules.class_akku import *
from modules.class_heatpump import * 
from modules.class_load_container import * 
from modules.class_inverter import * 
from modules.class_sommerzeit import *
from modules.visualize import *
from modules.class_haushaltsgeraet import *
import os
from flask import Flask, send_from_directory
from pprint import pprint
import matplotlib
matplotlib.use('Agg')  # Setzt das Backend auf Agg
import matplotlib.pyplot as plt
import string
from datetime import datetime
from deap import base, creator, tools, algorithms
import numpy as np
import random
import os


def isfloat(num):
    try:
        float(num)
        return True
    except:
        return False

class optimization_problem:
    def __init__(self, prediction_hours=24, strafe = 10):
        self.prediction_hours = prediction_hours#
        self.strafe = strafe
        self.opti_param = None

    def setup_deap_environment(self,opti_param, start_hour):
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
        self.toolbox.register("attr_int", random.randint, start_hour, 23)
        
        ###################
        # Haushaltsgeraete
        #print("Haushalt:",opti_param["haushaltsgeraete"])
        if opti_param["haushaltsgeraete"]>0:   
                def create_individual():
                        attrs = [self.toolbox.attr_bool() for _ in range(2*self.prediction_hours)] + [self.toolbox.attr_int()]
                        return creator.Individual(attrs)

        else:
                def create_individual():
                        attrs = [self.toolbox.attr_bool() for _ in range(2*self.prediction_hours)] 
                        return creator.Individual(attrs)

        
        self.toolbox.register("individual", create_individual)#tools.initCycle, creator.Individual, (self.toolbox.attr_bool,self.toolbox.attr_bool), n=self.prediction_hours+1)
        self.toolbox.register("population", tools.initRepeat, list, self.toolbox.individual)
        self.toolbox.register("mate", tools.cxTwoPoint)
        self.toolbox.register("mutate", tools.mutFlipBit, indpb=0.1)
        self.toolbox.register("select", tools.selTournament, tournsize=3)    
        
    def evaluate_inner(self,individual, ems,start_hour):
        ems.reset()
        
        #print("Spuel:",self.opti_param)
        
        # Haushaltsgeraete
        if self.opti_param["haushaltsgeraete"]>0:   
                spuelstart_int = individual[-1]
                individual = individual[:-1]
                ems.set_haushaltsgeraet_start(spuelstart_int,global_start_hour=start_hour)
                
        discharge_hours_bin = individual[0::2]
        eautocharge_hours_float = individual[1::2]

        
        ems.set_akku_discharge_hours(discharge_hours_bin)
        ems.set_eauto_charge_hours(eautocharge_hours_float)
        
        
        o = ems.simuliere(start_hour)

        return o

    # Fitness-Funktion (muss Ihre EnergieManagementSystem-Logik integrieren)
    def evaluate(self,individual,ems,parameter,start_hour,worst_case):

        try:
                o = self.evaluate_inner(individual,ems,start_hour)
        except: 
                return (100000.0,)
                
        gesamtbilanz = o["Gesamtbilanz_Euro"]
        if worst_case:
                gesamtbilanz = gesamtbilanz * -1.0
        
        # Überprüfung, ob der Mindest-SoC erreicht wird
        final_soc = ems.eauto.ladezustand_in_prozent()  # Nimmt den SoC am Ende des Optimierungszeitraums
        
        
        eauto_roi = max(0,(parameter['eauto_min_soc']-ems.eauto.ladezustand_in_prozent()) ) 
        
        individual.extra_data = (o["Gesamtbilanz_Euro"],o["Gesamt_Verluste"], eauto_roi )
        
        
        
        strafe = 0.0
        strafe = max(0,(parameter['eauto_min_soc']-ems.eauto.ladezustand_in_prozent()) * self.strafe ) 
        gesamtbilanz += strafe    
        gesamtbilanz += o["Gesamt_Verluste"]/10000.0
                
        return (gesamtbilanz,)





    # Genetischer Algorithmus
    def optimize(self,start_solution=None):
        population = self.toolbox.population(n=400)
        hof = tools.HallOfFame(1)
        
        stats = tools.Statistics(lambda ind: ind.fitness.values)
        stats.register("avg", np.mean)
        stats.register("min", np.min)
        stats.register("max", np.max)
        
        print("Start:",start_solution)
        
        if start_solution is not None and start_solution != -1:
                population.insert(0, creator.Individual(start_solution))     
        
        #algorithms.eaMuPlusLambda(population, self.toolbox, 100, 200, cxpb=0.2, mutpb=0.2, ngen=500,             stats=stats, halloffame=hof, verbose=True)
        algorithms.eaSimple(population, self.toolbox, cxpb=0.1, mutpb=0.1, ngen=400,             stats=stats, halloffame=hof, verbose=True)
        
        member = {"bilanz":[],"verluste":[],"nebenbedingung":[]}
        for ind in population:
                if hasattr(ind, 'extra_data'):
                        extra_value1, extra_value2,extra_value3 = ind.extra_data
                        member["bilanz"].append(extra_value1)
                        member["verluste"].append(extra_value2)
                        member["nebenbedingung"].append(extra_value3)
        
        
        return hof[0], member


    def optimierung_ems(self,parameter=None, start_hour=None,worst_case=False, startdate=None):

        
        ############
        # Parameter 
        ############
        if startdate == None:
                date = (datetime.now().date() + timedelta(hours = self.prediction_hours)).strftime("%Y-%m-%d")
                date_now = datetime.now().strftime("%Y-%m-%d")
        else:
                date = (startdate + timedelta(hours = self.prediction_hours)).strftime("%Y-%m-%d")
                date_now = startdate.strftime("%Y-%m-%d")
        #print("Start_date:",date_now)
        
        akku_size = parameter['pv_akku_cap'] # Wh
        year_energy = parameter['year_energy'] #2000*1000 #Wh
        
        einspeiseverguetung_euro_pro_wh = np.full(self.prediction_hours, parameter["einspeiseverguetung_euro_pro_wh"])  #=  # € / Wh 7/(1000.0*100.0)

        max_heizleistung = parameter['max_heizleistung'] #1000  # 5 kW Heizleistung
        wp = Waermepumpe(max_heizleistung,self.prediction_hours)

        pv_forecast_url = parameter['pv_forecast_url'] #"https://api.akkudoktor.net/forecast?lat=52.52&lon=13.405&power=5000&azimuth=-10&tilt=7&powerInvertor=10000&horizont=20,27,22,20&power=4800&azimuth=-90&tilt=7&powerInvertor=10000&horizont=30,30,30,50&power=1400&azimuth=-40&tilt=60&powerInvertor=2000&horizont=60,30,0,30&power=1600&azimuth=5&tilt=45&powerInvertor=1400&horizont=45,25,30,60&past_days=5&cellCoEff=-0.36&inverterEfficiency=0.8&albedo=0.25&timezone=Europe%2FBerlin&hourly=relativehumidity_2m%2Cwindspeed_10m"

        akku = PVAkku(kapazitaet_wh=akku_size,hours=self.prediction_hours,start_soc_prozent=parameter["pv_soc"], max_ladeleistung_w=5000)
        discharge_array = np.full(self.prediction_hours,1) #np.array([1, 0, 1, 0, 1, 1, 1, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1, 0, 0, 0, 1, 1, 1, 1, 0, 1, 1, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0])   #
        laden_moeglich = np.full(self.prediction_hours,1) # np.array([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1, 1, 0, 0, 0, 0, 1, 1, 1, 1, 0, 1, 1, 0, 0])
        
        
        eauto = PVAkku(kapazitaet_wh=parameter["eauto_cap"], hours=self.prediction_hours, lade_effizienz=parameter["eauto_charge_efficiency"], entlade_effizienz=1.0, max_ladeleistung_w=parameter["eauto_charge_power"] ,start_soc_prozent=parameter["eauto_soc"])
        eauto.set_charge_per_hour(laden_moeglich)
        min_soc_eauto = parameter['eauto_min_soc']
        start_params = parameter['start_solution']
        gesamtlast = Gesamtlast(prediction_hours=self.prediction_hours)
        
        ###############
        # spuelmaschine
        ##############
        print(parameter)
        if parameter["haushaltsgeraet_dauer"] >0:
                spuelmaschine = Haushaltsgeraet(hours=self.prediction_hours, verbrauch_kwh=parameter["haushaltsgeraet_wh"], dauer_h=parameter["haushaltsgeraet_dauer"])
                spuelmaschine.set_startzeitpunkt(start_hour)  # Startet jetzt
        else: 
                spuelmaschine = None


        ###############
        # Load Forecast
        ###############
        lf = LoadForecast(filepath=r'load_profiles.npz', year_energy=year_energy)
        #leistung_haushalt = lf.get_daily_stats(date)[0,...]  # Datum anpassen
        
        leistung_haushalt = lf.get_stats_for_date_range(date_now,date)[0] # Nur Erwartungswert!
        
        

        
        gesamtlast.hinzufuegen("Haushalt", leistung_haushalt)

        ###############
        # PV Forecast
        ###############
        #PVforecast = PVForecast(filepath=os.path.join(r'test_data', r'pvprognose.json'))
        PVforecast = PVForecast(prediction_hours = self.prediction_hours, url=pv_forecast_url)
        #print("PVPOWER",parameter['pvpowernow'])
        if isfloat(parameter['pvpowernow']):
            PVforecast.update_ac_power_measurement(date_time=datetime.now(), ac_power_measurement=float(parameter['pvpowernow']))
            #PVforecast.print_ac_power_and_measurement()
        pv_forecast = PVforecast.get_pv_forecast_for_date_range(date_now,date) #get_forecast_for_date(date)
        
        

        temperature_forecast = PVforecast.get_temperature_for_date_range(date_now,date)


        ###############
        # Strompreise   
        ###############
        specific_date_prices = parameter["strompreis_euro_pro_wh"]
        print(specific_date_prices)
        #print("https://api.akkudoktor.net/prices?start="+date_now+"&end="+date)

        ###############
        # WP
        ##############
        leistung_wp = wp.simulate_24h(temperature_forecast)
        gesamtlast.hinzufuegen("Heatpump", leistung_wp)

        wr = Wechselrichter(5000, akku)

        ems = EnergieManagementSystem(gesamtlast = gesamtlast, pv_prognose_wh=pv_forecast, strompreis_euro_pro_wh=specific_date_prices, einspeiseverguetung_euro_pro_wh=einspeiseverguetung_euro_pro_wh, eauto=eauto, haushaltsgeraet=spuelmaschine,wechselrichter=wr)
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
            return self.evaluate(individual, ems, parameter,start_hour,worst_case)
        
        self.toolbox.register("evaluate", evaluate_wrapper)
        start_solution, extra_data = self.optimize(start_params)
        best_solution = start_solution
        o = self.evaluate_inner(best_solution, ems,start_hour)
        eauto = ems.eauto.to_dict()
        spuelstart_int = None
        # Haushaltsgeraete
        if self.opti_param["haushaltsgeraete"]>0:   
                spuelstart_int = best_solution[-1]
                best_solution = best_solution[:-1]
        discharge_hours_bin = best_solution[0::2]
        eautocharge_hours_float = best_solution[1::2]
        

     
        print(parameter)
        print(best_solution)
        visualisiere_ergebnisse(gesamtlast, pv_forecast, specific_date_prices, o,best_solution[0::2],best_solution[1::2] , temperature_forecast, start_hour, self.prediction_hours,einspeiseverguetung_euro_pro_wh,extra_data=extra_data)
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
        
        #print(eauto)
        return {"discharge_hours_bin":discharge_hours_bin, "eautocharge_hours_float":eautocharge_hours_float ,"result":o ,"eauto_obj":eauto,"start_solution":best_solution,"spuelstart":spuelstart_int,"simulation_data":o}




