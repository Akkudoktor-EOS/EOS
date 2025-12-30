import json
from collections.abc import Sequence
from typing import Any, Dict, List, Optional, TypeVar, Union

import requests
from loguru import logger
from monsterui.franken import (
    H3,
    H4,
    Card,
    CardTitle,
    Details,
    Div,
    DividerLine,
    DivLAligned,
    DivRAligned,
    Form,
    Grid,
    Input,
    LabelCheckboxX,
    P,
    Summary,
    UkIcon,
)
from pydantic.fields import ComputedFieldInfo, FieldInfo
from pydantic_core import PydanticUndefined

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.prediction.pvforecast import PVForecastPlaneSetting
from akkudoktoreos.server.dash.components import (
    ConfigCard,
    JsonView,
    TextView,
    make_config_update_list_form,
    make_config_update_map_form,
    make_config_update_value_form,
)
from akkudoktoreos.server.dash.context import request_url_for

T = TypeVar("T")

# Latest configuration update results
# Dictionary of config names and associated dictionary with keys "value", "result", "error", "open".
config_update_latest: dict[str, dict[str, Optional[Union[str, bool]]]] = {}

# Current state of config displayed
config_visible: dict[str, dict] = {
    "config-visible-read-only": {
        "label": "Configuration (read-only)",
        "visible": False,
    },
}


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
            if isinstance(current, dict):
                current = current[str(key)]
            elif isinstance(current, list):
                current = current[int(key)]
            else:
                raise KeyError(f"Invalid key or index: {key}")
        return current
    except (KeyError, IndexError, TypeError):
        return default


def get_field_extra_dict(
    subfield_info: Union[FieldInfo, ComputedFieldInfo],
) -> Dict[str, Any]:
    """Extract json_schema_extra.

    Extract regardless of whether it is defined directly
    on the field (Pydantic v2) or inherited from v1 compatibility wrappers.
    Always returns a dictionary.
    """
    # Pydantic v2 location
    extra = getattr(subfield_info, "json_schema_extra", None)
    if isinstance(extra, dict):
        return extra

    # Pydantic v1 compatibility fallbacks
    fi = getattr(subfield_info, "field_info", None)
    if fi is not None:
        extra = getattr(fi, "json_schema_extra", None)
        if isinstance(extra, dict):
            return extra

    return {}


def get_description(
    subfield_info: Union[FieldInfo, ComputedFieldInfo],
    extra: Dict[str, Any],
) -> str:
    """Fetch description.

    Priority:
    1) json_schema_extra["description"]
    2) field_info.description
    3) empty string
    """
    if "description" in extra:
        return str(extra["description"])

    desc = getattr(subfield_info, "description", None)
    return str(desc) if desc is not None else ""


def get_deprecated(
    subfield_info: Union[FieldInfo, ComputedFieldInfo],
    extra: Dict[str, Any],
) -> Optional[Any]:
    """Fetch deprecated.

    Priority:
    1) json_schema_extra["deprecated"]
    2) field_info.deprecated
    3) None
    """
    if "deprecated" in extra:
        return extra["deprecated"]

    return getattr(subfield_info, "deprecated", None)


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


