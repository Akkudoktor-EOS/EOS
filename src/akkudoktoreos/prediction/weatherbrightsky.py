"""Retrieves and processes weather forecast data from BrightSky.

This module provides classes and mappings to manage weather data obtained from the
BrightSky API, including support for various weather attributes such as temperature,
humidity, cloud cover, and solar irradiance. The data is mapped to the `WeatherDataRecord`
format, enabling consistent access to forecasted and historical weather attributes.
"""

import json
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
import pvlib
import requests
from loguru import logger

from akkudoktoreos.core.cache import cache_in_file
from akkudoktoreos.prediction.weatherabc import WeatherDataRecord, WeatherProvider
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration

WheaterDataBrightSkyMapping: List[Tuple[str, Optional[str], Optional[Union[str, float]]]] = [
    # brightsky_key, description, corr_factor
    ("timestamp", "DateTime", "to datetime in timezone"),
    ("precipitation", "Precipitation Amount (mm)", 1),
    ("pressure_msl", "Pressure (mb)", 1),
    ("sunshine", None, None),
    ("temperature", "Temperature (°C)", 1),
    ("wind_direction", "Wind Direction (°)", 1),
    ("wind_speed", "Wind Speed (kmph)", 1),
    ("cloud_cover", "Total Clouds (% Sky Obscured)", 1),
    ("dew_point", "Dew Point (°C)", 1),
    ("relative_humidity", "Relative Humidity (%)", 1),
    ("visibility", "Visibility (m)", 1),
    ("wind_gust_direction", None, None),
    ("wind_gust_speed", None, None),
    ("condition", None, None),
    ("precipitation_probability", "Precipitation Probability (%)", 1),
    ("precipitation_probability_6h", None, None),
    ("solar", "Global Horizontal Irradiance (W/m2)", 1000),
    ("fallback_source_ids", None, None),
    ("icon", None, None),
]
"""Mapping of BrightSky weather data keys to WeatherDataRecord field descriptions.

Each tuple represents a field in the BrightSky data, with:
    - The BrightSky field key,
    - The corresponding `WeatherDataRecord` description, if applicable,
    - A correction factor for unit or value scaling.
Fields without descriptions or correction factors are mapped to `None`.
"""


