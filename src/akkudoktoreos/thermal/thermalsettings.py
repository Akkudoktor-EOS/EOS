"""Optional thermal shadow-planning settings, separate from device communication."""

from typing import Any, Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from akkudoktoreos.config.configabc import ConfigScope, SettingsBaseModel
from akkudoktoreos.thermal.thermaldistribution import ThermalDistributionSettings
from akkudoktoreos.thermal.thermalmodels import (
    FiniteNumber,
    ThermalBuildingRC2Parameters,
    ThermalComfortBand,
    ThermalRC2Parameters,
)
from akkudoktoreos.thermal.thermaltracking import ThermalTrackingSettings


class ThermalDistributionMeasurementBindings(SettingsBaseModel):
    """Explicit temperature channels for physical distribution-state meanings.

    A water-loop state is not automatically a supply or return thermometer.
    Bind a sensor only when its meaning matches the calibrated model node.
    """

    model_config = ConfigDict(extra="forbid")

    air_temperature_measurement_key: str | None = None
    water_temperature_measurement_key: str | None = None
    emitter_temperature_measurement_key: str | None = None
    buffer_temperature_measurement_key: str | None = None

    @field_validator("*")
    @classmethod
    def require_nonblank_keys(cls, value: str | None) -> str | None:
        """Keep supplied channel identifiers nonblank without rewriting them."""
        if value is not None and not value.strip():
            raise ValueError("Distribution temperature measurement keys must not be blank")
        return value


class ThermalDistributionObserverSettings(SettingsBaseModel):
    """Opt-in solid/slab observer with an explicitly accepted initial-state width.

    This limit bounds only propagated initial-state uncertainty under the
    configured physical parameters, not measurement noise or model error.
    The zone's minimum state-history duration also applies to this observer.
    """

    model_config = ConfigDict(extra="forbid")

    maximum_emitter_uncertainty_c: FiniteNumber = Field(gt=0)
    maximum_steps: int = Field(default=20000, gt=0, le=200000, strict=True)


