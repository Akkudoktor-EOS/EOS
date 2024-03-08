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



prediction_hours = 24
date = (datetime.now().date() + timedelta(hours = prediction_hours)).strftime("%Y-%m-%d")
date_now = datetime.now().strftime("%Y-%m-%d")

akku_size = 30000 # Wh
year_energy = 2000*1000 #Wh
einspeiseverguetung_cent_pro_wh = np.full(prediction_hours, 7/(1000.0*100.0)) # € / Wh

max_heizleistung = 1000  # 5 kW Heizleistung
wp = Waermepumpe(max_heizleistung,prediction_hours)

akku = PVAkku(akku_size,prediction_hours)
discharge_array = np.full(prediction_hours,1) #np.array([1, 0, 1, 0, 1, 1, 1, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1, 0, 0, 0, 1, 1, 1, 1, 0, 1, 1, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0])   #

laden_moeglich = np.full(prediction_hours,1) # np.array([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1, 1, 0, 0, 0, 0, 1, 1, 1, 1, 0, 1, 1, 0, 0])
#np.full(prediction_hours,1)
eauto = EAuto(soc=10, capacity = 60000, power_charge = 7000, load_allowed = laden_moeglich)
min_soc_eauto = 10
hohe_strafe = 10.0




#Gesamtlast
#############
gesamtlast = Gesamtlast()


# Load Forecast
###############
lf = LoadForecast(filepath=r'load_profiles.npz', year_energy=year_energy)
#leistung_haushalt = lf.get_daily_stats(date)[0,...]  # Datum anpassen
leistung_haushalt = lf.get_stats_for_date_range(date_now,date)[0,...].flatten()
# print(date_now," ",date)
# print(leistung_haushalt.shape)
gesamtlast.hinzufuegen("Haushalt", leistung_haushalt)

# PV Forecast
###############
#PVforecast = PVForecast(filepath=os.path.join(r'test_data', r'pvprognose.json'))
PVforecast = PVForecast(prediction_hours = prediction_hours, url="https://api.akkudoktor.net/forecast?lat=50.8588&lon=7.3747&power=5000&azimuth=-10&tilt=7&powerInvertor=10000&horizont=20,27,22,20&power=4800&azimuth=-90&tilt=7&powerInvertor=10000&horizont=30,30,30,50&power=1400&azimuth=-40&tilt=60&powerInvertor=2000&horizont=60,30,0,30&power=1600&azimuth=5&tilt=45&powerInvertor=1400&horizont=45,25,30,60&past_days=5&cellCoEff=-0.36&inverterEfficiency=0.8&albedo=0.25&timezone=Europe%2FBerlin&hourly=relativehumidity_2m%2Cwindspeed_10m")
pv_forecast = PVforecast.get_pv_forecast_for_date_range(date_now,date) #get_forecast_for_date(date)

temperature_forecast = PVforecast.get_temperature_for_date_range(date_now,date)



# Strompreise
###############
filepath = os.path.join (r'test_data', r'strompreise_akkudokAPI.json')  # Pfad zur JSON-Datei anpassen
#price_forecast = HourlyElectricityPriceForecast(source=filepath)
price_forecast = HourlyElectricityPriceForecast(source="https://api.akkudoktor.net/prices?start="+date_now+"&end="+date+"")
specific_date_prices = price_forecast.get_price_for_daterange(date_now,date)

# WP
##############
leistung_wp = wp.simulate_24h(temperature_forecast)
gesamtlast.hinzufuegen("Heatpump", leistung_wp)


# EAuto
######################
leistung_eauto = eauto.get_stuendliche_last()
soc_eauto = eauto.get_stuendlicher_soc()
gesamtlast.hinzufuegen("eauto", leistung_eauto)

# print(gesamtlast.gesamtlast_berechnen())

# EMS / Stromzähler Bilanz
ems = EnergieManagementSystem(akku, gesamtlast.gesamtlast_berechnen(), pv_forecast, specific_date_prices, einspeiseverguetung_cent_pro_wh)


o = ems.simuliere(0)#ems.simuliere_ab_jetzt()
#pprint(o)
#pprint(o["Gesamtbilanz_Euro"])

