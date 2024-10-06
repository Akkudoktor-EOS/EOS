import logging
from typing import List, Sequence


class Heatpump:
    MAX_HEATOUTPUT = 5000
    """Maximum heating power in watts"""

    BASE_HEATPOWER = 235.0
    """Base heating power value"""

    TEMPERATURE_COEFFICIENT = -11.645
    """Coefficient for temperature"""

    COP_BASE = 3.0
    """Base COP value"""

    COP_COEFFICIENT = 0.1
    """COP increase per degree"""

    def __init__(self, max_heat_output, prediction_hours):
        self.max_heat_output = max_heat_output
        self.prediction_hours = prediction_hours
        self.log = logging.getLogger(__name__)

    def __check_outside_temperature_range__(self, temp_celsius: float) -> bool:
        """Check if temperature is in valid range between -100 and 100 degree Celsius.

        Args:
            temp_celsius: Temperature in degree Celsius

        Returns:
            bool: True if in range
        """
        return temp_celsius > -100 and temp_celsius < 100

    def calculate_cop(self, outside_temperature_celsius: float) -> float:
        """Calculate the coefficient of performance (COP) based on outside temperature. Supported
        temperate range -100 degree Celsius to 100 degree Celsius.

        Args:
            outside_temperature_celsius: Outside temperature in degree Celsius

        Raise:
            ValueError: If outside temperature isn't range.

        Return:
            cop: Calculated COP based on temperature
        """
        # TODO: Support for other temperature units (e.g Fahrenheit, Kelvin)
        # Check for sensible temperature values
        if self.__check_outside_temperature_range__(outside_temperature_celsius):
            cop = self.COP_BASE + (outside_temperature_celsius * self.COP_COEFFICIENT)
            return max(cop, 1)
        else:
            err_msg = f"Outside temperature '{outside_temperature_celsius}' not in range (min: -100 Celsius, max: 100 Celsius) "
            self.log.error(err_msg)
            raise ValueError(err_msg)

    def calculate_heating_output(self, outside_temperature_celsius: float) -> float:
        """Calculate the heating output in Watts based on outside temperature in degree Celsius.
        Temperature range must be between -100 and 100 degree Celsius.

        Args:
            outside_temperature_celsius: Outside temperature in degree Celsius

        Raises:
            ValueError: Raised if outside temperature isn't in described range.

        Returns:
            heating output: Calculated heating output in Watts.
        """
        if self.__check_outside_temperature_range__(outside_temperature_celsius):
            heat_output = (
                (
                    self.BASE_HEATPOWER
                    + outside_temperature_celsius * self.TEMPERATURE_COEFFICIENT
                )
                * 1000
            ) / 24.0
            return min(self.max_heat_output, heat_output)
        else:
            err_msg = f"Outside temperature '{outside_temperature_celsius}' not in range (min: -100 Celsius, max: 100 Celsius) "
            self.log.error(err_msg)
            raise ValueError(err_msg)

    def calculate_heat_power(self, outside_temperature_celsius: float) -> float:
        """Calculate electrical power based on outside temperature (degree Celsius).

        Args:
            outside_temperature_celsius: Temperature in range -100 to 100 degree Celsius.

        Raises:
            ValueError: Raised if temperature isn't in described range

        Returns:
            power: Calculated electrical power in Watt.
        """
        if self.__check_outside_temperature_range__(outside_temperature_celsius):
            return (
                1164
                - 77.8 * outside_temperature_celsius
                + 1.62 * outside_temperature_celsius**2.0
            )
        else:
            err_msg = f"Outside temperature '{outside_temperature_celsius}' not in range (min: -100 Celsius, max: 100 Celsius) "
            self.log.error(err_msg)
            raise ValueError(err_msg)

    def simulate_24h(self, temperatures: Sequence[float]) -> List[float]:
        """Simulate power data for 24 hours based on provided temperatures."""
        power_data: List[float] = []

        if len(temperatures) != self.prediction_hours:
            raise ValueError(
                f"The temperature array must contain exactly {self.prediction_hours} entries, one for each hour of the day."
            )

        for temp in temperatures:
            power = self.calculate_heat_power(temp)
            power_data.append(power)
        return power_data


# Example usage of the class
if __name__ == "__main__":
    max_heizleistung = 5000  # 5 kW heating power
    start_innentemperatur = 15  # Initial indoor temperature
    isolationseffizienz = 0.8  # Insulation efficiency
    gewuenschte_innentemperatur = 20  # Desired indoor temperature
    wp = Heatpump(max_heizleistung, 24)  # Initialize heat pump with prediction hours

    # Print COP for various outside temperatures
    print(wp.calculate_cop(-10), " ", wp.calculate_cop(0), " ", wp.calculate_cop(10))

    # 24 hours of outside temperatures (example values)
    temperaturen = [ 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0, -1, -2, -3, -4, -5, -6, -7, -8, -9, -10, -5, -2, 5, ]  # fmt: skip

    # Calculate the 24-hour power data
    leistungsdaten = wp.simulate_24h(temperaturen)

    print(leistungsdaten)
