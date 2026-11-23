"""GENETIC request models (Pydantic): the public schema of what a run is given.

``GeneticRequest`` is the configuration-owned request of ``POST /v1/optimize``;
``geneticprepare`` completes it into the parameters of a run. The forecast, price and device
models are nested in it and in ``GeneticOptimizationParameters``, which the result carries as
the request it optimized. Each device model derives the frozen dataclass of the simulation.
"""

import math
from datetime import datetime
from typing import Annotated, Any, Optional, Union

from pydantic import (
    AliasChoices,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)
from typing_extensions import Self

from akkudoktoreos.config.configabc import (
    CycleTimeWindowSequence,
    TimeWindowSequence,
)
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.devices.devicesabc import (
    ConsumerDeadlinePolicy,
    ConsumerScheduleMode,
)
from akkudoktoreos.optimization.genetic.devices.geneticbattery import (
    SOC_TABLE_POINTS,
    GeneticBatteryParameters,
)
from akkudoktoreos.optimization.genetic.devices.genetichomeappliance import (
    GeneticDeadlinePolicy,
    GeneticHomeApplianceParameters,
    GeneticScheduleMode,
)
from akkudoktoreos.optimization.genetic.devices.geneticinverter import (
    GeneticInverterParameters,
)
from akkudoktoreos.optimization.genetic.devices.genetictimewindow import (
    GeneticTimeWindow,
)
from akkudoktoreos.optimization.genetic.geneticefficiency import (
    validate_efficiency_curve,
)
from akkudoktoreos.optimization.genetic.geneticenergymanagement import (
    GeneticEnergyManagementParameters,
)
from akkudoktoreos.utils.datetimeutil import (
    DateTime,
    compare_datetimes,
    to_datetime,
)


class GeneticParametersBaseModel(PydanticBaseModel):
    """Pydantic base model of the request and response models of GENETIC."""

    model_config = ConfigDict(extra="forbid")


class GeneticDeviceRequest(GeneticParametersBaseModel):
    """Base of the device request models: the device ID."""

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
    """The ``max_charge_power_w`` field of a battery request (default 5000 W, 0 allowed).

    Args:
        description: Field description; a generic one if None.

    Returns:
        The Pydantic field.
    """
    if description is None:
        description = "Maximum charging power in watts."
    # 0: the battery may not charge, e.g. the profile inhibits it at a low temperature.
    return Field(default=5000, ge=0, json_schema_extra={"description": description})


def initial_soc_percentage_field(description: str) -> int:
    """The ``initial_soc_percentage`` field of a battery request (0 ... 100 %).

    Args:
        description: Field description.

    Returns:
        The Pydantic field.
    """
    return Field(
        default=0, ge=0, le=100, json_schema_extra={"description": description, "examples": [42]}
    )


def discharging_efficiency_field(default_value: float) -> float:
    """The ``discharging_efficiency`` field of a battery request (0, 1].

    Args:
        default_value: Default efficiency.

    Returns:
        The Pydantic field.
    """
    return Field(
        default=default_value,
        gt=0,
        le=1,
        json_schema_extra={
            "description": "A float representing the discharge efficiency of the battery."
        },
    )


