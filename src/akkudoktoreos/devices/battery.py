from typing import Any, Optional

import numpy as np
from pydantic import Field, field_validator

from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.devices.devicesabc import (
    DeviceBase,
    DeviceOptimizeResult,
    DeviceParameters,
)
from akkudoktoreos.utils.utils import NumpyEncoder

logger = get_logger(__name__)


def max_charging_power_field(description: Optional[str] = None) -> float:
    if description is None:
        description = "Maximum charging power in watts."
    return Field(
        default=5000,
        gt=0,
        description=description,
    )


def initial_soc_percentage_field(description: str) -> int:
    return Field(default=0, ge=0, le=100, description=description, examples=[42])


def discharging_efficiency_field(default_value: float) -> float:
    return Field(
        default=default_value,
        gt=0,
        le=1,
        description="A float representing the discharge efficiency of the battery.",
    )


class BaseBatteryParameters(DeviceParameters):
    """Battery Device Simulation Configuration."""

    device_id: str = Field(description="ID of battery", examples=["battery1"])
    capacity_wh: int = Field(
        gt=0,
        description="An integer representing the capacity of the battery in watt-hours.",
        examples=[8000],
    )
    charging_efficiency: float = Field(
        default=0.88,
        gt=0,
        le=1,
        description="A float representing the charging efficiency of the battery.",
    )
    discharging_efficiency: float = discharging_efficiency_field(0.88)
    max_charge_power_w: Optional[float] = max_charging_power_field()
    initial_soc_percentage: int = initial_soc_percentage_field(
        "An integer representing the state of charge of the battery at the **start** of the current hour (not the current state)."
    )
    min_soc_percentage: int = Field(
        default=0,
        ge=0,
        le=100,
        description="An integer representing the minimum state of charge (SOC) of the battery in percentage.",
        examples=[10],
    )
    max_soc_percentage: int = Field(
        default=100,
        ge=0,
        le=100,
        description="An integer representing the maximum state of charge (SOC) of the battery in percentage.",
    )


class SolarPanelBatteryParameters(BaseBatteryParameters):
    max_charge_power_w: Optional[float] = max_charging_power_field()


class ElectricVehicleParameters(BaseBatteryParameters):
    """Battery Electric Vehicle Device Simulation Configuration."""

    device_id: str = Field(description="ID of electric vehicle", examples=["ev1"])
    discharging_efficiency: float = discharging_efficiency_field(1.0)
    initial_soc_percentage: int = initial_soc_percentage_field(
        "An integer representing the current state of charge (SOC) of the battery in percentage."
    )


class ElectricVehicleResult(DeviceOptimizeResult):
    """Result class containing information related to the electric vehicle's charging and discharging behavior."""

    device_id: str = Field(description="ID of electric vehicle", examples=["ev1"])
    charge_array: list[float] = Field(
        description="Hourly charging status (0 for no charging, 1 for charging)."
    )
    discharge_array: list[int] = Field(
        description="Hourly discharging status (0 for no discharging, 1 for discharging)."
    )
    discharging_efficiency: float = Field(description="The discharge efficiency as a float..")
    capacity_wh: int = Field(description="Capacity of the EV’s battery in watt-hours.")
    charging_efficiency: float = Field(description="Charging efficiency as a float..")
    max_charge_power_w: int = Field(description="Maximum charging power in watts.")
    soc_wh: float = Field(
        description="State of charge of the battery in watt-hours at the start of the simulation."
    )
    initial_soc_percentage: int = Field(
        description="State of charge at the start of the simulation in percentage."
    )

    @field_validator("discharge_array", "charge_array", mode="before")
    def convert_numpy(cls, field: Any) -> Any:
        return NumpyEncoder.convert_numpy(field)[0]


class Battery(DeviceBase):
    """Represents a battery device with methods to simulate energy charging and discharging."""

    def __init__(self, parameters: Optional[BaseBatteryParameters] = None):
        self.parameters: Optional[BaseBatteryParameters] = None
        super().__init__(parameters)

    def _setup(self) -> None:
        """Sets up the battery parameters based on configuration or provided parameters."""
        if self.parameters is None:
            raise ValueError(f"Parameters not set: {self.parameters}")
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
        self.discharge_array = np.full(self.hours, 1)
        self.charge_array = np.full(self.hours, 1)
        self.soc_wh = (self.initial_soc_percentage / 100) * self.capacity_wh
        self.min_soc_wh = (self.min_soc_percentage / 100) * self.capacity_wh
        self.max_soc_wh = (self.max_soc_percentage / 100) * self.capacity_wh

    def to_dict(self) -> dict[str, Any]:
        """Converts the object to a dictionary representation."""
        return {
            "device_id": self.device_id,
            "capacity_wh": self.capacity_wh,
            "initial_soc_percentage": self.initial_soc_percentage,
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
        self.soc_wh = (self.initial_soc_percentage / 100) * self.capacity_wh
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
