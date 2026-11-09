"""Resolution of the effective battery parameters.

Every effective battery parameter (capacity, power limits, efficiencies, SOC
range) comes from exactly two sources:

- the compiled battery profile (``BatteryProfile``): limit maps, efficiency
  curves, model power limits, technical SOC bounds and, unless it is a generic
  profile, the nominal capacity,
- the battery device settings (``BatteriesCommonSettings``): the
  ``profile_adjustment`` of this unit (capacity, installation power limits) and
  the operational SOC range.

The profile is evaluated at an operating point (``BatteryOperatingPoint``):
temperature, SoC and the current charge / discharge power. There are no built-in
fallback values: if the parameters cannot be resolved, a
``BatteryResolutionError`` names the battery and the reason.

The one exception is the parameter preparation of the optimizations, which
always succeeds for a configuration error: it fills the values that cannot be
resolved with the former defaults (``BATTERY_FALLBACK_*``) and reports them,
see ``BatteriesCommonSettings.resolve_operating_parameters_or_defaults``.

The functions here are pure: they neither load profiles nor read measurements.
Loading the profile is done by the profile store, reading the operating point by
``batterymeasurement``.
"""

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from akkudoktoreos.devices.profile.batteryprofileruntime import (
        BatteryProfile,
        PrecomputedEfficiencyCurve,
    )
    from akkudoktoreos.devices.settings.batterysettings import BatteriesCommonSettings


# Operating point assumed when the battery measurement provides no value.
BATTERY_DEFAULT_SOC_FACTOR: float = 0.5
BATTERY_DEFAULT_TEMPERATURE_DEGC: float = 25.0

# Defaults of the battery settings before battery profiles. Used only by the
# parameter preparation of the optimizations, for values the configuration
# does not allow to resolve.
BATTERY_FALLBACK_CAPACITY_WH: int = 8000
BATTERY_FALLBACK_MAX_CHARGE_POWER_W: float = 5000.0
BATTERY_FALLBACK_MAX_DISCHARGE_POWER_W: float = 5000.0
BATTERY_FALLBACK_CHARGING_EFFICIENCY: float = 0.88
BATTERY_FALLBACK_DISCHARGING_EFFICIENCY: float = 0.88

# Power limits of the installation for a generic profile without own power
# limits, if the profile adjustment sets none: the former default, instead of
# the C-rate of the generic profile (e.g. 60 kW for a 60 kWh electric vehicle).
BATTERY_GENERIC_MAX_CHARGE_POWER_W: float = BATTERY_FALLBACK_MAX_CHARGE_POWER_W
BATTERY_GENERIC_MAX_DISCHARGE_POWER_W: float = BATTERY_FALLBACK_MAX_DISCHARGE_POWER_W

# Full charge detection threshold if neither the profile adjustment nor a usable
# profile provides one; the default of the profile document.
BATTERY_DEFAULT_FULL_CHARGE_DETECT_PERCENTAGE: int = 99


class BatteryResolutionError(ValueError):
    """The effective parameters of a battery cannot be resolved.

    The message names the battery device and the reason, e.g. a generic profile
    without a capacity in the profile adjustment. The cause is the configuration
    (settings or battery profile); the operating point never causes it.
    """


class BatterySocRangeError(BatteryResolutionError):
    """The configured SoC range lies outside the technical SoC bounds of the profile."""


@dataclass(frozen=True)
class BatteryOperatingPoint:
    """Operating point the battery profile is evaluated at.

    The defaults form the reference operating point used when no measurement
    is taken into account.

    Attributes:
        temperature_degC: Battery temperature [degC].
        soc_factor: State of charge [0.0 ... 1.0].
        charge_power_w: Current charge power as positive magnitude [W], None if
            the battery is not charging. Used for the charging efficiency.
        discharge_power_w: Current discharge power as positive magnitude [W],
            None if the battery is not discharging. Used for the discharging
            efficiency.
    """

    temperature_degC: float = BATTERY_DEFAULT_TEMPERATURE_DEGC
    soc_factor: float = BATTERY_DEFAULT_SOC_FACTOR
    charge_power_w: Optional[float] = None
    discharge_power_w: Optional[float] = None