class GeneticBaseBatteryRequest(GeneticDeviceRequest):
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
    max_discharge_power_w: Optional[float] = Field(
        default=None,
        ge=0,
        json_schema_extra={
            "description": (
                "Maximum discharging power in watts. 0 means discharging is inhibited. "
                "None uses max_charge_power_w."
            ),
            "examples": [None, 4000],
        },
    )
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
    grid_export_rates: Optional[list[float]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Battery-to-grid export rates as factor of maximum discharge "
                "power ]0.00 ... 1.00]. These levels are available to algorithms "
                "that explicitly enable battery-to-grid export. None leaves the "
                "choice of export levels to the caller."
            ),
            "examples": [[0.25, 0.5, 0.75, 1.0], [1.0], None],
        },
    )
    charge_power_limit_w_by_soc: Optional[list[float]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Maximum charging power [W] per state of charge, e.g. from a battery "
                f"profile. {SOC_TABLE_POINTS} values, index i is the limit at SoC i %. "
                "Further limits max_charge_power_w. None means no SoC dependent limit."
            ),
            "examples": [None],
        },
    )
    discharge_power_limit_w_by_soc: Optional[list[float]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Maximum discharging power [W] per state of charge, e.g. from a battery "
                f"profile. {SOC_TABLE_POINTS} values, index i is the limit at SoC i %. "
                "Further limits max_discharge_power_w. None means no SoC dependent limit."
            ),
            "examples": [None],
        },
    )
    charging_efficiency_curve: Optional[list[tuple[float, float]]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Power dependent charging efficiency as a list of (power_fraction, "
                "efficiency) points. The power fraction is the charging power relative "
                "to max_charge_power_w [0.0 ... 1.0]; points must be strictly increasing "
                "in power fraction, efficiencies in (0, 1]. Values are interpolated "
                "linearly and clamped to the first/last point. When set, the curve "
                "replaces charging_efficiency in the simulation. None means constant "
                "charging_efficiency."
            ),
            "examples": [None, [[0.0, 0.975], [0.5, 0.96], [1.0, 0.94]]],
        },
    )
    discharging_efficiency_curve: Optional[list[tuple[float, float]]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Power dependent discharging efficiency as a list of (power_fraction, "
                "efficiency) points. The power fraction is the discharging power "
                "relative to max_discharge_power_w [0.0 ... 1.0]; same rules as "
                "charging_efficiency_curve. When set, the curve replaces "
                "discharging_efficiency in the simulation. None means constant "
                "discharging_efficiency."
            ),
            "examples": [None, [[0.0, 0.975], [1.0, 0.96]]],
        },
    )

    @field_validator("charge_power_limit_w_by_soc", "discharge_power_limit_w_by_soc")
    @classmethod
    def _validate_power_limit_by_soc(cls, value: Optional[list[float]]) -> Optional[list[float]]:
        if value is None:
            return None
        if len(value) != SOC_TABLE_POINTS:
            raise ValueError(
                f"A power limit table needs {SOC_TABLE_POINTS} values (SoC 0 % ... 100 %), "
                f"got {len(value)}."
            )
        for power_w in value:
            if not math.isfinite(power_w) or power_w < 0:
                raise ValueError(f"Power limits must be finite and >= 0 W, got {power_w}.")
        return value

    @field_validator("charging_efficiency_curve", "discharging_efficiency_curve")
    @classmethod
    def _validate_efficiency_curve(
        cls, value: Optional[list[tuple[float, float]]]
    ) -> Optional[list[tuple[float, float]]]:
        return validate_efficiency_curve(value)

    def to_parameters(self) -> GeneticBatteryParameters:
        """The battery of the simulation: no storage cost and no lower SoC limit.

        Returns:
            The frozen battery parameters.
        """
        return self._battery_parameters(
            min_soc_percentage=0, levelized_cost_of_storage_kwh=0.0, electric_vehicle=False
        )

    def _battery_parameters(
        self,
        min_soc_percentage: int,
        levelized_cost_of_storage_kwh: float,
        electric_vehicle: bool,
    ) -> GeneticBatteryParameters:
        return GeneticBatteryParameters(
            device_id=self.device_id,
            capacity_wh=self.capacity_wh,
            charging_efficiency=self.charging_efficiency,
            discharging_efficiency=self.discharging_efficiency,
            max_charge_power_w=self.max_charge_power_w,
            max_discharge_power_w=self.max_discharge_power_w,
            initial_soc_percentage=self.initial_soc_percentage,
            min_soc_percentage=min_soc_percentage,
            max_soc_percentage=self.max_soc_percentage,
            charge_rates=_tuple_or_none(self.charge_rates),
            charge_power_limit_w_by_soc=_tuple_or_none(self.charge_power_limit_w_by_soc),
            discharge_power_limit_w_by_soc=_tuple_or_none(self.discharge_power_limit_w_by_soc),
            charging_efficiency_curve=_curve_or_none(self.charging_efficiency_curve),
            discharging_efficiency_curve=_curve_or_none(self.discharging_efficiency_curve),
            levelized_cost_of_storage_kwh=levelized_cost_of_storage_kwh,
            electric_vehicle=electric_vehicle,
        )


