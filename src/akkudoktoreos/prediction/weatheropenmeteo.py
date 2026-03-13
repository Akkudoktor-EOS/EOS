"""Retrieves and processes weather forecast data from Open-Meteo.

This module provides classes and mappings to manage weather data obtained from the
Open-Meteo API, including support for various weather attributes such as temperature,
humidity, cloud cover, and solar irradiance. The data is mapped to the `WeatherDataRecord`
format, enabling consistent access to forecasted and historical weather attributes.
"""

from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
import pvlib
import requests
from loguru import logger

from akkudoktoreos.core.cache import cache_in_file
from akkudoktoreos.prediction.weatherabc import WeatherDataRecord, WeatherProvider
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration

WeatherDataOpenMeteoMapping: List[Tuple[str, Optional[str], Optional[Union[str, float]]]] = [
    # openmeteo_key, description, corr_factor
    ("time", "DateTime", "to datetime in timezone"),
    ("temperature_2m", "Temperature (°C)", 1),
    ("relative_humidity_2m", "Relative Humidity (%)", 1),
    ("precipitation", "Precipitation Amount (mm)", 1),
    ("rain", None, None),
    ("showers", None, None),
    ("snowfall", None, None),
    ("weather_code", None, None),
    ("cloud_cover", "Total Clouds (% Sky Obscured)", 1),
    ("cloud_cover_low", "Low Clouds (% Sky Obscured)", 1),
    ("cloud_cover_mid", "Medium Clouds (% Sky Obscured)", 1),
    ("cloud_cover_high", "High Clouds (% Sky Obscured)", 1),
    ("pressure_msl", "Pressure (mb)", 0.01),  # Pa to hPa
    ("surface_pressure", None, None),
    ("wind_speed_10m", "Wind Speed (kmph)", 3.6),  # m/s to km/h
    ("wind_direction_10m", "Wind Direction (°)", 1),
    ("wind_gusts_10m", "Wind Gust Speed (kmph)", 3.6),  # m/s to km/h
    ("shortwave_radiation", "Global Horizontal Irradiance (W/m2)", 1),
    ("direct_radiation", "Direct Normal Irradiance (W/m2)", 1),
    ("diffuse_radiation", "Diffuse Horizontal Irradiance (W/m2)", 1),
    ("direct_normal_irradiance", None, None),
    ("global_tilted_irradiance", None, None),
    ("terrestrial_radiation", None, None),
    ("shortwave_radiation_instant", None, None),
    ("direct_radiation_instant", None, None),
    ("diffuse_radiation_instant", None, None),
    ("direct_normal_irradiance_instant", None, None),
    ("global_tilted_irradiance_instant", None, None),
    ("terrestrial_radiation_instant", None, None),
    ("dew_point_2m", "Dew Point (°C)", 1),
    ("apparent_temperature", "Feels Like (°C)", 1),
    ("precipitation_probability", "Precipitation Probability (%)", 1),
    ("visibility", "Visibility (m)", 1),
    ("cape", None, None),
    ("evapotranspiration", None, None),
    ("et0_fao_evapotranspiration", None, None),
    ("vapour_pressure_deficit", None, None),
    ("soil_temperature_0_to_7cm", None, None),
    ("soil_temperature_7_to_28cm", None, None),
    ("soil_temperature_28_to_100cm", None, None),
    ("soil_temperature_100_to_255cm", None, None),
    ("soil_moisture_0_to_7cm", None, None),
    ("soil_moisture_7_to_28cm", None, None),
    ("soil_moisture_28_to_100cm", None, None),
    ("soil_moisture_100_to_255cm", None, None),
    ("sunshine_duration", None, None),  # seconds
]
"""Mapping of Open-Meteo weather data keys to WeatherDataRecord field descriptions.

Each tuple represents a field in the Open-Meteo data, with:

- The Open-Meteo field key,
- The corresponding `WeatherDataRecord` description, if applicable,
- A correction factor for unit or value scaling.

Fields without descriptions or correction factors are mapped to `None`.
"""


