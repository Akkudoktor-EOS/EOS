from flask import Flask, jsonify, request
import numpy as np
from datetime import datetime
from  modules.class_load import *
from  modules.class_ems import *
from  modules.class_pv_forecast import *
from modules.class_akku import *
from modules.class_strompreis import *
from modules.class_heatpump import * 
from modules.class_load_container import * 
from modules.class_eauto import * 

from pprint import pprint
import matplotlib.pyplot as plt
from modules.visualize import *
from deap import base, creator, tools, algorithms
import numpy as np
import random
import os


start_hour = datetime.now().hour
prediction_hours = 24
hohe_strafe = 10.0



def evaluate_inner(individual, ems):
    
    discharge_hours_bin = individual[0::2]
    eautocharge_hours_float = individual[1::2]
    
    #print(discharge_hours_bin)
    #print(len(eautocharge_hours_float))
    ems.reset()
    ems.set_akku_discharge_hours(discharge_hours_bin)
    ems.set_eauto_charge_hours(eautocharge_hours_float)
    o = ems.simuliere(start_hour)

    return o

# Fitness-Funktion (muss Ihre EnergieManagementSystem-Logik integrieren)
def evaluate(individual,ems,parameter):
    o = evaluate_inner(individual,ems)
    
    gesamtbilanz = o["Gesamtbilanz_Euro"]
    
    # Überprüfung, ob der Mindest-SoC erreicht wird
    final_soc = ems.eauto.ladezustand_in_prozent()  # Nimmt den SoC am Ende des Optimierungszeitraums
    
    
    strafe = 0.0
    strafe = max(0,(parameter['eauto_min_soc']-ems.eauto.ladezustand_in_prozent()) * hohe_strafe ) 
    
    # print(ems.eauto.charge_array)
    # print(ems.eauto.ladezustand_in_prozent())    
    # print(strafe)    
    gesamtbilanz += strafe    
    gesamtbilanz += o["Gesamt_Verluste"]/1000.0
    return (gesamtbilanz,)

# Werkzeug-Setup
creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
creator.create("Individual", list, fitness=creator.FitnessMin)
toolbox = base.Toolbox()
toolbox.register("attr_bool", random.randint, 0, 1)
toolbox.register("attr_bool", random.randint, 0, 1)
toolbox.register("individual", tools.initCycle, creator.Individual, (toolbox.attr_bool,toolbox.attr_bool), n=prediction_hours)
toolbox.register("population", tools.initRepeat, list, toolbox.individual)
toolbox.register("mate", tools.cxTwoPoint)
toolbox.register("mutate", tools.mutFlipBit, indpb=0.05)
toolbox.register("select", tools.selTournament, tournsize=3)



# Genetischer Algorithmus
def optimize():
    population = toolbox.population(n=500)
    hof = tools.HallOfFame(1)
    
    stats = tools.Statistics(lambda ind: ind.fitness.values)
    stats.register("avg", np.mean)
    stats.register("min", np.min)
    stats.register("max", np.max)
    
    algorithms.eaMuPlusLambda(population, toolbox, 50, 100, cxpb=0.5, mutpb=0.5, ngen=500,             stats=stats, halloffame=hof, verbose=True)
    #algorithms.eaSimple(population, toolbox, cxpb=0.2, mutpb=0.2, ngen=1000,             stats=stats, halloffame=hof, verbose=True)
    return hof[0]





app = Flask(__name__)

