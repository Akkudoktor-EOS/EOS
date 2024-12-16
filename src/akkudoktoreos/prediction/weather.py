"""Weather forecast module for weather predictions."""

from typing import Optional

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel


class WeatherCommonSettings(SettingsBaseModel):
    weather_provider: Optional[str] = Field(
        default=None, description="Weather provider id of provider to be used."
    )
