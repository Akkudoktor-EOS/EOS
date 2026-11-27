"""The heat plan of a thermal device as the exact solution of a linear program.

The decision variable of a thermal device is the heat power ``Q[t]`` of every slot, between 0 and
the highest heat of the slot; the compressor runs for the fraction ``d[t] = min(Q[t] /
Q_first[t], 1)`` of the slot (the device is a thermostat, a fractional run time is executable).
Against the other devices of the house the heat is planned with the cost curve of every slot (``dpkernel.thermal_cost_curve``):
the change of the objective if the heat pump runs one of about a dozen heat levels in that slot
and everything else stays as in the current plan, the consequences followed (the state of charge
of the batteries in the later slots, their import and end value), so the energy a battery gives
to the heat pump costs what it costs later. The lower convex hull of the curve is written as rows
of an epigraph; between the levels the program sees the hull, not the curve. The curve is the
first order effect of a change of one slot; the changes of several slots interact (the program
adds them), and the guard of the search decides on the exact objective. What the program proposes is therefore a candidate, and the
guard of the search decides on the exact objective. The program has

- the heat ``Q[t]`` and the epigraph variable ``z[t]`` (house cost of the slot),
- the ramp ``s[t] >= Q[t] / Q_first[t] - Q[t-1] / Q_first[t-1]`` priced with the start energy per
  unit ramp (the cost of the full start at the top level minus the cost without it). ``d`` is
  concave in ``Q``, the exact ramp would need a yes/no variable per slot; ``Q / Q_first`` is the
  convex bound, so a slot above the first point counts as more than one start (a run that
  modulates up is priced as extra starts). The auxiliaries use ``Q / Q_first`` as well. The
  program proposes, the exact objective of the guard decides,
- the temperatures of all states (2 or 3) from the exact discretised dynamics,
- slacks for the room temperature below the comfort minimum or above the comfort maximum
  (comfort price per Kelvin hour), below the protection minimum (protection price) and for the
  end value of the stored heat per state (the deficit against the reference state times the value
  of heat). With a weighted device (``ThermalParams.under_weight``, the expected cost under
  uncertain presence) the shortfall below the comfort minimum is split into three bounded tiers
  (``UNDER_TIER_WIDTHS_K``, the third open) with rising prices, each times the weight of its slot;
  the convex prices fill the tiers in order,
- the electric power limit of the heat source as linear rows (electricity of a running slot,
  start energy included, within the limit; slots whose standby and circulation alone exceed it
  carry no heat).

The room temperature at the start of slot ``t + 1`` is held against the band of slot ``t + 1``
for the hours of slot ``t`` (as ``thermal.comfort_hours`` measures it). The program is always
feasible (slacks), so the solution is the optimum of the model; whether it is also better for the
true objective is decided by the guard of the search (``DeviceSearch._consider``), which takes a
candidate only if the exact objective falls.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import numpy as np
import scipy.sparse as sparse
from loguru import logger
from scipy.optimize import linprog

from akkudoktoreos.devices.models.thermal import (
    ELECTRIC_LIMIT_TOLERANCE_W,
    RUN_TOLERANCE_W,
    UNDER_TIER_WIDTHS_K,
    ThermalParams,
    clip_heat_plan,
)

# Heat below this power counts as numerical dust of the solver and is set to 0 [W].
DUST_HEAT_W = 1e-3

# HiGHS keeps thread-local state of its task scheduler that crashes the process (access violation,
# seen as a segmentation fault in the tests) when a thread that solved ends or when solves run
# concurrently. The starts of the search run in short-lived threads, so every solve goes to one
# worker per process that never ends; the solves of the starts take turns on it (about 25 ms each).
_SOLVER_THREAD = ThreadPoolExecutor(max_workers=1, thread_name_prefix="heatplan-highs")


def heat_levels(unit: ThermalParams) -> tuple[np.ndarray, np.ndarray]:
    """Return the heat levels at which the cost curve of every slot is computed [W].

    The levels of a slot are 0, half of the first operating heat, the operating points and the
    midpoints between consecutive points; a slot where the heat pump must not run has only 0.

    Returns:
        ``(levels, counts)``: ``levels`` has shape ``(slots, most levels)`` ascending per row,
        padded with the last value; ``counts`` the number of levels of each slot.
    """
    rows: list[np.ndarray] = []
    limit = unit.heat_limit_w
    for slot in range(unit.slots):
        if limit[slot] <= RUN_TOLERANCE_W:
            rows.append(np.zeros(1))
            continue
        points = unit.operating_heat_w[slot, : int(unit.operating_count[slot])]
        points = points[points > RUN_TOLERANCE_W]
        middles = 0.5 * (points[1:] + points[:-1])
        rows.append(np.unique(np.concatenate(([0.0, 0.5 * points[0]], points, middles))))
    counts = np.array([row.size for row in rows], dtype=np.int64)
    levels = np.zeros((unit.slots, int(counts.max())))
    for slot, row in enumerate(rows):
        levels[slot, : row.size] = row
        levels[slot, row.size :] = row[-1]
    return levels, counts


def lower_hull(x: np.ndarray, y: np.ndarray) -> list[tuple[float, float]]:
    """Return the vertices (ascending in x) of the lower convex hull of the points ``(x, y)``."""
    hull: list[tuple[float, float]] = []
    for index in np.argsort(x, kind="stable"):
        point = (float(x[index]), float(y[index]))
        if hull and abs(point[0] - hull[-1][0]) < 1e-12:
            if point[1] < hull[-1][1]:
                hull[-1] = point
            continue
        while len(hull) >= 2 and (
            (hull[-1][0] - hull[-2][0]) * (point[1] - hull[-2][1])
            - (hull[-1][1] - hull[-2][1]) * (point[0] - hull[-2][0])
            <= 0.0
        ):
            hull.pop()
        hull.append(point)
    return hull


def hull_segments(hull: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Return ``(slope, intercept)`` of the segments of a hull."""
    segments = []
    for first, second in zip(hull[:-1], hull[1:]):
        slope = (second[1] - first[1]) / (second[0] - first[0])
        segments.append((slope, first[1] - slope * first[0]))
    return segments


