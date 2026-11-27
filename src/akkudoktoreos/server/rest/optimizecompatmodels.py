"""Wire models for the retained optimization compatibility endpoints."""

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

from akkudoktoreos.config.configabc import CycleTimeWindowSequence, TimeWindowSequence
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.devices.devicesabc import ConsumerDeadlinePolicy, ConsumerScheduleMode
from akkudoktoreos.optimization.solutionmodel import TerminalValueReport
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime


class CompatibilityDevice(PydanticBaseModel):
    """Common fields of a request-local device."""

    model_config = ConfigDict(extra="forbid")

    device_id: str
    hours: Optional[int] = Field(default=None, gt=0)


def max_charging_power_field(description: Optional[str] = None) -> float:
    if description is None:
        description = "Maximum charging power in watts."
    # 0: the battery may not charge, e.g. the profile inhibits it at a low temperature.
    return Field(default=5000, ge=0, json_schema_extra={"description": description})


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


class HourlyStorage(CompatibilityDevice):
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
                "Maximum discharging power in watts. 0: the battery may not discharge. "
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


class HourlyBattery(HourlyStorage):
    """PV battery device simulation configuration."""

    max_charge_power_w: Optional[float] = max_charging_power_field()


class HourlyVehicle(HourlyStorage):
    """Battery Electric Vehicle Device Simulation Configuration."""

    device_id: str = Field(
        json_schema_extra={"description": "ID of electric vehicle", "examples": ["ev1"]}
    )
    discharging_efficiency: float = discharging_efficiency_field(1.0)
    initial_soc_percentage: int = initial_soc_percentage_field(
        "An integer representing the current state of charge (SOC) of the battery in percentage."
    )


class HourlyInverter(CompatibilityDevice):
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


