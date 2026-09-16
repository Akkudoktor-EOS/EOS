"""General configuration settings for simulated devices for optimization."""

import json
from typing import Any, Optional, TextIO, cast

from loguru import logger
from pydantic import Field, computed_field, model_validator

from akkudoktoreos.config.configabc import ConfigScope, SettingsBaseModel
from akkudoktoreos.core.cache import CacheFileStore
from akkudoktoreos.core.coreabc import ConfigMixin, SingletonMixin
from akkudoktoreos.core.emplan import ResourceStatus
from akkudoktoreos.core.pydantic import ConfigDict, PydanticBaseModel
from akkudoktoreos.devices.settings.batterysettings import BatteriesCommonSettings
from akkudoktoreos.devices.settings.homeappliancesettings import (
    HomeApplianceCommonSettings,
)
from akkudoktoreos.devices.settings.invertersettings import InverterCommonSettings
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime


class DevicesCommonSettings(SettingsBaseModel):
    """Configuration for all controllable devices in the simulation.

    Every device collection is a ``dict[str, <Settings>]`` keyed by
    ``device_id``.  This makes config paths stable regardless of
    declaration order and lets each device settings class build its own
    config path from ``self.device_id`` without needing an external index.
    """

    # ---- Batteries ----
    batteries: Optional[dict[str, BatteriesCommonSettings]] = Field(
        default=None,
        json_schema_extra={
            "description": "Stationary battery storage devices, keyed by device_id.",
            "examples": [{"bat0": {"device_id": "bat0", "capacity_wh": 8000, "ports": []}}],
            "x-scope": [str(ConfigScope.GENETIC), str(ConfigScope.GENETIC0)],
        },
    )
    max_batteries: Optional[int] = Field(
        default=None,
        ge=0,
        json_schema_extra={
            "description": "Maximum number of batteries allowed.",
            "examples": [1],
            "x-scope": [str(ConfigScope.GENETIC), str(ConfigScope.GENETIC0)],
        },
    )

    # ---- Electric vehicles ----
    electric_vehicles: Optional[dict[str, BatteriesCommonSettings]] = Field(
        default=None,
        json_schema_extra={
            "description": "Electric vehicle battery packs, keyed by device_id.",
            "examples": [{"ev0": {"device_id": "ev0", "capacity_wh": 60000, "ports": []}}],
            "x-scope": [str(ConfigScope.GENETIC), str(ConfigScope.GENETIC0)],
        },
    )
    max_electric_vehicles: Optional[int] = Field(
        default=None,
        ge=0,
        json_schema_extra={
            "description": "Maximum number of EVs allowed.",
            "examples": [1],
            "x-scope": [str(ConfigScope.GENETIC), str(ConfigScope.GENETIC0)],
        },
    )

    # ---- Inverters ----
    inverters: Optional[dict[str, InverterCommonSettings]] = Field(
        default=None,
        json_schema_extra={
            "description": "Inverter devices, keyed by device_id.",
            "examples": [{}],
            "x-scope": [str(ConfigScope.GENETIC), str(ConfigScope.GENETIC0)],
        },
    )
    max_inverters: Optional[int] = Field(
        default=None,
        ge=0,
        json_schema_extra={
            "description": "Maximum number of inverters allowed.",
            "examples": [1],
            "x-scope": [str(ConfigScope.GENETIC), str(ConfigScope.GENETIC0)],
        },
    )

    # ---- Controllable home appliances ----
    home_appliances: dict[str, HomeApplianceCommonSettings] = Field(
        default_factory=dict,
        json_schema_extra={
            "description": "Shiftable home appliance devices, keyed by device_id.",
            "examples": [
                {
                    "dishwasher": {
                        "device_id": "dishwasher",
                        "consumption_wh": 1500,
                        "duration_h": 2.0,  # required field
                        "ports": [{"bus_id": "bus_ac", "port_id": "p_ac", "direction": "sink"}],
                    },
                },
            ],
            "x-scope": [str(ConfigScope.GENETIC), str(ConfigScope.GENETIC0)],
        },
    )
    max_home_appliances: Optional[int] = Field(
        default=None,
        ge=0,
        json_schema_extra={
            "description": "Maximum number of home appliances allowed.",
            "examples": [3],
            "x-scope": [str(ConfigScope.GENETIC), str(ConfigScope.GENETIC0)],
        },
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_keys(self) -> list[str]:
        """All measurement keys across all configured devices."""
        keys: list[str] = []
        for device_dict in [
            self.batteries,
            self.electric_vehicles,
            self.inverters,
            self.home_appliances,
        ]:
            for device in (device_dict or {}).values():
                keys.extend(device.measurement_keys)
        return keys


# Type used for indexing: (resource_id, optional actuator_id)
class ResourceKey(PydanticBaseModel):
    """Key identifying a resource and optionally an actuator."""

    resource_id: str
    actuator_id: Optional[str] = None

    model_config = ConfigDict(frozen=True)

    def __hash__(self) -> int:
        """Returns a stable hash based on the resource_id and actuator_id.

        Returns:
            int: Hash value derived from the resource_id and actuator_id.
        """
        return hash(self.resource_id + self.actuator_id if self.actuator_id else "")

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

    latest: dict[ResourceKey, ResourceStatus] = Field(
        default_factory=dict,
        json_schema_extra={
            "description": "Latest resource status that was reported per resource key.",
            "example": [],
        },
    )
    history: dict[ResourceKey, list[tuple[DateTime, ResourceStatus]]] = Field(
        default_factory=dict,
        json_schema_extra={
            "description": "History of resource stati that were reported per resource key.",
            "example": [],
        },
    )

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
        cache_file.truncate()  # Important to remove leftover data!

    def load(self) -> None:
        """Load registry state from file and update the current instance."""
        cache_file = CacheFileStore().get(key="resource_registry")
        if cache_file:
            try:
                cache_file.seek(0)
                data = json.load(cache_file)
                loaded = self.__class__.model_validate(data)

                self.keep_history = loaded.keep_history
                self.history_size = loaded.history_size
                self.latest = loaded.latest
                self.history = loaded.history
            except Exception as e:
                logger.error("Can not load resource registry: {}", e)
