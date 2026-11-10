"""Runtime form of a battery profile and the algorithms that build it.

A battery profile document (``BatteryProfileSettings``) describes limits as sparse,
non-uniform maps and curves. This module turns such a document into the
ready-to-use, immutable ``BatteryProfile`` object queried by controllers and the
simulation:

- ``compile_battery_profile`` resamples both limit maps onto dense, uniformly
  spaced grids and precomputes the efficiency curves, so each runtime query is a
  single array index lookup.
- The ``reference_*`` functions are the slow per-query interpolation the
  precomputation is built from. They define the semantics of a profile document.

The module has no pydantic models and does not read files or configuration. The
limit map interpolation enum lives here because the algorithms interpret it; the
efficiency curves use the shared curve support of ``profilecurve``. The
document module re-exports the enums.
"""

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, List, Optional

import numpy as np

from akkudoktoreos.devices.profile.profilecurve import (
    CurveInterpolation,
    OutOfRangeBehavior,
    PrecomputedCurve,
    reference_curve_lookup,
)

# Name of the curve interpolation in the battery profile documents.
EfficiencyInterpolation = CurveInterpolation

if TYPE_CHECKING:
    from akkudoktoreos.devices.profile.batteryprofile import (
        BatteryProfileSettings,
        EfficiencyCurve,
    )


class Interpolation(str, Enum):
    """Strategy used to interpolate between grid points of a limit map.

    Attributes:
        BILINEAR: Interpolate linearly along both axes.
        NEAREST: Snap to the nearest grid point.
        NONE: Only exact grid points yield a value; anything else yields
            None.
    """

    BILINEAR = "bilinear"
    NEAREST = "nearest"
    NONE = "none"


# ---------------------------------------------------------------------------
# Precomputed runtime objects
# ---------------------------------------------------------------------------


class PrecomputedEfficiencyCurve(PrecomputedCurve):
    """Immutable runtime representation of a C-rate efficiency curve.

    A ``PrecomputedCurve`` over the absolute C-rate. The C-rate axis remains
    independent of installed capacity. ``c_rate`` and ``efficiency`` name the
    breakpoints and values.
    """

    @property
    def c_rate(self) -> tuple[float, ...]:
        """C-rate breakpoints."""
        return self.x

    @property
    def efficiency(self) -> tuple[float, ...]:
        """Efficiency at each C-rate breakpoint."""
        return self.y

    @classmethod
    def from_curve(cls, curve: "EfficiencyCurve") -> "PrecomputedEfficiencyCurve":
        """Build immutable lookup data from a validated configuration curve."""
        return cls.from_points(
            curve.c_rate, curve.efficiency, curve.interpolation, curve.out_of_range_behavior
        )


