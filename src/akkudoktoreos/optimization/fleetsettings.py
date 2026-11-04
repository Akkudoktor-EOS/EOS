"""Persistent configuration for the shared AC-bus fleet optimizer."""

from typing import Literal

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel


class FleetDevicePolicy(SettingsBaseModel):
    """Additional fleet constraints keyed by an existing EOS device ID."""

    max_discharge_power_w: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    power_group: str | None = None


class FleetGroupSettings(SettingsBaseModel):
    """Shared AC charger or converter power ratings."""

    max_charge_power_w: float = Field(ge=0, allow_inf_nan=False)
    max_discharge_power_w: float = Field(default=0, ge=0, allow_inf_nan=False)


class FleetCommonSettings(SettingsBaseModel):
    """Fleet scheduling settings; device properties remain in devices.*."""

    interval_sec: Literal[900, 3600] = 900
    horizon_hours: int = Field(default=24, ge=1, le=96)
    import_limit_w: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    export_limit_w: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    prevent_export_at_negative_market_price: bool = False
    solver_time_limit_seconds: float = Field(default=30, gt=0, le=120, allow_inf_nan=False)
    measurement_max_age_seconds: int = Field(default=300, gt=0)
    terminal_value_eur_per_kwh: float = Field(default=0, ge=0, allow_inf_nan=False)
    allow_battery_export: bool = False
    devices: dict[str, FleetDevicePolicy] = Field(default_factory=dict)
    power_groups: dict[str, FleetGroupSettings] = Field(default_factory=dict)