class HourlyAppliance(CompatibilityDevice):
    """Home Appliance Device Simulation Configuration."""

    device_id: str = Field(
        json_schema_extra={"description": "ID of home appliance", "examples": ["dishwasher"]}
    )
    consumption_wh: int = Field(
        gt=0,
        json_schema_extra={
            "description": "An integer representing the energy consumption of a household device in watt-hours.",
            "examples": [2000],
        },
    )
    duration_h: int = Field(
        gt=0,
        json_schema_extra={
            "description": "An integer representing the usage duration of a household device in hours.",
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


class HourlyForecast(PydanticBaseModel):
    """Encapsulates energy-related forecasts and costs used in hourly optimization."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    pv_forecast_wh: list[float] = Field(
        validation_alias=AliasChoices("pv_forecast_wh", "pv_prognose_wh"),
        json_schema_extra={
            "description": "An array of floats representing the forecasted photovoltaic output in watts for different time intervals."
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
            "description": "An array of floats representing the total load (consumption) in watts for different time intervals."
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
    def validate_list_length(self) -> Self:
        """Validate that all input lists are of the same length.

        Raises:
            ValueError: If input list lengths differ.
        """
        pv_forecast_length = len(self.pv_forecast_wh)
        if (
            pv_forecast_length != len(self.electricity_price_per_wh)
            or pv_forecast_length != len(self.total_load)
            or (
                isinstance(self.feed_in_tariff_per_wh, list)
                and pv_forecast_length != len(self.feed_in_tariff_per_wh)
            )
        ):
            raise ValueError("Input lists have different lengths")
        return self


class HourlyOptimizationRequest(PydanticBaseModel):
    """Main parameter class for running the genetic energy optimization.

    Collects all model and configuration parameters necessary to run the
    optimization process, such as forecasts, pricing, battery and appliance models.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    ems: HourlyForecast
    pv_battery: Optional[HourlyBattery] = Field(
        validation_alias=AliasChoices("pv_battery", "pv_akku"),
        json_schema_extra={"description": "PV battery parameters."},
    )
    inverter: Optional[HourlyInverter]
    ev: Optional[HourlyVehicle] = Field(
        validation_alias=AliasChoices("ev", "eauto"),
        json_schema_extra={"description": "Electric vehicle parameters."},
    )
    dishwasher: Optional[HourlyAppliance] = None
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

    # Computed fields for backward compatibility (deprecated German names)
    @computed_field(json_schema_extra={"deprecated": True})
    def pv_akku(self) -> Optional[HourlyBattery]:
        """Deprecated: Use pv_battery instead."""
        return self.pv_battery

    @computed_field(json_schema_extra={"deprecated": True})
    def eauto(self) -> Optional[HourlyVehicle]:
        """Deprecated: Use ev instead."""
        return self.ev


class RuntimeForecasts(PydanticBaseModel):
    """External series from local midnight, in slot Wh and currency per Wh.

    The interval is configured in optimization.interval_sec. Omitted
    series are read from the configured providers, preserving their raw coverage.
    """

    model_config = ConfigDict(extra="forbid")

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


class ConfigOptimizationRequest(PydanticBaseModel):
    """Supply observations while hardware, tariffs and scheduling remain in config.

    Explicit SoC values take precedence over fresh measurements. Without a usable
    measurement, assume 0% initial SoC for the affected device and continue.
    """

    model_config = ConfigDict(extra="forbid")

    soc: dict[str, Annotated[float, Field(ge=0, le=100)]] = Field(default_factory=dict)
    forecasts: RuntimeForecasts = Field(default_factory=RuntimeForecasts)
    start_solution: Optional[list[float]] = None
    start_solution_datetime: Optional[DateTime] = None

    @field_validator("start_solution_datetime", mode="before")
    @classmethod
    def parse_start_solution_datetime(cls, value: Any) -> Optional[DateTime]:
        """Accept a previous genome timestamp with its original offset."""
        return to_datetime(value) if value is not None else None


class HourlySimulationResult(PydanticBaseModel):
    """This object contains the results of the simulation and provides insights into various parameters over the entire forecast period."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    load_wh_per_hour: list[float] = Field(
        validation_alias=AliasChoices("load_wh_per_hour", "Last_Wh_pro_Stunde"),
        json_schema_extra={"description": "The load in watt-hours per hour."},
    )
    ev_soc_per_hour: list[float] = Field(
        validation_alias=AliasChoices("ev_soc_per_hour", "EAuto_SoC_pro_Stunde"),
        json_schema_extra={"description": "The state of charge of the EV for each hour."},
    )
    revenue_per_hour: list[float] = Field(
        validation_alias=AliasChoices("revenue_per_hour", "Einnahmen_Euro_pro_Stunde"),
        json_schema_extra={
            "description": "The revenue from grid feed-in or other sources per hour."
        },
    )
    total_losses: float = Field(
        validation_alias=AliasChoices("total_losses", "Gesamt_Verluste"),
        json_schema_extra={"description": "The total losses in watt-hours over the entire period."},
    )
    total_balance: float = Field(
        validation_alias=AliasChoices("total_balance", "Gesamtbilanz_Euro"),
        json_schema_extra={"description": "The total balance of revenues minus costs."},
    )
    total_revenue: float = Field(
        validation_alias=AliasChoices("total_revenue", "Gesamteinnahmen_Euro"),
        json_schema_extra={"description": "The total revenues."},
    )
    total_costs: float = Field(
        validation_alias=AliasChoices("total_costs", "Gesamtkosten_Euro"),
        json_schema_extra={"description": "The total costs."},
    )
    home_appliance_wh_per_hour: list[Optional[float]] = Field(
        validation_alias=AliasChoices("home_appliance_wh_per_hour", "Home_appliance_wh_per_hour"),
        json_schema_extra={
            "description": "The energy consumption of a household appliance in watt-hours per hour."
        },
    )
    costs_per_hour: list[float] = Field(
        validation_alias=AliasChoices("costs_per_hour", "Kosten_Euro_pro_Stunde"),
        json_schema_extra={"description": "The costs per hour."},
    )
    grid_consumption_wh_per_hour: list[float] = Field(
        validation_alias=AliasChoices("grid_consumption_wh_per_hour", "Netzbezug_Wh_pro_Stunde"),
        json_schema_extra={"description": "The grid energy drawn in watt-hours per hour."},
    )
    grid_feed_in_wh_per_hour: list[float] = Field(
        validation_alias=AliasChoices("grid_feed_in_wh_per_hour", "Netzeinspeisung_Wh_pro_Stunde"),
        json_schema_extra={"description": "The energy fed into the grid in watt-hours per hour."},
    )
    losses_per_hour: list[float] = Field(
        validation_alias=AliasChoices("losses_per_hour", "Verluste_Pro_Stunde"),
        json_schema_extra={"description": "The losses in watt-hours per hour."},
    )
    battery_soc_per_hour: list[float] = Field(
        validation_alias=AliasChoices("battery_soc_per_hour", "akku_soc_pro_stunde"),
        json_schema_extra={
            "description": "The state of charge of the battery (not the EV) in percentage per hour."
        },
    )
    electricity_price: list[float] = Field(
        validation_alias=AliasChoices("electricity_price", "Electricity_price"),
        json_schema_extra={"description": "Used Electricity Price, including predictions"},
    )

    # Computed fields for backward compatibility (deprecated German names)
    @computed_field(json_schema_extra={"deprecated": True})
    def Last_Wh_pro_Stunde(self) -> list[float]:
        """Deprecated: Use load_wh_per_hour instead."""
        return self.load_wh_per_hour

    @computed_field(json_schema_extra={"deprecated": True})
    def EAuto_SoC_pro_Stunde(self) -> list[float]:
        """Deprecated: Use ev_soc_per_hour instead."""
        return self.ev_soc_per_hour

    @computed_field(json_schema_extra={"deprecated": True})
    def Einnahmen_Euro_pro_Stunde(self) -> list[float]:
        """Deprecated: Use revenue_per_hour instead."""
        return self.revenue_per_hour

    @computed_field(json_schema_extra={"deprecated": True})
    def Gesamt_Verluste(self) -> float:
        """Deprecated: Use total_losses instead."""
        return self.total_losses

    @computed_field(json_schema_extra={"deprecated": True})
    def Gesamtbilanz_Euro(self) -> float:
        """Deprecated: Use total_balance instead."""
        return self.total_balance

    @computed_field(json_schema_extra={"deprecated": True})
    def Gesamteinnahmen_Euro(self) -> float:
        """Deprecated: Use total_revenue instead."""
        return self.total_revenue

    @computed_field(json_schema_extra={"deprecated": True})
    def Gesamtkosten_Euro(self) -> float:
        """Deprecated: Use total_costs instead."""
        return self.total_costs

    @computed_field(json_schema_extra={"deprecated": True})
    def Home_appliance_wh_per_hour(self) -> list[Optional[float]]:
        """Deprecated: Use home_appliance_wh_per_hour instead."""
        return self.home_appliance_wh_per_hour

    @computed_field(json_schema_extra={"deprecated": True})
    def Kosten_Euro_pro_Stunde(self) -> list[float]:
        """Deprecated: Use costs_per_hour instead."""
        return self.costs_per_hour

    @computed_field(json_schema_extra={"deprecated": True})
    def Netzbezug_Wh_pro_Stunde(self) -> list[float]:
        """Deprecated: Use grid_consumption_wh_per_hour instead."""
        return self.grid_consumption_wh_per_hour

    @computed_field(json_schema_extra={"deprecated": True})
    def Netzeinspeisung_Wh_pro_Stunde(self) -> list[float]:
        """Deprecated: Use grid_feed_in_wh_per_hour instead."""
        return self.grid_feed_in_wh_per_hour

    @computed_field(json_schema_extra={"deprecated": True})
    def Verluste_Pro_Stunde(self) -> list[float]:
        """Deprecated: Use losses_per_hour instead."""
        return self.losses_per_hour

    @computed_field(json_schema_extra={"deprecated": True})
    def akku_soc_pro_stunde(self) -> list[float]:
        """Deprecated: Use battery_soc_per_hour instead."""
        return self.battery_soc_per_hour

    @computed_field(json_schema_extra={"deprecated": True})
    def Electricity_price(self) -> list[float]:
        """Deprecated: Use electricity_price instead."""
        return self.electricity_price


class CompatibilityVehicleResult(PydanticBaseModel):
    """Result class containing information related to the electric vehicle's charging and discharging behavior."""

    hours: int = Field(gt=0)
    device_id: str = Field(
        json_schema_extra={"description": "ID of electric vehicle", "examples": ["ev1"]}
    )
    charge_array: list[float] = Field(
        json_schema_extra={
            "description": "Hourly charging status (0 for no charging, 1 for charging)."
        }
    )
    discharge_array: list[int] = Field(
        json_schema_extra={
            "description": "Hourly discharging status (0 for no discharging, 1 for discharging)."
        }
    )
    discharging_efficiency: float = Field(
        json_schema_extra={"description": "The discharge efficiency as a float.."}
    )
    capacity_wh: int = Field(
        json_schema_extra={"description": "Capacity of the EV’s battery in watt-hours."}
    )
    charging_efficiency: float = Field(
        json_schema_extra={"description": "Charging efficiency as a float.."}
    )
    max_charge_power_w: int = Field(
        json_schema_extra={"description": "Maximum charging power in watts."}
    )
    soc_wh: float = Field(
        json_schema_extra={
            "description": "State of charge of the battery in watt-hours at the start of the simulation."
        }
    )
    initial_soc_percentage: int = Field(
        json_schema_extra={
            "description": "State of charge at the start of the simulation in percentage."
        }
    )


class ConfiguredVehicleResult(CompatibilityVehicleResult):
    """The configured API's vehicle result also reports maximum discharge power."""

    max_discharge_power_w: Optional[float] = Field(
        default=None,
        json_schema_extra={"description": "Maximum discharging power in watts."},
    )


class HourlyOptimizationResponse(PydanticBaseModel):
    """Control arrays indexed from local midnight and results from the start hour."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    ac_charge: list[float] = Field(
        json_schema_extra={
            "description": "Array with AC charging values as relative power (0.0-1.0), other values set to 0."
        }
    )
    dc_charge: list[float] = Field(
        json_schema_extra={
            "description": "Array with DC charging values as relative power (0-1), other values set to 0."
        }
    )
    discharge_allowed: list[int] = Field(
        json_schema_extra={
            "description": "Array with discharge values (1 for discharge, 0 otherwise)."
        }
    )
    ev_charge_hours_float: Optional[list[float]] = Field(
        validation_alias=AliasChoices("ev_charge_hours_float", "eautocharge_hours_float"),
        json_schema_extra={
            "description": "Array with EV charging values as relative power (0.0-1.0), or `null` if no EV is optimized."
        },
    )
    result: HourlySimulationResult
    ev_obj: Optional[CompatibilityVehicleResult] = Field(
        validation_alias=AliasChoices("ev_obj", "eauto_obj"),
        json_schema_extra={"description": "Electric vehicle state after optimization."},
    )
    start_solution: Optional[list[float]] = Field(
        default=None,
        json_schema_extra={
            "description": "An array of binary values (0 or 1) representing a possible starting solution for the simulation."
        },
    )
    washingstart: Optional[int] = Field(
        default=None,
        json_schema_extra={
            "description": "Can be `null` or contain an object representing the start of washing (if applicable)."
        },
    )

    # Computed fields for backward compatibility (deprecated German names)
    @computed_field(json_schema_extra={"deprecated": True})
    def eautocharge_hours_float(self) -> Optional[list[float]]:
        """Deprecated: Use ev_charge_hours_float instead."""
        return self.ev_charge_hours_float

    @computed_field(json_schema_extra={"deprecated": True})
    def eauto_obj(self) -> Optional[CompatibilityVehicleResult]:
        """Deprecated: Use ev_obj instead."""
        return self.ev_obj


class ConfigSimulationResult(HourlySimulationResult):
    """The slot results and per-appliance observations of the configured API."""

    home_appliance_energy_wh: dict[str, list[float]] = Field(default_factory=dict)
    home_appliance_running: dict[str, list[bool]] = Field(default_factory=dict)
    feed_in_tariff: list[float] = Field(default_factory=list)

    @computed_field(json_schema_extra={"deprecated": True})
    def Feed_in_tariff(self) -> list[float]:
        """Legacy name for the feed-in tariff series."""
        return self.feed_in_tariff


class ConfigStorageParameters(HourlyStorage):
    """Resolved parameters of a configured battery or vehicle."""

    grid_export_rates: Optional[list[float]] = None
    charge_power_limit_w_by_soc: Optional[list[float]] = None
    discharge_power_limit_w_by_soc: Optional[list[float]] = None
    charging_efficiency_curve: Optional[list[tuple[float, float]]] = None
    discharging_efficiency_curve: Optional[list[tuple[float, float]]] = None


class ConfigBatteryParameters(ConfigStorageParameters):
    """Resolved stationary battery parameters."""

    levelized_cost_of_storage_kwh: float
    full_charge_soc_percentage: Optional[int] = None
    full_charge_deadline_datetime: Optional[DateTime] = None


class ConfigVehicleParameters(ConfigStorageParameters):
    """Resolved vehicle parameters and target."""

    min_soc_deadline_datetime: Optional[DateTime] = None
    min_soc_max_duration_h: Optional[float] = None


class ConfigInverterParameters(HourlyInverter):
    """Resolved inverter operating parameters."""

    dc_to_ac_efficiency_curve: Optional[list[tuple[float, float]]] = None
    ac_to_dc_efficiency_curve: Optional[list[tuple[float, float]]] = None
    ac_charge_limits_total_charge: bool
    dc_to_ac_efficiency_reference_load_fraction: float


class ConfigApplianceParameters(CompatibilityDevice):
    """Resolved appliance scheduling inputs."""

    consumption_wh: Optional[int]
    duration_h: Optional[int]
    num_cycles: int
    completed_cycles: int
    run_started_datetime: Optional[DateTime]
    min_cycle_gap_h: int
    time_windows: Optional[CycleTimeWindowSequence]
    shared_time_windows: Optional[TimeWindowSequence]
    load_profile_power_w: Optional[list[float]]
    load_profile_interval_seconds: Optional[int]
    schedule_mode: ConsumerScheduleMode
    earliest_start_datetime: Optional[DateTime]
    deadline_datetime: Optional[DateTime]
    deadline_policy: ConsumerDeadlinePolicy


class ConfigOptimizationParameters(PydanticBaseModel):
    """Inputs used for the configured optimization response."""

    ems: HourlyForecast
    pv_battery: Optional[ConfigBatteryParameters]
    inverter: ConfigInverterParameters
    ev: Optional[ConfigVehicleParameters]
    dishwasher: Optional[ConfigApplianceParameters] = None
    home_appliances: list[ConfigApplianceParameters]
    temperature_forecast: Optional[list[Optional[float]]] = None
    forecast_interval_seconds: int
    start_solution: Optional[list[float]] = None
    start_solution_datetime: Optional[DateTime] = None

    @computed_field(json_schema_extra={"deprecated": True})
    def pv_akku(self) -> Optional[ConfigBatteryParameters]:
        """Historical name of the stationary battery."""
        return self.pv_battery

    @computed_field(json_schema_extra={"deprecated": True})
    def eauto(self) -> Optional[ConfigVehicleParameters]:
        """Historical name of the electric vehicle."""
        return self.ev


class ConfigOptimizationResponse(HourlyOptimizationResponse):
    """The documented response shape of POST /v1/optimize."""

    parameters: ConfigOptimizationParameters
    ev_obj: Optional[ConfiguredVehicleResult] = Field(
        validation_alias=AliasChoices("ev_obj", "eauto_obj"),
    )
    result: ConfigSimulationResult
    start_hour: int
    extra_data: Optional[dict[str, Any]] = None
    fitness_history: Optional[dict[str, Any]] = None
    fixed_seed: Optional[int] = None
    controls_start_at_now: bool = True
    battery_grid_export_allowed: list[int] = Field(default_factory=list)
    battery_grid_export_factor: list[float] = Field(default_factory=list)
    terminal_value: Optional[TerminalValueReport] = None
    start_solution_datetime: Optional[DateTime] = None
    appliance_starts: dict[str, list[DateTime]] = Field(default_factory=dict)
    appliance_deadline_missed: dict[str, bool] = Field(default_factory=dict)
    interval_seconds: int
    first_slot_fraction: float

    @computed_field(json_schema_extra={"deprecated": True})
    def eauto_obj(self) -> Optional[ConfiguredVehicleResult]:
        """Historical name of the configured vehicle result."""
        return self.ev_obj
