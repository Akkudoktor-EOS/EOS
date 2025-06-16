"""Retrieves and processes electricity price forecast data from Energy-Charts.

This module provides classes and mappings to manage electricity price data obtained from the
Energy-Charts API, including support for various electricity price attributes such as temperature,
humidity, cloud cover, and solar irradiance. The data is mapped to the `ElecPriceDataRecord`
format, enabling consistent access to forecasted and historical electricity price attributes.
"""

from typing import Any, List, Optional, Union

import numpy as np
import pandas as pd
import requests
from loguru import logger
from pydantic import ValidationError
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from akkudoktoreos.core.cache import cache_in_file
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.prediction.elecpriceabc import ElecPriceProvider
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration


class EnergyChartsElecPrice(PydanticBaseModel):
    license_info: str
    unix_seconds: List[int]
    price: List[float]
    unit: str
    deprecated: bool


class ElecPriceEnergyCharts(ElecPriceProvider):
    """Fetch and process electricity price forecast data from Energy-Charts.

    ElecPriceEnergyCharts is a singleton-based class that retrieves electricity price forecast data
    from the Energy-Charts API and maps it to `ElecPriceDataRecord` fields, applying
    any necessary scaling or unit corrections. It manages the forecast over a range
    of hours into the future and retains historical data.

    Attributes:
        hours (int, optional): Number of hours in the future for the forecast.
        historic_hours (int, optional): Number of past hours for retaining data.
        start_datetime (datetime, optional): Start datetime for forecasts, defaults to the current datetime.
        end_datetime (datetime, computed): The forecast's end datetime, computed based on `start_datetime` and `hours`.
        keep_datetime (datetime, computed): The datetime to retain historical data, computed from `start_datetime` and `historic_hours`.

    Methods:
        provider_id(): Returns a unique identifier for the provider.
        _request_forecast(): Fetches the forecast from the Energy-Charts API.
        _update_data(): Processes and updates forecast data from Energy-Charts in ElecPriceDataRecord format.
    """

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the Energy-Charts provider."""
        return "ElecPriceEnergyCharts"

    @classmethod
    def _validate_data(cls, json_str: Union[bytes, Any]) -> EnergyChartsElecPrice:
        """Validate Energy-Charts Electricity Price forecast data."""
        try:
            energy_charts_data = EnergyChartsElecPrice.model_validate_json(json_str)
        except ValidationError as e:
            error_msg = ""
            for error in e.errors():
                field = " -> ".join(str(x) for x in error["loc"])
                message = error["msg"]
                error_type = error["type"]
                error_msg += f"Field: {field}\nError: {message}\nType: {error_type}\n"
            logger.error(f"Energy-Charts schema change: {error_msg}")
            raise ValueError(error_msg)
        return energy_charts_data

    @cache_in_file(with_ttl="1 hour")
    def _request_forecast(self) -> EnergyChartsElecPrice:
        """Fetch electricity price forecast data from Energy-Charts API.

        This method sends a request to Energy-Charts API to retrieve forecast data for a specified
        date range. The response data is parsed and returned as JSON for further processing.

        Returns:
            dict: The parsed JSON response from Energy-Charts API containing forecast data.

        Raises:
            ValueError: If the API response does not include expected `electricity price` data.

        Todo:
            - add the file cache again.
        """
        source = "https://api.energy-charts.info"
        if not self.start_datetime:
            raise ValueError(f"Start DateTime not set: {self.start_datetime}")

        # Try to take data from 5 weeks back for prediction
        date = to_datetime(self.start_datetime - to_duration("35 days"), as_string="YYYY-MM-DD")
        last_date = to_datetime(self.end_datetime, as_string="YYYY-MM-DD")
        url = f"{source}/price?bzn=DE-LU&start={date}&end={last_date}"
        response = requests.get(url, timeout=10)
        logger.debug(f"Response from {url}: {response}")
        response.raise_for_status()  # Raise an error for bad responses
        energy_charts_data = self._validate_data(response.content)
        # We are working on fresh data (no cache), report update time
        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)
        return energy_charts_data

    def _cap_outliers(self, data: np.ndarray, sigma: int = 2) -> np.ndarray:
        mean = data.mean()
        std = data.std()
        lower_bound = mean - sigma * std
        upper_bound = mean + sigma * std
        capped_data = data.clip(min=lower_bound, max=upper_bound)
        return capped_data

    def _predict_ets(self, history: np.ndarray, seasonal_periods: int, hours: int) -> np.ndarray:
        clean_history = self._cap_outliers(history)
        model = ExponentialSmoothing(
            clean_history, seasonal="add", seasonal_periods=seasonal_periods
        ).fit()
        return model.forecast(hours)

    def _predict_median(self, history: np.ndarray, hours: int) -> np.ndarray:
        clean_history = self._cap_outliers(history)
        return np.full(hours, np.median(clean_history))

    def _update_data(
        self, force_update: Optional[bool] = False
    ) -> None:  # tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Update forecast data in the ElecPriceDataRecord format.

        Retrieves data from Energy-Charts, maps each Energy-Charts field to the corresponding
        `ElecPriceDataRecord` and applies any necessary scaling.

        The final mapped and processed data is inserted into the sequence as `ElecPriceDataRecord`.
        """
        # Get Energy-Charts electricity price data
        energy_charts_data = self._request_forecast(force_update=force_update)  # type: ignore
        if not self.start_datetime:
            raise ValueError(f"Start DateTime not set: {self.start_datetime}")

        # Assumption that all lists are the same length and are ordered chronologically
        # in ascending order and have the same timestamps.

        # Get charges_kwh in wh
        charges_wh = (self.config.elecprice.charges_kwh or 0) / 1000

        # Initialize
        highest_orig_datetime = None  # newest datetime from the api after that we want to update.
        series_data = pd.Series(dtype=float)  # Initialize an empty series

        # Iterate over timestamps and prices together
        for unix_sec, price_eur_per_mwh in zip(
            energy_charts_data.unix_seconds, energy_charts_data.price
        ):
            orig_datetime = to_datetime(unix_sec, in_timezone=self.config.general.timezone)

            # Track the latest datetime
            if highest_orig_datetime is None or orig_datetime > highest_orig_datetime:
                highest_orig_datetime = orig_datetime

            # Convert EUR/MWh to EUR/Wh, apply charges and VAT
            price_wh = ((price_eur_per_mwh / 1_000_000) + charges_wh) * 1.19

            # Store in series
            series_data.at[orig_datetime] = price_wh

        # Update values using key_from_series
        self.key_from_series("elecprice_marketprice_wh", series_data)

        # Generate history array for prediction
        history = self.key_to_array(
            key="elecprice_marketprice_wh", end_datetime=highest_orig_datetime, fill_method="linear"
        )

        amount_datasets = len(self.records)
        if not highest_orig_datetime:  # mypy fix
            error_msg = f"Highest original datetime not available: {highest_orig_datetime}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # some of our data is already in the future, so we need to predict less. If we got less data we increase the prediction hours
        needed_hours = int(
            self.config.prediction.hours
            - ((highest_orig_datetime - self.start_datetime).total_seconds() // 3600)
        )
        print(f"EOS CONFIG {self.config.prediction.hours}")

        if needed_hours <= 0:
            logger.warning(
                f"No prediction needed. needed_hours={needed_hours}, hours={self.config.prediction.hours},highest_orig_datetime {highest_orig_datetime}, start_datetime {self.start_datetime}"
            )  # this might keep data longer than self.start_datetime + self.config.prediction.hours in the records
            return

        if amount_datasets > 800:  # we do the full ets with seasons of 1 week
            prediction = self._predict_ets(history, seasonal_periods=168, hours=needed_hours)
        elif amount_datasets > 168:  # not enough data to do seasons of 1 week, but enough for 1 day
            prediction = self._predict_ets(history, seasonal_periods=24, hours=needed_hours)
        elif amount_datasets > 0:  # not enough data for ets, do median
            prediction = self._predict_median(history, hours=needed_hours)
        else:
            logger.error("No data available for prediction")
            raise ValueError("No data available")

        # write predictions into the records, update if exist.
        prediction_series = pd.Series(
            data=prediction,
            index=[
                highest_orig_datetime + to_duration(f"{i + 1} hours")
                for i in range(len(prediction))
            ],
        )
        self.key_from_series("elecprice_marketprice_wh", prediction_series)