class GeneticBatteryRequest(GeneticBaseBatteryRequest):
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
    full_charge_soc_percentage: Optional[int] = Field(
        default=None,
        ge=1,
        le=100,
        json_schema_extra={
            "description": (
                "State of charge a due full charge has to reach at least once "
                "before 'full_charge_deadline_datetime' [%]. Must not exceed "
                "'max_soc_percentage'. None means no full charge is due."
            ),
            "examples": [None, 100],
        },
    )
    full_charge_deadline_datetime: Optional[DateTime] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Moment by which 'full_charge_soc_percentage' has to be reached. "
                "A date time without timezone is read as local time. None means "
                "end of the optimization horizon."
            ),
            "examples": [None, "2026-11-20T07:00:00+01:00"],
        },
    )

    @field_validator("full_charge_deadline_datetime", mode="before")
    @classmethod
    def transform_full_charge_deadline(cls, value: Any) -> Optional[DateTime]:
        """Accept the usual date time representations, naive input is local time.

        The deadline is held in UTC, so it renders the same on every host.
        """
        if value is None:
            return None
        return to_datetime(to_datetime(value), in_timezone="UTC")

    @model_validator(mode="after")
    def validate_full_charge_target(self) -> "GeneticBatteryRequest":
        """A full charge target above the maximum SoC could never be reached."""
        if (
            self.full_charge_soc_percentage is not None
            and self.full_charge_soc_percentage > self.max_soc_percentage
        ):
            raise ValueError("full_charge_soc_percentage must be <= max_soc_percentage")
        return self

    def to_parameters(self) -> GeneticBatteryParameters:
        """The stationary battery of the simulation, with its storage cost and SoC limits.

        Returns:
            The frozen battery parameters.
        """
        return self._battery_parameters(
            min_soc_percentage=self.min_soc_percentage,
            levelized_cost_of_storage_kwh=self.levelized_cost_of_storage_kwh,
            electric_vehicle=False,
        )


class GeneticElectricVehicleRequest(GeneticBaseBatteryRequest):
    """Battery Electric Vehicle Device Simulation Configuration."""

    device_id: str = Field(
        json_schema_extra={"description": "ID of electric vehicle", "examples": ["ev1"]}
    )
    discharging_efficiency: float = discharging_efficiency_field(1.0)
    initial_soc_percentage: int = initial_soc_percentage_field(
        "An integer representing the current state of charge (SOC) of the battery in percentage."
    )

    min_soc_deadline_datetime: Optional[DateTime] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Absolute moment by which 'min_soc_percentage' has to be "
                "reached (departure time). A date time without timezone is read "
                "as local time. None means end of the optimization horizon."
            ),
            "examples": [None, "2026-07-16T07:00:00+02:00"],
        },
    )
    min_soc_max_duration_h: Optional[float] = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        json_schema_extra={
            "description": (
                "Maximum time from the start of the optimization until "
                "'min_soc_percentage' has to be reached [h]. Combined with "
                "'min_soc_deadline_datetime' the earlier of the two applies."
            ),
            "examples": [None, 6.0],
        },
    )

    @field_validator("min_soc_deadline_datetime", mode="before")
    @classmethod
    def transform_deadline_to_datetime(cls, value: Any) -> Optional[DateTime]:
        """Accept the usual date time representations, naive input is local time.

        The deadline is held in UTC, so it renders the same on every host.
        """
        if value is None:
            return None
        return to_datetime(to_datetime(value), in_timezone="UTC")

    def to_parameters(self) -> GeneticBatteryParameters:
        """The vehicle battery of the simulation.

        Its ``min_soc_percentage`` is a charging target the algorithm rewards, not a lower limit
        of the simulation.

        Returns:
            The frozen battery parameters.
        """
        return self._battery_parameters(
            min_soc_percentage=0, levelized_cost_of_storage_kwh=0.0, electric_vehicle=True
        )