#visualisiere_ergebnisse(gesamtlast,leistung_haushalt,leistung_wp, pv_forecast, specific_date_prices, o, soc_eauto)


#sys.exit()

# Optimierung

def evaluate_inner(individual):
    #print(individual)
    discharge_hours_bin = individual[0::2]
    eautocharge_hours_float = individual[1::2]
    
    #print(discharge_hours_bin)
    #print(len(eautocharge_hours_float))
    ems.reset()
    eauto.reset()
    ems.set_akku_discharge_hours(discharge_hours_bin)

    eauto.set_laden_moeglich(eautocharge_hours_float)
    eauto.berechne_ladevorgang()
    leistung_eauto = eauto.get_stuendliche_last()
    gesamtlast.hinzufuegen("eauto", leistung_eauto)
    
    ems.set_gesamtlast(gesamtlast.gesamtlast_berechnen())
    
    o = ems.simuliere(0)
    return o, eauto

# Fitness-Funktion (muss Ihre EnergieManagementSystem-Logik integrieren)
def evaluate(individual):
    o,eauto = evaluate_inner(individual)
    gesamtbilanz = o["Gesamtbilanz_Euro"]
    
    # Überprüfung, ob der Mindest-SoC erreicht wird
    final_soc = eauto.get_stuendlicher_soc()[-1]  # Nimmt den SoC am Ende des Optimierungszeitraums
    strafe = 0.0
    #if final_soc < min_soc_eauto:
    # Fügt eine Strafe hinzu, wenn der Mindest-SoC nicht erreicht wird
    strafe = max(0,(min_soc_eauto - final_soc) * hohe_strafe ) # `hohe_strafe` ist ein vorher festgelegter Strafwert
    gesamtbilanz += strafe    
    return (gesamtbilanz,)





# Werkzeug-Setup
creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
creator.create("Individual", list, fitness=creator.FitnessMin)

toolbox = base.Toolbox()

toolbox.register("attr_bool", random.randint, 0, 1)
toolbox.register("attr_bool", random.randint, 0, 1)
toolbox.register("individual", tools.initCycle, creator.Individual, (toolbox.attr_bool,toolbox.attr_bool), n=prediction_hours)



toolbox.register("population", tools.initRepeat, list, toolbox.individual)

toolbox.register("evaluate", evaluate)
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
    
    
start_solution = optimize()
    
    
print("Start Lösung:", start_solution)




# # Werkzeug-Setup
# creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
# creator.create("Individual", list, fitness=creator.FitnessMin)

# toolbox = base.Toolbox()




# toolbox.register("attr_bool", random.randint, 0, 1)
# toolbox.register("attr_float", random.uniform, 0.0, 1.0)
# toolbox.register("individual", tools.initCycle, creator.Individual, (toolbox.attr_bool,toolbox.attr_float), n=prediction_hours)

# start_individual = toolbox.individual()
# start_individual[:] = start_solution

# toolbox.register("population", tools.initRepeat, list, toolbox.individual)

# toolbox.register("evaluate", evaluate)
# toolbox.register("mate", tools.cxTwoPoint)
# toolbox.register("mutate", tools.mutFlipBit, indpb=0.05)
# toolbox.register("select", tools.selTournament, tournsize=3)

# # Genetischer Algorithmus
# def optimize():
    # population = toolbox.population(n=1000)
    # population[0] = start_individual
    # hof = tools.HallOfFame(1)
    
    # stats = tools.Statistics(lambda ind: ind.fitness.values)
    # stats.register("avg", np.mean)
    # stats.register("min", np.min)
    # stats.register("max", np.max)
    
    # algorithms.eaMuPlusLambda(population, toolbox, 100, 200, cxpb=0.5, mutpb=0.2, ngen=1000,   stats=stats, halloffame=hof, verbose=True)
    # #algorithms.eaSimple(population, toolbox, cxpb=0.2, mutpb=0.2, ngen=1000,             stats=stats, halloffame=hof, verbose=True)
    # return hof[0]

# best_solution = optimize()
best_solution = start_solution
print("Beste Lösung:", best_solution)

#ems.set_akku_discharge_hours(best_solution)
o,eauto = evaluate_inner(best_solution)

