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
from modules.class_soc_calc import *
from modules.visualize import *
from modules.class_battery_soc_predictor import *
import os
from flask import Flask, send_from_directory
from pprint import pprint
import matplotlib
matplotlib.use('Agg')  # Setzt das Backend auf Agg
import matplotlib.pyplot as plt
import string
from datetime import datetime
from deap import base, creator, tools, algorithms
from modules.class_optimize import *
import numpy as np
import random
import os
from config import *

app = Flask(__name__)

opt_class = optimization_problem(prediction_hours=48, strafe=10)




@app.route('/soc', methods=['GET'])
def flask_soc():

    # MariaDB Verbindungsdetails
    config = db_config

    # Parameter festlegen
    voltage_high_threshold = 55.4  # 100% SoC
    voltage_low_threshold = 46.5  # 0% SoC
    current_low_threshold = 2  # Niedriger Strom für beide Zustände
    gap = 30  # Zeitlücke in Minuten zum  Gruppieren von Maxima/Minima
    bat_capacity = 33 * 1000 / 48 

    # Zeitpunkt X definieren
    zeitpunkt_x = (datetime.now() - timedelta(weeks=3)).strftime('%Y-%m-%d %H:%M:%S')


    # BatteryDataProcessor instanziieren und verwenden
    processor = BatteryDataProcessor(config, voltage_high_threshold, voltage_low_threshold, current_low_threshold, gap,bat_capacity)
    processor.connect_db()
    processor.fetch_data(zeitpunkt_x)
    processor.process_data()
    last_points_100_df, last_points_0_df = processor.find_soc_points()
    soc_df, integration_results = processor.calculate_resetting_soc(last_points_100_df, last_points_0_df)
    #soh_df = processor.calculate_soh(integration_results)
    processor.update_database_with_soc(soc_df)
    #processor.plot_data(last_points_100_df, last_points_0_df, soc_df)
    processor.disconnect_db()
        
    return jsonify("Done")


@app.route('/strompreis', methods=['GET'])
def flask_strompreis():
        date_now,date = get_start_enddate(prediction_hours,startdate=datetime.now().date())
        filepath = os.path.join (r'test_data', r'strompreise_akkudokAPI.json')  # Pfad zur JSON-Datei anpassen
        #price_forecast = HourlyElectricityPriceForecast(source=filepath)
        price_forecast = HourlyElectricityPriceForecast(source="https://api.akkudoktor.net/prices?start="+date_now+"&end="+date+"", prediction_hours=prediction_hours)
        specific_date_prices = price_forecast.get_price_for_daterange(date_now,date)
        #print(specific_date_prices)
        return jsonify(specific_date_prices.tolist())


@app.route('/gesamtlast', methods=['GET'])
def flask_gesamtlast():
    if request.method == 'GET':
        year_energy = float(request.args.get("year_energy"))
        date_now,date = get_start_enddate(prediction_hours,startdate=datetime.now().date())
        ###############
        # Load Forecast
        ###############
        lf = LoadForecast(filepath=r'load_profiles.npz', year_energy=year_energy)
        #leistung_haushalt = lf.get_daily_stats(date)[0,...]  # Datum anpassen
        leistung_haushalt = lf.get_stats_for_date_range(date_now,date)[0] # Nur Erwartungswert!        
        
        gesamtlast = Gesamtlast(prediction_hours=prediction_hours)        
        gesamtlast.hinzufuegen("Haushalt", leistung_haushalt)

        # ###############
        # # WP
        # ##############
        # leistung_wp = wp.simulate_24h(temperature_forecast)
        # gesamtlast.hinzufuegen("Heatpump", leistung_wp)
                
        last = gesamtlast.gesamtlast_berechnen()
        print(last)
        #print(specific_date_prices)
        return jsonify(last.tolist())


@app.route('/optimize', methods=['POST'])
def flask_optimize():
    if request.method == 'POST':
        parameter = request.json
        
        # Erforderliche Parameter prüfen
        erforderliche_parameter = [ 'strompreis_euro_pro_wh', "gesamtlast",'pv_akku_cap', "einspeiseverguetung_euro_pro_wh",  'pv_forecast_url', 'eauto_min_soc', "eauto_cap","eauto_charge_efficiency","eauto_charge_power","eauto_soc","pv_soc","start_solution","pvpowernow","haushaltsgeraet_dauer","haushaltsgeraet_wh"]
        for p in erforderliche_parameter:
            if p not in parameter:
                return jsonify({"error": f"Fehlender Parameter: {p}"}), 400

        # Simulation durchführen
        ergebnis = opt_class.optimierung_ems(parameter=parameter, start_hour=datetime.now().hour) # , startdate = datetime.now().date() - timedelta(days = 1)
        
        return jsonify(ergebnis)


@app.route('/visualisierungsergebnisse.pdf')
def get_pdf():
    return send_from_directory('', 'visualisierungsergebnisse.pdf')






if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0")


