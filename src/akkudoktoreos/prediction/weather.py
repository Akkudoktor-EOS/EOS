"""Weather forecast module for weather predictions."""

from typing import Optional

from pydantic import Field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.prediction.prediction import get_prediction
from akkudoktoreos.prediction.weatherabc import WeatherProvider
from akkudoktoreos.prediction.weatherimport import WeatherImportCommonSettings

prediction_eos = get_prediction()

# Valid weather providers
weather_providers = [
    provider.provider_id()
    for provider in prediction_eos.providers
    if isinstance(provider, WeatherProvider)
]


class WeatherCommonSettings(SettingsBaseModel):
    """Weather Forecast Configuration."""

    provider: Optional[str] = Field(
        default=None,
        description="Weather provider id of provider to be used.",
        examples=["WeatherImport"],
    )

    provider_settings: Optional[WeatherImportCommonSettings] = Field(
        default=None, description="Provider settings", examples=[None]
    )

    # Validators
    @field_validator("provider", mode="after")
    @classmethod
    def validate_provider(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value in weather_providers:
            return value
        raise ValueError(
            f"Provider '{value}' is not a valid weather provider: {weather_providers}."
        )