class ThermalZoneSettings(SettingsBaseModel):
    """Describe a staged room zone and its measurement bindings.

    Enabling a zone requires an explicit comfort band, room measurement key and
    model reference. It does not enable thermal planning or send a command.
    """

    model_config = ConfigDict(extra="forbid")

    zone_id: str = Field(min_length=1, description="Stable ID matching the zones map key.")
    enabled: bool = Field(
        default=False, description="Whether this staged zone is complete for use."
    )
    room_temperature_measurement_key: str | None = Field(
        default=None, description="Room temperature measurement key, in degrees Celsius."
    )
    presence_measurement_key: str | None = Field(
        default=None, description="Optional presence measurement key; missing data is not absence."
    )
    presence_area_id: str | None = Field(
        default=None, description="Optional area in the native learned presence provider."
    )
    model_reference: str | None = Field(
        default=None, description="Opaque thermal model identifier; never imported or loaded here."
    )
    model_parameters: ThermalBuildingRC2Parameters | ThermalRC2Parameters | None = Field(
        default=None,
        description="Optional calibrated model; otherwise estimate from native history.",
    )
    outdoor_temperature_measurement_key: str | None = None
    state_history_hours: int = Field(default=48, ge=1, le=168, strict=True)
    minimum_state_history_hours: int = Field(default=12, ge=1, le=168, strict=True)
    state_room_observation_window_seconds: int | None = Field(
        default=None,
        ge=300,
        le=3600,
        strict=True,
        description=(
            "Optional: use the mean of the measured room values over this window before the "
            "newest measurement as the room start value, instead of the single newest raw "
            "value (for sensors that report in coarse steps or bursts). Null keeps the raw "
            "value. Not available with an air/water distribution state model."
        ),
    )
    recovery_max_heating_rate_kelvin_per_hour: FiniteNumber | None = Field(
        default=None,
        gt=0,
        le=20,
        description=(
            "Optional: when the room starts below the comfort minimum, the initial recovery "
            "aims at a rise of at most this many kelvin per hour (raised automatically if "
            "the minimum could not be reached in time). Null keeps the immediate-target "
            "recovery at full available power."
        ),
    )
    comfort: ThermalComfortBand | None = Field(
        default=None, description="Explicit default room comfort band; no temperature is invented."
    )
    target_measurement_key: str | None = Field(
        default=None,
        description=(
            "Optional measurement channel with the room target [degC] the user set at the "
            "controller. While a fresh value (not older than target_max_age_seconds, between "
            "the absence minimum and 24 degC) exists, the occupied comfort band moves with it: "
            "target = value, minimum shifted by the same difference, maximum at least the target. "
            "Without a fresh value the configured comfort band applies unchanged."
        ),
    )
    target_max_age_seconds: int = Field(
        default=14400,
        ge=300,
        le=86400,
        strict=True,
        description="Maximum age of the room target measurement [s].",
    )
    unoccupied_comfort: ThermalComfortBand | None = Field(
        default=None, description="Optional absence bounds; timing comes from runtime presence."
    )
    absence_minimum_temperature_c: FiniteNumber | None = Field(
        default=None,
        ge=5,
        le=25,
        description=(
            "Optional: lowest room temperature while a REPORTED absence (presence.absences or "
            "the plan request) runs; always respected. Null uses unoccupied_comfort when "
            "configured, else 14 degrees. Does not change learned reductions."
        ),
    )
    allow_warm_coasting: bool = Field(
        default=False,
        strict=True,
        description=(
            "Explicitly permit occupied-range warmth during absence, retaining the absence "
            "minimum and target. False keeps the configured absence upper bound hard."
        ),
    )
    distribution_measurements: ThermalDistributionMeasurementBindings = Field(
        default_factory=ThermalDistributionMeasurementBindings
    )
    distribution_observer: ThermalDistributionObserverSettings | None = None
    distribution: ThermalDistributionSettings | None = None

    @field_validator(
        "zone_id",
        "room_temperature_measurement_key",
        "presence_measurement_key",
        "presence_area_id",
        "model_reference",
        "outdoor_temperature_measurement_key",
    )
    @classmethod
    def require_nonblank_identifiers(cls, value: str | None) -> str | None:
        """Reject blank identifiers without rewriting external measurement keys."""
        if value is not None and not value.strip():
            raise ValueError("Thermal identifiers must not be blank")
        return value

    @model_validator(mode="after")
    def require_enabled_zone_inputs(self) -> Self:
        """Require the declared inputs of an enabled zone without fetching them."""
        if self.enabled and (
            self.room_temperature_measurement_key is None
            or self.comfort is None
            or self.model_reference is None
        ):
            raise ValueError(
                "Enabled thermal zones require room_temperature_measurement_key, "
                "comfort and model_reference"
            )
        kind = self.distribution.kind if self.distribution is not None else "direct"
        if self.state_room_observation_window_seconds is not None and kind != "direct":
            raise ValueError(
                "A room observation window is only available without a distribution state model"
            )
        bindings = self.distribution_measurements
        keys = bindings.model_dump()
        allowed = {
            "direct": set(),
            "air": {"air_temperature_measurement_key"},
            "water": {
                "water_temperature_measurement_key",
                "emitter_temperature_measurement_key",
                "buffer_temperature_measurement_key",
            },
        }[kind]
        if any(value is not None and key not in allowed for key, value in keys.items()):
            raise ValueError(
                "Distribution measurement bindings must match the selected state model"
            )
        if self.distribution_observer is not None and kind != "water":
            raise ValueError("An emitter observer requires a water distribution")
        if (
            kind == "water"
            and getattr(self.distribution, "buffer", None) is None
            and bindings.buffer_temperature_measurement_key is not None
        ):
            raise ValueError("A buffer measurement binding requires a configured buffer state")
        if (
            kind != "direct"
            and self.model_parameters is not None
            and not isinstance(self.model_parameters, ThermalBuildingRC2Parameters)
        ):
            raise ValueError(
                "Air/water distribution requires a source-independent thermal building model"
            )
        return self


