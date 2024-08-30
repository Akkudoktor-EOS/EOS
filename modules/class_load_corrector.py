import json,sys, os
from datetime import datetime, timedelta, timezone
import numpy as np
from pprint import pprint
import pandas as pd
import matplotlib.pyplot as plt
# from sklearn.model_selection import train_test_split, GridSearchCV
# from sklearn.ensemble import GradientBoostingRegressor
# from xgboost import XGBRegressor
# from statsmodels.tsa.statespace.sarimax import SARIMAX
# from tensorflow.keras.models import Sequential
# from tensorflow.keras.layers import Dense, LSTM
# from tensorflow.keras.optimizers import Adam
# from sklearn.preprocessing import MinMaxScaler
# from sklearn.metrics import mean_squared_error, r2_score
import mariadb
# from sqlalchemy import create_engine
import numpy as np
import matplotlib.pyplot as plt
from sklearn.metrics import mean_squared_error, r2_score
# Fügen Sie den übergeordneten Pfad zum sys.path hinzu
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import *
from modules.class_load import *

class LoadPredictionAdjuster:
    def __init__(self, measured_data, predicted_data, load_forecast):
        self.measured_data = measured_data
        self.predicted_data = predicted_data
        self.load_forecast = load_forecast
        self.merged_data = self._merge_data()
        self.train_data = None
        self.test_data = None
        self.weekday_diff = None
        self.weekend_diff = None


    def _remove_outliers(self, data, threshold=2):
            # Berechne den Z-Score der 'Last'-Daten
            data['Z-Score'] = np.abs((data['Last'] - data['Last'].mean()) / data['Last'].std())
            # Filtere die Daten nach dem Schwellenwert
            filtered_data = data[data['Z-Score'] < threshold]
            return filtered_data.drop(columns=['Z-Score'])


    def _merge_data(self):
        merged_data = pd.merge(self.measured_data, self.predicted_data, on='time', how='inner')
        merged_data['Hour'] = merged_data['time'].dt.hour
        merged_data['DayOfWeek'] = merged_data['time'].dt.dayofweek
        return merged_data

    def calculate_weighted_mean(self, train_period_weeks=9, test_period_weeks=1):
        self.merged_data = self._remove_outliers(self.merged_data)
        train_end_date = self.merged_data['time'].max() - pd.Timedelta(weeks=test_period_weeks)
        train_start_date = train_end_date - pd.Timedelta(weeks=train_period_weeks)

        test_start_date = train_end_date + pd.Timedelta(hours=1)
        test_end_date = test_start_date + pd.Timedelta(weeks=test_period_weeks) - pd.Timedelta(hours=1)

        self.train_data = self.merged_data[(self.merged_data['time'] >= train_start_date) & (self.merged_data['time'] <= train_end_date)]
        self.test_data = self.merged_data[(self.merged_data['time'] >= test_start_date) & (self.merged_data['time'] <= test_end_date)]

        self.train_data['Difference'] = self.train_data['Last'] - self.train_data['Last Pred']

        weekdays_train_data = self.train_data[self.train_data['DayOfWeek'] < 5]
        weekends_train_data = self.train_data[self.train_data['DayOfWeek'] >= 5]

        self.weekday_diff = weekdays_train_data.groupby('Hour').apply(self._weighted_mean_diff).dropna()
        self.weekend_diff = weekends_train_data.groupby('Hour').apply(self._weighted_mean_diff).dropna()

    def _weighted_mean_diff(self, data):
        train_end_date = self.train_data['time'].max()
        weights = 1 / (train_end_date - data['time']).dt.days.replace(0, np.nan)
        weighted_mean = (data['Difference'] * weights).sum() / weights.sum()
        return weighted_mean

    def adjust_predictions(self):
        self.train_data['Adjusted Pred'] = self.train_data.apply(self._adjust_row, axis=1)
        self.test_data['Adjusted Pred'] = self.test_data.apply(self._adjust_row, axis=1)

    def _adjust_row(self, row):
        if row['DayOfWeek'] < 5:
            return row['Last Pred'] + self.weekday_diff.get(row['Hour'], 0)
        else:
            return row['Last Pred'] + self.weekend_diff.get(row['Hour'], 0)

    def plot_results(self):
        self._plot_data(self.train_data, 'Training')
        self._plot_data(self.test_data, 'Testing')

    def _plot_data(self, data, data_type):
        plt.figure(figsize=(14, 7))
        plt.plot(data['time'], data['Last'], label=f'Actual Last - {data_type}', color='blue')
        plt.plot(data['time'], data['Last Pred'], label=f'Predicted Last - {data_type}', color='red', linestyle='--')
        plt.plot(data['time'], data['Adjusted Pred'], label=f'Adjusted Predicted Last - {data_type}', color='green', linestyle=':')
        plt.xlabel('Time')
        plt.ylabel('Load')
        plt.title(f'Actual vs Predicted vs Adjusted Predicted Load ({data_type} Data)')
        plt.legend()
        plt.grid(True)
        plt.show()

    def evaluate_model(self):
        mse = mean_squared_error(self.test_data['Last'], self.test_data['Adjusted Pred'])
        r2 = r2_score(self.test_data['Last'], self.test_data['Adjusted Pred'])
        print(f'Mean Squared Error: {mse}')
        print(f'R-squared: {r2}')

    def predict_next_hours(self, hours_ahead):
        last_date = self.merged_data['time'].max()
        future_dates = [last_date + pd.Timedelta(hours=i) for i in range(1, hours_ahead + 1)]
        future_df = pd.DataFrame({'time': future_dates})
        future_df['Hour'] = future_df['time'].dt.hour
        future_df['DayOfWeek'] = future_df['time'].dt.dayofweek
        future_df['Last Pred'] = future_df['time'].apply(self._forecast_next_hours)
        future_df['Adjusted Pred'] = future_df.apply(self._adjust_row, axis=1)
        return future_df

    def _forecast_next_hours(self, timestamp):
        date_str = timestamp.strftime('%Y-%m-%d')
        hour = timestamp.hour
        daily_forecast = self.load_forecast.get_daily_stats(date_str)
        return daily_forecast[0][hour] if hour < len(daily_forecast[0]) else np.nan