def create_config_details(
    model: type[PydanticBaseModel], values: dict, values_prefix: list[str] = []
) -> dict[str, dict]:
    """Generate configuration details based on provided values and model metadata.

    Args:
        model (type[PydanticBaseModel]): The Pydantic model to extract configuration from.
        values (dict): A dictionary containing the current configuration values.
        values_prefix (list[str]): A list of parent type names that prefixes the model values in the values.

    Returns:
        dict[dict]: A dictionary of configuration details, each represented as a dictionary.
    """
    config_details: dict[str, dict] = {}
    inner_types: set[type[PydanticBaseModel]] = set()

    for field_name, field_info in list(model.model_fields.items()) + list(
        model.model_computed_fields.items()
    ):

        def extract_nested_models(
            subfield_info: Union[ComputedFieldInfo, FieldInfo], parent_types: list[str]
        ) -> None:
            """Extract nested models from the given subfield information.

            Args:
                subfield_info (Union[ComputedFieldInfo, FieldInfo]): Field metadata from Pydantic.
                parent_types (list[str]): A list of parent type names for hierarchical representation.
            """
            nonlocal values, values_prefix
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
                    extra = get_field_extra_dict(subfield_info)

                    config: dict[str, Optional[Any]] = {}
                    config["name"] = ".".join(values_prefix + parent_types)
                    config["value"] = json.dumps(
                        get_nested_value(values, values_prefix + parent_types, "<unknown>")
                    )
                    config["default"] = json.dumps(get_default_value(subfield_info, regular_field))
                    config["description"] = get_description(subfield_info, extra)
                    config["deprecated"] = get_deprecated(subfield_info, extra)
                    if isinstance(subfield_info, ComputedFieldInfo):
                        config["read-only"] = "ro"
                        type_description = str(subfield_info.return_type)
                    else:
                        config["read-only"] = "rw"
                        type_description = str(subfield_info.annotation)
                    config["type"] = (
                        type_description.replace("typing.", "")
                        .replace("pathlib.", "")
                        .replace("NoneType", "None")
                        .replace("<class 'float'>", "float")
                    )
                    config_details[str(config["name"])] = config
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
    return config_details


def get_config(eos_host: str, eos_port: Union[str, int]) -> dict[str, Any]:
    """Fetch configuration data from the specified EOS server.

    Args:
        eos_host (str): The hostname of the EOS server.
        eos_port (Union[str, int]): The port of the EOS server.

    Returns:
        dict[str, Any]: A dict of configuration data.
    """
    server = f"http://{eos_host}:{eos_port}"

    # Get current configuration from server
    try:
        result = requests.get(f"{server}/v1/config", timeout=10)
        result.raise_for_status()
        config = result.json()
    except requests.exceptions.HTTPError as e:
        config = {}
        detail = result.json()["detail"]
        warning_msg = f"Can not retrieve configuration from {server}: {e}, {detail}"
        logger.warning(warning_msg)

    return config