class ThermalCommonSettings(SettingsBaseModel):
    """Optional thermal settings; live control remains reserved.

    Runtime measurements, presence state, manual overrides and actuator targets
    are intentionally not persisted in this configuration model.
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["off", "shadow", "control"] = Field(
        default="off",
        description=(
            "Off preserves normal planning; shadow permits only the separate thermal API. "
            "Live control is not implemented."
        ),
        json_schema_extra={"x-scope": [str(ConfigScope.UNUSED)]},
    )
    zones: dict[str, ThermalZoneSettings] = Field(default_factory=dict)
    tracking: ThermalTrackingSettings = Field(
        default_factory=ThermalTrackingSettings,
        description="Separate disabled-by-default room-target pilot; never executes the full plan.",
    )
    planning_time_limit_seconds: FiniteNumber = Field(
        default=60.0,
        gt=0,
        le=3600,
        description=(
            "Cooperative total optimization budget, including all thermal planning stages; "
            "not a hard process timeout or the native-data resolver duration."
        ),
    )
    electric_power_block_seconds: int | None = Field(
        default=1800,
        ge=900,
        multiple_of=900,
        strict=True,
        description="Optional hard constant-electric-power blocks; null lets electricity follow the physical heat-pump map each slot. Not an actuator dwell time.",
    )
    solver_process_memory_limit_mb: int = Field(
        default=1024,
        ge=128,
        le=8192,
        strict=True,
        description="Isolated solver memory limit; Linux address-space cap plus sampled process-tree RSS.",
    )
    solver_process_grace_seconds: FiniteNumber = Field(
        default=5.0,
        gt=0,
        le=30,
        description="Startup/transport allowance beyond the cooperative planning budget; then kill worker.",
    )
    pattern_polish_seconds: FiniteNumber = Field(
        default=0.0,
        ge=0,
        le=60,
        description=(
            "Seconds of the joint stage spent on exact local improvement of the heat-source "
            "on/off pattern (short pauses/bursts); 0 disables it (previous behaviour)."
        ),
    )
    plan_ttl_seconds: int = Field(
        default=1800, gt=0, description="Configured future plan lease in seconds."
    )
    absence_delay_seconds: int = Field(
        default=1800,
        gt=0,
        description="Required continuous valid absence before an absence policy.",
    )
    measurement_max_age_seconds: int = Field(
        default=180, gt=0, description="Maximum age of room measurements in seconds."
    )
    presence_max_age_seconds: int = Field(
        default=90, gt=0, description="Maximum age of valid presence data in seconds."
    )

    @field_validator(
        "plan_ttl_seconds",
        "absence_delay_seconds",
        "measurement_max_age_seconds",
        "presence_max_age_seconds",
        mode="before",
    )
    @classmethod
    def reject_boolean_durations(cls, value: Any) -> Any:
        """Keep booleans from silently becoming one-second durations."""
        if isinstance(value, bool):
            raise ValueError("Thermal durations must be positive numbers of seconds")
        return value

    @field_validator("zones", mode="before")
    @classmethod
    def validate_zone_ids(cls, value: Any) -> Any:
        """Populate missing zone IDs from map keys without mutating caller data."""
        if not isinstance(value, dict):
            return value
        result = {}
        for key, zone in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("Thermal zone map keys must be nonblank strings")
            if isinstance(zone, dict):
                zone = dict(zone)
                zone.setdefault("zone_id", key)
                zone_id = zone["zone_id"]
            elif isinstance(zone, ThermalZoneSettings):
                zone_id = zone.zone_id
            else:
                result[key] = zone
                continue  # Pydantic reports the invalid zone value's type.
            if zone_id != key:
                raise ValueError(f"zone_id {zone_id!r} must match map key {key!r}")
            result[key] = zone
        return result


def require_thermal_control_disabled(settings: ThermalCommonSettings) -> None:
    """Keep ordinary planning electrical in off/shadow; reject live thermal control."""
    if settings.mode == "control":
        raise ValueError("Live thermal control is not implemented; use thermal.mode=off or shadow")
