from typing import Any, Optional

import numpy as np

from akkudoktoreos.optimization.genetic.geneticdevices import (
    BaseBatteryParameters,
    SolarPanelBatteryParameters,
)


class Battery:
    """Represents a battery device with methods to simulate energy charging and discharging."""

    def __init__(self, parameters: BaseBatteryParameters, prediction_hours: int):
        self.parameters = parameters
        self.prediction_hours = prediction_hours
        self._setup()

    def _setup(self) -> None:
        """Sets up the battery parameters based on configuration or provided parameters."""
        self.capacity_wh = self.parameters.capacity_wh
        self.initial_soc_percentage = self.parameters.initial_soc_percentage
        self.charging_efficiency = self.parameters.charging_efficiency
        self.discharging_efficiency = self.parameters.discharging_efficiency

        # Only assign for storage battery
        self.min_soc_percentage = (
            self.parameters.min_soc_percentage
            if isinstance(self.parameters, SolarPanelBatteryParameters)
            else 0
        )
        self.max_soc_percentage = self.parameters.max_soc_percentage

        # Initialize state of charge
        if self.parameters.max_charge_power_w is not None:
            self.max_charge_power_w = self.parameters.max_charge_power_w
        else:
            self.max_charge_power_w = self.capacity_wh  # TODO this should not be equal capacity_wh
        self.discharge_array = np.full(self.prediction_hours, 1)
        self.charge_array = np.full(self.prediction_hours, 1)
        self.soc_wh = (self.initial_soc_percentage / 100) * self.capacity_wh
        self.min_soc_wh = (self.min_soc_percentage / 100) * self.capacity_wh
        self.max_soc_wh = (self.max_soc_percentage / 100) * self.capacity_wh

    def to_dict(self) -> dict[str, Any]:
        """Converts the object to a dictionary representation."""
        return {
            "device_id": self.parameters.device_id,
            "capacity_wh": self.capacity_wh,
            "initial_soc_percentage": self.initial_soc_percentage,
            "soc_wh": self.soc_wh,
            "hours": self.prediction_hours,
            "discharge_array": self.discharge_array,
            "charge_array": self.charge_array,
            "charging_efficiency": self.charging_efficiency,
            "discharging_efficiency": self.discharging_efficiency,
            "max_charge_power_w": self.max_charge_power_w,
        }

    def reset(self) -> None:
        """Resets the battery state to its initial values."""
        self.soc_wh = (self.initial_soc_percentage / 100) * self.capacity_wh
        self.soc_wh = min(max(self.soc_wh, self.min_soc_wh), self.max_soc_wh)
        self.discharge_array = np.full(self.prediction_hours, 1)
        self.charge_array = np.full(self.prediction_hours, 1)

    def set_discharge_per_hour(self, discharge_array: np.ndarray) -> None:
        """Sets the discharge values for each hour."""
        if len(discharge_array) != self.prediction_hours:
            raise ValueError(
                f"Discharge array must have exactly {self.prediction_hours} elements. Got {len(discharge_array)} elements."
            )
        self.discharge_array = np.array(discharge_array)

    def set_charge_per_hour(self, charge_array: np.ndarray) -> None:
        """Sets the charge values for each hour."""
        if len(charge_array) != self.prediction_hours:
            raise ValueError(
                f"Charge array must have exactly {self.prediction_hours} elements. Got {len(charge_array)} elements."
            )
        self.charge_array = np.array(charge_array)

    def set_charge_allowed_for_hour(self, charge: float, hour: int) -> None:
        """Sets the charge for a specific hour."""
        if hour >= self.prediction_hours:
            raise ValueError(
                f"Hour {hour} is out of range. Must be less than {self.prediction_hours}."
            )
        self.charge_array[hour] = charge

    def current_soc_percentage(self) -> float:
        """Calculates the current state of charge in percentage."""
        return (self.soc_wh / self.capacity_wh) * 100

    def discharge_energy(self, wh: float, hour: int) -> tuple[float, float]:
        """Discharges energy from the battery."""
        if self.discharge_array[hour] == 0:
            return 0.0, 0.0

        max_possible_discharge_wh = (self.soc_wh - self.min_soc_wh) * self.discharging_efficiency
        max_possible_discharge_wh = max(max_possible_discharge_wh, 0.0)

        max_possible_discharge_wh = min(
            max_possible_discharge_wh, self.max_charge_power_w
        )  # TODO make a new cfg variable max_discharge_power_w

        actual_discharge_wh = min(wh, max_possible_discharge_wh)
        actual_withdrawal_wh = (
            actual_discharge_wh / self.discharging_efficiency
            if self.discharging_efficiency > 0
            else 0.0
        )

        self.soc_wh -= actual_withdrawal_wh
        self.soc_wh = max(self.soc_wh, self.min_soc_wh)

        losses_wh = actual_withdrawal_wh - actual_discharge_wh
        return actual_discharge_wh, losses_wh

    def charge_energy(
        self, wh: Optional[float], hour: int, relative_power: float = 0.0
    ) -> tuple[float, float]:
        """Charges energy into the battery."""
        if hour is not None and self.charge_array[hour] == 0:
            return 0.0, 0.0  # Charging not allowed in this hour

        if relative_power > 0.0:
            wh = self.max_charge_power_w * relative_power

        wh = wh if wh is not None else self.max_charge_power_w

        max_possible_charge_wh = (
            (self.max_soc_wh - self.soc_wh) / self.charging_efficiency
            if self.charging_efficiency > 0
            else 0.0
        )
        max_possible_charge_wh = max(max_possible_charge_wh, 0.0)

        effective_charge_wh = min(wh, max_possible_charge_wh)
        charged_wh = effective_charge_wh * self.charging_efficiency

        self.soc_wh += charged_wh
        self.soc_wh = min(self.soc_wh, self.max_soc_wh)

        losses_wh = effective_charge_wh - charged_wh
        return charged_wh, losses_wh

    def current_energy_content(self) -> float:
        """Returns the current usable energy in the battery."""
        usable_energy = (self.soc_wh - self.min_soc_wh) * self.discharging_efficiency
        return max(usable_energy, 0.0)
