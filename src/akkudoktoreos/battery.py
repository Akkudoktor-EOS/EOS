from typing import Tuple

import numpy as np


class Battery:
    def __init__(
        self,
        capacity_wh: int = 0,
        hours: int = 0,
        charge_efficiency: float = 0.88,
        discharge_efficiency: float = 0.88,
        max_charging_power_w: float = None,
        start_soc_percent: int = 0,
        min_soc_percent: int = 0,
        max_soc_percent: int = 100,
    ):
        """Base class for an energy storage battery.

        Args:
            capacity_wh (optional): Battery capacity in Watt hours. Defaults to 0.
            hours (optional): Amount of hours battery is simulated. Defaults to 0.
            charge_efficiency (optional): Value between 0 to 1.0. Defaults to 0.88.
            discharge_efficiency (optional): Value between 0 to 1.0. Defaults to 0.88.
            max_charging_power_w (optional): Power to charge battery in Watt. Defaults to None.
            start_soc_percent (optional): State of charge (SoC) in percent. Defaults to 0.
            min_soc_percent (optional): Minimal SoC in percent. Defaults to 0.
            max_soc_percent (optional): Maximum SoC in percent. Defaults to 100.
        """
        self.capacity_wh = capacity_wh
        self.start_soc_percent = start_soc_percent
        self.soc_wh = (start_soc_percent / 100) * capacity_wh
        self.hours = hours if hours is not None else 24  # Default to 24 hours if not specified
        self.discharge_array = np.full(self.hours, 1)
        self.charge_array = np.full(self.hours, 1)
        # Charge and discharge efficiency
        self.charge_efficiency = charge_efficiency
        self.discharge_efficiency = discharge_efficiency
        self.max_charging_power_w = (
            max_charging_power_w if max_charging_power_w else self.capacity_wh
        )
        self.min_soc_percent = min_soc_percent
        self.max_soc_percent = max_soc_percent
        # Calculate min and max SoC in Wh
        self.min_soc_wh = (self.min_soc_percent / 100) * self.capacity_wh
        self.max_soc_wh = (self.max_soc_percent / 100) * self.capacity_wh

    def to_dict(self) -> dict:
        """Convert Battery object to dictionary.

        Returns:
            dict: dictionary containing all data
        """
        return {
            "capacity_wh": self.capacity_wh,
            "start_soc_percent": self.start_soc_percent,
            "soc_wh": self.soc_wh,
            "hours": self.hours,
            "discharge_array": self.discharge_array.tolist(),  # Convert np.array to list
            "charge_array": self.charge_array.tolist(),
            "charge_efficiency": self.charge_efficiency,
            "discharge_efficiency": self.discharge_efficiency,
            "max_charge_power_w": self.max_charging_power_w,
        }

    @classmethod
    def from_dict(cls, data: dict):
        """Create a Battery object from dictionary.

        Args:
            data: Dictionary containing a single battery object.

        Returns:
            _type_: _description_
        """
        obj = cls(
            data.get("capacity_wh", 0),
            data.get("hours", 0),
            data.get("charging_efficiency"),
            data.get("discharge_efficiency"),
            data.get("start_soc_percent", 0),
            data.get("max_charge_power_w"),
            data.get("min_soc_percent", 0),
            data.get("max_soc_percent", 100),
        )
        # Set arrays
        obj.discharge_array = np.array(data["discharge_array"])
        obj.charge_array = np.array(data["charge_array"])
        obj.soc_wh = data.get(
            "soc_wh"
        )  # Set current state of charge, which may differ from start_soc_prozent
        return obj

    def reset(self):
        """Reset battery fields 'soc_wh', 'discharge_array' and 'charge_array'."""
        self.soc_wh = (self.start_soc_percent / 100) * self.capacity_wh
        # Ensure soc_wh is within min and max limits
        self.soc_wh = min(max(self.soc_wh, self.min_soc_wh), self.max_soc_wh)

        self.discharge_array = np.full(self.hours, 1)
        self.charge_array = np.full(self.hours, 1)

    def set_discharge_per_hour(self, discharge_array):
        assert len(discharge_array) == self.hours
        self.discharge_array = np.array(discharge_array)

    def set_charge_allowed_for_hour(self, charge, hour):
        assert hour < self.hours
        self.charge_array[hour] = charge 

    def set_charge_per_hour(self, charge_array):
        assert len(charge_array) == self.hours
        self.charge_array = np.array(charge_array)

    def charge_state_percent(self) -> float:
        return (self.soc_wh / self.capacity_wh) * 100

    def discharge(self, wh: int, hour: int) -> Tuple[float, float]:
        """Discharge battery with provided watt hours and duration in hours.

        Args:
            wh: Watt hours to discharge
            hour: Time of discharge

        Returns:
            Tuple[float, float]: actual released watt hours, loss watt hours
        """
        if self.discharge_array[hour - 1] == 0:
            return 0.0, 0.0  # No energy discharge and no losses

        # Calculate the maximum energy that can be discharged considering min_soc and efficiency
        max_possible_discharge_wh = (self.soc_wh - self.min_soc_wh) * self.discharge_efficiency
        max_possible_discharge_wh = max(max_possible_discharge_wh, 0.0)  # Ensure non-negative

        # Consider the maximum discharge power of the battery
        max_released_wh = min(max_possible_discharge_wh, self.max_charging_power_w)

        # The actually discharged energy cannot exceed requested energy or maximum discharge
        actual_released_wh = min(wh, max_released_wh)

        # Calculate the actual amount withdrawn from the battery (before efficiency loss)
        if self.discharge_efficiency > 0:
            actual_withdrawal_wh = actual_released_wh / self.discharge_efficiency
        else:
            actual_withdrawal_wh = 0.0

        # Update the state of charge considering the actual withdrawal
        self.soc_wh -= actual_withdrawal_wh
        # Ensure soc_wh does not go below min_soc_wh
        self.soc_wh = max(self.soc_wh, self.min_soc_wh)

        # Calculate losses due to efficiency
        loss_wh = actual_withdrawal_wh - actual_released_wh

        # Return the actually discharged energy and the losses
        return actual_released_wh, loss_wh

    def charge(self, wh: int, hour: int, relative_power=0.0) -> Tuple[float, float]:
        """Charge battery with provided watt hours and time in hours.

        Args:
            wh (int): Watt hours to charge
            hour (int): Time to charge
            relative_power (float): Default=0.0

        Returns:
            Tuple[float, float]: Charged energy, loss watt hours
        """
        if hour and self.charge_array[hour - 1] == 0:
            return 0, 0  # Charging not allowed in this hour
        if relative_power > 0.0:
            wh = self.max_charging_power_w * relative_power

        # If no value for wh is given, use the maximum charging power
        wh = wh if wh is not None else self.max_charging_power_w

        # Relative to the maximum charging power (between 0 and 1)
        relative_charge_power = self.charge_array[hour - 1]
        effective_charge_power = relative_charge_power * self.max_charging_power_w

        # Calculate the maximum energy that can be charged considering max_soc and efficiency
        if self.charge_efficiency > 0:
            max_possible_charge_wh = (self.max_soc_wh - self.soc_wh) / self.charge_efficiency
        else:
            max_possible_charge_wh = 0.0
        max_possible_charge_wh = max(max_possible_charge_wh, 0.0)  # Ensure non-negative

        # The actually charged energy cannot exceed requested energy, charging power, or maximum possible charge
        effective_charge = min(wh, effective_charge_power, max_possible_charge_wh)

        # Energy actually stored in the battery
        charged_energy = effective_charge * self.charge_efficiency

        # Update soc_wh
        self.soc_wh += charged_energy
        # Ensure soc_wh does not exceed max_soc_wh
        self.soc_wh = min(self.soc_wh, self.max_soc_wh)

        # Calculate losses
        loss_wh = effective_charge - charged_energy

        return charged_energy, loss_wh

    def current_energy(self) -> float:
        """This method returns the current remaining energy considering efficiency.

        It accounts for both charging and discharging efficiency.

        Returns:
            max(usable_energy, 0.0)
        """
        # Calculate remaining energy considering discharge efficiency
        usable_energy = (self.soc_wh - self.min_soc_wh) * self.discharge_efficiency
        return max(usable_energy, 0.0)


