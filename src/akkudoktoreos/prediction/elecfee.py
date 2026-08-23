from typing import Optional

from pydantic import Field, computed_field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import get_prediction
from akkudoktoreos.prediction.elecfeeabc import ElecFeeProvider
from akkudoktoreos.prediction.elecfeefixed import ElecFeeFixedCommonSettings
from akkudoktoreos.prediction.elecfeeimport import ElecFeeImportCommonSettings


def elecfee_provider_ids() -> list[str]:
    """Valid elecfee provider ids."""
    try:
        prediction_eos = get_prediction()
    except Exception:
        # Prediction may not be initialized. Return static built-in provider ids.
        return [
            "ElecFeeFixed",
            "ElecFeeImport",
        ]

    return [
        provider.provider_id()
        for provider in prediction_eos.providers
        if isinstance(provider, ElecFeeProvider)
    ]


class ElecFeeCommonSettings(SettingsBaseModel):
    """Electricity Price Prediction Configuration."""

    provider: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": "Electricity fee provider id of provider to be used.",
            "examples": ["ElecFeeFixed"],
        },
    )

    elecfeefixed: ElecFeeFixedCommonSettings = Field(
        default_factory=ElecFeeFixedCommonSettings,
        json_schema_extra={"description": "Fixed electricity fees provider settings."},
    )

    elecfeeimport: ElecFeeImportCommonSettings = Field(
        default_factory=ElecFeeImportCommonSettings,
        json_schema_extra={"description": "Electricity fees import provider settings."},
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def providers(self) -> list[str]:
        """Available electricity fee provider ids."""
        return elecfee_provider_ids()

    # Validators
    @field_validator("provider", mode="after")
    @classmethod
    def validate_provider(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value in elecfee_provider_ids():
            return value
        raise ValueError(
            f"Provider '{value}' is not a valid electricity fees provider: {elecfee_provider_ids()}."
        )
