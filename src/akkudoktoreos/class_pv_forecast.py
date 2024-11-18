"""PV Power Forecasting Module.

This module contains classes and methods to retrieve, process, and display photovoltaic (PV)
power forecast data, including temperature, windspeed, DC power, and AC power forecasts.
The module supports caching of forecast data to reduce redundant network requests and includes
functions to update AC power measurements and retrieve forecasts within a specified date range.

Classes
    ForecastData: Represents a single forecast entry, including DC power, AC power,
                  temperature, and windspeed.
    PVForecast:   Retrieves, processes, and stores PV power forecast data, either from
                  a file or URL, with optional caching. It also provides methods to query
                  and update the forecast data, convert it to a DataFrame, and output key
                  metrics like AC power.

Example:
    # Initialize PVForecast class with an URL
    forecast = PVForecast(
        prediction_hours=24,
        url="https://api.akkudoktor.net/forecast?lat=50.8588&lon=7.3747..."
    )

    # Update the AC power measurement for a specific date and time
    forecast.update_ac_power_measurement(date_time=datetime.now(), ac_power_measurement=1000)

    # Print the forecast data with DC and AC power details
    forecast.print_ac_power_and_measurement()

    # Get the forecast data as a Pandas DataFrame
    df = forecast.get_forecast_dataframe()
    print(df)

Attributes:
    prediction_hours (int): Number of forecast hours. Defaults to 48.
"""

import json
from datetime import date, datetime
from typing import List, Optional, Union

import numpy as np
import pandas as pd
import requests
from pydantic import BaseModel, ValidationError

from akkudoktoreos.cachefilestore import cache_in_file
from akkudoktoreos.datetimeutil import to_datetime
from akkudoktoreos.logutil import get_logger

logger = get_logger(__name__, logging_level="DEBUG")


class AkkudoktorForecastHorizon(BaseModel):
    altitude: int
    azimuthFrom: int
    azimuthTo: int


class AkkudoktorForecastMeta(BaseModel):
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


class AkkudoktorForecastValue(BaseModel):
    datetime: str
    dcPower: float
    power: float
    sunTilt: float
    sunAzimuth: float
    temperature: float
    relativehumidity_2m: float
    windspeed_10m: float


class AkkudoktorForecast(BaseModel):
    meta: AkkudoktorForecastMeta
    values: List[List[AkkudoktorForecastValue]]


def validate_pv_forecast_data(data) -> str:
    """Validate PV forecast data."""
    data_type = None
    error_msg = ""

    try:
        AkkudoktorForecast.model_validate(data)
        data_type = "Akkudoktor"
    except ValidationError as e:
        for error in e.errors():
            field = " -> ".join(str(x) for x in error["loc"])
            message = error["msg"]
            error_type = error["type"]
            error_msg += f"Field: {field}\nError: {message}\nType: {error_type}\n"
        logger.debug(f"Validation did not succeed: {error_msg}")

    return data_type


class ForecastResponse(BaseModel):
    temperature: list[float]
    pvpower: list[float]


