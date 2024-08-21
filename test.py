from flask import Flask, jsonify, request
import numpy as np
from datetime import datetime
from  modules.class_optimize import *
# from  modules.class_load import *
# from  modules.class_ems import *
# from  modules.class_pv_forecast import *
# from modules.class_akku import *
# from modules.class_strompreis import *
# from modules.class_heatpump import * 
# from modules.class_load_container import * 
# from modules.class_eauto import * 
from modules.class_optimize import *

from pprint import pprint
import matplotlib.pyplot as plt
from modules.visualize import *
from deap import base, creator, tools, algorithms
import numpy as np
import random
import os


start_hour = 8

pv_forecast= [
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        46.0757222688471,
        474.780954810247,
        1049.36036517475,
        1676.86962934168,
        2037.0885036865,
        2600.03233682621,
        5307.79424852068,
        5214.54927119013,
        5392.8995394438,
        4229.09283442043,
        3568.84965239262,
        2627.95972505784,
        1618.04209206715,
        718.733713468062,
        102.060092599437,
        0,
        0,
        0,
        0,
        0,
        -0.068771006309608,
        0,
        0.0275649587447597,
        0,
        53.980235336087,
        543.602674801833,
        852.52597210804,
        964.253104261402,
        1043.15079499546,
        1333.69973977172,
        6901.19158127423,
        6590.62442617817,
        6161.97317306069,
        4530.33886807194,
        3535.37982191984,
        2388.65608163334,
        1365.10812389941,
        557.452392556485,
        82.376303341511,
        0.026903650788687,
        0
    ]
temperature_forecast= [
        18.3,
        17.8,
        16.9,
        16.2,
        15.6,
        15.1,
        14.6,
        14.2,
        14.3,
        14.8,
        15.7,
        16.7,
        17.4,
        18,
        18.6,
        19.2,
        19.1,
        18.7,
        18.5,
        17.7,
        16.2,
        14.6,
        13.6,
        13,
        12.6,
        12.2,
        11.7,
        11.6,
        11.3,
        11,
        10.7,
        10.2,
        11.4,
        14.4,
        16.4,
        18.3,
        19.5,
        20.7,
        21.9,
        22.7,
        23.1,
        23.1,
        22.8,
        21.8,
        20.2,
        19.1,
        18,
        17.4
    ]    

strompreis_euro_pro_wh = [
        0.00031540228,
        0.00031000228,
        0.00029390228,
        0.00028410228,
        0.00028840228,
        0.00028800228,
        0.00030930228,
        0.00031390228,
        0.00031540228,
        0.00028120228,
        0.00022820228,
        0.00022310228,
        0.00021500228,
        0.00020770228,
        0.00020670228,
        0.00021200228,
        0.00021540228,
        0.00023000228,
        0.00029530228,
        0.00032990228,
        0.00036840228,
        0.00035900228,
        0.00033140228,
        0.00031370228,
        0.00031540228,
        0.00031000228,
        0.00029390228,
        0.00028410228,
        0.00028840228,
        0.00028800228,
        0.00030930228,
        0.00031390228,
        0.00031540228,
        0.00028120228,
        0.00022820228,
        0.00022310228,
        0.00021500228,
        0.00020770228,
        0.00020670228,
        0.00021200228,
        0.00021540228,
        0.00023000228,
        0.00029530228,
        0.00032990228,
        0.00036840228,
        0.00035900228,
        0.00033140228,
        0.00031370228
    ]
gesamtlast= [
        723.794862683391,
        743.491222629184,
        836.32034938972,
        870.858204290382,
        877.988917620097,
        857.94124236693,
        535.7468553632,
        658.119336334815,
        955.15298014833,
        2636.705125629,
        1321.53672393798,
        1488.77669263834,
        1129.61536474922,
        1261.47022563591,
        1308.42804416213,
        1740.76791896787,
        989.769241971553,
        1291.60060799951,
        1360.9198505883,
        1290.04968399465,
        989.968377880823,
        1121.41872787695,
        1250.64584231737,
        852.708926147066,
        723.492531379247,
        743.121389279149,
        835.959858325763,
        870.44547874543,
        878.758616187391,
        858.773385266073,
        535.600426631561,
        658.438388271842,
        955.420012089818,
        2636.68835629389,
        1321.54382666298,
        1489.13090434992,
        1129.80079639256,
        1262.0092664333,
        1308.72647023183,
        1741.92058921559,
        990.700392687782,
        1293.57876397944,
        1363.67698321638,
        1291.28280716443,
        990.277508651153,
        1121.16294287294,
        1250.20143586737,
        852.488808763652
    ]