class WeatherOpenMeteo(WeatherProvider):
    """Fetch and process weather forecast data from Open-Meteo.

    WeatherOpenMeteo is a singleton-based class that retrieves weather forecast data
    from the Open-Meteo API and maps it to `WeatherDataRecord` fields, applying
    any necessary scaling or unit corrections. It manages the forecast over a range
    of hours into the future and retains historical data.

    Attributes:
        hours (int, optional): Number of hours in the future for the forecast.
        historic_hours (int, optional): Number of past hours for retaining data.
        latitude (float, optional): The latitude in degrees, validated to be between -90 and 90.
        longitude (float, optional): The longitude in degrees, validated to be between -180 and 180.
        start_datetime (datetime, optional): Start datetime for forecasts, defaults to the current
            datetime.
        end_datetime (datetime, computed): The forecast's end datetime, computed based on
            `start_datetime` and `hours`.
        keep_datetime (datetime, computed): The datetime to retain historical data, computed from
            `start_datetime` and `historic_hours`.

    Methods:
        provider_id(): Returns a unique identifier for the provider.
        _request_forecast(): Fetches the forecast from the Open-Meteo API.
        _update_data(): Processes and updates forecast data from Open-Meteo in WeatherDataRecord format.
    """

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the Open-Meteo provider."""
        return "OpenMeteo"

    @cache_in_file(with_ttl="1 hour")
    def _request_forecast(self) -> dict:
        """Fetch weather forecast data from Open-Meteo API.

        This method sends a request to Open-Meteo's API to retrieve forecast data
        for a specified date range and location. The response data is parsed and
        returned as JSON for further processing.

        Returns:
            dict: The parsed JSON response from Open-Meteo API containing forecast data.

        Raises:
            ValueError: If the API response does not include expected `hourly` data.
        """
        source = "https://api.open-meteo.com/v1/forecast"

        # Parameters for Open-Meteo API
        params = {
            "latitude": self.config.general.latitude,
            "longitude": self.config.general.longitude,
            "hourly": [
                "temperature_2m",
                "relative_humidity_2m",
                "precipitation",
                "rain",
                "showers",
                "snowfall",
                "weather_code",
                "cloud_cover",
                "cloud_cover_low",
                "cloud_cover_mid",
                "cloud_cover_high",
                "pressure_msl",
                "surface_pressure",
                "wind_speed_10m",
                "wind_direction_10m",
                "wind_gusts_10m",
                "shortwave_radiation",  # GHI
                "direct_radiation",  # DNI
                "diffuse_radiation",  # DHI
                "dew_point_2m",
                "apparent_temperature",
                "precipitation_probability",
                "visibility",
                "sunshine_duration",
            ],
            "timezone": self.config.general.timezone,
        }

        # Calculate the number of days between start and end
        start_dt = to_datetime(self.ems_start_datetime)
        end_dt = to_datetime(self.end_datetime)
        days_diff = (end_dt - start_dt).days + 1  # +1 for inclusive range

        # Open-Meteo has a maximum of 16 days
        forecast_days = min(days_diff, 16)

        # Decide whether we need forecast or historical data
        now = to_datetime(in_timezone=self.config.general.timezone)

        if start_dt.date() >= now.date():
            # Future data - use forecast_days
            params["forecast_days"] = forecast_days
        else:
            # Historical data - use start_date and end_date
            params["start_date"] = start_dt.strftime("%Y-%m-%d")
            params["end_date"] = end_dt.strftime("%Y-%m-%d")
            # For historical data we must specify the forecast model
            params["models"] = "best_match"  # or specific e.g. "dwd", "icon", etc.

        logger.debug(f"Open-Meteo Request params: {params}")

        response = requests.get(source, params=params, timeout=10)
        response.raise_for_status()  # Raise an error for bad responses
        logger.debug(f"Response from {source}: {response.status_code}")

        openmeteo_data = response.json()

        if "hourly" not in openmeteo_data:
            error_msg = f"Open-Meteo schema change. `hourly` expected to be part of Open-Meteo data: {openmeteo_data}."
            logger.error(error_msg)
            raise ValueError(error_msg)

        # We are working with fresh data (no cache), report update time
        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)
        return openmeteo_data

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

        Retrieves data from Open-Meteo, maps each Open-Meteo field to the corresponding
        `WeatherDataRecord` attribute using `WeatherDataOpenMeteoMapping`, and applies
        any necessary scaling. Open-Meteo provides direct GHI, DNI, and DHI values which
        are used directly without additional calculation. The final mapped and processed
        data is inserted into the sequence as `WeatherDataRecord`.
        """
        # Retrieve Open-Meteo weather data for the given coordinates
        openmeteo_data = self._request_forecast(force_update=force_update)  # type: ignore

        # Create key mapping from the description
        openmeteo_key_mapping: Dict[str, Tuple[Optional[str], Optional[Union[str, float]]]] = {}
        for openmeteo_key, description, corr_factor in WeatherDataOpenMeteoMapping:
            if description is None:
                openmeteo_key_mapping[openmeteo_key] = (None, None)
                continue
            weatherdata_key = WeatherDataRecord.key_from_description(description)
            if weatherdata_key is None:
                # Should not occur
                error_msg = f"No WeatherDataRecord key for description '{description}'"
                logger.error(error_msg)
                raise ValueError(error_msg)
            openmeteo_key_mapping[openmeteo_key] = (weatherdata_key, corr_factor)

        # Extract timestamps and values from Open-Meteo response
        hourly_data = openmeteo_data["hourly"]
        timestamps = hourly_data["time"]

        logger.info("Using direct radiation values from Open-Meteo (GHI, DNI, DHI)")

        # Process the data for each timestamp
        for idx, timestamp in enumerate(timestamps):
            weather_record = WeatherDataRecord()

            for openmeteo_key, item in openmeteo_key_mapping.items():
                key = item[0]
                if key is None:
                    continue

                # Take value from hourly data, if available
                if openmeteo_key in hourly_data:
                    value = hourly_data[openmeteo_key][idx]
                else:
                    value = None

                corr_factor = item[1]

                if value is not None:
                    if corr_factor == "to datetime in timezone":
                        value = to_datetime(value, in_timezone=self.config.general.timezone)
                    elif isinstance(corr_factor, (int, float)):
                        value = value * corr_factor

                setattr(weather_record, key, value)

            self.insert_by_datetime(weather_record)

        # Check whether radiation values exist (for logging)
        description_ghi = "Global Horizontal Irradiance (W/m2)"
        ghi_series = self._description_to_series(description_ghi)

        if ghi_series.isnull().all():
            logger.warning("No GHI data received from Open-Meteo")
        else:
            logger.debug(
                f"GHI data successfully loaded from Open-Meteo. Range: {ghi_series.min():.1f} - {ghi_series.max():.1f} W/m²"
            )

        # Add Precipitable Water (PWAT) using PVLib method
        key = WeatherDataRecord.key_from_description("Temperature (°C)")
        assert key  # noqa: S101
        temperature = self.key_to_array(
            key=key,
            start_datetime=self.ems_start_datetime,
            end_datetime=self.end_datetime,
            interval=to_duration("1 hour"),
        )
        if any(x is None or isinstance(x, float) and np.isnan(x) for x in temperature):
            # PWAT cannot be calculated
            debug_msg = f"Invalid temperature '{temperature}'"
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
            # PWAT cannot be calculated
            debug_msg = f"Invalid humidity '{humidity}'"
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
        description = "Precipitable Water (cm)"
        self._description_from_series(description, pwat)
