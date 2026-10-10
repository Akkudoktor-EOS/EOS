"""Abstract and base classes for devices."""

import math
from enum import StrEnum
from typing import Optional


class BatteryOperationMode(StrEnum):
    """Battery Operation Mode.

    Enumerates the operating modes of a battery in a home energy
    management simulation. These modes require no direct awareness
    of electricity prices or carbon intensity — higher-level
    controllers or optimizers decide when to switch modes.

    Modes
    -----
    - IDLE:
        No charging or discharging.

    - SELF_CONSUMPTION:
        Charge from local surplus and discharge to meet local demand.

    - NON_EXPORT:
        Charge from on-site or local surplus with the goal of
        minimizing or preventing energy export to the external grid.
        Discharging to the grid is not allowed.

    - PEAK_SHAVING:
        Discharge during local demand peaks to reduce grid draw.

    - GRID_SUPPORT_EXPORT:
        Discharge to support the upstream grid when commanded.

    - GRID_SUPPORT_IMPORT:
        Charge from the grid when instructed to absorb excess supply.

    - FREQUENCY_REGULATION:
        Perform fast bidirectional power adjustments based on grid
        frequency deviations.

    - RAMP_RATE_CONTROL:
        Smooth changes in local net load or generation.

    - RESERVE_BACKUP:
        Maintain a minimum state of charge for emergency use.

    - OUTAGE_SUPPLY:
        Discharge to power critical loads during a grid outage.

    - FORCED_CHARGE:
        Override all other logic and charge regardless of conditions.

    - FORCED_DISCHARGE:
        Override all other logic and discharge regardless of conditions.

    - FAULT:
        Battery is unavailable due to fault or error state.
    """

    IDLE = "IDLE"
    SELF_CONSUMPTION = "SELF_CONSUMPTION"
    NON_EXPORT = "NON_EXPORT"
    PEAK_SHAVING = "PEAK_SHAVING"
    GRID_SUPPORT_EXPORT = "GRID_SUPPORT_EXPORT"
    GRID_SUPPORT_IMPORT = "GRID_SUPPORT_IMPORT"
    FREQUENCY_REGULATION = "FREQUENCY_REGULATION"
    RAMP_RATE_CONTROL = "RAMP_RATE_CONTROL"
    RESERVE_BACKUP = "RESERVE_BACKUP"
    OUTAGE_SUPPLY = "OUTAGE_SUPPLY"
    FORCED_CHARGE = "FORCED_CHARGE"
    FORCED_DISCHARGE = "FORCED_DISCHARGE"
    FAULT = "FAULT"


def validate_home_appliance_load_definition(
    *,
    load_profile_power_w: Optional[list[float]],
    load_profile_interval_seconds: Optional[int],
    consumption_wh: Optional[float],
    duration_h: Optional[float],
) -> None:
    """Validate the load definition of a flexible consumer / home appliance.

    A consumer's load must be given **either** as a full explicit power profile
    (``load_profile_power_w``) **or** as the complete flat fallback
    (``consumption_wh`` together with ``duration_h``). Providing both, or only a
    part of the fallback, is rejected. Profile values must be finite and
    non-negative and the profile interval, if given, must be positive.

    Args:
        load_profile_power_w: Explicit per-step power values [W], or None.
        load_profile_interval_seconds: Duration of one profile step [s], or None.
        consumption_wh: Fallback total energy of one run [Wh], or None.
        duration_h: Fallback run duration [h], or None.

    Raises:
        ValueError: If the definition is conflicting, incomplete, or contains
            invalid profile values.
    """
    profile_given = load_profile_power_w is not None
    fallback_fields = (consumption_wh, duration_h)
    fallback_partial = any(field is not None for field in fallback_fields)
    fallback_given = all(field is not None for field in fallback_fields)

    if profile_given and fallback_partial:
        raise ValueError(
            "Conflicting home appliance load definition: provide either "
            "load_profile_power_w or consumption_wh together with duration_h, "
            "not both."
        )

    if load_profile_power_w is None:
        if not fallback_given:
            raise ValueError(
                "Incomplete home appliance load definition: provide a full "
                "load_profile_power_w or both consumption_wh and duration_h."
            )
        # Value ranges of the fallback fields are enforced by their Field
        # constraints (gt=0); nothing more to check here.
        return

    # Explicit profile path.
    if load_profile_interval_seconds is not None and load_profile_interval_seconds <= 0:
        raise ValueError("load_profile_interval_seconds must be greater than zero.")
    if len(load_profile_power_w) == 0:
        raise ValueError("load_profile_power_w must not be empty.")
    for value in load_profile_power_w:
        if value is None or math.isnan(value) or math.isinf(value):
            raise ValueError(
                "load_profile_power_w must contain only finite values (no NaN or infinity)."
            )
        if value < 0:
            raise ValueError("load_profile_power_w must not contain negative values.")


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


def interpolate_efficiency_curve(curve: list[tuple[float, float]], load_fraction: float) -> float:
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
    curve: list[tuple[float, float]],
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


class ConsumerScheduleMode(StrEnum):
    """Schedule mode of a flexible consumer (home appliance).

    Determines how often a consumer's load profile is scheduled within the
    optimization horizon.

    Modes
    -----
    - ONCE:
        The consumer runs exactly once somewhere within the optimization
        horizon ("fire and forget"). The optimizer picks the start.

    - DAILY:
        The consumer runs once per local calendar day, but only on days for
        which at least one complete, allowed run still fits into the remaining
        horizon. The optimizer picks one start per eligible day.
    """

    ONCE = "ONCE"
    DAILY = "DAILY"


class ConsumerDeadlinePolicy(StrEnum):
    """Behaviour when a flexible consumer's deadline cannot be met.

    A deadline (``deadline_datetime``) demands that a complete run has *finished*
    before that moment. Depending on "now", the run duration, the optimization
    horizon and the allowed time windows, no such start may exist.

    Policies
    --------
    - BEST_EFFORT:
        Run as early as the remaining constraints allow, i.e. minimize the
        delay instead of the cost ("it should have been done by 03:00, so
        start now"). A warning is logged. This keeps an optimization request
        answerable instead of failing it - the usual choice for home
        automation.

    - STRICT:
        Keep the deadline. A ONCE consumer without a feasible start makes the
        optimization fail; a DAILY consumer is simply not scheduled on days
        without a feasible start.
    """

    BEST_EFFORT = "BEST_EFFORT"
    STRICT = "STRICT"


class ApplianceOperationMode(StrEnum):
    """Appliance operation modes.

    Modes
    -----
    - OFF:
        Stop or prevent any active operation of the appliance.

    - RUN:
        Start or continue normal operation of the appliance.

    - DEFER:
        Postpone operation to a later time window based on
        scheduling or optimization criteria.

    - PAUSE:
        Temporarily suspend an ongoing operation, keeping the
        option to resume later.

    - RESUME:
        Continue an operation that was previously paused or
        deferred.

    - LIMIT_POWER:
        Run the appliance under reduced power constraints,
        for example in response to load-management or
        demand-response signals.

    - FORCED_RUN:
        Start or maintain operation even if constraints or
        optimization strategies would otherwise delay or limit it.

    - FAULT:
        Appliance is unavailable due to fault or error state.
    """

    OFF = "OFF"
    RUN = "RUN"
    DEFER = "DEFER"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    LIMIT_POWER = "LIMIT_POWER"
    FORCED_RUN = "FORCED_RUN"
    FAULT = "FAULT"