@dataclass(frozen=True)
class BatteryOperatingParameters:
    """Consistent snapshot of the effective battery parameters.

    All values belong to the same operating point and the same measurements, so
    one snapshot can be used for a whole optimizer conversion even if
    measurements change meanwhile.

    Attributes:
        temperature_degC: Battery temperature the profile was evaluated at.
        soc_factor: State of charge [0.0 ... 1.0] the profile was evaluated at.
        capacity_wh: Effective nominal capacity [Wh].
        charging_efficiency: Charging efficiency [0.01 ... 1.00].
        discharging_efficiency: Discharging efficiency [0.01 ... 1.00].
        max_charge_power_w: Effective maximum charging power [W].
        max_discharge_power_w: Effective maximum discharging power [W], 0 if
            discharging is inhibited at the operating point.
        min_soc_percentage: Effective minimum SoC [%], within technical bounds.
        max_soc_percentage: Effective maximum SoC [%], within technical bounds.
        profile: Compiled battery profile the snapshot was resolved from, None
            if it was resolved without profile. Conversions take the power
            tables from it, so they need not load the profile again and use the
            same profile version as the snapshot. Not part of comparisons.
    """

    temperature_degC: float
    soc_factor: float
    capacity_wh: int
    charging_efficiency: float
    discharging_efficiency: float
    max_charge_power_w: float
    max_discharge_power_w: float
    min_soc_percentage: int
    max_soc_percentage: int
    profile: Optional["BatteryProfile"] = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class BatteryFullCharge:
    """Effective periodic full charge policy of a stationary battery.

    Attributes:
        interval_days: Maximum number of days between two full charges [d], None
            if the periodic full charge is disabled.
        soc_percentage: State of charge a full charge has to reach [%], within
            the technical maximum SOC of the profile.
        detect_percentage: Measured state of charge from which the battery
            counts as fully charged [%], at most ``soc_percentage``.
    """

    interval_days: Optional[float]
    soc_percentage: int
    detect_percentage: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolution_error(
    settings: "BatteriesCommonSettings",
    reason: str,
    error_type: type[BatteryResolutionError] = BatteryResolutionError,
) -> BatteryResolutionError:
    """Return a resolution error naming the battery device."""
    return error_type(f"Battery '{settings.device_id}': {reason}")


def profile_limit_to_power_w(limit: float, profile: "BatteryProfile", capacity_wh: int) -> float:
    """Convert a profile limit value into power [W] according to its unit.

    Args:
        limit: Limit value in the unit of the profile (C-rate, W or A).
        profile: Compiled battery profile.
        capacity_wh: Effective nominal capacity [Wh], used for C-rate limits.

    Returns:
        The limit as power [W].

    Raises:
        ValueError: If the unit is not supported, or the limit is in A and the
            profile has no nominal voltage.
    """
    unit = profile.limit_unit
    if unit == "C-rate":
        return limit * capacity_wh
    if unit == "W":
        return limit
    if unit == "A":
        if profile.nominal_voltage_v is None:
            raise ValueError(
                f"Battery profile '{profile.battery_model}' has limits in A "
                "but no nominal_voltage_v to convert them to power."
            )
        return limit * profile.nominal_voltage_v
    raise ValueError(f"Unsupported limit unit '{unit}' in battery profile.")


def _installation_cap_w(
    profile: "BatteryProfile",
    adjusted_cap_w: Optional[float],
    profile_cap_w: Optional[float],
    generic_default_w: float,
) -> Optional[float]:
    """Return the power limit of the installation [W], None for no limit.

    The profile adjustment sets it. Without one, a generic profile (no nominal
    capacity of its own) without own power limit gets ``generic_default_w``; a
    profile of a battery model needs no installation limit.

    Args:
        profile: Compiled battery profile.
        adjusted_cap_w: Power limit of the profile adjustment, None if not set.
        profile_cap_w: Absolute power limit of the profile, None if not set.
        generic_default_w: Limit for a generic profile without own limit.

    Returns:
        The power limit of the installation, None if it does not limit.
    """
    if adjusted_cap_w is not None:
        return adjusted_cap_w
    if profile.nominal_capacity_wh is None and profile_cap_w is None:
        return generic_default_w
    return None


