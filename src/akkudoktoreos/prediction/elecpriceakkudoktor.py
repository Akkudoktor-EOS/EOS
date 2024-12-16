"""Retrieves and processes electricity price forecast data from Akkudoktor.

This module provides classes and mappings to manage electricity price data obtained from the
Akkudoktor API, including support for various electricity price attributes such as temperature,
humidity, cloud cover, and solar irradiance. The data is mapped to the `ElecPriceDataRecord`
format, enabling consistent access to forecasted and historical electricity price attributes.
"""

from typing import Any, List, Optional, Union

import requests
from pydantic import ValidationError

from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.prediction.elecpriceabc import ElecPriceDataRecord, ElecPriceProvider
from akkudoktoreos.utils.cacheutil import cache_in_file
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)


class AkkudoktorElecPriceMeta(PydanticBaseModel):
    start_timestamp: int
    end_timestamp: int
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
        """
        source = "https://api.akkudoktor.net"
        date = to_datetime(self.start_datetime, as_string="Y-M-D")
        last_date = to_datetime(self.end_datetime, as_string="Y-M-D")
        response = requests.get(
            f"{source}/prices?date={date}&last_date={last_date}&tz={self.config.timezone}"
        )
        response.raise_for_status()  # Raise an error for bad responses
        logger.debug(f"Response from {source}: {response}")
        akkudoktor_data = self._validate_data(response.content)
        # We are working on fresh data (no cache), report update time
        self.update_datetime = to_datetime(in_timezone=self.config.timezone)
        return akkudoktor_data

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
        values_len = len(akkudoktor_data.values)
        if values_len < 1:
            # Expect one value set per prediction hour
            raise ValueError(
                f"The forecast must have at least one dataset, "
                f"but only {values_len} data sets are given in forecast data."
            )

        previous_price = akkudoktor_data.values[0].marketpriceEurocentPerKWh
        for i in range(values_len):
            original_datetime = akkudoktor_data.values[i].start
            dt = to_datetime(original_datetime, in_timezone=self.config.timezone)

            if compare_datetimes(dt, self.start_datetime).le:
                # forecast data is too old
                previous_price = akkudoktor_data.values[i].marketpriceEurocentPerKWh
                continue

            record = ElecPriceDataRecord(
                date_time=dt,
                elecprice_marketprice=akkudoktor_data.values[i].marketpriceEurocentPerKWh,
            )
            self.append(record)
        if len(self) == 0:
            # Got no valid forecast data
            raise ValueError(
                f"No valid electricity price forecast for date range {self.start_datetime} to {self.end_datetime}."
            )
        # Assure price starts at start_time
        if compare_datetimes(self[0].date_time, self.start_datetime).gt:
            record = ElecPriceDataRecord(
                date_time=self.start_datetime,
                elecprice_marketprice=previous_price,
            )
            self.insert(0, record)
        # Assure price ends at end_time
        if compare_datetimes(self[-1].date_time, self.end_datetime).lt:
            record = ElecPriceDataRecord(
                date_time=self.end_datetime,
                elecprice_marketprice=self[-1].elecprice_marketprice,
            )
            self.append(record)
        # If some of the hourly values are missing, they will be interpolated when using
        # `key_to_array`.
