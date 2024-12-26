#!/usr/bin/env python
import pickle
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy.interpolate import RegularGridInterpolator


class SelfConsumptionPropabilityInterpolator:
    def __init__(self, filepath: str | Path):
        self.filepath = filepath
        # self.interpolator = None
        # Load the RegularGridInterpolator
        with open(self.filepath, "rb") as file:
            self.interpolator: RegularGridInterpolator = pickle.load(file)

    @lru_cache(maxsize=128)
    def generate_points(
        self, load_1h_power: float, pv_power: float
    ) -> tuple[np.ndarray, np.ndarray]:
        """Generate the grid points for interpolation."""
        partial_loads = np.arange(0, pv_power + 50, 50)
        points = np.array([np.full_like(partial_loads, load_1h_power), partial_loads]).T
        return points, partial_loads

    def calculate_self_consumption(self, load_1h_power: float, pv_power: float) -> float:
        points, partial_loads = self.generate_points(load_1h_power, pv_power)
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


# Test the function
# print(calculate_self_consumption(1000, 1200))
