"""Hybrid inverter model.

An inverter bridges the DC side (PV, battery) and the AC side (house, grid). Its AC power
limit bounds the energy that passes through it in a slot: direct PV consumption, battery
discharge and export. Conversion efficiencies are constant or depend on the AC load
(a curve of ``(load_fraction, efficiency)`` points). Grid charging of its battery is limited
by ``max_ac_charge_power_w``.

The step functions compile with numba. Parameters are flattened into:

- ``fields``: ``(n_inverters, N_FIELDS)`` scalar parameters, columns ``F_*``
- ``curve_x`` / ``curve_y``: ``(n_inverters, 2, n_points)`` curve points of the DC to AC curve
  (index 0) and the AC to DC curve (index 1); ``F_*_POINTS`` is the number of valid points
"""

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from akkudoktoreos.devices.models.jit import njit

# Columns of the ``fields`` array.
F_MAX_POWER_W = 0
F_DC_TO_AC_EFFICIENCY = 1
F_AC_TO_DC_EFFICIENCY = 2
F_MAX_AC_CHARGE_W = 3  # NaN: no limit
F_AC_LIMITS_TOTAL_CHARGE = 4
F_DC_TO_AC_POINTS = 5
F_AC_TO_DC_POINTS = 6
F_BATTERY = 7  # index of the attached battery, -1 without
N_FIELDS = 8

DC_TO_AC = 0
AC_TO_DC = 1


@dataclass(frozen=True)
class InverterParams:
    """Parameters of an inverter and the battery attached to it.

    Attributes:
        max_power_w: Maximum AC power [W].
        battery_index: Index of the attached battery in the problem's battery list, or None.
        dc_to_ac_efficiency: Constant DC to AC efficiency, used without curve.
        ac_to_dc_efficiency: Constant AC to DC efficiency; 0 disables AC charging.
        dc_to_ac_efficiency_curve: ``(load_fraction, efficiency)`` points; replaces the constant.
        ac_to_dc_efficiency_curve: Same for the AC to DC conversion of grid charging.
        max_ac_charge_power_w: Limit of the AC power drawn for charging, None for none.
        ac_charge_limits_total_charge: The grid charge level caps the total charge power,
            PV included (surplus above it is exported instead of stored).
        dc_to_ac_efficiency_reference_load_fraction: Load fraction at which the DC to AC curve
            values stored energy where no single conversion is known (terminal value,
            break-even of grid charging).
    """

    max_power_w: float
    battery_index: Optional[int] = 0
    dc_to_ac_efficiency: float = 1.0
    ac_to_dc_efficiency: float = 1.0
    dc_to_ac_efficiency_curve: Optional[tuple[tuple[float, float], ...]] = None
    ac_to_dc_efficiency_curve: Optional[tuple[tuple[float, float], ...]] = None
    max_ac_charge_power_w: Optional[float] = None
    ac_charge_limits_total_charge: bool = False
    dc_to_ac_efficiency_reference_load_fraction: float = 0.06


def reference_dc_to_ac_efficiency(inverter: InverterParams, slot_duration_h: float) -> float:
    """Return the DC to AC efficiency used to value stored energy without a known conversion.

    Constant without curve, else the curve at the configured reference load.
    """
    fields, curve_x, curve_y = pack_inverters((inverter,))
    max_power_wh = inverter.max_power_w * slot_duration_h
    return float(
        dc_to_ac_efficiency_at(
            fields,
            curve_x,
            curve_y,
            0,
            inverter.dc_to_ac_efficiency_reference_load_fraction * max_power_wh,
            slot_duration_h,
            1.0,
        )
    )


