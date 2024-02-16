from flask import Flask, jsonify, request
import numpy as np
from datetime import datetime

app = Flask(__name__)

# Lade die .npz-Datei beim Start der Anwendung
data = np.load('load_profiles.npz')
load_profiles_exp = data["yearly_profiles"] #.flatten().tolist()
load_profiles_std = data["yearly_profiles_std"] #.flatten().tolist()

print(load_profiles_exp)
print(load_profiles_exp.shape)
#load_profiles_exp = load_profiles_exp*1000.0
print(load_profiles_exp)
print(load_profiles_exp.sum())

@app.route('/getdata', methods=['GET'])
def get_data():
    # Hole das Datum aus den Query-Parametern
    date_str = request.args.get('date')
    year_energy = request.args.get('year_energy')
    
    try:
        # Konvertiere das Datum in ein datetime-Objekt
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        
        year_energy = float(year_energy)
        
        load_profiles_exp_l = load_profiles_exp*year_energy
        load_profiles_std_l = load_profiles_std*year_energy
        
        # Berechne den Tag des Jahres
        day_of_year = date_obj.timetuple().tm_yday
        
        # Konvertiere den Tag des Jahres in einen String, falls die Schlüssel als Strings gespeichert sind
        day_key = int(day_of_year)
        #print(day_key)
        # Überprüfe, ob der Tag im Jahr in den Daten vorhanden ist
        if day_key < len(load_profiles_exp):
            # Konvertiere das Array in eine Liste für die JSON-Antwort
            
            array_list = ((load_profiles_exp_l[day_key]).tolist(),(load_profiles_std_l)[day_key].tolist())
            
            return jsonify({date_str: array_list})
        else:
            return jsonify({"error": "Datum nicht gefunden"}), 404
    except ValueError:
        # Wenn das Datum nicht im richtigen Format ist oder ungültig ist
        return jsonify({"error": "Ungültiges Datum"}), 400

if __name__ == '__main__':
    app.run(debug=True)

