"""Home Assistant adapter."""

import os
from typing import Optional, Union

import pandas as pd
import requests
from loguru import logger
from pydantic import Field, computed_field, field_validator

from akkudoktoreos.adapter.adapterabc import AdapterProvider
from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.emplan import (
    DDBCInstruction,
    FRBCInstruction,
)
from akkudoktoreos.core.ems import EnergyManagementStage
from akkudoktoreos.devices.devices import get_resource_registry
from akkudoktoreos.utils.datetimeutil import to_datetime

# Supervisor API endpoint and token (injected automatically in add-on container)
CORE_API = "http://supervisor/core/api"
TOKEN = os.environ.get("SUPERVISOR_TOKEN")

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}

HOMEASSISTANT_ENTITY_ID_PREFIX = "sensor.eos_"

resources_eos = get_resource_registry()


class HomeAssistantAdapterCommonSettings(SettingsBaseModel):
    """Common settings for the home assistant adapter."""

    config_entity_ids: Optional[dict[str, str]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Mapping of EOS config keys to Home Assistant entity IDs.\n"
                "The config key has to be given by a ‘/’-separated path\n"
                "e.g. devices/batteries/0/capacity_wh"
            ),
            "examples": [
                {
                    "devices/batteries/0/capacity_wh": "sensor.battery1_capacity",
                }
            ],
        },
    )

    load_emr_entity_ids: Optional[list[str]] = Field(
        default=None,
        json_schema_extra={
            "description": "Entity ID(s) of load energy meter readings [kWh]",
            "examples": [
                ["sensor.load_energy_total_kwh"],
                ["sensor.load_emr1_kwh", "sensor.load_emr2_kwh"],
            ],
        },
    )

    grid_export_emr_entity_ids: Optional[list[str]] = Field(
        default=None,
        json_schema_extra={
            "description": "Entity ID(s) of export to grid energy meter readings [kWh]",
            "examples": [
                ["sensor.grid_export_energy_total_kwh"],
            ],
        },
    )

    grid_import_emr_entity_ids: Optional[list[str]] = Field(
        default=None,
        json_schema_extra={
            "description": "Entity ID(s) of import from grid energy meter readings [kWh]",
            "examples": [
                ["sensor.grid_import_energy_total_kwh"],
            ],
        },
    )

    pv_production_emr_entity_ids: Optional[list[str]] = Field(
        default=None,
        json_schema_extra={
            "description": "Entity ID(s) of PV production energy meter readings [kWh]",
            "examples": [
                ["sensor.pv_energy_total_kwh"],
                ["sensor.pv_emr1_kwh", "sensor.pv_emr2_kwh"],
            ],
        },
    )

    device_measurement_entity_ids: Optional[dict[str, str]] = Field(
        default=None,
        json_schema_extra={
            "description": "Mapping of EOS measurement keys used by device (resource) simulations to Home Assistant entity IDs.",
            "examples": [
                {
                    "ev11_soc_factor": "sensor.ev11_soc_factor",
                    "battery1_soc_factor": "sensor.battery1_soc_factor",
                }
            ],
        },
    )

    device_instruction_entity_ids: Optional[list[str]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Entity IDs for device (resource) instructions to be updated by EOS.\n"
                f"The device ids (resource ids) have to be prepended by '{HOMEASSISTANT_ENTITY_ID_PREFIX}' to build the entity_id.\n"
                f"E.g. The instruction for device id 'battery1' becomes the entity_id "
                f"'{HOMEASSISTANT_ENTITY_ID_PREFIX}battery1'."
            ),
            "examples": [
                [
                    f"{HOMEASSISTANT_ENTITY_ID_PREFIX}battery1",
                ]
            ],
        },
    )

    solution_entity_ids: Optional[list[str]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Entity IDs for optimization solution keys to be updated by EOS.\n"
                f"The solution keys have to be prepended by '{HOMEASSISTANT_ENTITY_ID_PREFIX}' to build the entity_id.\n"
                f"E.g. solution key 'battery1_idle_op_mode' becomes the entity_id "
                f"'{HOMEASSISTANT_ENTITY_ID_PREFIX}battery1_idle_op_mode'."
            ),
            "examples": [
                [
                    f"{HOMEASSISTANT_ENTITY_ID_PREFIX}battery1_idle_mode_mode",
                ]
            ],
        },
    )

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def homeassistant_entity_ids(self) -> list[str]:
        """Entity IDs available at Home Assistant."""
        try:
            from akkudoktoreos.adapter.adapter import get_adapter

            adapter_eos = get_adapter()
            result = adapter_eos.provider_by_id("HomeAssistant").get_homeassistant_entity_ids()
        except:
            return []
        return result

    @computed_field  # type: ignore[prop-decorator]
    @property
    def eos_solution_entity_ids(self) -> list[str]:
        """Entity IDs for optimization solution available at EOS."""
        try:
            from akkudoktoreos.adapter.adapter import get_adapter

            adapter_eos = get_adapter()
            result = adapter_eos.provider_by_id("HomeAssistant").get_eos_solution_entity_ids()
        except:
            return []
        return result

    @computed_field  # type: ignore[prop-decorator]
    @property
    def eos_device_instruction_entity_ids(self) -> list[str]:
        """Entity IDs for energy management instructions available at EOS."""
        try:
            from akkudoktoreos.adapter.adapter import get_adapter

            adapter_eos = get_adapter()
            result = adapter_eos.provider_by_id(
                "HomeAssistant"
            ).get_eos_device_instruction_entity_ids()
        except:
            return []
        return result

    # Validators
    @field_validator("solution_entity_ids", mode="after")
    @classmethod
    def validate_solution_entity_ids(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is None:
            return None
        for entity_id in value:
            if not entity_id.startswith(HOMEASSISTANT_ENTITY_ID_PREFIX):
                raise ValueError(
                    f"Invalid optimization solution entity id '{entity_id}': prefix '{HOMEASSISTANT_ENTITY_ID_PREFIX}' expected."
                )
        return value

    @field_validator("device_instruction_entity_ids", mode="after")
    @classmethod
    def validate_device_instruction_entity_ids(
        cls, value: Optional[list[str]]
    ) -> Optional[list[str]]:
        if value is None:
            return None
        for entity_id in value:
            if not entity_id.startswith(HOMEASSISTANT_ENTITY_ID_PREFIX):
                raise ValueError(
                    f"Invalid instruction entity id '{entity_id}': prefix '{HOMEASSISTANT_ENTITY_ID_PREFIX}' expected."
                )
        return value


class HomeAssistantAdapter(AdapterProvider):
    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the adapter provider."""
        return "HomeAssistant"

    def get_homeassistant_entity_ids(self) -> list[str]:
        """Retrieve the available entity IDs from Home Assistant.

        Returns:
            list[str]: The available entity IDs, or [].

        Example:
            >>> entity_ids = get_homeassistant_entity_ids()
            >>> print(entity_ids)
            ["sensor.pv_all", "sensor.battery1_soc"]
        """
        if not TOKEN:
            raise RuntimeError("Missing SUPERVISOR_TOKEN environment variable.")

        entity_ids = []

        url = f"{CORE_API}/states"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.ok:
            data = resp.json()
            entity_ids = [
                entity["entity_id"]
                for entity in data
                if not entity["entity_id"].startswith(HOMEASSISTANT_ENTITY_ID_PREFIX)
            ]
            debug_msg = f"homeassistant_entity_ids: {entity_ids}"
            logger.debug(debug_msg)
        else:
            error_msg = f"Failed to read entity states: {resp.text}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        return sorted(entity_ids)

    def _entity_id_from_solution_key(self, key: str) -> str:
        return HOMEASSISTANT_ENTITY_ID_PREFIX + key

    def get_eos_solution_entity_ids(self) -> list[str]:
        """Retrieve the available entity IDs for the EOS optimization solution.

        Returns:
            list[str]: The available entity IDs, or [].
        """
        solution_entity_ids = []
        try:
            optimization_solution_keys = self.config.optimization.keys
            for key in sorted(optimization_solution_keys):
                solution_entity_ids.append(self._entity_id_from_solution_key(key))
        except:
            solution_entity_ids = []
        return solution_entity_ids

    def _entity_id_from_resource_id(self, resource_id: str) -> str:
        return HOMEASSISTANT_ENTITY_ID_PREFIX + resource_id

    def get_eos_device_instruction_entity_ids(self) -> list[str]:
        """Retrieve the available entity IDs for the EOS energy management plan instructions.

        Returns:
            list[str]: The available entity IDs, or [].
        """
        instruction_entity_ids = []
        plan = self.ems.plan()
        if plan:
            resource_ids = plan.get_resources()
            for resource_id in resource_ids:
                instruction_entity_ids.append(self._entity_id_from_resource_id(resource_id))
        return sorted(instruction_entity_ids)

    def set_entity_state(
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
            >>> set_entity_state("sensor.energy_optimizer_status", "running")
        """
        if not TOKEN:
            raise RuntimeError("Missing SUPERVISOR_TOKEN environment variable.")

        url = f"{CORE_API}/states/{entity_id}"
        data = {"state": state_value, "attributes": attributes or {}}
        resp = requests.post(url, headers=HEADERS, json=data, timeout=10)
        if resp.status_code not in (200, 201):
            error_msg = f"Failed to update {entity_id}: {resp.text}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        else:
            debug_msg = f"Updated {entity_id} = {state_value}"
            logger.debug(debug_msg)

    def get_entity_state(self, entity_id: str) -> str:
        """Retrieve the current state of an entity from Home Assistant.

        Args:
            entity_id (str): The Home Assistant entity ID to query.

        Returns:
            str: The current state of the entity.

        Example:
            >>> state = get_entity_state("switch.living_room_lamp")
            >>> print(state)
            "on"
        """
        if not TOKEN:
            raise RuntimeError("Missing SUPERVISOR_TOKEN environment variable.")

        url = f"{CORE_API}/states/{entity_id}"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.ok:
            data = resp.json()
            debug_msg = f"{entity_id}: {data['state']}"
            logger.debug(debug_msg)
            return data["state"]
        else:
            error_msg = f"Failed to read {entity_id}: {resp.text}"
            logger.error(error_msg)
            raise ValueError(error_msg)

    def _convert_entity_state(self, state: str) -> Union[bool, float, str, None]:
        """Convert a Home Assistant entity state to a Python value.

        This method converts the raw ``state`` string of a Home Assistant entity
        into an appropriate Python type, following Home Assistant's global
        state model and commonly used domain semantics.

        Conversion rules:

        **Availability states**
        - ``"unavailable"``, ``"unknown"``, ``"none"`` → ``None``

        **Binary / boolean states**
        Used by binary sensors and many device domains:
        - ``"on"``, ``"true"``, ``"yes"``, ``"open"``, ``"opening"``,
        ``"locked"``, ``"home"``, ``"detected"``, ``"active"`` → ``True``
        - ``"off"``, ``"false"``, ``"no"``, ``"closed"``, ``"closing"``,
        ``"unlocked"``, ``"not_home"``, ``"clear"``, ``"idle"`` → ``False``

        **Numeric states**
        - Values that can be parsed as numbers are converted to ``float``.
        This covers most sensor entities (temperature, power, energy, etc.).

        **Other states**
        - Any remaining states (e.g. ``"playing"``, ``"paused"``,
        ``"cooling"``, ``"heating"``, ``"standby"``, ``"jammed"``) are
        returned as their original string value.

        The input state is normalized using ``strip()`` and ``lower()`` before
        conversion. If numeric conversion fails, the original unmodified
        state string is returned.

        Args:
            state: Raw entity state as provided by Home Assistant.

        Returns:
            The converted entity state as one of:
            ``None``, ``bool``, ``float``, or ``str``.
        """
        raw_state = state
        value = state.strip().lower()

        # Availability / unknown states
        if value in {"unavailable", "unknown", "none"}:
            return None

        # States that semantically represent True
        if value in {
            "on",
            "true",
            "yes",
            "y",
            "open",
            "opening",
            "locked",
            "home",
            "detected",
            "active",
        }:
            return True

        # States that semantically represent False
        if value in {
            "off",
            "false",
            "no",
            "n",
            "closed",
            "closing",
            "unlocked",
            "not_home",
            "clear",
            "idle",
        }:
            return False

        # Numeric states (sensors, counters, percentages, etc.)
        try:
            return float(value)
        except ValueError:
            # Preserve original state for enums and free-text states
            return raw_state

    def _update_data(self) -> None:
        stage = self.ems.stage()
        if stage == EnergyManagementStage.DATA_ACQUISITION:
            # Sync configuration
            entity_ids = self.config.adapter.homeassistant.config_entity_ids
            if entity_ids:
                for (
                    config_key,
                    entity_id,
                ) in entity_ids.items():
                    try:
                        state = self.get_entity_state(entity_id)
                        logger.debug(f"Entity {entity_id}: {state}")
                        value = self._convert_entity_state(state)
                        if value:
                            self.config.set_nested_value(config_key, value)
                    except Exception as e:
                        logger.error(f"{e}")

            # Retrieve measurements necessary for device simulations
            entity_ids = self.config.adapter.homeassistant.device_measurement_entity_ids
            if entity_ids:
                for (
                    measurement_key,
                    entity_id,
                ) in entity_ids.items():
                    if entity_id:
                        try:
                            state = self.get_entity_state(entity_id)
                            logger.debug(f"Entity {entity_id}: {state}")
                            if state:
                                measurement_value = float(state)
                                self.measurement.update_value(
                                    self.ems_start_datetime, measurement_key, measurement_value
                                )
                        except Exception as e:
                            logger.error(f"{e}")

            # Retrieve measurements for load prediction
            entity_ids = self.config.adapter.homeassistant.load_emr_entity_ids
            if entity_ids:
                measurement_keys = self.config.measurement.load_emr_keys
                if measurement_keys is None:
                    measurement_keys = []
                for entity_id in entity_ids:
                    measurement_key = entity_id
                    if measurement_key not in measurement_keys:
                        measurement_keys.append(measurement_key)
                        self.config.measurement.load_emr_keys = measurement_keys
                    try:
                        state = self.get_entity_state(entity_id)
                        logger.debug(f"Entity {entity_id}: {state}")
                        if state:
                            measurement_value = float(state)
                            self.measurement.update_value(
                                self.ems_start_datetime, measurement_key, measurement_value
                            )
                    except Exception as e:
                        logger.error(f"{e}")

            # Retrieve export to grid measurements
            entity_ids = self.config.adapter.homeassistant.grid_export_emr_entity_ids
            if entity_ids:
                measurement_keys = self.config.measurement.grid_export_emr_keys
                if measurement_keys is None:
                    measurement_keys = []
                for entity_id in entity_ids:
                    measurement_key = entity_id
                    if measurement_key not in measurement_keys:
                        measurement_keys.append(measurement_key)
                        self.config.measurement.grid_export_emr_keys = measurement_keys
                    try:
                        state = self.get_entity_state(entity_id)
                        logger.debug(f"Entity {entity_id}: {state}")
                        if state:
                            measurement_value = float(state)
                            self.measurement.update_value(
                                self.ems_start_datetime, measurement_key, measurement_value
                            )
                    except Exception as e:
                        logger.error(f"{e}")

            # Retrieve import from grid measurements
            entity_ids = self.config.adapter.homeassistant.grid_import_emr_entity_ids
            if entity_ids:
                measurement_keys = self.config.measurement.grid_import_emr_keys
                if measurement_keys is None:
                    measurement_keys = []
                for entity_id in entity_ids:
                    measurement_key = entity_id
                    if measurement_key not in measurement_keys:
                        measurement_keys.append(measurement_key)
                        self.config.measurement.grid_import_emr_keys = measurement_keys
                    try:
                        state = self.get_entity_state(entity_id)
                        logger.debug(f"Entity {entity_id}: {state}")
                        if state:
                            measurement_value = float(state)
                            self.measurement.update_value(
                                self.ems_start_datetime, measurement_key, measurement_value
                            )
                    except Exception as e:
                        logger.error(f"{e}")

            # Retrieve measurements for PV prediction
            entity_ids = self.config.adapter.homeassistant.pv_production_emr_entity_ids
            if entity_ids:
                measurement_keys = self.config.measurement.pv_production_emr_keys
                if measurement_keys is None:
                    measurement_keys = []
                for entity_id in entity_ids:
                    measurement_key = entity_id
                    if measurement_key not in measurement_keys:
                        measurement_keys.append(measurement_key)
                        self.config.measurement.pv_production_emr_keys = measurement_keys
                    try:
                        state = self.get_entity_state(entity_id)
                        logger.debug(f"Entity {entity_id}: {state}")
                        if state:
                            measurement_value = float(state)
                            self.measurement.update_value(
                                self.ems_start_datetime, measurement_key, measurement_value
                            )
                    except Exception as e:
                        logger.error(f"{e}")

            # We got data - mark the update time
            self.update_datetime = to_datetime()

        if stage == EnergyManagementStage.CONTROL_DISPATCH:
            # Currently active optimization solution
            optimization_solution = self.ems.optimization_solution()
            entity_ids = self.config.adapter.homeassistant.solution_entity_ids
            if optimization_solution and entity_ids:
                df = optimization_solution.solution.to_dataframe()
                now = pd.Timestamp.now(tz=df.index.tz)
                row = df.loc[:now].iloc[-1]  # Last known value before now
                for entity_id in entity_ids:
                    solution_key = entity_id[len(HOMEASSISTANT_ENTITY_ID_PREFIX) :]
                    try:
                        self.set_entity_state(entity_id, row[solution_key])
                    except Exception as e:
                        logger.error(f"{e}")
            # Currently active instructions
            instructions = self.ems.plan().get_active_instructions()
            entity_ids = self.config.adapter.homeassistant.device_instruction_entity_ids
            if instructions and entity_ids:
                for instruction in instructions:
                    entity_id = self._entity_id_from_resource_id(instruction.resource_id)
                    if entity_id in entity_ids:
                        if isinstance(instruction, (DDBCInstruction, FRBCInstruction)):
                            state = instruction.operation_mode_id.lower()
                            attributes = {
                                "operation_mode_factor": instruction.operation_mode_factor,
                            }
                            try:
                                self.set_entity_state(entity_id, state, attributes)
                            except Exception as e:
                                logger.error(f"{e}")
