"""Terminal value of the energy left in the battery at the end of the horizon.

The optimizer stops at the horizon, but the energy still stored in the battery
keeps its worth: it replaces grid imports that would otherwise be paid for
afterwards. Crediting that worth with a single price per kWh - the historical
``preis_euro_pro_wh_akku`` - cannot describe it, because the value of stored
energy is **not linear in the amount stored**:

- The first kWh replaces the most expensive hour after the horizon.
- The next one replaces the second most expensive hour, and so on.
- Once every hour that PV cannot cover is served, further energy replaces
  nothing; it is worth an export at best, and nothing at worst.

The resulting value function is monotone and concave. A scalar has to pick one
slope: high enough for the first kWh means hoarding a full battery, low enough
for the last kWh means running it empty by midnight. This module builds the
curve instead.

There is no forecast beyond the horizon, so the trailing window of the horizon
itself stands in for the day that follows: same season, same household rhythm,
same tariff structure. That approximation is the reason the curve is a planning
aid, not a prediction - which is also why the marginal values are deliberately
conservative wherever a choice exists.
"""

from typing import Optional

import numpy as np
from loguru import logger

from akkudoktoreos.core.pydantic import PydanticBaseModel
from pydantic import Field


class TerminalValueCurve(PydanticBaseModel):
    """Piecewise linear, concave value of battery energy left at the horizon.

    ``energy_wh`` and ``value_euro`` are the breakpoints of the cumulative
    value, ``marginal_euro_per_kwh`` the slope of each segment. Both arrays
    start at the origin; the curve is flat beyond its last breakpoint.
    """

    energy_wh: list[float] = Field(
        default_factory=list,
        json_schema_extra={
            "description": "Breakpoints of usable AC energy left in the battery [Wh]."
        },
    )
    value_euro: list[float] = Field(
        default_factory=list,
        json_schema_extra={"description": "Cumulative credit at each breakpoint [EUR]."},
    )
    marginal_euro_per_kwh: list[float] = Field(
        default_factory=list,
        json_schema_extra={
            "description": (
                "Marginal value of the segment that starts at each breakpoint "
                "[EUR/kWh]. Monotonically decreasing."
            )
        },
    )
    window_slots: int = Field(
        default=0,
        json_schema_extra={
            "description": (
                "Number of trailing horizon slots the curve was derived from. "
                "Fewer slots than a full day mean a shorter proxy period."
            )
        },
    )

    def value(self, energy_wh: float) -> float:
        """Return the credit for ``energy_wh`` of usable AC energy [EUR].

        Args:
            energy_wh: Usable AC energy left in the battery.

        Returns:
            Interpolated value of the curve; 0.0 for an empty curve.
        """
        if not self.energy_wh or energy_wh <= 0.0:
            return 0.0
        return float(np.interp(energy_wh, self.energy_wh, self.value_euro))


class TerminalValueResult(PydanticBaseModel):
    """What the optimizer credited for the energy left in the battery."""

    mode: str = Field(
        json_schema_extra={
            "description": "Terminal value mode the run used: AUTO or FIXED.",
            "examples": ["AUTO", "FIXED"],
        }
    )
    battery_energy_wh: float = Field(
        default=0.0,
        json_schema_extra={
            "description": "Usable AC energy left in the battery at the end of the horizon [Wh]."
        },
    )
    credited_euro: float = Field(
        default=0.0,
        json_schema_extra={"description": "Credit applied to the total balance [EUR]."},
    )
    curve: Optional[TerminalValueCurve] = Field(
        default=None,
        json_schema_extra={
            "description": "The value curve the credit was read from; None in FIXED mode."
        },
    )
    reason: str = Field(
        default="",
        json_schema_extra={
            "description": (
                "Why this mode applied. Empty in AUTO mode; in FIXED mode it "
                "says whether FIXED was configured or whether AUTO fell back "
                "because no curve could be derived."
            ),
            "examples": ["", "terminal_value_mode is FIXED"],
        },
    )


