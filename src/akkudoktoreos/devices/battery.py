from typing import Any, Optional

import numpy as np
from pydantic import BaseModel, Field, field_validator

from akkudoktoreos.devices.devicesabc import DeviceBase
from akkudoktoreos.utils.logutil import get_logger
from akkudoktoreos.utils.utils import NumpyEncoder

logger = get_logger(__name__)


def max_charging_power_field(default: Optional[float] = None) -> Optional[float]:
    """Field validator for maximum charging power in watts.
    Ensures that the value is greater than 0.
    """
    return Field(
        default=default, gt=0, description="The maximum charging power of the battery in watts."
    )


def initial_soc_percentage_field(description: str) -> int:
    """Field validator for the starting state of charge (SOC) in percentage.
    Ensures the value is between 0 and 100.
    """
    return Field(default=0, ge=0, le=100, description=description)


class BaseBatteryParameters(BaseModel):
    """Base class for battery parameters with fields for capacity, efficiency, and state of charge."""

    capacity_wh: int = Field(gt=0, description="Battery capacity in watt-hours (Wh).")
    charge_efficiency: float = Field(
        default=0.88, gt=0, le=1, description="Charging efficiency of the battery."
    )
    discharge_efficiency: float = Field(
        default=0.88, gt=0, le=1, description="Discharge efficiency of the battery."
    )
    max_charging_power_w: Optional[float] = max_charging_power_field()
    initial_soc_percentage: int = initial_soc_percentage_field(
        "Initial state of charge of the battery in percentage at the start of the simulation."
    )
    min_soc_percentage: int = Field(
        default=0,
        ge=0,
        le=100,
        description="Minimum state of charge (SOC) of the battery in percentage.",
    )
    max_soc_percentage: int = Field(
        default=100,
        ge=0,
        le=100,
        description="Maximum state of charge (SOC) of the battery in percentage.",
    )


class SolarPanelBatteryParameters(BaseBatteryParameters):
    """Parameters specific to a solar panel battery."""

    max_charging_power_w: Optional[float] = max_charging_power_field(5000)


class ElectricVehicleParameters(BaseBatteryParameters):
    """Parameters specific to an electric vehicle (EV)."""

    discharge_efficiency: float = 1.0
    initial_soc_percentage: int = initial_soc_percentage_field(
        "Current state of charge (SOC) of the EV's battery in percentage."
    )


