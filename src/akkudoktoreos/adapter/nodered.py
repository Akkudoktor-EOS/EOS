"""Nod-RED adapter."""

from typing import Optional, Union

import requests
from loguru import logger
from pydantic import Field, field_validator

from akkudoktoreos.adapter.adapterabc import AdapterProvider
from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.emplan import DDBCInstruction, FRBCInstruction
from akkudoktoreos.core.ems import EnergyManagementStage
from akkudoktoreos.server.server import get_default_host, validate_ip_or_hostname
from akkudoktoreos.utils.datetimeutil import to_datetime


class NodeREDAdapterCommonSettings(SettingsBaseModel):
    r"""Common settings for the NodeRED adapter.

    The Node-RED adapter sends to HTTP IN nodes.

    This is the example flow:

    [HTTP In \\<URL\\>] -> [Function (parse payload)] -> [Debug] -> [HTTP Response]

    There are two URLs that are used:

    - GET /eos/data_aquisition
      The GET is issued before the optimization.
    - POST /eos/control_dispatch
      The POST is issued after the optimization.
    """

    host: Optional[str] = Field(
        default=get_default_host(),
        json_schema_extra={
            "description": "Node-RED server IP address. Defaults to 127.0.0.1.",
            "examples": ["127.0.0.1", "localhost"],
        },
    )
    port: Optional[int] = Field(
        default=1880,
        json_schema_extra={
            "description": "Node-RED server IP port number. Defaults to 1880.",
            "examples": [
                1880,
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


class NodeREDAdapter(AdapterProvider):
    def provider_id(self) -> str:
        """Return the unique identifier for the adapter provider."""
        return "NodeRED"

    def _update_data(self) -> None:
        """Custom adapter data update logic.

        Data update may be requested at different stages of energy management. The stage can be
        detected by self.ems.stage().
        """
        server = f"http://{self.config.adapter.nodered.host}:{self.config.adapter.nodered.port}"

        data: Optional[dict[str, Union[str, float]]] = None
        stage = self.ems.stage()
        if stage == EnergyManagementStage.CONTROL_DISPATCH:
            data = {}
            # currently active instructions
            instructions = self.ems.plan().get_active_instructions()
            for instruction in instructions:
                idx = instruction.id.find("@")
                resource_id = instruction.id[:idx] if idx != -1 else instruction.id
                operation_mode_id = "<unknown>"
                operation_mode_factor = 0.0
                if isinstance(instruction, (DDBCInstruction, FRBCInstruction)):
                    operation_mode_id = instruction.operation_mode_id
                    operation_mode_factor = instruction.operation_mode_factor
                data[f"{resource_id}_op_mode"] = operation_mode_id
                data[f"{resource_id}_op_factor"] = operation_mode_factor
        elif stage == EnergyManagementStage.DATA_ACQUISITION:
            data = {}

        if data is None:
            return

        logger.info(f"NodeRED {str(stage).lower()} at {server}: {data}")

        try:
            error_msg = None
            if stage == EnergyManagementStage.CONTROL_DISPATCH:
                response = requests.post(f"{server}/eos/{str(stage).lower()}", json=data, timeout=5)
            elif stage == EnergyManagementStage.DATA_ACQUISITION:
                response = requests.get(f"{server}/eos/{str(stage).lower()}", json=data, timeout=5)
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            try:
                # Try to get 'detail' from the JSON response
                detail = response.json().get(
                    "detail", f"No error details for data '{data}' '{response.text}'"
                )
            except ValueError:
                # Response is not JSON
                detail = f"No error details for data '{data}' '{response.text}'"
            error_msg = f"NodeRED `{str(stage).lower()}` fails at `{server}`: {detail}"
        except Exception as e:
            error_msg = f"NodeRED `{str(stage).lower()}` fails at `{server}`: {e}"
        if error_msg:
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        if stage == EnergyManagementStage.DATA_ACQUISITION:
            data = response.json()

            # We got data - mark the update time
            self.update_datetime = to_datetime()
