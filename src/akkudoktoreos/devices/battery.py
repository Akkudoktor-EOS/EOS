from typing import Any, Optional

import numpy as np
from pydantic import BaseModel, Field, field_validator

from akkudoktoreos.utils.utils import NumpyEncoder


def max_charge_power_field(default: Optional[float] = None) -> Optional[float]:
    """Creates a field for maximum charging power."""
    return Field(
        default=default,
        gt=0,
        description="A float representing the maximum charging power of the battery in watts.",
    )


def initial_soc_percent_field(description: str) -> int:
    """Creates a field for the initial state of charge in percentage."""
    return Field(default=0, ge=0, le=100, description=description)


class BatteryParameters(BaseModel):
    """Base model for battery parameters."""

    capacity_wh: int = Field(gt=0, description="The capacity of the battery in watt-hours.")
    charging_efficiency: float = Field(
        default=0.88, gt=0, le=1, description="Charging efficiency of the battery."
    )
    discharging_efficiency: float = Field(default=0.88, gt=0, le=1)
    max_charge_power_w: Optional[float] = max_charge_power_field()
    initial_soc_percent: int = initial_soc_percent_field(
        "The initial state of charge of the battery in percentage."
    )
    min_soc_percent: int = Field(
        default=0,
        ge=0,
        le=100,
        description="The minimum state of charge (SOC) of the battery in percentage.",
    )
    max_soc_percent: int = Field(default=100, ge=0, le=100)


class PVBatteryParameters(BatteryParameters):
    """Model for battery parameters specific to PV systems."""

    max_charge_power_w: Optional[float] = max_charge_power_field(5000)  # why?


class ElectricCarParameters(BatteryParameters):
    """Model for battery parameters specific to electric cars."""

    discharging_efficiency: float = 1.0
    initial_soc_percent: int = initial_soc_percent_field(
        "The current state of charge (SOC) of the EV's battery in percentage."
    )


class ElectricCarResult(BaseModel):
    """Model for the result of an electric car simulation."""

    charge_array: list[float] = Field(
        description="Indicates whether the EV is charging (`0` for no charging, `1` for charging)."
    )
    discharge_array: list[int] = Field(
        description="Indicates whether the EV is discharging (`0` for no discharging, `1` for discharging)."
    )
    discharging_efficiency: float = Field(description="The discharging efficiency.")
    hours: int = Field(description="Number of hours the simulation runs.")
    capacity_wh: int = Field(description="The EV’s battery capacity in watt-hours.")
    charging_efficiency: float = Field(description="The charging efficiency.")
    max_charge_power_w: int = Field(description="The maximum charging power in watts.")
    soc_wh: float = Field(description="State of charge in watt-hours at the start.")
    initial_soc_percent: int = Field(description="Initial state of charge in percentage.")

    @field_validator(
        "discharge_array",
        "charge_array",
        mode="before",
    )
    def convert_numpy(cls, field: Any) -> Any:
        return NumpyEncoder.convert_numpy(field)[0]


class PVBattery:
    def __init__(self, parameters: BatteryParameters, hours: int = 24):
        """Initializes the battery with the provided parameters."""
        self.capacity_wh = parameters.capacity_wh
        self.initial_soc_percent = parameters.initial_soc_percent
        self.soc_wh = self._calculate_soc_wh(parameters.initial_soc_percent)
        self.hours = hours
        self.min_soc_percent = self._get_min_soc_percent(parameters)
        self.max_soc_percent = parameters.max_soc_percent
        self.min_soc_wh = self._calculate_soc_wh(self.min_soc_percent)
        self.max_soc_wh = self._calculate_soc_wh(self.max_soc_percent)
        self.discharge_array = np.full(self.hours, 1)
        self.charge_array = np.full(self.hours, 1)
        self.charging_efficiency = parameters.charging_efficiency
        self.discharging_efficiency = parameters.discharging_efficiency
        self.max_charge_power_w = parameters.max_charge_power_w or self.capacity_wh  # why??

    def _calculate_soc_wh(self, soc_percent: float) -> float:
        """Calculates the state of charge in watt-hours."""
        return (soc_percent / 100) * self.capacity_wh

    def _get_min_soc_percent(self, parameters: BatteryParameters) -> int:
        """Gets the minimum state of charge percentage."""
        if isinstance(parameters, PVBatteryParameters):
            return parameters.min_soc_percent
        return 0

    def to_dict(self) -> dict[str, Any]:
        """Converts the battery instance to a dictionary."""
        return {
            "capacity_wh": self.capacity_wh,
            "initial_soc_percent": self.initial_soc_percent,
            "soc_wh": self.soc_wh,
            "hours": self.hours,
            "discharge_array": self.discharge_array,
            "charge_array": self.charge_array,
            "charging_efficiency": self.charging_efficiency,
            "discharging_efficiency": self.discharging_efficiency,
            "max_charge_power_w": self.max_charge_power_w,
        }

    def reset(self) -> None:
        """Resets the battery state to its initial values."""
        self.soc_wh = self._calculate_soc_wh(self.initial_soc_percent)
        self.soc_wh = min(max(self.soc_wh, self.min_soc_wh), self.max_soc_wh)
        self.discharge_array = np.full(self.hours, 1)
        self.charge_array = np.full(self.hours, 1)

    def set_discharge_per_hour(self, discharge_array: np.ndarray) -> None:
        """Sets the discharge values for each hour."""
        if len(discharge_array) != self.hours:
            raise ValueError(f"Discharge array must have exactly {self.hours} elements.")
        self.discharge_array = np.array(discharge_array)

    def set_charge_per_hour(self, charge_array: np.ndarray) -> None:
        """Sets the charge values for each hour."""
        if len(charge_array) != self.hours:
            raise ValueError(f"Charge array must have exactly {self.hours} elements.")
        self.charge_array = np.array(charge_array)

    def set_charge_allowed_for_hour(self, charge: float, hour: int) -> None:
        """Sets the charge for a specific hour."""
        if hour >= self.hours:
            raise ValueError(f"Hour {hour} is out of range. Must be less than {self.hours}.")
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

        max_possible_discharge_wh = min(max_possible_discharge_wh, self.max_charge_power_w)  # why??

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
            return 0.0, 0.0

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
