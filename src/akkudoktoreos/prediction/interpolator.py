#!/usr/bin/env python
import pickle
from pathlib import Path

import numpy as np
from scipy.interpolate import RegularGridInterpolator

from akkudoktoreos.core.cache import cache_energy_management
from akkudoktoreos.core.coreabc import SingletonMixin


class SelfConsumptionProbabilityInterpolator:
    def __init__(self, filepath: str | Path):
        self.filepath = filepath
        # Load the RegularGridInterpolator
        with open(self.filepath, "rb") as file:
            self.interpolator: RegularGridInterpolator = pickle.load(file)  # noqa: S301

    def _generate_points(
        self, load_1h_power: float, pv_power: float
    ) -> tuple[np.ndarray, np.ndarray]:
        """Generate the grid points for interpolation."""
        partial_loads = np.arange(0, pv_power + 50, 50)
        points = np.array([np.full_like(partial_loads, load_1h_power), partial_loads]).T
        return points, partial_loads

    @cache_energy_management
    def calculate_self_consumption(self, load_1h_power: float, pv_power: float) -> float:
        """Calculate the PV self-consumption rate using RegularGridInterpolator.

        The results are cached until the start of the next energy management run/ optimization.

        Args:
         - last_1h_power: 1h power levels (W).
         - pv_power: Current PV power output (W).

        Returns:
         - Self-consumption rate as a float.
        """
        points, partial_loads = self._generate_points(load_1h_power, pv_power)
        probabilities = self.interpolator(points)
        return probabilities.sum()

    # def calculate_self_consumption(self, load_1h_power: float, pv_power: float) -> float:
    #     """Calculate the PV self-consumption rate using RegularGridInterpolator.

    #     Args:
    #     - last_1h_power: 1h power levels (W).
    #     - pv_power: Current PV power output (W).

    #     Returns:
    #     - Self-consumption rate as a float.
    #     """
    #     # Generate the range of partial loads (0 to last_1h_power)
    #     partial_loads = np.arange(0, pv_power + 50, 50)

    #     # Get probabilities for all partial loads
    #     points = np.array([np.full_like(partial_loads, load_1h_power), partial_loads]).T
    #     if self.interpolator == None:
    #         return -1.0
    #     probabilities = self.interpolator(points)
    #     self_consumption_rate = probabilities.sum()

    #     # probabilities = probabilities / (np.sum(probabilities))  # / (pv_power / 3450))
    #     # # for i, w in enumerate(partial_loads):
    #     # #    print(w, ": ", probabilities[i])
    #     # print(probabilities.sum())

    #     # # Ensure probabilities are within [0, 1]
    #     # probabilities = np.clip(probabilities, 0, 1)

    #     # # Mask: Only include probabilities where the load is <= PV power
    #     # mask = partial_loads <= pv_power

    #     # # Calculate the cumulative probability for covered loads
    #     # self_consumption_rate = np.sum(probabilities[mask]) / np.sum(probabilities)
    #     # print(self_consumption_rate)
    #     # sys.exit()

    #     return self_consumption_rate


class EOSLoadInterpolator(SelfConsumptionProbabilityInterpolator, SingletonMixin):
    def __init__(self) -> None:
        if hasattr(self, "_initialized"):
            return
        filename = Path(__file__).parent.resolve() / ".." / "data" / "regular_grid_interpolator.pkl"
        super().__init__(filename)


# Initialize the Energy Management System, it is a singleton.
eos_load_interpolator = EOSLoadInterpolator()


def get_eos_load_interpolator() -> EOSLoadInterpolator:
    return eos_load_interpolator
