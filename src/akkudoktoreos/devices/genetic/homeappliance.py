from typing import Optional

import numpy as np

from akkudoktoreos.optimization.genetic.geneticdevices import HomeApplianceParameters
from akkudoktoreos.utils.datetimeutil import (
    TimeWindow,
    TimeWindowSequence,
    to_datetime,
    to_duration,
    to_time,
)


class HomeAppliance:
    def __init__(
        self,
        parameters: HomeApplianceParameters,
        optimization_hours: int,
        prediction_hours: int,
    ):
        self.parameters: HomeApplianceParameters = parameters
        self.prediction_hours = prediction_hours
        self._setup()

    def _setup(self) -> None:
        """Sets up the home appliance parameters based provided parameters."""
        self.load_curve = np.zeros(self.prediction_hours)  # Initialize the load curve with zeros
        self.duration_h = self.parameters.duration_h
        self.consumption_wh = self.parameters.consumption_wh
        self.appliance_start: Optional[int] = None
        # setup possible start times
        if self.parameters.time_windows is None:
            self.parameters.time_windows = TimeWindowSequence(
                windows=[
                    TimeWindow(
                        start_time=to_time("00:00"),
                        duration=to_duration(f"{self.prediction_hours} hours"),
                    ),
                ]
            )
        start_datetime = to_datetime().set(hour=0, minute=0, second=0)
        duration = to_duration(f"{self.duration_h} hours")
        self.start_allowed: list[bool] = []
        for hour in range(0, self.prediction_hours):
            self.start_allowed.append(
                self.parameters.time_windows.contains(
                    start_datetime.add(hours=hour), duration=duration
                )
            )
        start_earliest = self.parameters.time_windows.earliest_start_time(duration, start_datetime)
        if start_earliest:
            self.start_earliest = start_earliest.hour
        else:
            self.start_earliest = 0
        start_latest = self.parameters.time_windows.latest_start_time(duration, start_datetime)
        if start_latest:
            self.start_latest = start_latest.hour
        else:
            self.start_latest = 23

    def set_starting_time(self, start_hour: int, global_start_hour: int = 0) -> None:
        """Sets the start time of the device and generates the corresponding load curve.

        :param start_hour: The hour at which the device should start.
        """
        self.reset_load_curve()

        # Check if the duration of use is within the available time windows
        if not self.start_allowed[start_hour]:
            # No available time window to start home appliance
            # Use the earliest one
            start_hour = self.start_earliest

        # Check if it is possibility to start the appliance
        if start_hour < global_start_hour:
            # Start is before current time
            # Use the latest one
            start_hour = self.start_latest

        # Calculate power per hour based on total consumption and duration
        power_per_hour = self.consumption_wh / self.duration_h  # Convert to watt-hours

        # Set the power for the duration of use in the load curve array
        self.load_curve[start_hour : start_hour + self.duration_h] = power_per_hour

        # Set the selected start hour
        self.appliance_start = start_hour

    def reset_load_curve(self) -> None:
        """Resets the load curve."""
        self.load_curve = np.zeros(self.prediction_hours)

    def get_load_curve(self) -> np.ndarray:
        """Returns the current load curve."""
        return self.load_curve

    def get_load_for_hour(self, hour: int) -> float:
        """Returns the load for a specific hour.

        :param hour: The hour for which the load is queried.
        :return: The load in watts for the specified hour.
        """
        if hour < 0 or hour >= self.prediction_hours:
            raise ValueError(
                f"The specified hour {hour} is outside the available time frame {self.prediction_hours}."
            )

        return self.load_curve[hour]

    def get_appliance_start(self) -> Optional[int]:
        return self.appliance_start
