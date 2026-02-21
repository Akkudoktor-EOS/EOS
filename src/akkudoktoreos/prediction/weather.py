"""Weather forecast module for weather predictions."""

from typing import Optional

from pydantic import Field, computed_field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import get_prediction
from akkudoktoreos.prediction.weatherabc import WeatherProvider
from akkudoktoreos.prediction.weatherimport import WeatherImportCommonSettings


def weather_provider_ids() -> list[str]:
    """Valid weather provider ids."""
    try:
        prediction_eos = get_prediction()
    except:
        # Prediction may not be initialized
        # Return at least provider used in example
        return ["WeatherImport"]

    return [
        provider.provider_id()
        for provider in prediction_eos.providers
        if isinstance(provider, WeatherProvider)
    ]


class WeatherCommonProviderSettings(SettingsBaseModel):
    """Weather Forecast Provider Configuration."""

    WeatherImport: Optional[WeatherImportCommonSettings] = Field(
        default=None,
        json_schema_extra={"description": "WeatherImport settings", "examples": [None]},
    )


class WeatherCommonSettings(SettingsBaseModel):
    """Weather Forecast Configuration."""

    provider: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": "Weather provider id of provider to be used.",
            "examples": ["WeatherImport"],
        },
    )

    provider_settings: WeatherCommonProviderSettings = Field(
        default_factory=WeatherCommonProviderSettings,
        json_schema_extra={
            "description": "Provider settings",
            "examples": [
                # Example 1: Empty/default settings (all providers None)
                {
                    "WeatherImport": None,
                },
            ],
        },
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def providers(self) -> list[str]:
        """Available weather provider ids."""
        return weather_provider_ids()

    # Validators
    @field_validator("provider", mode="after")
    @classmethod
    def validate_provider(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value in weather_provider_ids():
            return value
        raise ValueError(
            f"Provider '{value}' is not a valid weather provider: {weather_provider_ids()}."
        )