class LastEstimator:
    def __init__(self):
        self.conn_params = db_config
        self.conn = mariadb.connect(**self.conn_params)

    def fetch_data(self, start_date, end_date):
        queries = {
            "Stromzaehler": f"SELECT DATE_FORMAT(timestamp, '%Y-%m-%d %H:00:00') as timestamp, AVG(data) AS Stromzaehler FROM sensor_stromzaehler WHERE topic = 'stromzaehler leistung' AND timestamp BETWEEN '{start_date}' AND '{end_date}' GROUP BY 1 ORDER BY timestamp ASC",
            "PV": f"SELECT DATE_FORMAT(timestamp, '%Y-%m-%d %H:00:00') as timestamp, AVG(data) AS PV FROM data WHERE topic = 'solarallpower' AND timestamp BETWEEN '{start_date}' AND '{end_date}' GROUP BY 1 ORDER BY timestamp ASC",
            "Batterie_Strom_PIP": f"SELECT DATE_FORMAT(timestamp, '%Y-%m-%d %H:00:00') as timestamp, AVG(data) AS Batterie_Strom_PIP FROM pip WHERE topic = 'battery_current' AND timestamp BETWEEN '{start_date}' AND '{end_date}' GROUP BY 1 ORDER BY timestamp ASC",
            "Batterie_Volt_PIP": f"SELECT DATE_FORMAT(timestamp, '%Y-%m-%d %H:00:00') as timestamp, AVG(data) AS Batterie_Volt_PIP FROM pip WHERE topic = 'battery_voltage' AND timestamp BETWEEN '{start_date}' AND '{end_date}' GROUP BY 1 ORDER BY timestamp ASC",
            "Stromzaehler_Raus": f"SELECT DATE_FORMAT(timestamp, '%Y-%m-%d %H:00:00') as timestamp, AVG(data) AS Stromzaehler_Raus FROM sensor_stromzaehler WHERE topic = 'stromzaehler leistung raus' AND timestamp BETWEEN '{start_date}' AND '{end_date}' GROUP BY 1 ORDER BY timestamp ASC",
            "Wallbox": f"SELECT DATE_FORMAT(timestamp, '%Y-%m-%d %H:00:00') as timestamp, AVG(data) AS Wallbox_Leistung FROM wallbox WHERE topic = 'power_total' AND timestamp BETWEEN '{start_date}' AND '{end_date}' GROUP BY 1 ORDER BY timestamp ASC",

        }


        dataframes = {}
        for key, query in queries.items():
            dataframes[key] = pd.read_sql(query, self.conn)
        
        return dataframes

    def calculate_last(self, dataframes):
        # Batterie_Leistung = Batterie_Strom_PIP * Batterie_Volt_PIP
        dataframes["Batterie_Leistung"] = dataframes["Batterie_Strom_PIP"].merge(dataframes["Batterie_Volt_PIP"], on="timestamp", how="outer")
        dataframes["Batterie_Leistung"]["Batterie_Leistung"] = dataframes["Batterie_Leistung"]["Batterie_Strom_PIP"] * dataframes["Batterie_Leistung"]["Batterie_Volt_PIP"]

        # Stromzaehler_Saldo = Stromzaehler - Stromzaehler_Raus
        dataframes["Stromzaehler_Saldo"] = dataframes["Stromzaehler"].merge(dataframes["Stromzaehler_Raus"], on="timestamp", how="outer")
        dataframes["Stromzaehler_Saldo"]["Stromzaehler_Saldo"] = dataframes["Stromzaehler_Saldo"]["Stromzaehler"] - dataframes["Stromzaehler_Saldo"]["Stromzaehler_Raus"]

        # Stromzaehler_Saldo - Batterie_Leistung
        dataframes["Netzleistung"] = dataframes["Stromzaehler_Saldo"].merge(dataframes["Batterie_Leistung"], on="timestamp", how="outer")
        dataframes["Netzleistung"]["Netzleistung"] = dataframes["Netzleistung"]["Stromzaehler_Saldo"] - dataframes["Netzleistung"]["Batterie_Leistung"]

        # Füge die Wallbox-Leistung hinzu
        dataframes["Netzleistung"] = dataframes["Netzleistung"].merge(dataframes["Wallbox"], on="timestamp", how="left")
        dataframes["Netzleistung"]["Wallbox_Leistung"] = dataframes["Netzleistung"]["Wallbox_Leistung"].fillna(0)  # Fülle fehlende Werte mit 0

        # Last = Netzleistung + PV
        # Berechne die endgültige Last
        dataframes["Last"] = dataframes["Netzleistung"].merge(dataframes["PV"], on="timestamp", how="outer")
        dataframes["Last"]["Last_ohneWallbox"] = dataframes["Last"]["Netzleistung"] + dataframes["Last"]["PV"]
        dataframes["Last"]["Last"] = dataframes["Last"]["Netzleistung"] + dataframes["Last"]["PV"] - dataframes["Last"]["Wallbox_Leistung"]
        return dataframes["Last"].dropna()

    def get_last(self, start_date, end_date):
        dataframes = self.fetch_data(start_date, end_date)
        last_df = self.calculate_last(dataframes)
        return last_df





