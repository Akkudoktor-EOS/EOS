"""Settings for energy management.

Kept in an extra module to avoid cyclic dependencies on package import.
"""

from enum import StrEnum

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel, is_home_assistant_addon


class EnergyManagementMode(StrEnum):
    """Energy management mode."""

    DISABLED = "DISABLED"
    PREDICTION = "PREDICTION"
    OPTIMIZATION = "OPTIMIZATION"


def ems_default_mode() -> EnergyManagementMode:
    """Provide default EMS mode.

    Returns OPTIMIZATION when running under Home Assistant, else DISABLED.
    """
    if is_home_assistant_addon():
        return EnergyManagementMode.OPTIMIZATION
    return EnergyManagementMode.DISABLED


class EnergyManagementCommonSettings(SettingsBaseModel):
    """Energy Management Configuration."""

    startup_delay: float = Field(
        default=5,
        ge=1,
        json_schema_extra={
            "description": "Startup delay in seconds for EOS energy management runs."
        },
    )

    interval: float = Field(
        default=300.0,
        ge=60.0,
        json_schema_extra={
            "description": "Intervall between EOS energy management runs [seconds].",
            "examples": ["300"],
        },
    )

    mode: EnergyManagementMode = Field(
        default_factory=ems_default_mode,
        json_schema_extra={
            "description": "Energy management mode [DISABLED | OPTIMIZATION | PREDICTION].",
            "examples": ["OPTIMIZATION", "PREDICTION"],
        },
    )
