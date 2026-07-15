"""Genetic optimization algorithm device interfaces/ parameters."""

from typing import Optional

from pydantic import Field, model_validator
from typing_extensions import Self

from akkudoktoreos.config.configabc import TimeWindowSequence
from akkudoktoreos.devices.devicesabc import (
    ConsumerScheduleMode,
    validate_home_appliance_load_definition,
)
from akkudoktoreos.optimization.genetic.geneticabc import GeneticParametersBaseModel


class DeviceParameters(GeneticParametersBaseModel):
    device_id: str = Field(json_schema_extra={"description": "ID of device", "examples": "device1"})
    hours: Optional[int] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": "Number of prediction hours. Defaults to global config prediction hours.",
            "examples": [None],
        },
    )


def max_charging_power_field(description: Optional[str] = None) -> float:
    if description is None:
        description = "Maximum charging power in watts."
    return Field(default=5000, gt=0, json_schema_extra={"description": description})


def initial_soc_percentage_field(description: str) -> int:
    return Field(
        default=0, ge=0, le=100, json_schema_extra={"description": description, "examples": [42]}
    )


def discharging_efficiency_field(default_value: float) -> float:
    return Field(
        default=default_value,
        gt=0,
        le=1,
        json_schema_extra={
            "description": "A float representing the discharge efficiency of the battery."
        },
    )


class BaseBatteryParameters(DeviceParameters):
    """Battery Device Simulation Configuration."""

    device_id: str = Field(
        json_schema_extra={"description": "ID of battery", "examples": ["battery1"]}
    )
    capacity_wh: int = Field(
        gt=0,
        json_schema_extra={
            "description": "An integer representing the capacity of the battery in watt-hours.",
            "examples": [8000],
        },
    )
    charging_efficiency: float = Field(
        default=0.88,
        gt=0,
        le=1,
        json_schema_extra={
            "description": "A float representing the charging efficiency of the battery."
        },
    )
    discharging_efficiency: float = discharging_efficiency_field(0.88)
    max_charge_power_w: Optional[float] = max_charging_power_field()
    initial_soc_percentage: int = initial_soc_percentage_field(
        "An integer representing the state of charge of the battery at the **start** of the current hour (not the current state)."
    )
    min_soc_percentage: int = Field(
        default=0,
        ge=0,
        le=100,
        json_schema_extra={
            "description": "An integer representing the minimum state of charge (SOC) of the battery in percentage.",
            "examples": [10],
        },
    )
    max_soc_percentage: int = Field(
        default=100,
        ge=0,
        le=100,
        json_schema_extra={
            "description": "An integer representing the maximum state of charge (SOC) of the battery in percentage."
        },
    )
    charge_rates: Optional[list[float]] = Field(
        default=None,
        json_schema_extra={
            "description": "Charge rates as factor of maximum charging power [0.00 ... 1.00]. None denotes all charge rates are available.",
            "examples": [[0.0, 0.25, 0.5, 0.75, 1.0], None],
        },
    )


class SolarPanelBatteryParameters(BaseBatteryParameters):
    """PV battery device simulation configuration."""

    levelized_cost_of_storage_kwh: float = Field(
        default=0.0,
        ge=0.0,
        json_schema_extra={
            "description": (
                "Levelized cost of storage applied once to each kWh delivered "
                "by the battery [EUR/kWh]."
            ),
            "examples": [0.12],
        },
    )
    max_charge_power_w: Optional[float] = max_charging_power_field()


class ElectricVehicleParameters(BaseBatteryParameters):
    """Battery Electric Vehicle Device Simulation Configuration."""

    device_id: str = Field(
        json_schema_extra={"description": "ID of electric vehicle", "examples": ["ev1"]}
    )
    discharging_efficiency: float = discharging_efficiency_field(1.0)
    initial_soc_percentage: int = initial_soc_percentage_field(
        "An integer representing the current state of charge (SOC) of the battery in percentage."
    )