soc_eauto = eauto.get_stuendlicher_soc()
print(soc_eauto)
pprint(o)
pprint(eauto.get_stuendlicher_soc())





# # Werkzeug-Setup
# creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
# creator.create("Individual", list, fitness=creator.FitnessMin)

# toolbox = base.Toolbox()




# toolbox.register("attr_bool", random.randint, 0, 1)
# toolbox.register("attr_float", random.uniform, 0.0, 1.0)
# toolbox.register("individual", tools.initCycle, creator.Individual, (toolbox.attr_bool,toolbox.attr_float), n=prediction_hours)

# start_individual = toolbox.individual()
# start_individual[:] = start_solution

# toolbox.register("population", tools.initRepeat, list, toolbox.individual)

# toolbox.register("evaluate", evaluate)
# toolbox.register("mate", tools.cxTwoPoint)
# toolbox.register("mutate", tools.mutFlipBit, indpb=0.05)
# toolbox.register("select", tools.selTournament, tournsize=3)

# # Genetischer Algorithmus
# def optimize():
    # population = toolbox.population(n=1000)
    # population[0] = start_individual
    # hof = tools.HallOfFame(1)
    
    # stats = tools.Statistics(lambda ind: ind.fitness.values)
    # stats.register("avg", np.mean)
    # stats.register("min", np.min)
    # stats.register("max", np.max)
    
    # algorithms.eaMuPlusLambda(population, toolbox, 100, 200, cxpb=0.5, mutpb=0.2, ngen=1000,   stats=stats, halloffame=hof, verbose=True)
    # #algorithms.eaSimple(population, toolbox, cxpb=0.2, mutpb=0.2, ngen=1000,             stats=stats, halloffame=hof, verbose=True)
    # return hof[0]

# best_solution = optimize()
# print("Beste Lösung:", best_solution)

# #ems.set_akku_discharge_hours(best_solution)
# o,eauto = evaluate_inner(best_solution)

# soc_eauto = eauto.get_stuendlicher_soc()
# print(soc_eauto)
# pprint(o)
# pprint(eauto.get_stuendlicher_soc())

visualisiere_ergebnisse(gesamtlast,leistung_haushalt,leistung_wp, pv_forecast, specific_date_prices, o,soc_eauto,best_solution[0::2],best_solution[1::2] , temperature_forecast)


# for data in forecast.get_forecast_data():
    # print(data.get_date_time(), data.get_dc_power(), data.get_ac_power(), data.get_windspeed_10m(), data.get_temperature())for data in forecast.get_forecast_data():




# app = Flask(__name__)



# @app.route('/getdata', methods=['GET'])
# def get_data():
    # # Hole das Datum aus den Query-Parametern
    # date_str = request.args.get('date')
    # year_energy = request.args.get('year_energy')
    
    # try:
        # # Konvertiere das Datum in ein datetime-Objekt
        # date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        # filepath = r'.\load_profiles.npz'  # Pfad zur JSON-Datei anpassen
        # lf = cl.LoadForecast(filepath=filepath, year_energy=float(year_energy))
        # specific_date_prices = lf.get_daily_stats('2024-02-16')

        
        # # Berechne den Tag des Jahres
        # #day_of_year = date_obj.timetuple().tm_yday
        
        # # Konvertiere den Tag des Jahres in einen String, falls die Schlüssel als Strings gespeichert sind
        # #day_key = int(day_of_year)
        # #print(day_key)
        # # Überprüfe, ob der Tag im Jahr in den Daten vorhanden ist
        # array_list = lf.get_daily_stats(date_str)
        # pprint(array_list)
        # pprint(array_list.shape)
        # if array_list.shape == (2,24):
        # #if day_key < len(load_profiles_exp):
            # # Konvertiere das Array in eine Liste für die JSON-Antwort
             # #((load_profiles_exp_l[day_key]).tolist(),(load_profiles_std_l)[day_key].tolist())
            
            # return jsonify({date_str: array_list.tolist()})
        # else:
            # return jsonify({"error": "Datum nicht gefunden"}), 404
    # except ValueError:
        # # Wenn das Datum nicht im richtigen Format ist oder ungültig ist
        # return jsonify({"error": "Ungültiges Datum"}), 400

# if __name__ == '__main__':
    # app.run(debug=True)

