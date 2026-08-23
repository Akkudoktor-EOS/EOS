import enum
import json
from collections.abc import Sequence
from typing import Any, Dict, List, Optional, TypeVar, Union

import requests
from fasthtml.common import Select
from loguru import logger
from monsterui.franken import (
    Card,
    CardTitle,
    Div,
    Grid,
    LabelCheckboxX,
    Option,
)
from pydantic.fields import ComputedFieldInfo, FieldInfo
from pydantic_core import PydanticUndefined

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.server.dash.components import (
    HTMX_INCLUDE,
    ConfigCard,
    ConfigSection,
    Input,
)
from akkudoktoreos.server.dash.context import request_url_for
from akkudoktoreos.server.dash.eosstatus import eos_pvlib_cec_names, eos_server_address
from akkudoktoreos.server.dash.itemscard import ConfigItemsCard
from akkudoktoreos.server.dash.mapcard import ConfigMapCard
from akkudoktoreos.server.dash.uihints import (
    LAZY_OPTIONS_SOURCES,
    UI_HINTS,
    register_lazy_options_source,
    resolve_form_factory,
)

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


def get_scope(
    extra: Dict[str, Any],
) -> Optional[list[str]]:
    """Fetch x-scope.

    Returns the value of json_schema_extra["x-scope"] as a list of strings, or None if not set.
    """
    scope = extra.get("x-scope")
    if scope is None:
        return None
    if isinstance(scope, list):
        return [str(s) for s in scope]
    return [str(scope)]


def get_default_value(field_info: Union[FieldInfo, ComputedFieldInfo], regular_field: bool) -> Any:
    """Retrieve the default value of a field as a JSON-safe Python object.

    Handles both ``default`` and ``default_factory`` fields, and converts the
    resulting value to a JSON-safe representation before returning.  This
    covers all non-primitive default types encountered in the EOS config
    models: Pydantic model instances, lists of models, enums, ``Path``
    objects, and anything else that plain ``json.dumps`` would reject.

    For computed fields or fields with no default of any kind, a sentinel
    string is returned instead.

    Args:
        field_info: The field metadata from Pydantic.
        regular_field: ``True`` for a ``FieldInfo`` (regular field),
            ``False`` for a ``ComputedFieldInfo``.

    Returns:
        A JSON-safe Python object (dict, list, str, int, float, bool, or
        ``None``) representing the field default, or ``"N/A"`` when no
        meaningful default exists.
    """
    import pathlib

    if not regular_field:
        return "N/A"

    # Resolve the raw default — prefer plain default, fall back to factory
    if field_info.default is not PydanticUndefined:
        val = field_info.default
    elif field_info.default_factory is not None:
        try:
            val = field_info.default_factory()
        except Exception:
            return ""
    else:
        return ""

    def _to_json_safe(v: Any) -> Any:
        """Recursively convert a value to a JSON-safe type."""
        if v is None or isinstance(v, (bool, int, float, str)):
            return v
        if isinstance(v, PydanticBaseModel):
            return v.model_dump(mode="json")
        if isinstance(v, enum.Enum):
            return v.value
        if isinstance(v, pathlib.PurePath):
            return str(v)
        if isinstance(v, dict):
            return {str(k): _to_json_safe(w) for k, w in v.items()}
        if isinstance(v, (list, tuple, set, frozenset)):
            return [_to_json_safe(item) for item in v]
        # Last resort: str() — at minimum json.dumps won't crash
        return str(v)

    return _to_json_safe(val)


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

    def extract_nested_models(
        subfield_info: Union[ComputedFieldInfo, FieldInfo],
        parent_types: list[str],
        visited: frozenset,
    ) -> None:
        nonlocal values, values_prefix
        regular_field = isinstance(subfield_info, FieldInfo)
        subtype = subfield_info.annotation if regular_field else subfield_info.return_type

        nested_types = resolve_nested_types(subtype, [])
        found_basic = False
        for nested_type, nested_parent_types in nested_types:
            if not isinstance(nested_type, type) or not issubclass(nested_type, PydanticBaseModel):
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
                config["scope"] = get_scope(extra)
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
                if nested_type in visited:
                    # Genuine cycle along this path (self-referential model) — stop
                    # recursing here, but do NOT block sibling branches elsewhere.
                    continue
                new_parent_types = parent_types + nested_parent_types
                new_visited = visited | {nested_type}
                for nested_field_name, nested_field_info in list(
                    nested_type.model_fields.items()
                ) + list(nested_type.model_computed_fields.items()):
                    extract_nested_models(
                        nested_field_info,
                        new_parent_types + [nested_field_name],
                        new_visited,
                    )

    for field_name, field_info in list(model.model_fields.items()) + list(
        model.model_computed_fields.items()
    ):
        extract_nested_models(field_info, [field_name], frozenset())

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


