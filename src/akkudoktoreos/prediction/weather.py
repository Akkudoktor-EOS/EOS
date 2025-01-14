"""Weather forecast module for weather predictions."""

from typing import Optional

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.prediction.weatherimport import WeatherImportCommonSettings


class WeatherCommonSettings(SettingsBaseModel):
    """Weather Forecast Configuration."""

    weather_provider: Optional[str] = Field(
        default=None,
        description="Weather provider id of provider to be used.",
        examples=["WeatherImport"],
    )

    provider_settings: Optional[WeatherImportCommonSettings] = Field(
        default=None, description="Provider settings", examples=[None]
    )
