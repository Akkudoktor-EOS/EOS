#!/usr/bin/env python
import numpy as np
import pickle

# from scipy.interpolate import RegularGridInterpolator
from pathlib import Path


class self_consumption_probability_interpolator:
    def __init__(self, filepath: str | Path):
        self.filepath = filepath
        self.interpolator = None
        # Load the RegularGridInterpolator
        with open("regular_grid_interpolator.pkl", "rb") as file:
            interpolator = pickle.load(self.filepath)

    def calculate_self_consumption(self, load_1h_power: float, pv_power: float) -> float:
        """Calculate the PV self-consumption rate using RegularGridInterpolator.

        Args:
        - last_1h_power: 1h power levels (W).
        - pv_power: Current PV power output (W).

        Returns:
        - Self-consumption rate as a float.
        """
        # Generate the range of partial loads (0 to last_1h_power)
        partial_loads = np.arange(0, 3500, 50)

        # Get probabilities for all partial loads
        points = np.array([np.full_like(partial_loads, load_1h_power), partial_loads]).T
        probabilities = interpolator(points)
        probabilities = probabilities / probabilities.sum()
        for i, w in enumerate(partial_loads):
            print(w, ": ", probabilities[i])
        print(probabilities.sum())
        # Ensure probabilities are within [0, 1]
        probabilities = np.clip(probabilities, 0, 1)

        # Mask: Only include probabilities where the load is <= PV power
        mask = partial_loads <= pv_power

        # Calculate the cumulative probability for covered loads
        self_consumption_rate = np.sum(probabilities[mask]) / np.sum(probabilities)

        return self_consumption_rate


# Test the function
# print(calculate_self_consumption(1000, 1200))
