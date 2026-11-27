"""Terminal value of the energy left in the house batteries at the end of the horizon.

The plan stops at the horizon, but the energy still stored keeps its worth: it replaces grid
imports that would otherwise be paid for afterwards. A single price per kWh cannot describe
it, because the value of stored energy is not linear in the amount stored: the first kWh
replaces the most expensive hour after the horizon, the next one the second most expensive,
and once every hour that PV cannot cover is served, further energy is worth an export at
best. This module builds that concave curve from the trailing window of the horizon (the
stand-in for the day that follows). ``optimization.tailvalue`` refines it with a lookahead.

Several house batteries are valued as one combined battery (``combine_batteries``): the sum of
capacities and powers, efficiencies weighted by capacity. That is an approximation; with one
battery nothing is combined and the value is exact.
"""

from dataclasses import dataclass, field, replace
from typing import Any, Optional

import numpy as np

from akkudoktoreos.devices.models.inverter import InverterParams
from akkudoktoreos.devices.models.jit import njit
from akkudoktoreos.devices.models.storage import StorageParams


@dataclass(frozen=True)
class ValueCurve:
    """Value of usable AC battery energy at the end of the horizon, piecewise linear.

    Attributes:
        energy_wh: Breakpoints of usable AC energy [Wh], ascending.
        value_eur: Credit at each breakpoint [EUR].
        operating_eur: Tail operating cash flow at each breakpoint (lookahead curve only).
        continuation_eur: Continuation credit at each breakpoint (lookahead curve only).
        clamp_at_zero: Credit 0 for energy <= 0 (the concave curve); the lookahead curve
            interpolates with end values instead, because an empty battery has a value.
        residual_energy_wh: Energy up to which the curve is backed by residual load.
        trace: What the lookahead recursion left behind to walk the tail path forwards
            (``optimization.tailvalue.TailTrace``); not part of the comparison.
    """

    energy_wh: np.ndarray = field(default_factory=lambda: np.zeros(0))
    value_eur: np.ndarray = field(default_factory=lambda: np.zeros(0))
    operating_eur: np.ndarray = field(default_factory=lambda: np.zeros(0))
    continuation_eur: np.ndarray = field(default_factory=lambda: np.zeros(0))
    clamp_at_zero: bool = True
    residual_energy_wh: float = 0.0
    trace: Any = field(default=None, compare=False, repr=False)

    @property
    def empty(self) -> bool:
        """Whether the curve has no breakpoints."""
        return len(self.energy_wh) == 0

    def value(self, energy_wh: float) -> float:
        """Return the credit for ``energy_wh`` of usable AC energy [EUR]."""
        if self.empty:
            return 0.0
        return float(interpolate(self.energy_wh, self.value_eur, energy_wh, self.clamp_at_zero))


@njit
def interpolate(xp: np.ndarray, fp: np.ndarray, x: float, clamp_at_zero: bool) -> float:
    """Interpolate linearly like ``numpy.interp``; ``clamp_at_zero`` gives 0 for ``x <= 0``."""
    count = xp.shape[0]
    if count == 0 or (clamp_at_zero and x <= 0.0):
        return 0.0
    if x <= xp[0]:
        return fp[0]
    if x >= xp[count - 1]:
        return fp[count - 1]
    low = 0
    high = count - 1
    while high - low > 1:
        middle = (low + high) // 2
        if xp[middle] <= x:
            low = middle
        else:
            high = middle
    if xp[low] == x:
        return fp[low]
    slope = (fp[low + 1] - fp[low]) / (xp[low + 1] - xp[low])
    return slope * (x - xp[low]) + fp[low]


def trailing_window(values: Optional[np.ndarray], end_slot: int, window_slots: int) -> np.ndarray:
    """Return the ``window_slots`` values in front of ``end_slot`` (less if the data is short)."""
    if values is None:
        return np.zeros(0, dtype=float)
    end = min(int(end_slot), len(values))
    start = max(end - int(window_slots), 0)
    if end <= start:
        return np.zeros(0, dtype=float)
    return np.asarray(values[start:end], dtype=float)


