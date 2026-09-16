"""Controllable home appliance device settings.

Note: Used for the GENETIC and GENETIC0 algorithm.
"""

from typing import TYPE_CHECKING, Any, Optional, Self

from pydantic import Field, computed_field, field_validator, model_validator

from akkudoktoreos.config.configabc import (
    ConfigScope,
    CycleTimeWindowSequence,
    TimeWindowSequence,
)
from akkudoktoreos.devices.devicesabc import (
    ConsumerDeadlinePolicy,
    ConsumerScheduleMode,
    validate_home_appliance_load_definition,
)
from akkudoktoreos.devices.settings.devicebasesettings import DevicesBaseSettings
from akkudoktoreos.utils.datetimeutil import DateTime, compare_datetimes, to_datetime

if TYPE_CHECKING:
    from akkudoktoreos.devices.genetic0.genetic0homeappliance import (
        Genetic0HomeApplianceParameters,
    )
    from akkudoktoreos.devices.genetic.homeappliance import HomeApplianceParameters


class HomeApplianceCommonSettings(DevicesBaseSettings):
    """Controllable home appliance device settings.

    Represents a shiftable load whose start time — and optionally the
    start times for multiple sequential runs — can be deferred by the
    optimiser within per-cycle allowed time windows.

    The number of remaining cycles to plan is determined at runtime by
    reading ``cycles_completed_measurement_key`` from the measurement
    store inside ``HomeApplianceDevice.setup_run``.

    ``num_cycles`` is required when ``cycle_time_windows`` is ``None``
    (unconstrained); when windows are provided it is derived from
    ``cycle_time_windows.num_cycles()``.

    Single-cycle, unconstrained (start any time)
    ---------------------------------------------

    ::

        device_id: dishwasher
        consumption_wh: 1500
        duration_h: 2
        num_cycles: 1
        ports:
          - port_id: p_ac
            bus_id: bus_ac
            direction: sink

    Single-cycle, constrained to one window
    ----------------------------------------

    Each window's ``value`` field carries the **cycle index** (0-based).
    Windows without a ``value`` are ignored by the optimizer::

        device_id: dishwasher
        consumption_wh: 1500
        duration_h: 2
        ports:
          - port_id: p_ac
            bus_id: bus_ac
            direction: sink
        cycle_time_windows:
          windows:
            - start_time: "10:00"
              duration: "12 hours"
              value: 0

    Multi-cycle, per-cycle windows
    --------------------------------

    Two cycles, each with its own window.  Cycle 0 runs in the morning,
    cycle 1 in the evening::

        device_id: washing_machine
        consumption_wh: 2000
        duration_h: 2
        min_cycle_gap_h: 1
        ports:
          - port_id: p_ac
            bus_id: bus_ac
            direction: sink
        cycle_time_windows:
          windows:
            - start_time: "07:00"
              duration: "5 hours"
              value: 0
            - start_time: "17:00"
              duration: "5 hours"
              value: 1

    Multi-cycle, shared window (both cycles may run any time 10:00-20:00)
    -----------------------------------------------------------------------

    Assign the same-shaped windows to distinct cycle indices so the
    optimizer can place them independently::

        cycle_time_windows:
          windows:
            - start_time: "10:00"
              duration: "10 hours"
              value: 0
            - start_time: "10:00"
              duration: "10 hours"
              value: 1

    """

    consumption_wh: Optional[int] = Field(
        default=3000,
        gt=0,
        json_schema_extra={
            "description": "Energy consumption per run cycle [Wh].",
            "examples": [2000],
            "x-scope": [str(ConfigScope.GENETIC), str(ConfigScope.GENETIC0)],
        },
    )
    duration_h: Optional[int] = Field(
        default=3,
        gt=0,
        le=24,
        json_schema_extra={
            "description": "Run duration per cycle [h] (1-24).",
            "examples": [2],
            "x-scope": [str(ConfigScope.GENETIC), str(ConfigScope.GENETIC0)],
        },
    )
    num_cycles: int = Field(
        default=1,
        ge=1,
        json_schema_extra={
            "description": (
                "Number of times the appliance must run within the horizon. "
                "Required when cycle_time_windows is null (unconstrained). "
                "Ignored when cycle_time_windows is provided -- the number "
                "of distinct cycle indices in the windows defines num_cycles. "
                "Defaults to 1."
            ),
            "examples": [1, 2],
            "x-scope": [str(ConfigScope.GENETIC), str(ConfigScope.GENETIC0)],
        },
    )
    cycle_time_windows: Optional[CycleTimeWindowSequence] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Per-cycle allowed scheduling time windows. "
                "Each window's value field specifies the cycle index (0-based). "
                "When null, the appliance may start at any step and num_cycles "
                "must be set explicitly."
            ),
            "examples": [
                None,
                {
                    "windows": [
                        {"start_time": "07:00", "duration": "5 hours", "value": 0},
                        {"start_time": "17:00", "duration": "5 hours", "value": 1},
                    ]
                },
            ],
            "x-scope": [
                str(ConfigScope.GENETIC),
            ],
        },
    )
    min_cycle_gap_h: int = Field(
        default=0,
        ge=0,
        json_schema_extra={
            "description": (
                "Minimum idle time between the end of one cycle and the start "
                "of the next [h]. Applies uniformly between all consecutive cycles. "
                "0 means back-to-back runs are permitted."
            ),
            "examples": [0, 1, 4],
            "x-scope": [
                str(ConfigScope.GENETIC),
            ],
        },
    )
    cycles_completed_measurement_key: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Measurement store key holding the number of cycles already "
                "completed in the current planning day. Read by "
                "HomeApplianceDevice.setup_run via context.resolve_measurement. "
                "Defaults to '{device_id}.cycles_completed' when null."
            ),
            "examples": ["dishwasher.cycles_completed", None],
            "x-scope": [
                str(ConfigScope.GENETIC),
            ],
        },
    )

    @model_validator(mode="before")
    @classmethod
    def _profile_replaces_flat_defaults(cls, value: Any) -> Any:
        """Keep historical flat defaults unless an explicit profile is supplied."""
        if isinstance(value, dict) and value.get("load_profile_power_w") is not None:
            value = dict(value)
            value.setdefault("consumption_wh", None)
            value.setdefault("duration_h", None)
        return value

    load_profile_power_w: Optional[list[float]] = Field(
        default=None,
        json_schema_extra={
            "x-scope": [str(ConfigScope.GENETIC)],
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
            "x-scope": [str(ConfigScope.GENETIC)],
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
            "x-scope": [str(ConfigScope.GENETIC)],
            "description": (
                "Scheduling mode: ONCE (a single run within the horizon) or DAILY "
                "(one run per local calendar day with a feasible full run)."
            ),
            "examples": ["ONCE", "DAILY"],
        },
    )
    time_windows: Optional[TimeWindowSequence] = Field(
        default=None,
        json_schema_extra={
            "x-scope": [str(ConfigScope.GENETIC)],
            "description": "List of allowed time windows. Defaults to optimization general time window.",
            "examples": [
                [
                    {"start_time": "10:00", "duration": "3 hours"},
                ],
            ],
        },
    )
    earliest_start_datetime: Optional[DateTime] = Field(
        default=None,
        json_schema_extra={
            "x-scope": [str(ConfigScope.GENETIC)],
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
            "x-scope": [str(ConfigScope.GENETIC)],
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
            "x-scope": [str(ConfigScope.GENETIC)],
            "description": (
                "What to do when 'deadline_datetime' cannot be met: BEST_EFFORT "
                "runs as early as possible instead (warning logged), STRICT keeps "
                "the deadline (a ONCE consumer then fails the optimization)."
            ),
            "examples": ["BEST_EFFORT", "STRICT"],
        },
    )

    @field_validator("earliest_start_datetime", "deadline_datetime", mode="before")
    @classmethod
    def transform_to_datetime(cls, value: Any) -> Optional[DateTime]:
        """Accept the usual date time representations, naive input is local time."""
        if value is None:
            return None
        return to_datetime(value)

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

    @model_validator(mode="after")
    def _validate_num_cycles_specified(self) -> "HomeApplianceCommonSettings":
        """Require num_cycles when windows are not provided."""
        if self.cycle_time_windows is None and self.num_cycles is None:
            raise ValueError(
                "num_cycles must be set when cycle_time_windows is null. "
                "Provide either cycle_time_windows (windows define cycle count) "
                "or set num_cycles explicitly."
            )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_num_cycles(self) -> int:
        """Number of cycles as seen by the optimizer.

        Derived from cycle_time_windows.num_cycles() when windows are
        provided; falls back to the explicit num_cycles field otherwise.
        """
        if self.cycle_time_windows is not None:
            return self.cycle_time_windows.num_cycles()
        return self.num_cycles

    # ------------------------------------------------------------------
    # GENETIC domain conversion
    # ------------------------------------------------------------------

    def to_genetic_param(self) -> "HomeApplianceParameters":
        """Return HomeApplianceParameters for the GENETIC optimizer."""
        from akkudoktoreos.devices.genetic.homeappliance import HomeApplianceParameters

        return HomeApplianceParameters(
            device_id=self.device_id,
            consumption_wh=self.consumption_wh,
            duration_h=self.duration_h,
            num_cycles=self.effective_num_cycles,
            min_cycle_gap_h=self.min_cycle_gap_h,
            time_windows=self.cycle_time_windows,
            shared_time_windows=self.time_windows,
            load_profile_power_w=self.load_profile_power_w,
            load_profile_interval_seconds=self.load_profile_interval_seconds,
            schedule_mode=self.schedule_mode,
            earliest_start_datetime=self.earliest_start_datetime,
            deadline_datetime=self.deadline_datetime,
            deadline_policy=self.deadline_policy,
        )

    # ------------------------------------------------------------------
    # GENETIC0 domain conversion
    # ------------------------------------------------------------------

    def to_genetic0_param(self) -> "Genetic0HomeApplianceParameters":
        """Return Genetic0HomeApplianceParameters for the GENETIC0 optimizer."""
        from akkudoktoreos.devices.genetic0.genetic0homeappliance import (
            Genetic0HomeApplianceParameters,
        )

        if self.load_profile_power_w is not None:
            raise ValueError("Explicit appliance load profiles require the GENETIC optimizer.")
        if self.consumption_wh is None or self.duration_h is None:
            raise ValueError("GENETIC0 requires a flat appliance load definition.")
        return Genetic0HomeApplianceParameters(
            device_id=self.device_id,
            consumption_wh=self.consumption_wh,
            duration_h=self.duration_h,
            time_windows=self.cycle_time_windows,
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_keys(self) -> Optional[list[str]]:
        """Measurement keys for the home appliance stati that are measurements."""
        keys: list[str] = []
        return keys
