"""PV Power Forecasting with Akkudoktor.

This module provides classes and methods to retrieve, process, and display photovoltaic (PV) power forecast data. It includes features for working with environmental data such as temperature, wind speed, DC power, and AC power. Data retrieval is designed to work with Akkudoktor.net, and caching is implemented to reduce redundant network requests. Additionally, the module supports management of historical data for analysis over time.

Classes:
    AkkudoktorForecastHorizon: Represents details about the orientation of PV system horizons.
    AkkudoktorForecastMeta: Metadata configuration for the forecast, including location, system settings, and timezone.
    AkkudoktorForecastValue: Represents a single forecast data entry with information on temperature, wind speed, and solar orientation.
    AkkudoktorForecast: The main container for forecast data, holding both metadata and individual forecast entries.
    PVForecastAkkudoktorDataRecord: A specialized data record format for PV forecast data, including forecasted and actual AC power measurements.
    PVForecastAkkudoktorSettings: Contains configuration settings for constructing the Akkudoktor forecast API URL.
    PVForecastAkkudoktor: Primary class to manage PV power forecasts, handle data retrieval, caching, and integration with Akkudoktor.net.

Example:
    # Set up the configuration with necessary fields for URL generation
    settings_data = {
        "prediction_hours": 48,
        "prediction_historic_hours": 24,
        "latitude": 52.52,
        "longitude": 13.405,
        "pvforecast_provider": "Akkudoktor",
        "pvforecast0_peakpower": 5.0,
        "pvforecast0_surface_azimuth": -10,
        "pvforecast0_surface_tilt": 7,
        "pvforecast0_userhorizon": [20, 27, 22, 20],
        "pvforecast0_inverter_paco": 10000,
        "pvforecast1_peakpower": 4.8,
        "pvforecast1_surface_azimuth": -90,
        "pvforecast1_surface_tilt": 7,
        "pvforecast1_userhorizon": [30, 30, 30, 50],
        "pvforecast1_inverter_paco": 10000,
    }

    # Create the config instance from the provided data
    config = PVForecastAkkudoktorSettings(**settings_data)

    # Initialize the forecast object with the generated configuration
    forecast = PVForecastAkkudoktor(settings=config)

    # Get an actual forecast
    forecast.update_data()

    # Update the AC power measurement for a specific date and time
    forecast.update_value(to_datetime(None, to_maxtime=False), "pvforecastakkudoktor_ac_power_measured", 1000.0)

    # Report the DC and AC power forecast along with AC measurements
    print(forecast.report_ac_power_and_measurement())

Attributes:
    prediction_hours (int): Number of hours into the future to forecast. Default is 48.
    prediction_historic_hours (int): Number of past hours to retain for analysis. Default is 24.
    latitude (float): Latitude for the forecast location.
    longitude (float): Longitude for the forecast location.
    start_datetime (datetime): Start time for the forecast, defaulting to current datetime.
    end_datetime (datetime): Computed end datetime based on `start_datetime` and `prediction_hours`.
    keep_datetime (datetime): Computed threshold datetime for retaining historical data.

Methods:
    provider_id(): Returns the unique identifier for the Akkudoktor provider.
    _request_forecast(): Retrieves forecast data from the Akkudoktor API.
    _update_data(): Updates forecast data within the PVForecastAkkudoktorDataRecord structure.
    report_ac_power_and_measurement(): Generates a report on AC and DC power forecasts and actual measurements.

"""

from typing import Any, List, Optional, Union

import requests
from pydantic import Field, ValidationError, computed_field

from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.prediction.pvforecastabc import (
    PVForecastDataRecord,
    PVForecastProvider,
)
from akkudoktoreos.utils.cacheutil import cache_in_file
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime

logger = get_logger(__name__)


class AkkudoktorForecastHorizon(PydanticBaseModel):
    altitude: int
    azimuthFrom: int
    azimuthTo: int


