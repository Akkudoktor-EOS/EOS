from flask import Flask, jsonify, request
import numpy as np
from datetime import datetime
from pprint import pprint
import json, sys, os
import requests, hashlib
from dateutil import parser, tz



class ForecastData:
    def __init__(self, date_time, dc_power, ac_power, windspeed_10m=None, temperature=None,ac_power_measurement=None):
        self.date_time = date_time
        self.dc_power = dc_power
        self.ac_power = ac_power
        self.windspeed_10m = windspeed_10m
        self.temperature = temperature
        self.ac_power_measurement = None
        
    # Getter für die ForecastData-Attribute
    def get_date_time(self):
        return self.date_time

    def get_dc_power(self):
        return self.dc_power

    def ac_power_measurement(self):
        return self.ac_power_measurement

    def get_ac_power(self):
        if self.ac_power_measurement != None:
            return self.ac_power_measurement
        else:
            return self.ac_power

    def get_windspeed_10m(self):
        return self.windspeed_10m

    def get_temperature(self):
        return self.temperature

class PVForecast:
    def __init__(self, filepath=None, url=None, cache_dir='cache', prediction_hours = 48):
        self.meta = {}
        self.forecast_data = []
        self.cache_dir = cache_dir
        self.prediction_hours = prediction_hours
        self.current_measurement = None
        
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)
        if filepath:
            self.load_data_from_file(filepath)
        elif url:
            self.load_data_with_caching(url)
            
        # Überprüfung nach dem Laden der Daten
        if len(self.forecast_data) < self.prediction_hours:
            raise ValueError(f"Die Vorhersage muss mindestens {self.prediction_hours} Stunden umfassen, aber es wurden nur {len(self.forecast_data)} Stunden vorhergesagt.")

    def update_ac_power_measurement(self, date_time=None, ac_power_measurement=None):
        """Aktualisiert einen DC-Leistungsmesswert oder fügt ihn hinzu."""
        found = False
        target_timezone = tz.gettz('Europe/Berlin')
        input_date_hour = date_time.astimezone(target_timezone).replace(minute=0, second=0, microsecond=0)
        
    

        for forecast in self.forecast_data:
            forecast_date_hour = datetime.strptime(forecast.date_time, "%Y-%m-%dT%H:%M:%S.%f%z").astimezone(target_timezone).replace(minute=0, second=0, microsecond=0)


            #print(forecast_date_hour," ",input_date_hour)
            if forecast_date_hour == input_date_hour:
                forecast.ac_power_measurement = ac_power_measurement
                found = True
                break
        
        # if not found:
            # # Erstelle ein neues ForecastData-Objekt, falls kein entsprechender Zeitstempel gefunden wurde
            # # Hier kannst du entscheiden, wie die anderen Werte gesetzt werden sollen, falls keine Vorhersage existiert
            # new_forecast = ForecastData(date_time, dc_power=None, ac_power=None, dc_power_measurement=dc_power_measurement)
            # self.forecast_data.append(new_forecast)
            # # Liste sortieren, um sie chronologisch zu ordnen
            # self.forecast_data.sort(key=lambda x: datetime.strptime(x.date_time, "%Y-%m-%dT%H:%M:%S.%f%z").replace(minute=0, second=0, microsecond=0))



    def process_data(self, data):
        self.meta = data.get('meta', {})
        all_values = data.get('values', [])

        # Berechnung der Summe der DC- und AC-Leistungen für jeden Zeitstempel
        for i in range(len(all_values[0])):  # Annahme, dass alle Listen gleich lang sind
            sum_dc_power = sum(values[i]['dcPower'] for values in all_values)
            sum_ac_power = sum(values[i]['power'] for values in all_values)
            
            # Erstellen eines ForecastData-Objekts mit den summierten Werten
            forecast = ForecastData(
                date_time=all_values[0][i].get('datetime'),
                dc_power=sum_dc_power,
                ac_power=sum_ac_power,
                # Optional: Weitere Werte wie Windspeed und Temperature, falls benötigt
                windspeed_10m=all_values[0][i].get('windspeed_10m'),
                temperature=all_values[0][i].get('temperature')
            )
            
            self.forecast_data.append(forecast)        


    def load_data_from_file(self, filepath):
        with open(filepath, 'r') as file:
            data = json.load(file)
            self.process_data(data)

    def load_data_from_url(self, url):
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            pprint(data)
            self.process_data(data)
        else:
            print(f"Failed to load data from {url}. Status Code: {response.status_code}")
            self.load_data_from_url(url)

    def load_data_with_caching(self, url):
        date =  datetime.now().strftime("%Y-%m-%d")

        cache_file = os.path.join(self.cache_dir, self.generate_cache_filename(url,date))
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as file:
                data = json.load(file)
                print("Loading data from cache.")
        else:
            response = requests.get(url)
            if response.status_code == 200:
                data = response.json()
                with open(cache_file, 'w') as file:
                    json.dump(data, file)
                print("Data fetched from URL and cached.")
            else:
                print(f"Failed to load data from {url}. Status Code: {response.status_code}")
                return
        self.process_data(data)

    def generate_cache_filename(self, url,date):
        # Erzeugt einen SHA-256 Hash der URL als Dateinamen
        cache_key = hashlib.sha256(f"{url}{date}".encode('utf-8')).hexdigest()
        #cache_path = os.path.join(self.cache_dir, cache_key)
        return f"cache_{cache_key}.json"

    def get_forecast_data(self):
        return self.forecast_data

    
    # def get_forecast_for_date(self, input_date_str):
        # input_date = datetime.strptime(input_date_str, "%Y-%m-%d")
        # daily_forecast_obj = [data for data in self.forecast_data if datetime.strptime(data.get_date_time(), "%Y-%m-%dT%H:%M:%S.%f%z").date() == input_date.date()]
        # daily_forecast = []
        # for d in daily_forecast_obj:
            # daily_forecast.append(d.get_ac_power())
        
        # return np.array(daily_forecast)
    
    def get_temperature_forecast_for_date(self, input_date_str):
        input_date = datetime.strptime(input_date_str, "%Y-%m-%d")
        daily_forecast_obj = [data for data in self.forecast_data if datetime.strptime(data.get_date_time(), "%Y-%m-%dT%H:%M:%S.%f%z").date() == input_date.date()]
        daily_forecast = []
        for d in daily_forecast_obj:
            daily_forecast.append(d.get_temperature())
        
        return np.array(daily_forecast)

    def get_pv_forecast_for_date_range(self, start_date_str, end_date_str):
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        date_range_forecast = []

        for data in self.forecast_data:
            data_date = datetime.strptime(data.get_date_time(), "%Y-%m-%dT%H:%M:%S.%f%z").date()
            #print(data.get_date_time())
            if start_date <= data_date <= end_date:
                date_range_forecast.append(data)
        
        ac_power_forecast = np.array([data.get_ac_power() for data in date_range_forecast])

        return np.array(ac_power_forecast)[:self.prediction_hours]
        
    def get_temperature_for_date_range(self, start_date_str, end_date_str):
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        date_range_forecast = []
        
        for data in self.forecast_data:
            data_date = datetime.strptime(data.get_date_time(), "%Y-%m-%dT%H:%M:%S.%f%z").date()
            if start_date <= data_date <= end_date:
                date_range_forecast.append(data)
                
        forecast_data = date_range_forecast
        temperature_forecast = [data.get_temperature() for data in forecast_data]
        return np.array(temperature_forecast)[:self.prediction_hours]
        

    def print_ac_power_and_measurement(self):
        """Druckt die DC-Leistung und das Messwert für jede Stunde."""
        for forecast in self.forecast_data:
            date_time = forecast.date_time
            
           
            print(f"Zeit: {date_time}, DC: {forecast.dc_power}, AC: {forecast.ac_power}, Messwert: {forecast.ac_power_measurement} AC GET: {forecast.get_ac_power()}")



# Beispiel für die Verwendung der Klasse
if __name__ == '__main__':
    date_now = datetime.now()
    forecast = PVForecast(prediction_hours = 24, url="https://api.akkudoktor.net/forecast?lat=52.52&lon=13.405&power=5000&azimuth=-10&tilt=7&powerInvertor=10000&horizont=20,27,22,20&power=4800&azimuth=-90&tilt=7&powerInvertor=10000&horizont=30,30,30,50&power=1400&azimuth=-40&tilt=60&powerInvertor=2000&horizont=60,30,0,30&power=1600&azimuth=5&tilt=45&powerInvertor=1400&horizont=45,25,30,60&past_days=5&cellCoEff=-0.36&inverterEfficiency=0.8&albedo=0.25&timezone=Europe%2FBerlin&hourly=relativehumidity_2m%2Cwindspeed_10m")
    forecast.update_ac_power_measurement(date_time=datetime.now(), ac_power_measurement=1000)
    forecast.print_ac_power_and_measurement()
