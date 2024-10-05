import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import mean_squared_error, r2_score

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
        # Calculate the Z-Score of the 'Last' data
        data["Z-Score"] = np.abs(
            (data["Last"] - data["Last"].mean()) / data["Last"].std()
        )
        # Filter the data based on the threshold
        filtered_data = data[data["Z-Score"] < threshold]
        return filtered_data.drop(columns=["Z-Score"])

    def _merge_data(self):
        # Convert the time column in both DataFrames to datetime
        self.predicted_data["time"] = pd.to_datetime(self.predicted_data["time"])
        self.measured_data["time"] = pd.to_datetime(self.measured_data["time"])

        # Ensure both time columns have the same timezone
        if self.measured_data["time"].dt.tz is None:
            self.measured_data["time"] = self.measured_data["time"].dt.tz_localize(
                "UTC"
            )

        self.predicted_data["time"] = (
            self.predicted_data["time"]
            .dt.tz_localize("UTC")
            .dt.tz_convert("Europe/Berlin")
        )
        self.measured_data["time"] = self.measured_data["time"].dt.tz_convert(
            "Europe/Berlin"
        )

        # Optionally: Remove timezone information if only working locally
        self.predicted_data["time"] = self.predicted_data["time"].dt.tz_localize(None)
        self.measured_data["time"] = self.measured_data["time"].dt.tz_localize(None)

        # Now you can perform the merge
        merged_data = pd.merge(
            self.measured_data, self.predicted_data, on="time", how="inner"
        )
        print(merged_data)
        merged_data["Hour"] = merged_data["time"].dt.hour
        merged_data["DayOfWeek"] = merged_data["time"].dt.dayofweek
        return merged_data

    def calculate_weighted_mean(self, train_period_weeks=9, test_period_weeks=1):
        self.merged_data = self._remove_outliers(self.merged_data)
        train_end_date = self.merged_data["time"].max() - pd.Timedelta(
            weeks=test_period_weeks
        )
        train_start_date = train_end_date - pd.Timedelta(weeks=train_period_weeks)

        test_start_date = train_end_date + pd.Timedelta(hours=1)
        test_end_date = (
            test_start_date
            + pd.Timedelta(weeks=test_period_weeks)
            - pd.Timedelta(hours=1)
        )

        self.train_data = self.merged_data[
            (self.merged_data["time"] >= train_start_date)
            & (self.merged_data["time"] <= train_end_date)
        ]

        self.test_data = self.merged_data[
            (self.merged_data["time"] >= test_start_date)
            & (self.merged_data["time"] <= test_end_date)
        ]

        self.train_data["Difference"] = (
            self.train_data["Last"] - self.train_data["Last Pred"]
        )

        weekdays_train_data = self.train_data[self.train_data["DayOfWeek"] < 5]
        weekends_train_data = self.train_data[self.train_data["DayOfWeek"] >= 5]

        self.weekday_diff = (
            weekdays_train_data.groupby("Hour").apply(self._weighted_mean_diff).dropna()
        )
        self.weekend_diff = (
            weekends_train_data.groupby("Hour").apply(self._weighted_mean_diff).dropna()
        )

    def _weighted_mean_diff(self, data):
        train_end_date = self.train_data["time"].max()
        weights = 1 / (train_end_date - data["time"]).dt.days.replace(0, np.nan)
        weighted_mean = (data["Difference"] * weights).sum() / weights.sum()
        return weighted_mean

    def adjust_predictions(self):
        self.train_data["Adjusted Pred"] = self.train_data.apply(
            self._adjust_row, axis=1
        )
        self.test_data["Adjusted Pred"] = self.test_data.apply(self._adjust_row, axis=1)

    def _adjust_row(self, row):
        if row["DayOfWeek"] < 5:
            return row["Last Pred"] + self.weekday_diff.get(row["Hour"], 0)
        else:
            return row["Last Pred"] + self.weekend_diff.get(row["Hour"], 0)

    def plot_results(self):
        self._plot_data(self.train_data, "Training")
        self._plot_data(self.test_data, "Testing")

    def _plot_data(self, data, data_type):
        plt.figure(figsize=(14, 7))
        plt.plot(
            data["time"], data["Last"], label=f"Actual Last - {data_type}", color="blue"
        )
        plt.plot(
            data["time"],
            data["Last Pred"],
            label=f"Predicted Last - {data_type}",
            color="red",
            linestyle="--",
        )
        plt.plot(
            data["time"],
            data["Adjusted Pred"],
            label=f"Adjusted Predicted Last - {data_type}",
            color="green",
            linestyle=":",
        )
        plt.xlabel("Time")
        plt.ylabel("Load")
        plt.title(f"Actual vs Predicted vs Adjusted Predicted Load ({data_type} Data)")
        plt.legend()
        plt.grid(True)
        plt.show()

    def evaluate_model(self):
        mse = mean_squared_error(
            self.test_data["Last"], self.test_data["Adjusted Pred"]
        )
        r2 = r2_score(self.test_data["Last"], self.test_data["Adjusted Pred"])
        print(f"Mean Squared Error: {mse}")
        print(f"R-squared: {r2}")

    def predict_next_hours(self, hours_ahead):
        last_date = self.merged_data["time"].max()
        future_dates = [
            last_date + pd.Timedelta(hours=i) for i in range(1, hours_ahead + 1)
        ]
        future_df = pd.DataFrame({"time": future_dates})
        future_df["Hour"] = future_df["time"].dt.hour
        future_df["DayOfWeek"] = future_df["time"].dt.dayofweek
        future_df["Last Pred"] = future_df["time"].apply(self._forecast_next_hours)
        future_df["Adjusted Pred"] = future_df.apply(self._adjust_row, axis=1)
        return future_df

    def _forecast_next_hours(self, timestamp):
        date_str = timestamp.strftime("%Y-%m-%d")
        hour = timestamp.hour
        daily_forecast = self.load_forecast.get_daily_stats(date_str)
        return daily_forecast[0][hour] if hour < len(daily_forecast[0]) else np.nan