def _limited_power_w(
    limit: float,
    profile: "BatteryProfile",
    capacity_wh: int,
    profile_cap_w: Optional[float],
    installation_cap_w: Optional[float],
) -> float:
    """Convert a profile limit to watts and apply the profile and installation caps."""
    power_w = profile_limit_to_power_w(limit, profile, capacity_wh)
    if profile_cap_w is not None:
        power_w = min(power_w, profile_cap_w)
    if installation_cap_w is not None:
        power_w = min(power_w, installation_cap_w)
    return power_w


def _efficiency_at_power(
    curve: "PrecomputedEfficiencyCurve",
    measured_power_w: Optional[float],
    max_power_w: float,
    capacity_wh: int,
) -> float:
    """Return the efficiency of a profile curve at the measured, else the maximum power.

    Without positive power (e.g. discharging is inhibited) the efficiency at the
    lowest C-rate of the curve applies. If the curve gives no value at the
    C-rate (it rejects out-of-range C-rates or does not interpolate), the
    efficiency of the nearest curve breakpoint applies.
    """
    power_w = measured_power_w if measured_power_w is not None else max_power_w
    if power_w <= 0:
        return curve.efficiency[0]
    c_rate = power_w / capacity_wh
    efficiency = curve.lookup(c_rate)
    if efficiency is not None:
        return efficiency
    nearest = min(range(len(curve.c_rate)), key=lambda i: abs(curve.c_rate[i] - c_rate))
    return curve.efficiency[nearest]


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_nominal_capacity_wh(
    settings: "BatteriesCommonSettings", profile: "BatteryProfile"
) -> int:
    """Resolve the nominal capacity [Wh]: profile adjustment, else profile.

    Args:
        settings: Battery device settings.
        profile: Compiled battery profile.

    Returns:
        The effective nominal capacity [Wh].

    Raises:
        BatteryResolutionError: If neither the profile adjustment nor the
            profile provides a capacity (a generic profile without adjustment).
    """
    adjusted_wh = settings.profile_adjustment.nominal_capacity_wh
    if adjusted_wh is not None:
        return adjusted_wh
    if profile.nominal_capacity_wh is not None:
        # The settings work in whole Wh; never round a tiny capacity down to 0.
        return max(1, round(profile.nominal_capacity_wh))
    raise _resolution_error(
        settings,
        f"battery profile '{settings.battery_profile}' is generic and provides no "
        "nominal capacity; set 'profile_adjustment.nominal_capacity_wh'.",
    )


def resolve_full_charge(
    settings: "BatteriesCommonSettings", profile: Optional["BatteryProfile"]
) -> BatteryFullCharge:
    """Resolve the periodic full charge policy: profile adjustment, else profile.

    The interval and the detection threshold are properties of the battery model
    and come from the profile unless the profile adjustment sets them. The
    target SOC and whether to charge from the grid are operating policy of the
    battery settings.

    Args:
        settings: Battery device settings.
        profile: Compiled battery profile, None if it is not usable. Then only
            the profile adjustment and the defaults apply.

    Returns:
        The effective full charge policy. The target is capped by the technical
        maximum SOC of the profile and the detection threshold by the target.
    """
    adjustment = settings.profile_adjustment
    interval_days = adjustment.full_charge_interval_days
    detect_percentage = adjustment.full_charge_detect_percentage
    soc_percentage = settings.full_charge_soc_percentage
    if profile is not None:
        if interval_days is None:
            interval_days = profile.full_charge_interval_days
        if detect_percentage is None:
            detect_percentage = profile.full_charge_detect_percentage
        soc_percentage = min(soc_percentage, math.floor(profile.technical_max_soc_percent))
    if detect_percentage is None:
        detect_percentage = BATTERY_DEFAULT_FULL_CHARGE_DETECT_PERCENTAGE
    return BatteryFullCharge(
        interval_days=interval_days,
        soc_percentage=soc_percentage,
        detect_percentage=min(detect_percentage, soc_percentage),
    )


