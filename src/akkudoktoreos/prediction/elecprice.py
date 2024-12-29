from typing import Optional

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel


class ElecPriceCommonSettings(SettingsBaseModel):
    elecprice_provider: Optional[str] = Field(
        default=None, description="Electricity price provider id of provider to be used."
    )
    elecprice_charges: Optional[float] = Field(
        default=None, ge=0, description="Electricity price charges (€/kWh)."
    )