@dataclass(frozen=True)
class PrecomputedChargeDischargeLimitMap:
    """Dense, uniformly spaced lookup table for one direction.

    Instances are produced by ``precompute_limit_maps``. They resample the
    sparse, non-uniform limit map onto a regular grid once, so that each
    runtime query becomes a single array index lookup instead of a full
    bilinear interpolation.

    Attributes:
        grid: Two-dimensional array indexed as grid[temp_index, soc_index].
        t_min: Lowest temperature covered by the grid, in degrees Celsius.
        t_step: Temperature spacing between rows, in degrees Celsius.
        s_min: Lowest SOC covered by the grid, in percent.
        s_step: SOC spacing between columns, in percent.
        n_temp: Number of temperature rows.
        n_soc: Number of SOC columns.
        fill: Value returned for queries outside the grid bounds. None means
            such queries yield None; a float means they yield that value
            (used for clamp-to-edge semantics).
        allowed_min_degC: Lowest temperature the direction is allowed at
            (hard cutoff, inclusive), None for no cutoff. Checked on the exact
            query temperature, before rounding to a grid cell.
        allowed_max_degC: Highest temperature the direction is allowed at
            (hard cutoff, inclusive), None for no cutoff.
    """

    grid: np.ndarray
    t_min: float
    t_step: float
    s_min: float
    s_step: float
    n_temp: int
    n_soc: int
    fill: Optional[float] = None
    allowed_min_degC: Optional[float] = None
    allowed_max_degC: Optional[float] = None

    def _allowed(self, temperature_degC: float) -> bool:
        """Return whether the hard cutoffs allow the direction at the exact temperature."""
        if self.allowed_min_degC is not None and temperature_degC < self.allowed_min_degC:
            return False
        if self.allowed_max_degC is not None and temperature_degC > self.allowed_max_degC:
            return False
        return True

    def lookup(self, temperature_degC: float, soc_percent: float) -> Optional[float]:
        """Return the precomputed limit at the given operating point.

        A temperature outside the hard cutoffs returns None; the cutoffs are
        checked on the exact temperature. Otherwise the query is rounded to the
        nearest grid cell. Queries whose coordinates lie outside the grid's
        coordinate range return fill (or None if fill is None). The bounds check
        is performed on the raw coordinates before rounding, so that a query
        just below t_min is not accidentally rounded up into the first cell.

        Args:
            temperature_degC: Cell temperature in degrees Celsius.
            soc_percent: State of charge in percent.

        Returns:
            The stored limit, None if the hard cutoffs inhibit the direction,
            or fill if the query is outside the grid's coordinate range.
        """
        if not self._allowed(temperature_degC):
            return None
        t_max = self.t_min + (self.n_temp - 1) * self.t_step
        s_max = self.s_min + (self.n_soc - 1) * self.s_step
        if (
            temperature_degC < self.t_min
            or temperature_degC > t_max
            or soc_percent < self.s_min
            or soc_percent > s_max
        ):
            return self.fill
        ti = int(round((temperature_degC - self.t_min) / self.t_step))
        si = int(round((soc_percent - self.s_min) / self.s_step))
        value = float(self.grid[ti, si])
        if np.isnan(value):
            return self.fill
        return value

    def lookup_many(
        self,
        temperatures: np.ndarray,
        socs: np.ndarray,
    ) -> np.ndarray:
        """Vectorized lookup of many operating points at once.

        Args:
            temperatures: Array of temperatures in degrees Celsius.
            socs: Array of SOC values in percent, same shape as
                temperatures.

        Returns:
            Array of limits with the same shape as the inputs. Entries outside
            the hard cutoffs are NaN; other out-of-bounds entries are set to
            fill, or to NaN if fill is None.
        """
        temperatures = np.asarray(temperatures, dtype=float)
        socs = np.asarray(socs, dtype=float)
        t_max = self.t_min + (self.n_temp - 1) * self.t_step
        s_max = self.s_min + (self.n_soc - 1) * self.s_step
        in_bounds = (
            (temperatures >= self.t_min)
            & (temperatures <= t_max)
            & (socs >= self.s_min)
            & (socs <= s_max)
        )
        ti = np.clip(
            np.rint((temperatures - self.t_min) / self.t_step).astype(np.int64),
            0,
            self.n_temp - 1,
        )
        si = np.clip(
            np.rint((socs - self.s_min) / self.s_step).astype(np.int64),
            0,
            self.n_soc - 1,
        )
        out = np.full(temperatures.shape, np.nan, dtype=float)
        out[in_bounds] = self.grid[ti[in_bounds], si[in_bounds]]
        if self.fill is not None:
            out[~in_bounds] = self.fill
        if self.allowed_min_degC is not None:
            out[temperatures < self.allowed_min_degC] = np.nan
        if self.allowed_max_degC is not None:
            out[temperatures > self.allowed_max_degC] = np.nan
        return out