# if __name__ == '__main__':
#     estimator = LastEstimator()
#     start_date = "2024-06-01"
#     end_date = "2024-08-01"
#     last_df = estimator.get_last(start_date, end_date)

#     selected_columns = last_df[['timestamp', 'Last']]
#     selected_columns['time'] = pd.to_datetime(selected_columns['timestamp']).dt.floor('H')
#     selected_columns['Last'] = pd.to_numeric(selected_columns['Last'], errors='coerce')

#     # Drop rows with NaN values
#     cleaned_data = selected_columns.dropna()

#     print(cleaned_data)
#     # Create an instance of LoadForecast
#     lf = LoadForecast(filepath=r'.\load_profiles.npz', year_energy=6000*1000)

#     # Initialize an empty DataFrame to hold the forecast data
#     forecast_list = []

#     # Loop through each day in the date range
#     for single_date in pd.date_range(cleaned_data['time'].min().date(), cleaned_data['time'].max().date()):
#         date_str = single_date.strftime('%Y-%m-%d')
#         daily_forecast = lf.get_daily_stats(date_str)
#         mean_values = daily_forecast[0]  # Extract the mean values
#         hours = [single_date + pd.Timedelta(hours=i) for i in range(24)]
#         daily_forecast_df = pd.DataFrame({'time': hours, 'Last Pred': mean_values})
#         forecast_list.append(daily_forecast_df)

#     # Concatenate all daily forecasts into a single DataFrame
#     forecast_df = pd.concat(forecast_list, ignore_index=True)

#     # Create an instance of the LoadPredictionAdjuster class
#     adjuster = LoadPredictionAdjuster(cleaned_data, forecast_df, lf)

#     # Calculate the weighted mean differences
#     adjuster.calculate_weighted_mean()

#     # Adjust the predictions
#     adjuster.adjust_predictions()

#     # Plot the results
#     adjuster.plot_results()

#     # Evaluate the model
#     adjuster.evaluate_model()

#     # Predict the next x hours
#     future_predictions = adjuster.predict_next_hours(48)
#     print(future_predictions)