def resolve_battery_parameters(
    settings: "BatteriesCommonSettings",
    profile: "BatteryProfile",
    capacity_wh: int,
    operating_point: BatteryOperatingPoint,
) -> BatteryOperatingParameters:
    """Resolve all parameters for a profile and an operating point.

    Args:
        settings: Battery device settings.
        profile: Compiled battery profile.
        capacity_wh: Effective nominal capacity [Wh].
        operating_point: Operating point to evaluate the profile at.

    Returns:
        The effective parameters.

    The operating point never makes the resolution fail: if the profile inhibits
    charging or discharging there (e.g. the battery is too cold), the maximum
    charge or discharge power is 0.

    Raises:
        BatterySocRangeError: If the technical SOC bounds leave an empty SOC
            range.
        BatteryResolutionError: If the profile limits cannot be converted to
            power.
    """
    temperature_degC = operating_point.temperature_degC
    soc_factor = operating_point.soc_factor
    soc_percent = soc_factor * 100.0
    adjustment = settings.profile_adjustment

    # --- maximum charge power (0 if inhibited, e.g. too cold) ----------
    charge_limit = profile.max_charge_crate(temperature_degC, soc_percent)
    if charge_limit is None:
        max_charge_power_w = 0.0
    else:
        try:
            max_charge_power_w = max(
                0.0,
                _limited_power_w(
                    charge_limit,
                    profile,
                    capacity_wh,
                    profile.max_charge_power_w,
                    _installation_cap_w(
                        profile,
                        adjustment.max_charge_power_w,
                        profile.max_charge_power_w,
                        BATTERY_GENERIC_MAX_CHARGE_POWER_W,
                    ),
                ),
            )
        except ValueError as exc:
            raise _resolution_error(settings, str(exc)) from exc

    # --- maximum discharge power (0 if inhibited, nothing requires it) --
    discharge_limit = profile.max_discharge_crate(temperature_degC, soc_percent)
    if discharge_limit is None:
        max_discharge_power_w = 0.0
    else:
        try:
            max_discharge_power_w = _limited_power_w(
                discharge_limit,
                profile,
                capacity_wh,
                profile.max_discharge_power_w,
                _installation_cap_w(
                    profile,
                    adjustment.max_discharge_power_w,
                    profile.max_discharge_power_w,
                    BATTERY_GENERIC_MAX_DISCHARGE_POWER_W,
                ),
            )
        except ValueError as exc:
            raise _resolution_error(settings, str(exc)) from exc

    # --- efficiencies: measured power, else maximum power --------------
    charging_efficiency = _efficiency_at_power(
        profile.charge_efficiency,
        operating_point.charge_power_w,
        max_charge_power_w,
        capacity_wh,
    )
    discharging_efficiency = _efficiency_at_power(
        profile.discharge_efficiency,
        operating_point.discharge_power_w,
        max_discharge_power_w,
        capacity_wh,
    )

    # --- SOC range: user's operational range within technical bounds ---
    min_soc = max(settings.min_soc_percentage, math.ceil(profile.technical_min_soc_percent))
    max_soc = min(settings.max_soc_percentage, math.floor(profile.technical_max_soc_percent))
    if min_soc > max_soc:
        raise _resolution_error(
            settings,
            f"SOC range [{min_soc}, {max_soc}] is empty after applying the technical "
            f"SOC bounds of battery profile '{settings.battery_profile}'.",
            BatterySocRangeError,
        )

    return BatteryOperatingParameters(
        temperature_degC=temperature_degC,
        soc_factor=soc_factor,
        capacity_wh=capacity_wh,
        charging_efficiency=charging_efficiency,
        discharging_efficiency=discharging_efficiency,
        max_charge_power_w=max_charge_power_w,
        max_discharge_power_w=max_discharge_power_w,
        min_soc_percentage=min_soc,
        max_soc_percentage=max_soc,
        profile=profile,
    )


