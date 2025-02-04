"""Weather Forecast.

This module provides classes and methods to retrieve, manage, and process weather forecast data
from various online sources. It includes structured representations of weather data and utilities
for fetching forecasts for specific locations and time ranges. By integrating multiple data sources,
the module enables flexible access to weather information based on latitude, longitude, and
desired time periods.

Notes:
    - Supported weather sources can be expanded by adding new fetch methods within the
      WeatherForecast class.
    - Ensure appropriate API keys or configurations are set up if required by external data sources.
"""

import re
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from bs4 import BeautifulSoup

from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.prediction.weatherabc import WeatherDataRecord, WeatherProvider
from akkudoktoreos.utils.cacheutil import cache_in_file
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration, to_timezone

logger = get_logger(__name__)


WheaterDataClearOutsideMapping: List[Tuple[str, Optional[str], Optional[float]]] = [
    # clearoutside_key, description, corr_factor
    ("DateTime", "DateTime", None),
    ("Total Clouds (% Sky Obscured)", "Total Clouds (% Sky Obscured)", 1),
    ("Low Clouds (% Sky Obscured)", "Low Clouds (% Sky Obscured)", 1),
    ("Medium Clouds (% Sky Obscured)", "Medium Clouds (% Sky Obscured)", 1),
    ("High Clouds (% Sky Obscured)", "High Clouds (% Sky Obscured)", 1),
    ("ISS Passover", None, None),
    ("Visibility (miles)", "Visibility (m)", 1609.34),
    ("Fog (%)", "Fog (%)", 1),
    ("Precipitation Type", "Precipitation Type", None),
    ("Precipitation Probability (%)", "Precipitation Probability (%)", 1),
    ("Precipitation Amount (mm)", "Precipitation Amount (mm)", 1),
    ("Wind Speed (mph)", "Wind Speed (kmph)", 1.60934),
    ("Chance of Frost", "Chance of Frost", None),
    ("Temperature (°C)", "Temperature (°C)", 1),
    ("Feels Like (°C)", "Feels Like (°C)", 1),
    ("Dew Point (°C)", "Dew Point (°C)", 1),
    ("Relative Humidity (%)", "Relative Humidity (%)", 1),
    ("Pressure (mb)", "Pressure (mb)", 1),
    ("Ozone (du)", "Ozone (du)", 1),
    # Extra extraction
    ("Wind Direction (°)", "Wind Direction (°)", 1),
    # Generated from above
    ("Preciptable Water (cm)", "Preciptable Water (cm)", 1),
    ("Global Horizontal Irradiance (W/m2)", "Global Horizontal Irradiance (W/m2)", 1),
    ("Direct Normal Irradiance (W/m2)", "Direct Normal Irradiance (W/m2)", 1),
    ("Diffuse Horizontal Irradiance (W/m2)", "Diffuse Horizontal Irradiance (W/m2)", 1),
]
"""Mapping of ClearOutside weather data keys to WeatherDataRecord field description.

A list of tuples: (ClearOutside key, field description, correction factor).
"""