class ElectricVehicleResult(BaseModel):
    """Result class containing information related to the electric vehicle's charging and discharging behavior."""

    charge_array: list[float] = Field(
        description="Hourly charging status (0 for no charging, 1 for charging)."
    )
    discharge_array: list[int] = Field(
        description="Hourly discharging status (0 for no discharging, 1 for discharging)."
    )
    discharge_efficiency: float = Field(description="The discharge efficiency.")
    hours: int = Field(description="Number of hours in the simulation.")
    capacity_wh: int = Field(description="Capacity of the EV’s battery in watt-hours.")
    charge_efficiency: float = Field(description="Charging efficiency.")
    max_charging_power_w: int = Field(description="Maximum charging power in watts.")
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
    """Represents a solar panel battery device with methods to simulate energy charging and discharging."""

    def __init__(
        self,
        parameters: Optional[BaseBatteryParameters] = None,
        hours: Optional[int] = 24,
        provider_id: Optional[str] = None,
    ):
        # Initialize configuration and parameters
        self.provider_id = provider_id
        self.prefix = "<invalid>"
        if self.provider_id == "GenericBattery":
            self.prefix = "battery"
        elif self.provider_id == "GenericBEV":
            self.prefix = "bev"

        self.parameters = parameters
        self.hours = hours if hours is not None else 24

        self.initialised = False
        if self.parameters is not None:
            self.setup()

    def setup(self) -> None:
        """Sets up the battery parameters based on configuration or provided parameters."""
        if self.initialised:
            return

        if self.provider_id:
            # Setup from configuration
            self.capacity_wh = getattr(self.config, f"{self.prefix}_capacity")
            self.initial_soc_percentage = getattr(self.config, f"{self.prefix}_soc_start")
            self.hours = self.total_hours
            self.charge_efficiency = getattr(self.config, f"{self.prefix}_charge_efficiency")
            self.discharge_efficiency = getattr(self.config, f"{self.prefix}_discharge_efficiency")
            self.max_charging_power_w = getattr(self.config, f"{self.prefix}_charge_power_max")

            if self.provider_id == "GenericBattery":
                self.min_soc_percentage = getattr(self.config, f"{self.prefix}_soc_min")
            else:
                self.min_soc_percentage = 0

            self.max_soc_percentage = getattr(self.config, f"{self.prefix}_soc_max")
        elif self.parameters:
            # Setup from parameters
            self.capacity_wh = self.parameters.capacity_wh
            self.initial_soc_percentage = self.parameters.initial_soc_percentage
            self.charge_efficiency = self.parameters.charge_efficiency
            self.discharge_efficiency = self.parameters.discharge_efficiency
            self.max_charging_power_w = self.parameters.max_charging_power_w
            self.min_soc_percentage = (
                self.parameters.min_soc_percentage
                if isinstance(self.parameters, SolarPanelBatteryParameters)
                else 0
            )
            self.max_soc_percentage = self.parameters.max_soc_percentage
        else:
            error_msg = "Parameters and provider ID are missing. Cannot instantiate."
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Initialize state of charge
        if self.max_charging_power_w is None:
            self.max_charging_power_w = self.capacity_wh
        self.discharge_array = np.full(self.hours, 1)
        self.charge_array = np.full(self.hours, 1)
        self.soc_wh = (self.initial_soc_percentage / 100) * self.capacity_wh
        self.min_soc_wh = (self.min_soc_percentage / 100) * self.capacity_wh
        self.max_soc_wh = (self.max_soc_percentage / 100) * self.capacity_wh

        self.initialised = True

    def to_dict(self) -> dict[str, Any]:
        """Converts the object to a dictionary representation."""
        return {
            "capacity_wh": self.capacity_wh,
            "initial_soc_percentage": self.initial_soc_percentage,
            "soc_wh": self.soc_wh,
            "hours": self.hours,
            "discharge_array": self.discharge_array,
            "charge_array": self.charge_array,
            "charge_efficiency": self.charge_efficiency,
            "discharge_efficiency": self.discharge_efficiency,
            "max_charging_power_w": self.max_charging_power_w,
        }

    def reset(self) -> None:
        """Resets the state of charge and arrays to initial values."""
        self.soc_wh = (self.initial_soc_percentage / 100) * self.capacity_wh
        self.soc_wh = min(max(self.soc_wh, self.min_soc_wh), self.max_soc_wh)

        self.discharge_array = np.full(self.hours, 1)
        self.charge_array = np.full(self.hours, 1)

    def set_discharge_per_hour(self, discharge_array: np.ndarray) -> None:
        """Sets the discharge values for each hour."""
        assert len(discharge_array) == self.hours
        self.discharge_array = np.array(discharge_array)

    def set_charge_per_hour(self, charge_array: np.ndarray) -> None:
        """Sets the charge values for each hour."""
        assert len(charge_array) == self.hours
        self.charge_array = np.array(charge_array)

    def set_charge_allowed_for_hour(self, charge: float, hour: int) -> None:
        """Sets the allowed charge for a specific hour."""
        assert hour < self.hours
        self.charge_array[hour] = charge

    def current_soc_percentage(self) -> float:
        """Returns the current state of charge in percentage."""
        return (self.soc_wh / self.capacity_wh) * 100

    def discharge_energy(self, wh: float, hour: int) -> tuple[float, float]:
        """Discharges energy from the battery, considering efficiency and state of charge limits."""
        if self.discharge_array[hour] == 0:
            return 0.0, 0.0  # No energy discharge

        # Calculate maximum energy that can be discharged
        max_possible_discharge_wh = (self.soc_wh - self.min_soc_wh) * self.discharge_efficiency
        max_possible_discharge_wh = max(max_possible_discharge_wh, 0.0)

        # Consider battery's maximum discharge power
        max_possible_discharge_wh = min(max_possible_discharge_wh, self.max_charging_power_w)

        # Actual energy to discharge
        actual_discharge_wh = min(wh, max_possible_discharge_wh)

        # Account for efficiency loss
        if self.discharge_efficiency > 0:
            actual_withdrawal_wh = actual_discharge_wh / self.discharge_efficiency
        else:
            actual_withdrawal_wh = 0.0

        # Update the state of charge
        self.soc_wh -= actual_withdrawal_wh
        self.soc_wh = max(self.soc_wh, self.min_soc_wh)

        # Calculate losses due to efficiency
        losses_wh = actual_withdrawal_wh - actual_discharge_wh

        return actual_discharge_wh, losses_wh

    def charge_energy(
        self, wh: Optional[float], hour: int, relative_power: float = 0.0
    ) -> tuple[float, float]:
        """Charges the battery considering efficiency and state of charge limits."""
        if self.charge_array[hour] == 0:
            return 0.0, 0.0  # Charging not allowed in this hour

        if relative_power > 0.0:
            wh = self.max_charging_power_w * relative_power

        # If no specific charging value is given, use the maximum charging power
        wh = wh if wh is not None else self.max_charging_power_w

        # Calculate the maximum chargeable energy
        if self.charge_efficiency > 0:
            max_possible_charge_wh = (self.max_soc_wh - self.soc_wh) / self.charge_efficiency
        else:
            max_possible_charge_wh = 0.0
        max_possible_charge_wh = max(max_possible_charge_wh, 0.0)

        # Actual energy to charge
        effective_charge_wh = min(wh, max_possible_charge_wh)

        # Energy stored in the battery
        charged_energy = effective_charge_wh * self.charge_efficiency

        # Update state of charge
        self.soc_wh += charged_energy
        self.soc_wh = min(self.soc_wh, self.max_soc_wh)

        # Calculate losses due to charging efficiency
        losses_wh = effective_charge_wh - charged_energy

        return charged_energy, losses_wh

    def current_energy_content(self) -> float:
        """Returns the current usable energy in the battery, considering discharge efficiency."""
        usable_energy = (self.soc_wh - self.min_soc_wh) * self.discharge_efficiency
        return max(usable_energy, 0.0)