class HomeApplianceParameters(DeviceParameters):
    """Flexible consumer (home appliance) device simulation configuration.

    A consumer's load is defined **either** by an explicit power profile
    (``load_profile_power_w`` with an optional ``load_profile_interval_seconds``)
    **or** by the flat fallback ``consumption_wh`` + ``duration_h``. Exactly one
    of the two must be provided.
    """

    device_id: str = Field(
        json_schema_extra={"description": "ID of home appliance", "examples": ["dishwasher1"]}
    )
    load_profile_power_w: Optional[list[float]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Explicit load profile describing a single complete run as a "
                "sequence of non-negative power values in watts. Each value "
                "covers 'load_profile_interval_seconds'. Mutually exclusive with "
                "consumption_wh/duration_h."
            ),
            "examples": [[200.0, 2000.0, 1800.0, 100.0]],
        },
    )
    load_profile_interval_seconds: Optional[int] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": (
                "Duration of one 'load_profile_power_w' step in seconds. Defaults "
                "to the configured optimization interval when a profile is given."
            ),
            "examples": [900, 3600],
        },
    )
    schedule_mode: ConsumerScheduleMode = Field(
        default=ConsumerScheduleMode.ONCE,
        json_schema_extra={
            "description": (
                "Scheduling mode: ONCE (a single run within the horizon) or DAILY "
                "(one run per local calendar day with a feasible full run)."
            ),
            "examples": ["ONCE", "DAILY"],
        },
    )
    consumption_wh: Optional[int] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": (
                "Flat fallback: total energy consumption of one run in watt-hours. "
                "Used only when no load_profile_power_w is given."
            ),
            "examples": [2000],
        },
    )
    duration_h: Optional[int] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": (
                "Flat fallback: run duration in hours. Used only when no "
                "load_profile_power_w is given."
            ),
            "examples": [3],
        },
    )
    time_windows: Optional[TimeWindowSequence] = Field(
        default=None,
        json_schema_extra={
            "description": "List of allowed time windows. Defaults to optimization general time window.",
            "examples": [
                [
                    {"start_time": "10:00", "duration": "3 hours"},
                ],
            ],
        },
    )

    @model_validator(mode="after")
    def validate_load_definition(self) -> Self:
        """Ensure exactly one complete, valid load definition is provided."""
        validate_home_appliance_load_definition(
            load_profile_power_w=self.load_profile_power_w,
            load_profile_interval_seconds=self.load_profile_interval_seconds,
            consumption_wh=self.consumption_wh,
            duration_h=self.duration_h,
        )
        return self


class InverterParameters(DeviceParameters):
    """Inverter Device Simulation Configuration."""

    device_id: str = Field(
        json_schema_extra={"description": "ID of inverter", "examples": ["inverter1"]}
    )
    max_power_wh: float = Field(gt=0, json_schema_extra={"examples": [10000]})
    battery_id: Optional[str] = Field(
        default=None,
        json_schema_extra={"description": "ID of battery", "examples": [None, "battery1"]},
    )
    ac_to_dc_efficiency: float = Field(
        default=1.0,
        ge=0,
        le=1,
        json_schema_extra={
            "description": (
                "Efficiency of AC to DC conversion (for AC/grid charging of battery). "
                "Set to 0 to disable AC charging via inverter. "
                "Default 1.0 for backward compatibility (no additional inverter loss)."
            ),
            "examples": [0.95, 1.0, 0.0],
        },
    )
    dc_to_ac_efficiency: float = Field(
        default=1.0,
        gt=0,
        le=1,
        json_schema_extra={
            "description": (
                "Efficiency of DC to AC conversion (for battery discharging to AC load/grid). "
                "Default 1.0 for backward compatibility (no additional inverter loss)."
            ),
            "examples": [0.95, 1.0],
        },
    )
    max_ac_charge_power_w: Optional[float] = Field(
        default=None,
        ge=0,
        json_schema_extra={
            "description": (
                "Maximum AC charging power in watts. "
                "None means no additional limit (battery's own max_charge_power_w applies). "
                "Set to 0 to disable AC charging."
            ),
            "examples": [None, 0, 5000],
        },
    )
