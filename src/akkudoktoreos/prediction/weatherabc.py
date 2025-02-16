"""Abstract and base classes for weather predictions.

Notes:
    - Supported weather sources can be expanded by adding new fetch methods within the
      WeatherForecast class.
    - Ensure appropriate API keys or configurations are set up if required by external data sources.
"""

from abc import abstractmethod
from typing import List, Optional

import numpy as np
import pandas as pd
import pvlib
from pydantic import Field

from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.prediction.predictionabc import PredictionProvider, PredictionRecord

logger = get_logger(__name__)


class WeatherDataRecord(PredictionRecord):
    """Represents a weather data record containing various weather attributes at a specific datetime.

    Attributes:
        date_time (Optional[AwareDatetime]): The datetime of the record.
        total_clouds (Optional[float]): Total cloud cover as a percentage of the sky obscured.
        low_clouds (Optional[float]): Cloud cover in the lower atmosphere (% sky obscured).
        medium_clouds (Optional[float]): Cloud cover in the middle atmosphere (% sky obscured).
        high_clouds (Optional[float]): Cloud cover in the upper atmosphere (% sky obscured).
        visibility (Optional[float]): Horizontal visibility in meters.
        fog (Optional[float]): Fog cover percentage.
        precip_type (Optional[str]): Type of precipitation (e.g., "rain", "snow").
        precip_prob (Optional[float]): Probability of precipitation as a percentage.
        precip_amt (Optional[float]): Precipitation amount in millimeters.
        preciptable_water (Optional[float]): Precipitable water in centimeters.
        wind_speed (Optional[float]): Wind speed in kilometers per hour.
        wind_direction (Optional[float]): Wind direction in degrees (0-360°).
        frost_chance (Optional[str]): Probability of frost.
        temp_air (Optional[float]): Air temperature in degrees Celsius.
        feels_like (Optional[float]): Feels-like temperature in degrees Celsius.
        dew_point (Optional[float]): Dew point in degrees Celsius.
        relative_humidity (Optional[float]): Relative humidity in percentage.
        pressure (Optional[float]): Atmospheric pressure in millibars.
        ozone (Optional[float]): Ozone concentration in Dobson units.
        ghi (Optional[float]): Global Horizontal Irradiance in watts per square meter (W/m²).
        dni (Optional[float]): Direct Normal Irradiance in watts per square meter (W/m²).
        dhi (Optional[float]): Diffuse Horizontal Irradiance in watts per square meter (W/m²).
    """

    weather_total_clouds: Optional[float] = Field(
        default=None, description="Total Clouds (% Sky Obscured)"
    )
    weather_low_clouds: Optional[float] = Field(
        default=None, description="Low Clouds (% Sky Obscured)"
    )
    weather_medium_clouds: Optional[float] = Field(
        default=None, description="Medium Clouds (% Sky Obscured)"
    )
    weather_high_clouds: Optional[float] = Field(
        default=None, description="High Clouds (% Sky Obscured)"
    )
    weather_visibility: Optional[float] = Field(default=None, description="Visibility (m)")
    weather_fog: Optional[float] = Field(default=None, description="Fog (%)")
    weather_precip_type: Optional[str] = Field(default=None, description="Precipitation Type")
    weather_precip_prob: Optional[float] = Field(
        default=None, description="Precipitation Probability (%)"
    )
    weather_precip_amt: Optional[float] = Field(
        default=None, description="Precipitation Amount (mm)"
    )
    weather_preciptable_water: Optional[float] = Field(
        default=None, description="Precipitable Water (cm)"
    )
    weather_wind_speed: Optional[float] = Field(default=None, description="Wind Speed (kmph)")
    weather_wind_direction: Optional[float] = Field(default=None, description="Wind Direction (°)")
    weather_frost_chance: Optional[str] = Field(default=None, description="Chance of Frost")
    weather_temp_air: Optional[float] = Field(default=None, description="Temperature (°C)")
    weather_feels_like: Optional[float] = Field(default=None, description="Feels Like (°C)")
    weather_dew_point: Optional[float] = Field(default=None, description="Dew Point (°C)")
    weather_relative_humidity: Optional[float] = Field(
        default=None, description="Relative Humidity (%)"
    )
    weather_pressure: Optional[float] = Field(default=None, description="Pressure (mb)")
    weather_ozone: Optional[float] = Field(default=None, description="Ozone (du)")
    weather_ghi: Optional[float] = Field(
        default=None, description="Global Horizontal Irradiance (W/m2)"
    )
    weather_dni: Optional[float] = Field(
        default=None, description="Direct Normal Irradiance (W/m2)"
    )
    weather_dhi: Optional[float] = Field(
        default=None, description="Diffuse Horizontal Irradiance (W/m2)"
    )


