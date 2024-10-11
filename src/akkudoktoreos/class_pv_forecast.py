"""PV Power Forecasting Module.

This module contains classes and methods to retrieve, process, and display photovoltaic (PV)
power forecast data, including temperature, windspeed, DC power, and AC power forecasts.
The module supports caching of forecast data to reduce redundant network requests and includes
functions to update AC power measurements and retrieve forecasts within a specified date range.

Classes:
    ForecastData: Represents a single forecast entry, including DC power, AC power,
                  temperature, and windspeed.
    PVForecast:   Retrieves, processes, and stores PV power forecast data, either from
                  a file or URL, with optional caching. It also provides methods to query
                  and update the forecast data, convert it to a DataFrame, and output key
                  metrics like AC power.

Example usage:
    # Initialize PVForecast class with a URL
    forecast = PVForecast(
        prediction_hours=24,
        url="https://api.akkudoktor.net/forecast?lat=50.8588&lon=7.3747..."
    )

    # Update the AC power measurement for a specific date and time
    forecast.update_ac_power_measurement(date_time=datetime.now(), ac_power_measurement=1000)

    # Print the forecast data with DC and AC power details
    forecast.print_ac_power_and_measurement()

    # Get the forecast data as a Pandas DataFrame
    df = forecast.get_forecast_dataframe()
    print(df)

Attributes:
    cache_dir (str): The directory where cached data is stored. Defaults to 'cache'.
    prediction_hours (int): Number of forecast hours. Defaults to 48.
"""

import hashlib
import json
import os
from datetime import datetime
from pprint import pprint

import numpy as np
import pandas as pd
import requests
from dateutil import parser
from pydantic import BaseModel


class ForecastResponse(BaseModel):
    temperature: list[float]
    pvpower: list[float]


class ForecastData:
    """Stores forecast data for PV power and weather parameters.

    Attributes:
        date_time (datetime): The date and time of the forecast.
        dc_power (float): The direct current (DC) power in watts.
        ac_power (float): The alternating current (AC) power in watts.
        windspeed_10m (float, optional): Wind speed at 10 meters altitude.
        temperature (float, optional): Temperature in degrees Celsius.
        ac_power_measurement (float, optional): Measured AC power.
    """

    def __init__(
        self,
        date_time,
        dc_power,
        ac_power,
        windspeed_10m=None,
        temperature=None,
        ac_power_measurement=None,
    ):
        """Initializes the ForecastData instance.

        Args:
            date_time (datetime): The date and time of the forecast.
            dc_power (float): The DC power in watts.
            ac_power (float): The AC power in watts.
            windspeed_10m (float, optional): Wind speed at 10 meters altitude. Defaults to None.
            temperature (float, optional): Temperature in degrees Celsius. Defaults to None.
            ac_power_measurement (float, optional): Measured AC power. Defaults to None.
        """
        self.date_time = date_time
        self.dc_power = dc_power
        self.ac_power = ac_power
        self.windspeed_10m = windspeed_10m
        self.temperature = temperature
        self.ac_power_measurement = ac_power_measurement

    def get_date_time(self):
        """Returns the forecast date and time.

        Returns:
            datetime: The date and time of the forecast.
        """
        return self.date_time

    def get_dc_power(self):
        """Returns the DC power.

        Returns:
            float: DC power in watts.
        """
        return self.dc_power

    def ac_power_measurement(self):
        """Returns the measured AC power.

        It returns the measured AC power if available; otherwise None.

        Returns:
            float: Measured AC power in watts or None
        """
        return self.ac_power_measurement

    def get_ac_power(self):
        """Returns the AC power.

        If a measured value is available, it returns the measured AC power;
        otherwise, it returns the forecasted AC power.

        Returns:
            float: AC power in watts.
        """
        if self.ac_power_measurement is not None:
            return self.ac_power_measurement
        else:
            return self.ac_power

    def get_windspeed_10m(self):
        """Returns the wind speed at 10 meters altitude.

        Returns:
            float: Wind speed in meters per second.
        """
        return self.windspeed_10m

    def get_temperature(self):
        """Returns the temperature.

        Returns:
            float: Temperature in degrees Celsius.
        """
        return self.temperature


