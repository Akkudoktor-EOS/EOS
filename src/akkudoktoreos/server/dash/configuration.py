import json
from functools import reduce
from http import HTTPStatus
from typing import Any, Dict, List, Optional, TypeVar, Union

import requests
from monsterui.franken import Table, Tbody, Td, Th, Thead, Tr
from pydantic.fields import ComputedFieldInfo, FieldInfo
from pydantic_core import PydanticUndefined
from requests.exceptions import RequestException

from akkudoktoreos.config.config import get_config
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.server.dash.components import ScrollArea

logger = get_logger(__name__)
config_eos = get_config()

T = TypeVar("T")


def get_nested_value(
    dictionary: Dict[str, Any], keys: List[str], default: Optional[T] = None
) -> Union[Any, T]:
    """Robust nested dictionary value retrieval with type checking.

    Args:
        dictionary: The nested dictionary to search
        keys: A list of keys representing the path to the desired value
        default: Value to return if the full path is not found

    Returns:
        The value at the specified nested path, or the default value if not found

    Raises:
        TypeError: If the first argument is not a dictionary or keys is not a list
    """
    # Validate input type
    if not isinstance(dictionary, dict):
        raise TypeError("First argument must be a dictionary")

    # Validate keys input
    if not isinstance(keys, list):
        raise TypeError("Keys must be provided as a list")

    # Empty key list returns the entire dictionary
    if not keys:
        return dictionary

    try:
        # Use reduce for a functional approach
        return reduce(lambda d, key: d[key], keys, dictionary)
    except (KeyError, TypeError):
        return default


def get_default_value(field_info: Union[FieldInfo, ComputedFieldInfo], regular_field: bool) -> Any:
    default_value = ""
    if regular_field:
        if (val := field_info.default) is not PydanticUndefined:
            default_value = val
    else:
        default_value = "N/A"
    return default_value


def resolve_nested_types(field_type: Any, parent_types: list[str]) -> list[tuple[Any, list[str]]]:
    resolved_types: list[tuple[Any, list[str]]] = []

    origin = getattr(field_type, "__origin__", field_type)
    if origin is Union:
        for arg in getattr(field_type, "__args__", []):
            if arg is not type(None):
                resolved_types.extend(resolve_nested_types(arg, parent_types))
    else:
        resolved_types.append((field_type, parent_types))

    return resolved_types


def configuration(values: dict) -> list:
    configs = []
    inner_types: set[type[PydanticBaseModel]] = set()

    for field_name, field_info in list(config_eos.model_fields.items()) + list(
        config_eos.model_computed_fields.items()
    ):

        def extract_nested_models(
            subfield_info: Union[ComputedFieldInfo, FieldInfo], parent_types: list[str]
        ) -> None:
            regular_field = isinstance(subfield_info, FieldInfo)
            subtype = subfield_info.annotation if regular_field else subfield_info.return_type

            if subtype in inner_types:
                return

            nested_types = resolve_nested_types(subtype, [])
            found_basic = False
            for nested_type, nested_parent_types in nested_types:
                if not isinstance(nested_type, type) or not issubclass(
                    nested_type, PydanticBaseModel
                ):
                    if found_basic:
                        continue

                    config = {}
                    config["name"] = ".".join(parent_types)
                    config["value"] = str(get_nested_value(values, parent_types, "<unknown>"))
                    config["default"] = str(get_default_value(subfield_info, regular_field))
                    config["description"] = (
                        subfield_info.description if subfield_info.description else ""
                    )
                    if isinstance(subfield_info, ComputedFieldInfo):
                        config["read-only"] = "ro"
                        type_description = str(subfield_info.return_type)
                    else:
                        config["read-only"] = "rw"
                        type_description = str(subfield_info.annotation)
                    config["type"] = (
                        type_description.replace("typing.", "")
                        .replace("pathlib.", "")
                        .replace("[", "[ ")
                        .replace("NoneType", "None")
                    )
                    configs.append(config)
                    found_basic = True
                else:
                    new_parent_types = parent_types + nested_parent_types
                    inner_types.add(nested_type)
                    for nested_field_name, nested_field_info in list(
                        nested_type.model_fields.items()
                    ) + list(nested_type.model_computed_fields.items()):
                        extract_nested_models(
                            nested_field_info,
                            new_parent_types + [nested_field_name],
                        )

        extract_nested_models(field_info, [field_name])
    return sorted(configs, key=lambda x: x["name"])


def get_configuration(eos_host: Optional[str], eos_port: Optional[Union[str, int]]) -> list[dict]:
    config_eos = get_config()
    if eos_host is None:
        eos_host = config_eos.server.host
    if eos_port is None:
        eos_port = config_eos.server.port

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

    return configuration(config_values)


def Configuration(eos_host: Optional[str], eos_port: Optional[Union[str, int]]) -> Table:
    flds = "Name", "Type", "RO/RW", "Value", "Default", "Description"
    rows = [
        Tr(
            Td(
                config["name"],
                cls="max-w-64 text-wrap break-all",
            ),
            Td(
                config["type"],
                cls="max-w-48 text-wrap break-all",
            ),
            Td(
                config["read-only"],
                cls="max-w-24 text-wrap break-all",
            ),
            Td(
                config["value"],
                cls="max-w-md text-wrap break-all",
            ),
            Td(config["default"], cls="max-w-48 text-wrap break-all"),
            Td(
                config["description"],
                cls="max-w-prose text-wrap",
            ),
            cls="even:bg-lime-100",
        )
        for config in get_configuration(eos_host, eos_port)
    ]
    head = Thead(*map(Th, flds), cls="bg-lime-400 text-left")
    return ScrollArea(
        Table(head, Tbody(*rows), cls="w-full"),
        cls="h-[75vh] w-full rounded-md",
    )
