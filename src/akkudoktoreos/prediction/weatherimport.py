"""Retrieves weather forecast data from an import file.

This module provides classes and mappings to manage weather data obtained from
an import file, including support for various weather attributes such as temperature,
humidity, cloud cover, and solar irradiance. The data is mapped to the `WeatherDataRecord`
format, enabling consistent access to forecasted and historical weather attributes.
"""

from pathlib import Path
from typing import Optional, Union

from pydantic import Field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.prediction.predictionabc import PredictionImportProvider
from akkudoktoreos.prediction.weatherabc import WeatherProvider

logger = get_logger(__name__)


class WeatherImportCommonSettings(SettingsBaseModel):
    """Common settings for weather data import from file or JSON string."""

    weatherimport_file_path: Optional[Union[str, Path]] = Field(
        default=None, description="Path to the file to import weather data from."
    )

    weatherimport_json: Optional[str] = Field(
        default=None, description="JSON string, dictionary of weather forecast value lists."
    )

    # Validators
    @field_validator("weatherimport_file_path", mode="after")
    @classmethod
    def validate_weatherimport_file_path(cls, value: Optional[Union[str, Path]]) -> Optional[Path]:
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
        if self.config.weather.provider_settings.weatherimport_file_path is not None:
            self.import_from_file(
                self.config.weather.provider_settings.weatherimport_file_path, key_prefix="weather"
            )
        if self.config.weather.provider_settings.weatherimport_json is not None:
            self.import_from_json(
                self.config.weather.provider_settings.weatherimport_json, key_prefix="weather"
            )