if __name__ == "__main__":
    # Test battery discharge below min_soc
    print("Test: Discharge below min_soc")
    akku = Battery(
        capacity_wh=10000,
        hours=1,
        start_soc_percent=50,
        min_soc_percent=20,
        max_soc_percent=80,
    )
    akku.reset()
    print(f"Initial SoC: {akku.charge_state_percent()}%")

    # Try to discharge 5000 Wh
    abgegeben_wh, verlust_wh = akku.discharge(5000, 0)
    print(f"Energy discharged: {abgegeben_wh} Wh, Losses: {verlust_wh} Wh")
    print(f"SoC after discharge: {akku.charge_state_percent()}%")
    print(f"Expected min SoC: {akku.min_soc_percent}%")

    # Test battery charge above max_soc
    print("\nTest: Charge above max_soc")
    akku = Battery(
        capacity_wh=10000,
        hours=1,
        start_soc_percent=50,
        min_soc_percent=20,
        max_soc_percent=80,
    )
    akku.reset()
    print(f"Initial SoC: {akku.charge_state_percent()}%")

    # Try to charge 5000 Wh
    geladen_wh, verlust_wh = akku.charge(5000, 0)
    print(f"Energy charged: {geladen_wh} Wh, Losses: {verlust_wh} Wh")
    print(f"SoC after charge: {akku.charge_state_percent()}%")
    print(f"Expected max SoC: {akku.max_soc_percent}%")

    # Test charging when battery is at max_soc
    print("\nTest: Charging when at max_soc")
    akku = Battery(
        capacity_wh=10000,
        hours=1,
        start_soc_percent=80,
        min_soc_percent=20,
        max_soc_percent=80,
    )
    akku.reset()
    print(f"Initial SoC: {akku.charge_state_percent()}%")

    geladen_wh, verlust_wh = akku.charge(5000, 0)
    print(f"Energy charged: {geladen_wh} Wh, Losses: {verlust_wh} Wh")
    print(f"SoC after charge: {akku.charge_state_percent()}%")

    # Test discharging when battery is at min_soc
    print("\nTest: Discharging when at min_soc")
    akku = Battery(
        capacity_wh=10000,
        hours=1,
        start_soc_percent=20,
        min_soc_percent=20,
        max_soc_percent=80,
    )
    akku.reset()
    print(f"Initial SoC: {akku.charge_state_percent()}%")

    abgegeben_wh, verlust_wh = akku.discharge(5000, 0)
    print(f"Energy discharged: {abgegeben_wh} Wh, Losses: {verlust_wh} Wh")
    print(f"SoC after discharge: {akku.charge_state_percent()}%")
