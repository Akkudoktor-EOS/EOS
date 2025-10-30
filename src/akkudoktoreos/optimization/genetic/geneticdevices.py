"""Genetic optimization algorithm device interfaces/ parameters."""

from typing import Optional

from pydantic import Field

from akkudoktoreos.optimization.genetic.geneticabc import GeneticParametersBaseModel
from akkudoktoreos.utils.datetimeutil import TimeWindowSequence


class DeviceParameters(GeneticParametersBaseModel):
    device_id: str = Field(description="ID of device", examples="device1")
    hours: Optional[int] = Field(
        default=None,
        gt=0,
        description="Number of prediction hours. Defaults to global config prediction hours.",
        examples=[None],
    )


def max_charging_power_field(description: Optional[str] = None) -> float:
    if description is None:
        description = "Maximum charging power in watts."
    return Field(
        default=5000,
        gt=0,
        description=description,
    )


def initial_soc_percentage_field(description: str) -> int:
    return Field(default=0, ge=0, le=100, description=description, examples=[42])


def discharging_efficiency_field(default_value: float) -> float:
    return Field(
        default=default_value,
        gt=0,
        le=1,
        description="A float representing the discharge efficiency of the battery.",
    )


class BaseBatteryParameters(DeviceParameters):
    """Battery Device Simulation Configuration."""

    device_id: str = Field(description="ID of battery", examples=["battery1"])
    capacity_wh: int = Field(
        gt=0,
        description="An integer representing the capacity of the battery in watt-hours.",
        examples=[8000],
    )
    charging_efficiency: float = Field(
        default=0.88,
        gt=0,
        le=1,
        description="A float representing the charging efficiency of the battery.",
    )
    discharging_efficiency: float = discharging_efficiency_field(0.88)
    max_charge_power_w: Optional[float] = max_charging_power_field()
    initial_soc_percentage: int = initial_soc_percentage_field(
        "An integer representing the state of charge of the battery at the **start** of the current hour (not the current state)."
    )
    min_soc_percentage: int = Field(
        default=0,
        ge=0,
        le=100,
        description="An integer representing the minimum state of charge (SOC) of the battery in percentage.",
        examples=[10],
    )
    max_soc_percentage: int = Field(
        default=100,
        ge=0,
        le=100,
        description="An integer representing the maximum state of charge (SOC) of the battery in percentage.",
    )


class SolarPanelBatteryParameters(BaseBatteryParameters):
    """PV battery device simulation configuration."""

    max_charge_power_w: Optional[float] = max_charging_power_field()


class ElectricVehicleParameters(BaseBatteryParameters):
    """Battery Electric Vehicle Device Simulation Configuration."""

    device_id: str = Field(description="ID of electric vehicle", examples=["ev1"])
    discharging_efficiency: float = discharging_efficiency_field(1.0)
    initial_soc_percentage: int = initial_soc_percentage_field(
        "An integer representing the current state of charge (SOC) of the battery in percentage."
    )
    charge_rates: Optional[list[float]] = Field(
        default=None,
        description="Charge rates as factor of maximum charging power [0.00 ... 1.00]. None denotes all charge rates are available.",
        examples=[[0.0, 0.25, 0.5, 0.75, 1.0], None],
    )


class HomeApplianceParameters(DeviceParameters):
    """Home Appliance Device Simulation Configuration."""

    device_id: str = Field(description="ID of home appliance", examples=["dishwasher"])
    consumption_wh: int = Field(
        gt=0,
        description="An integer representing the energy consumption of a household device in watt-hours.",
        examples=[2000],
    )
    duration_h: int = Field(
        gt=0,
        description="An integer representing the usage duration of a household device in hours.",
        examples=[3],
    )
    time_windows: Optional[TimeWindowSequence] = Field(
        default=None,
        description="List of allowed time windows. Defaults to optimization general time window.",
        examples=[
            [
                {"start_time": "10:00", "duration": "2 hours"},
            ],
        ],
    )


class InverterParameters(DeviceParameters):
    """Inverter Device Simulation Configuration."""

    device_id: str = Field(description="ID of inverter", examples=["inverter1"])
    max_power_wh: float = Field(gt=0, examples=[10000])
    battery_id: Optional[str] = Field(
        default=None, description="ID of battery", examples=[None, "battery1"]
    )