def build_terminal_value_curve(
    *,
    prices_euro_per_wh: np.ndarray,
    load_wh: np.ndarray,
    pv_wh: np.ndarray,
    feed_in_euro_per_wh: np.ndarray,
    max_energy_wh: float,
    lcos_euro_per_kwh: float = 0.0,
    dc_to_ac_efficiency: float = 1.0,
    grid_export_allowed: bool = False,
) -> TerminalValueCurve:
    """Build the terminal value curve from the trailing horizon window.

    Every slot of the window contributes its residual load - the part of the
    load that PV does not cover - at its import price. Sorting those slots by
    price and accumulating them yields the marginal value of the first, second,
    ... kWh in the battery. Energy beyond the residual load can only be
    exported, and only when direct marketing allows it.

    Args:
        prices_euro_per_wh: Import prices of the window [EUR/Wh].
        load_wh: Load per slot of the window [Wh].
        pv_wh: PV generation per slot of the window [Wh].
        feed_in_euro_per_wh: Feed-in tariff of the window [EUR/Wh].
        max_energy_wh: Usable AC energy of a full battery [Wh]; the curve ends here.
        lcos_euro_per_kwh: Levelized cost of storage, already charged per
            delivered DC energy in the simulation and therefore subtracted here
            so stored energy is not credited twice.
        dc_to_ac_efficiency: Inverter efficiency, used to convert the LCOS from
            delivered DC energy to the AC energy of the curve.
        grid_export_allowed: Whether the battery may feed the grid (direct
            marketing). Without it, energy beyond the residual load gets no
            credit: it can neither be exported nor is its use covered by the
            proxy window.

    Returns:
        The curve; empty when the window carries no usable information.
    """
    window = min(len(prices_euro_per_wh), len(load_wh), len(pv_wh))
    if window <= 0 or max_energy_wh <= 0.0:
        return TerminalValueCurve()

    residual = np.maximum(load_wh[:window] - pv_wh[:window], 0.0)
    prices = np.asarray(prices_euro_per_wh[:window], dtype=float)

    # LCOS is charged on delivered DC energy; the curve is in AC energy.
    lcos_per_wh_ac = (lcos_euro_per_kwh / 1000.0) / max(dc_to_ac_efficiency, 1e-9)

    order = np.argsort(-prices)
    energy_points: list[float] = [0.0]
    value_points: list[float] = [0.0]
    marginals: list[float] = []

    cumulative_energy = 0.0
    cumulative_value = 0.0
    for index in order:
        slot_energy = float(residual[index])
        if slot_energy <= 0.0:
            continue
        # Negative or very cheap hours are not worth storing energy for.
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
        marginals.append(marginal * 1000.0)

    # Everything beyond the residual load can only be sold. A median feed-in
    # tariff rather than the best one: exporting all of it in the single best
    # slot is not something the horizon can promise.
    if grid_export_allowed and cumulative_energy < max_energy_wh:
        positive_feed_in = [
            float(value) for value in feed_in_euro_per_wh[:window] if float(value) > 0.0
        ]
        export_marginal = max(
            (float(np.median(positive_feed_in)) if positive_feed_in else 0.0) - lcos_per_wh_ac,
            0.0,
        )
        if export_marginal > 0.0:
            remaining = max_energy_wh - cumulative_energy
            cumulative_energy += remaining
            cumulative_value += remaining * export_marginal
            energy_points.append(cumulative_energy)
            value_points.append(cumulative_value)
            marginals.append(export_marginal * 1000.0)

    if len(energy_points) <= 1:
        logger.debug("Terminal value curve is empty - no priced residual load in the window.")
        return TerminalValueCurve(window_slots=window)

    # The segment slopes are decreasing by construction (prices were sorted),
    # so the curve is concave; the export tail is the flattest segment.
    return TerminalValueCurve(
        energy_wh=energy_points,
        value_euro=value_points,
        marginal_euro_per_kwh=marginals,
        window_slots=window,
    )


def trailing_window(
    values: Optional[np.ndarray],
    end_slot: int,
    window_slots: int,
) -> np.ndarray:
    """Return the ``window_slots`` values in front of ``end_slot``.

    Args:
        values: Full slot array, or None.
        end_slot: Exclusive end of the window (end of the optimization horizon).
        window_slots: Desired window length; a shorter horizon yields less.

    Returns:
        The window as a float array, empty when no data is available.
    """
    if values is None:
        return np.zeros(0, dtype=float)
    end = min(int(end_slot), len(values))
    start = max(end - int(window_slots), 0)
    if end <= start:
        return np.zeros(0, dtype=float)
    return np.asarray(values[start:end], dtype=float)