def build_continuation_curve(
    *,
    prices_per_wh: np.ndarray,
    load_wh: np.ndarray,
    pv_wh: np.ndarray,
    feed_in_per_wh: np.ndarray,
    max_energy_wh: float,
    lcos_per_kwh: float = 0.0,
    dc_to_ac_efficiency: float = 1.0,
    grid_export_allowed: bool = False,
) -> ValueCurve:
    """Build the concave terminal value curve from the trailing window.

    Every slot of the window contributes its residual load (load that PV does not cover) at
    its import price. Sorted by price and accumulated, this gives the marginal value of the
    first, second, ... kWh in the battery. Energy beyond the residual load can only be
    exported, and only with direct marketing; it is valued at the median positive tariff.

    Args:
        prices_per_wh: Import prices of the window [EUR/Wh].
        load_wh: Load per slot of the window [Wh].
        pv_wh: PV generation per slot of the window [Wh].
        feed_in_per_wh: Feed-in tariff of the window [EUR/Wh].
        max_energy_wh: Usable AC energy of a full battery [Wh]; the curve ends here.
        lcos_per_kwh: Levelized cost of storage, already charged in the simulation and
            therefore subtracted so stored energy is not credited twice.
        dc_to_ac_efficiency: Converts the storage cost from DC to AC energy.
        grid_export_allowed: Whether the battery may feed the grid (direct marketing).

    Returns:
        The curve; empty when the window carries no usable information.
    """
    window = min(len(prices_per_wh), len(load_wh), len(pv_wh))
    if window <= 0 or max_energy_wh <= 0.0:
        return ValueCurve()
    residual = np.maximum(load_wh[:window] - pv_wh[:window], 0.0)
    prices = np.asarray(prices_per_wh[:window], dtype=float)
    lcos_per_wh_ac = (lcos_per_kwh / 1000.0) / max(dc_to_ac_efficiency, 1e-9)
    # Stable: slots with the same price keep their order, which decides where the curve is
    # cut at the battery capacity.
    order = np.argsort(-prices, kind="stable")
    energy_points = [0.0]
    value_points = [0.0]
    cumulative_energy = 0.0
    cumulative_value = 0.0
    for index in order:
        slot_energy = float(residual[index])
        if slot_energy <= 0.0:
            continue
        marginal = max(float(prices[index]) - lcos_per_wh_ac, 0.0)
        if marginal <= 0.0:
            continue
        slot_energy = min(slot_energy, max_energy_wh - cumulative_energy)
        if slot_energy <= 0.0:
            break
        cumulative_energy += slot_energy
        cumulative_value += slot_energy * marginal
        energy_points.append(cumulative_energy)
        value_points.append(cumulative_value)
    residual_energy_wh = cumulative_energy
    if grid_export_allowed and cumulative_energy < max_energy_wh:
        positive = [float(value) for value in feed_in_per_wh[:window] if float(value) > 0.0]
        export_marginal = max(
            (float(np.median(positive)) if positive else 0.0) - lcos_per_wh_ac, 0.0
        )
        if export_marginal > 0.0:
            remaining = max_energy_wh - cumulative_energy
            cumulative_energy += remaining
            cumulative_value += remaining * export_marginal
            energy_points.append(cumulative_energy)
            value_points.append(cumulative_value)
    if len(energy_points) <= 1:
        return ValueCurve()
    return ValueCurve(
        energy_wh=np.array(energy_points),
        value_eur=np.array(value_points),
        residual_energy_wh=residual_energy_wh,
    )