def pack_inverters(
    inverters: tuple[InverterParams, ...],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Flatten inverter parameters into ``(fields, curve_x, curve_y)``."""
    count = len(inverters)
    fields = np.zeros((count, N_FIELDS))
    width = 2
    for inverter in inverters:
        for curve in (inverter.dc_to_ac_efficiency_curve, inverter.ac_to_dc_efficiency_curve):
            if curve is not None:
                width = max(width, len(curve))
    curve_x = np.zeros((count, 2, width))
    curve_y = np.zeros((count, 2, width))
    for index, inverter in enumerate(inverters):
        row = fields[index]
        row[F_MAX_POWER_W] = inverter.max_power_w
        row[F_DC_TO_AC_EFFICIENCY] = inverter.dc_to_ac_efficiency
        row[F_AC_TO_DC_EFFICIENCY] = inverter.ac_to_dc_efficiency
        row[F_MAX_AC_CHARGE_W] = (
            math.nan if inverter.max_ac_charge_power_w is None else inverter.max_ac_charge_power_w
        )
        row[F_AC_LIMITS_TOTAL_CHARGE] = 1.0 if inverter.ac_charge_limits_total_charge else 0.0
        row[F_BATTERY] = -1 if inverter.battery_index is None else inverter.battery_index
        for kind, curve in (
            (DC_TO_AC, inverter.dc_to_ac_efficiency_curve),
            (AC_TO_DC, inverter.ac_to_dc_efficiency_curve),
        ):
            if curve is None:
                continue
            row[F_DC_TO_AC_POINTS + kind] = len(curve)
            curve_x[index, kind, : len(curve)] = [point[0] for point in curve]
            curve_y[index, kind, : len(curve)] = [point[1] for point in curve]
    return fields, curve_x, curve_y


@njit
def dc_to_ac_efficiency_at(
    fields: np.ndarray,
    curve_x: np.ndarray,
    curve_y: np.ndarray,
    inverter: int,
    ac_wh: float,
    slot_duration_h: float,
    scale: float,
) -> float:
    """Return the DC to AC efficiency of a conversion delivering ``ac_wh`` in a slot."""
    if fields[inverter, F_DC_TO_AC_POINTS] == 0.0:
        return fields[inverter, F_DC_TO_AC_EFFICIENCY]
    slot_max_power_wh = fields[inverter, F_MAX_POWER_W] * slot_duration_h * scale
    load_fraction = ac_wh / slot_max_power_wh if slot_max_power_wh > 0 else 0.0
    return interpolate_curve_points(
        curve_x,
        curve_y,
        inverter,
        DC_TO_AC,
        int(fields[inverter, F_DC_TO_AC_POINTS]),
        load_fraction,
    )


@njit
def ac_charge_factor(
    fields: np.ndarray, max_charge_w: float, inverter: int, factor: float
) -> float:
    """Return the grid charge level the inverter can execute.

    The level is a fraction of the battery's rated charge power. It is capped so that the AC
    input stays within ``max_ac_charge_power_w`` and is 0 when AC charging is impossible.
    """
    if (
        factor <= 0.0
        or fields[inverter, F_BATTERY] < 0.0
        or fields[inverter, F_AC_TO_DC_EFFICIENCY] <= 0.0
    ):
        return 0.0
    max_ac = fields[inverter, F_MAX_AC_CHARGE_W]
    if not math.isnan(max_ac) and max_charge_w > 0:
        capped = max_ac * fields[inverter, F_AC_TO_DC_EFFICIENCY] / max_charge_w
        if capped < factor:
            factor = capped
    return max(factor, 0.0)


@njit
def solve_ac_for_dc_energy(
    curve_x: np.ndarray,
    curve_y: np.ndarray,
    inverter: int,
    points: int,
    dc_wh: float,
    rated_wh: float,
    upper_ac_wh: float,
) -> float:
    """Return the AC energy a DC energy yields through a load dependent DC to AC conversion.

    The delivered AC energy ``a`` solves ``a = dc_wh * efficiency(a / rated_wh)``; the curve is
    piecewise linear and clamped, so the equation is solved exactly on each piece. The result
    is the largest consistent value not above ``upper_ac_wh``.
    """
    if dc_wh <= 0.0 or rated_wh <= 0.0 or upper_ac_wh <= 0.0:
        return 0.0
    tolerance = 1e-9
    best = -1.0
    infinity = math.inf
    first_fraction = curve_x[inverter, DC_TO_AC, 0]
    first_efficiency = curve_y[inverter, DC_TO_AC, 0]
    last_fraction = curve_x[inverter, DC_TO_AC, points - 1]
    last_efficiency = curve_y[inverter, DC_TO_AC, points - 1]
    # Clamped ends: constant efficiency below the first and above the last point.
    for candidate_index in range(2):
        if candidate_index == 0:
            ac_wh = dc_wh * first_efficiency
            low = -infinity
            high = first_fraction
        else:
            ac_wh = dc_wh * last_efficiency
            low = last_fraction
            high = infinity
        fraction = ac_wh / rated_wh
        if (
            low - tolerance <= fraction <= high + tolerance
            and 0.0 < ac_wh <= upper_ac_wh * (1.0 + tolerance)
            and ac_wh > best
        ):
            best = ac_wh
    for position in range(points - 1):
        x0 = curve_x[inverter, DC_TO_AC, position]
        e0 = curve_y[inverter, DC_TO_AC, position]
        x1 = curve_x[inverter, DC_TO_AC, position + 1]
        e1 = curve_y[inverter, DC_TO_AC, position + 1]
        slope = (e1 - e0) / (x1 - x0)
        denominator = 1.0 - dc_wh * slope / rated_wh
        if abs(denominator) < 1e-12:
            continue
        ac_wh = dc_wh * (e0 - slope * x0) / denominator
        fraction = ac_wh / rated_wh
        if (
            x0 - tolerance <= fraction <= x1 + tolerance
            and 0.0 < ac_wh <= upper_ac_wh * (1.0 + tolerance)
            and ac_wh > best
        ):
            best = ac_wh
    if best >= 0.0:
        return min(best, upper_ac_wh)
    # No piece holds a solution within the bound (only possible numerically at a kink):
    # converge on it from the bound.
    ac_wh = min(upper_ac_wh, dc_wh)
    for _ in range(50):
        efficiency = interpolate_curve_points(
            curve_x, curve_y, inverter, DC_TO_AC, points, ac_wh / rated_wh
        )
        next_ac_wh = min(dc_wh * efficiency, upper_ac_wh)
        if abs(next_ac_wh - ac_wh) <= tolerance:
            break
        ac_wh = next_ac_wh
    return ac_wh


@njit
def interpolate_curve_points(
    curve_x: np.ndarray,
    curve_y: np.ndarray,
    inverter: int,
    kind: int,
    points: int,
    load_fraction: float,
) -> float:
    """Return the efficiency of the first ``points`` points of a curve at a load fraction."""
    first_fraction = curve_x[inverter, kind, 0]
    first_efficiency = curve_y[inverter, kind, 0]
    if load_fraction <= first_fraction:
        return first_efficiency
    for position in range(1, points):
        fraction = curve_x[inverter, kind, position]
        efficiency = curve_y[inverter, kind, position]
        if load_fraction <= fraction:
            return first_efficiency + (load_fraction - first_fraction) / (
                fraction - first_fraction
            ) * (efficiency - first_efficiency)
        first_fraction = fraction
        first_efficiency = efficiency
    return first_efficiency


@njit
def solve_ac_for_charge_dc_energy(
    curve_x: np.ndarray,
    curve_y: np.ndarray,
    inverter: int,
    points: int,
    dc_wh: float,
    rated_wh: float,
) -> float:
    """Return the AC energy an AC to DC conversion draws to deliver ``dc_wh``.

    The AC energy ``a`` solves ``a * efficiency(a / rated_wh) = dc_wh``; on each piece of the
    curve this is a quadratic that is solved exactly. The smallest consistent value is returned.
    """
    if dc_wh <= 0.0:
        return 0.0
    if rated_wh <= 0.0:
        return dc_wh / curve_y[inverter, AC_TO_DC, points - 1]
    tolerance = 1e-9
    best = math.inf
    first_fraction = curve_x[inverter, AC_TO_DC, 0]
    first_efficiency = curve_y[inverter, AC_TO_DC, 0]
    last_fraction = curve_x[inverter, AC_TO_DC, points - 1]
    last_efficiency = curve_y[inverter, AC_TO_DC, points - 1]
    for candidate_index in range(2):
        if candidate_index == 0:
            ac_wh = dc_wh / first_efficiency
            low = -math.inf
            high = first_fraction
        else:
            ac_wh = dc_wh / last_efficiency
            low = last_fraction
            high = math.inf
        fraction = ac_wh / rated_wh
        if low - tolerance <= fraction <= high + tolerance and 0.0 < ac_wh < best:
            best = ac_wh
    for position in range(points - 1):
        x0 = curve_x[inverter, AC_TO_DC, position]
        e0 = curve_y[inverter, AC_TO_DC, position]
        x1 = curve_x[inverter, AC_TO_DC, position + 1]
        e1 = curve_y[inverter, AC_TO_DC, position + 1]
        slope = (e1 - e0) / (x1 - x0)
        c = e0 - slope * x0
        k = slope / rated_wh
        if abs(k) < 1e-15:
            if c > 0.0:
                ac_wh = dc_wh / c
                fraction = ac_wh / rated_wh
                if x0 - tolerance <= fraction <= x1 + tolerance and 0.0 < ac_wh < best:
                    best = ac_wh
            continue
        discriminant = c * c + 4.0 * k * dc_wh
        if discriminant < 0.0:
            continue
        root = math.sqrt(discriminant)
        for sign in range(2):
            if sign == 0:
                ac_wh = (-c + root) / (2.0 * k)
            else:
                ac_wh = (-c - root) / (2.0 * k)
            fraction = ac_wh / rated_wh
            if x0 - tolerance <= fraction <= x1 + tolerance and 0.0 < ac_wh < best:
                best = ac_wh
    if best < math.inf:
        return best
    # Only possible numerically at a kink: converge on it.
    ac_wh = dc_wh / interpolate_curve_points(curve_x, curve_y, inverter, AC_TO_DC, points, 1.0)
    for _ in range(50):
        efficiency = interpolate_curve_points(
            curve_x, curve_y, inverter, AC_TO_DC, points, ac_wh / rated_wh
        )
        next_ac_wh = dc_wh / efficiency
        if abs(next_ac_wh - ac_wh) <= tolerance:
            break
        ac_wh = next_ac_wh
    return ac_wh