def config_matches_search(config: dict, search: str) -> bool:
    if not search:
        return True

    return (
        search in config["name"].lower()
        or search in config["description"].lower()
        or search in config["type"].lower()
        or search in config["value"].lower()
    )


# ---------------------
# Lazy Options Handling
# ---------------------


def _fetch_cec_names(kind: str) -> tuple[str, ...]:
    """Fetch CEC module or inverter names from the EOS server.

    Cached per kind for the EOSdash process lifetime — the CEC database
    is static.

    Args:
        kind: "modules" or "inverters".
    """
    global eos_server_address, eos_pvlib_cec_names

    if kind in eos_pvlib_cec_names:
        # Already cached
        return eos_pvlib_cec_names[kind]

    if eos_server_address is None:
        logger.warning(
            f"EOS server address not yet known: `{eos_server_address}`; cannot fetch CEC {kind}"
        )
        return ()
    host, port = eos_server_address
    # host = "127.0.0.1"
    # port = 8503
    server = f"http://{host}:{port}"
    path = f"/v1/prediction/pvforecast/pvlib/{kind}"
    try:
        result = requests.get(f"{server}{path}", timeout=10)
        result.raise_for_status()
        data = result.json()
        names = tuple(data) if isinstance(data, list) else tuple(data.keys())
        eos_pvlib_cec_names[kind] = names
    except requests.exceptions.RequestException as e:
        logger.warning(f"Can not retrieve CEC {kind} from {server}: {e}")
        return ()

    logger.info(f"Fetched CEC {kind}")

    return names


def _cec_modules_source() -> list[str]:
    return list(_fetch_cec_names("modules"))


def _cec_inverters_source() -> list[str]:
    return list(_fetch_cec_names("inverters"))


register_lazy_options_source("pvlib.modules", _cec_modules_source)
register_lazy_options_source("pvlib.inverters", _cec_inverters_source)


def config_options(
    options_source: str, search: str, select_id: str, name: str, current: str
) -> Select:
    """Return a filtered, capped <select> fragment for a select_lazy field."""
    source = LAZY_OPTIONS_SOURCES.get(options_source)
    all_values = source() if source else []

    search_l = (search or "").strip().lower()
    matches = [v for v in all_values if search_l in v.lower()] if search_l else all_values
    matches = matches[:50]  # hard cap regardless of source size

    if current and current not in matches:
        # keep the current selection visible even if it doesn't match
        # the active filter, so switching searches never silently loses it
        matches = [current] + matches

    return Select(
        *[Option(v, value=v, selected=(v == current)) for v in matches],
        id=select_id,
        name=name,
        required=True,
        cls="border rounded px-3 py-2 w-full",
    )


