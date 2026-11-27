"""Thermal device: a heat pump with a room model, planned like a battery.

The state is the temperature vector of the room model: room and building mass (``direct``
heating, 2 states) plus the air node of an air distribution (3 states). One slot of the model is
the exact discretisation of the linear dynamics

    T[k+1] = A[k] T[k] + B[k] Q[k] + E[k] T_out[k]

with matrices built before the planning (``thermal.thermaldistribution``); ``Q`` is the heat
delivered during the slot [W]. The decision of a slot is the heat power: any value from 0 up to
the maximum power of the slot (the last operating point of its table). The run time
``d = min(Q / Q_first, 1)`` is the fraction of the slot the compressor runs, at its first
operating point or, above it, at its load; a fractional run time is executable (the device is a
thermostat).

Electricity follows from the heat through a per-slot table of operating points
``(heat, electric power)``, interpolated linearly and never extrapolated, with the chord from
(0 W, 0 W) to the first point below it (time sharing), plus the electricity that does not depend
on the heat: standby and the circulation of the distribution in every slot, the auxiliaries
scaled with the run time, and the energy of a start. The start energy is continuous: it is
the ramp ``max(0, d[k] - d[k-1])`` times the energy of one start (``startup_energy_wh`` plus a
warm-up time at the electric power of the first operating point). A compressor that keeps
running and only modulates up (``d`` stays 1) does not start.

The electricity of the slot is load of the house, added to the base load by the kernel. The
temperatures never depend on the electric state, so the thermal trajectory of a plan is computed
in a pass of its own before the house is simulated.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from akkudoktoreos.devices.models.jit import njit

# Heat below this power counts as "not running" [W]; also the tolerance at the support limits.
RUN_TOLERANCE_W = 1e-5

# Tolerance of the electric power limit [W] (rounding of the cut of the operating table).
ELECTRIC_LIMIT_TOLERANCE_W = 1e-6

# Widths of the first two tiers of the shortfall below the comfort minimum [K]; the third is open.
UNDER_TIER_WIDTHS_K = (0.5, 1.0)

# Entries of the ``data`` tuple of a thermal device (see ``ThermalParams.arrays``).
D_A = 0
D_B = 1
D_E = 2
D_OUTDOOR = 3
D_HOURS = 4
D_OP_HEAT = 5
D_OP_ELECTRIC = 6
D_OP_COUNT = 7
D_FIXED_W = 8
D_SCALARS = 9

# Entries of the scalar array.
S_AUXILIARY_W = 0
S_STARTUP_WH = 1
S_WARMUP_S = 2
S_LIMIT_W = 3

# Status of ``run_thermal``: 0 is fine, otherwise the 1-based slot with unsupported heat.
STATUS_OK = 0


@dataclass(frozen=True, eq=False)
class ThermalParams:
    """Parameters and forecasts of one thermal device. Every series has one value per slot.

    Attributes:
        initial_temperature_c: Temperatures at slot 0, room first, then building mass, then
            the air node (``n`` = 2 or 3).
        initial_running: Whether the heat pump was running before slot 0: the run share before
            slot 0 is 1 if so, else 0 (decides the start energy of the first slot).
        dynamics_a: ``(slots, n, n)`` transition matrices.
        dynamics_b: ``(slots, n)`` effect of 1 W of delivered heat [K/W] over the slot.
        dynamics_e: ``(slots, n)`` effect of the outdoor temperature.
        outdoor_c: Outdoor temperature per slot [°C].
        slot_hours: Hours covered by each slot (slot 0 may be shorter) [h].
        comfort_min_c: Lower room temperature bound per slot [°C]; the room temperature at the
            start of a slot is held against the bound of that slot, the final one against
            the last slot's.
        comfort_max_c: Upper room temperature bound per slot [°C].
        protection_min_c: Protection minimum of the room temperature per slot [°C]
            (damp or frost protection, below the comfort minimum); ``-inf`` for none.
        operating_heat_w: ``(slots, points)`` heat of the operating points, ascending in a row,
            padded with the last value; the heat between 0 and the first point is time sharing.
        operating_electric_w: ``(slots, points)`` compressor electric power at those heats [W].
        operating_count: Number of operating points per slot; 0 = the heat pump cannot run.
        blocked: True where the heat pump must not run (barred by the user or the grid operator,
            or outside the operating range).
        fixed_power_w: Electric power in every slot independent of the heat: standby and
            circulation [W].
        running_auxiliary_power_w: Additional electric power at the run share 1 [W]; it scales
            with the share.
        startup_energy_wh: Energy of one full start (ramp 1) [Wh].
        startup_warmup_s: Warm-up time of a full start at the compressor power of the slot [s].
        max_electric_power_w: Electric power the heat source may draw in a running slot [W]: the
            electricity of the slot (compressor, standby, circulation, auxiliaries and the energy
            of the start, ramp times start energy, spread over the slot) divided by its hours.
            The operating table is cut at it for the running slots; the start energy counts on top.
            ``inf`` = no limit.
        capacity_wh_per_k: Heat capacity of each state [Wh/K] (converts temperature differences
            into stored heat).
        reference_temperature_c: Temperatures the building should have at the end of the horizon
            (the steady state that holds the target room temperature at the mean outdoor
            temperature); the end value of the stored heat is measured against it.
        reference_heat_w: Heat that holds the reference state [W]; the end value is only active
            where this is positive (a heating load exists).
        under_weight: ``(slots, 3)`` weight of a Kelvin hour below the comfort minimum per tier of
            the shortfall (``UNDER_TIER_WIDTHS_K``) and slot, relative to the comfort price: the
            presence probability of the slot times the tier price over the comfort price.
            None = 1, the shortfall is priced at the comfort price.
    """

    initial_temperature_c: np.ndarray
    dynamics_a: np.ndarray
    dynamics_b: np.ndarray
    dynamics_e: np.ndarray
    outdoor_c: np.ndarray
    slot_hours: np.ndarray
    comfort_min_c: np.ndarray
    comfort_max_c: np.ndarray
    operating_heat_w: np.ndarray
    operating_electric_w: np.ndarray
    operating_count: np.ndarray
    capacity_wh_per_k: np.ndarray
    reference_temperature_c: np.ndarray
    protection_min_c: Optional[np.ndarray] = None
    blocked: Optional[np.ndarray] = None
    fixed_power_w: Optional[np.ndarray] = None
    initial_running: bool = False
    running_auxiliary_power_w: float = 0.0
    startup_energy_wh: float = 0.0
    startup_warmup_s: float = 0.0
    max_electric_power_w: float = math.inf
    reference_heat_w: float = 0.0
    under_weight: Optional[np.ndarray] = None
    name: str = "heat_pump"
    _arrays: Any = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Validate the shapes and prepare the arrays of the compiled functions."""
        states = len(self.initial_temperature_c)
        if states not in (2, 3):
            raise ValueError(
                f"A thermal device has 2 (direct) or 3 (air distribution) states, got {states}. "
                "Water and buffer distributions are not supported."
            )
        slots = len(self.outdoor_c)

        def as_float(name: str, shape: tuple[int, ...], default: Optional[float] = None) -> None:
            value = getattr(self, name)
            if value is None:
                array = np.full(shape, default, dtype=float)
            else:
                array = np.ascontiguousarray(value, dtype=float)
            if array.shape != shape:
                raise ValueError(f"{name} has shape {array.shape}, expected {shape}.")
            object.__setattr__(self, name, array)

        as_float("initial_temperature_c", (states,))
        as_float("dynamics_a", (slots, states, states))
        as_float("dynamics_b", (slots, states))
        as_float("dynamics_e", (slots, states))
        as_float("outdoor_c", (slots,))
        as_float("slot_hours", (slots,))
        as_float("comfort_min_c", (slots,))
        as_float("comfort_max_c", (slots,))
        as_float("protection_min_c", (slots,), -np.inf)
        as_float("fixed_power_w", (slots,), 0.0)
        as_float("capacity_wh_per_k", (states,))
        as_float("reference_temperature_c", (states,))
        as_float("under_weight", (slots, 3), 1.0)
        if self.operating_heat_w.ndim != 2 or self.operating_heat_w.shape[0] != slots:
            raise ValueError("operating_heat_w must have shape (slots, points).")
        points = self.operating_heat_w.shape[1]
        as_float("operating_heat_w", (slots, points))
        as_float("operating_electric_w", (slots, points))
        count = np.ascontiguousarray(self.operating_count, dtype=np.int64)
        if count.shape != (slots,) or np.any(count < 0) or np.any(count > points):
            raise ValueError("operating_count needs one value in 0 ... points per slot.")
        object.__setattr__(self, "operating_count", count)
        blocked = (
            np.zeros(slots, dtype=bool)
            if self.blocked is None
            else np.ascontiguousarray(self.blocked, dtype=bool)
        )
        if blocked.shape != (slots,):
            raise ValueError("blocked needs one value per slot.")
        object.__setattr__(self, "blocked", blocked | (count == 0))
        for slot in range(slots):
            heat = self.operating_heat_w[slot, : count[slot]]
            if np.any(np.diff(heat) < 0.0) or np.any(heat < 0.0):
                raise ValueError("Operating points must ascend in heat and be nonnegative.")
        if np.any(self.capacity_wh_per_k <= 0.0):
            raise ValueError("capacity_wh_per_k must be positive.")
        if not self.max_electric_power_w >= 0.0:
            raise ValueError("max_electric_power_w must not be negative (inf = no limit).")
        if np.any(self.slot_hours <= 0.0):
            raise ValueError("slot_hours must be positive.")

    @property
    def states(self) -> int:
        """Number of temperature states (2 or 3)."""
        return int(self.initial_temperature_c.shape[0])

    @property
    def tiered(self) -> bool:
        """Whether the shortfall below the comfort minimum is weighted by tier and slot."""
        return bool(np.any(self.under_weight != 1.0))

    @property
    def slots(self) -> int:
        """Number of slots of the horizon."""
        return int(self.outdoor_c.shape[0])

    def arrays(self) -> tuple[Any, ...]:
        """Return the flat arrays of the compiled functions (built once), see ``D_*``."""
        cached = self._arrays
        if cached is None:
            scalars = np.array(
                [
                    self.running_auxiliary_power_w,
                    self.startup_energy_wh,
                    self.startup_warmup_s,
                    self.max_electric_power_w,
                ]
            )
            cached = (
                self.dynamics_a,
                self.dynamics_b,
                self.dynamics_e,
                self.outdoor_c,
                self.slot_hours,
                self.operating_heat_w,
                self.operating_electric_w,
                self.operating_count,
                self.fixed_power_w,
                scalars,
            )
            object.__setattr__(self, "_arrays", cached)
        return tuple(cached)

    def heat_range_w(self, slot: int) -> Optional[tuple[float, float]]:
        """Return the first operating point's heat and the highest heat of a slot [W].

        None where the heat pump must not run. The heat between 0 and the first point is
        allowed too (time sharing); the first point is where the table starts.
        """
        if self.blocked is not None and bool(self.blocked[slot]):
            return None
        count = int(self.operating_count[slot])
        return float(self.operating_heat_w[slot, 0]), float(self.operating_heat_w[slot, count - 1])

    @property
    def heat_limit_w(self) -> np.ndarray:
        """Return the highest heat of every slot [W]; 0 where the heat pump must not run."""
        top = np.zeros(self.slots)
        for slot in range(self.slots):
            count = int(self.operating_count[slot])
            if count > 0 and not (self.blocked is not None and self.blocked[slot]):
                top[slot] = self.operating_heat_w[slot, count - 1]
        return top

    def unsupported_heat(self, heat_w: np.ndarray) -> np.ndarray:
        """Return per slot whether the heat is not finite or above the operating table."""
        last = np.maximum(self.operating_count - 1, 0)
        top = self.operating_heat_w[np.arange(self.slots), last] * (self.operating_count > 0)
        return ~np.isfinite(heat_w) | (heat_w > top + RUN_TOLERANCE_W)