@dataclass(frozen=True)
class BatteryProfile:
    """Ready-to-use charge/discharge limits for a battery.

    Instances are produced by ``compile_battery_profile``, usually through the
    profile store (``get_profile_store().get(BatteryProfileSettings, name)``). The metadata
    fields identify the battery; precomputed limit maps and efficiency curves
    answer queries at runtime.

    Attributes:
        battery_model: Model name of the battery pack.
        chemistry: Cell chemistry, if known.
        nominal_voltage_v: Nominal voltage, if known.
        max_charge_power_w: Optional absolute model charge-power limit.
        max_discharge_power_w: Optional absolute model discharge-power limit.
        technical_min_soc_percent: Manufacturer-defined lower SOC limit.
        technical_max_soc_percent: Manufacturer-defined upper SOC limit.
        charge_efficiency: Charge-efficiency curve.
        discharge_efficiency: Discharge-efficiency curve.
        limit_unit: Unit of the values returned by the limit accessors.
        charge_map: Precomputed charge limit map.
        discharge_map: Precomputed discharge limit map.
        nominal_capacity_wh: Nominal capacity of the battery model [Wh], None for
            a generic profile.
        full_charge_interval_days: Maximum number of days between two full
            charges (SoC recalibration), None if not needed or unknown.
        full_charge_detect_percentage: Reported SOC from which the battery
            counts as fully charged [%].
    """

    battery_model: str
    chemistry: Optional[str]
    nominal_voltage_v: Optional[float]
    max_charge_power_w: Optional[float]
    max_discharge_power_w: Optional[float]
    technical_min_soc_percent: float
    technical_max_soc_percent: float
    charge_efficiency: PrecomputedEfficiencyCurve
    discharge_efficiency: PrecomputedEfficiencyCurve
    limit_unit: str
    charge_map: PrecomputedChargeDischargeLimitMap
    discharge_map: PrecomputedChargeDischargeLimitMap
    nominal_capacity_wh: Optional[float] = None
    full_charge_interval_days: Optional[float] = None
    full_charge_detect_percentage: int = 99

    def max_charge_crate(self, temperature_degC: float, soc_percent: float) -> Optional[float]:
        """Return the maximum charge limit at the given operating point.

        Args:
            temperature_degC: Cell temperature in degrees Celsius.
            soc_percent: State of charge in percent.

        Returns:
            The precomputed charge limit, or None if charging must be
            inhibited at this operating point.
        """
        return self.charge_map.lookup(temperature_degC, soc_percent)

    def max_discharge_crate(self, temperature_degC: float, soc_percent: float) -> Optional[float]:
        """Return the maximum discharge limit at the given operating point.

        Args:
            temperature_degC: Cell temperature in degrees Celsius.
            soc_percent: State of charge in percent.

        Returns:
            The precomputed discharge limit, or None if discharging must be
            inhibited at this operating point.
        """
        return self.discharge_map.lookup(temperature_degC, soc_percent)

    def charge_efficiency_at(self, c_rate: float) -> Optional[float]:
        """Return charge efficiency at the given absolute C-rate.

        None if the C-rate is outside the curve and the curve rejects it, or the
        curve does not interpolate and the C-rate is no breakpoint.
        """
        return self.charge_efficiency.lookup(abs(c_rate))

    def discharge_efficiency_at(self, c_rate: float) -> Optional[float]:
        """Return discharge efficiency at the given absolute C-rate.

        None in the same cases as ``charge_efficiency_at``.
        """
        return self.discharge_efficiency.lookup(abs(c_rate))


# ---------------------------------------------------------------------------
# Reference implementation (slow, per query)
# ---------------------------------------------------------------------------


def reference_efficiency_lookup(curve: "EfficiencyCurve", c_rate: float) -> Optional[float]:
    """Return the interpolated efficiency of a curve document at an absolute C-rate.

    Reference semantics for ``PrecomputedEfficiencyCurve.lookup``, see
    ``profilecurve.reference_curve_lookup``.

    Args:
        curve: Validated efficiency curve document.
        c_rate: Absolute C-rate.

    Returns:
        The efficiency, or None when the query is outside the curve and the
        out-of-range behavior is REJECT, or when interpolation is NONE and the
        query is not an exact breakpoint.
    """
    return reference_curve_lookup(
        curve.c_rate, curve.efficiency, curve.interpolation, curve.out_of_range_behavior, c_rate
    )


def _bracket(axis: List[float], x: float) -> tuple[int, int, float]:
    """Return (i_low, i_high, fraction) for x along a strictly increasing axis."""
    if x <= axis[0]:
        return 0, 0, 0.0
    if x >= axis[-1]:
        return len(axis) - 1, len(axis) - 1, 0.0
    for i in range(len(axis) - 1):
        if axis[i] <= x <= axis[i + 1]:
            frac = (x - axis[i]) / (axis[i + 1] - axis[i])
            return i, i + 1, frac
    return len(axis) - 1, len(axis) - 1, 0.0


