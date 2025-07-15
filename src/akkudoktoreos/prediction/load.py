"""Load forecast module for load predictions."""

from typing import Optional, Union

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


class LoadCommonSettings(SettingsBaseModel):
    """Load Prediction Configuration."""

    provider: Optional[str] = Field(
        default=None,
        description="Load provider id of provider to be used.",
        examples=["LoadAkkudoktor"],
    )

    provider_settings: Optional[
        Union[LoadAkkudoktorCommonSettings, LoadVrmCommonSettings, LoadImportCommonSettings]
    ] = Field(default=None, description="Provider settings", examples=[None])

    # Validators
    @field_validator("provider", mode="after")
    @classmethod
    def validate_provider(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value in load_providers:
            return value
        raise ValueError(f"Provider '{value}' is not a valid load provider: {load_providers}.")