def ConfigPlanesCard(
    config_name: str,
    config_type: str,
    read_only: str,
    value: str,
    default: str,
    description: str,
    max_planes: int,
    update_error: Optional[str],
    update_value: Optional[str],
    update_open: Optional[bool],
) -> Card:
    """Creates a styled configuration card for PV planes.

    This function generates a configuration card that is displayed in the UI with
    various sections such as configuration name, type, description, default value,
    current value, and error details. It supports both read-only and editable modes.

    Args:
        config_name (str): The name of the PV planes configuration.
        config_type (str): The type of the PV planes configuration.
        read_only (str): Indicates if the PV planes configuration is read-only ("rw" for read-write,
                         any other value indicates read-only).
        value (str): The current value of the PV planes configuration.
        default (str): The default value of the PV planes configuration.
        description (str): A description of the PV planes configuration.
        max_planes (int): Maximum number of planes that can be set
        update_error (Optional[str]): The error message, if any, during the update process.
        update_value (Optional[str]): The value to be updated, if different from the current value.
        update_open (Optional[bool]): A flag indicating whether the update section of the card
                                      should be initially expanded.

    Returns:
        Card: A styled Card component containing the PV planes configuration details.
    """
    config_id = config_name.replace(".", "-")
    # Remember overall planes update status
    planes_update_error = update_error
    planes_update_value = update_value
    if not planes_update_value:
        planes_update_value = value
    planes_update_open = update_open
    if not planes_update_open:
        planes_update_open = False
    # Create EOS planes configuration
    eos_planes = json.loads(value)
    eos_planes_config = {
        "pvforecast": {
            "planes": eos_planes,
        },
    }
    # Create cards for all planes
    rows = []
    for i in range(0, max_planes):
        plane_config = create_config_details(
            PVForecastPlaneSetting(),
            eos_planes_config,
            values_prefix=["pvforecast", "planes", str(i)],
        )
        plane_rows = []
        plane_update_open = False
        if eos_planes and len(eos_planes) > i:
            plane_value = json.dumps(eos_planes[i])
        else:
            plane_value = json.dumps(None)
        for config_key in sorted(plane_config.keys()):
            config = plane_config[config_key]
            update_error = config_update_latest.get(config["name"], {}).get("error")  # type: ignore
            update_value = config_update_latest.get(config["name"], {}).get("value")  # type: ignore
            update_open = config_update_latest.get(config["name"], {}).get("open")  # type: ignore
            update_form_factory = None
            if update_open:
                planes_update_open = True
                plane_update_open = True
            # Make mypy happy - should never trigger
            if (
                not isinstance(update_error, (str, type(None)))
                or not isinstance(update_value, (str, type(None)))
                or not isinstance(update_open, (bool, type(None)))
            ):
                error_msg = "update_error or update_value or update_open of wrong type."
                logger.error(error_msg)
                raise TypeError(error_msg)
            if config["name"].endswith("pvtechchoice"):
                update_form_factory = make_config_update_value_form(
                    ["crystSi", "CIS", "CdTe", "Unknown"]
                )
            elif config["name"].endswith("mountingplace"):
                update_form_factory = make_config_update_value_form(["free", "building"])
            plane_rows.append(
                ConfigCard(
                    config["name"],
                    config["type"],
                    config["read-only"],
                    config["value"],
                    config["default"],
                    config["description"],
                    config["deprecated"],
                    update_error,
                    update_value,
                    update_open,
                    update_form_factory,
                )
            )
        rows.append(
            Card(
                Details(
                    Summary(
                        Grid(
                            Grid(
                                DivLAligned(
                                    UkIcon(icon="play"),
                                    H4(f"pvforecast.planes.{i}"),
                                ),
                                DivRAligned(
                                    P(read_only),
                                ),
                            ),
                            JsonView(json.loads(plane_value)),
                        ),
                        cls="list-none",
                    ),
                    *plane_rows,
                    cls="space-y-4 gap-4",
                    open=plane_update_open,
                ),
                cls="w-full",
            )
        )

    return Card(
        Details(
            Summary(
                Grid(
                    Grid(
                        DivLAligned(
                            UkIcon(icon="play"),
                            P(config_name),
                        ),
                        DivRAligned(
                            P(read_only),
                        ),
                    ),
                    JsonView(json.loads(value)),
                ),
                cls="list-none",
            ),
            Grid(
                TextView(description),
                P(config_type),
            ),
            # Default
            Grid(
                DivRAligned(P("default")),
                P(default),
            )
            if read_only == "rw"
            else None,
            # Set value
            Grid(
                DivRAligned(P("update")),
                Grid(
                    Form(
                        Input(value="update", type="hidden", id="action"),
                        Input(value=config_name, type="hidden", id="key"),
                        Input(value=planes_update_value, type="text", id="value"),
                        hx_put=request_url_for("/eosdash/configuration"),
                        hx_target="#page-content",
                        hx_swap="innerHTML",
                    ),
                ),
            )
            if read_only == "rw"
            else None,
            # Last error
            Grid(
                DivRAligned(P("update error")),
                TextView(planes_update_error),
            )
            if planes_update_error
            else None,
            # Now come the single element configs
            *rows,
            cls="space-y-4 gap-4",
            open=planes_update_open,
        ),
        cls="w-full",
    )