class WeatherClearOutside(WeatherProvider):
    """Retrieves and processes weather forecast data from ClearOutside.

    WeatherClearOutside is a thread-safe singleton, ensuring only one instance of this class is created.

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

    @classmethod
    def provider_id(cls) -> str:
        return "ClearOutside"

    @cache_in_file(with_ttl="1 hour")
    def _request_forecast(self) -> requests.Response:
        """Requests weather forecast from ClearOutside.

        Returns:
            response: Weather forecast request reponse from ClearOutside.
        """
        source = "https://clearoutside.com/forecast"
        latitude = round(self.config.general.latitude, 2)
        longitude = round(self.config.general.longitude, 2)
        response = requests.get(f"{source}/{latitude}/{longitude}?desktop=true")
        response.raise_for_status()  # Raise an error for bad responses
        logger.debug(f"Response from {source}: {response}")
        # We are working on fresh data (no cache), report update time
        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)
        return response

    def _update_data(self, force_update: Optional[bool] = None) -> None:
        """Scrape weather forecast data from ClearOutside's website.

        This method requests weather forecast data from ClearOutside based on latitude
        and longitude, then processes and structures this data for further use in analysis.

        The forecast data includes a variety of weather parameters such as cloud cover, temperature,
        humidity, visibility, precipitation, wind speed, and additional irradiance values
        calculated using the cloud cover data.

        Raises:
            ValueError: If the HTML structure of ClearOutside's website changes, causing
            extraction issues with forecast dates, timezone, or expected data sections.

        Note:
            - The function partly builds on code from https://github.com/davidusb-geek/emhass/blob/master/src/emhass/forecast.py (MIT License).
            - Uses `pvlib` to estimate irradiance (GHI, DNI, DHI) based on cloud cover data.

        Workflow:
            1. **Retrieve Web Content**: Uses a helper method to fetch or retrieve cached ClearOutside HTML content.
            2. **Extract Forecast Date and Timezone**:
                - Parses the forecast's start and end dates and the UTC offset from the "Generated" header.
            3. **Extract Weather Data**:
                - For each day in the 7-day forecast, the function finds detailed weather parameters
                and associates values for each hour.
                - Parameters include cloud cover, temperature, humidity, visibility, and precipitation type, among others.
            4. **Irradiance Calculation**:
                - Calculates irradiance (GHI, DNI, DHI) values using cloud cover data and the `pvlib` library.
            5. **Store Data**:
                - Combines all hourly data into `WeatherDataRecord` objects, with keys
                standardized according to `WeatherDataRecord` attributes.
        """
        # Get ClearOutside web content - either from site or cached
        response = self._request_forecast(force_update=force_update)  # type: ignore

        # Scrape the data
        soup = BeautifulSoup(response.content, "html.parser")

        # Find generation data
        p_generated = soup.find("h2", string=lambda text: text and text.startswith("Generated:"))
        if not p_generated:
            error_msg = f"Clearoutside schema change. Could not get '<h2>Generated:', got {p_generated} from {str(response.content)}."
            logger.error(error_msg)
            raise ValueError(error_msg)
        # Extract forecast start and end dates
        forecast_pattern = r"Forecast: (\d{2}/\d{2}/\d{2}) to (\d{2}/\d{2}/\d{2})"
        forecast_match = re.search(forecast_pattern, p_generated.get_text())
        if forecast_match:
            forecast_start_date = forecast_match.group(1)
            forecast_end_date = forecast_match.group(2)
        else:
            error_msg = f"Clearoutside schema change. Could not extract forecast start and end dates from {p_generated}."
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Extract timezone offset
        timezone_pattern = r"Timezone: UTC([+-]\d+)\.(\d+)"
        timezone_match = re.search(timezone_pattern, p_generated.get_text())
        if timezone_match:
            hours = int(timezone_match.group(1))
            # Convert the decimal part to minutes (e.g., .50 -> 30 minutes)
            minutes = int(timezone_match.group(2)) * 6  # Multiply by 6 to convert to minutes

            # Create the timezone object using offset
            utc_offset = float(hours) + float(minutes) / 60.0
            forecast_timezone = to_timezone(utc_offset=utc_offset)
        else:
            error_msg = "Clearoutside schema change. Could not extract forecast timezone."
            logger.error(error_msg)
            raise ValueError(error_msg)

        forecast_start_datetime = to_datetime(
            forecast_start_date, in_timezone=forecast_timezone, to_maxtime=False
        )

        # Get key mapping from description
        clearoutside_key_mapping: Dict[str, Tuple[Optional[str], Optional[float]]] = {}
        for clearoutside_key, description, corr_factor in WheaterDataClearOutsideMapping:
            if description is None:
                clearoutside_key_mapping[clearoutside_key] = (None, None)
                continue
            weatherdata_key = WeatherDataRecord.key_from_description(description)
            if weatherdata_key is None:
                # Should not happen
                error_msg = f"No WeatherDataRecord key for '{description}'"
                logger.error(error_msg)
                raise ValueError(error_msg)
            clearoutside_key_mapping[clearoutside_key] = (weatherdata_key, corr_factor)

        # Find all paragraphs with id 'day_<x>'. There should be seven.
        p_days = soup.find_all(id=re.compile(r"day_[0-9]"))
        if len(p_days) != 7:
            error_msg = f"Clearoutside schema change. Found {len(p_days)} day tables, expected 7."
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Delete all records that will be newly added
        self.delete_by_datetime(start_datetime=forecast_start_datetime)

        # Collect weather data, loop over all days
        for day, p_day in enumerate(p_days):
            # Within day_x paragraph find the details labels
            p_detail_labels = p_day.find_all(class_="fc_detail_label")
            detail_names = [p.get_text() for p in p_detail_labels]

            # Check for schema changes
            if len(detail_names) < 18:
                error_msg = f"Clearoutside schema change. Unexpected number ({len(detail_names)}) of `fc_detail_label`."
                logger.error(error_msg)
                raise ValueError(error_msg)
            for detail_name in detail_names:
                if detail_name not in clearoutside_key_mapping:
                    warning_msg = (
                        f"Clearoutside schema change. Unexpected detail name {detail_name}."
                    )
                    logger.warning(warning_msg)

            # Find all the paragraphs that are associated to the details.
            # Beware there is one ul paragraph before that is not associated to a detail
            p_detail_tables = p_day.find_all("ul")
            if len(p_detail_tables) != len(detail_names) + 1:
                error_msg = f"Clearoutside schema change. Unexpected number ({p_detail_tables}) of `ul` for details {len(detail_names)}. Should be one extra only."
                logger.error(error_msg)
                raise ValueError(error_msg)
            p_detail_tables.pop(0)

            # Create clearout data
            clearout_data = {}
            # Replace some detail names that we use differently
            detail_names = [
                s.replace("Wind Speed/Direction (mph)", "Wind Speed (mph)") for s in detail_names
            ]
            # Number of detail values. On last day may be less than 24.
            detail_values_count = None
            # Add data values
            scrape_detail_names = detail_names.copy()  # do not change list during iteration!
            for i, detail_name in enumerate(scrape_detail_names):
                p_detail_values = p_detail_tables[i].find_all("li")

                # Assure the number of values fits
                p_detail_values_count = len(p_detail_values)
                if (day == 6 and p_detail_values_count > 24) or (
                    day < 6 and p_detail_values_count != 24
                ):
                    error_msg = f"Clearoutside schema change. Unexpected number ({p_detail_values_count}) of `li` for detail `{detail_name}` data. Should be 24 or less on day 7. Table is `{p_detail_tables[i]}`."
                    logger.error(error_msg)
                    raise ValueError(error_msg)
                if detail_values_count is None:
                    # Remember detail values count only once
                    detail_values_count = p_detail_values_count
                if p_detail_values_count != detail_values_count:
                    # Value count for details differ.
                    error_msg = f"Clearoutside schema change. Number ({p_detail_values_count}) of `li` for detail `{detail_name}` data is different than last one {detail_values_count}. Table is `{p_detail_tables[i]}`."
                    logger.error(error_msg)
                    raise ValueError(error_msg)

                # Scrape the detail values
                detail_data = []
                extra_detail_name = None
                extra_detail_data = []
                for p_detail_value in p_detail_values:
                    if detail_name == "Wind Speed (mph)":
                        # Get the  usual value
                        value_str = p_detail_value.get_text()
                        # Also extract extra data
                        extra_detail_name = "Wind Direction (°)"
                        extra_value = None
                        match = re.search(r"(\d+)°", str(p_detail_value))
                        if match:
                            extra_value = float(match.group(1))
                        else:
                            error_msg = f"Clearoutside schema change. Can't extract direction angle from `{p_detail_value}` for detail `{extra_detail_name}`. Table is `{p_detail_tables[i]}`."
                            logger.error(error_msg)
                            raise ValueError(error_msg)
                        extra_detail_data.append(extra_value)
                    elif (
                        detail_name in ("Precipitation Type", "Chance of Frost")
                        and hasattr(p_detail_value, "title")
                        and p_detail_value.title
                    ):
                        value_str = p_detail_value.title.string
                    else:
                        value_str = p_detail_value.get_text()
                    try:
                        value = float(value_str)
                    except ValueError:
                        value = value_str
                    detail_data.append(value)
                clearout_data[detail_name] = detail_data
                if extra_detail_name:
                    if extra_detail_name not in detail_names:
                        detail_names.append(extra_detail_name)
                    clearout_data[extra_detail_name] = extra_detail_data
                    logger.debug(f"Added extra data {extra_detail_name} with {extra_detail_data}")

            # Add datetimes of the scrapped data
            clearout_data["DateTime"] = [
                forecast_start_datetime + to_duration(f"{day} days {i} hours")
                for i in range(0, detail_values_count)  # type: ignore[arg-type]
            ]
            detail_names.append("DateTime")

            # Converting the cloud cover into Irradiance (GHI, DNI, DHI)
            cloud_cover = pd.Series(
                data=clearout_data["Total Clouds (% Sky Obscured)"], index=clearout_data["DateTime"]
            )
            ghi, dni, dhi = self.estimate_irradiance_from_cloud_cover(
                self.config.general.latitude, self.config.general.longitude, cloud_cover
            )

            # Add GHI, DNI, DHI to clearout data
            clearout_data["Global Horizontal Irradiance (W/m2)"] = ghi
            detail_names.append("Global Horizontal Irradiance (W/m2)")
            clearout_data["Direct Normal Irradiance (W/m2)"] = dni
            detail_names.append("Direct Normal Irradiance (W/m2)")
            clearout_data["Diffuse Horizontal Irradiance (W/m2)"] = dhi
            detail_names.append("Diffuse Horizontal Irradiance (W/m2)")

            # Add Preciptable Water (PWAT) with a PVLib method.
            clearout_data["Preciptable Water (cm)"] = self.estimate_preciptable_water(
                pd.Series(data=clearout_data["Temperature (°C)"]),
                pd.Series(data=clearout_data["Relative Humidity (%)"]),
            ).to_list()
            detail_names.append("Preciptable Water (cm)")

            # Add weather data
            # Add the records from clearout
            for row_index in range(0, len(clearout_data["DateTime"])):
                weather_record = WeatherDataRecord()
                for detail_name in detail_names:
                    key = clearoutside_key_mapping[detail_name][0]
                    if key is None:
                        continue
                    if detail_name in clearout_data:
                        value = clearout_data[detail_name][row_index]
                        corr_factor = clearoutside_key_mapping[detail_name][1]
                        if corr_factor:
                            value = value * corr_factor
                        setattr(weather_record, key, value)
                self.insert_by_datetime(weather_record)
