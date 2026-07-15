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
        self.load_power_min_w = float(self.interpolator.grid[0][0])
        self.load_power_max_w = float(self.interpolator.grid[0][-1])
        self.minute_load_levels_w = np.asarray(self.interpolator.grid[1], dtype=float)
        self.minute_load_max_w = float(self.interpolator.grid[1][-1])

    def _load_distribution(self, mean_load_power_w: float) -> tuple[np.ndarray, np.ndarray]:
        """Return the conditional minute-load distribution for a mean load.

        The table stores one probability mass for each 50 W minute-load bin.
        Linear interpolation between its mean-load rows can introduce very small
        numerical deviations, so negative masses are removed and the result is
        normalized explicitly.
        """
        bounded_mean_load_w = float(
            np.clip(mean_load_power_w, self.load_power_min_w, self.load_power_max_w)
        )
        points = np.column_stack(
            (
                np.full(self.minute_load_levels_w.shape, bounded_mean_load_w),
                self.minute_load_levels_w,
            )
        )
        probabilities = np.maximum(np.asarray(self.interpolator(points), dtype=float), 0.0)
        probability_sum = float(probabilities.sum())
        if probability_sum <= 0.0:
            return self.minute_load_levels_w, probabilities
        return self.minute_load_levels_w, probabilities / probability_sum

    def _generate_points(
        self, mean_load_power_w: float, pv_power_w: float
    ) -> tuple[np.ndarray, np.ndarray]:
        """Generate in-bounds grid points for interpolation.

        The bundled probability table was calibrated from a one-hour mean load
        and one-minute samples. Sub-hourly optimization still passes *power* in
        watts here; a native 15-minute mean is therefore a documented
        approximation until a separately calibrated table is available.
        """
        bounded_mean_load_w = float(
            np.clip(mean_load_power_w, self.load_power_min_w, self.load_power_max_w)
        )
        bounded_pv_power_w = float(np.clip(pv_power_w, 0.0, self.minute_load_max_w))
        partial_loads = np.arange(0.0, bounded_pv_power_w + 1.0, 50.0)
        points = np.column_stack((np.full(partial_loads.shape, bounded_mean_load_w), partial_loads))
        return points, partial_loads

    @cache_energy_management
    def calculate_self_consumption(self, mean_load_power_w: float, pv_power_w: float) -> float:
        """Return the legacy cumulative minute-load probability.

        This method is retained for API compatibility. Its result is the
        probability that the minute load is no greater than ``pv_power_w``;
        it is not an energy self-consumption ratio. New energy-flow code must
        use :meth:`calculate_expected_direct_consumption`.

        The results are cached until the start of the next energy management run/ optimization.

        Args:
         - mean_load_power_w: Mean load power for the current forecast interval (W).
         - pv_power_w: Current PV power output (W).

        Returns:
         - Self-consumption rate as a float.
        """
        points, _ = self._generate_points(mean_load_power_w, pv_power_w)
        probabilities = self.interpolator(points)
        return float(np.clip(probabilities.sum(), 0.0, 1.0))

    @cache_energy_management
    def calculate_expected_direct_consumption(
        self, mean_load_power_w: float, pv_power_w: float
    ) -> float:
        """Calculate expected direct PV-to-load power in watts.

        For conditional minute-load probabilities ``p_i`` and load-bin powers
        ``L_i``, the expected direct consumption is

        ``sum(p_i * min(L_i, pv_power_w))``.

        The tabulated load-bin powers are rescaled to preserve the supplied
        forecast mean exactly. This compensates for discretization and the
        finite upper table boundary while retaining the distribution shape.

        Args:
            mean_load_power_w: Mean load power of the forecast interval [W].
            pv_power_w: Mean PV power of the forecast interval [W].

        Returns:
            Expected direct PV-to-load power [W].
        """
        mean_load_power_w = max(float(mean_load_power_w), 0.0)
        pv_power_w = max(float(pv_power_w), 0.0)
        if mean_load_power_w == 0.0 or pv_power_w == 0.0:
            return 0.0

        load_levels_w, probabilities = self._load_distribution(mean_load_power_w)
        modeled_mean_load_w = float(np.dot(probabilities, load_levels_w))
        if modeled_mean_load_w <= 0.0:
            return 0.0

        # Preserve the requested mean load while keeping the conditional shape
        # from the probability table.
        normalized_load_levels_w = load_levels_w * (mean_load_power_w / modeled_mean_load_w)
        expected_direct_power_w = float(
            np.dot(probabilities, np.minimum(normalized_load_levels_w, pv_power_w))
        )
        return float(np.clip(expected_direct_power_w, 0.0, min(mean_load_power_w, pv_power_w)))

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
