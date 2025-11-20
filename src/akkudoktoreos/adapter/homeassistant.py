"""Home Assistant adapter."""

import os
from typing import Optional

import requests
from pydantic import Field, field_validator

from akkudoktoreos.adapter.adapterabc import AdapterProvider
from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.server.server import get_default_host, validate_ip_or_hostname

# Supervisor API endpoint and token (injected automatically in add-on container)
SUPERVISOR_API = "http://supervisor/core/api"
TOKEN = os.environ.get("SUPERVISOR_TOKEN")

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}

# Global in-memory state (for demo purposes)
state = {"mode": "auto"}


class HomeAssistantAdapterCommonSettings(SettingsBaseModel):
    """Common settings for home assistant adapter provider."""

    host: Optional[str] = Field(
        default=get_default_host(),
        json_schema_extra={
            "description": "Home Assitant server IP address. Defaults to 127.0.0.1.",
            "examples": ["127.0.0.1", "localhost"],
        },
    )
    port: Optional[int] = Field(
        default=8123,
        json_schema_extra={
            "description": "Home Assistant server IP port number. Defaults to 8123.",
            "examples": [
                8123,
            ],
        },
    )

    @field_validator("host", mode="before")
    def validate_server_host(cls, value: Optional[str]) -> Optional[str]:
        if isinstance(value, str):
            value = validate_ip_or_hostname(value)
        return value

    @field_validator("port")
    def validate_server_port(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and not (1024 <= value <= 49151):
            raise ValueError("Server port number must be between 1024 and 49151.")
        return value


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
            print(f"Failed to update {entity_id}: {resp.text}")
        else:
            print(f"Updated {entity_id} = {state_value}")

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
            print(f"{entity_id}: {data['state']}")
            return data["state"]
        else:
            print(f"Failed to read {entity_id}: {resp.text}")
            return None

    def _update_data(self) -> None:
        if not TOKEN:
            raise RuntimeError("Missing SUPERVISOR_TOKEN environment variable.")
