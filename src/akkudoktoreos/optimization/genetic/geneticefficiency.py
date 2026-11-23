"""Load-dependent conversion efficiency of the GENETIC simulation devices.

GENETIC's own copy of the efficiency curve helpers (ADR 0004: optimizers duplicate code instead
of sharing it), so its simulation does not import the shared device configuration.
"""

import math
from collections.abc import Sequence
from typing import Optional


def validate_efficiency_curve(
    curve: Optional[list[tuple[float, float]]],
) -> Optional[list[tuple[float, float]]]:
    """Validate a load-dependent conversion efficiency curve.

    A curve is a list of ``(load_fraction, efficiency)`` points. The load
    fraction is the converted power relative to the rated power of the device
    (0.0 = idle, 1.0 = rated power), the efficiency is the conversion
    efficiency at that load.

    Rules:
        - At least two points.
        - Load fractions are finite, within [0, 1] and strictly increasing
          (sorted, no duplicates).
        - Efficiencies are finite and within (0, 1].

    Args:
        curve: The curve points, or None for no curve.

    Returns:
        The unchanged curve, or None.

    Raises:
        ValueError: If the curve violates one of the rules.
    """
    if curve is None:
        return None
    if len(curve) < 2:
        raise ValueError("An efficiency curve needs at least two points.")
    previous_fraction: Optional[float] = None
    for fraction, efficiency in curve:
        if not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0:
            raise ValueError(f"Efficiency curve load fraction {fraction} is outside of [0, 1].")
        if not math.isfinite(efficiency) or not 0.0 < efficiency <= 1.0:
            raise ValueError(f"Efficiency curve efficiency {efficiency} is outside of (0, 1].")
        if previous_fraction is not None and fraction <= previous_fraction:
            raise ValueError(
                "Efficiency curve load fractions must be strictly increasing "
                f"(sorted, no duplicates); got {fraction} after {previous_fraction}."
            )
        previous_fraction = fraction
    return curve


def interpolate_efficiency_curve(
    curve: Sequence[tuple[float, float]], load_fraction: float
) -> float:
    """Return the efficiency of a curve at a load fraction.

    Interpolates linearly between the curve points. Load fractions below the
    first or above the last point are clamped to the efficiency of that point.

    Args:
        curve: Points as validated by ``validate_efficiency_curve``.
        load_fraction: Converted power relative to the rated power.

    Returns:
        The efficiency at ``load_fraction``.
    """
    first_fraction, first_efficiency = curve[0]
    if load_fraction <= first_fraction:
        return first_efficiency
    for fraction, efficiency in curve[1:]:
        if load_fraction <= fraction:
            return first_efficiency + (load_fraction - first_fraction) / (
                fraction - first_fraction
            ) * (efficiency - first_efficiency)
        first_fraction, first_efficiency = fraction, efficiency
    return first_efficiency