class ForecastData:
    """Stores forecast data for PV power and weather parameters.

    Attributes:
        date_time (datetime): The date and time of the forecast.
        dc_power (float): The direct current (DC) power in watts.
        ac_power (float): The alternating current (AC) power in watts.
        windspeed_10m (float, optional): Wind speed at 10 meters altitude.
        temperature (float, optional): Temperature in degrees Celsius.
        ac_power_measurement (float, optional): Measured AC power.
    """

    def __init__(
        self,
        date_time: datetime,
        dc_power: float,
        ac_power: float,
        windspeed_10m: Optional[float] = None,
        temperature: Optional[float] = None,
        ac_power_measurement: Optional[float] = None,
    ):
        """Initializes the ForecastData instance.

        Args:
            date_time (datetime): The date and time of the forecast.
            dc_power (float): The DC power in watts.
            ac_power (float): The AC power in watts.
            windspeed_10m (float, optional): Wind speed at 10 meters altitude. Defaults to None.
            temperature (float, optional): Temperature in degrees Celsius. Defaults to None.
            ac_power_measurement (float, optional): Measured AC power. Defaults to None.
        """
        self.date_time = date_time
        self.dc_power = dc_power
        self.ac_power = ac_power
        self.windspeed_10m = windspeed_10m
        self.temperature = temperature
        self.ac_power_measurement = ac_power_measurement

    def get_date_time(self) -> datetime:
        """Returns the forecast date and time.

        Returns:
            datetime: The date and time of the forecast.
        """
        return self.date_time

    def get_dc_power(self) -> float:
        """Returns the DC power.

        Returns:
            float: DC power in watts.
        """
        return self.dc_power

    def ac_power_measurement(self) -> float:
        """Returns the measured AC power.

        It returns the measured AC power if available; otherwise None.

        Returns:
            float: Measured AC power in watts or None
        """
        return self.ac_power_measurement

    def get_ac_power(self) -> float:
        """Returns the AC power.

        If a measured value is available, it returns the measured AC power;
        otherwise, it returns the forecasted AC power.

        Returns:
            float: AC power in watts.
        """
        if self.ac_power_measurement is not None:
            return self.ac_power_measurement
        else:
            return self.ac_power

    def get_windspeed_10m(self) -> float:
        """Returns the wind speed at 10 meters altitude.

        Returns:
            float: Wind speed in meters per second.
        """
        return self.windspeed_10m

    def get_temperature(self) -> float:
        """Returns the temperature.

        Returns:
            float: Temperature in degrees Celsius.
        """
        return self.temperature


