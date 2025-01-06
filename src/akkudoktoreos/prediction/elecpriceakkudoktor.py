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

from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.prediction.elecpriceabc import ElecPriceDataRecord, ElecPriceProvider
from akkudoktoreos.utils.cacheutil import CacheFileStore, cache_in_file
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime, to_duration

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

    elecprice_35days: NDArray[Shape["24, 35"], float] = Field(
        default=np.full((24, 35), np.nan),
        description="Hourly electricity prices for the last 35 days and today (€/KWh). "
        "A NumPy array of 24 elements, each representing the hourly prices "
        "of the last 35 days. Today is represented by the last column (index 34).",
    )
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

    def _calculate_weighted_mean(self, day_of_week: int, hour: int) -> float:
        """Calculate the weighted mean price for given day_of_week and hour.

        Args:
            day_of_week (int). The day of week to calculate the mean for (0=Monday..6).
            hour (int): The hour  week to calculate the mean for (0..23).

        Returns:
            price_weihgted_mead (float): Weighted mean price for given day_of:week and hour.
        """
        if np.isnan(self.elecprice_8days_weights_day_of_week[0][0]):
            # Weights not initialized - do now

            # Priority of day: 1=most .. 7=least
            priority_of_day = np.array(
                #    Available Prediction days /
                #    M,Tu,We,Th,Fr,Sa,Su,Today/ Forecast day_of_week
                [
                    [1, 2, 3, 4, 5, 6, 7, 1],  # Monday
                    [3, 1, 2, 4, 5, 6, 7, 1],  # Tuesday
                    [4, 2, 1, 3, 5, 6, 7, 1],  # Wednesday
                    [5, 4, 2, 1, 3, 6, 7, 1],  # Thursday
                    [5, 4, 3, 2, 1, 6, 7, 1],  # Friday
                    [7, 6, 5, 4, 2, 1, 3, 1],  # Saturday
                    [7, 6, 5, 4, 3, 2, 1, 1],  # Sunday
                ]
            )
            # Take priorities above to decrease relevance in 2s exponential
            self.elecprice_8days_weights_day_of_week = 2 / (2**priority_of_day)
        last_8_days = self.elecprice_35days[:, -8:]
        # Compute the weighted mean for day_of_week and hour
        prices_of_hour = last_8_days[hour]
        if np.isnan(prices_of_hour).all():
            # No prediction prices available for this hour - use mean value of all prices
            price_weighted_mean = np.nanmean(last_8_days)
        else:
            weights = self.elecprice_8days_weights_day_of_week[day_of_week]
            prices_of_hour_masked: NDArray[Shape["24"]] = np.ma.MaskedArray(
                prices_of_hour, mask=np.isnan(prices_of_hour)
            )
            price_weighted_mean = np.ma.average(prices_of_hour_masked, weights=weights)

        return float(price_weighted_mean)

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

        # Get cached values
        elecprice_cache_file = CacheFileStore().get(key="ElecPriceAkkudoktor35dayCache")
        if elecprice_cache_file is None:
            # Cache does not exist - create it
            elecprice_cache_file = CacheFileStore().create(
                key="ElecPriceAkkudoktordayCache",
                until_datetime=to_datetime("infinity"),
                suffix=".npy",
            )
            np.save(elecprice_cache_file, self.elecprice_35days)
        elecprice_cache_file.seek(0)
        self.elecprice_35days = np.load(elecprice_cache_file)

        # Get elecprice_charges_kwh_kwh
        charges_kwh = (
            self.config.elecprice_charges_kwh if self.config.elecprice_charges_kwh else 0.0
        )

        for i in range(values_len):
            original_datetime = akkudoktor_data.values[i].start
            dt = to_datetime(original_datetime, in_timezone=self.config.timezone)

            akkudoktor_value = akkudoktor_data.values[i]
            price_wh = (
                akkudoktor_value.marketpriceEurocentPerKWh / (100 * 1000) + charges_kwh / 1000
            )
            assert self.start_datetime  # mypy fix
            # We provide prediction starting at start of day, to be compatible to old system.
            if compare_datetimes(dt, self.start_datetime.start_of("day")).lt:
                # forecast data is too old - older than start_datetime with time set to 00:00:00
                self.elecprice_35days[dt.hour, dt.day_of_week] = price_wh
                continue
            self.elecprice_35days[dt.hour, 34] = price_wh  # Update today's price

            self.update_value(dt, "elecprice_marketprice_wh", price_wh)

        # Update 8day cache
        elecprice_cache_file.seek(0)
        np.save(elecprice_cache_file, self.elecprice_35days)

        # Check for new/ valid forecast data
        if len(self) == 0:
            # Got no valid forecast data
            return

        # Assure price starts at start_time
        while compare_datetimes(self[0].date_time, self.start_datetime).gt:
            # Repeat the mean on the 8 day array to cover the missing hours
            dt = self[0].date_time.subtract(hours=1)  # type: ignore
            value = self._calculate_weighted_mean(dt.day_of_week, dt.hour)

            record = ElecPriceDataRecord(
                date_time=dt,
                elecprice_marketprice_wh=value,
            )
            self.insert(0, record)
        # Assure price ends at end_time
        while compare_datetimes(self[-1].date_time, self.end_datetime).lt:
            # Repeat the mean on the 8 day array to cover the missing hours
            dt = self[-1].date_time.add(hours=1)  # type: ignore
            value = self._calculate_weighted_mean(dt.day_of_week, dt.hour)
            record = ElecPriceDataRecord(
                date_time=dt,
                elecprice_marketprice_wh=value,
            )
            self.append(record)
