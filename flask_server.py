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

app = Flask(__name__)


opt_class = optimization_problem(prediction_hours=48, strafe=10)
soc_predictor = BatterySocPredictor.load_model('battery_model.pkl')


@app.route('/soc', methods=['GET'])
def flask_soc():
    if request.method == 'GET':
        # URL-Parameter lesen
        voltage = request.args.get('voltage')
        current = request.args.get('current')
        
        # Erforderliche Parameter prüfen
        if voltage is None or current is None:
            missing_params = []
            if voltage is None:
                missing_params.append('voltage')
            if current is None:
                missing_params.append('current')
            return jsonify({"error": f"Fehlende Parameter: {', '.join(missing_params)}"}), 400

        # Werte in ein numpy Array umwandeln
        x = np.array( [[float(voltage), float(current)]] )
        
        # Simulation durchführen
        ergebnis = soc_predictor.predict(x)
        print(ergebnis)
        
        return jsonify(ergebnis)


@app.route('/optimize', methods=['POST'])
def flask_optimize():
    if request.method == 'POST':
        parameter = request.json
        
        # Erforderliche Parameter prüfen
        erforderliche_parameter = [ 'pv_akku_cap', 'year_energy',"einspeiseverguetung_euro_pro_wh", 'max_heizleistung', 'pv_forecast_url', 'eauto_min_soc', "eauto_cap","eauto_charge_efficiency","eauto_charge_power","eauto_soc","pv_soc","start_solution","pvpowernow","haushaltsgeraet_dauer","haushaltsgeraet_wh"]
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