start_solution= [
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        0,
        1,
        1,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        1,
        0,
        0,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1
    ]
parameter= {'pv_soc': 92.4052, 'pv_akku_cap': 30000, 'year_energy': 4100000, 'einspeiseverguetung_euro_pro_wh': 7e-05, 'max_heizleistung': 1000,"gesamtlast":gesamtlast, 'pv_forecast': pv_forecast, "temperature_forecast":temperature_forecast, "strompreis_euro_pro_wh":strompreis_euro_pro_wh, 'eauto_min_soc': 100, 'eauto_cap': 60000, 'eauto_charge_efficiency': 0.95, 'eauto_charge_power': 5500, 'eauto_soc': 77, 'pvpowernow': 211.137503624, 'start_solution': start_solution, 'haushaltsgeraet_wh': 937, 'haushaltsgeraet_dauer': 0}



opt_class = optimization_problem(prediction_hours=48, strafe=10)
ergebnis = opt_class.optimierung_ems(parameter=parameter, start_hour=start_hour)


# #Gesamtlast
# #############
# gesamtlast = Gesamtlast()


# # Load Forecast
# ###############
# lf = LoadForecast(filepath=r'load_profiles.npz', year_energy=year_energy)
# #leistung_haushalt = lf.get_daily_stats(date)[0,...]  # Datum anpassen
# leistung_haushalt = lf.get_stats_for_date_range(date_now,date)[0,...].flatten()
# # print(date_now," ",date)
# # print(leistung_haushalt.shape)
# gesamtlast.hinzufuegen("Haushalt", leistung_haushalt)

# # PV Forecast
# ###############
# #PVforecast = PVForecast(filepath=os.path.join(r'test_data', r'pvprognose.json'))
# PVforecast = PVForecast(prediction_hours = prediction_hours, url="https://api.akkudoktor.net/forecast?lat=52.52&lon=13.405&power=5000&azimuth=-10&tilt=7&powerInvertor=10000&horizont=20,27,22,20&power=4800&azimuth=-90&tilt=7&powerInvertor=10000&horizont=30,30,30,50&power=1400&azimuth=-40&tilt=60&powerInvertor=2000&horizont=60,30,0,30&power=1600&azimuth=5&tilt=45&powerInvertor=1400&horizont=45,25,30,60&past_days=5&cellCoEff=-0.36&inverterEfficiency=0.8&albedo=0.25&timezone=Europe%2FBerlin&hourly=relativehumidity_2m%2Cwindspeed_10m")
# pv_forecast = PVforecast.get_pv_forecast_for_date_range(date_now,date) #get_forecast_for_date(date)

# temperature_forecast = PVforecast.get_temperature_for_date_range(date_now,date)



# # Strompreise
# ###############
# filepath = os.path.join (r'test_data', r'strompreise_akkudokAPI.json')  # Pfad zur JSON-Datei anpassen
# #price_forecast = HourlyElectricityPriceForecast(source=filepath)
# price_forecast = HourlyElectricityPriceForecast(source="https://api.akkudoktor.net/prices?start="+date_now+"&end="+date+"")
# specific_date_prices = price_forecast.get_price_for_daterange(date_now,date)
# # print("13:",specific_date_prices[13]) 
# # print("14:",specific_date_prices[14]) 
# # print("15:",specific_date_prices[15]) 
# # sys.exit()
# # WP
# ##############
# leistung_wp = wp.simulate_24h(temperature_forecast)
# gesamtlast.hinzufuegen("Heatpump", leistung_wp)


# # EAuto
# ######################
# # leistung_eauto = eauto.get_stuendliche_last()
# # soc_eauto = eauto.get_stuendlicher_soc()
# # gesamtlast.hinzufuegen("eauto", leistung_eauto)

# # print(gesamtlast.gesamtlast_berechnen())

# # EMS / Stromzähler Bilanz
# #akku=None,  pv_prognose_wh=None, strompreis_cent_pro_wh=None, einspeiseverguetung_cent_pro_wh=None, eauto=None, gesamtlast=None

