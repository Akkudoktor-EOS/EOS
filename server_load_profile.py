from flask import Flask, jsonify, request
import numpy as np
from datetime import datetime
import modules.class_load as cl
from pprint import pprint

app = Flask(__name__)



@app.route('/getdata', methods=['GET'])
def get_data():
    # Hole das Datum aus den Query-Parametern
    date_str = request.args.get('date')
    year_energy = request.args.get('year_energy')
    
    try:
        # Konvertiere das Datum in ein datetime-Objekt
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        filepath = r'.\load_profiles.npz'  # Pfad zur JSON-Datei anpassen
        lf = cl.LoadForecast(filepath=filepath, year_energy=float(year_energy))
        specific_date_prices = lf.get_daily_stats('2024-02-16')

        
        # Berechne den Tag des Jahres
        #day_of_year = date_obj.timetuple().tm_yday
        
        # Konvertiere den Tag des Jahres in einen String, falls die Schlüssel als Strings gespeichert sind
        #day_key = int(day_of_year)
        #print(day_key)
        # Überprüfe, ob der Tag im Jahr in den Daten vorhanden ist
        array_list = lf.get_daily_stats(date_str)
        pprint(array_list)
        pprint(array_list.shape)
        if array_list.shape == (2,24):
        #if day_key < len(load_profiles_exp):
            # Konvertiere das Array in eine Liste für die JSON-Antwort
             #((load_profiles_exp_l[day_key]).tolist(),(load_profiles_std_l)[day_key].tolist())
            
            return jsonify({date_str: array_list.tolist()})
        else:
            return jsonify({"error": "Datum nicht gefunden"}), 404
    except ValueError:
        # Wenn das Datum nicht im richtigen Format ist oder ungültig ist
        return jsonify({"error": "Ungültiges Datum"}), 400

if __name__ == '__main__':
    app.run(debug=True)