class GeneticInverterRequest(GeneticDeviceRequest):
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
    dc_to_ac_efficiency_curve: Optional[list[tuple[float, float]]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Load-dependent efficiency of DC to AC conversion as a list of "
                "(load_fraction, efficiency) points. The load fraction is the AC power "
                "of the conversion relative to max_power_wh [0.0 ... 1.0]; points must be "
                "strictly increasing in load fraction, efficiencies in (0, 1]. Values are "
                "interpolated linearly and clamped to the first/last point. When set, the "
                "curve replaces dc_to_ac_efficiency. "
                "Default None (constant dc_to_ac_efficiency)."
            ),
            "examples": [None, [[0.02, 0.8], [0.07, 0.93], [0.3, 0.96], [1.0, 0.95]]],
        },
    )
    dc_to_ac_efficiency_reference_load_fraction: float = Field(
        default=0.06,
        ge=0,
        le=1,
        json_schema_extra={
            "description": (
                "Load fraction at which dc_to_ac_efficiency_curve is evaluated where "
                "stored battery energy is valued without a specific conversion "
                "(e.g. remaining battery energy at the end of the horizon, AC charge "
                "break-even). Should represent the typical load the battery serves, "
                "e.g. the night-time base load. Only used with dc_to_ac_efficiency_curve. "
                "Default 0.06."
            ),
            "examples": [0.06, 0.1],
        },
    )
    ac_to_dc_efficiency_curve: Optional[list[tuple[float, float]]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Load-dependent efficiency of AC to DC conversion (AC/grid charging of the "
                "battery) as a list of (load_fraction, efficiency) points. The load "
                "fraction is the AC power drawn relative to max_power_wh [0.0 ... 1.0]; "
                "same rules as dc_to_ac_efficiency_curve. When set, the curve replaces "
                "ac_to_dc_efficiency for the energy drawn from the grid; "
                "ac_to_dc_efficiency still converts the AC charge power limit and should "
                "be the efficiency at that limit. Default None (constant "
                "ac_to_dc_efficiency)."
            ),
            "examples": [None, [[0.05, 0.86], [0.3, 0.96], [1.0, 0.96]]],
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
    ac_charge_limits_total_charge: bool = Field(
        default=False,
        json_schema_extra={
            "description": (
                "True if the AC charge setpoint caps the battery's total charge power, "
                "PV included. PV surplus above it is exported, not stored. False keeps "
                "the default model: PV surplus charges first and the grid adds "
                "ac_charge x max_charge_power_w on top."
            ),
            "examples": [False, True],
        },
    )

    @field_validator("dc_to_ac_efficiency_curve", "ac_to_dc_efficiency_curve")
    @classmethod
    def _validate_efficiency_curves(
        cls, value: Optional[list[tuple[float, float]]]
    ) -> Optional[list[tuple[float, float]]]:
        return validate_efficiency_curve(value)

    def to_parameters(self) -> GeneticInverterParameters:
        """The inverter of the simulation.

        Returns:
            The frozen inverter parameters.
        """
        return GeneticInverterParameters(
            device_id=self.device_id,
            max_power_wh=self.max_power_wh,
            battery_id=self.battery_id,
            ac_to_dc_efficiency=self.ac_to_dc_efficiency,
            dc_to_ac_efficiency=self.dc_to_ac_efficiency,
            dc_to_ac_efficiency_curve=_curve_or_none(self.dc_to_ac_efficiency_curve),
            dc_to_ac_efficiency_reference_load_fraction=(
                self.dc_to_ac_efficiency_reference_load_fraction
            ),
            ac_to_dc_efficiency_curve=_curve_or_none(self.ac_to_dc_efficiency_curve),
            max_ac_charge_power_w=self.max_ac_charge_power_w,
            ac_charge_limits_total_charge=self.ac_charge_limits_total_charge,
        )


class GeneticHomeApplianceRequest(GeneticDeviceRequest):
    """Configuration for a simulated home appliance device."""

    device_id: str = Field(
        json_schema_extra={
            "description": "ID of home appliance",
            "examples": ["dishwasher"],
        }
    )
    consumption_wh: Optional[int] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": (
                "An integer representing the energy consumption "
                "of a household device in watt-hours."
            ),
            "examples": [2000],
        },
    )
    duration_h: Optional[int] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": (
                "An integer representing the usage duration of a household device in hours."
            ),
            "examples": [3],
        },
    )
    num_cycles: int = Field(
        default=1,
        gt=0,
        json_schema_extra={
            "description": "Number of cycles the appliance must run.",
            "examples": [2],
        },
    )
    completed_cycles: int = Field(
        default=0, ge=0, description="Cycles already completed on the first planning day."
    )
    run_started_datetime: Optional[DateTime] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Start of the run in progress, None when the appliance is not "
                "running. The run is kept until the device reports it as finished: "
                "its remaining load is fixed from now on, independent of windows, "
                "deadline and gene, and it is not counted in 'completed_cycles' yet."
            ),
            "examples": [None, "2026-11-14T00:47:45+01:00"],
        },
    )

    min_cycle_gap_h: int = Field(
        default=0,
        ge=0,
        json_schema_extra={
            "description": (
                "Minimum idle time between the end of one cycle and the start of the next cycle."
            ),
            "examples": [1],
        },
    )
    time_windows: Optional[CycleTimeWindowSequence] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Allowed per-cycle time windows. Each window's `value` "
                "encodes the 0-based cycle index it applies to; multiple "
                "windows may share a cycle index. When omitted, every "
                "cycle is unconstrained across the full prediction "
                "horizon."
            ),
            "examples": [
                [
                    {
                        "start_time": "10:00",
                        "duration": "3 hours",
                        "value": 0,
                    },
                ],
            ],
        },
    )

    shared_time_windows: Optional[TimeWindowSequence] = Field(
        default=None,
        description="Allowed recurring windows shared by every cycle; intersected with per-cycle windows.",
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
    earliest_start_datetime: Optional[DateTime] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Absolute earliest moment the run may start. Starts before it are "
                "dropped, in addition to 'time_windows' and the horizon. A date "
                "time without timezone is read as local time. This bound is never "
                "relaxed."
            ),
            "examples": [None, "2026-07-15T20:00:00+02:00"],
        },
    )
    deadline_datetime: Optional[DateTime] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Absolute deadline: the complete run must have *finished* at or "
                "before this moment (e.g. end of the day, or 03:00 tonight). A "
                "date time without timezone is read as local time. See "
                "'deadline_policy' for what happens when no start can meet it."
            ),
            "examples": [None, "2026-07-16T03:00:00+02:00"],
        },
    )
    deadline_policy: ConsumerDeadlinePolicy = Field(
        default=ConsumerDeadlinePolicy.BEST_EFFORT,
        json_schema_extra={
            "description": (
                "What to do when 'deadline_datetime' cannot be met: BEST_EFFORT "
                "runs as early as possible instead (warning logged), STRICT keeps "
                "the deadline (a ONCE consumer then fails the optimization)."
            ),
            "examples": ["BEST_EFFORT", "STRICT"],
        },
    )

    @field_validator(
        "earliest_start_datetime", "deadline_datetime", "run_started_datetime", mode="before"
    )
    @classmethod
    def transform_to_datetime(cls, value: Any) -> Optional[DateTime]:
        """Accept the usual date time representations, naive input is local time.

        The date time is held in UTC, so it renders the same on every host.
        """
        if value is None:
            return None
        return to_datetime(to_datetime(value), in_timezone="UTC")

    @model_validator(mode="after")
    def validate_load_definition(self) -> Self:
        """Ensure a complete load definition and valid completed-cycle count."""
        if self.completed_cycles > self.num_cycles:
            raise ValueError("completed_cycles must not exceed num_cycles.")
        validate_home_appliance_load_definition(
            load_profile_power_w=self.load_profile_power_w,
            load_profile_interval_seconds=self.load_profile_interval_seconds,
            consumption_wh=self.consumption_wh,
            duration_h=self.duration_h,
        )
        return self

    @model_validator(mode="after")
    def validate_schedule_bounds(self) -> Self:
        """Reject an empty scheduling interval."""
        if self.earliest_start_datetime is not None and self.deadline_datetime is not None:
            if compare_datetimes(self.deadline_datetime, self.earliest_start_datetime).le:
                raise ValueError(
                    f"deadline_datetime {self.deadline_datetime} must be after "
                    f"earliest_start_datetime {self.earliest_start_datetime}."
                )
        return self

    def to_parameters(self) -> GeneticHomeApplianceParameters:
        """The home appliance of the simulation.

        Returns:
            The frozen home appliance parameters.
        """
        return GeneticHomeApplianceParameters(
            device_id=self.device_id,
            consumption_wh=self.consumption_wh,
            duration_h=self.duration_h,
            num_cycles=self.num_cycles,
            completed_cycles=self.completed_cycles,
            run_started_datetime=self.run_started_datetime,
            min_cycle_gap_h=self.min_cycle_gap_h,
            time_windows=_windows_or_none(self.time_windows),
            shared_time_windows=_windows_or_none(self.shared_time_windows),
            load_profile_power_w=_tuple_or_none(self.load_profile_power_w),
            load_profile_interval_seconds=self.load_profile_interval_seconds,
            schedule_mode=GeneticScheduleMode(str(self.schedule_mode)),
            earliest_start_datetime=self.earliest_start_datetime,
            deadline_datetime=self.deadline_datetime,
            deadline_policy=GeneticDeadlinePolicy(str(self.deadline_policy)),
        )