@njit
def base_electric_w(
    op_heat: np.ndarray, op_electric: np.ndarray, op_count: np.ndarray, slot: int, heat_w: float
) -> float:
    """Return the compressor electric power at ``heat_w`` [W]; -1 where the heat is unsupported.

    The table of the slot is interpolated linearly; below its first point the chord from
    (0 W, 0 W) to that point applies (time sharing). There is no extrapolation above the last
    point: a heat beyond it (beyond the tolerance) is unsupported, and so is any heat in a slot
    without operating points.
    """
    if heat_w <= RUN_TOLERANCE_W:
        return 0.0
    count = op_count[slot]
    if count == 0:
        return -1.0
    first = op_heat[slot, 0]
    last = op_heat[slot, count - 1]
    if heat_w > last + RUN_TOLERANCE_W:
        return -1.0
    if heat_w < first:
        return op_electric[slot, 0] * heat_w / first
    if heat_w <= first:
        return op_electric[slot, 0]
    if heat_w >= last:
        return op_electric[slot, count - 1]
    for point in range(1, count):
        upper = op_heat[slot, point]
        if heat_w <= upper:
            lower = op_heat[slot, point - 1]
            if upper == lower:
                return op_electric[slot, point]
            weight = (heat_w - lower) / (upper - lower)
            return op_electric[slot, point - 1] + weight * (
                op_electric[slot, point] - op_electric[slot, point - 1]
            )
    return op_electric[slot, count - 1]


