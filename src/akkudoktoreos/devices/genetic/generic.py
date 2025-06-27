import numpy as np

from akkudoktoreos.optimization.geneticdevices import HomeApplianceParameters


class HomeAppliance:
    def __init__(
        self, parameters: HomeApplianceParameters, optimization_hours: int, prediction_hours: int
    ):
        self.parameters: HomeApplianceParameters = parameters
        self.optimization_hours = optimization_hours
        self.prediction_hours = prediction_hours
        self._setup()

    def _setup(self) -> None:
        self.load_curve = np.zeros(self.prediction_hours)  # Initialize the load curve with zeros
        self.duration_h = self.parameters.duration_h
        self.consumption_wh = self.parameters.consumption_wh

    def set_starting_time(self, start_hour: int, global_start_hour: int = 0) -> None:
        """Sets the start time of the device and generates the corresponding load curve.

        :param start_hour: The hour at which the device should start.
        """
        self.reset_load_curve()
        # Check if the duration of use is within the available time frame
        if start_hour + self.duration_h > self.optimization_hours:
            raise ValueError("The duration of use exceeds the available time frame.")
        if start_hour < global_start_hour:
            raise ValueError("The start time is earlier than the available time frame.")

        # Calculate power per hour based on total consumption and duration
        power_per_hour = self.consumption_wh / self.duration_h  # Convert to watt-hours

        # Set the power for the duration of use in the load curve array
        self.load_curve[start_hour : start_hour + self.duration_h] = power_per_hour

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

    def get_latest_starting_point(self) -> int:
        """Returns the latest possible start hour at which the device can still run completely."""
        return self.optimization_hours - self.duration_h