class AkkudoktorForecastMeta(PydanticBaseModel):
    lat: float
    lon: float
    power: List[int]
    azimuth: List[int]
    tilt: List[int]
    timezone: str
    albedo: float
    past_days: int
    inverterEfficiency: float
    powerInverter: List[int]
    cellCoEff: float
    range: bool
    horizont: List[List[AkkudoktorForecastHorizon]]
    horizontString: List[str]


class AkkudoktorForecastValue(PydanticBaseModel):
    datetime: str
    dcPower: float
    power: float
    sunTilt: float
    sunAzimuth: float
    temperature: Optional[float]
    relativehumidity_2m: Optional[float]
    windspeed_10m: Optional[float]


class AkkudoktorForecast(PydanticBaseModel):
    meta: AkkudoktorForecastMeta
    values: List[List[AkkudoktorForecastValue]]


class PVForecastAkkudoktorDataRecord(PVForecastDataRecord):
    """Represents a Akkudoktor specific pvforecast data record containing various pvforecast attributes at a specific datetime."""

    pvforecastakkudoktor_ac_power_measured: Optional[float] = Field(
        default=None, description="Total AC power measured (W)"
    )
    pvforecastakkudoktor_wind_speed_10m: Optional[float] = Field(
        default=None, description="Wind Speed 10m (kmph)"
    )
    pvforecastakkudoktor_temp_air: Optional[float] = Field(
        default=None, description="Temperature (°C)"
    )

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def pvforecastakkudoktor_ac_power_any(self) -> Optional[float]:
        """Returns the AC power.

        If a measured value is available, it returns the measured AC power;
        otherwise, it returns the forecasted AC power.

        Returns:
            float: AC power in watts or None if no forecast data is available.
        """
        if self.pvforecastakkudoktor_ac_power_measured is not None:
            return self.pvforecastakkudoktor_ac_power_measured
        else:
            return self.pvforecast_ac_power


