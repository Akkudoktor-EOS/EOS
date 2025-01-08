"""Retrieves and processes electricity price forecast data from Akkudoktor.

This module provides classes and mappings to manage electricity price data obtained from the
Akkudoktor API, including support for various electricity price attributes such as temperature,
humidity, cloud cover, and solar irradiance. The data is mapped to the `ElecPriceDataRecord`
format, enabling consistent access to forecasted and historical electricity price attributes.
"""

from typing import Any, List, Optional, Union

import numpy as np
import pandas as pd
import requests
from pydantic import ValidationError
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.prediction.elecpriceabc import ElecPriceProvider
from akkudoktoreos.utils.cacheutil import cache_in_file
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration

logger = get_logger(__name__)


class AkkudoktorElecPriceMeta(PydanticBaseModel):
    start_timestamp: str
    end_timestamp: str
    start: str
    end: str


class AkkudoktorElecPriceValue(PydanticBaseModel):
    start_timestamp: int
    end_timestamp: int
    start: str
    end: str
    marketprice: float
    unit: str
    marketpriceEurocentPerKWh: float


class AkkudoktorElecPrice(PydanticBaseModel):
    meta: AkkudoktorElecPriceMeta
    values: List[AkkudoktorElecPriceValue]


class ElecPriceAkkudoktor(ElecPriceProvider):
    """Fetch and process electricity price forecast data from Akkudoktor.

    ElecPriceAkkudoktor is a singleton-based class that retrieves electricity price forecast data
    from the Akkudoktor API and maps it to `ElecPriceDataRecord` fields, applying
    any necessary scaling or unit corrections. It manages the forecast over a range
    of hours into the future and retains historical data.

    Attributes:
        prediction_hours (int, optional): Number of hours in the future for the forecast.
        prediction_historic_hours (int, optional): Number of past hours for retaining data.
        start_datetime (datetime, optional): Start datetime for forecasts, defaults to the current datetime.
        end_datetime (datetime, computed): The forecast's end datetime, computed based on `start_datetime` and `prediction_hours`.
        keep_datetime (datetime, computed): The datetime to retain historical data, computed from `start_datetime` and `prediction_historic_hours`.

    Methods:
        provider_id(): Returns a unique identifier for the provider.
        _request_forecast(): Fetches the forecast from the Akkudoktor API.
        _update_data(): Processes and updates forecast data from Akkudoktor in ElecPriceDataRecord format.
    """

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the Akkudoktor provider."""
        return "ElecPriceAkkudoktor"

    @classmethod
    def _validate_data(cls, json_str: Union[bytes, Any]) -> AkkudoktorElecPrice:
        """Validate Akkudoktor Electricity Price forecast data."""
        try:
            akkudoktor_data = AkkudoktorElecPrice.model_validate_json(json_str)
        except ValidationError as e:
            error_msg = ""
            for error in e.errors():
                field = " -> ".join(str(x) for x in error["loc"])
                message = error["msg"]
                error_type = error["type"]
                error_msg += f"Field: {field}\nError: {message}\nType: {error_type}\n"
            logger.error(f"Akkudoktor schema change: {error_msg}")
            raise ValueError(error_msg)
        return akkudoktor_data

    @cache_in_file(with_ttl="1 hour")
    def _request_forecast(self) -> AkkudoktorElecPrice:
        """Fetch electricity price forecast data from Akkudoktor API.

        This method sends a request to Akkudoktor's API to retrieve forecast data for a specified
        date range. The response data is parsed and returned as JSON for further processing.

        Returns:
            dict: The parsed JSON response from Akkudoktor API containing forecast data.

        Raises:
            ValueError: If the API response does not include expected `electricity price` data.

        Todo:
            - add the file cache again.
        """
        source = "https://api.akkudoktor.net"
        assert self.start_datetime  # mypy fix
        # Try to take data from 5 weeks back for prediction
        date = to_datetime(self.start_datetime - to_duration("35 days"), as_string="YYYY-MM-DD")
        last_date = to_datetime(self.end_datetime, as_string="YYYY-MM-DD")
        url = f"{source}/prices?start={date}&end={last_date}&tz={self.config.timezone}"
        response = requests.get(url)
        logger.debug(f"Response from {url}: {response}")
        response.raise_for_status()  # Raise an error for bad responses
        akkudoktor_data = self._validate_data(response.content)
        # We are working on fresh data (no cache), report update time
        self.update_datetime = to_datetime(in_timezone=self.config.timezone)
        return akkudoktor_data

    def _cap_outliers(self, data: np.ndarray, sigma: int = 2) -> np.ndarray:
        mean = data.mean()
        std = data.std()
        lower_bound = mean - sigma * std
        upper_bound = mean + sigma * std
        capped_data = data.clip(min=lower_bound, max=upper_bound)
        return capped_data

    def _predict_ets(
        self, history: np.ndarray, seasonal_periods: int, prediction_hours: int
    ) -> np.ndarray:
        clean_history = self._cap_outliers(history)
        model = ExponentialSmoothing(
            clean_history, seasonal="add", seasonal_periods=seasonal_periods
        ).fit()
        return model.forecast(prediction_hours)

    def _predict_median(self, history: np.ndarray, prediction_hours: int) -> np.ndarray:
        clean_history = self._cap_outliers(history)
        return np.full(prediction_hours, np.median(clean_history))

    def _update_data(
        self, force_update: Optional[bool] = False
    ) -> None:  # Tuple[np.ndarray, np.ndarray, np.ndarray]:  # for debug main
        """Update forecast data in the ElecPriceDataRecord format.

        Retrieves data from Akkudoktor, maps each Akkudoktor field to the corresponding
        `ElecPriceDataRecord` and applies any necessary scaling.

        The final mapped and processed data is inserted into the sequence as `ElecPriceDataRecord`.
        """
        # Get Akkudoktor electricity price data
        akkudoktor_data = self._request_forecast(force_update=force_update)  # type: ignore
        assert self.start_datetime  # mypy fix

        # Assumption that all lists are the same length and are ordered chronologically
        # in ascending order and have the same timestamps.

        # Get elecprice_charges_kwh in wh
        charges_wh = (self.config.elecprice_charges_kwh or 0) / 1000

        highest_orig_datetime = None  # newest datetime from the api after that we want to update.
        series_data = pd.Series(dtype=float)  # Initialize an empty series

        for value in akkudoktor_data.values:
            orig_datetime = to_datetime(value.start, in_timezone=self.config.timezone)
            if highest_orig_datetime is None or orig_datetime > highest_orig_datetime:
                highest_orig_datetime = orig_datetime

            price_wh = value.marketpriceEurocentPerKWh / (100 * 1000) + charges_wh

            # Collect all values into the Pandas Series
            series_data.at[orig_datetime] = price_wh

        # Update values using key_from_series
        self.key_from_series("elecprice_marketprice_wh", series_data)

        # Generate history array for prediction
        history = self.key_to_array(
            key="elecprice_marketprice_wh", end_datetime=highest_orig_datetime, fill_method="linear"
        )

        amount_datasets = len(self.records)
        assert highest_orig_datetime  # mypy fix

        if amount_datasets > 800:  # we do the full ets with seasons of 1 week
            prediction = self._predict_ets(
                history, seasonal_periods=168, prediction_hours=self.config.prediction_hours
            )
        elif amount_datasets > 168:  # not enough data to do seasons of 1 week, but enough for 1 day
            prediction = self._predict_ets(
                history, seasonal_periods=24, prediction_hours=self.config.prediction_hours
            )
        elif amount_datasets > 0:  # not enough data for ets, do median
            prediction = self._predict_median(
                history, prediction_hours=self.config.prediction_hours
            )
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

        # history2 = self.key_to_array(key="elecprice_marketprice_wh", fill_method="linear") + 0.0002
        # return history, history2, prediction  # for debug main


"""
def visualize_predictions(
    history: np.ndarray[Any, Any],
    history2: np.ndarray[Any, Any],
    predictions: np.ndarray[Any, Any],
) -> None:
    import matplotlib.pyplot as plt

    plt.figure(figsize=(28, 14))
    plt.plot(range(len(history)), history, label="History", color="green")
    plt.plot(range(len(history2)), history2, label="History_new", color="blue")
    plt.plot(
        range(len(history), len(history) + len(predictions)),
        predictions,
        label="Predictions",
        color="red",
    )
    plt.title("Predictions vs True Values for ets")
    plt.xlabel("Time")
    plt.ylabel("Price")
    plt.legend()
    plt.savefig("predictions_vs_true.png")
    plt.close()


def main() -> None:
    elec_price_akkudoktor = ElecPriceAkkudoktor()
    history, history2, predictions = elec_price_akkudoktor._update_data()

    visualize_predictions(history, history2, predictions)
    # print(history, history2, predictions)


if __name__ == "__main__":
    main()
"""