def reference_limit_lookup(
    document: "BatteryProfileSettings",
    grid: List[List[float]],
    temperature_degC: float,
    soc_percent: float,
) -> Optional[float]:
    """Interpolate a value from a limit grid of a profile document.

    Applies the document's out_of_range_behavior and honors the union of the
    charge and discharge hard cutoffs. Callers that need a direction-specific
    cutoff use ``reference_max_charge_crate`` or ``reference_max_discharge_crate``.

    Args:
        document: Validated battery profile document.
        grid: Two-dimensional grid of limit values, indexed by temperature
            along rows and SOC along columns.
        temperature_degC: Query temperature in degrees Celsius.
        soc_percent: Query state of charge in percent.

    Returns:
        The interpolated limit, or None if the query is outside the union of
        the hard cutoffs, or outside the axis range when out_of_range_behavior
        is REJECT, or not on an exact grid point when interpolation is NONE.
    """
    temps = document.axes.temperature_degC
    socs = document.axes.soc_percent
    cutoffs = document.hard_cutoffs

    t_lo = min(cutoffs.charge_allowed_below_degC, cutoffs.discharge_allowed_below_degC)
    t_hi = max(cutoffs.charge_allowed_above_degC, cutoffs.discharge_allowed_above_degC)
    if temperature_degC < t_lo or temperature_degC > t_hi:
        return None

    if document.out_of_range_behavior == OutOfRangeBehavior.REJECT:
        if not (temps[0] <= temperature_degC <= temps[-1]):
            return None
        if not (socs[0] <= soc_percent <= socs[-1]):
            return None

    if document.interpolation == Interpolation.NEAREST:
        ti = min(range(len(temps)), key=lambda i: abs(temps[i] - temperature_degC))
        si = min(range(len(socs)), key=lambda i: abs(socs[i] - soc_percent))
        return grid[ti][si]

    if document.interpolation == Interpolation.NONE:
        if temperature_degC in temps and soc_percent in socs:
            return grid[temps.index(temperature_degC)][socs.index(soc_percent)]
        return None

    t0, t1, tf = _bracket(temps, temperature_degC)
    s0, s1, sf = _bracket(socs, soc_percent)
    v00, v01 = grid[t0][s0], grid[t0][s1]
    v10, v11 = grid[t1][s0], grid[t1][s1]
    v0 = v00 + (v01 - v00) * sf
    v1 = v10 + (v11 - v10) * sf
    return v0 + (v1 - v0) * tf


def reference_max_charge_crate(
    document: "BatteryProfileSettings", temperature_degC: float, soc_percent: float
) -> Optional[float]:
    """Return the maximum charge limit of a profile document by per-query interpolation.

    Args:
        document: Validated battery profile document.
        temperature_degC: Cell temperature in degrees Celsius.
        soc_percent: State of charge in percent.

    Returns:
        The interpolated charge limit, or None if charging must be inhibited at
        this operating point.
    """
    c = document.hard_cutoffs
    if not (c.charge_allowed_below_degC <= temperature_degC <= c.charge_allowed_above_degC):
        return None
    return reference_limit_lookup(
        document, document.charge_limit_crate.values, temperature_degC, soc_percent
    )


def reference_max_discharge_crate(
    document: "BatteryProfileSettings", temperature_degC: float, soc_percent: float
) -> Optional[float]:
    """Return the maximum discharge limit of a profile document by per-query interpolation.

    Args:
        document: Validated battery profile document.
        temperature_degC: Cell temperature in degrees Celsius.
        soc_percent: State of charge in percent.

    Returns:
        The interpolated discharge limit, or None if discharging must be
        inhibited at this operating point.
    """
    c = document.hard_cutoffs
    if not (c.discharge_allowed_below_degC <= temperature_degC <= c.discharge_allowed_above_degC):
        return None
    return reference_limit_lookup(
        document, document.discharge_limit_crate.values, temperature_degC, soc_percent
    )


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