@njit
def heat_share(data: tuple[Any, ...], slot: int, heat_w: float) -> float:
    """Return the run time ``min(heat / first point, 1)`` of a slot, 0 where it cannot run (0 ... 1)."""
    if data[D_OP_COUNT][slot] == 0 or heat_w <= RUN_TOLERANCE_W:
        return 0.0
    return min(heat_w / max(data[D_OP_HEAT][slot, 0], RUN_TOLERANCE_W), 1.0)


@njit
def thermal_electric_wh(
    data: tuple[Any, ...], slot: int, heat_w: float, previous_share: float
) -> tuple[float, bool]:
    """Return the electric energy of a slot [Wh] and whether the heat is supported.

    Args:
        data: Arrays of the device, see ``D_*``.
        slot: Slot index.
        heat_w: Delivered heat during the slot [W].
        previous_share: Run time of the slot before (1 if the heat pump was running before
            slot 0, else 0); the ramp ``max(0, share - previous_share)`` carries the start energy.

    Returns:
        ``(electric_wh, supported)``; ``supported`` is False where the heat lies outside the
        operating table of the slot (the energy is then that of zero heat).
    """
    base = base_electric_w(data[D_OP_HEAT], data[D_OP_ELECTRIC], data[D_OP_COUNT], slot, heat_w)
    supported = base >= 0.0
    if not supported:
        base = 0.0
    scalars = data[D_SCALARS]
    share = heat_share(data, slot, heat_w) if supported else 0.0
    power = base + data[D_FIXED_W][slot] + scalars[S_AUXILIARY_W] * share
    energy = power * data[D_HOURS][slot]
    ramp = max(share - previous_share, 0.0)
    start_w = data[D_OP_ELECTRIC][slot, 0]  # the compressor starts at its first operating point
    energy += ramp * (scalars[S_STARTUP_WH] + scalars[S_WARMUP_S] / 3600.0 * start_w)
    return energy, supported


