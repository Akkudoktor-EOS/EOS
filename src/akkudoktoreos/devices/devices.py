"""General configuration settings for simulated devices for optimization."""

import json
import re
from typing import Any, Optional, TextIO, cast

import numpy as np
from loguru import logger
from numpydantic import NDArray, Shape
from pydantic import Field, computed_field, field_validator, model_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.cache import CacheFileStore
from akkudoktoreos.core.coreabc import ConfigMixin, SingletonMixin
from akkudoktoreos.core.emplan import ResourceStatus
from akkudoktoreos.core.pydantic import ConfigDict, PydanticBaseModel
from akkudoktoreos.devices.devicesabc import DevicesBaseSettings
from akkudoktoreos.utils.datetimeutil import DateTime, TimeWindowSequence, to_datetime

# Default charge rates for battery
BATTERY_DEFAULT_CHARGE_RATES = np.linspace(0.0, 1.0, 11)  # 0.0, 0.1, ..., 1.0


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

    charge_rates: Optional[NDArray[Shape["*"], float]] = Field(
        default=BATTERY_DEFAULT_CHARGE_RATES,
        description=(
            "Charge rates as factor of maximum charging power [0.00 ... 1.00]. "
            "None triggers fallback to default charge-rates."
        ),
        examples=[[0.0, 0.25, 0.5, 0.75, 1.0], None],
    )

    min_soc_percentage: int = Field(
        default=0,
        ge=0,
        le=100,
        description=(
            "Minimum state of charge (SOC) as percentage of capacity [%]. "
            "This is the target SoC for charging"
        ),
        examples=[10],
    )

    max_soc_percentage: int = Field(
        default=100,
        ge=0,
        le=100,
        description="Maximum state of charge (SOC) as percentage of capacity [%].",
        examples=[100],
    )

    @field_validator("charge_rates", mode="before")
    def validate_and_sort_charge_rates(cls, v: Any) -> NDArray[Shape["*"], float]:
        # None means fallback to default values
        if v is None:
            return BATTERY_DEFAULT_CHARGE_RATES.copy()

        # Convert to numpy array
        if isinstance(v, str):
            # Remove brackets and split by comma or whitespace
            numbers = re.split(r"[,\s]+", v.strip("[]"))

            # Filter out any empty strings and convert to floats
            arr = np.array([float(x) for x in numbers if x])
        else:
            arr = np.array(v, dtype=float)

        # Must not be empty
        if arr.size == 0:
            raise ValueError("charge_rates must contain at least one value.")

        # Enforce bounds: 0.0 ≤ x ≤ 1.0
        if (arr < 0.0).any() or (arr > 1.0).any():
            raise ValueError("charge_rates must be within [0.0, 1.0].")

        # Remove duplicates + sort
        arr = np.unique(arr)
        arr.sort()

        return arr

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_key_soc_factor(self) -> str:
        """Measurement key for the battery state of charge (SoC) as factor of total capacity [0.0 ... 1.0]."""
        return f"{self.device_id}-soc-factor"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_key_power_l1_w(self) -> str:
        """Measurement key for the L1 power the battery is charged or discharged with [W]."""
        return f"{self.device_id}-power-l1-w"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_key_power_l2_w(self) -> str:
        """Measurement key for the L2 power the battery is charged or discharged with [W]."""
        return f"{self.device_id}-power-l2-w"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_key_power_l3_w(self) -> str:
        """Measurement key for the L3 power the battery is charged or discharged with [W]."""
        return f"{self.device_id}-power-l3-w"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_key_power_3_phase_sym_w(self) -> str:
        """Measurement key for the symmetric 3 phase power the battery is charged or discharged with [W]."""
        return f"{self.device_id}-power-3-phase-sym-w"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_keys(self) -> Optional[list[str]]:
        """Measurement keys for the battery stati that are measurements.

        Battery SoC, power.
        """
        keys: list[str] = [
            self.measurement_key_soc_factor,
            self.measurement_key_power_l1_w,
            self.measurement_key_power_l2_w,
            self.measurement_key_power_l3_w,
            self.measurement_key_power_3_phase_sym_w,
        ]
        return keys


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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_keys(self) -> Optional[list[str]]:
        """Measurement keys for the inverter stati that are measurements."""
        keys: list[str] = []
        return keys


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

    time_windows: Optional[TimeWindowSequence] = Field(
        default=None,
        description="Sequence of allowed time windows. Defaults to optimization general time window.",
        examples=[
            {
                "windows": [
                    {"start_time": "10:00", "duration": "2 hours"},
                ],
            },
        ],
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_keys(self) -> Optional[list[str]]:
        """Measurement keys for the home appliance stati that are measurements."""
        keys: list[str] = []
        return keys


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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measurement_keys(self) -> Optional[list[str]]:
        """Return the measurement keys for the resource/ device stati that are measurements."""
        keys: list[str] = []

        if self.max_batteries and self.batteries:
            for battery in self.batteries:
                keys.extend(battery.measurement_keys)
        if self.max_electric_vehicles and self.electric_vehicles:
            for electric_vehicle in self.electric_vehicles:
                keys.extend(electric_vehicle.measurement_keys)
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
        description="Latest resource status that was reported per resource key.",
        example=[],
    )
    history: dict[ResourceKey, list[tuple[DateTime, ResourceStatus]]] = Field(
        default_factory=dict,
        description="History of resource stati that were reported per resource key.",
        example=[],
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


def get_resource_registry() -> ResourceRegistry:
    """Gets the EOS resource registry."""
    return ResourceRegistry()