class _Rows:
    """Rows of a sparse constraint matrix with their right hand sides."""

    def __init__(self) -> None:
        self.row: list[int] = []
        self.column: list[int] = []
        self.value: list[float] = []
        self.rhs: list[float] = []

    def add(self, entries: list[tuple[int, float]], rhs: float) -> None:
        index = len(self.rhs)
        for column, value in entries:
            self.row.append(index)
            self.column.append(column)
            self.value.append(value)
        self.rhs.append(rhs)

    def matrix(self, columns: int) -> sparse.csr_matrix:
        return sparse.csr_matrix(
            (self.value, (self.row, self.column)), shape=(len(self.rhs), columns)
        )


def plan_heat(
    unit: ThermalParams,
    levels: np.ndarray,
    counts: np.ndarray,
    curve: np.ndarray,
    *,
    comfort_price: float,
    protection_price: float,
    heat_value_per_wh: float,
    time_limit_s: Optional[float] = None,
) -> Optional[tuple[np.ndarray, int]]:
    """Return the heat plan that minimises the linear program of the module [W].

    Args:
        unit: The thermal device.
        levels: ``(slots, levels)`` heat levels of the cost curve, see ``heat_levels``.
        counts: Number of levels per slot.
        curve: ``(slots, levels, 2)`` cost of the house, see ``dpkernel.thermal_cost_curve``.
        comfort_price: Price per Kelvin hour outside the comfort band [EUR/Kh].
        protection_price: Price per Kelvin hour below the protection minimum [EUR/Kh].
        heat_value_per_wh: End value per Wh of heat below the reference state [EUR/Wh].
        time_limit_s: Time the solver may take [s], the rest of the budget of the search; None
            for no limit.

    Returns:
        ``(heat, clipped)``: the heat per slot cut back to what the device can do
        (``clip_heat_plan``) and the number of slots that cut changed (the hull of the electric
        power limit lies below the table), or None if the device can never run or the solver
        fails, also for non-finite input or an exception of the solver (logged as a warning, the
        plan is kept).
    """
    slots = unit.slots
    states = unit.states
    top = unit.heat_limit_w
    if not np.any(top > RUN_TOLERANCE_W):
        return None
    hours = unit.slot_hours
    protection = unit.protection_min_c
    fixed = unit.fixed_power_w
    if protection is None or fixed is None:
        raise ValueError("The thermal device lacks its protection minimum or fixed power series.")
    limit_w = unit.max_electric_power_w
    # Variable layout: heat, epigraph, ramp, under, over, protection (each per slot), the second
    # and third tier of the shortfall of a weighted device, the temperatures (state-major), the end
    # value slack per state.
    q0, z0, s0, un0, ov0, pr0, un1, un2 = (k * slots for k in range(8))
    tiered = unit.tiered
    t0 = (8 if tiered else 6) * slots
    short0 = t0 + states * slots
    count = short0 + states
    cost = np.zeros(count)
    lower = np.zeros(count)
    upper = np.full(count, np.inf)
    lower[z0 : z0 + slots] = -np.inf
    lower[t0:short0] = -np.inf
    if tiered:
        upper[un0 : un0 + slots] = UNDER_TIER_WIDTHS_K[0]
        upper[un1 : un1 + slots] = UNDER_TIER_WIDTHS_K[1]
    inequality = _Rows()
    equality = _Rows()
    start_w = np.zeros(slots)
    scalars = unit.running_auxiliary_power_w
    first = np.maximum(unit.operating_heat_w[:, 0], RUN_TOLERANCE_W)
    for slot in range(slots):
        n_levels = int(counts[slot])
        slot_levels = levels[slot, :n_levels]
        no_start = curve[slot, :n_levels, 0] - curve[slot, 0, 0]
        runnable = top[slot] > RUN_TOLERANCE_W and (
            not np.isfinite(limit_w) or fixed[slot] <= limit_w + ELECTRIC_LIMIT_TOLERANCE_W
        )
        upper[q0 + slot] = top[slot] if runnable else 0.0
        cost[z0 + slot] = 1.0
        hull = lower_hull(slot_levels, no_start)
        if len(hull) == 1:
            inequality.add([(z0 + slot, -1.0)], -hull[0][1])
        for slope, intercept in hull_segments(hull):
            inequality.add([(q0 + slot, slope), (z0 + slot, -1.0)], -intercept)
        upper[s0 + slot] = top[slot] / first[slot]
        if runnable:
            top_level = n_levels - 1
            # The price of the energy of a full start, per unit ramp (energy only costs).
            cost[s0 + slot] = max(curve[slot, top_level, 1] - curve[slot, top_level, 0], 0.0)
            start_w[slot] = (
                unit.startup_energy_wh
                + unit.startup_warmup_s / 3600.0 * unit.operating_electric_w[slot, 0]
            ) / hours[slot]
            # Ramp: s[t] >= Q[t] / Q_first[t] - Q[t-1] / Q_first[t-1].
            entries = [(q0 + slot, 1.0 / first[slot]), (s0 + slot, -1.0)]
            right = 0.0
            if slot == 0:
                right = 1.0 if unit.initial_running else 0.0
            elif top[slot - 1] > RUN_TOLERANCE_W:
                entries.append((q0 + slot - 1, -1.0 / first[slot - 1]))
            inequality.add(entries, right)
            if np.isfinite(limit_w):
                count_points = int(unit.operating_count[slot])
                points_x = np.concatenate(([0.0], unit.operating_heat_w[slot, :count_points]))
                points_y = np.concatenate(([0.0], unit.operating_electric_w[slot, :count_points]))
                for slope, intercept in hull_segments(lower_hull(points_x, points_y)):
                    inequality.add(
                        [
                            (q0 + slot, slope + scalars / first[slot]),
                            (s0 + slot, start_w[slot]),
                        ],
                        limit_w - fixed[slot] - intercept,
                    )
        # Dynamics: T[i, t] = sum_j A[t, i, j] T[j, t-1] + B[t, i] Q[t] + E[t, i] T_out[t].
        for state in range(states):
            entries = [(t0 + state * slots + slot, 1.0), (q0 + slot, -unit.dynamics_b[slot, state])]
            right = unit.dynamics_e[slot, state] * unit.outdoor_c[slot]
            for other in range(states):
                factor = unit.dynamics_a[slot, state, other]
                if slot == 0:
                    right += factor * unit.initial_temperature_c[other]
                else:
                    entries.append((t0 + other * slots + slot - 1, -factor))
            equality.add(entries, right)
        # Comfort band of the next slot holds the room at the end of this one.
        band = min(slot + 1, slots - 1)
        room = t0 + slot
        under = [(un0 + slot, -1.0)] + ([(un1 + slot, -1.0), (un2 + slot, -1.0)] if tiered else [])
        inequality.add([(room, -1.0), *under], -unit.comfort_min_c[band])
        inequality.add([(room, 1.0), (ov0 + slot, -1.0)], unit.comfort_max_c[band])
        if np.isfinite(protection[band]):
            inequality.add([(room, -1.0), (pr0 + slot, -1.0)], -protection[band])
        for tier, (variable, _) in enumerate(under):
            cost[variable] = hours[slot] * comfort_price * unit.under_weight[band, tier]  # type: ignore[index]
        cost[ov0 + slot] = hours[slot] * comfort_price
        cost[pr0 + slot] = hours[slot] * protection_price
    for state in range(states):
        capacity = unit.capacity_wh_per_k[state]
        inequality.add(
            [(t0 + state * slots + slots - 1, -capacity), (short0 + state, -1.0)],
            -unit.reference_temperature_c[state] * capacity,
        )
        cost[short0 + state] = max(heat_value_per_wh, 0.0)  # a heat deficit never earns
    matrix_ub, matrix_eq = inequality.matrix(count), equality.matrix(count)
    right_ub, right_eq = np.array(inequality.rhs), np.array(equality.rhs)
    finite = (
        np.all(np.isfinite(cost))
        and np.all(np.isfinite(right_ub))
        and np.all(np.isfinite(right_eq))
        and np.all(np.isfinite(matrix_ub.data))
        and np.all(np.isfinite(matrix_eq.data))
    )
    if not finite:
        logger.warning(
            "The heat plan of {} could not be computed (non-finite input); the plan is kept.",
            unit.name,
        )
        return None
    options = {} if time_limit_s is None else {"time_limit": max(time_limit_s, 1e-3)}
    try:
        result = _SOLVER_THREAD.submit(
            linprog,  # type: ignore[arg-type]  # sparse matrices are valid
            cost,
            A_ub=matrix_ub,
            b_ub=right_ub,
            A_eq=matrix_eq,
            b_eq=right_eq,
            bounds=np.column_stack([lower, upper]),
            method="highs",
            options=options,
        ).result()
    except Exception as exc:  # the solver must not cancel the planning of the other devices
        logger.warning(
            "The heat plan of {} could not be computed ({}: {}); the plan is kept.",
            unit.name,
            type(exc).__name__,
            exc,
        )
        return None
    if result.status != 0 or result.x is None:
        logger.warning(
            "The heat plan of {} could not be computed (solver status {}: {}); the plan is kept.",
            unit.name,
            result.status,
            result.message,
        )
        return None
    heat = np.clip(result.x[q0 : q0 + slots], 0.0, top)
    heat[heat < DUST_HEAT_W] = 0.0
    clipped = clip_heat_plan(unit, heat)
    return clipped, int(np.count_nonzero(np.abs(clipped - heat) > 1e-6))