class PVForecast:
    """Manages PV power forecasts and weather data.

    Attributes:
        meta (dict): Metadata of the forecast.
        forecast_data (list): List of ForecastData objects.
        cache_dir (str): Directory for cached data.
        prediction_hours (int): Number of hours for which the forecast is made.
        current_measurement (float): Current AC power measurement.
    """

    def __init__(self, filepath=None, url=None, cache_dir="cache", prediction_hours=48):
        """Initializes the PVForecast instance.

        Loads data either from a file or from a URL.

        Args:
            filepath (str, optional): Path to the JSON file with forecast data. Defaults to None.
            url (str, optional): URL to the API providing forecast data. Defaults to None.
            cache_dir (str, optional): Directory for cache data. Defaults to "cache".
            prediction_hours (int, optional): Number of hours to forecast. Defaults to 48.

        Raises:
            ValueError: If the forecasted data is less than `prediction_hours`.
        """
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
            raise ValueError(
                f"The forecast must cover at least {self.prediction_hours} hours, "
                f"but only {len(self.forecast_data)} hours were predicted."
            )

    def update_ac_power_measurement(self, date_time=None, ac_power_measurement=None) -> bool:
        """Updates the AC power measurement for a specific time.

        Args:
            date_time (datetime): The date and time of the measurement.
            ac_power_measurement (float): Measured AC power.

        Returns:
            bool: True if a matching timestamp was found, False otherwise.
        """
        found = False
        input_date_hour = date_time.replace(minute=0, second=0, microsecond=0)

        for forecast in self.forecast_data:
            forecast_date_hour = parser.parse(forecast.date_time).replace(
                minute=0, second=0, microsecond=0
            )
            if forecast_date_hour == input_date_hour:
                forecast.ac_power_measurement = ac_power_measurement
                found = True
                break
        return found

    def process_data(self, data):
        """Processes JSON data and stores the forecasts.

        Args:
            data (dict): JSON data containing forecast values.
        """
        self.meta = data.get("meta", {})
        all_values = data.get("values", [])

        for i in range(len(all_values[0])):  # Annahme, dass alle Listen gleich lang sind
            sum_dc_power = sum(values[i]["dcPower"] for values in all_values)
            sum_ac_power = sum(values[i]["power"] for values in all_values)

            # Zeige die ursprünglichen und berechneten Zeitstempel an
            original_datetime = all_values[0][i].get("datetime")
            # print(original_datetime," ",sum_dc_power," ",all_values[0][i]['dcPower'])
            dt = datetime.strptime(original_datetime, "%Y-%m-%dT%H:%M:%S.%f%z")
            dt = dt.replace(tzinfo=None)
            # iso_datetime = parser.parse(original_datetime).isoformat()  # Konvertiere zu ISO-Format
            # print()
            # Optional: 2 Stunden abziehen, um die Zeitanpassung zu testen
            # adjusted_datetime = parser.parse(original_datetime) - timedelta(hours=2)
            # print(f"Angepasste Zeitstempel: {adjusted_datetime.isoformat()}")

            forecast = ForecastData(
                date_time=dt,  # Verwende angepassten Zeitstempel
                dc_power=sum_dc_power,
                ac_power=sum_ac_power,
                windspeed_10m=all_values[0][i].get("windspeed_10m"),
                temperature=all_values[0][i].get("temperature"),
            )

            self.forecast_data.append(forecast)

    def load_data_from_file(self, filepath):
        """Loads forecast data from a file.

        Args:
            filepath (str): Path to the file containing the forecast data.
        """
        with open(filepath, "r") as file:
            data = json.load(file)
            self.process_data(data)

    def load_data_from_url(self, url):
        """Loads forecast data from a URL.

        Args:
            url (str): URL of the API providing forecast data.
        """
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            pprint(data)
            self.process_data(data)
        else:
            print(f"Failed to load data from {url}. Status Code: {response.status_code}")
            self.load_data_from_url(url)

    def load_data_with_caching(self, url):
        """Loads data from a URL or from the cache if available.

        Args:
            url (str): URL of the API providing forecast data.
        """
        date = datetime.now().strftime("%Y-%m-%d")

        cache_file = os.path.join(self.cache_dir, self.generate_cache_filename(url, date))
        if os.path.exists(cache_file):
            with open(cache_file, "r") as file:
                data = json.load(file)
                print("Loading data from cache.")
        else:
            response = requests.get(url)
            if response.status_code == 200:
                data = response.json()
                with open(cache_file, "w") as file:
                    json.dump(data, file)
                print("Data fetched from URL and cached.")
            else:
                print(f"Failed to load data from {url}. Status Code: {response.status_code}")
                return
        self.process_data(data)

    def generate_cache_filename(self, url, date):
        """Generates a cache filename based on the URL and date.

        Args:
            url (str): URL of the API.
            date (str): Date in the format YYYY-MM-DD.

        Returns:
            str: Generated cache filename.
        """
        cache_key = hashlib.sha256(f"{url}{date}".encode("utf-8")).hexdigest()
        return f"cache_{cache_key}.json"

    def get_forecast_data(self):
        """Returns the forecast data.

        Returns:
            list: List of ForecastData objects.
        """
        return self.forecast_data

    def get_temperature_forecast_for_date(self, input_date_str):
        """Returns the temperature forecast for a specific date.

        Args:
            input_date_str (str): Date in the format YYYY-MM-DD.

        Returns:
            np.array: Array of temperature forecasts.
        """
        input_date = datetime.strptime(input_date_str, "%Y-%m-%d")
        daily_forecast_obj = [
            data
            for data in self.forecast_data
            if parser.parse(data.get_date_time()).date() == input_date.date()
        ]
        daily_forecast = []
        for d in daily_forecast_obj:
            daily_forecast.append(d.get_temperature())

        return np.array(daily_forecast)

    def get_pv_forecast_for_date_range(self, start_date_str, end_date_str):
        """Returns the PV forecast for a date range.

        Args:
            start_date_str (str): Start date in the format YYYY-MM-DD.
            end_date_str (str): End date in the format YYYY-MM-DD.

        Returns:
            pd.DataFrame: DataFrame containing the forecast data.
        """
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        date_range_forecast = []

        for data in self.forecast_data:
            data_date = data.get_date_time().date()  # parser.parse(data.get_date_time()).date()
            if start_date <= data_date <= end_date:
                date_range_forecast.append(data)
                print(data.get_date_time(), " ", data.get_ac_power())

        ac_power_forecast = np.array([data.get_ac_power() for data in date_range_forecast])

        return np.array(ac_power_forecast)[: self.prediction_hours]

    def get_temperature_for_date_range(self, start_date_str, end_date_str):
        """Returns the temperature forecast for a given date range.

        Args:
            start_date_str (str): Start date in the format YYYY-MM-DD.
            end_date_str (str): End date in the format YYYY-MM-DD.

        Returns:
            np.array: Array containing temperature forecasts for each hour within the date range.
        """
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        date_range_forecast = []

        for data in self.forecast_data:
            data_date = data.get_date_time().date()
            if start_date <= data_date <= end_date:
                date_range_forecast.append(data)

        temperature_forecast = [data.get_temperature() for data in date_range_forecast]
        return np.array(temperature_forecast)[: self.prediction_hours]

    def get_forecast_dataframe(self):
        """Converts the forecast data into a Pandas DataFrame.

        Returns:
            pd.DataFrame: A DataFrame containing the forecast data with columns for date/time,
                          DC power, AC power, windspeed, and temperature.
        """
        data = [
            {
                "date_time": f.get_date_time(),
                "dc_power": f.get_dc_power(),
                "ac_power": f.get_ac_power(),
                "windspeed_10m": f.get_windspeed_10m(),
                "temperature": f.get_temperature(),
            }
            for f in self.forecast_data
        ]

        # Erstelle ein DataFrame
        df = pd.DataFrame(data)
        return df

    def print_ac_power_and_measurement(self):
        """Prints the DC power, AC power, and AC power measurement for each forecast hour.

        For each forecast entry, it prints the time, DC power, forecasted AC power,
        measured AC power (if available), and the value returned by the `get_ac_power` method.
        """
        for forecast in self.forecast_data:
            date_time = forecast.date_time
            print(
                f"Zeit: {date_time}, DC: {forecast.dc_power}, AC: {forecast.ac_power}, "
                "Messwert: {forecast.ac_power_measurement}, AC GET: {forecast.get_ac_power()}"
            )


# Example of how to use the PVForecast class
if __name__ == "__main__":
    """Main execution block to demonstrate the use of the PVForecast class.

    Fetches PV power forecast data from a given URL, updates the AC power measurement
    for the current date/time, and prints the DC and AC power information.
    """
    forecast = PVForecast(
        prediction_hours=24,
        url="https://api.akkudoktor.net/forecast?lat=50.8588&lon=7.3747&"
        "power=5000&azimuth=-10&tilt=7&powerInvertor=10000&horizont=20,27,22,20&"
        "power=4800&azimuth=-90&tilt=7&powerInvertor=10000&horizont=30,30,30,50&"
        "power=1400&azimuth=-40&tilt=60&powerInvertor=2000&horizont=60,30,0,30&"
        "power=1600&azimuth=5&tilt=45&powerInvertor=1400&horizont=45,25,30,60&"
        "past_days=5&cellCoEff=-0.36&inverterEfficiency=0.8&albedo=0.25&timezone=Europe%2FBerlin&"
        "hourly=relativehumidity_2m%2Cwindspeed_10m",
    )
    forecast.update_ac_power_measurement(date_time=datetime.now(), ac_power_measurement=1000)
    forecast.print_ac_power_and_measurement()
