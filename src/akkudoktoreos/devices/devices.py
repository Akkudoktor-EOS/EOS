"""General configuration settings for simulated devices for optimization."""

import json
from typing import Any, Optional, TextIO, cast

from pydantic import Field, model_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.cache import CacheFileStore
from akkudoktoreos.core.coreabc import ConfigMixin, SingletonMixin
from akkudoktoreos.core.emplan import ResourceStatus
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.devices.devicesabc import DevicesBaseSettings
from akkudoktoreos.utils.datetimeutil import DateTime, TimeWindow, to_datetime


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

    measured_soc_percentage_key: Optional[str] = Field(
        default=None,
        description="The key of the measurements that are state of charge readings of this battery [%].",
        examples=["battery1_soc"],
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
        examples=[1],
    )

    time_windows: Optional[list[TimeWindow]] = Field(
        default=None,
        description="List of allowed time windows. Defaults to optimization general time window.",
        examples=[
            [
                {"start_time": "10:00", "duration": "2 hours"},
            ],
        ],
    )


class DevicesCommonSettings(SettingsBaseModel):
    """Base configuration for devices simulation settings."""

    batteries: Optional[list[BatteriesCommonSettings]] = Field(
        default=None,
        description="List of battery devices",
        examples=[[{"device_id": "battery1", "capacity_wh": 8000}]],
    )

    max_batteries: Optional[int] = Field(
        default=None,
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
        default=None,
        ge=0,
        description="Maximum number of electric vehicles that can be set",
        examples=[1, 2],
    )

    inverters: Optional[list[InverterCommonSettings]] = Field(
        default=None, description="List of inverters", examples=[[]]
    )

    max_inverters: Optional[int] = Field(
        default=None,
        ge=0,
        description="Maximum number of inverters that can be set",
        examples=[1, 2],
    )

    home_appliances: Optional[list[HomeApplianceCommonSettings]] = Field(
        default=None, description="List of home appliances", examples=[[]]
    )

    max_home_appliances: Optional[int] = Field(
        default=None,
        ge=0,
        description="Maximum number of home_appliances that can be set",
        examples=[1, 2],
    )


# Type used for indexing: (resource_id, optional actuator_id)
class ResourceKey(PydanticBaseModel, ConfigMixin):
    """Key identifying a resource and optionally an actuator."""

    resource_id: str
    actuator_id: Optional[str] = None

    def as_tuple(self) -> tuple[str, Optional[str]]:
        """Return the key as a tuple for internal dictionary indexing."""
        return (self.resource_id, self.actuator_id)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, ResourceKey):
            return NotImplemented
        return self.resource_id == other.resource_id and self.actuator_id == other.actuator_id


class ResourceRegistry(SingletonMixin, ConfigMixin, PydanticBaseModel):
    """Registry for collecting and retrieving device status reports for simulations.

    Maintains the latest and optionally historical status reports for each resource.
    """

    keep_history: bool = False
    history_size: int = 100

    latest: dict[ResourceKey, ResourceStatus] = Field(default_factory=dict)
    history: dict[ResourceKey, list[tuple[DateTime, ResourceStatus]]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _enforce_history_limits(self) -> "ResourceRegistry":
        """Ensure history list lengths respect the history_size limit."""
        if self.keep_history:
            for key, records in self.history.items():
                if len(records) > self.history_size:
                    self.history[key] = records[-self.history_size :]
        return self

    def update_status(self, key: ResourceKey, status: ResourceStatus) -> None:
        """Update the latest status and optionally store in history.

        Args:
            key (ResourceKey): Identifier for the resource.
            status (ResourceStatus): Status report to store.
        """
        self.latest[key] = status
        if self.keep_history:
            timestamp = getattr(status, "transition_timestamp", None) or to_datetime()
            self.history.setdefault(key, []).append((timestamp, status))
            if len(self.history[key]) > self.history_size:
                self.history[key] = self.history[key][-self.history_size :]

    def status_latest(self, key: ResourceKey) -> Optional[ResourceStatus]:
        """Retrieve the most recent status for a resource."""
        return self.latest.get(key)

    def status_history(self, key: ResourceKey) -> list[tuple[DateTime, ResourceStatus]]:
        """Retrieve historical status reports for a resource."""
        if not self.keep_history:
            raise RuntimeError("History tracking is disabled.")
        return self.history.get(key, [])

    def status_exists(self, key: ResourceKey) -> bool:
        """Check if a status report exists for the given resource.

        Args:
            key (ResourceKey): Identifier for the resource.
        """
        return key in self.latest

    def save(self) -> None:
        """Save the registry to file."""
        # Make explicit cast to make mypy happy
        cache_file = cast(
            TextIO, CacheFileStore().create(key="resource_registry", mode="w+", suffix=".json")
        )
        cache_file.seek(0)
        cache_file.write(self.model_dump_json(indent=4))

    def load(self) -> None:
        """Load registry state from file and update the current instance."""
        cache_file = CacheFileStore().get(key="resource_registry")
        if cache_file:
            cache_file.seek(0)
            data = json.load(cache_file)
            loaded = self.model_validate(data)

            self.keep_history = loaded.keep_history
            self.history_size = loaded.history_size
            self.latest = loaded.latest
            self.history = loaded.history


def get_resource_registry() -> ResourceRegistry:
    """Gets the EOS resource registry."""
    return ResourceRegistry()