def combine_batteries(
    batteries: tuple[StorageParams, ...], inverters: tuple[InverterParams, ...]
) -> tuple[StorageParams, InverterParams]:
    """Combine house batteries and their inverters into one battery and one inverter.

    One battery is returned unchanged. Several are summed: capacities, powers and SoC limits
    in energy; efficiencies and storage cost are weighted by capacity (inverter efficiencies
    by power). Efficiency curves, SoC power limits and charge levels are kept only if all
    batteries agree on them, SoC power limits summed; otherwise constants are used.

    Raises:
        ValueError: Without a battery.
    """
    if not batteries:
        raise ValueError("No battery to combine.")
    by_battery = {i.battery_index: i for i in inverters}
    if len(batteries) == 1:
        return batteries[0], by_battery[0]
    capacity = sum(b.capacity_wh for b in batteries)
    weights = [
        b.capacity_wh / capacity if capacity > 0 else 1.0 / len(batteries) for b in batteries
    ]

    def weighted(values: list[float]) -> float:
        return float(sum(w * v for w, v in zip(weights, values)))

    first = batteries[0]

    def same(attribute: str) -> bool:
        return all(getattr(b, attribute) == getattr(first, attribute) for b in batteries)

    def summed_table(attribute: str) -> Optional[tuple[float, ...]]:
        tables = [getattr(b, attribute) for b in batteries]
        if any(table is None for table in tables):
            return None
        return tuple(float(sum(column)) for column in zip(*tables))

    combined = replace(
        first,
        capacity_wh=capacity,
        initial_soc_pct=(
            sum(b.initial_soc_pct / 100 * b.capacity_wh for b in batteries) / capacity * 100
            if capacity > 0
            else 0.0
        ),
        min_soc_pct=(
            sum(b.min_soc_pct / 100 * b.capacity_wh for b in batteries) / capacity * 100
            if capacity > 0
            else 0.0
        ),
        max_soc_pct=(
            sum(b.max_soc_pct / 100 * b.capacity_wh for b in batteries) / capacity * 100
            if capacity > 0
            else 100.0
        ),
        charging_efficiency=weighted([b.charging_efficiency for b in batteries]),
        discharging_efficiency=weighted([b.discharging_efficiency for b in batteries]),
        max_charge_power_w=sum(b.max_charge_power_w for b in batteries),
        max_discharge_power_w=sum(b.max_discharge_power_w for b in batteries),
        lcos_per_kwh=weighted([b.lcos_per_kwh for b in batteries]),
        charge_rates=first.charge_rates if same("charge_rates") else (0.0, 1.0),
        charge_power_limit_w_by_soc=summed_table("charge_power_limit_w_by_soc"),
        discharge_power_limit_w_by_soc=summed_table("discharge_power_limit_w_by_soc"),
        charging_efficiency_curve=(
            first.charging_efficiency_curve if same("charging_efficiency_curve") else None
        ),
        discharging_efficiency_curve=(
            first.discharging_efficiency_curve if same("discharging_efficiency_curve") else None
        ),
    )
    units = [by_battery[index] for index in range(len(batteries))]
    power = sum(u.max_power_w for u in units)
    power_weights = [u.max_power_w / power if power > 0 else 1.0 / len(units) for u in units]
    ac_limits = [u.max_ac_charge_power_w for u in units]
    inverter = replace(
        units[0],
        max_power_w=power,
        battery_index=0,
        dc_to_ac_efficiency=float(
            sum(w * u.dc_to_ac_efficiency for w, u in zip(power_weights, units))
        ),
        ac_to_dc_efficiency=float(
            sum(w * u.ac_to_dc_efficiency for w, u in zip(power_weights, units))
        ),
        dc_to_ac_efficiency_curve=(
            units[0].dc_to_ac_efficiency_curve
            if all(u.dc_to_ac_efficiency_curve == units[0].dc_to_ac_efficiency_curve for u in units)
            else None
        ),
        ac_to_dc_efficiency_curve=(
            units[0].ac_to_dc_efficiency_curve
            if all(u.ac_to_dc_efficiency_curve == units[0].ac_to_dc_efficiency_curve for u in units)
            else None
        ),
        max_ac_charge_power_w=(
            None if any(limit is None for limit in ac_limits) else float(sum(ac_limits))  # type: ignore[arg-type]
        ),
        ac_charge_limits_total_charge=any(u.ac_charge_limits_total_charge for u in units),
    )
    return combined, inverter
