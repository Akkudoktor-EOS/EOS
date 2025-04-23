from typing import Optional

from akkudoktoreos.core.coreabc import SingletonMixin
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.devices.battery import Battery
from akkudoktoreos.devices.devicesabc import DevicesBase
from akkudoktoreos.devices.generic import HomeAppliance
from akkudoktoreos.devices.inverter import Inverter
from akkudoktoreos.devices.settings import DevicesCommonSettings

logger = get_logger(__name__)


class Devices(SingletonMixin, DevicesBase):
    def __init__(self, settings: Optional[DevicesCommonSettings] = None):
        if hasattr(self, "_initialized"):
            return
        super().__init__()
        if settings is None:
            settings = self.config.devices
            if settings is None:
                return

        # initialize devices
        if settings.batteries is not None:
            for battery_params in settings.batteries:
                self.add_device(Battery(battery_params))
        if settings.inverters is not None:
            for inverter_params in settings.inverters:
                self.add_device(Inverter(inverter_params))
        if settings.home_appliances is not None:
            for home_appliance_params in settings.home_appliances:
                self.add_device(HomeAppliance(home_appliance_params))

        self.post_setup()

    def post_setup(self) -> None:
        for device in self.devices.values():
            device.post_setup()


# Initialize the Devices simulation, it is a singleton.
devices: Optional[Devices] = None


def get_devices() -> Devices:
    global devices
    # Fix circular import at runtime
    if devices is None:
        devices = Devices()
    """Gets the EOS Devices simulation."""
    return devices
