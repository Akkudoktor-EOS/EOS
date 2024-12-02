import hashlib
import json
import zoneinfo
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

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
        use_cache: bool = True,
    ):  # 228
        self.cache_dir = config.working_dir / config.directories.cache
        self.use_cache = use_cache
        if not self.cache_dir.is_dir():
            raise SetupIncomplete(f"Output path does not exist: {self.cache_dir}.")

        self.cache_time_file = self.cache_dir / "cache_timestamp.txt"
        self.prices = self.load_data(source)
        self.prediction_hours = config.eos.prediction_hours

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

    # Calculate the electricity price in kWh with taxes 
    def calc_price(self, euro_pro_mwh: float) -> float:
        """
        Provider: Tibber
        Beschaffung: 1.81
        Netznutzung: 9.3
        Steuern, Abgaben und Umlagen: 5.214
        Mehrwertsteuer: 19% = 1.19
        """
        return (euro_pro_mwh / 10 + 1.81 + 9.3 + 5.214) * 1.19
    
    def get_price_for_date(self, date_str: str) -> np.ndarray:
        """Returns all prices for the specified date, including the price from 00:00 of the previous day."""
        
        # Convert date string to datetime object
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")

        # Calculate the previous day
        previous_day = date_obj - timedelta(days=1)
        previous_day_str = previous_day.strftime("%Y-%m-%d")

        # Extract the price from 00:00 of the previous day
        last_price_of_previous_day = [
            self.calc_price(entry["marketprice"])
            for entry in self.prices
            if previous_day_str in entry["end"]
        ][-1]

        # Extract all prices for the specified date
        date_prices = [
            self.calc_price(entry["marketprice"])
            for entry in self.prices
            if date_str in entry["end"]
        ]
        print(f"getPrice: {len(date_prices)}")

        # Add the last price of the previous day at the start of the list
        if len(date_prices) == 23:
            date_prices.insert(0, last_price_of_previous_day)

        return np.round(np.array(date_prices) / 100000.0, 10)

    def get_price_for_daterange(self, start_date_str: str, end_date_str: str) -> np.ndarray:
        """Returns all prices between the start and end dates."""
        print(start_date_str)
        print(end_date_str)
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

        price_list_np = np.array(price_list)

        # If prediction hours are greater than 0, reshape the price list
        if self.prediction_hours > 0:
            price_list_np = repeat_to_shape(price_list_np, (self.prediction_hours,))

        return price_list_np
