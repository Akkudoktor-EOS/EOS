"""Load forecast module for load predictions."""

from typing import Optional

from pydantic import Field, computed_field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import get_prediction
from akkudoktoreos.prediction.loadabc import LoadProvider
from akkudoktoreos.prediction.loadakkudoktor import LoadAkkudoktorCommonSettings
from akkudoktoreos.prediction.loadimport import LoadImportCommonSettings
from akkudoktoreos.prediction.loadvrm import LoadVrmCommonSettings


def load_providers() -> list[str]:
    """Valid load provider ids."""
    try:
        prediction_eos = get_prediction()
    except:
        # Prediction may not be initialized
        # Return at least provider used in example
        return ["LoadAkkudoktor", "LoadVrm", "LoadImport"]

    return [
        provider.provider_id()
        for provider in prediction_eos.providers
        if isinstance(provider, LoadProvider)
    ]


class LoadCommonProviderSettings(SettingsBaseModel):
    """Load Prediction Provider Configuration."""

    LoadAkkudoktor: Optional[LoadAkkudoktorCommonSettings] = Field(
        default=None,
        json_schema_extra={"description": "LoadAkkudoktor settings", "examples": [None]},
    )
    LoadVrm: Optional[LoadVrmCommonSettings] = Field(
        default=None, json_schema_extra={"description": "LoadVrm settings", "examples": [None]}
    )
    LoadImport: Optional[LoadImportCommonSettings] = Field(
        default=None, json_schema_extra={"description": "LoadImport settings", "examples": [None]}
    )


class LoadCommonSettings(SettingsBaseModel):
    """Load Prediction Configuration."""

    provider: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": "Load provider id of provider to be used.",
            "examples": ["LoadAkkudoktor"],
        },
    )

    provider_settings: LoadCommonProviderSettings = Field(
        default_factory=LoadCommonProviderSettings,
        json_schema_extra={
            "description": "Provider settings",
            "examples": [
                # Example 1: Empty/default settings (all providers None)
                {
                    "LoadAkkudoktor": None,
                    "LoadVrm": None,
                    "LoadImport": None,
                },
            ],
        },
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def providers(self) -> list[str]:
        """Available load provider ids."""
        return load_providers()

    # Validators
    @field_validator("provider", mode="after")
    @classmethod
    def validate_provider(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value in load_providers():
            return value
        raise ValueError(f"Provider '{value}' is not a valid load provider: {load_providers()}.")
