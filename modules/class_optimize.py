from flask import Flask, jsonify, request
import numpy as np
from  modules.class_load import *
from  modules.class_ems import *
from  modules.class_pv_forecast import *
from modules.class_akku import *
from modules.class_strompreis import *
from modules.class_heatpump import * 
from modules.class_load_container import * 
from modules.class_sommerzeit import *
from modules.visualize import *
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
        
        # Werkzeug-Setup
        creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
        creator.create("Individual", list, fitness=creator.FitnessMin)
        self.toolbox = base.Toolbox()
        self.prediction_hours = prediction_hours#
        self.strafe = strafe
        
        # PARAMETER
        self.toolbox.register("attr_bool", random.randint, 0, 1)
        self.toolbox.register("attr_bool", random.randint, 0, 1)
        self.toolbox.register("individual", tools.initCycle, creator.Individual, (self.toolbox.attr_bool,self.toolbox.attr_bool), n=self.prediction_hours)
        self.toolbox.register("population", tools.initRepeat, list, self.toolbox.individual)
        self.toolbox.register("mate", tools.cxTwoPoint)
        self.toolbox.register("mutate", tools.mutFlipBit, indpb=0.05)
        self.toolbox.register("select", tools.selTournament, tournsize=3)    
        
        return
        
    def evaluate_inner(self,individual, ems,start_hour):
        
        discharge_hours_bin = individual[0::2]
        eautocharge_hours_float = individual[1::2]
        
        ems.reset()
        ems.set_akku_discharge_hours(discharge_hours_bin)
        ems.set_eauto_charge_hours(eautocharge_hours_float)
        o = ems.simuliere(start_hour)

        return o

    # Fitness-Funktion (muss Ihre EnergieManagementSystem-Logik integrieren)
    def evaluate(self,individual,ems,parameter,start_hour):
        o = self.evaluate_inner(individual,ems,start_hour)
        
        gesamtbilanz = o["Gesamtbilanz_Euro"]
        
        # Überprüfung, ob der Mindest-SoC erreicht wird
        final_soc = ems.eauto.ladezustand_in_prozent()  # Nimmt den SoC am Ende des Optimierungszeitraums
        
        
        strafe = 0.0
        strafe = max(0,(parameter['eauto_min_soc']-ems.eauto.ladezustand_in_prozent()) * self.strafe ) 
        
        gesamtbilanz += strafe    
        gesamtbilanz += o["Gesamt_Verluste"]/1000.0
        return (gesamtbilanz,)





    # Genetischer Algorithmus
    def optimize(self,start_solution=None):
        population = self.toolbox.population(n=100)
        hof = tools.HallOfFame(1)
        
        stats = tools.Statistics(lambda ind: ind.fitness.values)
        stats.register("avg", np.mean)
        stats.register("min", np.min)
        stats.register("max", np.max)
        
        print("Start:",start_solution)
        
        if start_solution is not None and start_solution != -1:
                population.insert(0, creator.Individual(start_solution))     
        
        #algorithms.eaMuPlusLambda(population, self.toolbox, 100, 200, cxpb=0.3, mutpb=0.3, ngen=500,             stats=stats, halloffame=hof, verbose=True)
        algorithms.eaSimple(population, self.toolbox, cxpb=0.8, mutpb=0.8, ngen=400,             stats=stats, halloffame=hof, verbose=True)
        return hof[0]


    def optimierung_ems(self,parameter=None, start_hour=None):

        ############
        # Parameter 
        ############
        date = (datetime.now().date() + timedelta(hours = self.prediction_hours)).strftime("%Y-%m-%d")
        date_now = datetime.now().strftime("%Y-%m-%d")
        
        akku_size = parameter['pv_akku_cap'] # Wh
        year_energy = parameter['year_energy'] #2000*1000 #Wh
        
        einspeiseverguetung_euro_pro_wh = np.full(self.prediction_hours, parameter["einspeiseverguetung_euro_pro_wh"])  #=  # € / Wh 7/(1000.0*100.0)

        max_heizleistung = parameter['max_heizleistung'] #1000  # 5 kW Heizleistung
        wp = Waermepumpe(max_heizleistung,self.prediction_hours)

        pv_forecast_url = parameter['pv_forecast_url'] #"https://api.akkudoktor.net/forecast?lat=50.8588&lon=7.3747&power=5000&azimuth=-10&tilt=7&powerInvertor=10000&horizont=20,27,22,20&power=4800&azimuth=-90&tilt=7&powerInvertor=10000&horizont=30,30,30,50&power=1400&azimuth=-40&tilt=60&powerInvertor=2000&horizont=60,30,0,30&power=1600&azimuth=5&tilt=45&powerInvertor=1400&horizont=45,25,30,60&past_days=5&cellCoEff=-0.36&inverterEfficiency=0.8&albedo=0.25&timezone=Europe%2FBerlin&hourly=relativehumidity_2m%2Cwindspeed_10m"

        akku = PVAkku(kapazitaet_wh=akku_size,hours=self.prediction_hours,start_soc_prozent=parameter["pv_soc"])
        discharge_array = np.full(self.prediction_hours,1) #np.array([1, 0, 1, 0, 1, 1, 1, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1, 0, 0, 0, 1, 1, 1, 1, 0, 1, 1, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0])   #
        laden_moeglich = np.full(self.prediction_hours,1) # np.array([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1, 1, 0, 0, 0, 0, 1, 1, 1, 1, 0, 1, 1, 0, 0])
        
        
        eauto = PVAkku(kapazitaet_wh=parameter["eauto_cap"], hours=self.prediction_hours, lade_effizienz=parameter["eauto_charge_efficiency"], entlade_effizienz=1.0, max_ladeleistung_w=parameter["eauto_charge_power"] ,start_soc_prozent=parameter["eauto_soc"])
        eauto.set_charge_per_hour(laden_moeglich)
        min_soc_eauto = parameter['eauto_min_soc']

        start_params = parameter['start_solution']

        gesamtlast = Gesamtlast()

        ###############
        # Load Forecast
        ###############
        lf = LoadForecast(filepath=r'load_profiles.npz', year_energy=year_energy)
        #leistung_haushalt = lf.get_daily_stats(date)[0,...]  # Datum anpassen
        leistung_haushalt = lf.get_stats_for_date_range(date_now,date)[0,...].flatten()
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
        filepath = os.path.join (r'test_data', r'strompreise_akkudokAPI.json')  # Pfad zur JSON-Datei anpassen
        #price_forecast = HourlyElectricityPriceForecast(source=filepath)
        price_forecast = HourlyElectricityPriceForecast(source="https://api.akkudoktor.net/prices?start="+date_now+"&end="+date+"")
        specific_date_prices = price_forecast.get_price_for_daterange(date_now,date)

        ###############
        # WP
        ##############
        leistung_wp = wp.simulate_24h(temperature_forecast)
        gesamtlast.hinzufuegen("Heatpump", leistung_wp)

        ems = EnergieManagementSystem(akku=akku, gesamtlast = gesamtlast, pv_prognose_wh=pv_forecast, strompreis_euro_pro_wh=specific_date_prices, einspeiseverguetung_euro_pro_wh=einspeiseverguetung_euro_pro_wh, eauto=eauto)
        o = ems.simuliere(start_hour)
    

        def evaluate_wrapper(individual):
            return self.evaluate(individual, ems, parameter,start_hour)
        
        self.toolbox.register("evaluate", evaluate_wrapper)
        start_solution = self.optimize(start_params)
        best_solution = start_solution
        o = self.evaluate_inner(best_solution, ems,start_hour)
        eauto = ems.eauto.to_dict()
        discharge_hours_bin = best_solution[0::2]
        eautocharge_hours_float = best_solution[1::2]
        
        #print(o)
        
        visualisiere_ergebnisse(gesamtlast, pv_forecast, specific_date_prices, o,best_solution[0::2],best_solution[1::2] , temperature_forecast, start_hour, self.prediction_hours,einspeiseverguetung_euro_pro_wh)
        
        os.system("scp visualisierungsergebnisse.pdf andreas@192.168.1.135:")
        
        #print(eauto)
        return {"discharge_hours_bin":discharge_hours_bin, "eautocharge_hours_float":eautocharge_hours_float ,"result":o ,"eauto_obj":eauto,"start_solution":best_solution}