def solve_ac_for_dc_energy(
    curve: Sequence[tuple[float, float]],
    dc_wh: float,
    rated_wh: float,
    upper_ac_wh: float,
) -> float:
    """Return the AC energy a DC energy yields through a load-dependent conversion.

    The efficiency of a curve depends on the AC load actually delivered, so for a
    given DC energy the delivered AC energy ``a`` is the solution of
    ``a = dc_wh * efficiency(a / rated_wh)``. The curve is piecewise linear and
    clamped at both ends, so the equation is solved exactly on each piece.

    Args:
        curve: Points as validated by ``validate_efficiency_curve``.
        dc_wh: DC energy available to the conversion in one slot.
        rated_wh: AC energy at rated power in one slot (load fraction 1.0).
        upper_ac_wh: Upper bound of the result, e.g. the requested AC energy.

    Returns:
        The largest consistent AC energy that does not exceed ``upper_ac_wh``.
    """
    if dc_wh <= 0.0 or rated_wh <= 0.0 or upper_ac_wh <= 0.0:
        return 0.0
    tolerance = 1e-9
    best = -1.0

    def consider(ac_wh: float, low: float, high: float) -> None:
        nonlocal best
        fraction = ac_wh / rated_wh
        if (
            low - tolerance <= fraction <= high + tolerance
            and 0.0 < ac_wh <= upper_ac_wh * (1.0 + tolerance)
            and ac_wh > best
        ):
            best = ac_wh

    first_fraction, first_efficiency = curve[0]
    last_fraction, last_efficiency = curve[-1]
    # Clamped ends: constant efficiency below the first and above the last point.
    consider(dc_wh * first_efficiency, float("-inf"), first_fraction)
    consider(dc_wh * last_efficiency, last_fraction, float("inf"))
    for (x0, e0), (x1, e1) in zip(curve, curve[1:]):
        slope = (e1 - e0) / (x1 - x0)
        # a = dc * (e0 + slope * (a / rated - x0))  =>  a * (1 - dc*slope/rated) = dc * (e0 - slope*x0)
        denominator = 1.0 - dc_wh * slope / rated_wh
        if abs(denominator) < 1e-12:
            continue
        consider(dc_wh * (e0 - slope * x0) / denominator, x0, x1)
    if best >= 0.0:
        return min(best, upper_ac_wh)
    # No piece holds a solution within the bound (only possible numerically at a
    # kink): converge on it from the bound.
    ac_wh = min(upper_ac_wh, dc_wh)
    for _ in range(50):
        next_ac_wh = min(dc_wh * interpolate_efficiency_curve(curve, ac_wh / rated_wh), upper_ac_wh)
        if abs(next_ac_wh - ac_wh) <= tolerance:
            break
        ac_wh = next_ac_wh
    return ac_wh


def solve_ac_for_charge_dc_energy(
    curve: Sequence[tuple[float, float]],
    dc_wh: float,
    rated_wh: float,
) -> float:
    """Return the AC energy an AC→DC conversion draws to deliver a DC energy.

    The efficiency of a curve depends on the AC load drawn, so the AC energy
    ``a`` that delivers ``dc_wh`` is the solution of
    ``a * efficiency(a / rated_wh) = dc_wh``. The curve is piecewise linear and
    clamped at both ends; on each piece the equation is quadratic and solved
    exactly.

    Args:
        curve: Points as validated by ``validate_efficiency_curve``.
        dc_wh: DC energy delivered by the conversion in one slot.
        rated_wh: AC energy at rated power in one slot (load fraction 1.0).

    Returns:
        The smallest consistent AC energy, 0.0 for no DC energy.
    """
    if dc_wh <= 0.0:
        return 0.0
    if rated_wh <= 0.0:
        return dc_wh / curve[-1][1]
    tolerance = 1e-9
    best = float("inf")

    def consider(ac_wh: float, low: float, high: float) -> None:
        nonlocal best
        fraction = ac_wh / rated_wh
        if low - tolerance <= fraction <= high + tolerance and 0.0 < ac_wh < best:
            best = ac_wh

    first_fraction, first_efficiency = curve[0]
    last_fraction, last_efficiency = curve[-1]
    # Clamped ends: constant efficiency below the first and above the last point.
    consider(dc_wh / first_efficiency, float("-inf"), first_fraction)
    consider(dc_wh / last_efficiency, last_fraction, float("inf"))
    for (x0, e0), (x1, e1) in zip(curve, curve[1:]):
        slope = (e1 - e0) / (x1 - x0)
        # a * (c + k * a) = dc with c = e0 - slope * x0 and k = slope / rated.
        c = e0 - slope * x0
        k = slope / rated_wh
        if abs(k) < 1e-15:
            if c > 0.0:
                consider(dc_wh / c, x0, x1)
            continue
        discriminant = c * c + 4.0 * k * dc_wh
        if discriminant < 0.0:
            continue
        root = math.sqrt(discriminant)
        consider((-c + root) / (2.0 * k), x0, x1)
        consider((-c - root) / (2.0 * k), x0, x1)
    if best < float("inf"):
        return best
    # Only possible numerically at a kink: converge on it.
    ac_wh = dc_wh / interpolate_efficiency_curve(curve, 1.0)
    for _ in range(50):
        next_ac_wh = dc_wh / interpolate_efficiency_curve(curve, ac_wh / rated_wh)
        if abs(next_ac_wh - ac_wh) <= tolerance:
            break
        ac_wh = next_ac_wh
    return ac_wh
