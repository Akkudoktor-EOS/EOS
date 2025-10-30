"""Home Assistant adapter."""

import os
from typing import Any, Optional

import requests
from loguru import logger
from pydantic import Field, RootModel, model_validator

from akkudoktoreos.adapter.adapterabc import AdapterProvider
from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.emplan import FRBCStorageStatus
from akkudoktoreos.devices.devices import ResourceKey, get_resource_registry

# Supervisor API endpoint and token (injected automatically in add-on container)
SUPERVISOR_API = "http://supervisor/core/api"
TOKEN = os.environ.get("SUPERVISOR_TOKEN")

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}

# Global in-memory state (for demo purposes)
resources_eos = get_resource_registry()


class HomeAssistantEntityIdMapping(RootModel[dict[str, Optional[str]]], SettingsBaseModel):
    """A dynamic Pydantic root model mapping str → Optional[str].

    This model stores a mapping from string keys to optional string values and
    enforces that only keys listed in :meth:`allowed_keys` are permitted.
    Missing allowed keys are automatically added with a default value of ``None``.

    The allowed key set can be changed at runtime.
    """

    # Internal runtime-modifiable key store
    _dynamic_allowed_keys: set[str] = set()

    # ----------------------------------------------------------------------
    # Allowed keys management
    # ----------------------------------------------------------------------
    @classmethod
    def allowed_keys(cls) -> set[str]:
        """Return the current set of allowed mapping keys.

        Returns:
            Set[str]: Valid mapping keys.
        """
        return cls._dynamic_allowed_keys

    @classmethod
    def set_allowed_keys(cls, keys: set[str]) -> None:
        """Set the allowed keys at runtime.

        Args:
            keys (Set[str]): Keys that should be permitted in the mapping.
        """
        cls._dynamic_allowed_keys = set(keys)

    # ----------------------------------------------------------------------
    # Validation logic
    # ----------------------------------------------------------------------
    @model_validator(mode="before")
    @classmethod
    def validate_and_add_defaults(cls, data: Any) -> dict[str, Optional[str]]:
        """Validate input data and ensure missing allowed keys default to ``None``.

        This validator:
        * Checks all keys are in :meth:`allowed_keys`.
        * Inserts missing keys with a default value ``None``.

        Args:
            data (Any): Raw input passed to the model.

        Raises:
            ValueError: If the input contains keys not in the allowed key set.

        Returns:
            dict[str, Optional[str]]: Validated and completed mapping.
        """
        if not isinstance(data, dict):
            return data

        allowed = cls.allowed_keys()

        # Reject keys not in the allowed list
        invalid = set(data.keys()) - allowed
        if invalid:
            raise ValueError(f"Invalid keys: {invalid}. Allowed keys: {allowed}")

        # Add missing allowed keys
        for key in allowed:
            data.setdefault(key, None)

        return data

    # ----------------------------------------------------------------------
    # Initialization factory
    # ----------------------------------------------------------------------
    @classmethod
    def with_defaults(cls, keys: set[str]) -> "HomeAssistantEntityIdMapping":
        """Create a new instance with all given keys set to ``None``.

        Args:
            keys (Set[str]): Keys to initialize and allow.

        Returns:
            HomeAssistantEntityIdMapping: A fully initialized mapping.
        """
        cls.set_allowed_keys(keys)
        return cls.model_validate({k: None for k in keys})


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
