"""Abstract and base classes for devices."""

import math
from enum import StrEnum
from typing import Optional

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel


class DevicesBaseSettings(SettingsBaseModel):
    """Base devices setting."""

    device_id: str = Field(
        default="<unknown>",
        json_schema_extra={
            "description": "ID of device",
            "examples": ["battery1", "ev1", "inverter1", "dishwasher"],
        },
    )


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

    if not profile_given:
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
                "load_profile_power_w must contain only finite values "
                "(no NaN or infinity)."
            )
        if value < 0:
            raise ValueError("load_profile_power_w must not contain negative values.")


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