@njit
def electric_within_limit(
    data: tuple[Any, ...], slot: int, heat_w: float, energy_wh: float
) -> bool:
    """Return whether the electricity of a running slot stays within the power limit.

    Args:
        data: Arrays of the device, see ``D_*``.
        slot: Slot index.
        heat_w: Delivered heat during the slot [W]; a slot without heat is not checked (standby
            alone cannot be avoided).
        energy_wh: Electric energy of the slot [Wh] as ``thermal_electric_wh`` returns it, the
            energy of a start included.
    """
    if heat_w <= RUN_TOLERANCE_W:
        return True
    hours = data[D_HOURS][slot]
    return energy_wh <= (data[D_SCALARS][S_LIMIT_W] + ELECTRIC_LIMIT_TOLERANCE_W) * hours


@njit
def thermal_step(
    data: tuple[Any, ...],
    slot: int,
    temperature: np.ndarray,
    heat_w: float,
    previous_share: float,
    out_temperature: np.ndarray,
) -> tuple[float, bool]:
    """Advance one slot: write the next temperatures, return its electricity.

    Args:
        data: Arrays of the device, see ``D_*``.
        slot: Slot index.
        temperature: Temperatures at the start of the slot.
        heat_w: Delivered heat during the slot [W].
        previous_share: Run share of the slot before.
        out_temperature: Result, temperatures at the end of the slot (may not alias the input).

    Returns:
        ``(electric_wh, supported)`` as ``thermal_electric_wh``.
    """
    a = data[D_A]
    b = data[D_B]
    e = data[D_E]
    outdoor = data[D_OUTDOOR][slot]
    states = temperature.shape[0]
    for i in range(states):
        value = b[slot, i] * heat_w + e[slot, i] * outdoor
        for j in range(states):
            value += a[slot, i, j] * temperature[j]
        out_temperature[i] = value
    return thermal_electric_wh(data, slot, heat_w, previous_share)


