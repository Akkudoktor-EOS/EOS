"""Expected direct PV-to-load consumption within a slot, compiled.

The EOS load table holds, for a mean load, the probability of each one-minute load level.
The direct consumption of a slot is the expectation of ``min(load, pv)`` over that
distribution, with the load levels rescaled to keep the forecast mean exactly. This module
evaluates the same bilinear table lookup as ``EOSLoadInterpolator`` with numba.
"""

import numpy as np

from akkudoktoreos.devices.models.jit import njit


def load_table() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return the load table as ``(mean_load_axis, minute_load_levels, probabilities)``."""
    from akkudoktoreos.prediction.interpolator import get_eos_load_interpolator

    grid = get_eos_load_interpolator().interpolator
    return (
        np.ascontiguousarray(grid.grid[0], dtype=float),
        np.ascontiguousarray(grid.grid[1], dtype=float),
        np.ascontiguousarray(grid.values, dtype=float),
    )


@njit
def expected_direct_power_w(
    mean_load_w: float,
    pv_power_w: float,
    axis: np.ndarray,
    levels: np.ndarray,
    values: np.ndarray,
) -> float:
    """Return the expected direct PV-to-load power [W] of a slot.

    Args:
        mean_load_w: Mean load power of the slot [W].
        pv_power_w: Mean PV power of the slot [W].
        axis: Mean load axis of the table.
        levels: Minute load levels of the table [W].
        values: Probability table ``(len(axis), len(levels))``.
    """
    mean_load_w = max(mean_load_w, 0.0)
    pv_power_w = max(pv_power_w, 0.0)
    if mean_load_w == 0.0 or pv_power_w == 0.0:
        return 0.0
    count = axis.shape[0]
    bounded = min(max(mean_load_w, axis[0]), axis[count - 1])
    # Interval with axis[i] <= x < axis[i + 1]; the last interval also takes the end point.
    row = 0
    for position in range(count):
        if axis[position] <= bounded:
            row = position
    if row > count - 2:
        row = count - 2
    row_distance = (bounded - axis[row]) / (axis[row + 1] - axis[row])
    weight_low = 1 - row_distance
    # The levels are grid points of the second axis, so the bilinear lookup reduces to a
    # linear blend of two table rows. Probabilities are normalized by their sum below.
    width = levels.shape[0]
    probability_sum = 0.0
    weighted_level_sum = 0.0
    for level in range(width):
        value = values[row, level] * weight_low + values[row + 1, level] * row_distance
        if value < 0.0:
            value = 0.0
        probability_sum += value
        weighted_level_sum += value * levels[level]
    if probability_sum <= 0.0:
        return 0.0
    modeled_mean_w = weighted_level_sum / probability_sum
    if modeled_mean_w <= 0.0:
        return 0.0
    # Rescale the load levels to keep the forecast mean exactly.
    level_scale = mean_load_w / modeled_mean_w
    expected_w = 0.0
    for level in range(width):
        value = values[row, level] * weight_low + values[row + 1, level] * row_distance
        if value < 0.0:
            value = 0.0
        expected_w += value * min(levels[level] * level_scale, pv_power_w)
    expected_w = expected_w / probability_sum
    return min(max(expected_w, 0.0), min(mean_load_w, pv_power_w))
