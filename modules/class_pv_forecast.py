from flask import Flask, jsonify, request
import numpy as np
import json
import os
import requests
import hashlib
from datetime import datetime
from dateutil import parser
import pandas as pd

class ForecastData:
    def __init__(self, date_time, dc_power, ac_power, windspeed_10m=None, temperature=None, ac_power_measurement=None):
        self.date_time = date_time
        self.dc_power = dc_power
        self.ac_power = ac_power
        self.windspeed_10m = windspeed_10m
        self.temperature = temperature
        self.ac_power_measurement = ac_power_measurement

    def get_date_time(self):
        return self.date_time

    def get_dc_power(self):
        return self.dc_power

    def ac_power_measurement(self): #should be get_!
        return self.ac_power_measurement

    def get_ac_power(self):
        """Return measured AC power if available; otherwise, forecasted AC power."""
        return self.ac_power_measurement if self.ac_power_measurement is not None else self.ac_power

    def get_windspeed_10m(self):
        return self.windspeed_10m

    def get_temperature(self):
        return self.temperature

class PVForecast:
    def __init__(self, filepath=None, url=None, cache_dir='cache', prediction_hours=48):
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

        if len(self.forecast_data) < self.prediction_hours:
            raise ValueError(f"The forecast must cover at least {self.prediction_hours} hours, but only {len(self.forecast_data)} hours are available.")

    def update_ac_power_measurement(self, date_time=None, ac_power_measurement=None):
        """Update the measured AC power for the corresponding forecast hour."""
        found = False
        input_date_hour = date_time.replace(minute=0, second=0, microsecond=0)

        for forecast in self.forecast_data:
            forecast_date_hour = parser.parse(forecast.date_time).replace(minute=0, second=0, microsecond=0)
            if forecast_date_hour == input_date_hour:
                forecast.ac_power_measurement = ac_power_measurement
                found = True
                break

        if not found:
            print(f"No forecast entry found for {input_date_hour}")

    def process_data(self, data):
        """Process the raw forecast data and store it in the forecast_data list."""
        self.meta = data.get('meta', {})
        all_values = data.get('values', [])
        
        for i in range(len(all_values[0])):  # Assume all lists have the same length
            sum_dc_power = sum(values[i]['dcPower'] for values in all_values)
            sum_ac_power = sum(values[i]['power'] for values in all_values)

            original_datetime = all_values[0][i].get('datetime')
            dt = datetime.strptime(original_datetime, "%Y-%m-%dT%H:%M:%S.%f%z").replace(tzinfo=None)

            forecast = ForecastData(
                date_time=dt,
                dc_power=sum_dc_power,
                ac_power=sum_ac_power,
                windspeed_10m=all_values[0][i].get('windspeed_10m'),
                temperature=all_values[0][i].get('temperature')
            )

            self.forecast_data.append(forecast)

    def load_data_from_file(self, filepath):
        """Load forecast data from a local file."""
        with open(filepath, 'r') as file:
            data = json.load(file)
            self.process_data(data)

    def load_data_from_url(self, url):
        """Load forecast data from a URL."""
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            pprint(data)
            self.process_data(data)
        else:
            print(f"Failed to load data from {url}. Status Code: {response.status_code}")
            self.load_data_from_url(url)

    def load_data_with_caching(self, url):
        """Load forecast data with caching to avoid redundant requests."""
        date = datetime.now().strftime("%Y-%m-%d")
        cache_file = os.path.join(self.cache_dir, self.generate_cache_filename(url, date))

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

    def generate_cache_filename(self, url, date):
        """Generate a unique filename for caching based on the URL and date."""
        cache_key = hashlib.sha256(f"{url}{date}".encode('utf-8')).hexdigest()
        return f"cache_{cache_key}.json"

    def get_forecast_data(self):
        """Return the raw forecast data."""
        return self.forecast_data

    def get_temperature_forecast_for_date(self, input_date_str):
        """Return the temperature forecast for a specific date."""
        input_date = datetime.strptime(input_date_str, "%Y-%m-%d")
        return np.array([data.get_temperature() for data in self.forecast_data if parser.parse(data.get_date_time()).date() == input_date.date()])

    def get_pv_forecast_for_date_range(self, start_date_str, end_date_str):
        """Get AC power forecast for a specific date range."""
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()

        date_range_forecast = [data for data in self.forecast_data if start_date <= data.get_date_time().date() <= end_date]
        
        return np.array([data.get_ac_power() for data in date_range_forecast])[:self.prediction_hours]

    def get_temperature_for_date_range(self, start_date_str, end_date_str):
        """Get the temperature forecast for a specific date range."""
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()

        date_range_forecast = [data for data in self.forecast_data if start_date <= data.get_date_time().date() <= end_date]
        
        return np.array([data.get_temperature() for data in date_range_forecast])[:self.prediction_hours]

    def get_forecast_dataframe(self):
        """Convert forecast data into a Pandas DataFrame."""
        data = [{
            'date_time': f.get_date_time(),
            'dc_power': f.get_dc_power(),
            'ac_power': f.get_ac_power(),
            'windspeed_10m': f.get_windspeed_10m(),
            'temperature': f.get_temperature()
        } for f in self.forecast_data]

        return pd.DataFrame(data)

    def print_ac_power_and_measurement(self):
        """Print DC power and AC power measurements for each forecasted hour."""
        for forecast in self.forecast_data:
            print(f"Time: {forecast.date_time}, DC: {forecast.dc_power}, AC: {forecast.ac_power}, Measurement: {forecast.ac_power_measurement}, AC (Get): {forecast.get_ac_power()}")

# Example usage
if __name__ == '__main__':
    forecast = PVForecast(
        prediction_hours=24, 
        url="https://api.akkudoktor.net/forecast?lat=50.8588&lon=7.3747&power=5000&azimuth=-10&tilt=7&powerInvertor=10000&horizont=20,27,22,20&power=4800&azimuth=-90&tilt=7&powerInvertor=10000&horizont=30,30,30,50&power=1400&azimuth=-40&tilt=60&powerInvertor=2000&horizont=60,30,0,30&power=1600&azimuth=5&tilt=45&powerInvertor=1400&horizont=45,25,30,60&past_days=5&cellCoEff=-0.36&inverterEfficiency=0.8&albedo=0.25&timezone=Europe%2FBerlin&hourly=relativehumidity_2m%2Cwindspeed_10m"
    )
    forecast.update_ac_power_measurement(date_time=datetime.now(), ac_power_measurement=1000)
    forecast.print_ac_power_and_measurement()
