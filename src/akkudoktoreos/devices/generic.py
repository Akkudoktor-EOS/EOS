import numpy as np
from pydantic import BaseModel, Field


class HomeApplianceParameters(BaseModel):
    consumption_wh: int = Field(
        gt=0,
        description="An integer representing the energy consumption of a household device in watt-hours.",
    )
    duration_h: int = Field(
        gt=0,
        description="An integer representing the usage duration of a household device in hours.",
    )


class HomeAppliance:
    def __init__(self, parameters: HomeApplianceParameters, hours=None):
        self.hours = hours  # Total duration for which the planning is done
        self.consumption_wh = (
            parameters.consumption_wh
        )  # Total energy consumption of the device in kWh
        self.duration_h = parameters.duration_h  # Duration of use in hours
        self.load_curve = np.zeros(self.hours)  # Initialize the load curve with zeros

    def set_starting_time(self, start_hour: int, global_start_hour: int = 0):
        """Sets the start time of the device and generates the corresponding load curve.

        :param start_hour: The hour at which the device should start.
        """
        self.reset()
        # Check if the duration of use is within the available time frame
        if start_hour + self.duration_h > self.hours:
            raise ValueError("The duration of use exceeds the available time frame.")
        if start_hour < global_start_hour:
            raise ValueError("The start time is earlier than the available time frame.")

        # Calculate power per hour based on total consumption and duration
        power_per_hour = self.consumption_wh / self.duration_h  # Convert to watt-hours

        # Set the power for the duration of use in the load curve array
        self.load_curve[start_hour : start_hour + self.duration_h] = power_per_hour

    def reset(self):
        """Resets the load curve."""
        self.load_curve = np.zeros(self.hours)

    def get_load_curve(self):
        """Returns the current load curve."""
        return self.load_curve

    def get_load_for_hour(self, hour):
        """Returns the load for a specific hour.

        :param hour: The hour for which the load is queried.
        :return: The load in watts for the specified hour.
        """
        if hour < 0 or hour >= self.hours:
            raise ValueError("The specified hour is outside the available time frame.")

        return self.load_curve[hour]

    def get_latest_starting_point(self):
        """Returns the latest possible start time at which the device can still run completely."""
        return self.hours - self.duration_h