def precompute_limit_maps(
    document: "BatteryProfileSettings",
    t_step: float = 1.0,
    s_step: float = 1.0,
    apply_cutoffs: bool = True,
) -> tuple[PrecomputedChargeDischargeLimitMap, PrecomputedChargeDischargeLimitMap]:
    """Resample both limit maps of a profile document onto dense, uniform grids.

    The expensive interpolation is performed once here, using the document's
    interpolation and out_of_range_behavior. The returned objects answer each
    runtime query with a single array index lookup.

    If apply_cutoffs is true, the precomputed grids extend beyond the axis
    range far enough to cover the hard cutoffs, and the maps keep the exact
    cutoff temperatures: a lookup outside the respective cutoff returns None,
    decided on the exact temperature, not on the rounded grid cell. Cells
    between the axis range and the cutoff are filled according to
    out_of_range_behavior, i.e. with the clamped edge value under
    CLAMP_TO_NEAREST or with NaN under REJECT.

    If apply_cutoffs is false, the precomputed grids cover exactly the axis
    range and the caller is responsible for checking the cutoffs separately.

    Args:
        document: Validated battery profile document.
        t_step: Spacing of the precomputed temperature axis, in degrees
            Celsius. Defaults to 1.0.
        s_step: Spacing of the precomputed SOC axis, in percent.
            Defaults to 1.0.
        apply_cutoffs: Whether to bake the hard cutoffs into the precomputed
            grids. Defaults to True.

    Returns:
        A pair (charge_map, discharge_map) of precomputed lookup objects.

    Raises:
        ValueError: If t_step or s_step is not positive, or if the axis span
            is smaller than the step size.
    """
    if t_step <= 0 or s_step <= 0:
        raise ValueError("t_step and s_step must be positive")

    temps = np.asarray(document.axes.temperature_degC, dtype=float)
    socs = np.asarray(document.axes.soc_percent, dtype=float)
    cutoffs = document.hard_cutoffs

    if apply_cutoffs:
        t_lo = min(
            temps[0],
            cutoffs.charge_allowed_below_degC,
            cutoffs.discharge_allowed_below_degC,
        )
        t_hi = max(
            temps[-1],
            cutoffs.charge_allowed_above_degC,
            cutoffs.discharge_allowed_above_degC,
        )
    else:
        t_lo, t_hi = float(temps[0]), float(temps[-1])

    t_grid = np.arange(t_lo, t_hi + t_step * 0.5, t_step)
    s_grid = np.arange(socs[0], socs[-1] + s_step * 0.5, s_step)
    if t_grid.size < 1 or s_grid.size < 1:
        raise ValueError("axis span smaller than step size")

    def resample(
        values: List[List[float]],
        cutoff_lo: Optional[float],
        cutoff_hi: Optional[float],
    ) -> PrecomputedChargeDischargeLimitMap:
        out = np.empty((t_grid.size, s_grid.size), dtype=float)
        for i, t in enumerate(t_grid):
            # The cutoffs are not baked into the cells: a cell stands for half a
            # step around its temperature, so the map checks them exactly.
            for j, s in enumerate(s_grid):
                v = reference_limit_lookup(document, values, float(t), float(s))
                out[i, j] = np.nan if v is None else float(v)
        # Compiled profiles are cached and shared, so the grid must not change.
        out.setflags(write=False)
        return PrecomputedChargeDischargeLimitMap(
            grid=out,
            t_min=float(t_grid[0]),
            t_step=float(t_step),
            s_min=float(s_grid[0]),
            s_step=float(s_step),
            n_temp=int(t_grid.size),
            n_soc=int(s_grid.size),
            fill=None,
            allowed_min_degC=cutoff_lo,
            allowed_max_degC=cutoff_hi,
        )

    charge_map = resample(
        document.charge_limit_crate.values,
        cutoffs.charge_allowed_below_degC if apply_cutoffs else None,
        cutoffs.charge_allowed_above_degC if apply_cutoffs else None,
    )
    discharge_map = resample(
        document.discharge_limit_crate.values,
        cutoffs.discharge_allowed_below_degC if apply_cutoffs else None,
        cutoffs.discharge_allowed_above_degC if apply_cutoffs else None,
    )
    return charge_map, discharge_map


def compile_battery_profile(
    document: "BatteryProfileSettings", t_step: float = 1.0, s_step: float = 1.0
) -> BatteryProfile:
    """Precompute a profile document into a ready-to-use BatteryProfile.

    Args:
        document: Validated battery profile document.
        t_step: Spacing of the precomputed temperature axis, in degrees
            Celsius. Defaults to 1.0.
        s_step: Spacing of the precomputed SOC axis, in percent.
            Defaults to 1.0.

    Returns:
        A ready-to-use BatteryProfile object.

    Raises:
        ValueError: If t_step or s_step is not positive.
    """
    charge_map, discharge_map = precompute_limit_maps(document, t_step=t_step, s_step=s_step)
    return BatteryProfile(
        battery_model=document.battery_model,
        chemistry=document.chemistry,
        nominal_voltage_v=document.nominal_voltage_v,
        max_charge_power_w=document.max_charge_power_w,
        max_discharge_power_w=document.max_discharge_power_w,
        technical_min_soc_percent=document.technical_min_soc_percent,
        technical_max_soc_percent=document.technical_max_soc_percent,
        charge_efficiency=PrecomputedEfficiencyCurve.from_curve(document.charge_efficiency),
        discharge_efficiency=PrecomputedEfficiencyCurve.from_curve(document.discharge_efficiency),
        limit_unit=document.units.limit,
        charge_map=charge_map,
        discharge_map=discharge_map,
        nominal_capacity_wh=document.nominal_capacity_wh,
        full_charge_interval_days=document.full_charge_interval_days,
        full_charge_detect_percentage=document.full_charge_detect_percentage,
    )
