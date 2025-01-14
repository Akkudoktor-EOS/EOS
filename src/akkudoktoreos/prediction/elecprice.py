from typing import Optional

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.prediction.elecpriceimport import ElecPriceImportCommonSettings


class ElecPriceCommonSettings(SettingsBaseModel):
    """Electricity Price Prediction Configuration."""

    elecprice_provider: Optional[str] = Field(
        default=None,
        description="Electricity price provider id of provider to be used.",
        examples=["ElecPriceAkkudoktor"],
    )
    elecprice_charges_kwh: Optional[float] = Field(
        default=None, ge=0, description="Electricity price charges (€/kWh).", examples=[0.21]
    )

    provider_settings: Optional[ElecPriceImportCommonSettings] = Field(
        default=None, description="Provider settings", examples=[None]
    )
