"""Physical settings for staged electric room heat sources; no actuator access."""

from typing import Any, Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.devices.heatpump import (
    HeatPumpDefrostSettings,
    HeatPumpElectricalSettings,
    HeatPumpPresetId,
    HeatPumpProfile,
    WaterHeatingCurve,
    get_heat_pump_preset,
)
from akkudoktoreos.devices.settings.devicebasesettings import DevicesBaseSettings


class HeatPumpConfiguration(SettingsBaseModel):
    """Select a reusable catalog map or a custom map, without device communication.

    Delivery temperature is water flow for air-to-water systems and the indoor
    rating condition for air-to-air systems. A runtime planner may instead
    supply a forecast of delivery temperatures; no 35 C value is assumed.
    """

    model_config = ConfigDict(extra="forbid")
    preset: HeatPumpPresetId | None = None
    custom_profile: HeatPumpProfile | None = None
    delivery_temperature_c: float | None = Field(default=None, allow_inf_nan=False)
    delivery_temperature_measurement_key: str | None = None
    source_temperature_c: float | None = Field(default=None, allow_inf_nan=False)
    source_temperature_measurement_key: str | None = None
    defrost: HeatPumpDefrostSettings = Field(default_factory=HeatPumpDefrostSettings)
    electrical: HeatPumpElectricalSettings = Field(default_factory=HeatPumpElectricalSettings)
    heating_curve: WaterHeatingCurve | None = None

    @model_validator(mode="after")
    def validate_map_selection(self) -> Self:
        """Require exactly one explicit, reproducible performance-data source."""
        if (self.preset is None) == (self.custom_profile is None):
            raise ValueError("Select exactly one heat-pump preset or custom profile")
        if (
            self.delivery_temperature_measurement_key is not None
            and not self.delivery_temperature_measurement_key.strip()
        ):
            raise ValueError("Delivery-temperature measurement key must not be blank")
        return self

    def resolve_profile(self) -> HeatPumpProfile:
        """Return an independent profile from the selected map source."""
        if self.custom_profile is not None:
            return HeatPumpProfile.model_validate(self.custom_profile.model_dump())
        if self.preset is None:
            raise ValueError("No heat-pump performance map selected")
        return get_heat_pump_preset(self.preset)


class HeatSourceSettings(DevicesBaseSettings):
    """Describe electrical limits and zone bindings without controlling a heater.

    Power is electrical input in watts, not delivered heat. No efficiency, COP,
    device protocol or learned-model defaults are inferred by this model.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["electric_room", "heat_pump"] = "electric_room"
    heat_pump: HeatPumpConfiguration | None = None
    enabled: bool = False
    max_electric_power_w: float = Field(
        gt=0, allow_inf_nan=False, description="Explicit maximum electrical input power in watts."
    )
    min_electric_power_w: float = Field(
        default=0,
        ge=0,
        allow_inf_nan=False,
        description="Minimum active electrical input power in watts; zero leaves it unspecified.",
    )
    electric_power_measurement_key: str | None = Field(
        default=None, description="Optional source electrical power measurement key, in watts."
    )
    thermal_power_measurement_key: str | None = None
    thermal_power_is_estimated: bool = Field(default=False, strict=True)
    thermal_power_idle_electric_threshold_w: float | None = Field(
        default=None, ge=0, allow_inf_nan=False
    )
    idle_power_tolerance_w: float = Field(
        default=0,
        ge=0,
        allow_inf_nan=False,
        description="Explicit negative idle-noise tolerance; raw observations are preserved.",
    )
    zone_ids: list[str] = Field(
        default_factory=list, description="Unique thermal zone IDs supplied by this source."
    )

    @field_validator("max_electric_power_w", "min_electric_power_w", mode="before")
    @classmethod
    def reject_boolean_power(cls, value: Any) -> Any:
        """Reject a boolean in place of a physical electrical power value."""
        if isinstance(value, bool):
            raise ValueError("Heat source power must be a finite number of watts")
        return value

    @field_validator("device_id", "electric_power_measurement_key", "thermal_power_measurement_key")
    @classmethod
    def require_nonblank_identifiers(cls, value: str | None) -> str | None:
        """Keep source IDs and supplied measurement keys nonblank."""
        if value is not None and not value.strip():
            raise ValueError("Heat source identifiers must not be blank")
        return value

    @field_validator("zone_ids")
    @classmethod
    def require_unique_zone_ids(cls, value: list[str]) -> list[str]:
        """Reject duplicate or blank zone bindings, including staged sources."""
        if any(not zone_id.strip() for zone_id in value):
            raise ValueError("Heat source zone IDs must not be blank")
        if len(set(value)) != len(value):
            raise ValueError("Heat source zone IDs must be unique")
        return value

    @model_validator(mode="after")
    def validate_source_limits(self) -> Self:
        """Check local physical limits; cross-zone references belong to ConfigEOS."""
        if self.thermal_power_idle_electric_threshold_w is not None and (
            not self.thermal_power_measurement_key or not self.thermal_power_is_estimated
        ):
            raise ValueError(
                "Electrical idle gating requires an explicitly estimated thermal channel"
            )
        if self.min_electric_power_w > self.max_electric_power_w:
            raise ValueError("Minimum electric power must not exceed maximum electric power")
        if self.enabled and not self.zone_ids:
            raise ValueError("Enabled heat sources require at least one zone ID")
        if (self.kind == "heat_pump") != (self.heat_pump is not None):
            raise ValueError("Heat-pump sources require a heat_pump map configuration")
        return self
