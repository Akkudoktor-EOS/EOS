"""Abstract and base classes for devices."""

from enum import StrEnum

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel


class DevicesBaseSettings(SettingsBaseModel):
    """Base devices setting."""

    device_id: str = Field(
        default="<unknown>",
        description="ID of device",
        examples=["battery1", "ev1", "inverter1", "dishwasher"],
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
