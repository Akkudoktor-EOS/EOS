import numpy as np
from pydantic import BaseModel, Field


class HaushaltsgeraetParameters(BaseModel):
    verbrauch_wh: int = Field(
        gt=0,
        description="An integer representing the energy consumption of a household device in watt-hours.",
    )
    dauer_h: int = Field(
        gt=0,
        description="An integer representing the usage duration of a household device in hours.",
    )


class Haushaltsgeraet:
    def __init__(self, parameters: HaushaltsgeraetParameters, hours=24):
        self.hours = hours  # Total duration for which the planning is done
        self.verbrauch_wh = (
            parameters.verbrauch_wh  # Total energy consumption of the device in kWh
        )
        self.dauer_h = parameters.dauer_h  # Duration of use in hours
        self.lastkurve = np.zeros(self.hours)  # Initialize the load curve with zeros

    def set_startzeitpunkt(self, start_hour, global_start_hour=0):
        """Sets the start time of the device and generates the corresponding load curve.

        :param start_hour: The hour at which the device should start.
        """
        self.reset()
        # Check if the duration of use is within the available time frame
        if start_hour + self.dauer_h > self.hours:
            raise ValueError("The duration of use exceeds the available time frame.")
        if start_hour < global_start_hour:
            raise ValueError("The start time is earlier than the available time frame.")

        # Calculate power per hour based on total consumption and duration
        leistung_pro_stunde = self.verbrauch_wh / self.dauer_h  # Convert to watt-hours

        # Set the power for the duration of use in the load curve array
        self.lastkurve[start_hour : start_hour + self.dauer_h] = leistung_pro_stunde

    def reset(self):
        """Resets the load curve."""
        self.lastkurve = np.zeros(self.hours)

    def get_lastkurve(self):
        """Returns the current load curve."""
        return self.lastkurve

    def get_last_fuer_stunde(self, hour):
        """Returns the load for a specific hour.

        :param hour: The hour for which the load is queried.
        :return: The load in watts for the specified hour.
        """
        if hour < 0 or hour >= self.hours:
            raise ValueError("The specified hour is outside the available time frame.")

        return self.lastkurve[hour]

    def spaetestmoeglicher_startzeitpunkt(self):
        """Returns the latest possible start time at which the device can still run completely."""
        return self.hours - self.dauer_h
