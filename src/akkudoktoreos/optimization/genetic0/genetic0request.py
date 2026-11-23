"""GENETIC0 optimization request: the forecast and device parameters a client sends.

This module defines the Pydantic models of the request, including photovoltaic forecasts,
electricity pricing, and system component parameters. The parameter preparation
(``genetic0prepare``) assembles a request from configuration, predictions and measurements.
"""

from typing import Any, Optional, Union

from pydantic import (
    AliasChoices,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)
from typing_extensions import Self

from akkudoktoreos.config.configabc import TimeWindow, TimeWindowSequence
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.optimization.genetic0.devices.genetic0battery import (
    Genetic0BatteryParameters,
)
from akkudoktoreos.optimization.genetic0.devices.genetic0homeappliance import (
    Genetic0HomeApplianceParameters,
)
from akkudoktoreos.optimization.genetic0.devices.genetic0inverter import (
    Genetic0InverterParameters,
)
from akkudoktoreos.optimization.genetic0.genetic0energymanagement import (
    Genetic0EnergyManagementParameters,
)
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration, to_time


class Genetic0ParametersBaseModel(PydanticBaseModel):
    """Pydantic base model of the request and response models of GENETIC0."""

    model_config = ConfigDict(extra="forbid")


class Genetic0DeviceRequest(Genetic0ParametersBaseModel):
    """Fields every device of the request has."""

    device_id: str = Field(json_schema_extra={"description": "ID of device", "examples": "device1"})
    hours: Optional[int] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": "Number of prediction hours. Defaults to global config prediction hours.",
            "examples": [None],
        },
    )


def max_charging_power_field(description: Optional[str] = None) -> Any:
    """Pydantic field of a maximum charging power in watts.

    Args:
        description: Field description; ``None`` uses a generic one.

    Returns:
        The field definition.
    """
    if description is None:
        description = "Maximum charging power in watts."
    # 0: the battery may not charge, e.g. the profile inhibits it at a low temperature.
    return Field(default=5000, ge=0, json_schema_extra={"description": description})


def initial_soc_percentage_field(description: str) -> Any:
    """Pydantic field of an initial state of charge in percent.

    Args:
        description: Field description.

    Returns:
        The field definition.
    """
    return Field(
        default=0, ge=0, le=100, json_schema_extra={"description": description, "examples": [42]}
    )


def discharging_efficiency_field(default_value: float) -> Any:
    """Pydantic field of a discharging efficiency (0, 1].

    Args:
        default_value: Default efficiency.

    Returns:
        The field definition.
    """
    return Field(
        default=default_value,
        gt=0,
        le=1,
        json_schema_extra={
            "description": "A float representing the discharge efficiency of the battery."
        },
    )


class Genetic0BaseBatteryRequest(Genetic0DeviceRequest):
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

    def to_parameters(self) -> Genetic0BatteryParameters:
        """The parameters of the simulated battery.

        Returns:
            The battery parameters; a stationary battery keeps its minimum state of charge.
        """
        return Genetic0BatteryParameters(
            device_id=self.device_id,
            capacity_wh=self.capacity_wh,
            charging_efficiency=self.charging_efficiency,
            discharging_efficiency=self.discharging_efficiency,
            max_charge_power_w=self.max_charge_power_w,
            max_discharge_power_w=self.max_discharge_power_w,
            initial_soc_percentage=self.initial_soc_percentage,
            min_soc_percentage=self.min_soc_percentage,
            max_soc_percentage=self.max_soc_percentage,
            charge_rates=None if self.charge_rates is None else tuple(self.charge_rates),
            stationary=isinstance(self, Genetic0SolarPanelBatteryRequest),
        )


class Genetic0SolarPanelBatteryRequest(Genetic0BaseBatteryRequest):
    """PV battery device simulation configuration."""

    max_charge_power_w: Optional[float] = max_charging_power_field()


class Genetic0ElectricVehicleRequest(Genetic0BaseBatteryRequest):
    """Battery Electric Vehicle Device Simulation Configuration."""

    device_id: str = Field(
        json_schema_extra={"description": "ID of electric vehicle", "examples": ["ev1"]}
    )
    discharging_efficiency: float = discharging_efficiency_field(1.0)
    initial_soc_percentage: int = initial_soc_percentage_field(
        "An integer representing the current state of charge (SOC) of the battery in percentage."
    )


