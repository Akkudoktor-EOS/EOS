"""GENETIC0 optimization request: the forecast and device parameters a client sends.

This module defines the Pydantic models of the request, including photovoltaic forecasts,
electricity pricing, and system component parameters. The parameter preparation
(``genetic0prepare``) assembles a request from configuration, predictions and measurements.
"""

from typing import Optional, Union

from pydantic import (
    AliasChoices,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)
from typing_extensions import Self

from akkudoktoreos.optimization.genetic0.devices.genetic0battery import (
    Genetic0ElectricVehicleParameters,
    Genetic0SolarPanelBatteryParameters,
)
from akkudoktoreos.optimization.genetic0.devices.genetic0homeappliance import (
    Genetic0HomeApplianceParameters,
)
from akkudoktoreos.optimization.genetic0.devices.genetic0inverter import (
    Genetic0InverterParameters,
)
from akkudoktoreos.optimization.genetic0.genetic0abc import Genetic0ParametersBaseModel


class Genetic0EnergyManagementParameters(Genetic0ParametersBaseModel):
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


class Genetic0Request(Genetic0ParametersBaseModel):
    """Main parameter class for running the genetic energy optimization.

    Collects all model and configuration parameters necessary to run the
    optimization process, such as forecasts, pricing, battery and appliance models.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    ems: Genetic0EnergyManagementParameters
    pv_battery: Optional[Genetic0SolarPanelBatteryParameters] = Field(
        validation_alias=AliasChoices("pv_battery", "pv_akku"),
        json_schema_extra={"description": "PV battery parameters."},
    )
    inverter: Optional[Genetic0InverterParameters]
    ev: Optional[Genetic0ElectricVehicleParameters] = Field(
        validation_alias=AliasChoices("ev", "eauto"),
        json_schema_extra={"description": "Electric vehicle parameters."},
    )
    dishwasher: Optional[Genetic0HomeApplianceParameters] = None
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
    def pv_akku(self) -> Optional[Genetic0SolarPanelBatteryParameters]:
        """Deprecated: Use pv_battery instead."""
        return self.pv_battery

    @computed_field(json_schema_extra={"deprecated": True})
    def eauto(self) -> Optional[Genetic0ElectricVehicleParameters]:
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
