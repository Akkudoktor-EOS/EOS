import json
from datetime import datetime, timedelta, timezone
import numpy as np
from pprint import pprint


class Waermepumpe:
    MAX_HEIZLEISTUNG = 5000  # Maximum heating power in watts
    BASE_HEIZLEISTUNG = 235.0  # Base heating power value
    TEMPERATURE_COEFFICIENT = -11.645  # Coefficient for temperature
    COP_BASE = 3.0  # Base COP value
    COP_COEFFICIENT = 0.1  # COP increase per degree

    def __init__(self, max_heizleistung, prediction_hours):
        self.max_heizleistung = max_heizleistung
        self.prediction_hours = prediction_hours

    def cop_berechnen(self, aussentemperatur):
        """Calculate the coefficient of performance (COP) based on outside temperature."""
        cop = self.COP_BASE + (aussentemperatur * self.COP_COEFFICIENT)
        return max(cop, 1)

    def heizleistung_berechnen(self, aussentemperatur):
        """Calculate heating power based on outside temperature."""
        heizleistung = ((self.BASE_HEIZLEISTUNG + aussentemperatur * self.TEMPERATURE_COEFFICIENT) * 1000) / 24.0
        return min(self.max_heizleistung, heizleistung)

    def elektrische_leistung_berechnen(self, aussentemperatur):
        """Calculate electrical power based on outside temperature."""
        return 1164 - 77.8 * aussentemperatur + 1.62 * aussentemperatur ** 2.0

    def simulate_24h(self, temperaturen):
        """Simulate power data for 24 hours based on provided temperatures."""
        leistungsdaten = []

        if len(temperaturen) != self.prediction_hours:
            raise ValueError("The temperature array must contain exactly " + str(self.prediction_hours) + " entries, one for each hour of the day.")
        
        for temp in temperaturen:
            elektrische_leistung = self.elektrische_leistung_berechnen(temp)
            leistungsdaten.append(elektrische_leistung)
        return leistungsdaten


# Example usage of the class
if __name__ == '__main__':
    max_heizleistung = 5000  # 5 kW heating power
    start_innentemperatur = 15  # Initial indoor temperature
    isolationseffizienz = 0.8  # Insulation efficiency
    gewuenschte_innentemperatur = 20  # Desired indoor temperature
    wp = Waermepumpe(max_heizleistung, 24)  # Initialize heat pump with prediction hours

    # Print COP for various outside temperatures
    print(wp.cop_berechnen(-10), " ", wp.cop_berechnen(0), " ", wp.cop_berechnen(10))

    # 24 hours of outside temperatures (example values)
    temperaturen = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0, -1, -2, -3, -4, -5, -6, -7, -8, -9, -10, -5, -2, 5]

    # Calculate the 24-hour power data
    leistungsdaten = wp.simulate_24h(temperaturen)

    print(leistungsdaten)
