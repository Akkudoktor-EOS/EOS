from typing import Any, Iterator, Optional

import numpy as np

from akkudoktoreos.devices.devices import BATTERY_DEFAULT_CHARGE_RATES
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
        """Sets up the battery parameters based on provided parameters."""
        self.capacity_wh = self.parameters.capacity_wh
        self.initial_soc_percentage = self.parameters.initial_soc_percentage
        self.charging_efficiency = self.parameters.charging_efficiency
        self.discharging_efficiency = self.parameters.discharging_efficiency

        # Charge rates, in case of None use default
        self.charge_rates = BATTERY_DEFAULT_CHARGE_RATES
        if self.parameters.charge_rates:
            charge_rates = np.array(self.parameters.charge_rates, dtype=float)
            charge_rates = np.unique(charge_rates)
            charge_rates.sort()
            self.charge_rates = charge_rates

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
        self.discharge_array = np.full(self.prediction_hours, 0)
        self.charge_array = np.full(self.prediction_hours, 0)
        self.soc_wh = (self.initial_soc_percentage / 100) * self.capacity_wh
        self.min_soc_wh = (self.min_soc_percentage / 100) * self.capacity_wh
        self.max_soc_wh = (self.max_soc_percentage / 100) * self.capacity_wh

    def _lower_charge_rates_desc(self, start_rate: float) -> Iterator[float]:
        """Yield all charge rates lower than a given rate in descending order.

        Args:
            charge_rates (np.ndarray): Sorted 1D array of available charge rates.
            start_rate (float): The reference charge rate.

        Yields:
            float: Charge rates lower than `start_rate`, in descending order.
        """
        charge_rates_fast = self.charge_rates

        # Find the insertion index for start_rate (left-most position)
        idx = np.searchsorted(charge_rates_fast, start_rate, side="left")

        # Yield values before idx in reverse (descending)
        return (charge_rates_fast[j] for j in range(idx - 1, -1, -1))

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
        self.discharge_array = np.full(self.prediction_hours, 0)
        self.charge_array = np.full(self.prediction_hours, 0)

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

    def current_soc_percentage(self) -> float:
        """Calculates the current state of charge in percentage."""
        return (self.soc_wh / self.capacity_wh) * 100

    def discharge_energy(self, wh: float, hour: int) -> tuple[float, float]:
        """Discharge energy from the battery.

        Discharge is limited by:
        * Requested delivered energy
        * Remaining energy above minimum SoC
        * Maximum discharge power
        * Discharge efficiency

        Args:
            wh (float): Requested delivered energy in watt-hours.
            hour (int): Time index. If `self.discharge_array[hour] == 0`,
                no discharge occurs.

        Returns:
            tuple[float, float]:
                delivered_wh (float): Actual delivered energy [Wh].
                losses_wh (float): Conversion losses [Wh].

        """
        if self.discharge_array[hour] == 0:
            return 0.0, 0.0

        # Raw extractable energy above minimum SoC
        raw_available_wh = max(self.soc_wh - self.min_soc_wh, 0.0)

        # Maximum raw discharge due to power limit
        max_raw_wh = self.max_charge_power_w  # TODO rename to max_discharge_power_w

        # Actual raw withdrawal (internal)
        raw_withdrawal_wh = min(raw_available_wh, max_raw_wh)

        # Convert raw to delivered
        max_deliverable_wh = raw_withdrawal_wh * self.discharging_efficiency

        # Cap by requested delivered energy
        delivered_wh = min(wh, max_deliverable_wh)

        # Effective raw withdrawal based on what is delivered
        raw_used_wh = delivered_wh / self.discharging_efficiency

        # Update SoC
        self.soc_wh -= raw_used_wh
        self.soc_wh = max(self.soc_wh, self.min_soc_wh)

        # Losses
        losses_wh = raw_used_wh - delivered_wh

        return delivered_wh, losses_wh

    def charge_energy(
        self,
        wh: Optional[float],
        hour: int,
        charge_factor: float = 0.0,
    ) -> tuple[float, float]:
        """Charge energy into the battery.

        Two **exclusive** modes:

        **Mode 1:**

        - `wh is not None` and `charge_factor == 0`
        - The raw requested charge energy is `wh` (pre-efficiency).
        - If remaining capacity is insufficient, charging is automatically limited.
        - No exception is raised due to capacity limits.

        **Mode 2:**

        - `wh is None` and `charge_factor > 0`
        - The raw requested energy is `max_charge_power_w * charge_factor`.
        - If the request exceeds remaining capacity, the algorithm tries to find a lower
          `charge_factor` that is compatible. If such a charge factor exists, this hour’s
          `charge_factor` is replaced.
        - If no charge factor can accommodate charging, the request is ignored (``(0.0, 0.0)`` is
          returned) and a penalty is applied elsewhere.

        Charging is constrained by:

        - Available SoC headroom (``max_soc_wh − soc_wh``)
        - ``max_charge_power_w``
        - ``charging_efficiency``

        Args:
            wh (float | None):
                Requested raw energy [Wh] before efficiency.
                Must be provided only for Mode 1 (charge_factor must be 0).

            hour (int):
                Time index. If charging is disabled at this hour (charge_array[hour] == 0),
                returns `(0.0, 0.0)`.

            charge_factor (float):
                Fraction (0–1) of max charge power.
                Must be >0 only in Mode 2 (`wh is None`).

        Returns:
            tuple[float, float]:
                stored_wh : float
                    Energy stored after efficiency [Wh].
                losses_wh : float
                    Conversion losses [Wh].

        Raises:
            ValueError:
                - If the mode is ambiguous (neither Mode 1 nor Mode 2).
                - If the final new SoC would exceed capacity_wh.

        Notes:
            stored_wh = raw_input_wh * charging_efficiency
            losses_wh = raw_input_wh − stored_wh
        """
        # Charging allowed in this hour?
        if hour is not None and self.charge_array[hour] == 0:
            return 0.0, 0.0

        # Provide fast (3x..5x) local read access (vs. self.xxx) for repetitive read access
        soc_wh_fast = self.soc_wh
        max_charge_power_w_fast = self.max_charge_power_w
        charging_efficiency_fast = self.charging_efficiency

        # Decide mode & determine raw_request_wh and raw_charge_wh
        if wh is not None and charge_factor == 0.0:  # mode 1
            raw_request_wh = wh
            raw_charge_wh = max(self.max_soc_wh - soc_wh_fast, 0.0) / charging_efficiency_fast
        elif wh is None and charge_factor > 0.0:  # mode 2
            raw_request_wh = max_charge_power_w_fast * charge_factor
            raw_charge_wh = max(self.max_soc_wh - soc_wh_fast, 0.0) / charging_efficiency_fast
            if raw_request_wh > raw_charge_wh:
                # Use a lower charge factor
                lower_charge_factors = self._lower_charge_rates_desc(charge_factor)
                for charge_factor in lower_charge_factors:
                    raw_request_wh = max_charge_power_w_fast * charge_factor
                    if raw_request_wh <= raw_charge_wh:
                        self.charge_array[hour] = charge_factor
                        break
                if raw_request_wh > raw_charge_wh:
                    # ignore request - penalty for missing SoC will be applied
                    self.charge_array[hour] = 0
                    return 0.0, 0.0
        else:
            raise ValueError(
                f"{self.parameters.device_id}: charge_energy must be called either "
                "with wh != None and charge_factor == 0, or with wh == None and charge_factor > 0."
            )

        # Remaining capacity
        max_raw_wh = min(raw_charge_wh, max_charge_power_w_fast)

        # Actual raw intake
        raw_input_wh = raw_request_wh if raw_request_wh < max_raw_wh else max_raw_wh

        # Apply efficiency
        stored_wh = raw_input_wh * charging_efficiency_fast
        new_soc = soc_wh_fast + stored_wh

        if new_soc > self.capacity_wh:
            raise ValueError(
                f"{self.parameters.device_id}: SoC {new_soc} Wh exceeds capacity {self.capacity_wh} Wh"
            )

        self.soc_wh = new_soc
        losses_wh = raw_input_wh - stored_wh

        return stored_wh, losses_wh

    def current_energy_content(self) -> float:
        """Returns the current usable energy in the battery."""
        usable_energy = (self.soc_wh - self.min_soc_wh) * self.discharging_efficiency
        return max(usable_energy, 0.0)