# -----------------------------------
# Configuration Visual Representation
# -----------------------------------


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
    global config_visible, eos_server_address
    dark = False

    # Remember for usage
    eos_server_address = (eos_host, int(eos_port))

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
            except Exception:
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

    # Configuration search
    search_value = (data.get("search", "") if data else "").strip().lower()

    SearchBar = Card(
        Input(
            placeholder="Search configuration… (name, description, type)",
            name="search",
            value=search_value,
            hx_get=request_url_for("/eosdash/configuration"),
            hx_push_url="true",
            hx_trigger="keyup changed delay:250ms",
            hx_target="#page-content",
            hx_swap="innerHTML",
            hx_vals='js:{ "dark": window.matchMedia("(prefers-color-scheme: dark)").matches }',
            cls="w-full border rounded px-3 py-2",
        )
    )

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
                    hx_include=HTMX_INCLUDE,
                    # lbl_cls=f"text-{solution_color[renderer]}",
                )
                for renderer in list(config_visible.keys())
            ],
            cols=4,
        ),
        header=CardTitle("Choose What's Shown"),
    )

    # find some special configuration values
    try:
        max_planes = int(config_details["pvforecast.max_planes"]["value"])
    except Exception:
        max_planes = 0
    logger.debug(f"max_planes: {max_planes}")

    try:
        homeassistant_entity_ids = json.loads(
            config_details["adapter.homeassistant.homeassistant_entity_ids"]["value"]
        )
    except Exception:
        homeassistant_entity_ids = []
    logger.debug(f"homeassistant_entity_ids: {homeassistant_entity_ids}")

    eos_solution_entity_ids = []
    try:
        eos_solution_entity_ids = json.loads(
            config_details["adapter.homeassistant.eos_solution_entity_ids"]["value"]
        )
    except Exception:
        eos_solution_entity_ids = []
    logger.debug(f"eos_solution_entity_ids {eos_solution_entity_ids}")

    eos_device_instruction_entity_ids = []
    try:
        eos_device_instruction_entity_ids = json.loads(
            config_details["adapter.homeassistant.eos_device_instruction_entity_ids"]["value"]
        )
    except Exception:
        eos_device_instruction_entity_ids = []
    logger.debug(f"eos_device_instruction_entity_ids {eos_device_instruction_entity_ids}")

    devices_measurement_keys = []
    try:
        devices_measurement_keys = json.loads(config_details["devices.measurement_keys"]["value"])
    except Exception:
        devices_measurement_keys = []
    logger.debug(f"devices_measurement_keys {devices_measurement_keys}")

    # build visual representation
    sections: dict[str, dict[str, Any]] = {}

    # Fill sections with config cards
    for config_key in sorted(config_details.keys()):
        config = config_details[config_key]
        category = config["name"].split(".")[0]
        sections.setdefault(category, {})

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
            # Do not display read only values
            not config_visible["config-visible-read-only"]["visible"]
            and config["read-only"] != "rw"
        ):
            continue

        if not config_matches_search(config, search_value):
            # Search value given but does not match
            continue

        hint = UI_HINTS.get(config["name"])

        if hint and hint.form == "items" and not config["deprecated"]:
            card = ConfigItemsCard(
                config=config,
                hint=hint,
                config_details=config_details,
                config_update_latest=config_update_latest,
                create_config_details=create_config_details,
            )
        elif hint and hint.form == "map_items" and not config["deprecated"]:
            card = ConfigMapCard(
                config=config,
                hint=hint,
                config_details=config_details,
                config_update_latest=config_update_latest,
                create_config_details=create_config_details,
            )
        elif not config["deprecated"]:
            update_form_factory = resolve_form_factory(hint, config_details) if hint else None
            card = ConfigCard(
                config["name"],
                config["type"],
                config["read-only"],
                config["value"],
                config["default"],
                config["description"],
                config["deprecated"],
                config["scope"],
                update_error,
                update_value,
                update_open,
                update_form_factory,
            )
        else:
            continue

        sections[category][config["name"]] = card

    section_components = []

    for category in sorted(sections.keys()):
        # Open if searching OR if last update touched this category — including
        # updates on deeply nested sub-fields (e.g. pvforecast.planes.2.module_model)
        # whose full dotted name never appears as a key in the outer config_details
        # dict, since that dict only holds top-level model fields. ConfigItemsCard
        # resolves per-item sub-fields internally via its own create_config_details
        # call, so we must match against config_update_latest's own keys instead.
        open_section = bool(search_value)

        if not open_section:
            open_section = any(
                info.get("open")
                for key, info in config_update_latest.items()
                if key == category or key.startswith(f"{category}.")
            )

        cards: list[Card] = []
        for name, card in sections[category].items():
            if name.rsplit(".", 1)[-1] == "provider":
                # Make provider the first config item in category
                cards.insert(0, card)
            else:
                cards.append(card)

        section_components.append(ConfigSection(category, *cards, open=open_section))

    return Div(
        Grid(
            ConfigMenu,
            SearchBar,
        ),
        *section_components,
        cls="space-y-4",
    )
