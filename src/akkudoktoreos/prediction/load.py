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


class LoadCommonSettings(SettingsBaseModel):
    """Load Prediction Configuration."""

    provider: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": "Load provider id of provider to be used.",
            "examples": ["LoadAkkudoktor"],
        },
    )

    loadakkudoktor: LoadAkkudoktorCommonSettings = Field(
        default_factory=LoadAkkudoktorCommonSettings,
        json_schema_extra={"description": "LoadAkkudoktor provider settings."},
    )

    loadvrm: LoadVrmCommonSettings = Field(
        default_factory=LoadVrmCommonSettings,
        json_schema_extra={"description": "LoadVrm provider settings."},
    )

    loadimport: LoadImportCommonSettings = Field(
        default_factory=LoadImportCommonSettings,
        json_schema_extra={"description": "LoadImport provider settings."},
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
