import numpy as np


class DomesticAppliance:
    def __init__(self, hours=None, consumption_wh=None, duration_h=None):
        self.hours = hours  # Total duration for which the planning is done
        self.consumption_wh = consumption_wh  # Total energy consumption of the device in kWh
        self.duration_h = duration_h  # Duration of use in hours
        self.lastkurve = np.zeros(self.hours)  # Initialize the load curve with zeros

    def set_start_time(self, start_hour, global_start_hour=0):
        """
        Sets the start time of the device and generates the corresponding load curve.
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
        self.power_profile[start_hour : start_hour + self.duration_h] = power_per_hour

    def reset(self):
        """
        Resets the load curve.
        """
        self.power_profile = np.zeros(self.hours)

    def fetch_power_profile(self):
        """
        Returns the current load curve.
        """
        return self.power_profile

    def fetch_load_for_hour(self, hour):
        """
        Returns the load for a specific hour.
        :param hour: The hour for which the load is queried.
        :return: The load in watts for the specified hour.
        """
        if hour < 0 or hour >= self.hours:
            raise ValueError("The specified hour is outside the available time frame.")

        return self.power_profile[hour]

    def calculate_latest_start_time(self):
        """
        Returns the latest possible start time at which the device can still run completely.
        """
        return self.hours - self.duration_h
