"""Genetic algorithm optimisation solution."""

from typing import Any, Optional, Union

from pydantic import AliasChoices, ConfigDict, Field, computed_field, field_validator

from akkudoktoreos.optimization.genetic.devices.geneticbattery import GeneticBattery
from akkudoktoreos.optimization.genetic.geneticrequest import (
    GeneticOptimizationParameters,
    GeneticParametersBaseModel,
)
from akkudoktoreos.optimization.genetic.geneticterminalvalue import TerminalValueResult
from akkudoktoreos.utils.datetimeutil import DateTime
from akkudoktoreos.utils.utils import NumpyEncoder


class DeviceOptimizeResult(GeneticParametersBaseModel):
    device_id: str = Field(
        json_schema_extra={"description": "ID of device", "examples": ["device1"]}
    )
    hours: int = Field(
        gt=0,
        json_schema_extra={"description": "Number of hours in the simulation.", "examples": [24]},
    )


class ElectricVehicleResult(DeviceOptimizeResult):
    """Result class containing information related to the electric vehicle's charging and discharging behavior."""

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
    max_discharge_power_w: Optional[float] = Field(
        default=None,
        json_schema_extra={"description": "Maximum discharging power in watts."},
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

    @field_validator("discharge_array", "charge_array", mode="before")
    def convert_numpy(cls, field: Any) -> Any:
        return NumpyEncoder.convert_numpy(field)[0]


class GeneticSimulationResult(GeneticParametersBaseModel):
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

    @field_validator(
        "load_wh_per_hour",
        "grid_feed_in_wh_per_hour",
        "battery_soc_per_hour",
        "grid_consumption_wh_per_hour",
        "costs_per_hour",
        "revenue_per_hour",
        "ev_soc_per_hour",
        "losses_per_hour",
        "home_appliance_wh_per_hour",
        "electricity_price",
        "feed_in_tariff",
        mode="before",
    )
    def convert_numpy(cls, field: Any) -> Any:
        return NumpyEncoder.convert_numpy(field)[0]

    home_appliance_energy_wh: dict[str, list[float]] = Field(
        default_factory=dict,
        json_schema_extra={
            "description": (
                "Per-device appliance energy in watt-hours per optimization slot, "
                "keyed by device_id."
            )
        },
    )

    feed_in_tariff: list[float] = Field(
        validation_alias=AliasChoices("feed_in_tariff", "Feed_in_tariff"),
        default_factory=list,
        json_schema_extra={
            "description": "Used feed-in tariff in €/Wh per hour, including predictions"
        },
    )

    home_appliance_running: dict[str, list[bool]] = Field(
        default_factory=dict, description="Active run occupancy, including zero-power phases."
    )

    @field_validator("home_appliance_energy_wh", mode="before")
    def convert_numpy_appliance_energy(cls, field: Any) -> Any:
        if isinstance(field, dict):
            return {
                device_id: NumpyEncoder.convert_numpy(values)[0]
                for device_id, values in field.items()
            }
        return field

    @computed_field(json_schema_extra={"deprecated": True})
    def Feed_in_tariff(self) -> Optional[list[float]]:
        """Deprecated: use feed_in_tariff."""
        return self.feed_in_tariff


class GeneticSolution(GeneticParametersBaseModel):
    """**Note**: The first value of "load_wh_per_hour", "grid_feed_in_wh_per_hour", and "grid_consumption_wh_per_hour", will be set to null in the JSON output and represented as NaN or None in the corresponding classes' data returns. This approach is adopted to ensure that the current hour's processing remains unchanged."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    parameters: GeneticOptimizationParameters = Field(
        json_schema_extra={"description": "Optimization parameters used to generate solution."}
    )

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

    result: GeneticSimulationResult

    ev_obj: Optional[ElectricVehicleResult] = Field(
        validation_alias=AliasChoices("ev_obj", "eauto_obj"),
        json_schema_extra={"description": "Electric vehicle state after optimization."},
    )

    start_hour: int = Field(
        default=0,
        json_schema_extra={"description": "Start hour."},
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

    extra_data: Optional[dict[str, Union[list[int], list[float]]]] = Field(
        default=None,
        json_schema_extra={
            "description": ("Dictionary of balance: TBD, losses: TBD, constraints: TBD.")
        },
    )

    fitness_history: Optional[dict[str, Any]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Dictionary of "
                "gen: Generation numbers (X-axis), "
                "avg: Average fitness for each generation (Y-axis), "
                "max: Maximum fitness for each generation (Y-axis), "
                "min: Minimum fitness for each generation (Y-axis)."
            )
        },
    )

    fixed_seed: Optional[int] = Field(
        default=None,
        json_schema_extra={"description": "Fixed seed."},
    )

    @computed_field(json_schema_extra={"deprecated": True})
    def eautocharge_hours_float(self) -> Optional[list[float]]:
        """Deprecated: Use ev_charge_hours_float instead."""
        return self.ev_charge_hours_float

    @computed_field(json_schema_extra={"deprecated": True})
    def eauto_obj(self) -> Optional[ElectricVehicleResult]:
        """Deprecated: Use ev_obj instead."""
        return self.ev_obj

    @field_validator(
        "ac_charge",
        "dc_charge",
        "discharge_allowed",
        "battery_grid_export_allowed",
        "battery_grid_export_factor",
        mode="before",
    )
    def convert_numpy(cls, field: Any) -> Any:
        return NumpyEncoder.convert_numpy(field)[0]

    @field_validator(
        "ev_obj",
        mode="before",
    )
    def convert_eauto(cls, field: Any) -> Any:
        if isinstance(field, GeneticBattery):
            return ElectricVehicleResult(**field.to_dict())
        return field

    controls_start_at_now: bool = Field(
        default=False, description="Control arrays start at the run timestamp instead of midnight."
    )

    battery_grid_export_allowed: list[int] = Field(
        default_factory=list,
        json_schema_extra={
            "description": "Array with battery-to-grid export values (1 for export discharge, 0 otherwise)."
        },
    )

    terminal_value: Optional[TerminalValueResult] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "The terminal value applied to the energy left in the battery at "
                "the end of the horizon, including the curve it was read from. "
                "None when no battery is part of the optimization."
            )
        },
    )

    battery_grid_export_factor: list[float] = Field(
        default_factory=list,
        json_schema_extra={
            "description": (
                "Array with the battery-to-grid export level per slot as factor "
                "of the rated discharge power (0.0 for no export). Empty when "
                "direct marketing is disabled; a solution without this array "
                "exports at full power wherever "
                "'battery_grid_export_allowed' is 1."
            )
        },
    )

    start_solution_datetime: Optional[DateTime] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Start of the slot that gene 0 of 'start_solution' controls. Send it "
                "back together with 'start_solution' so the next run can shift the "
                "warm start by the slots that have elapsed since."
            ),
            "examples": [None, "2026-09-14T07:45:00+02:00"],
        },
    )

    appliance_starts: dict[str, list[DateTime]] = Field(
        default_factory=dict,
        json_schema_extra={
            "description": (
                "Scheduled run start times per appliance device_id as absolute local datetimes. "
                "A run in progress is listed first with its actual (past) start."
            )
        },
    )

    appliance_deadline_missed: dict[str, bool] = Field(
        default_factory=dict,
        json_schema_extra={
            "description": (
                "Per appliance device_id with a 'deadline_datetime': whether the "
                "scheduled run misses that deadline (or was not scheduled at "
                "all). Appliances without a deadline are not listed."
            )
        },
    )

    interval_seconds: int = Field(
        default=3600, description="Duration of one result/control slot in seconds."
    )

    first_slot_fraction: float = Field(
        default=1.0,
        gt=0.0,
        le=1.0,
        description=(
            "Part of the first control slot that was still ahead when the "
            "optimization ran (0.25 for a run at 10:45 with hourly slots). The "
            "results of the first slot cover only that part, and its factors are "
            "chosen for it. 1.0 for a run on a slot boundary."
        ),
    )

    @field_validator("start_solution_datetime", mode="before")
    @classmethod
    def transform_start_solution_datetime(cls, value: Any) -> Optional[DateTime]:
        """Accept the usual date time representations, naive input is local time."""
        return GeneticOptimizationParameters.transform_start_solution_datetime(value)
