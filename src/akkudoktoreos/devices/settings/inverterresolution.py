"""Resolution of the effective inverter parameters.

Every effective inverter parameter (nominal and maximum power, AC charge power
limit, efficiencies) comes from exactly two sources:

- the compiled inverter profile (``InverterProfile``): efficiency curves over the
  load fraction, the AC charge power limit of the model, the power derating over
  the ambient temperature and, unless it is a generic profile, the nominal power,
- the inverter device settings (``InverterCommonSettings``): the
  ``profile_adjustment`` of this unit (nominal power, installation limits).

The profile is evaluated at an operating point (``InverterOperatingPoint``), the
ambient temperature of the inverter. Without a temperature measurement it is
``INVERTER_DEFAULT_TEMPERATURE_DEGC`` (45 degC). If the parameters cannot be
resolved, an ``InverterResolutionError`` names the inverter and the reason.

The functions here are pure: they neither load profiles nor read measurements.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from akkudoktoreos.devices.profile.inverterprofile import InverterProfile
    from akkudoktoreos.devices.profile.profilecurve import PrecomputedCurve
    from akkudoktoreos.devices.settings.invertersettings import InverterCommonSettings


# Ambient temperature of the inverter assumed when no measurement is available.
INVERTER_DEFAULT_TEMPERATURE_DEGC: float = 45.0


class InverterResolutionError(ValueError):
    """The effective parameters of an inverter cannot be resolved.

    The message names the inverter and the reason.
    """


@dataclass(frozen=True)
class InverterOperatingPoint:
    """Conditions the inverter profile is evaluated at.

    Attributes:
        temperature_degC: Ambient temperature of the inverter [degC].
    """

    temperature_degC: float = INVERTER_DEFAULT_TEMPERATURE_DEGC


@dataclass(frozen=True)
class InverterOperatingParameters:
    """Consistent snapshot of the effective inverter parameters.

    All values belong to the same operating point.

    Attributes:
        temperature_degC: Ambient temperature the profile was evaluated at.
        nominal_power_w: Effective nominal AC power [W]: profile adjustment, else
            profile.
        power_fraction: Derating of the nominal power at the temperature
            [0.0 ... 1.0].
        max_power_w: Effective maximum AC power [W]: the derated nominal power,
            capped by the installation limit of the profile adjustment.
        max_ac_charge_power_w: Effective AC charge power limit [W]: the smallest
            of the maximum power, the derated limit of the model and the
            installation limit. 0 disables AC charging.
        ac_charge_limits_total_charge: Whether the AC charge power limit also
            limits PV charging of the battery.
        dc_to_ac_efficiency_curve: DC→AC efficiency over the load fraction of
            ``max_power_w`` as ``(load_fraction, efficiency)`` points within
            [0, 1].
        ac_to_dc_efficiency_curve: AC→DC efficiency over the load fraction of
            ``max_power_w`` as ``(load_fraction, efficiency)`` points within
            [0, 1].
        dc_to_ac_efficiency: DC→AC efficiency at the reference load fraction of
            the settings.
        ac_to_dc_efficiency: AC→DC efficiency at the AC charge power limit (at
            the maximum power if AC charging is disabled).
    """

    temperature_degC: float
    nominal_power_w: float
    power_fraction: float
    max_power_w: float
    max_ac_charge_power_w: float
    ac_charge_limits_total_charge: bool
    dc_to_ac_efficiency_curve: tuple[tuple[float, float], ...]
    ac_to_dc_efficiency_curve: tuple[tuple[float, float], ...]
    dc_to_ac_efficiency: float
    ac_to_dc_efficiency: float


def _resolution_error(settings: "InverterCommonSettings", reason: str) -> InverterResolutionError:
    """Return an error naming the inverter."""
    return InverterResolutionError(f"Inverter '{settings.device_id}': {reason}")


def _curve_over_max_power(
    curve: "PrecomputedCurve", scale: float
) -> tuple[tuple[float, float], ...]:
    """Return a profile efficiency curve over the load fraction of the maximum power.

    The profile curve is over the load fraction of the nominal power; ``scale``
    is nominal power / maximum power (>= 1). Loads above the maximum power cannot
    occur, so the curve ends at load fraction 1.0 with the value there. It always
    has at least two points within [0, 1].

    Args:
        curve: Profile curve over the load fraction of the nominal power.
        scale: Nominal power divided by maximum power.

    Returns:
        The ``(load_fraction, efficiency)`` points.
    """
    points = [(x * scale, y) for x, y in zip(curve.x, curve.y) if x * scale < 1.0]
    end_value = curve.lookup(1.0 / scale)
    points.append((1.0, end_value if end_value is not None else curve.y[-1]))
    if len(points) < 2:
        points.insert(0, (0.0, curve.y[0]))
    return tuple(points)


def resolve_nominal_power_w(
    settings: "InverterCommonSettings", profile: "InverterProfile"
) -> float:
    """Resolve the nominal AC power [W]: profile adjustment, else profile.

    Raises:
        InverterResolutionError: If neither the profile adjustment nor the
            profile provides a nominal power (a generic profile without
            adjustment).
    """
    adjusted_w = settings.profile_adjustment.nominal_power_w
    if adjusted_w is not None:
        return adjusted_w
    if profile.nominal_power_w is not None:
        return profile.nominal_power_w
    raise _resolution_error(
        settings,
        f"inverter profile '{settings.inverter_profile}' is generic and provides no "
        "nominal power; set 'profile_adjustment.nominal_power_w'.",
    )


def resolve_inverter_parameters(
    settings: "InverterCommonSettings",
    profile: "InverterProfile",
    operating_point: InverterOperatingPoint,
) -> InverterOperatingParameters:
    """Resolve the effective inverter parameters at an operating point.

    Args:
        settings: Inverter device settings.
        profile: Compiled inverter profile.
        operating_point: Conditions to evaluate the profile at.

    Returns:
        The effective parameters.

    Raises:
        InverterResolutionError: If the nominal power cannot be resolved or the
            inverter is derated to zero power at the operating point.
    """
    adjustment = settings.profile_adjustment
    nominal_power_w = resolve_nominal_power_w(settings, profile)
    power_fraction = profile.power_fraction_at(operating_point.temperature_degC)
    max_power_w = nominal_power_w * power_fraction
    if adjustment.max_power_w is not None:
        max_power_w = min(max_power_w, adjustment.max_power_w)
    if max_power_w <= 0.0:
        raise _resolution_error(
            settings,
            f"inverter profile '{settings.inverter_profile}' allows no power at "
            f"{operating_point.temperature_degC} degC.",
        )

    max_ac_charge_power_w = max_power_w
    if profile.max_ac_charge_power_w is not None:
        max_ac_charge_power_w = min(
            max_ac_charge_power_w, profile.max_ac_charge_power_w * power_fraction
        )
    if adjustment.max_ac_charge_power_w is not None:
        max_ac_charge_power_w = min(max_ac_charge_power_w, adjustment.max_ac_charge_power_w)

    # The profile curves are over the load fraction of the nominal power, the
    # optimizers read them over the load fraction of the maximum power.
    scale = nominal_power_w / max_power_w
    dc_to_ac_curve = _curve_over_max_power(profile.dc_to_ac_efficiency, scale)
    ac_to_dc_curve = _curve_over_max_power(profile.ac_to_dc_efficiency, scale)
    reference_power_w = settings.dc_to_ac_efficiency_reference_load_fraction * max_power_w
    ac_charge_power_w = max_ac_charge_power_w if max_ac_charge_power_w > 0.0 else max_power_w
    return InverterOperatingParameters(
        temperature_degC=operating_point.temperature_degC,
        nominal_power_w=nominal_power_w,
        power_fraction=power_fraction,
        max_power_w=max_power_w,
        max_ac_charge_power_w=max_ac_charge_power_w,
        ac_charge_limits_total_charge=profile.ac_charge_limits_total_charge,
        dc_to_ac_efficiency_curve=dc_to_ac_curve,
        ac_to_dc_efficiency_curve=ac_to_dc_curve,
        dc_to_ac_efficiency=profile.dc_to_ac_efficiency_at(reference_power_w / nominal_power_w),
        ac_to_dc_efficiency=profile.ac_to_dc_efficiency_at(ac_charge_power_w / nominal_power_w),
    )


def check_inverter_adjustment(
    settings: "InverterCommonSettings", parameters: InverterOperatingParameters
) -> list[str]:
    """Return warnings for installation limits of the adjustment that have no effect.

    Args:
        settings: Inverter device settings.
        parameters: Resolved parameters; only the nominal power is used.

    Returns:
        One warning per installation limit above the nominal power.
    """
    warnings: list[str] = []
    adjustment = settings.profile_adjustment
    for name, limit_w in (
        ("max_power_w", adjustment.max_power_w),
        ("max_ac_charge_power_w", adjustment.max_ac_charge_power_w),
    ):
        if limit_w is not None and limit_w > parameters.nominal_power_w:
            warnings.append(
                f"Inverter '{settings.device_id}': 'profile_adjustment.{name}' {limit_w} W is "
                f"above the nominal power {parameters.nominal_power_w} W and has no effect."
            )
    return warnings