class GeneticEnergyManagementRequest(GeneticParametersBaseModel):
    """Encapsulates energy-related forecasts and costs used in GENETIC optimization."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    pv_forecast_wh: list[float] = Field(
        validation_alias=AliasChoices("pv_forecast_wh", "pv_prognose_wh"),
        json_schema_extra={
            "description": "An array of floats representing the forecasted photovoltaic energy in watt-hours per slot for different time intervals."
        },
    )
    electricity_price_per_wh: list[float] = Field(
        validation_alias=AliasChoices("electricity_price_per_wh", "strompreis_euro_pro_wh"),
        json_schema_extra={
            "description": "An array of floats representing the electricity price per watt-hour for different time intervals."
        },
    )
    feed_in_tariff_per_wh: Union[list[float], float] = Field(
        validation_alias=AliasChoices("feed_in_tariff_per_wh", "einspeiseverguetung_euro_pro_wh"),
        json_schema_extra={
            "description": "A float or array of floats representing the feed-in compensation per watt-hour."
        },
    )
    price_per_wh_battery: float = Field(
        validation_alias=AliasChoices("price_per_wh_battery", "preis_euro_pro_wh_akku"),
        json_schema_extra={
            "description": "A float representing the cost of battery energy per watt-hour."
        },
    )
    total_load: list[float] = Field(
        validation_alias=AliasChoices("total_load", "gesamtlast"),
        json_schema_extra={
            "description": "An array of floats representing the total load (consumption) in watt-hours per slot for different time intervals."
        },
    )

    # Computed fields for backward compatibility (deprecated German names)
    @computed_field(json_schema_extra={"deprecated": True})
    def pv_prognose_wh(self) -> list[float]:
        """Deprecated: Use pv_forecast_wh instead."""
        return self.pv_forecast_wh

    @computed_field(json_schema_extra={"deprecated": True})
    def strompreis_euro_pro_wh(self) -> list[float]:
        """Deprecated: Use electricity_price_per_wh instead."""
        return self.electricity_price_per_wh

    @computed_field(json_schema_extra={"deprecated": True})
    def einspeiseverguetung_euro_pro_wh(self) -> Union[list[float], float]:
        """Deprecated: Use feed_in_tariff_per_wh instead."""
        return self.feed_in_tariff_per_wh

    @computed_field(json_schema_extra={"deprecated": True})
    def preis_euro_pro_wh_akku(self) -> float:
        """Deprecated: Use price_per_wh_battery instead."""
        return self.price_per_wh_battery

    @computed_field(json_schema_extra={"deprecated": True})
    def gesamtlast(self) -> list[float]:
        """Deprecated: Use total_load instead."""
        return self.total_load

    @model_validator(mode="after")
    def validate_forecast_arrays(self) -> Self:
        """Require forecasts; the optimizer validates control coverage and clips the tail."""
        arrays = [self.pv_forecast_wh, self.electricity_price_per_wh, self.total_load]
        if isinstance(self.feed_in_tariff_per_wh, list):
            arrays.append(self.feed_in_tariff_per_wh)
        if any(not values for values in arrays):
            raise ValueError("Forecast arrays must not be empty.")
        return self

    def to_parameters(self) -> GeneticEnergyManagementParameters:
        """The forecasts and prices of the simulation.

        Returns:
            The frozen forecasts and prices.
        """
        tariff = self.feed_in_tariff_per_wh
        return GeneticEnergyManagementParameters(
            pv_forecast_wh=tuple(self.pv_forecast_wh),
            electricity_price_per_wh=tuple(self.electricity_price_per_wh),
            feed_in_tariff_per_wh=tuple(tariff) if isinstance(tariff, list) else tariff,
            price_per_wh_battery=self.price_per_wh_battery,
            total_load=tuple(self.total_load),
        )


class GeneticOptimizationParameters(GeneticParametersBaseModel):
    """Main parameter class for running the genetic energy optimization.

    Collects all model and configuration parameters necessary to run the
    optimization process, such as forecasts, pricing, battery and appliance models.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    ems: GeneticEnergyManagementRequest
    pv_battery: Optional[GeneticBatteryRequest] = Field(
        validation_alias=AliasChoices("pv_battery", "pv_akku"),
        json_schema_extra={"description": "PV battery parameters."},
    )
    inverter: Optional[GeneticInverterRequest]
    ev: Optional[GeneticElectricVehicleRequest] = Field(
        validation_alias=AliasChoices("ev", "eauto"),
        json_schema_extra={"description": "Electric vehicle parameters."},
    )
    dishwasher: Optional[GeneticHomeApplianceRequest] = None
    temperature_forecast: Optional[list[Optional[float]]] = Field(
        default=None,
        json_schema_extra={
            "description": "An array of floats representing the temperature forecast in degrees Celsius for different time intervals."
        },
    )
    start_solution: Optional[list[float]] = Field(
        default=None,
        json_schema_extra={
            "description": "Can be `null` or contain a previous solution (if available)."
        },
    )

    forecast_interval_seconds: Optional[int] = Field(
        default=None,
        description="Input interval: 3600 for hourly, or optimization interval for native slots.",
    )

    home_appliances: Optional[list[GeneticHomeApplianceRequest]] = Field(
        default=None,
        json_schema_extra={
            "description": "List of flexible consumers (home appliances) to schedule."
        },
    )

    start_solution_datetime: Optional[DateTime] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Start of the slot that gene 0 of 'start_solution' controls, as "
                "returned with the previous solution. The warm start is shifted by "
                "the slots that have elapsed until this run. Without it, a "
                "'start_solution' identical to the last solution of this server "
                "uses that solution's start; any other one is used unshifted."
            ),
            "examples": [None, "2026-09-14T07:45:00+02:00"],
        },
    )

    @field_validator("forecast_interval_seconds")
    @classmethod
    def validate_forecast_interval(cls, value: Optional[int]) -> Optional[int]:
        if value not in (None, 900, 3600):
            raise ValueError("forecast_interval_seconds must be 900 or 3600")
        return value

    @field_validator("start_solution_datetime", mode="before")
    @classmethod
    def transform_start_solution_datetime(cls, value: Any) -> Optional[DateTime]:
        """Accept the usual date time representations, naive input is local time."""
        if value is None:
            return None
        # A stored run must retain its timezone/fold even when the server host
        # uses another local timezone. ISO offsets survive JSON round trips.
        aware = value
        if isinstance(value, str):
            try:
                aware = datetime.fromisoformat(value)
            except ValueError:
                pass
        if isinstance(aware, datetime) and aware.tzinfo is not None:
            return DateTime.instance(aware)
        return to_datetime(value)

    @model_validator(mode="after")
    def validate_home_appliances(self) -> Self:
        """Reject conflicting home appliance definitions.

        The deprecated ``dishwasher`` field and the new ``home_appliances`` list
        must not be set at the same time; nothing is silently overwritten.
        Device ids within ``home_appliances`` must be unique.
        """
        # Read the deprecated field via __dict__ to avoid emitting a deprecation
        # warning on every internal validation.
        dishwasher = self.__dict__.get("dishwasher")
        if dishwasher is not None and self.home_appliances is not None:
            raise ValueError(
                "Provide either 'home_appliances' or the deprecated 'dishwasher', not both."
            )
        appliances = self.home_appliances or []
        device_ids = [appliance.device_id for appliance in appliances]
        if len(device_ids) != len(set(device_ids)):
            raise ValueError("home_appliances device_id values must be unique.")
        return self

    def resolved_home_appliances(self) -> list[GeneticHomeApplianceRequest]:
        """Return the effective home appliance list.

        Maps the deprecated single ``dishwasher`` onto a one-element list so the
        optimizer only ever deals with the list form.
        """
        if self.home_appliances is not None:
            return list(self.home_appliances)
        dishwasher = self.__dict__.get("dishwasher")
        if dishwasher is not None:
            return [dishwasher]
        return []

    # Computed fields for backward compatibility (deprecated German names)
    @computed_field(json_schema_extra={"deprecated": True})
    def pv_akku(self) -> Optional[GeneticBatteryRequest]:
        """Deprecated: Use pv_battery instead."""
        return self.pv_battery

    @computed_field(json_schema_extra={"deprecated": True})
    def eauto(self) -> Optional[GeneticElectricVehicleRequest]:
        """Deprecated: Use ev instead."""
        return self.ev

    @model_validator(mode="after")
    def validate_list_length(self) -> Self:
        """Ensure that temperature forecast list matches the PV forecast length.

        Raises:
            ValueError: If list lengths mismatch.
        """
        arr_length = len(self.ems.pv_forecast_wh)
        if self.temperature_forecast is not None and arr_length != len(self.temperature_forecast):
            raise ValueError("Input lists have different lengths")
        return self

    @field_validator("start_solution")
    def validate_start_solution(
        cls, start_solution: Optional[list[float]]
    ) -> Optional[list[float]]:
        """Validate that the starting solution has at least two elements.

        Args:
            start_solution (list[float]): Optional list of solution values.

        Returns:
            list[float]: Validated list.

        Raises:
            ValueError: If the solution is too short.
        """
        if start_solution is not None and len(start_solution) < 2:
            raise ValueError("Requires at least two values.")
        return start_solution


