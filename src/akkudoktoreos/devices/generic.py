from typing import Optional

import numpy as np
from pydantic import BaseModel, Field

from akkudoktoreos.devices.devicesabc import DeviceBase
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)


class HomeApplianceParameters(BaseModel):
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
        hours: Optional[int] = 24,
        provider_id: Optional[str] = None,
    ):
        # Configuration initialisation
        self.provider_id = provider_id
        self.prefix = "<invalid>"
        if self.provider_id == "GenericDishWasher":
            self.prefix = "dishwasher"
        # Parameter initialisiation
        self.parameters = parameters
        if hours is None:
            self.hours = self.total_hours
        else:
            self.hours = hours

        self.initialised = False
        # Run setup if parameters are given, otherwise setup() has to be called later when the config is initialised.
        if self.parameters is not None:
            self.setup()

    def setup(self) -> None:
        if self.initialised:
            return
        if self.provider_id is not None:
            # Setup by configuration
            self.hours = self.total_hours
            self.consumption_wh = getattr(self.config, f"{self.prefix}_consumption")
            self.duration_h = getattr(self.config, f"{self.prefix}_duration")
        elif self.parameters is not None:
            # Setup by parameters
            self.consumption_wh = (
                self.parameters.consumption_wh
            )  # Total energy consumption of the device in kWh
            self.duration_h = self.parameters.duration_h  # Duration of use in hours
        else:
            error_msg = "Parameters and provider ID missing. Can't instantiate."
            logger.error(error_msg)
            raise ValueError(error_msg)
        self.load_curve = np.zeros(self.hours)  # Initialize the load curve with zeros
        self.initialised = True

    def set_starting_time(self, start_hour: int, global_start_hour: int = 0) -> None:
        """Sets the start time of the device and generates the corresponding load curve.

        :param start_hour: The hour at which the device should start.
        :param global_start_hour: The start hour of the current optimization (defaults to current hour)
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