class WeatherBrightSky(WeatherProvider):
    """Fetch and process weather forecast data from BrightSky.

    WeatherBrightSky is a singleton-based class that retrieves weather forecast data
    from the BrightSky API and maps it to `WeatherDataRecord` fields, applying
    any necessary scaling or unit corrections. It manages the forecast over a range
    of hours into the future and retains historical data.

    Attributes:
        hours (int, optional): Number of hours in the future for the forecast.
        historic_hours (int, optional): Number of past hours for retaining data.
        latitude (float, optional): The latitude in degrees, validated to be between -90 and 90.
        longitude (float, optional): The longitude in degrees, validated to be between -180 and 180.
        start_datetime (datetime, optional): Start datetime for forecasts, defaults to the current datetime.
        end_datetime (datetime, computed): The forecast's end datetime, computed based on `start_datetime` and `hours`.
        keep_datetime (datetime, computed): The datetime to retain historical data, computed from `start_datetime` and `historic_hours`.

    Methods:
        provider_id(): Returns a unique identifier for the provider.
        _request_forecast(): Fetches the forecast from the BrightSky API.
        _update_data(): Processes and updates forecast data from BrightSky in WeatherDataRecord format.
    """

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the BrightSky provider."""
        return "BrightSky"

    @cache_in_file(with_ttl="1 hour")
    def _request_forecast(self) -> dict:
        """Fetch weather forecast data from BrightSky API.

        This method sends a request to BrightSky's API to retrieve forecast data
        for a specified date range and location. The response data is parsed and
        returned as JSON for further processing.

        Returns:
            dict: The parsed JSON response from BrightSky API containing forecast data.

        Raises:
            ValueError: If the API response does not include expected `weather` data.
        """
        source = "https://api.brightsky.dev"
        date = to_datetime(self.ems_start_datetime, as_string=True)
        last_date = to_datetime(self.end_datetime, as_string=True)
        response = requests.get(
            f"{source}/weather?lat={self.config.general.latitude}&lon={self.config.general.longitude}&date={date}&last_date={last_date}&tz={self.config.general.timezone}",
            timeout=10,
        )
        response.raise_for_status()  # Raise an error for bad responses
        logger.debug(f"Response from {source}: {response}")
        brightsky_data = json.loads(response.content)
        if "weather" not in brightsky_data:
            error_msg = f"BrightSky schema change. `wheather`expected to be part of BrightSky data: {brightsky_data}."
            logger.error(error_msg)
            raise ValueError(error_msg)
        # We are working on fresh data (no cache), report update time
        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)
        return brightsky_data

    def _description_to_series(self, description: str) -> pd.Series:
        """Retrieve a pandas Series corresponding to a weather data description.

        This method fetches the key associated with the provided description
        and retrieves the data series mapped to that key. If the description
        does not correspond to a valid key, a `ValueError` is raised.

        Args:
            description (str): The description of the WeatherDataRecord to retrieve.

        Returns:
            pd.Series: The data series corresponding to the description.

        Raises:
            ValueError: If no key is found for the provided description.
        """
        key = WeatherDataRecord.key_from_description(description)
        if key is None:
            error_msg = f"No WeatherDataRecord key for '{description}'"
            logger.error(error_msg)
            raise ValueError(error_msg)
        series = self.key_to_series(key)
        return series

    def _description_from_series(self, description: str, data: pd.Series) -> None:
        """Update a weather data with a pandas Series based on its description.

        This method fetches the key associated with the provided description
        and updates the weather data with the provided data series. If the description
        does not correspond to a valid key, a `ValueError` is raised.

        Args:
            description (str): The description of the weather data to update.
            data (pd.Series): The pandas Series containing the data to update.

        Raises:
            ValueError: If no key is found for the provided description.
        """
        key = WeatherDataRecord.key_from_description(description)
        if key is None:
            error_msg = f"No WeatherDataRecord key for '{description}'"
            logger.error(error_msg)
            raise ValueError(error_msg)
        self.key_from_series(key, data)

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update forecast data in the WeatherDataRecord format.

        Retrieves data from BrightSky, maps each BrightSky field to the corresponding
        `WeatherDataRecord` attribute using `WheaterDataBrightSkyMapping`, and applies
        any necessary scaling. Forecast data such as cloud cover, temperature, and
        humidity is further processed to estimate solar irradiance and precipitable water.

        The final mapped and processed data is inserted into the sequence as `WeatherDataRecord`.
        """
        # Get BrightSky weather data for the given coordinates
        brightsky_data = self._request_forecast(force_update=force_update)  # type: ignore

        # Get key mapping from description
        brightsky_key_mapping: Dict[str, Tuple[Optional[str], Optional[Union[str, float]]]] = {}
        for brightsky_key, description, corr_factor in WheaterDataBrightSkyMapping:
            if description is None:
                brightsky_key_mapping[brightsky_key] = (None, None)
                continue
            weatherdata_key = WeatherDataRecord.key_from_description(description)
            if weatherdata_key is None:
                # Should not happen
                error_msg = "No WeatherDataRecord key for 'description'"
                logger.error(error_msg)
                raise ValueError(error_msg)
            brightsky_key_mapping[brightsky_key] = (weatherdata_key, corr_factor)

        for brightsky_record in brightsky_data["weather"]:
            weather_record = WeatherDataRecord()
            for brightsky_key, item in brightsky_key_mapping.items():
                key = item[0]
                if key is None:
                    continue
                value = brightsky_record[brightsky_key]
                corr_factor = item[1]
                if value and corr_factor:
                    if corr_factor == "to datetime in timezone":
                        value = to_datetime(value, in_timezone=self.config.general.timezone)
                    else:
                        value = value * corr_factor
                setattr(weather_record, key, value)
            self.insert_by_datetime(weather_record)

        # Converting the cloud cover into Irradiance (GHI, DNI, DHI)
        description = "Total Clouds (% Sky Obscured)"
        cloud_cover = self._description_to_series(description)
        ghi, dni, dhi = self.estimate_irradiance_from_cloud_cover(
            self.config.general.latitude, self.config.general.longitude, cloud_cover
        )

        description = "Global Horizontal Irradiance (W/m2)"
        ghi = pd.Series(data=ghi, index=cloud_cover.index)
        self._description_from_series(description, ghi)

        description = "Direct Normal Irradiance (W/m2)"
        dni = pd.Series(data=dni, index=cloud_cover.index)
        self._description_from_series(description, dni)

        description = "Diffuse Horizontal Irradiance (W/m2)"
        dhi = pd.Series(data=dhi, index=cloud_cover.index)
        self._description_from_series(description, dhi)

        # Add Preciptable Water (PWAT) with a PVLib method.
        key = WeatherDataRecord.key_from_description("Temperature (°C)")
        assert key  # noqa: S101
        temperature = self.key_to_array(
            key=key,
            start_datetime=self.ems_start_datetime,
            end_datetime=self.end_datetime,
            interval=to_duration("1 hour"),
        )
        if any(x is None or isinstance(x, float) and np.isnan(x) for x in temperature):
            # We can not calculate PWAT
            debug_msg = f"Innvalid temperature '{temperature}'"
            logger.debug(debug_msg)
            return
        key = WeatherDataRecord.key_from_description("Relative Humidity (%)")
        assert key  # noqa: S101
        humidity = self.key_to_array(
            key=key,
            start_datetime=self.ems_start_datetime,
            end_datetime=self.end_datetime,
            interval=to_duration("1 hour"),
        )
        if any(x is None or isinstance(x, float) and np.isnan(x) for x in humidity):
            # We can not calculate PWAT
            debug_msg = f"Innvalid humidity '{humidity}'"
            logger.debug(debug_msg)
            return
        data = pvlib.atmosphere.gueymard94_pw(temperature, humidity)
        pwat = pd.Series(
            data=data,
            index=pd.DatetimeIndex(
                pd.date_range(
                    start=self.ems_start_datetime,
                    end=self.end_datetime,
                    freq="1h",
                    inclusive="left",
                )
            ),
        )
        description = "Preciptable Water (cm)"
        self._description_from_series(description, pwat)
