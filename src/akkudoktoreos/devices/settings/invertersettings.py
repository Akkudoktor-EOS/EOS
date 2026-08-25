"""Inverter device settings."""

from typing import TYPE_CHECKING, Optional

from pydantic import Field, computed_field, model_validator

from akkudoktoreos.config.configabc import ConfigScope
from akkudoktoreos.devices.settings.devicebasesettings import (
    DevicesBaseSettings,
)

if TYPE_CHECKING:
    from akkudoktoreos.devices.genetic0.genetic0inverter import (
        Genetic0InverterParameters,
    )
    from akkudoktoreos.devices.genetic.inverter import InverterParameters


class InverterCommonSettings(DevicesBaseSettings):
    """Inverter device settings.

    An inverter bridges a DC bus (PV / battery) and an AC bus (grid /
    household). It must therefore have at least one DC port and one AC
    port.
    """

    # ------------------------------------------------------------------
    # Shared fields (GENETIC + GENETIC0)
    # ------------------------------------------------------------------

    max_power_w: Optional[float] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": "Maximum AC output power [W].",
            "examples": [10000],
            "x-scope": [str(ConfigScope.GENETIC), str(ConfigScope.GENETIC0)],
        },
    )
    ac_to_dc_efficiency: float = Field(
        default=1.0,
        ge=0,
        le=1,
        json_schema_extra={
            "description": (
                "Efficiency of AC→DC conversion for grid-to-battery charging (0–1). "
                "Set to 0 to disable AC charging. Default 1.0."
            ),
            "examples": [0.95, 1.0, 0.0],
            "x-scope": [str(ConfigScope.GENETIC), str(ConfigScope.GENETIC0)],
        },
    )
    dc_to_ac_efficiency: float = Field(
        default=1.0,
        gt=0,
        le=1,
        json_schema_extra={
            "description": (
                "Efficiency of DC→AC conversion for battery discharging (0–1). Default 1.0."
            ),
            "examples": [0.95, 1.0],
            "x-scope": [str(ConfigScope.GENETIC), str(ConfigScope.GENETIC0)],
        },
    )
    max_ac_charge_power_w: Optional[float] = Field(
        default=None,
        ge=0,
        json_schema_extra={
            "description": (
                "Maximum AC charging power [W]. "
                "null means no additional limit. 0 disables AC charging."
            ),
            "examples": [None, 0, 5000],
            "x-scope": [str(ConfigScope.GENETIC), str(ConfigScope.GENETIC0)],
        },
    )
    battery_id: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": ("Device ID of the battery."),
            "examples": [None],
            "x-scope": [str(ConfigScope.GENETIC), str(ConfigScope.GENETIC0)],
        },
    )

    # ------------------------------------------------------------------
    # UNUSED-only fields
    # ------------------------------------------------------------------

    # Auxiliary power consumption
    off_state_power_consumption_w: float = Field(
        default=0.0,
        ge=0,
        json_schema_extra={
            "description": (
                "Standby power consumed when the inverter is fully idle "
                "(battery=0 and PV=0) [W]. Default 0.0."
            ),
            "examples": [5.0, 0.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    on_state_power_consumption_w: float = Field(
        default=0.0,
        ge=0,
        json_schema_extra={
            "description": (
                "Auxiliary power consumed whenever the inverter is active "
                "(non-zero AC power) [W]. Default 0.0."
            ),
            "examples": [10.0, 0.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )

    # PV parameters (used for SOLAR and HYBRID inverter types)
    pv_to_ac_efficiency: float = Field(
        default=1.0,
        gt=0,
        le=1,
        json_schema_extra={
            "description": (
                "Efficiency of PV DC→AC conversion (0–1). "
                "Required when pv_power_w_key is set (SOLAR or HYBRID). Default 1.0."
            ),
            "examples": [0.97, 1.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    pv_to_battery_efficiency: float = Field(
        default=1.0,
        gt=0,
        le=1,
        json_schema_extra={
            "description": (
                "Efficiency of PV DC→battery charging path (0–1). "
                "Used for HYBRID inverters only. Default 1.0."
            ),
            "examples": [0.98, 1.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    pv_max_power_w: Optional[float] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": (
                "Maximum DC PV power fed into the inverter [W]. "
                "Required when pv_power_w_key is set (SOLAR or HYBRID). "
                "Values from pv_power_w_key are clipped to this limit."
            ),
            "examples": [8000.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    pv_min_power_w: float = Field(
        default=0.0,
        ge=0,
        json_schema_extra={
            "description": (
                "Minimum DC PV power threshold [W]. Steps with available PV "
                "below this value are treated as zero. Default 0.0."
            ),
            "examples": [50.0, 0.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    pv_power_w_key: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "SimulationContext prediction key resolving to a per-step PV "
                "power forecast array [W] of shape (horizon,). "
                "Set for SOLAR and HYBRID inverter types; leave None for BATTERY."
            ),
            "examples": ["pv_forecast_w", None],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )

    # Battery parameters (used for BATTERY and HYBRID inverter types)
    battery_capacity_wh: Optional[float] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": (
                "Usable battery capacity [Wh]. Required for BATTERY and HYBRID inverter types."
            ),
            "examples": [10000.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    battery_charge_rates: Optional[list[float]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Optional list of discrete charge rate fractions (each in (0, 1]). "
                "When set, the battery is constrained to these specific fractions "
                "of battery_max_charge_rate. null means continuous charging. "
                "All values must be in (0, 1]."
            ),
            "examples": [None, [0.25, 0.5, 1.0]],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    battery_min_charge_rate: float = Field(
        default=0.0,
        ge=0,
        le=1,
        json_schema_extra={
            "description": (
                "Minimum non-zero charge rate as a fraction of the 1C rate "
                "(1C = battery_capacity_wh W). "
                "Charge commands below this threshold are rounded to zero. Default 0.0."
            ),
            "examples": [0.1, 0.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    battery_max_charge_rate: float = Field(
        default=1.0,
        gt=0,
        le=1,
        json_schema_extra={
            "description": (
                "Maximum charge rate as a fraction of the 1C rate "
                "(1C = battery_capacity_wh W). "
                "bat_factor=+1 maps to this rate. Default 1.0."
            ),
            "examples": [0.5, 1.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    battery_min_discharge_rate: float = Field(
        default=0.0,
        ge=0,
        le=1,
        json_schema_extra={
            "description": (
                "Minimum discharge rate as a fraction of the 1C rate "
                "(1C = battery_capacity_wh W). "
                "Discharge commands below this threshold are rounded to zero. Default 0.0."
            ),
            "examples": [0.1, 0.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    battery_max_discharge_rate: float = Field(
        default=1.0,
        gt=0,
        le=1,
        json_schema_extra={
            "description": (
                "Maximum discharge rate as a fraction of the 1C rate. "
                "(1C = battery_capacity_wh W). "
                "bat_factor=−1 maps to this rate. Default 1.0."
            ),
            "examples": [0.5, 1.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    battery_min_soc_factor: float = Field(
        default=0.0,
        ge=0,
        lt=1,
        json_schema_extra={
            "description": (
                "Minimum allowed state of charge as a fraction of battery_capacity_wh. "
                "Must be < battery_max_soc_factor. Default 0.0."
            ),
            "examples": [0.1, 0.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    battery_max_soc_factor: float = Field(
        default=1.0,
        gt=0,
        le=1,
        json_schema_extra={
            "description": (
                "Maximum allowed state of charge as a fraction of battery_capacity_wh. "
                "Must be > battery_min_soc_factor. Default 1.0."
            ),
            "examples": [0.9, 1.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    battery_initial_soc_factor_key: str = Field(
        default="",
        json_schema_extra={
            "description": (
                "SimulationContext measurement key resolving to the initial battery "
                "SoC as a fraction of battery_capacity_wh, in [min_soc_factor, max_soc_factor]. "
                "An empty string means the device uses battery_min_soc_factor as the "
                "initial SoC (fully depleted to the minimum)."
            ),
            "examples": ["battery1_soc_factor", ""],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    battery_lcos_amt_kwh: float = Field(
        default=0.0,
        ge=0,
        json_schema_extra={
            "description": (
                "Levelized cost of battery storage [Amt./kWh cycled]. "
                "Penalises unnecessary charging/discharging so the GA avoids "
                "grid-charge→discharge cycles with no price-spread benefit. "
                "Typical residential Li-ion value: 0.05 Amt./kWh. "
                "Set to 0.0 to encourage the optimizer to use the battery. "
                "Defaults to 0.0."
            ),
            "examples": [0.05, 0.0],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )
    battery_discharge_reward_amt_kwh: float = Field(
        default=0.02,
        ge=0,
        json_schema_extra={
            "description": (
                "Shadow price rewarding battery discharge [Amt./kWh discharged AC]. "
                "Adds a direct fitness benefit per kWh the battery delivers, on top of "
                "the grid import cost reduction already captured by GridConnectionDevice. "
                "Helps the GA discover discharge when the load-matching rate is small "
                "relative to mutation noise. "
                "Suggested value: import_price - export_price - lcos "
                "(e.g. 0.30 - 0.08 - 0.05 = 0.17). Set to 0.0 to disable."
            ),
            "examples": [0.0, 0.17],
            "x-scope": [str(ConfigScope.UNUSED)],
        },
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _validate_soc_factors(self) -> "InverterCommonSettings":
        if self.battery_min_soc_factor >= self.battery_max_soc_factor:
            raise ValueError(
                "battery_min_soc_factor must be strictly less than battery_max_soc_factor"
            )
        return self

    # ------------------------------------------------------------------
    # GENETIC domain conversion
    # ------------------------------------------------------------------

    def to_genetic_param(self) -> "InverterParameters":
        """Return InverterParameters for the GENETIC optimizer."""
        from akkudoktoreos.devices.genetic.inverter import InverterParameters

        return InverterParameters(
            device_id=self.device_id,
            max_power_wh=self.max_power_w,
            battery_id=self.battery_id,
            ac_to_dc_efficiency=self.ac_to_dc_efficiency,
            dc_to_ac_efficiency=self.dc_to_ac_efficiency,
            max_ac_charge_power_w=self.max_ac_charge_power_w,
        )

    # ------------------------------------------------------------------
    # GENETIC0 domain conversion
    # ------------------------------------------------------------------

    def to_genetic0_param(self) -> "Genetic0InverterParameters":
        """Return Genetic0InverterParameters for the GENETIC0 optimizer."""
        from akkudoktoreos.devices.genetic0.genetic0inverter import (
            Genetic0InverterParameters,
        )

        return Genetic0InverterParameters(
            device_id=self.device_id,
            max_power_wh=self.max_power_w,
            battery_id=self.battery_id,
            ac_to_dc_efficiency=self.ac_to_dc_efficiency,
            dc_to_ac_efficiency=self.dc_to_ac_efficiency,
            max_ac_charge_power_w=self.max_ac_charge_power_w,
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_keys(self) -> list[str]:
        """Measurement keys for this inverter.

        Returns the ``battery_initial_soc_factor_key`` if non-empty, so
        the EMS measurement store knows to watch for this key.
        """
        if self.battery_initial_soc_factor_key:
            return [self.battery_initial_soc_factor_key]
        return []
