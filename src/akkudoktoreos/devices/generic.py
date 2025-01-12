from typing import Optional

import numpy as np
from pydantic import Field

from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.devices.devicesabc import DeviceBase, DeviceParameters

logger = get_logger(__name__)


class HomeApplianceParameters(DeviceParameters):
    device_id: str = Field(description="ID of home appliance")
    consumption_wh: int = Field(
        gt=0,
        description="An integer representing the energy consumption of a household device in watt-hours.",
    )
    duration_h: int = Field(
        gt=0,
        description="An integer representing the usage duration of a household device in hours.",
    )


class HomeAppliance(DeviceBase):
    def __init__(
        self,
        parameters: Optional[HomeApplianceParameters] = None,
    ):
        self.parameters: Optional[HomeApplianceParameters] = None
        super().__init__(parameters)

    def _setup(self) -> None:
        assert self.parameters is not None
        self.load_curve = np.zeros(self.hours)  # Initialize the load curve with zeros
        self.duration_h = self.parameters.duration_h
        self.consumption_wh = self.parameters.consumption_wh

    def set_starting_time(self, start_hour: int, global_start_hour: int = 0) -> None:
        """Sets the start time of the device and generates the corresponding load curve.

        :param start_hour: The hour at which the device should start.
        """
        self.reset_load_curve()
        # Check if the duration of use is within the available time frame
        if start_hour + self.duration_h > self.hours:
            raise ValueError("The duration of use exceeds the available time frame.")
        if start_hour < global_start_hour:
            raise ValueError("The start time is earlier than the available time frame.")

        # Calculate power per hour based on total consumption and duration
        power_per_hour = self.consumption_wh / self.duration_h  # Convert to watt-hours

        # Set the power for the duration of use in the load curve array
        self.load_curve[start_hour : start_hour + self.duration_h] = power_per_hour

    def reset_load_curve(self) -> None:
        """Resets the load curve."""
        self.load_curve = np.zeros(self.hours)

    def get_load_curve(self) -> np.ndarray:
        """Returns the current load curve."""
        return self.load_curve

    def get_load_for_hour(self, hour: int) -> float:
        """Returns the load for a specific hour.

        :param hour: The hour for which the load is queried.
        :return: The load in watts for the specified hour.
        """
        if hour < 0 or hour >= self.hours:
            raise ValueError("The specified hour is outside the available time frame.")

        return self.load_curve[hour]

    def get_latest_starting_point(self) -> int:
        """Returns the latest possible start time at which the device can still run completely."""
        return self.hours - self.duration_h
