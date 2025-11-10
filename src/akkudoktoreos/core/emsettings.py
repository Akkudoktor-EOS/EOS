"""Settings for energy management.

Kept in an extra module to avoid cyclic dependencies on package import.
"""

from enum import Enum
from typing import Optional

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel


class EnergyManagementMode(str, Enum):
    """Energy management mode."""

    PREDICTION = "PREDICTION"
    OPTIMIZATION = "OPTIMIZATION"


class EnergyManagementCommonSettings(SettingsBaseModel):
    """Energy Management Configuration."""

    startup_delay: float = Field(
        default=5,
        ge=1,
        json_schema_extra={
            "description": "Startup delay in seconds for EOS energy management runs."
        },
    )

    interval: Optional[float] = Field(
        default=None,
        json_schema_extra={
            "description": "Intervall in seconds between EOS energy management runs.",
            "examples": ["300"],
        },
    )

    mode: Optional[EnergyManagementMode] = Field(
        default=None,
        json_schema_extra={
            "description": "Energy management mode [OPTIMIZATION | PREDICTION].",
            "examples": ["OPTIMIZATION", "PREDICTION"],
        },
    )
