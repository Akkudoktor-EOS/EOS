from typing import Literal, Optional

from pydantic import Field, computed_field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import get_prediction
from akkudoktoreos.prediction.elecpriceabc import ElecPriceProvider
from akkudoktoreos.prediction.elecpriceenergycharts import (
    ElecPriceEnergyChartsCommonSettings,
)
from akkudoktoreos.prediction.elecpricefixed import ElecPriceFixedCommonSettings
from akkudoktoreos.prediction.elecpriceimport import ElecPriceImportCommonSettings
from akkudoktoreos.prediction.elecpricetibber import ElecPriceTibberCommonSettings


def elecprice_provider_ids() -> list[str]:
    """Valid elecprice provider ids."""
    try:
        prediction_eos = get_prediction()
    except Exception:
        # Prediction may not be initialized. Return static built-in provider ids.
        return [
            "ElecPriceAkkudoktor",
            "ElecPriceEnergyCharts",
            "ElecPriceFixed",
            "ElecPriceImport",
            "ElecPriceTibber",
        ]

    return [
        provider.provider_id()
        for provider in prediction_eos.providers
        if isinstance(provider, ElecPriceProvider)
    ]


class ElecPriceChargeComponent(SettingsBaseModel):
    """A single electricity price charge/fee component.

    Components are applied in order (first to last) on top of the market
    (spot working) price to build the final consumer price. A component is
    either a fixed absolute amount per kWh or a percentage add-on computed on a
    configurable basis.
    """

    name: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": "Optional name of the charge component. Used to reference this "
            "component as the basis of a later percentage component.",
            "examples": ["Netzentgelt", "Stromsteuer", "MwSt"],
        },
    )

    type: Literal["fixed", "percent"] = Field(
        json_schema_extra={
            "description": "Type of the charge component. 'fixed' adds an absolute amount per "
            "kWh, 'percent' adds a percentage of a basis.",
            "examples": ["fixed", "percent"],
        },
    )

    amount: float = Field(
        ge=0,
        json_schema_extra={
            "description": "For 'fixed' components the absolute amount per kWh [amount/kWh]. "
            "For 'percent' components the rate as a fraction (e.g. 0.19 for 19%).",
            "examples": [0.0205, 0.19],
        },
    )

    basis: Optional[list[str]] = Field(
        default=None,
        json_schema_extra={
            "description": "Only used for 'percent' components. Names of the preceding "
            "components (and/or the literal 'market' for the market price) that form the "
            "basis of the percentage. If omitted, the percentage applies to the full "
            "accumulated price so far (market price plus all preceding add-ons).",
            "examples": [["market"], ["Netzentgelt", "Stromsteuer"]],
        },
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

    charges: Optional[list[ElecPriceChargeComponent]] = Field(
        default=None,
        json_schema_extra={
            "description": "Ordered list of electricity price charge/fee components added on "
            "top of the market (spot working) price to build the final consumer price. "
            "Applied by all providers except the import provider. If omitted, the market "
            "price is used unchanged.",
            "examples": [
                [
                    {"name": "Netzentgelt", "type": "fixed", "amount": 0.1153},
                    {"name": "Stromsteuer", "type": "fixed", "amount": 0.0205},
                    {"name": "MwSt", "type": "percent", "amount": 0.19},
                ]
            ],
        },
    )

    elecpricefixed: ElecPriceFixedCommonSettings = Field(
        default_factory=ElecPriceFixedCommonSettings,
        json_schema_extra={"description": "Fixed electricity price provider settings."},
    )

    elecpriceimport: ElecPriceImportCommonSettings = Field(
        default_factory=ElecPriceImportCommonSettings,
        json_schema_extra={"description": "Electricity price import provider settings."},
    )

    energycharts: ElecPriceEnergyChartsCommonSettings = Field(
        default_factory=ElecPriceEnergyChartsCommonSettings,
        json_schema_extra={"description": "Energy Charts provider settings."},
    )

    tibber: ElecPriceTibberCommonSettings = Field(
        default_factory=ElecPriceTibberCommonSettings,
        json_schema_extra={"description": "Tibber electricity price provider settings."},
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

    def apply_charges(self, market_price_wh: float) -> float:
        """Apply the configured charge components to a per-Wh market price.

        Components are processed in order. Fixed components add an absolute
        amount per kWh (converted to per Wh). Percent components add a fraction
        of their basis: either the referenced preceding components (and/or the
        market price via the literal ``"market"``) or, when no basis is given,
        the full accumulated price so far.

        Args:
            market_price_wh: The market (spot working) price per Wh.

        Returns:
            The final consumer price per Wh.
        """
        if not self.charges:
            return market_price_wh

        # All bookkeeping in per-kWh to keep the configured amounts intuitive.
        market_kwh = market_price_wh * 1000.0
        total_kwh = market_kwh
        # Contribution of each named/anonymous component, plus the market price.
        contributions: dict[str, float] = {"market": market_kwh}

        for index, component in enumerate(self.charges):
            if component.type == "fixed":
                added = component.amount
            else:  # percent
                if component.basis is None:
                    base = total_kwh
                else:
                    base = 0.0
                    for name in component.basis:
                        if name not in contributions:
                            raise ValueError(
                                f"Charge component basis '{name}' is not a known preceding "
                                f"component or 'market'."
                            )
                        base += contributions[name]
                added = base * component.amount

            total_kwh += added
            key = component.name if component.name is not None else f"__{index}"
            contributions[key] = contributions.get(key, 0.0) + added

        return total_kwh / 1000.0