class GeneticForecastsRequest(GeneticParametersBaseModel):
    """External series from local midnight, in slot Wh and currency per Wh.

    The interval is configured in optimization.genetic.interval_sec. Omitted
    series are read from the configured providers, preserving their raw coverage.
    """

    pv_forecast_wh: Optional[list[float]] = Field(
        default=None, validation_alias=AliasChoices("pv_forecast_wh", "pv_prognose_wh")
    )
    total_load: Optional[list[float]] = Field(
        default=None, validation_alias=AliasChoices("total_load", "gesamtlast")
    )
    electricity_price_per_wh: Optional[list[float]] = Field(
        default=None,
        validation_alias=AliasChoices("electricity_price_per_wh", "strompreis_euro_pro_wh"),
    )
    feed_in_tariff_per_wh: Optional[list[float]] = Field(
        default=None,
        validation_alias=AliasChoices("feed_in_tariff_per_wh", "einspeiseverguetung_euro_pro_wh"),
    )
    temperature_forecast: Optional[list[Optional[float]]] = None


class GeneticRequest(GeneticParametersBaseModel):
    """Supply observations while hardware, tariffs and scheduling remain in config.

    Explicit SoC values take precedence over fresh measurements. Without a usable
    measurement, assume 0% initial SoC for the affected device and continue.
    """

    soc: dict[str, Annotated[int, Field(ge=0, le=100)]] = Field(default_factory=dict)
    forecasts: GeneticForecastsRequest = Field(default_factory=GeneticForecastsRequest)
    start_solution: Optional[list[float]] = None
    start_solution_datetime: Optional[DateTime] = None

    @field_validator("start_solution_datetime", mode="before")
    @classmethod
    def validate_start_solution_datetime(cls, value: Any) -> Optional[DateTime]:
        """Parse a previous-plan timestamp while retaining its explicit timezone."""
        return GeneticOptimizationParameters.transform_start_solution_datetime(value)


def _tuple_or_none(values: Optional[list[float]]) -> Optional[tuple[float, ...]]:
    return None if values is None else tuple(values)


def _curve_or_none(
    curve: Optional[list[tuple[float, float]]],
) -> Optional[tuple[tuple[float, float], ...]]:
    return None if curve is None else tuple((float(x), float(y)) for x, y in curve)


# GENETIC's own copy of the shared check (ADR 0004).
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


def _windows_or_none(
    sequence: Optional[TimeWindowSequence],
) -> Optional[tuple[GeneticTimeWindow, ...]]:
    """The windows of a request as simulation windows; ``None`` stays unconstrained."""
    if sequence is None:
        return None
    return tuple(
        GeneticTimeWindow(
            start_time=window.start_time,
            duration=window.duration,
            end_time=window.end_time,
            # The request validates a weekday name into its number.
            day_of_week=None if window.day_of_week is None else int(window.day_of_week),
            date=window.date,
            month=window.month,
            value=getattr(window, "value", None),
        )
        for window in sequence.windows
    )
