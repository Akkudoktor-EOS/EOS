from typing import Optional

from pydantic import Field, computed_field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import get_prediction
from akkudoktoreos.prediction.elecpriceabc import ElecPriceProvider
from akkudoktoreos.prediction.elecpriceenergycharts import (
    ElecPriceEnergyChartsCommonSettings,
)
from akkudoktoreos.prediction.elecpriceimport import ElecPriceImportCommonSettings


def elecprice_provider_ids() -> list[str]:
    """Valid elecprice provider ids."""
    try:
        prediction_eos = get_prediction()
    except:
        # Prediction may not be initialized
        # Return at least provider used in example
        return ["ElecPriceAkkudoktor"]

    return [
        provider.provider_id()
        for provider in prediction_eos.providers
        if isinstance(provider, ElecPriceProvider)
    ]


class ElecPriceCommonSettings(SettingsBaseModel):
    """Electricity Price Prediction Configuration."""

    provider: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": "Electricity price provider id of provider to be used.",
            "examples": ["ElecPriceAkkudoktor"],
        },
    )
    charges_kwh: Optional[float] = Field(
        default=None,
        ge=0,
        json_schema_extra={
            "description": "Electricity price charges [€/kWh]. Will be added to variable market price.",
            "examples": [0.21],
        },
    )
    vat_rate: Optional[float] = Field(
        default=1.19,
        ge=0,
        json_schema_extra={
            "description": "VAT rate factor applied to electricity price when charges are used.",
            "examples": [1.19],
        },
    )

    elecpriceimport: ElecPriceImportCommonSettings = Field(
        default_factory=ElecPriceImportCommonSettings,
        json_schema_extra={"description": "Import provider settings."},
    )

    energycharts: ElecPriceEnergyChartsCommonSettings = Field(
        default_factory=ElecPriceEnergyChartsCommonSettings,
        json_schema_extra={"description": "Energy Charts provider settings."},
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def providers(self) -> list[str]:
        """Available electricity price provider ids."""
        return elecprice_provider_ids()

    # Validators
    @field_validator("provider", mode="after")
    @classmethod
    def validate_provider(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value in elecprice_provider_ids():
            return value
        raise ValueError(
            f"Provider '{value}' is not a valid electricity price provider: {elecprice_provider_ids()}."
        )
