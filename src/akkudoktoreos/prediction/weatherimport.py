"""Retrieves weather forecast data from an import file.

This module provides classes and mappings to manage weather data obtained from
an import file, including support for various weather attributes such as temperature,
humidity, cloud cover, and solar irradiance. The data is mapped to the `WeatherDataRecord`
format, enabling consistent access to forecasted and historical weather attributes.
"""

from pathlib import Path
from typing import Optional, Union

from loguru import logger
from pydantic import Field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.prediction.predictionabc import PredictionImportProvider
from akkudoktoreos.prediction.weatherabc import WeatherProvider


class WeatherImportCommonSettings(SettingsBaseModel):
    """Common settings for weather data import from file or JSON string."""

    import_file_path: Optional[Union[str, Path]] = Field(
        default=None,
        description="Path to the file to import weather data from.",
        examples=[None, "/path/to/weather_data.json"],
    )

    import_json: Optional[str] = Field(
        default=None,
        description="JSON string, dictionary of weather forecast value lists.",
        examples=['{"weather_temp_air": [18.3, 17.8, 16.9]}'],
    )

    # Validators
    @field_validator("import_file_path", mode="after")
    @classmethod
    def validate_import_file_path(cls, value: Optional[Union[str, Path]]) -> Optional[Path]:
        if value is None:
            return None
        if isinstance(value, str):
            value = Path(value)
        """Ensure file is available."""
        value.resolve()
        if not value.is_file():
            raise ValueError(f"Import file path '{value}' is not a file.")
        return value


class WeatherImport(WeatherProvider, PredictionImportProvider):
    """Fetch weather forecast data from import file or JSON string.

    WeatherImport is a singleton-based class that retrieves weather forecast data
    from a file or JSON string and maps it to `WeatherDataRecord` fields. It manages the forecast
    over a range of hours into the future and retains historical data.
    """

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the WeatherImport provider."""
        return "WeatherImport"

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        if self.config.weather.provider_settings.WeatherImport is None:
            logger.debug(f"{self.provider_id()} data update without provider settings.")
            return
        if self.config.weather.provider_settings.WeatherImport.import_file_path:
            self.import_from_file(
                self.config.weather.provider_settings.WeatherImport.import_file_path,
                key_prefix="weather",
            )
        if self.config.weather.provider_settings.WeatherImport.import_json:
            self.import_from_json(
                self.config.weather.provider_settings.WeatherImport.import_json,
                key_prefix="weather",
            )
