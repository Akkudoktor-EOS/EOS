from typing import Annotated, Optional

from pydantic import Field, computed_field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel, ValueTimeWindowSequence
from akkudoktoreos.core.coreabc import get_prediction
from akkudoktoreos.prediction.elecpriceabc import ElecPriceProvider
from akkudoktoreos.prediction.elecpriceenergycharts import (
    ElecPriceEnergyChartsCommonSettings,
)
from akkudoktoreos.prediction.elecpricefixed import ElecPriceFixedCommonSettings
from akkudoktoreos.prediction.elecpriceimport import ElecPriceImportCommonSettings
from akkudoktoreos.prediction.elecpricesmard import ElecPriceSMARDCommonSettings
from akkudoktoreos.prediction.elecpricetibber import ElecPriceTibberCommonSettings


def elecprice_provider_ids() -> list[str]:
    """Valid elecprice provider ids."""
    try:
        prediction_eos = get_prediction()
    except:
        # Prediction may not be initialized. Return static built-in provider ids.
        return [
            "ElecPriceAkkudoktor",
            "ElecPriceEnergyCharts",
            "ElecPriceFixed",
            "ElecPriceImport",
            "ElecPriceSMARD",
            "ElecPriceTibber",
        ]

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

    elecpricefixed: ElecPriceFixedCommonSettings = Field(
        default_factory=ElecPriceFixedCommonSettings,
        json_schema_extra={"description": "Fixed electricity price provider settings."},
    )

    elecpriceimport: ElecPriceImportCommonSettings = Field(
        default_factory=ElecPriceImportCommonSettings,
        json_schema_extra={"description": "Import provider settings."},
    )

    energycharts: ElecPriceEnergyChartsCommonSettings = Field(
        default_factory=ElecPriceEnergyChartsCommonSettings,
        json_schema_extra={"description": "Energy Charts provider settings."},
    )

    tibber: ElecPriceTibberCommonSettings = Field(
        default_factory=ElecPriceTibberCommonSettings,
        json_schema_extra={"description": "Tibber electricity price provider settings."},
    )

    charge_components_kwh: dict[str, Annotated[float, Field(ge=0)]] = Field(
        default_factory=dict,
        json_schema_extra={
            "description": (
                "Named constant net charge components [€/kWh]. Their sum is added to "
                "charges_kwh, variable network fees, and the market price."
            ),
            "examples": [
                {
                    "electricity_tax": 0.0205,
                    "concession_fee": 0.0132,
                    "kwkg_levy": 0.00446,
                    "section_19_levy": 0.01559,
                    "offshore_grid_levy": 0.00941,
                    "supplier_markup": 0.0,
                }
            ],
        },
    )

    smard: ElecPriceSMARDCommonSettings = Field(
        default_factory=ElecPriceSMARDCommonSettings,
        json_schema_extra={"description": "Direct SMARD electricity price provider settings."},
    )

    network_fees_kwh: ValueTimeWindowSequence = Field(
        default_factory=ValueTimeWindowSequence,
        json_schema_extra={
            "description": (
                "Recurring time windows for variable network fees [€/kWh, net]. "
                "The first matching window is added to charges_kwh and the market price."
            ),
            "examples": [
                {
                    "windows": [
                        {"start_time": "00:00", "duration": "7 hours", "value": 0.0095},
                        {"start_time": "07:00", "duration": "8 hours", "value": 0.0953},
                        {"start_time": "15:00", "duration": "5 hours", "value": 0.1565},
                        {"start_time": "20:00", "duration": "4 hours", "value": 0.0953},
                    ]
                }
            ],
        },
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