class Genetic0InverterRequest(Genetic0DeviceRequest):
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

    def to_parameters(self) -> Genetic0InverterParameters:
        """The parameters of the simulated inverter.

        Returns:
            The inverter parameters.
        """
        return Genetic0InverterParameters(
            device_id=self.device_id,
            max_power_wh=self.max_power_wh,
            battery_id=self.battery_id,
            ac_to_dc_efficiency=self.ac_to_dc_efficiency,
            dc_to_ac_efficiency=self.dc_to_ac_efficiency,
            max_ac_charge_power_w=self.max_ac_charge_power_w,
        )


class Genetic0HomeApplianceRequest(Genetic0DeviceRequest):
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

    def to_parameters(self, prediction_hours: int) -> Genetic0HomeApplianceParameters:
        """The parameters of the simulated home appliance.

        Evaluates the time windows into the hours a run may start, relative to midnight of
        today, as the simulation device did before. Without time windows the request gets a
        window over all prediction hours, which the result echoes.

        Args:
            prediction_hours: Length of the simulated arrays.

        Returns:
            The home appliance parameters.
        """
        if self.time_windows is None:
            self.time_windows = TimeWindowSequence(
                windows=[
                    TimeWindow(
                        start_time=to_time("00:00"),
                        duration=to_duration(f"{prediction_hours} hours"),
                    ),
                ]
            )
        start_datetime = to_datetime().set(hour=0, minute=0, second=0)
        duration = to_duration(f"{self.duration_h} hours")
        start_allowed = tuple(
            self.time_windows.contains(start_datetime.add(hours=hour), duration=duration)
            for hour in range(0, prediction_hours)
        )
        start_earliest = self.time_windows.earliest_start_time(duration, start_datetime)
        start_latest = self.time_windows.latest_start_time(duration, start_datetime)
        return Genetic0HomeApplianceParameters(
            device_id=self.device_id,
            consumption_wh=self.consumption_wh,
            duration_h=self.duration_h,
            start_allowed=start_allowed,
            start_earliest=start_earliest.hour if start_earliest else 0,
            start_latest=start_latest.hour if start_latest else 23,
        )


class Genetic0EnergyManagementRequest(Genetic0ParametersBaseModel):
    """Encapsulates energy-related forecasts and costs used in GENETIC optimization."""

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

    def to_parameters(self) -> Genetic0EnergyManagementParameters:
        """The forecasts and prices of the simulation.

        Returns:
            The energy management parameters; a constant feed-in tariff stays a single value.
        """
        tariff = self.feed_in_tariff_per_wh
        return Genetic0EnergyManagementParameters(
            pv_forecast_wh=tuple(self.pv_forecast_wh),
            electricity_price_per_wh=tuple(self.electricity_price_per_wh),
            feed_in_tariff_per_wh=tuple(tariff) if isinstance(tariff, list) else tariff,
            price_per_wh_battery=self.price_per_wh_battery,
            total_load=tuple(self.total_load),
        )


class Genetic0Request(Genetic0ParametersBaseModel):
    """Main parameter class for running the genetic energy optimization.

    Collects all model and configuration parameters necessary to run the
    optimization process, such as forecasts, pricing, battery and appliance models.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    ems: Genetic0EnergyManagementRequest
    pv_battery: Optional[Genetic0SolarPanelBatteryRequest] = Field(
        validation_alias=AliasChoices("pv_battery", "pv_akku"),
        json_schema_extra={"description": "PV battery parameters."},
    )
    inverter: Optional[Genetic0InverterRequest]
    ev: Optional[Genetic0ElectricVehicleRequest] = Field(
        validation_alias=AliasChoices("ev", "eauto"),
        json_schema_extra={"description": "Electric vehicle parameters."},
    )
    dishwasher: Optional[Genetic0HomeApplianceRequest] = None
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
    def pv_akku(self) -> Optional[Genetic0SolarPanelBatteryRequest]:
        """Deprecated: Use pv_battery instead."""
        return self.pv_battery

    @computed_field(json_schema_extra={"deprecated": True})
    def eauto(self) -> Optional[Genetic0ElectricVehicleRequest]:
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
