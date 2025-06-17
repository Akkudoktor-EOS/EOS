"""Load forecast module for load predictions."""

from typing import Optional

from pydantic import Field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.prediction.loadabc import LoadProvider
from akkudoktoreos.prediction.loadakkudoktor import LoadAkkudoktorCommonSettings
from akkudoktoreos.prediction.loadimport import LoadImportCommonSettings
from akkudoktoreos.prediction.loadvrm import LoadVrmCommonSettings
from akkudoktoreos.prediction.prediction import get_prediction

prediction_eos = get_prediction()

# Valid load providers
load_providers = [
    provider.provider_id()
    for provider in prediction_eos.providers
    if isinstance(provider, LoadProvider)
]


class LoadCommonProviderSettings(SettingsBaseModel):
    """Load Prediction Provider Configuration."""

    LoadAkkudoktor: Optional[LoadAkkudoktorCommonSettings] = Field(
        default=None, description="LoadAkkudoktor settings", examples=[None]
    )
    LoadVrm: Optional[LoadVrmCommonSettings] = Field(
        default=None, description="LoadVrm settings", examples=[None]
    )
    LoadImport: Optional[LoadImportCommonSettings] = Field(
        default=None, description="LoadImport settings", examples=[None]
    )


class LoadCommonSettings(SettingsBaseModel):
    """Load Prediction Configuration."""

    provider: Optional[str] = Field(
        default=None,
        description="Load provider id of provider to be used.",
        examples=["LoadAkkudoktor"],
    )

    provider_settings: LoadCommonProviderSettings = Field(
        default_factory=LoadCommonProviderSettings,
        description="Provider settings",
        examples=[
            # Example 1: Empty/default settings (all providers None)
            {
                "LoadAkkudoktor": None,
                "LoadVrm": None,
                "LoadImport": None,
            },
        ],
    )

    # Validators
    @field_validator("provider", mode="after")
    @classmethod
    def validate_provider(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value in load_providers:
            return value
        raise ValueError(f"Provider '{value}' is not a valid load provider: {load_providers}.")