def resolve_battery_parameters_without_profile(
    settings: "BatteriesCommonSettings", operating_point: BatteryOperatingPoint
) -> tuple[BatteryOperatingParameters, list[str]]:
    """Resolve the parameters from the profile adjustment and the former defaults.

    For the parameter preparation of the optimizations if the battery profile
    cannot be used. The values of the profile adjustment apply where set; the
    former defaults (``BATTERY_FALLBACK_*``) fill the others.

    Args:
        settings: Battery device settings.
        operating_point: Operating point the parameters belong to.

    Returns:
        The parameters and a description of each default value used, e.g.
        ``"nominal capacity 8000 Wh"``.
    """
    adjustment = settings.profile_adjustment
    defaults: list[str] = []
    capacity_wh = adjustment.nominal_capacity_wh
    if capacity_wh is None:
        capacity_wh = BATTERY_FALLBACK_CAPACITY_WH
        defaults.append(f"nominal capacity {capacity_wh} Wh")
    max_charge_power_w = adjustment.max_charge_power_w
    if max_charge_power_w is None:
        max_charge_power_w = BATTERY_FALLBACK_MAX_CHARGE_POWER_W
        defaults.append(f"maximum charge power {max_charge_power_w} W")
    max_discharge_power_w = adjustment.max_discharge_power_w
    if max_discharge_power_w is None:
        max_discharge_power_w = BATTERY_FALLBACK_MAX_DISCHARGE_POWER_W
        defaults.append(f"maximum discharge power {max_discharge_power_w} W")
    defaults.append(f"charging efficiency {BATTERY_FALLBACK_CHARGING_EFFICIENCY}")
    defaults.append(f"discharging efficiency {BATTERY_FALLBACK_DISCHARGING_EFFICIENCY}")
    defaults.append("no SoC dependent power limits")
    return (
        BatteryOperatingParameters(
            temperature_degC=operating_point.temperature_degC,
            soc_factor=operating_point.soc_factor,
            capacity_wh=capacity_wh,
            charging_efficiency=BATTERY_FALLBACK_CHARGING_EFFICIENCY,
            discharging_efficiency=BATTERY_FALLBACK_DISCHARGING_EFFICIENCY,
            max_charge_power_w=max_charge_power_w,
            max_discharge_power_w=max_discharge_power_w,
            min_soc_percentage=settings.min_soc_percentage,
            max_soc_percentage=settings.max_soc_percentage,
        ),
        defaults,
    )


# ---------------------------------------------------------------------------
# Power curves for simulations
# ---------------------------------------------------------------------------

# Number of points of a power limit table: one per SoC percent, 0 % ... 100 %.
BATTERY_SOC_TABLE_POINTS: int = 101

# Power fractions an efficiency curve is sampled at: 0.0, 0.05, ..., 1.0.
BATTERY_EFFICIENCY_CURVE_FRACTIONS: tuple[float, ...] = tuple(i / 20 for i in range(21))


@dataclass(frozen=True)
class BatteryPowerCurves:
    """Power limits and efficiencies of a battery profile at one temperature.

    Prepared for simulations that evaluate the battery many times, e.g. the
    GENETIC optimizer: plain lists instead of profile lookups.

    Attributes:
        temperature_degC: Battery temperature the profile was evaluated at.
        charge_power_limit_w_by_soc: Maximum charging power [W] per SoC
            percent; index i is SoC i %. 0 where charging is inhibited.
        discharge_power_limit_w_by_soc: Maximum discharging power [W] per SoC
            percent; 0 where discharging is inhibited.
        max_charge_power_w: Highest charging power of the table [W].
        max_discharge_power_w: Highest discharging power of the table [W].
        charging_efficiency_curve: ``(power_fraction, efficiency)`` points; the
            power fraction is relative to ``max_charge_power_w``. None if the
            profile has no charge efficiency curve.
        discharging_efficiency_curve: ``(power_fraction, efficiency)`` points;
            the power fraction is relative to ``max_discharge_power_w``. None if
            the profile has no discharge efficiency curve or discharging is
            inhibited.
    """

    temperature_degC: float
    charge_power_limit_w_by_soc: list[float]
    discharge_power_limit_w_by_soc: list[float]
    max_charge_power_w: float
    max_discharge_power_w: float
    charging_efficiency_curve: Optional[list[tuple[float, float]]]
    discharging_efficiency_curve: Optional[list[tuple[float, float]]]


def _power_limit_table_w(
    crate_at: Callable[[float, float], Optional[float]],
    temperature_degC: float,
    profile: "BatteryProfile",
    capacity_wh: int,
    profile_cap_w: Optional[float],
    installation_cap_w: Optional[float],
) -> list[float]:
    """Return the power limit [W] per SoC percent at one temperature."""
    table: list[float] = []
    for soc_percent in range(BATTERY_SOC_TABLE_POINTS):
        limit = crate_at(temperature_degC, float(soc_percent))
        if limit is None:
            table.append(0.0)
            continue
        power_w = _limited_power_w(limit, profile, capacity_wh, profile_cap_w, installation_cap_w)
        table.append(max(power_w, 0.0))
    return table


