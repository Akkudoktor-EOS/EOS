"""GENETIC algorithm paramters.

This module defines the Pydantic-based configuration and input parameter models
used in the energy optimization routines, including photovoltaic forecasts,
electricity pricing, and system component parameters.

It also provides a method to assemble these parameters from predictions,
forecasts, and fallback defaults, preparing them for optimization runs.
"""

from typing import Any, Optional, Union

from loguru import logger
from pydantic import (
    AliasChoices,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)
from typing_extensions import Self

from akkudoktoreos.core.coreabc import (
    ConfigMixin,
    MeasurementMixin,
    PredictionMixin,
)
from akkudoktoreos.devices.genetic.battery import (
    ElectricVehicleParameters,
    SolarPanelBatteryParameters,
)
from akkudoktoreos.devices.genetic.homeappliance import HomeApplianceParameters
from akkudoktoreos.devices.genetic.inverter import InverterParameters
from akkudoktoreos.optimization.genetic.geneticabc import GeneticParametersBaseModel
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime

# Do not import directly from akkudoktoreos.core.coreabc
# EnergyManagementSystemMixin - Creates circular dependency with ems.py
# StartMixin                  - Creates circular dependency with ems.py


class GeneticEnergyManagementParameters(GeneticParametersBaseModel):
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


class GeneticOptimizationParameters(
    ConfigMixin,
    MeasurementMixin,
    PredictionMixin,
    # EnergyManagementSystemMixin, # Creates circular dependency with ems.py
    # StartMixin,                  # Creates circular dependency with ems.py
    GeneticParametersBaseModel,
):
    """Main parameter class for running the genetic energy optimization.

    Collects all model and configuration parameters necessary to run the
    optimization process, such as forecasts, pricing, battery and appliance models.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    ems: GeneticEnergyManagementParameters
    pv_battery: Optional[SolarPanelBatteryParameters] = Field(
        validation_alias=AliasChoices("pv_battery", "pv_akku"),
        json_schema_extra={"description": "PV battery parameters."},
    )
    inverter: Optional[InverterParameters]
    ev: Optional[ElectricVehicleParameters] = Field(
        validation_alias=AliasChoices("ev", "eauto"),
        json_schema_extra={"description": "Electric vehicle parameters."},
    )
    dishwasher: Optional[HomeApplianceParameters] = None
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

    home_appliances: Optional[list[HomeApplianceParameters]] = Field(
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

    def resolved_home_appliances(self) -> list[HomeApplianceParameters]:
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
    def pv_akku(self) -> Optional[SolarPanelBatteryParameters]:
        """Deprecated: Use pv_battery instead."""
        return self.pv_battery

    @computed_field(json_schema_extra={"deprecated": True})
    def eauto(self) -> Optional[ElectricVehicleParameters]:
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

    @classmethod
    async def prepare(cls) -> "Optional[GeneticOptimizationParameters]":
        """Resolve configured devices and fresh forecasts for automatic optimization.

        Missing inputs cancel the run without changing providers or inventing devices.
        The same resolver serves the configuration-owned HTTP request.
        """
        from akkudoktoreos.optimization.genetic.configrequest import (
            ConfigOptimizationRequest,
        )

        try:
            return await ConfigOptimizationRequest().resolve()
        except Exception as exc:
            logger.error(
                "Cannot prepare GENETIC parameters; canceling optimization with provider {}: {}",
                cls.config.feedintariff.provider,
                exc,
            )
            return None