@njit
def run_thermal(
    data: tuple[Any, ...],
    start_slot: int,
    temperature0: np.ndarray,
    previous_share: float,
    heat_w: np.ndarray,
    temperatures: np.ndarray,
    electric_wh: np.ndarray,
    share: np.ndarray,
) -> int:
    """Simulate the slots ``start_slot ... slots - 1`` of a heat plan.

    Args:
        data: Arrays of the device, see ``D_*``.
        start_slot: First simulated slot.
        temperature0: Temperatures at the start of ``start_slot``.
        previous_share: Run share of the slot before ``start_slot``.
        heat_w: Heat power per slot of the whole horizon [W].
        temperatures: Result ``(slots - start_slot + 1, n)``, temperatures at the start of each
            simulated slot and after the last one.
        electric_wh: Result ``(slots - start_slot,)``, electric energy per slot [Wh].
        share: Result ``(slots - start_slot,)``, run share of each simulated slot.

    Returns:
        0, or the 1-based slot whose heat is outside the operating table.
    """
    slots = heat_w.shape[0]
    states = temperature0.shape[0]
    for i in range(states):
        temperatures[0, i] = temperature0[i]
    status = 0
    previous = previous_share
    current = np.empty(states)
    following = np.empty(states)
    for slot in range(start_slot, slots):
        index = slot - start_slot
        for i in range(states):
            current[i] = temperatures[index, i]
        energy, supported = thermal_step(data, slot, current, heat_w[slot], previous, following)
        if not supported and status == 0:
            status = slot + 1
        for i in range(states):
            temperatures[index + 1, i] = following[i]
        electric_wh[index] = energy
        previous = heat_share(data, slot, heat_w[slot])
        share[index] = previous
    return status