def profile_peak_power_w(
    profile: "BatteryProfile", capacity_wh: int, temperature_degC: float
) -> tuple[float, float]:
    """Return the highest charge and discharge power of the profile over SoC [W].

    Only the profile and its absolute model power limits apply, no installation
    limit, so the result shows whether a ``profile_adjustment`` power limit
    lowers the profile at all.

    Args:
        profile: Compiled battery profile.
        capacity_wh: Effective nominal capacity [Wh].
        temperature_degC: Battery temperature to evaluate the profile at.

    Returns:
        ``(max_charge_power_w, max_discharge_power_w)``.

    Raises:
        ValueError: If the profile limits cannot be converted to power.
    """
    charge = _power_limit_table_w(
        profile.max_charge_crate,
        temperature_degC,
        profile,
        capacity_wh,
        profile.max_charge_power_w,
        None,
    )
    discharge = _power_limit_table_w(
        profile.max_discharge_crate,
        temperature_degC,
        profile,
        capacity_wh,
        profile.max_discharge_power_w,
        None,
    )
    return max(charge), max(discharge)


def _efficiency_curve(
    lookup: Callable[[float], Optional[float]],
    max_power_w: float,
    capacity_wh: int,
) -> Optional[list[tuple[float, float]]]:
    """Sample an efficiency curve over the power fraction of ``max_power_w``."""
    if max_power_w <= 0:
        return None
    curve: list[tuple[float, float]] = []
    for fraction in BATTERY_EFFICIENCY_CURVE_FRACTIONS:
        efficiency = lookup(fraction * max_power_w / capacity_wh)
        if efficiency is None:
            return None
        curve.append((fraction, efficiency))
    return curve


def resolve_battery_power_curves(
    settings: "BatteriesCommonSettings",
    profile: "BatteryProfile",
    capacity_wh: int,
    temperature_degC: float,
) -> Optional[BatteryPowerCurves]:
    """Evaluate the profile over the whole SoC range at one temperature.

    The power limits are capped by the model power limits of the profile and
    by the power limits of the profile adjustment, like the parameters resolved
    by ``resolve_battery_parameters``.

    Args:
        settings: Battery device settings.
        profile: Compiled battery profile.
        capacity_wh: Effective nominal capacity [Wh].
        temperature_degC: Battery temperature to evaluate the profile at.

    Returns:
        The power curves, or None if the profile allows no charging at any SoC
        at this temperature.

    Raises:
        ValueError: If the profile limits cannot be converted to power (see
            ``profile_limit_to_power_w``).
    """
    adjustment = settings.profile_adjustment
    charge_table = _power_limit_table_w(
        profile.max_charge_crate,
        temperature_degC,
        profile,
        capacity_wh,
        profile.max_charge_power_w,
        _installation_cap_w(
            profile,
            adjustment.max_charge_power_w,
            profile.max_charge_power_w,
            BATTERY_GENERIC_MAX_CHARGE_POWER_W,
        ),
    )
    max_charge_power_w = max(charge_table)
    if max_charge_power_w <= 0:
        return None
    discharge_table = _power_limit_table_w(
        profile.max_discharge_crate,
        temperature_degC,
        profile,
        capacity_wh,
        profile.max_discharge_power_w,
        _installation_cap_w(
            profile,
            adjustment.max_discharge_power_w,
            profile.max_discharge_power_w,
            BATTERY_GENERIC_MAX_DISCHARGE_POWER_W,
        ),
    )
    max_discharge_power_w = max(discharge_table)
    return BatteryPowerCurves(
        temperature_degC=temperature_degC,
        charge_power_limit_w_by_soc=charge_table,
        discharge_power_limit_w_by_soc=discharge_table,
        max_charge_power_w=max_charge_power_w,
        max_discharge_power_w=max_discharge_power_w,
        charging_efficiency_curve=_efficiency_curve(
            profile.charge_efficiency_at, max_charge_power_w, capacity_wh
        ),
        discharging_efficiency_curve=_efficiency_curve(
            profile.discharge_efficiency_at, max_discharge_power_w, capacity_wh
        ),
    )