def Configuration(
    eos_host: str,
    eos_port: Union[str, int],
    data: Optional[dict] = None,
) -> Div:
    """Create a visual representation of the configuration.

    Args:
        eos_host (str): The hostname of the EOS server.
        eos_port (Union[str, int]): The port of the EOS server.
        data (Optional[dict], optional): Incoming data to trigger config actions. Defaults to None.

    Returns:
        rows:  Rows of configuration details.
    """
    global config_visible
    dark = False

    if data and data.get("action", None):
        if data.get("dark", None) == "true":
            dark = True
        if data["action"] == "visible":
            renderer = data.get("renderer", None)
            if renderer:
                config_visible[renderer]["visible"] = bool(data.get(f"{renderer}-visible", False))
        elif data["action"] == "update":
            # This data contains a new value for key
            key = data["key"]
            value_json_str: str = data.get("value", "")
            try:
                value = json.loads(value_json_str)
            except:
                if value_json_str in ("None", "none", "Null", "null"):
                    value = None
                else:
                    value = value_json_str

            error = None
            config = None
            try:
                server = f"http://{eos_host}:{eos_port}"
                path = key.replace(".", "/")
                response = requests.put(f"{server}/v1/config/{path}", json=value, timeout=10)
                response.raise_for_status()
                config = response.json()
            except requests.exceptions.HTTPError as err:
                try:
                    # Try to get 'detail' from the JSON response
                    detail = response.json().get(
                        "detail", f"No error details for value '{value}' '{response.text}'"
                    )
                except ValueError:
                    # Response is not JSON
                    detail = f"No error details for value '{value}' '{response.text}'"
                error = f"Can not set {key} on {server}: {err}, {detail}"
            # Mark all updates as closed
            for k in config_update_latest:
                config_update_latest[k]["open"] = False
            # Remember this update as latest one
            config_update_latest[key] = {
                "error": error,
                "result": config,
                "value": value_json_str,
                "open": True,
            }

    # (Re-)read configuration details to be shure we display actual data
    config = get_config(eos_host, eos_port)

    # Process configuration data
    config_details = create_config_details(ConfigEOS, config)

    ConfigMenu = Card(
        # CheckboxGroup to toggle config data visibility
        Grid(
            *[
                LabelCheckboxX(
                    label=config_visible[renderer]["label"],
                    id=f"{renderer}-visible",
                    name=f"{renderer}-visible",
                    value="true",
                    checked=config_visible[renderer]["visible"],
                    hx_post=request_url_for("/eosdash/configuration"),
                    hx_target="#page-content",
                    hx_swap="innerHTML",
                    hx_vals='js:{ "action": "visible", "renderer": '
                    + '"'
                    + f"{renderer}"
                    + '", '
                    + '"dark": window.matchMedia("(prefers-color-scheme: dark)").matches '
                    + "}",
                    # lbl_cls=f"text-{solution_color[renderer]}",
                )
                for renderer in list(config_visible.keys())
            ],
            cols=4,
        ),
        header=CardTitle("Choose What's Shown"),
    )

    rows = []
    last_category = ""
    # find some special configuration values
    try:
        max_planes = int(config_details["pvforecast.max_planes"]["value"])
    except:
        max_planes = 0
    logger.debug(f"max_planes: {max_planes}")

    try:
        homeassistant_entity_ids = json.loads(
            config_details["adapter.homeassistant.homeassistant_entity_ids"]["value"]
        )
    except:
        homeassistant_entity_ids = []
    logger.debug(f"homeassistant_entity_ids: {homeassistant_entity_ids}")

    eos_solution_entity_ids = []
    try:
        eos_solution_entity_ids = json.loads(
            config_details["adapter.homeassistant.eos_solution_entity_ids"]["value"]
        )
    except:
        eos_solution_entity_ids = []
    logger.debug(f"eos_solution_entity_ids {eos_solution_entity_ids}")

    eos_device_instruction_entity_ids = []
    try:
        eos_device_instruction_entity_ids = json.loads(
            config_details["adapter.homeassistant.eos_device_instruction_entity_ids"]["value"]
        )
    except:
        eos_device_instruction_entity_ids = []
    logger.debug(f"eos_device_instruction_entity_ids {eos_device_instruction_entity_ids}")

    devices_measurement_keys = []
    try:
        devices_measurement_keys = json.loads(config_details["devices.measurement_keys"]["value"])
    except:
        devices_measurement_keys = []
    logger.debug(f"devices_measurement_keys {devices_measurement_keys}")

    # build visual representation
    for config_key in sorted(config_details.keys()):
        config = config_details[config_key]
        category = config["name"].split(".")[0]
        if category != last_category:
            rows.append(H3(category))
            rows.append(DividerLine())
            last_category = category
        update_error = config_update_latest.get(config["name"], {}).get("error")
        update_value = config_update_latest.get(config["name"], {}).get("value")
        update_open = config_update_latest.get(config["name"], {}).get("open")
        # Make mypy happy - should never trigger
        if (
            not isinstance(update_error, (str, type(None)))
            or not isinstance(update_value, (str, type(None)))
            or not isinstance(update_open, (bool, type(None)))
        ):
            error_msg = "update_error or update_value or update_open of wrong type."
            logger.error(error_msg)
            raise TypeError(error_msg)
        if (
            not config_visible["config-visible-read-only"]["visible"]
            and config["read-only"] != "rw"
        ):
            # Do not display read only values
            continue
        if (
            config["type"]
            == "Optional[list[akkudoktoreos.prediction.pvforecast.PVForecastPlaneSetting]]"
            and not config["deprecated"]
        ):
            # Special configuration for PV planes
            rows.append(
                ConfigPlanesCard(
                    config["name"],
                    config["type"],
                    config["read-only"],
                    config["value"],
                    config["default"],
                    config["description"],
                    max_planes,
                    update_error,
                    update_value,
                    update_open,
                )
            )
        elif not config["deprecated"]:
            update_form_factory = None
            if config["name"].endswith(".provider"):
                # Special configuration for prediction provider setting
                try:
                    provider_ids = json.loads(config_details[config["name"] + "s"]["value"])
                except:
                    provider_ids = []
                if config["type"].startswith("Optional[list"):
                    update_form_factory = make_config_update_list_form(provider_ids)
                else:
                    provider_ids.append("None")
                    update_form_factory = make_config_update_value_form(provider_ids)
            elif config["name"].startswith("adapter.homeassistant.config_entity_ids"):
                # Home Assistant adapter config entities
                update_form_factory = make_config_update_map_form(None, homeassistant_entity_ids)
            elif config["name"].startswith("adapter.homeassistant.load_emr_entity_ids"):
                # Home Assistant adapter load energy meter readings entities
                update_form_factory = make_config_update_list_form(homeassistant_entity_ids)
            elif config["name"].startswith("adapter.homeassistant.pv_production_emr_entity_ids"):
                # Home Assistant adapter pv energy meter readings entities
                update_form_factory = make_config_update_list_form(homeassistant_entity_ids)
            elif config["name"].startswith("adapter.homeassistant.device_measurement_entity_ids"):
                # Home Assistant adapter device measurement entities
                update_form_factory = make_config_update_map_form(
                    devices_measurement_keys, homeassistant_entity_ids
                )
            elif config["name"].startswith("adapter.homeassistant.device_instruction_entity_ids"):
                # Home Assistant adapter device instruction entities
                update_form_factory = make_config_update_list_form(
                    eos_device_instruction_entity_ids
                )
            elif config["name"].startswith("adapter.homeassistant.solution_entity_ids"):
                # Home Assistant adapter optimization solution entities
                update_form_factory = make_config_update_list_form(eos_solution_entity_ids)
            elif config["name"].startswith("ems.mode"):
                #  Energy managemnt mode
                update_form_factory = make_config_update_value_form(
                    ["OPTIMIZATION", "PREDICTION", "None"]
                )

            rows.append(
                ConfigCard(
                    config["name"],
                    config["type"],
                    config["read-only"],
                    config["value"],
                    config["default"],
                    config["description"],
                    config["deprecated"],
                    update_error,
                    update_value,
                    update_open,
                    update_form_factory,
                )
            )

    return Div(ConfigMenu, *rows, cls="space-y-3")