if __name__ == '__main__':


        estimator = LastEstimator()
        start_date = "2024-06-01"
        end_date = "2024-08-01"
        last_df = estimator.get_last(start_date, end_date)

        selected_columns = last_df[['timestamp', 'Last']]
        selected_columns['time'] = pd.to_datetime(selected_columns['timestamp']).dt.floor('H')
        selected_columns['Last'] = pd.to_numeric(selected_columns['Last'], errors='coerce')

        # Drop rows with NaN values
        cleaned_data = selected_columns.dropna()

        print(cleaned_data)
        # Create an instance of LoadForecast
        
        lf = LoadForecast(filepath=r'.\load_profiles.npz', year_energy=6000*1000)

        # Initialize an empty DataFrame to hold the forecast data
        forecast_list = []

        # Loop through each day in the date range
        for single_date in pd.date_range(cleaned_data['time'].min().date(), cleaned_data['time'].max().date()):
                date_str = single_date.strftime('%Y-%m-%d')
                daily_forecast = lf.get_daily_stats(date_str)
                mean_values = daily_forecast[0]  # Extract the mean values
                hours = [single_date + pd.Timedelta(hours=i) for i in range(24)]
                daily_forecast_df = pd.DataFrame({'time': hours, 'Last Pred': mean_values})
                forecast_list.append(daily_forecast_df)

        # Concatenate all daily forecasts into a single DataFrame
        forecast_df = pd.concat(forecast_list, ignore_index=True)

        # Create an instance of the LoadPredictionAdjuster class
        adjuster = LoadPredictionAdjuster(cleaned_data, forecast_df, lf)

        # Calculate the weighted mean differences
        adjuster.calculate_weighted_mean()

        # Adjust the predictions
        adjuster.adjust_predictions()

        # Plot the results
        adjuster.plot_results()

        # Evaluate the model
        adjuster.evaluate_model()

        # Predict the next x hours
        future_predictions = adjuster.predict_next_hours(48)
        print(future_predictions)