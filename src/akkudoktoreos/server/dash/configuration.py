from typing import Any, Dict, List, Optional, Sequence, TypeVar, Union

import requests
from monsterui.franken import Div, DividerLine, P, Table, Tbody, Td, Th, Thead, Tr
from pydantic.fields import ComputedFieldInfo, FieldInfo
from pydantic_core import PydanticUndefined

from akkudoktoreos.config.config import get_config
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.server.dash.components import ConfigCard

logger = get_logger(__name__)
config_eos = get_config()

T = TypeVar("T")


def get_nested_value(
    dictionary: Union[Dict[str, Any], List[Any]],
    keys: Sequence[Union[str, int]],
    default: Optional[T] = None,
) -> Union[Any, T]:
    """Retrieve a nested value from a dictionary or list using a sequence of keys.

    Args:
        dictionary (Union[Dict[str, Any], List[Any]]): The nested dictionary or list to search.
        keys (Sequence[Union[str, int]]): A sequence of keys or indices representing the path to the desired value.
        default (Optional[T]): A value to return if the path is not found.

    Returns:
        Union[Any, T]: The value at the specified nested path, or the default value if not found.

    Raises:
        TypeError: If the input is not a dictionary or list, or if keys are not a sequence.
        KeyError: If a key is not found in a dictionary.
        IndexError: If an index is out of range in a list.
    """
    if not isinstance(dictionary, (dict, list)):
        raise TypeError("The first argument must be a dictionary or list")
    if not isinstance(keys, Sequence):
        raise TypeError("Keys must be provided as a sequence (e.g., list, tuple)")

    if not keys:
        return dictionary

    try:
        # Traverse the structure
        current = dictionary
        for key in keys:
            if isinstance(current, dict) and isinstance(key, str):
                current = current[key]
            elif isinstance(current, list) and isinstance(key, int):
                current = current[key]
            else:
                raise KeyError(f"Invalid key or index: {key}")
        return current
    except (KeyError, IndexError, TypeError):
        return default


def get_default_value(field_info: Union[FieldInfo, ComputedFieldInfo], regular_field: bool) -> Any:
    """Retrieve the default value of a field.

    Args:
        field_info (Union[FieldInfo, ComputedFieldInfo]): The field metadata from Pydantic.
        regular_field (bool): Indicates if the field is a regular field.

    Returns:
        Any: The default value of the field or "N/A" if not a regular field.
    """
    default_value = ""
    if regular_field:
        if (val := field_info.default) is not PydanticUndefined:
            default_value = val
    else:
        default_value = "N/A"
    return default_value


def resolve_nested_types(field_type: Any, parent_types: list[str]) -> list[tuple[Any, list[str]]]:
    """Resolve nested types within a field and return their structure.

    Args:
        field_type (Any): The type of the field to resolve.
        parent_types (List[str]): A list of parent type names.

    Returns:
        List[tuple[Any, List[str]]]: A list of tuples containing resolved types and their parent hierarchy.
    """
    resolved_types: list[tuple[Any, list[str]]] = []

    origin = getattr(field_type, "__origin__", field_type)
    if origin is Union:
        for arg in getattr(field_type, "__args__", []):
            if arg is not type(None):
                resolved_types.extend(resolve_nested_types(arg, parent_types))
    else:
        resolved_types.append((field_type, parent_types))

    return resolved_types


def configuration(values: dict) -> list[dict]:
    """Generate configuration details based on provided values and model metadata.

    Args:
        values (dict): A dictionary containing the current configuration values.

    Returns:
        List[dict]: A sorted list of configuration details, each represented as a dictionary.
    """
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
    """Fetch and process configuration data from the specified EOS server.

    Args:
        eos_host (Optional[str]): The hostname of the server.
        eos_port (Optional[Union[str, int]]): The port of the server.

    Returns:
        List[dict]: A list of processed configuration entries.
    """
    if eos_host is None:
        eos_host = config_eos.server.host
    if eos_port is None:
        eos_port = config_eos.server.port
    server = f"http://{eos_host}:{eos_port}"

    # Get current configuration from server
    try:
        result = requests.get(f"{server}/v1/config")
        result.raise_for_status()
    except requests.exceptions.HTTPError as e:
        detail = result.json()["detail"]
        warning_msg = f"Can not retrieve configuration from {server}: {e}, {detail}"
        logger.warning(warning_msg)
        return configuration({})
    config = result.json()

    return configuration(config)


def Configuration(eos_host: Optional[str], eos_port: Optional[Union[str, int]]) -> Div:
    """Create a visual representation of the configuration.

    Args:
        eos_host (Optional[str]): The hostname of the EOS server.
        eos_port (Optional[Union[str, int]]): The port of the EOS server.

    Returns:
        Table: A `monsterui.franken.Table` component displaying configuration details.
    """
    flds = "Name", "Type", "RO/RW", "Value", "Default", "Description"
    rows = []
    last_category = ""
    for config in get_configuration(eos_host, eos_port):
        category = config["name"].split(".")[0]
        if category != last_category:
            rows.append(P(category))
            rows.append(DividerLine())
            last_category = category
        rows.append(
            ConfigCard(
                config["name"],
                config["type"],
                config["read-only"],
                config["value"],
                config["default"],
                config["description"],
            )
        )
    return Div(*rows, cls="space-y-4")


def ConfigurationOrg(eos_host: Optional[str], eos_port: Optional[Union[str, int]]) -> Table:
    """Create a visual representation of the configuration.

    Args:
        eos_host (Optional[str]): The hostname of the EOS server.
        eos_port (Optional[Union[str, int]]): The port of the EOS server.

    Returns:
        Table: A `monsterui.franken.Table` component displaying configuration details.
    """
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
            cls="",
        )
        for config in get_configuration(eos_host, eos_port)
    ]
    head = Thead(*map(Th, flds), cls="text-left")
    return Table(head, Tbody(*rows), cls="w-full uk-table uk-table-divider uk-table-striped")
