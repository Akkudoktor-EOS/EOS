"""Resolution of the effective battery parameters.

The effective battery parameters (capacity, power limits, efficiencies, SOC
range) follow from three inputs:

- the battery device settings (``BatteriesCommonSettings``): capacity override,
  installation power limits, operational SOC range,
- the compiled battery profile (``BatteryProfile``): limit maps, efficiency
  curves, model power limits, technical SOC bounds,
- the operating point (``BatteryOperatingPoint``): temperature, SoC and the
  current charge / discharge power.

The functions here are pure: they neither load profiles nor read measurements.
Loading the profile is done by the profile store, reading the operating point by
``batterymeasurement``.
"""

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

from loguru import logger

if TYPE_CHECKING:
    from akkudoktoreos.devices.profile.batteryprofileruntime import BatteryProfile
    from akkudoktoreos.devices.settings.batterysettings import BatteriesCommonSettings


# Built-in fallbacks, used only when the profile cannot be loaded or applied.
BATTERY_DEFAULT_CHARGING_EFFICIENCY: float = 0.88
BATTERY_DEFAULT_DISCHARGING_EFFICIENCY: float = 0.88
BATTERY_DEFAULT_MAX_CHARGE_POWER_W: float = 5000.0
BATTERY_DEFAULT_MAX_DISCHARGE_POWER_W: float = 5000.0
BATTERY_DEFAULT_CAPACITY_WH: int = 8000

# Operating point assumed when the battery measurement provides no value.
BATTERY_DEFAULT_SOC_FACTOR: float = 0.5
BATTERY_DEFAULT_TEMPERATURE_DEGC: float = 25.0


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    lookup: Callable[[float], Optional[float]],
    measured_power_w: Optional[float],
    max_power_w: float,
    capacity_wh: int,
    default: float,
) -> float:
    """Return the efficiency at the measured power, else at the maximum power.

    Falls back to the built-in default if the profile has no usable curve or
    there is no positive power to derive a C-rate from.
    """
    power_w = measured_power_w if measured_power_w is not None else max_power_w
    if power_w <= 0:
        return default
    efficiency = lookup(power_w / capacity_wh)
    return efficiency if efficiency is not None else default


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_nominal_capacity_wh(
    settings: "BatteriesCommonSettings", profile: Optional["BatteryProfile"]
) -> int:
    """Resolve the nominal capacity [Wh]: settings, else profile, else built-in default.

    Args:
        settings: Battery device settings.
        profile: Compiled battery profile, None if it could not be loaded.

    Returns:
        The effective nominal capacity [Wh].
    """
    if settings.nominal_capacity_wh is not None:
        return settings.nominal_capacity_wh
    if profile is not None and profile.nominal_capacity_wh is not None:
        # The settings work in whole Wh; never round a tiny capacity down to 0.
        return max(1, round(profile.nominal_capacity_wh))
    return BATTERY_DEFAULT_CAPACITY_WH


def _resolve_without_profile(
    settings: "BatteriesCommonSettings",
    capacity_wh: int,
    operating_point: BatteryOperatingPoint,
) -> BatteryOperatingParameters:
    """Resolve the parameters from installation limits and built-in defaults only."""
    return BatteryOperatingParameters(
        temperature_degC=operating_point.temperature_degC,
        soc_factor=operating_point.soc_factor,
        capacity_wh=capacity_wh,
        charging_efficiency=BATTERY_DEFAULT_CHARGING_EFFICIENCY,
        discharging_efficiency=BATTERY_DEFAULT_DISCHARGING_EFFICIENCY,
        max_charge_power_w=(
            settings.max_charge_power_limit_w
            if settings.max_charge_power_limit_w is not None
            else BATTERY_DEFAULT_MAX_CHARGE_POWER_W
        ),
        max_discharge_power_w=(
            settings.max_discharge_power_limit_w
            if settings.max_discharge_power_limit_w is not None
            else BATTERY_DEFAULT_MAX_DISCHARGE_POWER_W
        ),
        min_soc_percentage=settings.min_soc_percentage,
        max_soc_percentage=settings.max_soc_percentage,
    )


