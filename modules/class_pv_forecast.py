from flask import Flask, jsonify, request
import numpy as np
from datetime import datetime
from pprint import pprint
import json, sys

class PVForecast:
    class ForecastData:
        def __init__(self, date_time, dc_power, ac_power, windspeed_10m, temperature):
            self.date_time = date_time
            self.dc_power = dc_power
            self.ac_power = ac_power
            self.windspeed_10m = windspeed_10m
            self.temperature = temperature

        # Getter für die ForecastData-Attribute
        def get_date_time(self):
            return self.date_time

        def get_dc_power(self):
            return self.dc_power

        def get_ac_power(self):
            return self.ac_power

        def get_windspeed_10m(self):
            return self.windspeed_10m

        def get_temperature(self):
            return self.temperature

    def __init__(self, filepath):
        self.filepath = filepath
        self.meta = {}
        self.forecast_data = []
        self.load_data()

    def load_data(self):
        with open(self.filepath, 'r') as file:
            data = json.load(file)
            self.meta = data.get('meta', {})
            values = data.get('values', [])[0]
            for value in values:
                # Erstelle eine ForecastData-Instanz für jeden Wert in der Liste
                forecast = self.ForecastData(
                    date_time=value.get('datetime'),
                    dc_power=value.get('dcPower'),
                    ac_power=value.get('power'),
                    windspeed_10m=value.get('windspeed_10m'),
                    temperature=value.get('temperature')
                )
                self.forecast_data.append(forecast)

    def get_forecast_data(self):
        return self.forecast_data

    
    def get_forecast_for_date(self, input_date_str):
        input_date = datetime.strptime(input_date_str, "%Y-%m-%d")
        daily_forecast_obj = [data for data in self.forecast_data if datetime.strptime(data.get_date_time(), "%Y-%m-%dT%H:%M:%S.%f%z").date() == input_date.date()]
        daily_forecast = []
        for d in daily_forecast_obj:
            daily_forecast.append(d.get_ac_power())
        
        return np.array(daily_forecast)





# Beispiel für die Verwendung der Klasse
if __name__ == '__main__':
    forecast = PVForecast(r'..\test_data\pvprognose.json')
    for data in forecast.get_forecast_data():
        print(data.get_date_time(), data.get_dc_power(), data.get_ac_power(), data.get_windspeed_10m(), data.get_temperature())