# ems = EnergieManagementSystem(akku=akku, gesamtlast = gesamtlast, pv_prognose_wh=pv_forecast, strompreis_cent_pro_wh=specific_date_prices, einspeiseverguetung_cent_pro_wh=einspeiseverguetung_cent_pro_wh, eauto=eauto)


# o = ems.simuliere(start_hour)#ems.simuliere_ab_jetzt()
# #pprint(o)
# #pprint(o["Gesamtbilanz_Euro"])

# #visualisiere_ergebnisse(gesamtlast, pv_forecast, specific_date_prices, o,discharge_array,laden_moeglich, temperature_forecast, start_hour, prediction_hours)



# # Optimierung

# def evaluate_inner(individual):
    # #print(individual)
    # discharge_hours_bin = individual[0::2]
    # eautocharge_hours_float = individual[1::2]
    
    # #print(discharge_hours_bin)
    # #print(len(eautocharge_hours_float))
    # ems.reset()
    # #eauto.reset()
    # ems.set_akku_discharge_hours(discharge_hours_bin)
    # ems.set_eauto_charge_hours(eautocharge_hours_float)
    
    # #eauto.set_laden_moeglich(eautocharge_hours_float)
    # #eauto.berechne_ladevorgang()
    # #leistung_eauto = eauto.get_stuendliche_last()
    # #gesamtlast.hinzufuegen("eauto", leistung_eauto)
    
    # #ems.set_gesamtlast(gesamtlast.gesamtlast_berechnen())
    
    # o = ems.simuliere(start_hour)
    # return o, eauto

# # Fitness-Funktion (muss Ihre EnergieManagementSystem-Logik integrieren)
# def evaluate(individual):
    # o,eauto = evaluate_inner(individual)
    # gesamtbilanz = o["Gesamtbilanz_Euro"]
    
    # # Überprüfung, ob der Mindest-SoC erreicht wird
    # final_soc = eauto.ladezustand_in_prozent()  # Nimmt den SoC am Ende des Optimierungszeitraums
    # strafe = 0.0
    # #if final_soc < min_soc_eauto:
    # # Fügt eine Strafe hinzu, wenn der Mindest-SoC nicht erreicht wird
    # strafe = max(0,(min_soc_eauto - final_soc) * hohe_strafe ) # `hohe_strafe` ist ein vorher festgelegter Strafwert
    # gesamtbilanz += strafe    
    # gesamtbilanz += o["Gesamt_Verluste"]/1000.0
    # return (gesamtbilanz,)





# # Werkzeug-Setup
# creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
# creator.create("Individual", list, fitness=creator.FitnessMin)

# toolbox = base.Toolbox()

# toolbox.register("attr_bool", random.randint, 0, 1)
# toolbox.register("attr_bool", random.randint, 0, 1)
# toolbox.register("individual", tools.initCycle, creator.Individual, (toolbox.attr_bool,toolbox.attr_bool), n=prediction_hours)



# toolbox.register("population", tools.initRepeat, list, toolbox.individual)

# toolbox.register("evaluate", evaluate)
# toolbox.register("mate", tools.cxTwoPoint)
# toolbox.register("mutate", tools.mutFlipBit, indpb=0.05)
# toolbox.register("select", tools.selTournament, tournsize=3)

# # Genetischer Algorithmus
# def optimize():
    # population = toolbox.population(n=500)
    # hof = tools.HallOfFame(1)
    
    # stats = tools.Statistics(lambda ind: ind.fitness.values)
    # stats.register("avg", np.mean)
    # stats.register("min", np.min)
    # stats.register("max", np.max)
    
    # algorithms.eaMuPlusLambda(population, toolbox, 50, 100, cxpb=0.5, mutpb=0.5, ngen=500,             stats=stats, halloffame=hof, verbose=True)
    # #algorithms.eaSimple(population, toolbox, cxpb=0.2, mutpb=0.2, ngen=1000,             stats=stats, halloffame=hof, verbose=True)
    # return hof[0]
    
    
# start_solution = optimize()
    
    
# print("Start Lösung:", start_solution)




# # # Werkzeug-Setup
# # creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
# # creator.create("Individual", list, fitness=creator.FitnessMin)

# # toolbox = base.Toolbox()