def resolve_battery_parameters(
    settings: "BatteriesCommonSettings",
    profile: Optional["BatteryProfile"],
    capacity_wh: int,
    operating_point: BatteryOperatingPoint,
) -> BatteryOperatingParameters:
    """Resolve all parameters for a profile and an operating point, strictly.

    Without profile (it could not be loaded) only installation limits and
    built-in defaults apply.

    Args:
        settings: Battery device settings.
        profile: Compiled battery profile, or None.
        capacity_wh: Effective nominal capacity [Wh].
        operating_point: Operating point to evaluate the profile at.

    Returns:
        The effective parameters.

    Raises:
        ValueError: If the profile inhibits charging at the operating point,
            yields a non-positive charge power, or the technical SOC bounds
            leave an empty SOC range.
    """
    if profile is None:
        return _resolve_without_profile(settings, capacity_wh, operating_point)

    temperature_degC = operating_point.temperature_degC
    soc_factor = operating_point.soc_factor
    soc_percent = soc_factor * 100.0

    # --- maximum charge power -----------------------------------------
    charge_limit = profile.max_charge_crate(temperature_degC, soc_percent)
    if charge_limit is None:
        raise ValueError(
            f"Battery profile '{profile.battery_model}' inhibits charging at "
            f"{temperature_degC} degC / {soc_percent} % SoC."
        )
    max_charge_power_w = _limited_power_w(
        charge_limit,
        profile,
        capacity_wh,
        profile.max_charge_power_w,
        settings.max_charge_power_limit_w,
    )
    if max_charge_power_w <= 0:
        raise ValueError(
            f"Battery profile '{profile.battery_model}' allows no charge power at "
            f"{temperature_degC} degC / {soc_percent} % SoC."
        )

    # --- maximum discharge power (0 if inhibited, nothing requires it) --
    discharge_limit = profile.max_discharge_crate(temperature_degC, soc_percent)
    if discharge_limit is None:
        max_discharge_power_w = 0.0
    else:
        max_discharge_power_w = _limited_power_w(
            discharge_limit,
            profile,
            capacity_wh,
            profile.max_discharge_power_w,
            settings.max_discharge_power_limit_w,
        )

    # --- efficiencies: measured power, else maximum power --------------
    charging_efficiency = _efficiency_at_power(
        profile.charge_efficiency_at,
        operating_point.charge_power_w,
        max_charge_power_w,
        capacity_wh,
        BATTERY_DEFAULT_CHARGING_EFFICIENCY,
    )
    discharging_efficiency = _efficiency_at_power(
        profile.discharge_efficiency_at,
        operating_point.discharge_power_w,
        max_discharge_power_w,
        capacity_wh,
        BATTERY_DEFAULT_DISCHARGING_EFFICIENCY,
    )

    # --- SOC range: user's operational range within technical bounds ---
    min_soc = settings.min_soc_percentage
    max_soc = settings.max_soc_percentage
    if profile.technical_min_soc_percent is not None:
        min_soc = max(min_soc, math.ceil(profile.technical_min_soc_percent))
    if profile.technical_max_soc_percent is not None:
        max_soc = min(max_soc, math.floor(profile.technical_max_soc_percent))
    if min_soc > max_soc:
        raise ValueError(
            f"SOC range [{min_soc}, {max_soc}] is empty after applying the technical "
            f"SOC bounds of battery profile '{profile.battery_model}'."
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
    )


def resolve_battery_parameters_lenient(
    settings: "BatteriesCommonSettings",
    profile: Optional["BatteryProfile"],
    operating_point: Optional[BatteryOperatingPoint] = None,
) -> BatteryOperatingParameters:
    """Resolve like ``resolve_battery_parameters``, but never raise.

    If the profile cannot be applied at the operating point, installation limits
    and built-in defaults are used, but the capacity still comes from the
    profile.

    Args:
        settings: Battery device settings.
        profile: Compiled battery profile, None if it could not be loaded.
        operating_point: Operating point, the reference operating point if None.

    Returns:
        The effective parameters.
    """
    if operating_point is None:
        operating_point = BatteryOperatingPoint()
    capacity_wh = resolve_nominal_capacity_wh(settings, profile)
    try:
        return resolve_battery_parameters(settings, profile, capacity_wh, operating_point)
    except ValueError as exc:
        logger.debug(f"Battery profile not applicable, using defaults: {exc}")
        return _resolve_without_profile(settings, capacity_wh, operating_point)


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
    profile: Optional["BatteryProfile"],
    capacity_wh: int,
    temperature_degC: float,
) -> Optional[BatteryPowerCurves]:
    """Evaluate the profile over the whole SoC range at one temperature.

    The power limits are capped by the model power limits of the profile and
    by the installation limits of the settings, like the parameters resolved by
    ``resolve_battery_parameters``.

    Args:
        settings: Battery device settings.
        profile: Compiled battery profile, None if it could not be loaded.
        capacity_wh: Effective nominal capacity [Wh].
        temperature_degC: Battery temperature to evaluate the profile at.

    Returns:
        The power curves, or None if there is no profile or the profile allows
        no charging at any SoC at this temperature.

    Raises:
        ValueError: If the profile limits cannot be converted to power (see
            ``profile_limit_to_power_w``).
    """
    if profile is None:
        return None
    charge_table = _power_limit_table_w(
        profile.max_charge_crate,
        temperature_degC,
        profile,
        capacity_wh,
        profile.max_charge_power_w,
        settings.max_charge_power_limit_w,
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
        settings.max_discharge_power_limit_w,
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
