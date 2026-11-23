#!/usr/bin/env python
"""Self-consumption probability of the GENETIC0 inverter simulation.

The backend comes from the run settings (``optimization.self_consumption_interpolator``, read by
the parameter preparation), never from the configuration directly (ADR 0003).
"""

import functools
import threading
from pathlib import Path

import numpy as np

from akkudoktoreos.utils.gridinterpolator import (
    GridInterpolatorBackend,
    SwitchableGridInterpolator,
    load_grid_interpolator,
)

# Distinct (load, PV) pairs remembered per interpolator; the values never change.
_CACHE_SIZE = 1 << 16


class SelfConsumptionProbabilityInterpolator:
    def __init__(
        self,
        filepath: str | Path,
        backend: GridInterpolatorBackend = GridInterpolatorBackend.SCIPY,
    ) -> None:
        """Load the self-consumption table.

        Args:
            filepath: Pickled table.
            backend: How the table is evaluated.
        """
        self.filepath = filepath
        self.backend = backend
        # The table is a pickled SciPy RegularGridInterpolator. It is read
        # without SciPy; the backend decides whether SciPy (default) or NumPy
        # evaluates it.
        self.interpolator = SwitchableGridInterpolator(
            load_grid_interpolator(self.filepath), lambda: self.backend
        )
        # The table is static, so results can be kept across runs.
        self.calculate_self_consumption = functools.lru_cache(maxsize=_CACHE_SIZE)(
            self._calculate_self_consumption
        )

    def _generate_points(
        self, load_1h_power: float, pv_power: float
    ) -> tuple[np.ndarray, np.ndarray]:
        """Generate the grid points for interpolation."""
        partial_loads = np.arange(0, pv_power + 50, 50)
        points = np.array([np.full_like(partial_loads, load_1h_power), partial_loads]).T
        return points, partial_loads

    def _calculate_self_consumption(self, load_1h_power: float, pv_power: float) -> float:
        """Calculate the PV self-consumption rate using RegularGridInterpolator.

        Called through ``calculate_self_consumption``, which caches the results.

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


class Genetic0LoadInterpolator(SelfConsumptionProbabilityInterpolator):
    """The self-consumption table shipped with EOS."""

    def __init__(self, backend: GridInterpolatorBackend = GridInterpolatorBackend.SCIPY) -> None:
        """Load the shipped table.

        Args:
            backend: How the table is evaluated.
        """
        filename = (
            Path(__file__).parent.parent.parent.resolve()
            / "data"
            / "genetic0_load_interpolator.pkl"
        )
        super().__init__(filename, backend)


# One interpolator per backend, created on first use.
_genetic0_load_interpolators: dict[GridInterpolatorBackend, Genetic0LoadInterpolator] = {}
_genetic0_load_interpolator_lock = threading.Lock()


def get_genetic0_load_interpolator(
    backend: GridInterpolatorBackend = GridInterpolatorBackend.SCIPY,
) -> Genetic0LoadInterpolator:
    """Return the shared interpolator for ``backend``.

    Args:
        backend: How the table is evaluated.

    Returns:
        The interpolator, loaded on first use.
    """
    interpolator = _genetic0_load_interpolators.get(backend)
    if interpolator is None:
        with _genetic0_load_interpolator_lock:
            interpolator = _genetic0_load_interpolators.get(backend)
            if interpolator is None:
                interpolator = Genetic0LoadInterpolator(backend)
                _genetic0_load_interpolators[backend] = interpolator
    return interpolator
