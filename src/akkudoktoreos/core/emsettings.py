"""Settings for energy management.

Kept in an extra module to avoid cyclic dependencies on package import.
"""

from enum import StrEnum
from typing import Optional

from pydantic import Field, computed_field, field_validator

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
            "description": (
                f"Energy management mode "
                f"[{' | '.join(mode.value for mode in EnergyManagementMode)}]. "
                f"Defaults to {ems_default_mode()}."
            ),
            "examples": ["OPTIMIZATION"],
        },
    )

    notify_url: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "URL that receives an HTTP POST with a small JSON event after every "
                "completed optimization, so a client can fetch the new solution right "
                "away instead of polling for it. None = off."
            ),
            "examples": [None, "http://127.0.0.1:8080/api/eos/solution-ready"],
        },
    )

    @field_validator("notify_url")
    @classmethod
    def _validate_notify_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value.strip() == "":
            return None
        value = value.strip()
        if not value.startswith(("http://", "https://")):
            raise ValueError("notify_url must be an http:// or https:// URL")
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def modes(self) -> list[str]:
        """Available energy management modes."""
        return [mode.value for mode in EnergyManagementMode]
