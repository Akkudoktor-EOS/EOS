from typing import Optional

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel


class ElecPriceCommonSettings(SettingsBaseModel):
    elecprice_provider: Optional[str] = Field(
        default=None, description="Electicity price provider id of provider to be used."
    )