"""Abstract and base classes for devices."""

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel


class DevicesBaseSettings(SettingsBaseModel):
    """Base devices setting."""

    device_id: str = Field(
        default="<unknown>",
        description="ID of device",
        examples=["battery1", "ev1", "inverter1", "dishwasher"],
    )
