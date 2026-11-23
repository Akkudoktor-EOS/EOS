"""GENETIC0 home appliance simulation device."""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class Genetic0HomeApplianceParameters:
    """Parameters of a simulated home appliance.

    Attributes:
        device_id: ID of the appliance.
        consumption_wh: Energy of one run in watt-hours.
        duration_h: Duration of one run in hours.
        start_allowed: Per hour of the arrays, whether a run may start then.
        start_earliest: Earliest hour of the day a run may start.
        start_latest: Latest hour of the day a run may start.
    """

    device_id: str
    consumption_wh: int
    duration_h: int
    start_allowed: tuple[bool, ...]
    start_earliest: int
    start_latest: int


class Genetic0HomeAppliance:
    def __init__(
        self,
        parameters: Genetic0HomeApplianceParameters,
        optimization_hours: int,
        prediction_hours: int,
    ) -> None:
        """Set up the home appliance.

        Args:
            parameters: Home appliance parameters.
            optimization_hours: Optimization horizon in hours (unused).
            prediction_hours: Length of the simulated arrays.
        """
        self.parameters: Genetic0HomeApplianceParameters = parameters
        self.prediction_hours = prediction_hours
        self._setup()

    def _setup(self) -> None:
        """Sets up the home appliance parameters based provided parameters."""
        self.load_curve = np.zeros(self.prediction_hours)  # Initialize the load curve with zeros
        self.duration_h = self.parameters.duration_h
        self.consumption_wh = self.parameters.consumption_wh
        # Possible start times, evaluated from the time windows by the parameter preparation.
        self.start_allowed: list[bool] = list(self.parameters.start_allowed)
        self.start_earliest = self.parameters.start_earliest
        self.start_latest = self.parameters.start_latest

    def set_starting_time(self, start_hour: int, global_start_hour: int = 0) -> int:
        """Sets the start time of the device and generates the corresponding load curve.

        :param start_hour: The hour at which the device should start.
        """
        if not self.start_allowed[start_hour]:
            # It is not allowed (by the time windows) to start the application at this time
            if global_start_hour <= self.start_latest:
                # There is a time window left to start the appliance. Use it
                start_hour = self.start_latest
            else:
                # There is no time window left to run the application
                # Set the start into tomorrow
                start_hour = self.start_earliest + 24

        self.reset_load_curve()

        # Calculate power per hour based on total consumption and duration
        power_per_hour = self.consumption_wh / self.duration_h  # Convert to watt-hours

        # Set the power for the duration of use in the load curve array
        if start_hour < len(self.load_curve):
            end_hour = min(start_hour + self.duration_h, self.prediction_hours)
            self.load_curve[start_hour:end_hour] = power_per_hour

        return start_hour

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
