from flask import Flask, jsonify, request
import numpy as np
from datetime import datetime
from  modules.class_load import *
from  modules.class_ems import *
from  modules.class_pv_forecast import *
from modules.class_akku import *
from modules.class_strompreis import *
from pprint import pprint
import matplotlib.pyplot as plt
from modules.visualize import *




date = "2024-02-16"
akku_size = 1000 # Wh
year_energy = 2000*1000 #Wh
einspeiseverguetung_cent_pro_wh = np.full(24, 7/1000.0)

akku = PVAkku(akku_size)

# Load Forecast
lf = LoadForecast(filepath=r'load_profiles.npz', year_energy=year_energy)
specific_date_load = lf.get_daily_stats(date)[0,...]  # Datum anpassen

pprint(specific_date_load.shape)

# PV Forecast
PVforecast = PVForecast(r'.\test_data\pvprognose.json')
pv_forecast = PVforecast.get_forecast_for_date(date)
pprint(pv_forecast.shape)

# Strompreise
filepath = r'.\test_data\strompreis.json'  # Pfad zur JSON-Datei anpassen
price_forecast = HourlyElectricityPriceForecast(filepath)
specific_date_prices = price_forecast.get_prices_for_date(date) 


# EMS / Stromzähler Bilanz
ems = EnergieManagementSystem(akku, specific_date_load, pv_forecast, specific_date_prices, einspeiseverguetung_cent_pro_wh)
o = ems.simuliere()
pprint(o)


visualisiere_ergebnisse(specific_date_load, pv_forecast, specific_date_prices, o)


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