class WeatherProvider(PredictionProvider):
    """Abstract base class for weather providers.

    WeatherProvider is a thread-safe singleton, ensuring only one instance of this class is created.

    Configuration variables:
        provider (str): Prediction provider for weather.

    Attributes:
        hours (int, optional): The number of hours into the future for which predictions are generated.
        historic_hours (int, optional): The number of past hours for which historical data is retained.
        latitude (float, optional): The latitude in degrees, must be within -90 to 90.
        longitude (float, optional): The longitude in degrees, must be within -180 to 180.
        start_datetime (datetime, optional): The starting datetime for predictions, defaults to the current datetime if unspecified.
        end_datetime (datetime, computed): The datetime representing the end of the prediction range,
            calculated based on `start_datetime` and `hours`.
        keep_datetime (datetime, computed): The earliest datetime for retaining historical data, calculated
            based on `start_datetime` and `historic_hours`.
    """

    # overload
    records: List[WeatherDataRecord] = Field(
        default_factory=list, description="List of WeatherDataRecord records"
    )

    @classmethod
    @abstractmethod
    def provider_id(cls) -> str:
        return "WeatherProvider"

    def enabled(self) -> bool:
        return self.provider_id() == self.config.weather.provider

    @classmethod
    def estimate_irradiance_from_cloud_cover(
        cls, lat: float, lon: float, cloud_cover: pd.Series, offset: int = 35
    ) -> tuple:
        """Estimates irradiance values (GHI, DNI, DHI) based on cloud cover.

        This method estimates solar irradiance in several steps:
        1. **Clear Sky GHI Calculation**: Determines the Global Horizontal Irradiance (GHI) under clear sky conditions using the Ineichen model and climatological turbidity data.
        2. **Cloudy Sky GHI Estimation**: Adjusts the clear sky GHI based on the provided cloud cover percentage to estimate cloudy sky GHI.
        3. **Direct Normal Irradiance (DNI) Estimation**: Uses the DISC model to estimate the DNI from the adjusted GHI.
        4. **Diffuse Horizontal Irradiance (DHI) Calculation**: Computes DHI from the estimated GHI and DNI values.

        Args:
            lat (float): Latitude of the location for irradiance estimation.
            lon (float): Longitude of the location for irradiance estimation.
            cloud_cover (pd.Series): Series of cloud cover values (0-100%) indexed by datetime.
            offset (Optional[sint]): Baseline for GHI adjustment as a percentage (default is 35).

        Returns:
            tuple: Lists of estimated irradiance values in the order of GHI, DNI, and DHI.

        Note:
            This method is based on the implementation from PVLib and is adapted from
            https://github.com/davidusb-geek/emhass/blob/master/src/emhass/forecast.py (MIT License).
        """
        # Adjust offset percentage to scaling factor
        offset_fraction = offset / 100.0

        # Get cloud cover datetimes
        cloud_cover_times = cloud_cover.index

        # Create a location object
        location = pvlib.location.Location(latitude=lat, longitude=lon)

        # Get solar position and clear-sky GHI using the Ineichen model
        solpos = location.get_solarposition(cloud_cover_times)
        clear_sky = location.get_clearsky(cloud_cover_times, model="ineichen")

        # Convert cloud cover percentage to a scaling factor
        cloud_cover_fraction = np.array(cloud_cover) / 100.0

        # Calculate adjusted GHI with proportional offset adjustment
        adjusted_ghi = clear_sky["ghi"] * (
            offset_fraction + (1 - offset_fraction) * (1 - cloud_cover_fraction)
        )
        adjusted_ghi.fillna(0.0, inplace=True)

        # Apply DISC model to estimate Direct Normal Irradiance (DNI) from adjusted GHI
        disc_output = pvlib.irradiance.disc(adjusted_ghi, solpos["zenith"], cloud_cover_times)
        adjusted_dni = disc_output["dni"]
        adjusted_dni.fillna(0.0, inplace=True)

        # Calculate Diffuse Horizontal Irradiance (DHI) as DHI = GHI - DNI * cos(zenith)
        zenith_rad = np.radians(solpos["zenith"])
        adjusted_dhi = adjusted_ghi - adjusted_dni * np.cos(zenith_rad)
        adjusted_dhi.fillna(0.0, inplace=True)

        # Return GHI, DNI, DHI lists
        ghi = adjusted_ghi.to_list()
        dni = adjusted_dni.to_list()
        dhi = adjusted_dhi.to_list()
        return ghi, dni, dhi

    @classmethod
    def estimate_preciptable_water(
        cls, temperature: pd.Series, relative_humidity: pd.Series
    ) -> pd.Series:
        return pvlib.atmosphere.gueymard94_pw(temperature, relative_humidity)