class PVForecastAkkudoktor(PVForecastProvider):
    """Fetch and process PV forecast data from akkudoktor.net.

    PVForecastAkkudoktor is a singleton-based class that retrieves weather forecast data
    from the PVForecastAkkudoktor API and maps it to `PVForecastDataRecord` fields, applying
    any necessary scaling or unit corrections. It manages the forecast over a range
    of hours into the future and retains historical data.

    Attributes:
        prediction_hours (int, optional): Number of hours in the future for the forecast.
        prediction_historic_hours (int, optional): Number of past hours for retaining data.
        latitude (float, optional): The latitude in degrees, validated to be between -90 and 90.
        longitude (float, optional): The longitude in degrees, validated to be between -180 and 180.
        start_datetime (datetime, optional): Start datetime for forecasts, defaults to the current datetime.
        end_datetime (datetime, computed): The forecast's end datetime, computed based on `start_datetime` and `prediction_hours`.
        keep_datetime (datetime, computed): The datetime to retain historical data, computed from `start_datetime` and `prediction_historic_hours`.

    Methods:
        provider_id(): Returns a unique identifier for the provider.
        _request_forecast(): Fetches the forecast from the Akkudoktor API.
        _update_data(): Processes and updates forecast data from Akkudoktor in PVForecastDataRecord format.
    """

    # overload
    records: List[PVForecastAkkudoktorDataRecord] = Field(
        default_factory=list, description="List of PVForecastAkkudoktorDataRecord records"
    )

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the Akkudoktor provider."""
        return "PVForecastAkkudoktor"

    @classmethod
    def _validate_data(cls, json_str: Union[bytes, Any]) -> AkkudoktorForecast:
        """Validate Akkudoktor PV forecast data."""
        try:
            akkudoktor_data = AkkudoktorForecast.model_validate_json(json_str)
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

    def _url(self) -> str:
        """Build akkudoktor.net API request URL."""
        url = f"https://api.akkudoktor.net/forecast?lat={self.config.latitude}&lon={self.config.longitude}&"
        planes_peakpower = self.config.pvforecast_planes_peakpower
        planes_azimuth = self.config.pvforecast_planes_azimuth
        planes_tilt = self.config.pvforecast_planes_tilt
        planes_inverter_paco = self.config.pvforecast_planes_inverter_paco
        planes_userhorizon = self.config.pvforecast_planes_userhorizon
        for i, plane in enumerate(self.config.pvforecast_planes):
            url += f"power={int(planes_peakpower[i]*1000)}&"
            url += f"azimuth={int(planes_azimuth[i])}&"
            url += f"tilt={int(planes_tilt[i])}&"
            url += f"powerInverter={int(planes_inverter_paco[i])}&"
            url += "horizont="
            for horizon in planes_userhorizon[i]:
                url += f"{int(horizon)},"
            url = url[:-1]  # remove trailing comma
            url += "&"
        url += "past_days=5&cellCoEff=-0.36&inverterEfficiency=0.8&albedo=0.25&"
        url += f"timezone={self.config.timezone}&"
        url += "hourly=relativehumidity_2m%2Cwindspeed_10m"
        logger.debug(f"Akkudoktor URL: {url}")
        return url

    @cache_in_file(with_ttl="1 hour")
    def _request_forecast(self) -> AkkudoktorForecast:
        """Fetch PV forecast data from Akkudoktor API.

        This method sends a request to Akkudoktor API to retrieve forecast data
        for a specified date range and location. The response data is parsed and
        returned as JSON for further processing.

        Returns:
            dict: The parsed JSON response from Akkudoktor API containing forecast data.

        Raises:
            ValueError: If the API response does not include expected `meta` data.
        """
        response = requests.get(self._url())
        response.raise_for_status()  # Raise an error for bad responses
        logger.debug(f"Response from {self._url()}: {response}")
        akkudoktor_data = self._validate_data(response.content)
        # We are working on fresh data (no cache), report update time
        self.update_datetime = to_datetime(in_timezone=self.config.timezone)
        return akkudoktor_data

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update forecast data in the PVForecastAkkudoktorDataRecord format.

        Retrieves data from Akkudoktor. The processed data is inserted into the sequence as
        `PVForecastAkkudoktorDataRecord`.
        """
        # Assure we have something to request PV power for.
        if len(self.config.pvforecast_planes) == 0:
            # No planes for PV
            error_msg = "Requested PV forecast, but no planes configured."
            logger.error(f"Configuration error: {error_msg}")
            raise ValueError(error_msg)

        # Get Akkudoktor PV Forecast data for the given configuration.
        akkudoktor_data = self._request_forecast(force_update=force_update)  # type: ignore

        # Timezone of the PV system
        if self.config.timezone != akkudoktor_data.meta.timezone:
            error_msg = f"Configured timezone '{self.config.timezone}' does not match Akkudoktor timezone '{akkudoktor_data.meta.timezone}'."
            logger.error(f"Akkudoktor schema change: {error_msg}")
            raise ValueError(error_msg)

        # Assumption that all lists are the same length and are ordered chronologically
        # in ascending order and have the same timestamps.
        values_len = len(akkudoktor_data.values[0])
        if values_len < self.config.prediction_hours:
            # Expect one value set per prediction hour
            error_msg = (
                f"The forecast must cover at least {self.config.prediction_hours} hours, "
                f"but only {values_len} data sets are given in forecast data."
            )
            logger.error(f"Akkudoktor schema change: {error_msg}")
            raise ValueError(error_msg)

        for i in range(values_len):
            original_datetime = akkudoktor_data.values[0][i].datetime
            dt = to_datetime(original_datetime, in_timezone=self.config.timezone)

            # We provide prediction starting at start of day, to be compatible to old system.
            if compare_datetimes(dt, self.start_datetime.start_of("day")).lt:
                # forecast data is too old
                continue

            sum_dc_power = sum(values[i].dcPower for values in akkudoktor_data.values)
            sum_ac_power = sum(values[i].power for values in akkudoktor_data.values)

            data = {
                "pvforecast_dc_power": sum_dc_power,
                "pvforecast_ac_power": sum_ac_power,
                "pvforecastakkudoktor_wind_speed_10m": akkudoktor_data.values[0][i].windspeed_10m,
                "pvforecastakkudoktor_temp_air": akkudoktor_data.values[0][i].temperature,
            }
            self.update_value(dt, data)

        if len(self) < self.config.prediction_hours:
            raise ValueError(
                f"The forecast must cover at least {self.config.prediction_hours} hours, "
                f"but only {len(self)} hours starting from {self.start_datetime} "
                f"were predicted."
            )

    def report_ac_power_and_measurement(self) -> str:
        """Report DC/ AC power, and AC power measurement for each forecast hour.

        For each forecast entry, the time, DC power, forecasted AC power, measured AC power
        (if available), and the value returned by the `get_ac_power` method is provided.

        Returns:
            str: The report.
        """
        rep = ""
        for record in self.records:
            date_time = record.date_time
            dc_pow = round(record.pvforecast_dc_power, 2) if record.pvforecast_dc_power else None
            ac_pow = round(record.pvforecast_ac_power, 2) if record.pvforecast_ac_power else None
            ac_pow_measurement = (
                round(record.pvforecastakkudoktor_ac_power_measured, 2)
                if record.pvforecastakkudoktor_ac_power_measured
                else None
            )
            ac_pow_any = (
                round(record.pvforecastakkudoktor_ac_power_any, 2)
                if record.pvforecastakkudoktor_ac_power_any
                else None
            )
            rep += (
                f"Date&Time: {date_time}, DC: {dc_pow}, AC: {ac_pow}, "
                f"AC sampled: {ac_pow_measurement}, AC any: {ac_pow_any}"
                "\n"
            )
        return rep