class PVForecast:
    """Manages PV (photovoltaic) power forecasts and weather data.

    Forecast data can be loaded from different sources (in-memory data, file, or URL).

    Attributes:
        meta (dict): Metadata related to the forecast (e.g., source, location).
        forecast_data (list): A list of forecast data points of `ForecastData` objects.
        prediction_hours (int): The number of hours into the future the forecast covers.
        current_measurement (Optional[float]): The current AC power measurement in watts (or None if unavailable).
        data (Optional[dict]): JSON data containing the forecast information (if provided).
        filepath (Optional[str]): Filepath to the forecast data file (if provided).
        url (Optional[str]): URL to retrieve forecast data from an API (if provided).
        _forecast_start (Optional[date]): Start datetime for the forecast period.
        tz_name (Optional[str]): The time zone name of the forecast data, if applicable.
    """

    def __init__(
        self,
        data: Optional[dict] = None,
        filepath: Optional[str] = None,
        url: Optional[str] = None,
        forecast_start: Union[datetime, date, str, int, float] = None,
        prediction_hours: Optional[int] = None,
    ):
        """Initializes a `PVForecast` instance.

        Forecast data can be loaded from in-memory `data`, a file specified by `filepath`, or
        fetched from a remote `url`. If none are provided, an empty forecast will be initialized.
        The `forecast_start` and `prediction_hours` parameters can be specified to control the
        forecasting time period.

        Use `process_data()` to fill an empty forecast later on.

        Args:
            data (Optional[dict]): In-memory JSON data containing forecast information. Defaults to None.
            filepath (Optional[str]): Path to a local file containing forecast data in JSON format. Defaults to None.
            url (Optional[str]): URL to an API providing forecast data. Defaults to None.
            forecast_start (Union[datetime, date, str, int, float]): The start datetime for the forecast period.
                Can be a `datetime`, `date`, `str` (formatted date), `int` (timestamp), `float`, or None. Defaults to None.
            prediction_hours (Optional[int]): The number of hours to forecast into the future. Defaults to 48 hours.

        Example:
            forecast = PVForecast(data=my_forecast_data, forecast_start="2024-10-13", prediction_hours=72)
        """
        self.meta = {}
        self.forecast_data = []
        self.current_measurement = None
        self.data = data
        self.filepath = filepath
        self.url = url
        if forecast_start:
            self._forecast_start = to_datetime(forecast_start, to_naiv=True, to_maxtime=False)
        else:
            self._forecast_start = None
        self.prediction_hours = prediction_hours
        self._tz_name = None

        if self.data or self.filepath or self.url:
            self.process_data(
                data=self.data,
                filepath=self.filepath,
                url=self.url,
                forecast_start=self._forecast_start,
                prediction_hours=self.prediction_hours,
            )

    def update_ac_power_measurement(
        self,
        date_time: Union[datetime, date, str, int, float, None] = None,
        ac_power_measurement=None,
    ) -> bool:
        """Updates the AC power measurement for a specific time.

        Args:
            date_time (datetime): The date and time of the measurement.
            ac_power_measurement (float): Measured AC power.

        Returns:
            bool: True if a matching timestamp was found, False otherwise.
        """
        found = False
        input_date_hour = to_datetime(
            date_time, to_timezone=self._tz_name, to_naiv=True, to_maxtime=False
        ).replace(minute=0, second=0, microsecond=0)

        for forecast in self.forecast_data:
            forecast_date_hour = to_datetime(forecast.date_time, to_naiv=True).replace(
                minute=0, second=0, microsecond=0
            )
            if forecast_date_hour == input_date_hour:
                forecast.ac_power_measurement = ac_power_measurement
                found = True
                logger.debug(
                    f"AC Power measurement updated at date {input_date_hour}: {ac_power_measurement}"
                )
                break
        return found

    def process_data(
        self,
        data: Optional[dict] = None,
        filepath: Optional[str] = None,
        url: Optional[str] = None,
        forecast_start: Union[datetime, date, str, int, float] = None,
        prediction_hours: Optional[int] = None,
    ) -> None:
        """Processes the forecast data from the provided source (in-memory `data`, `filepath`, or `url`).

        If `forecast_start` and `prediction_hours` are provided, they define the forecast period.

        Args:
            data (Optional[dict]): JSON data containing forecast values. Defaults to None.
            filepath (Optional[str]): Path to a file with forecast data. Defaults to None.
            url (Optional[str]): API URL to retrieve forecast data from. Defaults to None.
            forecast_start (Union[datetime, date, str, int, float, None]): Start datetime of the forecast
                period. Defaults to None. If given before it is cached.
            prediction_hours (Optional[int]): The number of hours to forecast into the future.
                Defaults to None. If given before it is cached.

        Returns:
            None

        Raises:
            FileNotFoundError: If the specified `filepath` does not exist.
            ValueError: If no valid data source or data is provided.

        Example:
            forecast = PVForecast(
                url="https://api.akkudoktor.net/forecast?lat=50.8588&lon=7.3747&"
                "power=5000&azimuth=-10&tilt=7&powerInvertor=10000&horizont=20,27,22,20&"
                "power=4800&azimuth=-90&tilt=7&powerInvertor=10000&horizont=30,30,30,50&"
                "power=1400&azimuth=-40&tilt=60&powerInvertor=2000&horizont=60,30,0,30&"
                "power=1600&azimuth=5&tilt=45&powerInvertor=1400&horizont=45,25,30,60&"
                "past_days=5&cellCoEff=-0.36&inverterEfficiency=0.8&albedo=0.25&"
                "timezone=Europe%2FBerlin&hourly=relativehumidity_2m%2Cwindspeed_10m",
                prediction_hours = 24,
            )
        """
        # Get input forecast data
        if data:
            pass
        elif filepath:
            data = self.load_data_from_file(filepath)
        elif url:
            data = self.load_data_from_url_with_caching(url)
        elif self.data or self.filepath or self.url:
            # Re-process according to previous arguments
            if self.data:
                data = self.data
            elif self.filepath:
                data = self.load_data_from_file(self.filepath)
            elif self.url:
                data = self.load_data_from_url_with_caching(self.url)
            else:
                raise NotImplementedError(
                    "Re-processing for None input is not implemented!"
                )  # Invalid path
        else:
            raise ValueError("No prediction input data available.")
        # Validate input data to be of a known format
        data_format = validate_pv_forecast_data(data)
        if data_format != "Akkudoktor":
            raise ValueError(f"Prediction input data are of unknown format: '{data_format}'.")

        # Assure we have a forecast start datetime
        if forecast_start is None:
            forecast_start = self._forecast_start
            if forecast_start is None:
                forecast_start = datetime(1970, 1, 1)

        # Assure we have prediction hours set
        if prediction_hours is None:
            prediction_hours = self.prediction_hours
            if prediction_hours is None:
                prediction_hours = 48
        self.prediction_hours = prediction_hours

        if data_format == "Akkudoktor":
            # --------------------------------------------
            # From here Akkudoktor PV forecast data format
            # ---------------------------------------------
            self.meta = data.get("meta")
            all_values = data.get("values")

            # timezone of the PV system
            self._tz_name = self.meta.get("timezone", None)
            if not self._tz_name:
                raise NotImplementedError(
                    "Processing without PV system timezone info ist not implemented!"
                )

            # Assumption that all lists are the same length and are ordered chronologically
            # in ascending order and have the same timestamps.
            values_len = len(all_values[0])
            if values_len < self.prediction_hours:
                # Expect one value set per prediction hour
                raise ValueError(
                    f"The forecast must cover at least {self.prediction_hours} hours, "
                    f"but only {values_len} data sets are given in forecast data."
                )

            # Convert forecast_start to timezone of PV system and make it a naiv datetime
            self._forecast_start = to_datetime(
                forecast_start, to_timezone=self._tz_name, to_naiv=True
            )
            logger.debug(f"Forecast start set to {self._forecast_start}")

            for i in range(values_len):
                # Zeige die ursprünglichen und berechneten Zeitstempel an
                original_datetime = all_values[0][i].get("datetime")
                # print(original_datetime," ",sum_dc_power," ",all_values[0][i]['dcPower'])
                dt = to_datetime(original_datetime, to_timezone=self._tz_name, to_naiv=True)
                # iso_datetime = parser.parse(original_datetime).isoformat()  # Konvertiere zu ISO-Format
                # print()
                # Optional: 2 Stunden abziehen, um die Zeitanpassung zu testen
                # adjusted_datetime = parser.parse(original_datetime) - timedelta(hours=2)
                # print(f"Angepasste Zeitstempel: {adjusted_datetime.isoformat()}")

                if dt < self._forecast_start:
                    # forecast data are too old
                    continue

                sum_dc_power = sum(values[i]["dcPower"] for values in all_values)
                sum_ac_power = sum(values[i]["power"] for values in all_values)

                forecast = ForecastData(
                    date_time=dt,  # Verwende angepassten Zeitstempel
                    dc_power=sum_dc_power,
                    ac_power=sum_ac_power,
                    windspeed_10m=all_values[0][i].get("windspeed_10m"),
                    temperature=all_values[0][i].get("temperature"),
                )
                self.forecast_data.append(forecast)

        if len(self.forecast_data) < self.prediction_hours:
            raise ValueError(
                f"The forecast must cover at least {self.prediction_hours} hours, "
                f"but only {len(self.forecast_data)} hours starting from {forecast_start} "
                f"were predicted."
            )

        # Adapt forecast start to actual value
        self._forecast_start = self.forecast_data[0].get_date_time()
        logger.debug(f"Forecast start adapted to {self._forecast_start}")

    def load_data_from_file(self, filepath: str) -> dict:
        """Loads forecast data from a file.

        Args:
            filepath (str): Path to the file containing the forecast data.

        Returns:
            data (dict): JSON data containing forecast values.
        """
        with open(filepath, "r") as file:
            data = json.load(file)
        return data

    def load_data_from_url(self, url: str) -> dict:
        """Loads forecast data from a URL.

        Example:
            https://api.akkudoktor.net/forecast?lat=50.8588&lon=7.3747&power=5000&azimuth=-10&tilt=7&powerInvertor=10000&horizont=20,27,22,20&power=4800&azimuth=-90&tilt=7&powerInvertor=10000&horizont=30,30,30,50&power=1400&azimuth=-40&tilt=60&powerInvertor=2000&horizont=60,30,0,30&power=1600&azimuth=5&tilt=45&powerInvertor=1400&horizont=45,25,30,60&past_days=5&cellCoEff=-0.36&inverterEfficiency=0.8&albedo=0.25&timezone=Europe%2FBerlin&hourly=relativehumidity_2m%2Cwindspeed_10m

        Args:
            url (str): URL of the API providing forecast data.

        Returns:
            data (dict): JSON data containing forecast values.
        """
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
        else:
            data = f"Failed to load data from `{url}`. Status Code: {response.status_code}"
            logger.error(data)
        return data

    @cache_in_file()  # use binary mode by default as we have python objects not text
    def load_data_from_url_with_caching(self, url: str, until_date=None) -> dict:
        """Loads data from a URL or from the cache if available.

        Args:
            url (str): URL of the API providing forecast data.

        Returns:
            data (dict): JSON data containing forecast values.
        """
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            logger.debug(f"Data fetched from URL `{url} and cached.")
        else:
            data = f"Failed to load data from `{url}`. Status Code: {response.status_code}"
            logger.error(data)
        return data

    def get_forecast_data(self):
        """Returns the forecast data.

        Returns:
            list: List of ForecastData objects.
        """
        return self.forecast_data

    def get_temperature_forecast_for_date(
        self, input_date: Union[datetime, date, str, int, float, None]
    ):
        """Returns the temperature forecast for a specific date.

        Args:
            input_date (str): Date

        Returns:
            np.array: Array of temperature forecasts.
        """
        if not self._tz_name:
            raise NotImplementedError(
                "Processing without PV system timezone info ist not implemented!"
            )
        input_date = to_datetime(input_date, to_timezone=self._tz_name, to_naiv=True).date()
        daily_forecast_obj = [
            data for data in self.forecast_data if data.get_date_time().date() == input_date
        ]
        daily_forecast = []
        for d in daily_forecast_obj:
            daily_forecast.append(d.get_temperature())

        return np.array(daily_forecast)

    def get_pv_forecast_for_date_range(
        self,
        start_date: Union[datetime, date, str, int, float, None],
        end_date: Union[datetime, date, str, int, float, None],
    ):
        """Returns the PV forecast for a date range.

        Args:
            start_date_str (str): Start date in the format YYYY-MM-DD.
            end_date_str (str): End date in the format YYYY-MM-DD.

        Returns:
            pd.DataFrame: DataFrame containing the forecast data.
        """
        if not self._tz_name:
            raise NotImplementedError(
                "Processing without PV system timezone info ist not implemented!"
            )
        start_date = to_datetime(start_date, to_timezone=self._tz_name, to_naiv=True).date()
        end_date = to_datetime(end_date, to_timezone=self._tz_name, to_naiv=True).date()
        date_range_forecast = []

        for data in self.forecast_data:
            data_date = data.get_date_time().date()
            if start_date <= data_date <= end_date:
                date_range_forecast.append(data)
                # print(data.get_date_time(), " ", data.get_ac_power())

        ac_power_forecast = np.array([data.get_ac_power() for data in date_range_forecast])

        return np.array(ac_power_forecast)[: self.prediction_hours]

    def get_temperature_for_date_range(
        self,
        start_date: Union[datetime, date, str, int, float, None],
        end_date: Union[datetime, date, str, int, float, None],
    ):
        """Returns the temperature forecast for a given date range.

        Args:
            start_date (datetime | date | str | int | float | None): Start date.
            end_date (datetime | date | str | int | float | None): End date.

        Returns:
            np.array: Array containing temperature forecasts for each hour within the date range.
        """
        if not self._tz_name:
            raise NotImplementedError(
                "Processing without PV system timezone info ist not implemented!"
            )
        start_date = to_datetime(start_date, to_timezone=self._tz_name, to_naiv=True).date()
        end_date = to_datetime(end_date, to_timezone=self._tz_name, to_naiv=True).date()
        date_range_forecast = []

        for data in self.forecast_data:
            data_date = data.get_date_time().date()
            if start_date <= data_date <= end_date:
                date_range_forecast.append(data)

        temperature_forecast = [data.get_temperature() for data in date_range_forecast]
        return np.array(temperature_forecast)[: self.prediction_hours]

    def get_forecast_dataframe(self):
        """Converts the forecast data into a Pandas DataFrame.

        Returns:
            pd.DataFrame: A DataFrame containing the forecast data with columns for date/time,
                          DC power, AC power, windspeed, and temperature.
        """
        data = [
            {
                "date_time": f.get_date_time(),
                "dc_power": f.get_dc_power(),
                "ac_power": f.get_ac_power(),
                "windspeed_10m": f.get_windspeed_10m(),
                "temperature": f.get_temperature(),
            }
            for f in self.forecast_data
        ]

        # Erstelle ein DataFrame
        df = pd.DataFrame(data)
        return df

    def get_forecast_start(self) -> datetime:
        """Return the start of the forecast data in local timezone.

        Returns:
            forecast_start (datetime | None): The start datetime or None if no data available.
        """
        if not self._forecast_start:
            return None
        return to_datetime(
            self._forecast_start, to_timezone=self._tz_name, to_naiv=True, to_maxtime=False
        )

    def report_ac_power_and_measurement(self) -> str:
        """Report DC/ AC power, and AC power measurement for each forecast hour.

        For each forecast entry, the time, DC power, forecasted AC power, measured AC power
        (if available), and the value returned by the `get_ac_power` method is provided.

        Returns:
            str: The report.
        """
        rep = ""
        for forecast in self.forecast_data:
            date_time = forecast.date_time
            dc_pow = round(forecast.dc_power, 2) if forecast.dc_power else None
            ac_pow = round(forecast.ac_power, 2) if forecast.ac_power else None
            ac_pow_measurement = (
                round(forecast.ac_power_measurement, 2) if forecast.ac_power_measurement else None
            )
            get_ac_pow = round(forecast.get_ac_power(), 2) if forecast.get_ac_power() else None
            rep += (
                f"Date&Time: {date_time}, DC: {dc_pow}, AC: {ac_pow}, "
                f"AC measured: {ac_pow_measurement}, AC GET: {get_ac_pow}"
                "\n"
            )
        return rep


# Example of how to use the PVForecast class
if __name__ == "__main__":
    """Main execution block to demonstrate the use of the PVForecast class.

    Fetches PV power forecast data from a given URL, updates the AC power measurement
    for the current date/time, and prints the DC and AC power information.
    """
    forecast = PVForecast(
        prediction_hours=24,
        url="https://api.akkudoktor.net/forecast?lat=50.8588&lon=7.3747&"
        "power=5000&azimuth=-10&tilt=7&powerInvertor=10000&horizont=20,27,22,20&"
        "power=4800&azimuth=-90&tilt=7&powerInvertor=10000&horizont=30,30,30,50&"
        "power=1400&azimuth=-40&tilt=60&powerInvertor=2000&horizont=60,30,0,30&"
        "power=1600&azimuth=5&tilt=45&powerInvertor=1400&horizont=45,25,30,60&"
        "past_days=5&cellCoEff=-0.36&inverterEfficiency=0.8&albedo=0.25&timezone=Europe%2FBerlin&"
        "hourly=relativehumidity_2m%2Cwindspeed_10m",
    )
    forecast.update_ac_power_measurement(date_time=datetime.now(), ac_power_measurement=1000)
    print(forecast.report_ac_power_and_measurement())
