"""Retrieves and processes electricity price forecast data from Akkudoktor.

This module provides classes and mappings to manage electricity price data obtained from the
Akkudoktor API, including support for various electricity price attributes such as temperature,
humidity, cloud cover, and solar irradiance. The data is mapped to the `ElecPriceDataRecord`
format, enabling consistent access to forecasted and historical electricity price attributes.
"""

from typing import Any, List, Optional, Union

import numpy as np
import requests
from numpydantic import NDArray, Shape
from pydantic import Field, ValidationError
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.prediction.elecpriceabc import ElecPriceDataRecord, ElecPriceProvider
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

    elecprice_8days_weights_day_of_week: NDArray[Shape["7, 8"], float] = Field(
        default=np.full((7, 8), np.nan),
        description="Daily electricity price weights for the last 7 days and today. "
        "A NumPy array of 7 elements (Monday..Sunday), each representing "
        "the daily price weights of the last 7 days (index 0..6, Monday..Sunday) "
        "and today (index 7).",
    )

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

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update forecast data in the ElecPriceDataRecord format.

        Retrieves data from Akkudoktor, maps each Akkudoktor field to the corresponding
        `ElecPriceDataRecord` and applies any necessary scaling.

        The final mapped and processed data is inserted into the sequence as `ElecPriceDataRecord`.
        """
        # Get Akkudoktor electricity price data
        akkudoktor_data = self._request_forecast(force_update=force_update)  # type: ignore

        # Assumption that all lists are the same length and are ordered chronologically
        # in ascending order and have the same timestamps.

        # Get elecprice_charges_kwh_kwh
        charges_wh = (
            self.config.elecprice_charges_kwh / 1000 if self.config.elecprice_charges_kwh else 0.0
        )
        assert self.start_datetime  # mypy fix

        for akkudoktor_value in akkudoktor_data.values:
            orig_datetime = to_datetime(akkudoktor_value.start, in_timezone=self.config.timezone)

            price_wh = akkudoktor_value.marketpriceEurocentPerKWh / (100 * 1000) + charges_wh

            record = ElecPriceDataRecord(
                date_time=orig_datetime,
                elecprice_marketprice_wh=price_wh,
            )
            self.insert(
                0, record
            )  # idk what happens if the date is already there. try except update?

        # now we check if we have data newer than the last from the api. if so thats old prediction. we delete them all.

        # now we count how many data points we have.
        # if its > 800 (5 weeks) we will use EST
        # elif > idk maybe 168 (1 week) we use EST without season
        # elif < 168 we use a simple median
        # #elif == 0 we need some static value from the config

        # depending on the result we check prediction_hours and predict that many hours.

        # we get the result and iterate over it to put it into ElecPriceDataRecord
