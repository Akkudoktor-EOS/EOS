import hashlib
import json
import zoneinfo
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Union

import numpy as np
import requests

from akkudoktoreos.config import AppConfig, SetupIncomplete
from akkudoktoreos.logutil import get_logger

# Initialize logger with DEBUG level
logger = get_logger(__name__, logging_level="DEBUG")


def repeat_to_shape(array: np.ndarray, target_shape: tuple[int, ...]) -> np.ndarray:
    """Expands an array to a specified shape using repetition."""
    logger.debug(f"Expanding array with shape {array.shape} to target shape {target_shape}")
    if len(target_shape) != array.ndim:
        error_msg = "Array and target shape must have the same number of dimensions"
        logger.debug(f"Validation did not succeed: {error_msg}")
        raise ValueError(error_msg)

    repeats = tuple(target_shape[i] // array.shape[i] for i in range(array.ndim))
    expanded_array = np.tile(array, repeats)
    logger.debug(f"Expanded array shape: {expanded_array.shape}")
    return expanded_array


class HourlyElectricityPriceForecast:
    def __init__(
        self,
        source: Union[str, Path],
        config: AppConfig,
        charges: float = 0.000228,
        use_cache: bool = True,
    ) -> None:
        logger.debug("Initializing HourlyElectricityPriceForecast")
        self.cache_dir = config.working_dir / config.directories.cache
        self.use_cache = use_cache
        self.charges = charges
        self.prediction_hours = config.eos.prediction_hours

        if not self.cache_dir.is_dir():
            error_msg = f"Output path does not exist: {self.cache_dir}"
            logger.debug(f"Validation did not succeed: {error_msg}")
            raise SetupIncomplete(error_msg)

        self.cache_time_file = self.cache_dir / "cache_timestamp.txt"
        self.prices = self.load_data(source)

    def load_data(self, source: Union[str, Path]) -> list[dict[str, Union[str, float]]]:
        """Loads data from a cache file or source, returns a list of price entries."""
        cache_file = self.get_cache_file(source)
        logger.debug(f"Loading data from source: {source}, using cache file: {cache_file}")

        if (
            isinstance(source, str)
            and self.use_cache
            and cache_file.is_file()
            and not self.is_cache_expired()
        ):
            logger.debug("Loading data from cache...")
            with cache_file.open("r") as file:
                json_data = json.load(file)
        else:
            logger.debug("Fetching data from source and updating cache...")
            json_data = self.fetch_and_cache_data(source, cache_file)

        return json_data.get("values", [])

    def get_cache_file(self, source: Union[str, Path]) -> Path:
        """Generates a unique cache file path for the source URL."""
        url = str(source)
        hash_object = hashlib.sha256(url.encode())
        hex_dig = hash_object.hexdigest()
        cache_file = self.cache_dir / f"cache_{hex_dig}.json"
        logger.debug(f"Generated cache file path: {cache_file}")
        return cache_file

    def is_cache_expired(self) -> bool:
        """Checks if the cache has expired based on a one-hour limit."""
        if not self.cache_time_file.is_file():
            logger.debug("Cache timestamp file does not exist; cache considered expired")
            return True

        with self.cache_time_file.open("r") as file:
            timestamp_str = file.read()
        last_cache_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        cache_expired = datetime.now() - last_cache_time > timedelta(hours=1)
        logger.debug(f"Cache expired: {cache_expired}")
        return cache_expired

    def update_cache_timestamp(self) -> None:
        """Updates the cache timestamp to the current time."""
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.cache_time_file.open("w") as file:
            file.write(current_time)
        logger.debug(f"Updated cache timestamp to {current_time}")

    def fetch_and_cache_data(self, source: Union[str, Path], cache_file: Path) -> dict:
        """Fetches data from a URL or file and caches it."""
        if isinstance(source, str):
            logger.debug(f"Fetching data from URL: {source}")
            response = requests.get(source)
            if response.status_code != 200:
                error_msg = f"Error fetching data: {response.status_code}"
                logger.debug(f"Validation did not succeed: {error_msg}")
                raise Exception(error_msg)

            json_data = response.json()
            with cache_file.open("w") as file:
                json.dump(json_data, file)
            self.update_cache_timestamp()
        elif source.is_file():
            logger.debug(f"Loading data from file: {source}")
            with source.open("r") as file:
                json_data = json.load(file)
        else:
            error_msg = f"Invalid input path: {source}"
            logger.debug(f"Validation did not succeed: {error_msg}")
            raise ValueError(error_msg)

        return json_data

    def get_price_for_date(self, date_str: str) -> np.ndarray:
        """Retrieves all prices for a specified date, adding the previous day's last price if needed."""
        logger.debug(f"Getting prices for date: {date_str}")
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        previous_day_str = (date_obj - timedelta(days=1)).strftime("%Y-%m-%d")

        previous_day_prices = [
            entry["marketpriceEurocentPerKWh"] + self.charges
            for entry in self.prices
            if previous_day_str in entry["end"]
        ]
        last_price_of_previous_day = previous_day_prices[-1] if previous_day_prices else 0

        date_prices = [
            entry["marketpriceEurocentPerKWh"] + self.charges
            for entry in self.prices
            if date_str in entry["end"]
        ]

        if len(date_prices) < 24:
            date_prices.insert(0, last_price_of_previous_day)

        logger.debug(f"Retrieved {len(date_prices)} prices for date {date_str}")
        return np.round(np.array(date_prices) / 100000.0, 10)

    def get_price_for_daterange(self, start_date_str: str, end_date_str: str) -> np.ndarray:
        """Retrieves all prices within a specified date range."""
        logger.debug(f"Getting prices from {start_date_str} to {end_date_str}")
        start_date = (
            datetime.strptime(start_date_str, "%Y-%m-%d")
            .replace(tzinfo=timezone.utc)
            .astimezone(zoneinfo.ZoneInfo("Europe/Berlin"))
        )
        end_date = (
            datetime.strptime(end_date_str, "%Y-%m-%d")
            .replace(tzinfo=timezone.utc)
            .astimezone(zoneinfo.ZoneInfo("Europe/Berlin"))
        )

        price_list = []

        while start_date < end_date:
            date_str = start_date.strftime("%Y-%m-%d")
            daily_prices = self.get_price_for_date(date_str)

            if daily_prices.size == 24:
                price_list.extend(daily_prices)
            start_date += timedelta(days=1)

        if self.prediction_hours > 0:
            logger.debug(f"Reshaping price list to match prediction hours: {self.prediction_hours}")
            price_list = repeat_to_shape(np.array(price_list), (self.prediction_hours,))

        logger.debug(f"Total prices retrieved for date range: {len(price_list)}")
        return np.round(np.array(price_list), 10)
