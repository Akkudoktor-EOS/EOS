from typing import Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import mean_squared_error, r2_score


class LoadPredictionAdjuster:
    def __init__(
        self,
        measured_data: pd.DataFrame,
        predicted_data: pd.DataFrame,
        load_forecast: object,
    ) -> None:
        """
        Initialize the LoadPredictionAdjuster with measured, predicted data, and a load forecast object.
        """
        # Store the input dataframes
        self.measured_data: pd.DataFrame = measured_data
        self.predicted_data: pd.DataFrame = predicted_data
        self.load_forecast: object = load_forecast

        # Merge measured and predicted data
        self.merged_data: pd.DataFrame = self._merge_data()

        # Initialize placeholders for train/test data and differences
        self.train_data: Optional[pd.DataFrame] = None
        self.test_data: Optional[pd.DataFrame] = None
        self.weekday_diff: Optional[pd.Series] = None
        self.weekend_diff: Optional[pd.Series] = None

    def _remove_outliers(self, data: pd.DataFrame, threshold: float = 2.0) -> pd.DataFrame:
        """
        Remove outliers based on the Z-score from the 'Last' column.

        Args:
            data (pd.DataFrame): The input data with 'Last' column.
            threshold (float): The Z-score threshold for detecting outliers.

        Returns:
            pd.DataFrame: Filtered data without outliers.
        """
        # Calculate Z-score for 'Last' column and filter based on threshold
        data["Z-Score"] = np.abs((data["Last"] - data["Last"].mean()) / data["Last"].std())
        filtered_data = data[data["Z-Score"] < threshold]
        return filtered_data.drop(columns=["Z-Score"])  # Drop Z-score column after filtering

    def _merge_data(self) -> pd.DataFrame:
        """
        Merge the measured and predicted data on the 'time' column.

        Returns:
            pd.DataFrame: The merged dataset.
        """
        # Convert time columns to datetime in both datasets

    def _merge_data(self) -> pd.DataFrame:
        """
        Merge the measured and predicted data on the 'time' column.

        Returns:
            pd.DataFrame: The merged dataset.
        """
        # Convert time columns to datetime in both datasets
        self.predicted_data["time"] = pd.to_datetime(self.predicted_data["time"])
        self.measured_data["time"] = pd.to_datetime(self.measured_data["time"])

        # Localize time to UTC and then convert to Berlin time
        if self.measured_data["time"].dt.tz is None:
            self.measured_data["time"] = self.measured_data["time"].dt.tz_localize("UTC")

        self.predicted_data["time"] = (
            self.predicted_data["time"].dt.tz_localize("UTC").dt.tz_convert("Europe/Berlin")
        )
        self.measured_data["time"] = self.measured_data["time"].dt.tz_convert("Europe/Berlin")

        # Remove timezone information (optional for local work)
        self.predicted_data["time"] = self.predicted_data["time"].dt.tz_localize(None)
        self.measured_data["time"] = self.measured_data["time"].dt.tz_localize(None)

        # Merge the measured and predicted dataframes on 'time'
        merged_data = pd.merge(self.measured_data, self.predicted_data, on="time", how="inner")

        # Extract useful columns such as 'Hour' and 'DayOfWeek'
        merged_data["Hour"] = merged_data["time"].dt.hour
        merged_data["DayOfWeek"] = merged_data["time"].dt.dayofweek
        return merged_data

    def calculate_weighted_mean(
        self, train_period_weeks: int = 9, test_period_weeks: int = 1
    ) -> None:
        """
        Calculate the weighted mean difference between actual and predicted values for training and testing periods.

        Args:
            train_period_weeks (int): Number of weeks to use for training data.
            test_period_weeks (int): Number of weeks to use for testing data.
        """
        # Remove outliers from the merged data
        self.merged_data = self._remove_outliers(self.merged_data)

        # Define training and testing periods based on weeks
        train_end_date = self.merged_data["time"].max() - pd.Timedelta(weeks=test_period_weeks)
        train_start_date = train_end_date - pd.Timedelta(weeks=train_period_weeks)

        test_start_date = train_end_date + pd.Timedelta(hours=1)
        test_end_date = (
            test_start_date + pd.Timedelta(weeks=test_period_weeks) - pd.Timedelta(hours=1)
        )

        # Split merged data into training and testing datasets
        self.train_data = self.merged_data[
            (self.merged_data["time"] >= train_start_date)
            & (self.merged_data["time"] <= train_end_date)
        ]
        self.test_data = self.merged_data[
            (self.merged_data["time"] >= test_start_date)
            & (self.merged_data["time"] <= test_end_date)
        ]

        # Calculate the difference between actual ('Last') and predicted ('Last Pred')
        self.train_data["Difference"] = self.train_data["Last"] - self.train_data["Last Pred"]

        # Separate training data into weekdays and weekends
        weekdays_train_data = self.train_data[self.train_data["DayOfWeek"] < 5]
        weekends_train_data = self.train_data[self.train_data["DayOfWeek"] >= 5]

        # Calculate weighted mean differences for both weekdays and weekends
        self.weekday_diff = (
            weekdays_train_data.groupby("Hour").apply(self._weighted_mean_diff).dropna()
        )
        self.weekend_diff = (
            weekends_train_data.groupby("Hour").apply(self._weighted_mean_diff).dropna()
        )

    def _weighted_mean_diff(self, data: pd.DataFrame) -> float:
        """
        Compute the weighted mean difference between actual and predicted values.

        Args:
            data (pd.DataFrame): Data for a specific hour.

        Returns:
            float: Weighted mean difference for that hour.
        """
        # Weigh recent data more by using days difference from the last date in the training set
        train_end_date = self.train_data["time"].max()
        weights = 1 / (train_end_date - data["time"]).dt.days.replace(0, np.nan)
        weighted_mean = (data["Difference"] * weights).sum() / weights.sum()
        return weighted_mean

    def adjust_predictions(self) -> None:
        """
        Adjust predictions for both training and test data using the calculated weighted differences.
        """
        # Apply adjustments to both training and testing data
        self.train_data["Adjusted Pred"] = self.train_data.apply(self._adjust_row, axis=1)
        self.test_data["Adjusted Pred"] = self.test_data.apply(self._adjust_row, axis=1)

    def _adjust_row(self, row: pd.Series) -> float:
        """
        Adjust a single row's prediction based on the hour and day of the week.

        Args:
            row (pd.Series): A single row of data.

        Returns:
            float: Adjusted prediction.
        """
        # Adjust predictions based on whether it's a weekday or weekend
        if row["DayOfWeek"] < 5:
            return row["Last Pred"] + self.weekday_diff.get(row["Hour"], 0)
        else:
            return row["Last Pred"] + self.weekend_diff.get(row["Hour"], 0)

    def plot_results(self) -> None:
        """
        Plot the actual, predicted, and adjusted predicted values for both training and testing data.
        """
        # Plot results for training and testing data
        self._plot_data(self.train_data, "Training")
        self._plot_data(self.test_data, "Testing")

    def _plot_data(self, data: pd.DataFrame, data_type: str) -> None:
        """
        Helper function to plot the data.

        Args:
            data (pd.DataFrame): Data to plot (training or testing).
            data_type (str): Label to identify whether it's training or testing data.
        """
        plt.figure(figsize=(14, 7))
        plt.plot(data["time"], data["Last"], label=f"Actual Last - {data_type}", color="blue")
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

    def evaluate_model(self) -> Tuple[float, float]:
        """
        Evaluate the model performance using Mean Squared Error and R-squared metrics.
        
        Args:
            mse: Mean squared error of the adjusted prediction w.r.t. last test data.
            r2: R2 score of the adjusted prediction w.r.t. last test data.
        """
        # Calculate Mean Squared Error and R-squared for the adjusted predictions
        mse = mean_squared_error(self.test_data["Last"], self.test_data["Adjusted Pred"])
        r2 = r2_score(self.test_data["Last"], self.test_data["Adjusted Pred"])
        print(f"Mean Squared Error: {mse}")
        print(f"R-squared: {r2}")
        return mse, r2

    def predict_next_hours(self, hours_ahead: int) -> pd.DataFrame:
        """
        Predict load for the next given number of hours.

        Args:
            hours_ahead (int): Number of hours to predict.

        Returns:
            pd.DataFrame: DataFrame with future predicted and adjusted load.
        """
        # Get the latest time in the merged data
        last_date = self.merged_data["time"].max()

        # Generate future timestamps for the next 'hours_ahead'
        future_dates = [last_date + pd.Timedelta(hours=i) for i in range(1, hours_ahead + 1)]
        future_df = pd.DataFrame({"time": future_dates})

        # Extract hour and day of the week for the future predictions
        future_df["Hour"] = future_df["time"].dt.hour
        future_df["DayOfWeek"] = future_df["time"].dt.dayofweek

        # Predict the load and apply adjustments for future predictions
        future_df["Last Pred"] = future_df["time"].apply(self._forecast_next_hours)
        future_df["Adjusted Pred"] = future_df.apply(self._adjust_row, axis=1)

        return future_df

    def _forecast_next_hours(self, timestamp: pd.Timestamp) -> float:
        """
        Helper function to forecast the load for the next hours using the load_forecast object.

        Args:
            timestamp (pd.Timestamp): The time for which to predict the load.

        Returns:
            float: Predicted load for the given time.
        """
        # Use the load_forecast object to get the hourly forecast for the given timestamp
        date_str = timestamp.strftime("%Y-%m-%d")
        hour = timestamp.hour
        daily_forecast = self.load_forecast.get_daily_stats(date_str)


        # Return forecast for the specific hour, or NaN if hour is out of range
        return daily_forecast[0][hour] if hour < len(daily_forecast[0]) else np.nan
