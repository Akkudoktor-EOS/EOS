from typing import Optional

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.devices.battery import BaseBatteryParameters
from akkudoktoreos.devices.generic import HomeApplianceParameters
from akkudoktoreos.devices.inverter import InverterParameters

logger = get_logger(__name__)


class DevicesCommonSettings(SettingsBaseModel):
    """Base configuration for devices simulation settings."""

    batteries: Optional[list[BaseBatteryParameters]] = Field(
        default=None, description="List of battery/ev devices"
    )
    inverters: Optional[list[InverterParameters]] = Field(
        default=None, description="List of inverters"
    )
    home_appliances: Optional[list[HomeApplianceParameters]] = Field(
        default=None, description="List of home appliances"
    )
