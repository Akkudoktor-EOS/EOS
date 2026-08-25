"""Base classe for all device settings.

This module contains the building blocks that every device settings class
depends on.:

- ``DevicesBaseSettings``: common ``device_id`` field for all devices.

Nothing in this module is optimizer-specific.
"""

import secrets
import string

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel

# ============================================================
# Base settings
# ============================================================


def device_default_id() -> str:
    """Provide random default device id."""
    alphabet = string.ascii_letters + string.digits
    device_id = "".join(secrets.choice(alphabet) for _ in range(10))
    return device_id


class DevicesBaseSettings(SettingsBaseModel):
    """Base devices setting."""

    device_id: str = Field(
        default_factory=device_default_id,
        json_schema_extra={
            "description": "ID of device",
            "examples": ["battery1", "ev1", "inverter1", "dishwasher"],
        },
    )