@njit
def comfort_hours(
    temperatures: np.ndarray,
    start_slot: int,
    comfort_min: np.ndarray,
    comfort_max: np.ndarray,
    protection_min: np.ndarray,
    hours: np.ndarray,
    under_weight: np.ndarray,
    out: np.ndarray,
) -> None:
    """Measure the violation of the room temperature bands in Kelvin hours.

    The room temperature at the start of slot ``j`` (``j`` > ``start_slot``, up to the end of the
    horizon) is held against the bounds of slot ``j`` (those of the last slot for the final one);
    the violation counts for the hours of the slot before. The temperature at the start of the
    simulated part is given, not decided, and does not count.

    Args:
        temperatures: ``(slots - start_slot + 1, n)`` as written by ``run_thermal``.
        start_slot: First simulated slot.
        comfort_min: Lower bound per slot.
        comfort_max: Upper bound per slot.
        protection_min: Protection minimum per slot.
        hours: Hours of each slot.
        under_weight: ``(slots, 3)`` weight per tier of the shortfall and slot (``ThermalParams``).
        out: Result ``(3,)``: weighted Kelvin hours below the comfort minimum, above the comfort maximum,
            below the protection minimum.
    """
    slots = comfort_min.shape[0]
    under = 0.0
    over = 0.0
    protection = 0.0
    for index in range(1, temperatures.shape[0]):
        slot = start_slot + index
        band = slot if slot < slots else slots - 1
        room = temperatures[index, 0]
        weight = hours[start_slot + index - 1]
        if room < comfort_min[band]:
            first = min(comfort_min[band] - room, UNDER_TIER_WIDTHS_K[0])
            second = min(comfort_min[band] - room - first, UNDER_TIER_WIDTHS_K[1])
            third = comfort_min[band] - room - first - second
            under += (
                under_weight[band, 0] * first
                + under_weight[band, 1] * second
                + under_weight[band, 2] * third
            ) * weight
        if room > comfort_max[band]:
            over += (room - comfort_max[band]) * weight
        if room < protection_min[band]:
            protection += (protection_min[band] - room) * weight
    out[0] = under
    out[1] = over
    out[2] = protection


def fit_electric_limit(
    unit: ThermalParams, slot: int, heat_w: float, previous_share: float
) -> float:
    """Return the largest heat up to ``heat_w`` whose slot stays within the electric power limit.

    The electricity of the slot (the energy of the start included) does not fall with the heat,
    so the largest heat that fits is found by bisection between 0 (always fits) and ``heat_w``.
    """
    data = unit.arrays()

    def fits(heat: float) -> bool:
        energy, _ = thermal_electric_wh(data, slot, heat, previous_share)
        return bool(electric_within_limit(data, slot, heat, energy))

    if fits(heat_w):
        return heat_w
    low, high = 0.0, heat_w
    for _ in range(60):
        middle = 0.5 * (low + high)
        if fits(middle):
            low = middle
        else:
            high = middle
    return low


def clip_heat_plan(unit: ThermalParams, heat_w: np.ndarray) -> np.ndarray:
    """Return a heat plan cut back to what the device can do.

    The heat of every slot lies in 0 ... highest heat of the slot (0 where the heat pump must not
    run), and the electricity of a running slot, the energy of its start included, stays within
    the electric power limit; slots are cut in time order since the start energy depends on the
    run share before. Heat that is already allowed stays as it is.
    """
    result = np.clip(np.asarray(heat_w, dtype=float), 0.0, unit.heat_limit_w)
    result[result <= RUN_TOLERANCE_W] = 0.0
    data = unit.arrays()
    previous = 1.0 if unit.initial_running else 0.0
    for slot in range(unit.slots):
        if result[slot] > 0.0:
            result[slot] = fit_electric_limit(unit, slot, float(result[slot]), previous)
            if result[slot] <= RUN_TOLERANCE_W:
                result[slot] = 0.0
        previous = float(heat_share(data, slot, float(result[slot])))
    return result
