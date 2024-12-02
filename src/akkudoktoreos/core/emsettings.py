"""Settings for energy management.

Kept in an extra module to avoid cyclic dependencies on package import.
"""

from typing import Optional

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel


class EnergyManagementCommonSettings(SettingsBaseModel):
    """Energy Management Configuration."""

    startup_delay: float = Field(
        default=5,
        ge=1,
        description="Startup delay in seconds for EOS energy management runs.",
    )

    interval: Optional[float] = Field(
        default=None,
        description="Intervall in seconds between EOS energy management runs.",
        examples=["300"],
    )
