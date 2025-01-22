import json
from http import HTTPStatus
from typing import Optional, Union

import requests
from requests.exceptions import RequestException
from shad4fast.components.scroll_area import ScrollArea
from shad4fast.components.table import (
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
)

from akkudoktoreos.config.config import get_config
from akkudoktoreos.core.logging import get_logger

logger = get_logger(__name__)


def get_configuration(eos_host: Optional[str], eos_port: Optional[Union[str, int]]) -> list[dict]:
    config_eos = get_config()
    config_keys = config_eos.config_keys
    config_keys_read_only = config_eos.config_keys_read_only

    if eos_host is None:
        eos_host = config_eos.server_eos_host
    if eos_port is None:
        eos_port = config_eos.server_eos_port

    result = requests.Response()
    try:
        result = requests.get(f"http://{eos_host}:{eos_port}/v1/config")
    except RequestException as e:
        result.status_code = HTTPStatus.SERVICE_UNAVAILABLE
        warning_msg = f"{e}"
        logger.warning(warning_msg)

    config_values = {}
    if result.status_code == HTTPStatus.OK:
        config_values = json.loads(result.content)
    else:
        for config_key in config_keys:
            config_values[config_key] = "<unknown>"

    configs = []
    for config_key in config_keys:
        config = {}
        config["name"] = config_key
        config["value"] = config_values.get(config_key, getattr(config_eos, config_key))

        if config_key in config_keys_read_only:
            config["read-only"] = "ro"
            computed_field_info = config_eos.__pydantic_decorators__.computed_fields[
                config_key
            ].info
            config["default"] = "N/A"
            config["description"] = computed_field_info.description
            config["type"] = (
                str(computed_field_info.return_type)
                .replace("typing.", "")
                .replace("pathlib.", "")
                .replace("[", "[ ")
                .replace
            )
        else:
            config["read-only"] = "rw"
            field_info = config_eos.model_fields[config_key]
            config["default"] = field_info.default
            config["description"] = field_info.description
            config["type"] = (
                str(field_info.annotation).replace("typing.", "").replace("pathlib.", "")
            )

        configs.append(config)

    return configs


def Configuration(eos_host: Optional[str], eos_port: Optional[Union[str, int]]) -> Table:
    flds = "Name", "Type", "RO/RW", "Value", "Default", "Description"
    rows = [
        TableRow(
            TableCell(
                config["name"],
                cls="max-w-64 text-wrap break-all",
            ),
            TableCell(
                config["type"],
                cls="max-w-48 text-wrap break-all",
            ),
            TableCell(
                config["read-only"],
                cls="max-w-24 text-wrap break-all",
            ),
            TableCell(
                config["value"],
                cls="max-w-md text-wrap break-all",
            ),
            TableCell(config["default"], cls="max-w-48 text-wrap break-all"),
            TableCell(
                config["description"],
                cls="max-w-prose text-wrap",
            ),
            cls="even:bg-lime-100",
        )
        for config in sorted(get_configuration(eos_host, eos_port), key=lambda x: x["name"])
    ]
    head = TableHeader(*map(TableHead, flds), cls="bg-lime-400 text-left")
    return ScrollArea(
        Table(head, TableBody(*rows), cls="w-full"),
        cls="h-[75vh] w-full rounded-md",
    )