# Dummy-Funktion für die Durchführung der Simulation/Optimierung
# Ersetzen Sie diese Logik durch Ihren eigentlichen Optimierungscode
def durchfuehre_simulation(parameter):

    ############
    # Parameter 
    ############
    date = (datetime.now().date() + timedelta(hours = prediction_hours)).strftime("%Y-%m-%d")
    date_now = datetime.now().strftime("%Y-%m-%d")

    akku_size = parameter['pv_akku_cap'] # Wh
    year_energy = parameter['year_energy'] #2000*1000 #Wh
    
    einspeiseverguetung_cent_pro_wh = np.full(prediction_hours, parameter["einspeiseverguetung_euro_pro_wh"])  #=  # € / Wh 7/(1000.0*100.0)

    max_heizleistung = parameter['max_heizleistung'] #1000  # 5 kW Heizleistung
    wp = Waermepumpe(max_heizleistung,prediction_hours)

    pv_forecast_url = parameter['pv_forecast_url'] #"https://api.akkudoktor.net/forecast?lat=52.52&lon=13.405&power=5000&azimuth=-10&tilt=7&powerInvertor=10000&horizont=20,27,22,20&power=4800&azimuth=-90&tilt=7&powerInvertor=10000&horizont=30,30,30,50&power=1400&azimuth=-40&tilt=60&powerInvertor=2000&horizont=60,30,0,30&power=1600&azimuth=5&tilt=45&powerInvertor=1400&horizont=45,25,30,60&past_days=5&cellCoEff=-0.36&inverterEfficiency=0.8&albedo=0.25&timezone=Europe%2FBerlin&hourly=relativehumidity_2m%2Cwindspeed_10m"

    akku = PVAkku(kapazitaet_wh=akku_size,hours=prediction_hours,start_soc_prozent=parameter["pv_soc"])
    discharge_array = np.full(prediction_hours,1) #np.array([1, 0, 1, 0, 1, 1, 1, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1, 0, 0, 0, 1, 1, 1, 1, 0, 1, 1, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0])   #
    laden_moeglich = np.full(prediction_hours,1) # np.array([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1, 1, 0, 0, 0, 0, 1, 1, 1, 1, 0, 1, 1, 0, 0])
    
    
    eauto = PVAkku(kapazitaet_wh=parameter["eauto_cap"], hours=prediction_hours, lade_effizienz=parameter["eauto_charge_efficiency"], entlade_effizienz=1.0, max_ladeleistung_w=parameter["eauto_charge_power"] ,start_soc_prozent=parameter["eauto_soc"])
    eauto.set_charge_per_hour(laden_moeglich)
    min_soc_eauto = parameter['eauto_min_soc']


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
    PVforecast = PVForecast(prediction_hours = prediction_hours, url=pv_forecast_url)
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

    ems = EnergieManagementSystem(akku=akku, gesamtlast = gesamtlast, pv_prognose_wh=pv_forecast, strompreis_cent_pro_wh=specific_date_prices, einspeiseverguetung_cent_pro_wh=einspeiseverguetung_cent_pro_wh, eauto=eauto)
    o = ems.simuliere(start_hour)
    

    
    
    def evaluate_wrapper(individual):
        return evaluate(individual, ems, parameter)
    
    
    toolbox.register("evaluate", evaluate_wrapper)

    

    start_solution = optimize()
    best_solution = start_solution
    o = evaluate_inner(best_solution, ems)
    eauto = ems.eauto.to_dict()
    discharge_hours_bin = best_solution[0::2]
    eautocharge_hours_float = best_solution[1::2]
    
    #print(o)
    
    #visualisiere_ergebnisse(gesamtlast, pv_forecast, specific_date_prices, o,best_solution[0::2],best_solution[1::2] , temperature_forecast, start_hour, prediction_hours)
    
    #print(eauto)
    return {"discharge_hours_bin":discharge_hours_bin, "eautocharge_hours_float":eautocharge_hours_float ,"result":o ,"eauto_obj":eauto}




@app.route('/simulation', methods=['POST'])
def simulation():
    if request.method == 'POST':
        parameter = request.json
        
        # Erforderliche Parameter prüfen
        erforderliche_parameter = [ 'pv_akku_cap', 'year_energy',"einspeiseverguetung_euro_pro_wh", 'max_heizleistung', 'pv_forecast_url', 'eauto_min_soc', "eauto_cap","eauto_charge_efficiency","eauto_charge_power","eauto_soc","pv_soc"]
        for p in erforderliche_parameter:
            if p not in parameter:
                return jsonify({"error": f"Fehlender Parameter: {p}"}), 400

        # Optional Typen der Parameter prüfen und sicherstellen, dass sie den Erwartungen entsprechen
        # if not isinstance(parameter['start_hour'], int):
            # return jsonify({"error": "start_hour muss vom Typ int sein"}), 400

        # Simulation durchführen
        ergebnis = durchfuehre_simulation(parameter)
        
        return jsonify(ergebnis)






if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0")


