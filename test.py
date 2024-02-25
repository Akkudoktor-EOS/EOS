from flask import Flask, jsonify, request
import numpy as np
from datetime import datetime
from  modules.class_load import *
from  modules.class_ems import *
from  modules.class_pv_forecast import *
from modules.class_akku import *
from modules.class_strompreis import *
from modules.class_heatpump import * 
from pprint import pprint
import matplotlib.pyplot as plt
from modules.visualize import *
from deap import base, creator, tools, algorithms
import numpy as np
import random
import os


date = "2024-02-26"
akku_size = 1000 # Wh
year_energy = 2000*1000 #Wh
einspeiseverguetung_cent_pro_wh = np.full(24, 7/1000.0)

max_heizleistung = 5000  # 5 kW Heizleistung
wp = Waermepumpe(max_heizleistung)

akku = PVAkku(akku_size)
discharge_array = np.full(24,1)

# Load Forecast
lf = LoadForecast(filepath=r'load_profiles.npz', year_energy=year_energy)
leistung_haushalt = lf.get_daily_stats(date)[0,...]  # Datum anpassen
pprint(leistung_haushalt.shape)

# PV Forecast
#PVforecast = PVForecast(filepath=os.path.join(r'test_data', r'pvprognose.json'))

PVforecast = PVForecast(url="https://api.akkudoktor.net/forecast?lat=50.8588&lon=7.3747&power=5400&azimuth=-10&tilt=7&powerInvertor=2500&horizont=20,40,30,30&power=4800&azimuth=-90&tilt=7&powerInvertor=2500&horizont=20,40,45,50&power=1480&azimuth=-90&tilt=70&powerInvertor=1120&horizont=60,45,30,70&power=1600&azimuth=5&tilt=60&powerInvertor=1200&horizont=60,45,30,70&past_days=5&cellCoEff=-0.36&inverterEfficiency=0.8&albedo=0.25&timezone=Europe%2FBerlin&hourly=relativehumidity_2m%2Cwindspeed_10m")
pv_forecast = PVforecast.get_forecast_for_date(date)
temperature_forecast = PVforecast.get_temperature_forecast_for_date(date)
pprint(pv_forecast)
sys.exit()
# Strompreise
filepath = os.path.join (r'test_data', r'strompreis.json')  # Pfad zur JSON-Datei anpassen
price_forecast = HourlyElectricityPriceForecast(filepath)
specific_date_prices = price_forecast.get_prices_for_date(date) 

# WP
leistung_wp = wp.simulate_24h(temperature_forecast)

# LOAD
load = leistung_haushalt + leistung_wp


# EMS / Stromzähler Bilanz
ems = EnergieManagementSystem(akku, load, pv_forecast, specific_date_prices, einspeiseverguetung_cent_pro_wh)
o = ems.simuliere()
pprint(o["Gesamtbilanz_Euro"])


# Optimierung

# Fitness-Funktion (muss Ihre EnergieManagementSystem-Logik integrieren)
def evaluate(individual):
    # Hier müssen Sie Ihre Logik einbauen, um die Gesamtbilanz zu berechnen
    # basierend auf dem gegebenen `individual` (discharge_array)
    #akku.set_discharge_per_hour(individual)
    ems.reset()
    ems.set_akku_discharge_hours(individual)
    o = ems.simuliere()
    gesamtbilanz = o["Gesamtbilanz_Euro"]
    #print(individual, " ",gesamtbilanz)
    return (gesamtbilanz,)

# Werkzeug-Setup
creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
creator.create("Individual", list, fitness=creator.FitnessMin)

toolbox = base.Toolbox()
toolbox.register("attr_bool", random.randint, 0, 1)
toolbox.register("individual", tools.initRepeat, creator.Individual, toolbox.attr_bool, 24)
toolbox.register("population", tools.initRepeat, list, toolbox.individual)

toolbox.register("evaluate", evaluate)
toolbox.register("mate", tools.cxTwoPoint)
toolbox.register("mutate", tools.mutFlipBit, indpb=0.05)
toolbox.register("select", tools.selTournament, tournsize=3)

# Genetischer Algorithmus
def optimize():
    population = toolbox.population(n=100)
    hof = tools.HallOfFame(1)
    
    stats = tools.Statistics(lambda ind: ind.fitness.values)
    stats.register("avg", np.mean)
    stats.register("min", np.min)
    stats.register("max", np.max)
    
    algorithms.eaSimple(population, toolbox, cxpb=0.5, mutpb=0.2, ngen=100, 
                        stats=stats, halloffame=hof, verbose=True)
    return hof[0]

best_solution = optimize()
print("Beste Lösung:", best_solution)

ems.set_akku_discharge_hours(best_solution)
o = ems.simuliere()
pprint(o["Gesamtbilanz_Euro"])



visualisiere_ergebnisse(load,leistung_haushalt,leistung_wp, pv_forecast, specific_date_prices, o)


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