# # toolbox.register("attr_bool", random.randint, 0, 1)
# # toolbox.register("attr_float", random.uniform, 0.0, 1.0)
# # toolbox.register("individual", tools.initCycle, creator.Individual, (toolbox.attr_bool,toolbox.attr_float), n=prediction_hours)

# # start_individual = toolbox.individual()
# # start_individual[:] = start_solution

# # toolbox.register("population", tools.initRepeat, list, toolbox.individual)

# # toolbox.register("evaluate", evaluate)
# # toolbox.register("mate", tools.cxTwoPoint)
# # toolbox.register("mutate", tools.mutFlipBit, indpb=0.05)
# # toolbox.register("select", tools.selTournament, tournsize=3)

# # # Genetischer Algorithmus
# # def optimize():
    # # population = toolbox.population(n=1000)
    # # population[0] = start_individual
    # # hof = tools.HallOfFame(1)
    
    # # stats = tools.Statistics(lambda ind: ind.fitness.values)
    # # stats.register("avg", np.mean)
    # # stats.register("min", np.min)
    # # stats.register("max", np.max)
    
    # # algorithms.eaMuPlusLambda(population, toolbox, 100, 200, cxpb=0.5, mutpb=0.2, ngen=1000,   stats=stats, halloffame=hof, verbose=True)
    # # #algorithms.eaSimple(population, toolbox, cxpb=0.2, mutpb=0.2, ngen=1000,             stats=stats, halloffame=hof, verbose=True)
    # # return hof[0]

# # best_solution = optimize()
# best_solution = start_solution
# print("Beste Lösung:", best_solution)

# #ems.set_akku_discharge_hours(best_solution)
# o,eauto = evaluate_inner(best_solution)

# # soc_eauto = eauto.get_stuendlicher_soc()
# # print(soc_eauto)
# # pprint(o)
# # pprint(eauto.get_stuendlicher_soc())


# #visualisiere_ergebnisse(gesamtlast,leistung_haushalt,leistung_wp, pv_forecast, specific_date_prices, o,soc_eauto,best_solution[0::2],best_solution[1::2] , temperature_forecast)
# visualisiere_ergebnisse(gesamtlast, pv_forecast, specific_date_prices, o,best_solution[0::2],best_solution[1::2] , temperature_forecast, start_hour, prediction_hours)


# # for data in forecast.get_forecast_data():
    # # print(data.get_date_time(), data.get_dc_power(), data.get_ac_power(), data.get_windspeed_10m(), data.get_temperature())for data in forecast.get_forecast_data():




# # app = Flask(__name__)



# # @app.route('/getdata', methods=['GET'])
# # def get_data():
    # # # Hole das Datum aus den Query-Parametern
    # # date_str = request.args.get('date')
    # # year_energy = request.args.get('year_energy')
    
    # # try:
        # # # Konvertiere das Datum in ein datetime-Objekt
        # # date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        # # filepath = r'.\load_profiles.npz'  # Pfad zur JSON-Datei anpassen
        # # lf = cl.LoadForecast(filepath=filepath, year_energy=float(year_energy))
        # # specific_date_prices = lf.get_daily_stats('2024-02-16')

        
        # # # Berechne den Tag des Jahres
        # # #day_of_year = date_obj.timetuple().tm_yday
        
        # # # Konvertiere den Tag des Jahres in einen String, falls die Schlüssel als Strings gespeichert sind
        # # #day_key = int(day_of_year)
        # # #print(day_key)
        # # # Überprüfe, ob der Tag im Jahr in den Daten vorhanden ist
        # # array_list = lf.get_daily_stats(date_str)
        # # pprint(array_list)
        # # pprint(array_list.shape)
        # # if array_list.shape == (2,24):
        # # #if day_key < len(load_profiles_exp):
            # # # Konvertiere das Array in eine Liste für die JSON-Antwort
             # # #((load_profiles_exp_l[day_key]).tolist(),(load_profiles_std_l)[day_key].tolist())
            
            # # return jsonify({date_str: array_list.tolist()})
        # # else:
            # # return jsonify({"error": "Datum nicht gefunden"}), 404
    # # except ValueError:
        # # # Wenn das Datum nicht im richtigen Format ist oder ungültig ist
        # # return jsonify({"error": "Ungültiges Datum"}), 400

# # if __name__ == '__main__':
    # # app.run(debug=True)

