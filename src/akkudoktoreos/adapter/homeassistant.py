"""Home Assistant adapter."""

import os
from contextvars import ContextVar
from typing import Any, ItemsView, KeysView, Optional, ValuesView

import requests
from loguru import logger
from pydantic import Field, RootModel, model_validator

from akkudoktoreos.adapter.adapterabc import AdapterProvider
from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.emplan import FRBCStorageStatus
from akkudoktoreos.core.ems import EnergyManagementStage
from akkudoktoreos.devices.devices import ResourceKey, get_resource_registry
from akkudoktoreos.utils.datetimeutil import to_datetime

# Supervisor API endpoint and token (injected automatically in add-on container)
SUPERVISOR_API = "http://supervisor/core/api"
TOKEN = os.environ.get("SUPERVISOR_TOKEN")

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}

resources_eos = get_resource_registry()

# Context variable to pass allowed_keys during validation
_allowed_keys_context: ContextVar[Optional[set[str]]] = ContextVar(
    "allowed_keys_context", default=None
)


class HomeAssistantEntityIdMapping(RootModel[dict[str, Optional[str]]]):
    """Dynamic mapping model that restricts keys based on allowed_keys parameter.

    This model validates mappings where:
    - Keys must be strings
    - Values are optional strings (str | None)
    - If allowed_keys is provided, only those keys are permitted
    - Missing allowed keys are auto-filled with None
    """

    root: dict[str, Optional[str]] = Field(
        default_factory=dict,
        json_schema_extra={
            "description": "Mapping of entity names to Home Assistant entity IDs",
            "examples": [
                {
                    "light": "light.living_room",
                    "switch": "switch.kitchen",
                    "sensor": "sensor.temperature",
                },
                {
                    "pv_production": "sensor.pv_energy_total_kwh",
                    "battery_soc": "sensor.battery_state_of_charge",
                    "grid_import": "sensor.grid_import_kwh",
                },
            ],
        },
    )

    def __init__(
        self,
        root: Optional[dict[str, Optional[str]]] = None,
        /,
        allowed_keys: Optional[set[str]] = None,
        **kwargs: Any,
    ) -> None:
        """Initialize a mapping-based RootModel with optional key restrictions.

        This initializer supports both explicit ``root`` dictionaries and keyword
        arguments. Keyword arguments are merged into the ``root`` dictionary and
        validated as part of the model. The optional ``allowed_keys`` parameter
        restricts which keys are permitted and is enforced by validators using a
        context variable.

        Examples:
            Using an explicit root dictionary:
                HomeAssistantEntityIdMapping(root={"pv_production": "sensor.a"})

            Using keyword arguments (equivalent to root dict initialization):
                HomeAssistantEntityIdMapping(pv_production="sensor.a", grid_import="sensor.b")

            Combining both forms (keyword args override root keys):
                HomeAssistantEntityIdMapping(
                    root={"pv_production": "sensor.x"},
                    grid_import="sensor.y",
                )

        Args:
            root (dict[str, Optional[str]] | None):
                Initial mapping data for the model. If omitted or ``None``,
                an empty mapping is used. Keyword arguments are merged into
                this dictionary.
            allowed_keys (set[str] | None):
                Optional set of valid keys for the mapping. If provided,
                validators will enforce membership in this set. If omitted,
                previously stored allowed keys on the instance (if any) are reused.
            **kwargs:
                Additional key-value pairs to be included in the mapping.
                These behave as if they were part of the ``root`` dictionary.

        Raises:
            ValidationError:
                If the merged mapping violates Pydantic validation rules or
                contains keys not permitted by ``allowed_keys``.
        """
        # Merge root and kwargs into a single dictionary
        if root is None:
            root = {}
        elif isinstance(root, dict):
            root = dict(root)  # Copy to avoid mutation issues
        # If root is not None and not a dict, let Pydantic validation handle it

        if kwargs:
            # Only merge kwargs if root is a dict
            if isinstance(root, dict):
                root.update(kwargs)

        # Manage context for allowed_keys
        token = None
        if allowed_keys is not None:
            token = _allowed_keys_context.set(allowed_keys)
        else:
            # Use previously stored allowed keys if available
            ak = (
                object.__getattribute__(self, "_allowed_keys")
                if hasattr(self, "_allowed_keys")
                else None
            )
            if ak:
                allowed_keys = ak

        try:
            # Validate via Pydantic RootModel
            super().__init__(root=root)

            # Store allowed_keys in the instance
            object.__setattr__(self, "_allowed_keys", allowed_keys or set())

        finally:
            if token is not None:
                _allowed_keys_context.reset(token)

    @model_validator(mode="before")
    @classmethod
    def validate_and_normalize(cls, data: Any) -> dict[str, Optional[str]]:
        """Validate data against allowed keys from context and normalize.

        Args:
            data: Raw mapping data

        Returns:
            Validated and normalized mapping

        Raises:
            ValueError: If data contains invalid keys
        """
        if not isinstance(data, dict):
            # Let Pydantic handle non-dict types
            return data

        # Get allowed_keys from context
        allowed_keys = _allowed_keys_context.get()

        # If no allowed_keys constraint, accept as-is
        if allowed_keys is None:
            return data

        # Check for invalid keys
        invalid = set(data.keys()) - allowed_keys
        if invalid:
            raise ValueError(f"Invalid keys: {invalid}. Allowed keys are: {allowed_keys}")

        # Create normalized dict with all allowed keys
        normalized = {key: data.get(key) for key in allowed_keys}
        return normalized

    @classmethod
    def with_defaults(cls, keys: set[str]) -> "HomeAssistantEntityIdMapping":
        """Create a new mapping pre-filled with None for all allowed keys.

        Args:
            keys: Keys to allow and initialize

        Returns:
            A fully initialized instance with all keys set to None
        """
        return cls({k: None for k in keys}, allowed_keys=keys)

    @property
    def allowed_keys(self) -> set[str]:
        """Get the allowed keys for this instance."""
        return getattr(self, "_allowed_keys", set())

    def set_allowed_keys(
        self, keys: set[str], validate: bool = True, auto_fill: bool = True
    ) -> None:
        """Update the allowed keys for this instance.

        Args:
            keys: New set of allowed keys
            validate: If True, validate current data against new keys
            auto_fill: If True auto-fill missing keys with None

        Raises:
            ValueError: If validate=True and current data contains keys not in the new allowed set
        """
        if validate and keys:
            # Check for invalid keys in current data
            invalid = set(self.root.keys()) - set(keys)
            if invalid:
                info_msg = f"Deleting invalid keys: {invalid}. New allowed keys are: {keys}"
                logger.info(info_msg)
                for key in invalid:
                    del self.root[key]

        if auto_fill:
            # Add missing keys with None
            for key in keys:
                if key not in self.root:
                    self.root[key] = None

        # Update allowed keys
        object.__setattr__(self, "_allowed_keys", keys)

    def add_allowed_keys(self, keys: set[str], auto_fill: bool = True) -> None:
        """Add new keys to the allowed keys set.

        Args:
            keys: Keys to add to the allowed set
            auto_fill: If True, automatically add new keys to root with None values
        """
        current_allowed = self.allowed_keys
        new_allowed = current_allowed | keys
        object.__setattr__(self, "_allowed_keys", new_allowed)

        if auto_fill:
            for key in keys:
                if key not in self.root:
                    self.root[key] = None

    def remove_allowed_keys(self, keys: set[str], remove_data: bool = False) -> None:
        """Remove keys from the allowed keys set.

        Args:
            keys: Keys to remove from the allowed set
            remove_data: If True, also remove these keys from the root data

        Raises:
            ValueError: If trying to remove keys that don't exist in allowed_keys
        """
        current_allowed = self.allowed_keys

        # Only validate if we have allowed keys set
        if current_allowed:
            not_in_allowed = keys - current_allowed
            if not_in_allowed:
                raise ValueError(f"Cannot remove keys not in allowed set: {not_in_allowed}")

        new_allowed = current_allowed - keys
        object.__setattr__(self, "_allowed_keys", new_allowed)

        if remove_data:
            for key in keys:
                self.root.pop(key, None)

    def __getitem__(self, key: str) -> Optional[str]:
        """Retrieve a value from the mapping.

        Args:
            key (str): The key to look up.

        Returns:
            Optional[str]: The value associated with the key, or ``None`` if the key
            exists but its value is ``None``.

        Raises:
            KeyError: If the key does not exist in the mapping.
        """
        return self.root[key]

    def __setitem__(self, key: str, value: Optional[str]) -> None:
        """Assign a value to a key in the mapping.

        This enables dict-like assignment syntax (``mapping[key] = value``).
        If ``_allowed_keys`` is defined, only keys in that set may be assigned.

        Args:
            key (str): The key to assign.
            value (Optional[str]): The value to assign to the key.

        Raises:
            ValueError: If ``key`` is not in the set of allowed keys.
        """
        allowed: set[str] = getattr(self, "_allowed_keys", set())
        if allowed and key not in allowed:
            error_msg = f"Key '{key}' not in allowed keys: {allowed}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        self.root[key] = value

    def validate_and_set(self, key: str, value: Optional[str]) -> None:
        """Validate and set a key-value pair.

        This method should be used when setting values through Pydantic's
        validation mechanism (e.g., from _set_nested_value).

        Args:
            key: The key to set
            value: The value to set

        Raises:
            ValueError: If the key is not in allowed_keys or value is invalid
        """
        # Validate key
        allowed: set[str] = getattr(self, "_allowed_keys", set())
        if allowed and key not in allowed:
            raise ValueError(f"Key '{key}' not in allowed keys: {allowed}")

        # Validate value type
        if value is not None and not isinstance(value, str):
            raise ValueError(f"Value must be a string or None, got {type(value)}")

        # Set the value (this will also trigger __setitem__ validation)
        self[key] = value

    def update(self, other: dict[str, Optional[str]]) -> None:
        """Update mapping with another dict, validating all keys.

        Args:
            other: Dictionary to update from

        Raises:
            ValueError: If any keys in other are not in allowed_keys
        """
        allowed: set[str] = getattr(self, "_allowed_keys", set())
        if allowed:
            invalid = set(other.keys()) - allowed
            if invalid:
                raise ValueError(
                    f"Cannot update with invalid keys: {invalid}. Allowed keys are: {allowed}"
                )
        self.root.update(other)

    def __setattr__(self, name: str, value: Any) -> None:
        """Intercept attribute assignment to validate root updates.

        This catches assignments like: mapping.root = {...}
        """
        # Allow Pydantic's internal attributes
        if name in (
            "root",
            "__dict__",
            "__pydantic_fields_set__",
            "__pydantic_extra__",
            "__pydantic_private__",
            "_allowed_keys",
        ):
            # If setting root and we have allowed_keys, validate
            if name == "root" and isinstance(value, dict):
                allowed: set[str] = getattr(self, "_allowed_keys", set())
                if allowed:
                    invalid = set(value.keys()) - allowed
                    if invalid:
                        raise ValueError(
                            f"Cannot assign root with invalid keys: {invalid}. "
                            f"Allowed keys are: {allowed}"
                        )
            super().__setattr__(name, value)
        else:
            raise AttributeError(f"Cannot set attribute '{name}' on HomeAssistantEntityIdMapping")

    def __contains__(self, key: str) -> bool:
        """Enable 'in' operator: key in mapping."""
        return key in self.root

    def items(self) -> ItemsView[str, Optional[str]]:
        """Return a dynamic view of the mapping’s key–value pairs.

        This enables dict-like iteration such as::

            for key, value in mapping.items():
                ...

        Returns:
            ItemsView[str, Optional[str]]: A view over the key–value pairs in the mapping.
        """
        return self.root.items()

    def keys(self) -> KeysView[str]:
        """Return a dynamic view of all keys in the mapping.

        Returns:
            KeysView[str]: A view over all keys in the mapping.
        """
        return self.root.keys()

    def values(self) -> ValuesView[Optional[str]]:
        """Return a dynamic view of all values in the mapping.

        Returns:
            ValuesView[Optional[str]]: A view over all values in the mapping.
        """
        return self.root.values()

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Dict-like get method."""
        return self.root.get(key, default)


class HomeAssistantAdapterCommonSettings(SettingsBaseModel):
    """Common settings for the home assistant adapter."""

    entity_id_pv_production_emr_kwh: Optional[list[str]] = Field(
        default=None,
        json_schema_extra={
            "description": "Entity ID(s) of PV production energy meter reading [kWh]",
            "examples": [
                ["sensor.pv_energy_total_kwh"],
                ["sensor.pv_emr1_kwh", "sensor.pv_emr2_kwh"],
            ],
        },
    )
    entity_id_battery_soc_factor: Optional[list[str]] = Field(
        default=None,
        json_schema_extra={
            "description": "Entity ID(s) of battery SoC factor [0.0..1.0]",
            "examples": [
                ["sensor.battery_soc_factor"],
                ["sensor.bat1_soc_factor", "sensor.bat2_soc_factor"],
            ],
        },
    )
    entity_id_ev_soc_factor: Optional[list[str]] = Field(
        default=None,
        json_schema_extra={
            "description": "Entity ID(s) of electric vehicle battery SoC factor [0.0..1.0]",
            "examples": [
                ["sensor.ev_soc_factor"],
                ["sensor.ev1_soc_factor", "sensor.ev2_soc_factor"],
            ],
        },
    )

    measurement_entity_ids: HomeAssistantEntityIdMapping = Field(
        default_factory=lambda: HomeAssistantEntityIdMapping(),
        json_schema_extra={
            "description": "Mapping of EOS measurement keys to Home Assistant entity IDs",
            "examples": [
                {
                    "pv_production": "sensor.pv_energy_total_kwh",
                    "battery_soc": "sensor.battery_state_of_charge",
                    "grid_import": "sensor.grid_import_kwh",
                    "grid_export": "sensor.grid_export_kwh",
                }
            ],
        },
    )

    optimization_solution_entity_ids: HomeAssistantEntityIdMapping = Field(
        default_factory=lambda: HomeAssistantEntityIdMapping(),
        json_schema_extra={
            "description": "Mapping of EOS optimization solution to Home Assistant entity IDs",
            "examples": [
                {
                    "battery_operation_mode_id": "sensor.battery_operation_mode_id",
                    "battery_operation_mode_factor": "sensor.battery_operation_mode_factor",
                }
            ],
        },
    )


# ----------------------------------------------
# Track config changes for adapter configuration
# ----------------------------------------------


def adapter_home_assistant_track_config(
    config_eos: Any, path: str, old_value: Any, value: Any
) -> None:
    """Track config changes."""
    if path.startswith("measurement"):
        measurement_keys = set(config_eos.measurement.keys)
        config_eos.adapter.homeassistant.measurement_entity_ids.set_allowed_keys(measurement_keys)
        logger.info(f"Home assistant adapter reconfigured - measurement keys: {measurement_keys}.")
    else:
        raise ValueError(f"Home assistant adapter shall not track '{path}'")


class HomeAssistantAdapter(AdapterProvider):
    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the adapter provider."""
        return "HomeAssistant"

    def set_sensor_state(
        self, entity_id: str, state_value: str, attributes: dict | None = None
    ) -> None:
        """Post or update a Home Assistant entity state.

        Args:
            entity_id (str): The Home Assistant entity ID to update.
            state_value (str): The new state value for the entity.
            attributes (dict | None): Optional dictionary of additional attributes.

        Raises:
            requests.RequestException: If the HTTP request to Home Assistant fails.

        Example:
            >>> set_sensor_state("sensor.energy_optimizer_status", "running")
        """
        url = f"{SUPERVISOR_API}/states/{entity_id}"
        data = {"state": state_value, "attributes": attributes or {}}
        resp = requests.post(url, headers=HEADERS, json=data, timeout=10)
        if resp.status_code not in (200, 201):
            error_msg = f"Failed to update {entity_id}: {resp.text}"
            logger.error(error_msg)
        else:
            debug_msg = f"Updated {entity_id} = {state_value}"
            logger.debug(debug_msg)

    def get_entity_state(self, entity_id: str) -> str | None:
        """Retrieve the current state of an entity from Home Assistant.

        Args:
            entity_id (str): The Home Assistant entity ID to query.

        Returns:
            str | None: The current state of the entity, or None if unavailable.

        Example:
            >>> state = get_entity_state("switch.living_room_lamp")
            >>> print(state)
            "on"
        """
        url = f"{SUPERVISOR_API}/states/{entity_id}"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.ok:
            data = resp.json()
            debug_msg = f"{entity_id}: {data['state']}"
            logger.debug(debug_msg)
            return data["state"]
        else:
            error_msg = f"Failed to read {entity_id}: {resp.text}"
            logger.error(error_msg)
            return None

    def _update_data(self) -> None:
        if not TOKEN:
            raise RuntimeError("Missing SUPERVISOR_TOKEN environment variable.")

        stage = self.ems.stage()
        if stage == EnergyManagementStage.DATA_ACQUISITION:
            for (
                measurement_key,
                entity_id,
            ) in self.config.adapter.homeassistant.measurement_entity_ids.items():
                if entity_id:
                    state = self.get_entity_state(entity_id)
                    logger.debug(f"Entity {entity_id}: {state}")
                    if state:
                        measurement_value = float(state)
                        self.measurement.update_value(
                            self.ems_start_datetime, measurement_key, measurement_value
                        )

            entities = self.config.adapter.homeassistant.entity_id_battery_soc_factor
            if entities:
                for index, entity in enumerate(entities):
                    state = self.get_entity_state(entity)
                    logger.debug(f"Entity {entity}: {state}")
                    if state:
                        soc_factor = float(state)
                        resource_key = ResourceKey(
                            resource_id=self.config.devices.batteries[index].device_id
                        )
                        resource_status = FRBCStorageStatus(present_fill_level=float(soc_factor))
                        resources_eos.update_status(resource_key, resource_status)

            # We got data - mark the update time
            self.update_datetime = to_datetime()

        if stage == EnergyManagementStage.CONTROL_DISPATCH:
            optimization_solution = self.ems.optimization_solution()
            if optimization_solution:
                # Prepare mapping
                df = optimization_solution.solution.to_dataframe()
                allowed_keys = df.columns.tolist()
                self.config.adapter.homeassistant.optimization_solution_entity_ids.set_allowed_keys(
                    allowed_keys
                )
