"""Expected direct PV-to-load power of the GENETIC inverter simulation.

GENETIC's own copy of the self-consumption probability table evaluation (ADR 0004). The backend
comes from the run settings (``optimization.self_consumption_interpolator``, read by the
parameter preparation), never from the configuration directly (ADR 0003).
"""

import threading
from pathlib import Path

import numpy as np

from akkudoktoreos.utils.gridinterpolator import (
    GridInterpolatorBackend,
    SwitchableGridInterpolator,
    load_grid_interpolator,
)

# The table shipped with EOS: minute-load probabilities per one-hour mean load.
_TABLE_FILE = (
    Path(__file__).parent.parent.parent.resolve() / "data" / "regular_grid_interpolator.pkl"
)


def fixed_order_dot(a: np.ndarray, b: np.ndarray) -> float:
    """Return the dot product of two vectors, summed from the first element to the last.

    ``np.dot`` calls BLAS, whose kernel is chosen per CPU at run time, so the last bit of the
    result depends on the machine. The optimizer compares fitness values exactly; here every
    product is rounded and added in index order, with Python floats: the same result on every
    CPU.

    Args:
        a: First vector.
        b: Second vector, same length as ``a``.

    Returns:
        ``sum(a[i] * b[i])`` in index order.
    """
    total = 0.0
    for x, y in zip(a.tolist(), b.tolist(), strict=True):
        total += x * y
    return total


class GeneticLoadInterpolator:
    """Expected direct consumption from the self-consumption probability table."""

    LOAD_DISTRIBUTION_CACHE_SIZE = 4096
    DIRECT_CONSUMPTION_CACHE_SIZE = 8192

    def __init__(self, backend: GridInterpolatorBackend) -> None:
        """Load the table.

        Args:
            backend: How the table is evaluated.
        """
        self.interpolator = SwitchableGridInterpolator(
            load_grid_interpolator(_TABLE_FILE), lambda: backend
        )
        self.load_power_min_w = float(self.interpolator.grid[0][0])
        self.load_power_max_w = float(self.interpolator.grid[0][-1])
        self.minute_load_levels_w = np.asarray(self.interpolator.grid[1], dtype=float)
        # The table is static and the backend fixed, so a cached value never goes stale; the
        # caches are only bounded in size.
        self._load_distributions: dict[float, tuple[np.ndarray, np.ndarray]] = {}
        self._direct_consumption: dict[tuple[float, float], float] = {}

    def _load_distribution(self, mean_load_power_w: float) -> tuple[np.ndarray, np.ndarray]:
        """Return the conditional minute-load distribution for a mean load.

        Negative masses from the linear interpolation between mean-load rows are removed and
        the result is normalized. Callers must not modify the returned arrays.
        """
        bounded_mean_load_w = min(
            max(float(mean_load_power_w), self.load_power_min_w), self.load_power_max_w
        )
        cached = self._load_distributions.get(bounded_mean_load_w)
        if cached is not None:
            return cached
        points = np.column_stack(
            (
                np.full(self.minute_load_levels_w.shape, bounded_mean_load_w),
                self.minute_load_levels_w,
            )
        )
        probabilities = np.maximum(np.asarray(self.interpolator(points), dtype=float), 0.0)
        probability_sum = float(probabilities.sum())
        if probability_sum <= 0.0:
            distribution = (self.minute_load_levels_w, probabilities)
        else:
            distribution = (self.minute_load_levels_w, probabilities / probability_sum)
        if bounded_mean_load_w == bounded_mean_load_w:  # not NaN: NaN never matches a key
            if len(self._load_distributions) >= self.LOAD_DISTRIBUTION_CACHE_SIZE:
                self._load_distributions.clear()
            self._load_distributions[bounded_mean_load_w] = distribution
        return distribution

    def calculate_expected_direct_consumption(
        self, mean_load_power_w: float, pv_power_w: float
    ) -> float:
        """Calculate expected direct PV-to-load power in watts.

        For conditional minute-load probabilities ``p_i`` and load-bin powers ``L_i``, the
        expected direct consumption is ``sum(p_i * min(L_i, pv_power_w))``. The load-bin powers
        are rescaled to preserve the supplied forecast mean exactly.

        Args:
            mean_load_power_w: Mean load power of the forecast interval [W].
            pv_power_w: Mean PV power of the forecast interval [W].

        Returns:
            Expected direct PV-to-load power [W].
        """
        cache = self._direct_consumption
        key = (mean_load_power_w, pv_power_w)
        cached = cache.get(key)
        if cached is not None:
            return cached
        result = self._expected_direct_consumption(mean_load_power_w, pv_power_w)
        if len(cache) >= self.DIRECT_CONSUMPTION_CACHE_SIZE:
            cache.clear()
        cache[key] = result
        return result

    def _expected_direct_consumption(self, mean_load_power_w: float, pv_power_w: float) -> float:
        """Calculate the expected direct PV-to-load power in watts, uncached."""
        mean_load_power_w = max(float(mean_load_power_w), 0.0)
        pv_power_w = max(float(pv_power_w), 0.0)
        if mean_load_power_w == 0.0 or pv_power_w == 0.0:
            return 0.0

        load_levels_w, probabilities = self._load_distribution(mean_load_power_w)
        modeled_mean_load_w = fixed_order_dot(probabilities, load_levels_w)
        if modeled_mean_load_w <= 0.0:
            return 0.0

        normalized_load_levels_w = load_levels_w * (mean_load_power_w / modeled_mean_load_w)
        expected_direct_power_w = fixed_order_dot(
            probabilities, np.minimum(normalized_load_levels_w, pv_power_w)
        )
        return min(max(expected_direct_power_w, 0.0), min(mean_load_power_w, pv_power_w))


# One interpolator per backend, created on first use.
_interpolators: dict[GridInterpolatorBackend, GeneticLoadInterpolator] = {}
_interpolators_lock = threading.Lock()


def get_genetic_load_interpolator(backend: GridInterpolatorBackend) -> GeneticLoadInterpolator:
    """Return the shared interpolator for ``backend``.

    Args:
        backend: How the table is evaluated.

    Returns:
        The interpolator, loaded on first use.
    """
    interpolator = _interpolators.get(backend)
    if interpolator is None:
        with _interpolators_lock:
            interpolator = _interpolators.get(backend)
            if interpolator is None:
                interpolator = GeneticLoadInterpolator(backend)
                _interpolators[backend] = interpolator
    return interpolator
