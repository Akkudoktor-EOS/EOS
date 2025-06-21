"""Simulation devices for optimization."""

from typing import Optional

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.devices.devicesabc import DevicesBaseSettings


class BatteriesCommonSettings(DevicesBaseSettings):
    """Battery devices base settings."""

    capacity_wh: int = Field(
        default=8000,
        gt=0,
        description="Capacity [Wh].",
        examples=[8000],
    )

    charging_efficiency: float = Field(
        default=0.88,
        gt=0,
        le=1,
        description="Charging efficiency [0.01 ... 1.00].",
        examples=[0.88],
    )

    discharging_efficiency: float = Field(
        default=0.88,
        gt=0,
        le=1,
        description="Discharge efficiency [0.01 ... 1.00].",
        examples=[0.88],
    )

    levelized_cost_of_storage_kwh: float = Field(
        default=0.0,
        description="Levelized cost of storage (LCOS), the average lifetime cost of delivering one kWh [€/kWh].",
        examples=[0.12],
    )

    max_charge_power_w: Optional[float] = Field(
        default=5000,
        gt=0,
        description="Maximum charging power [W].",
        examples=[5000],
    )

    min_charge_power_w: Optional[float] = Field(
        default=50,
        gt=0,
        description="Minimum charging power [W].",
        examples=[50],
    )

    charge_rates: Optional[list[float]] = Field(
        default=None,
        description="Charge rates as factor of maximum charging power [0.00 ... 1.00]. None denotes all charge rates are available.",
        examples=[[0.0, 0.25, 0.5, 0.75, 1.0], None],
    )

    min_soc_percentage: int = Field(
        default=0,
        ge=0,
        le=100,
        description="Minimum state of charge (SOC) as percentage of capacity [%].",
        examples=[10],
    )

    max_soc_percentage: int = Field(
        default=100,
        ge=0,
        le=100,
        description="Maximum state of charge (SOC) as percentage of capacity [%].",
        examples=[100],
    )


class InverterCommonSettings(DevicesBaseSettings):
    """Inverter devices base settings."""

    max_power_w: Optional[float] = Field(
        default=None,
        gt=0,
        description="Maximum power [W].",
        examples=[10000],
    )

    battery_id: Optional[str] = Field(
        default=None,
        description="ID of battery controlled by this inverter.",
        examples=[None, "battery1"],
    )


class HomeApplianceCommonSettings(DevicesBaseSettings):
    """Home Appliance devices base settings."""

    consumption_wh: int = Field(
        gt=0,
        description="Energy consumption [Wh].",
        examples=[2000],
    )

    duration_h: int = Field(
        gt=0,
        le=24,
        description="Usage duration in hours [0 ... 24].",
        examples=[3],
    )


class DevicesCommonSettings(SettingsBaseModel):
    """Base configuration for devices simulation settings."""

    batteries: Optional[list[BatteriesCommonSettings]] = Field(
        default=None,
        description="List of battery devices",
        examples=[[{"device_id": "battery1", "capacity_wh": 8000}]],
    )

    max_batteries: Optional[int] = Field(
        default=0,
        ge=0,
        description="Maximum number of batteries that can be set",
        examples=[1, 2],
    )

    electric_vehicles: Optional[list[BatteriesCommonSettings]] = Field(
        default=None,
        description="List of electric vehicle devices",
        examples=[[{"device_id": "battery1", "capacity_wh": 8000}]],
    )

    max_electric_vehicles: Optional[int] = Field(
        default=0,
        ge=0,
        description="Maximum number of electric vehicles that can be set",
        examples=[1, 2],
    )

    inverters: Optional[list[InverterCommonSettings]] = Field(
        default=None, description="List of inverters", examples=[[]]
    )

    max_inverters: Optional[int] = Field(
        default=0,
        ge=0,
        description="Maximum number of inverters that can be set",
        examples=[1, 2],
    )

    home_appliances: Optional[list[HomeApplianceCommonSettings]] = Field(
        default=None, description="List of home appliances", examples=[[]]
    )

    max_home_appliances: Optional[int] = Field(
        default=0,
        ge=0,
        description="Maximum number of home_appliances that can be set",
        examples=[1, 2],
    )
