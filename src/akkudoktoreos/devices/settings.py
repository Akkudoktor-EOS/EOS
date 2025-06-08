from typing import Optional

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.devices.battery import BaseBatteryParameters
from akkudoktoreos.devices.generic import HomeApplianceParameters
from akkudoktoreos.devices.inverter import InverterParameters


class DevicesCommonSettings(SettingsBaseModel):
    """Base configuration for devices simulation settings."""

    batteries: Optional[list[BaseBatteryParameters]] = Field(
        default=None,
        description="List of battery/ev devices",
        examples=[[{"device_id": "battery1", "capacity_wh": 8000}]],
    )
    inverters: Optional[list[InverterParameters]] = Field(
        default=None, description="List of inverters", examples=[[]]
    )
    home_appliances: Optional[list[HomeApplianceParameters]] = Field(
        default=None, description="List of home appliances", examples=[[]]
    )