# Example of how to use the PVForecastAkkudoktor class
if __name__ == "__main__":
    """Main execution block to demonstrate the use of the PVForecastAkkudoktor class.

    Sets up the forecast configuration fields, fetches PV power forecast data,
    updates the AC power measurement for the current date/time, and prints
    the DC and AC power information.
    """
    # Set up the configuration with necessary fields for URL generation
    settings_data = {
        "prediction_hours": 48,
        "prediction_historic_hours": 24,
        "latitude": 52.52,
        "longitude": 13.405,
        "pvforecast_provider": "PVForecastAkkudoktor",
        "pvforecast0_peakpower": 5.0,
        "pvforecast0_surface_azimuth": -10,
        "pvforecast0_surface_tilt": 7,
        "pvforecast0_userhorizon": [20, 27, 22, 20],
        "pvforecast0_inverter_paco": 10000,
        "pvforecast1_peakpower": 4.8,
        "pvforecast1_surface_azimuth": -90,
        "pvforecast1_surface_tilt": 7,
        "pvforecast1_userhorizon": [30, 30, 30, 50],
        "pvforecast1_inverter_paco": 10000,
        "pvforecast2_peakpower": 1.4,
        "pvforecast2_surface_azimuth": -40,
        "pvforecast2_surface_tilt": 60,
        "pvforecast2_userhorizon": [60, 30, 0, 30],
        "pvforecast2_inverter_paco": 2000,
        "pvforecast3_peakpower": 1.6,
        "pvforecast3_surface_azimuth": 5,
        "pvforecast3_surface_tilt": 45,
        "pvforecast3_userhorizon": [45, 25, 30, 60],
        "pvforecast3_inverter_paco": 1400,
    }

    # Initialize the forecast object with the generated configuration
    forecast = PVForecastAkkudoktor()

    # Get an actual forecast
    forecast.update_data()

    # Update the AC power measurement for a specific date and time
    forecast.update_value(
        to_datetime(None, to_maxtime=False), "pvforecastakkudoktor_ac_power_measured", 1000.0
    )

    # Report the DC and AC power forecast along with AC measurements
    print(forecast.report_ac_power_and_measurement())
