from typing import Optional

from pydantic import Field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.prediction.elecpriceabc import ElecPriceProvider
from akkudoktoreos.prediction.elecpriceimport import ElecPriceImportCommonSettings
from akkudoktoreos.prediction.prediction import get_prediction

prediction_eos = get_prediction()

# Valid elecprice providers
elecprice_providers = [
    provider.provider_id()
    for provider in prediction_eos.providers
    if isinstance(provider, ElecPriceProvider)
]


class ElecPriceCommonProviderSettings(SettingsBaseModel):
    """Electricity Price Prediction Provider Configuration."""

    ElecPriceImport: Optional[ElecPriceImportCommonSettings] = Field(
        default=None,
        json_schema_extra={"description": "ElecPriceImport settings", "examples": [None]},
    )


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

    provider_settings: ElecPriceCommonProviderSettings = Field(
        default_factory=ElecPriceCommonProviderSettings,
        json_schema_extra={
            "description": "Provider settings",
            "examples": [
                # Example 1: Empty/default settings (all providers None)
                {
                    "ElecPriceImport": None,
                },
            ],
        },
    )

    # Validators
    @field_validator("provider", mode="after")
    @classmethod
    def validate_provider(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value in elecprice_providers:
            return value
        raise ValueError(
            f"Provider '{value}' is not a valid electricity price provider: {elecprice_providers}."
        )
