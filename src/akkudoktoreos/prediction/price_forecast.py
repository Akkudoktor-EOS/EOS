import hashlib
import json
import zoneinfo
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
import requests

from akkudoktoreos.config import AppConfig, SetupIncomplete


def repeat_to_shape(array: np.ndarray, target_shape: Sequence[int]) -> np.ndarray:
    # Check if the array fits the target shape
    if len(target_shape) != array.ndim:
        raise ValueError("Array and target shape must have the same number of dimensions")

    # Number of repetitions per dimension
    repeats = tuple(target_shape[i] // array.shape[i] for i in range(array.ndim))

    # Use np.tile to expand the array
    expanded_array = np.tile(array, repeats)
    return expanded_array


class HourlyElectricityPriceForecast:
    def __init__(
        self,
        source: str | Path,
        config: AppConfig,
        charges: float = 0.00021,
        use_cache: bool = True,
    ):  # 228
        self.cache_dir = config.working_dir / config.directories.cache
        self.use_cache = use_cache
        if not self.cache_dir.is_dir():
            raise SetupIncomplete(f"Output path does not exist: {self.cache_dir}.")

        self.seven_day_mean = np.array([])
        self.cache_time_file = self.cache_dir / "cache_timestamp.txt"
        self.prices = self.load_data(source)
        self.charges = charges
        self.prediction_hours = config.eos.prediction_hours
        self.seven_day_mean = self.get_average_price_last_7_days()

    def load_data(self, source: str | Path) -> list[dict[str, Any]]:
        cache_file = self.get_cache_file(source)
        if isinstance(source, str):
            if cache_file.is_file() and not self.is_cache_expired() and self.use_cache:
                print("Loading data from cache...")
                with cache_file.open("r") as file:
                    json_data = json.load(file)
            else:
                print("Loading data from the URL...")
                response = requests.get(source)
                if response.status_code == 200:
                    json_data = response.json()
                    with cache_file.open("w") as file:
                        json.dump(json_data, file)
                    self.update_cache_timestamp()
                else:
                    raise Exception(f"Error fetching data: {response.status_code}")
        elif source.is_file():
            with source.open("r") as file:
                json_data = json.load(file)
        else:
            raise ValueError(f"Input is not a valid path: {source}")
        return json_data["values"]

    def get_cache_file(self, url: str | Path) -> Path:
        if isinstance(url, Path):
            url = str(url)
        hash_object = hashlib.sha256(url.encode())
        hex_dig = hash_object.hexdigest()
        return self.cache_dir / f"cache_{hex_dig}.json"

    def is_cache_expired(self) -> bool:
        if not self.cache_time_file.is_file():
            return True
        with self.cache_time_file.open("r") as file:
            timestamp_str = file.read()
            last_cache_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        return datetime.now() - last_cache_time > timedelta(hours=1)

    def update_cache_timestamp(self) -> None:
        with self.cache_time_file.open("w") as file:
            file.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def get_price_for_date(self, date_str: str) -> np.ndarray:
        """Returns all prices for the specified date, including the price from 00:00 of the previous day."""
        # Convert date string to datetime object
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")

        # Calculate the previous day
        previous_day = date_obj - timedelta(days=1)
        previous_day_str = previous_day.strftime("%Y-%m-%d")

        # Extract the price from 00:00 of the previous day
        previous_day_prices = [
            entry["marketprice"]  # + self.charges
            entry["marketprice"]  # + self.charges
            for entry in self.prices
            if previous_day_str in entry["end"]
        ]
        last_price_of_previous_day = previous_day_prices[-1] if previous_day_prices else 0

        # Extract all prices for the specified date
        date_prices = [
            entry["marketprice"]  # + self.charges
            entry["marketprice"]  # + self.charges
            for entry in self.prices
            if date_str in entry["end"]
        ]

        # Add the last price of the previous day at the start of the list
        if len(date_prices) == 23:
            date_prices.insert(0, last_price_of_previous_day)

        return np.array(date_prices) / (1000.0 * 1000.0) + self.charges

        return np.array(date_prices) / (1000.0 * 1000.0) + self.charges

    def get_average_price_last_7_days(self, end_date_str: Optional[str] = None) -> np.ndarray:
        """Calculate the hourly average electricity price for the last 7 days.

        Parameters:
            end_date_str (Optional[str]): End date in the format "YYYY-MM-DD".
                                        If not provided, today's date will be used.

        Returns:
            np.ndarray: A NumPy array of 24 elements, each representing the hourly
                        average price over the last 7 days.

        Raises:
            ValueError: If there is insufficient data to calculate the averages.
        """
        # Determine the end date (use today's date if not provided)
        if end_date_str is None:
            end_date = datetime.now().date() - timedelta(days=1)
        else:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()

        if self.seven_day_mean.size > 0:
            return np.array([self.seven_day_mean])

        # Calculate the start date (7 days before the end date)
        start_date = end_date - timedelta(days=7)

        # Convert dates to strings
        start_date_str = start_date.strftime("%Y-%m-%d")
        end_date_str = end_date.strftime("%Y-%m-%d")

        # Retrieve price data for the specified date range
        price_data = self.get_price_for_daterange(start_date_str, end_date_str)

        # Ensure there is enough data for 7 full days (7 days × 24 hours)
        if price_data.size < 7 * 24:
            raise ValueError(
                "Not enough data to calculate the average for the last 7 days.", price_data
            )

        # Reshape the data into a 7x24 matrix (7 rows for days, 24 columns for hours)
        price_matrix = price_data.reshape(-1, 24)
        # Calculate the average price for each hour across the 7 days
        average_prices = np.average(
            price_matrix,
            axis=0,
            weights=np.array([1, 2, 4, 8, 16, 32, 64]) / np.sum(np.array([1, 2, 4, 8, 16, 32, 64])),
        )
        return average_prices
        average_prices = np.average(
            price_matrix,
            axis=0,
            weights=np.array([1, 2, 4, 8, 16, 32, 64]) / np.sum(np.array([1, 2, 4, 8, 16, 32, 64])),
        )
        final_weights = np.linspace(1, 0, price_matrix.shape[1])

        # Weight last known price linear falling
        average_prices_with_final_weight = [
            (average_prices[i] * (1 - final_weights[i]))
            + (price_matrix[-1, -1] * (final_weights[i]))
            for i in range(price_matrix.shape[1])
        ]

        return np.array(average_prices_with_final_weight)

    def get_price_for_daterange(
        self, start_date_str: str, end_date_str: str, repeat: bool = False
    ) -> np.ndarray:
        """Returns all prices between the start and end dates."""
        start_date_utc = datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_date_utc = datetime.strptime(end_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        start_date = start_date_utc.astimezone(zoneinfo.ZoneInfo("Europe/Berlin"))
        end_date = end_date_utc.astimezone(zoneinfo.ZoneInfo("Europe/Berlin"))

        price_list: list[float] = []

        while start_date < end_date:
            date_str = start_date.strftime("%Y-%m-%d")
            daily_prices = self.get_price_for_date(date_str)

            if daily_prices.size == 24:
                price_list.extend(daily_prices)
            start_date += timedelta(days=1)
            # print(date_str, ":", daily_prices)
        price_list_np = np.array(price_list)

        # print(price_list_np.shape, " ", self.prediction_hours)
        # If prediction hours are greater than 0 and repeat is True
        # print(price_list_np)
        if self.prediction_hours > 0 and repeat:
            # Check if price_list_np is shorter than prediction_hours
            if price_list_np.size < self.prediction_hours:
                # Repeat the seven_day_mean array to cover the missing hours
                repeat_count = (self.prediction_hours // self.seven_day_mean.size) + 1
                additional_values = np.tile(self.seven_day_mean, repeat_count)[
                    : self.prediction_hours - price_list_np.size
                ]

                # Concatenate existing values with the repeated values
                price_list_np = np.concatenate((price_list_np, additional_values))
        # print(price_list_np)
        return price_list_np
