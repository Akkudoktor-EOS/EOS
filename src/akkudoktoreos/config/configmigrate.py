"""Migrate config file to actual version."""

import json
import os
import sys
import tempfile
import types
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Set,
    Tuple,
    Union,
    get_args,
    get_origin,
)

from loguru import logger

from akkudoktoreos.core.pydantic import BaseModel
from akkudoktoreos.core.version import __version__

if TYPE_CHECKING:
    # There are circular dependencies - only import here for type checking
    from akkudoktoreos.config.config import SettingsEOSDefaults


CONFIG_SCHEMA_VERSION = 1

_KEEP_DEFAULT = object()


# -----------------------------
# Migration helpers
# -----------------------------


def _list_to_device_dict(
    prefix: str,
) -> Callable[[Any], Any]:
    """Return a transform that converts a list of device dicts to a keyed dict.

    Each item must be a dict.  The key is taken from the item's own
    ``device_id`` field when present; otherwise a key is synthesised
    from *prefix* + the zero-based index (e.g. ``"bat0"``, ``"bat1"``).
    """

    def _transform(value: Any) -> Any:
        if not isinstance(value, (list, dict)):
            return value
        result: Dict[str, Any] = {}
        entries = enumerate(value) if isinstance(value, list) else value.items()
        for index, original in entries:
            if not isinstance(original, dict):
                raise ValueError("Device settings must be an object")
            item = dict(original)
            # Legacy transient simulation inputs are not installation settings.
            item.pop("hours", None)
            item.pop("initial_soc_percentage", None)
            key = (
                (item.get("device_id") or f"{prefix}{index}") if isinstance(value, list) else index
            )
            if not isinstance(key, str):
                raise ValueError("Device identifiers must be strings")
            if key in result:
                raise ValueError(f"Duplicate device_id: {key!r}")
            item.setdefault("device_id", key)
            if "levelized_cost_of_storage_kwh" in item:
                item.setdefault(
                    "levelized_cost_of_storage_amt_kwh", item.pop("levelized_cost_of_storage_kwh")
                )
            result[key] = item
        return result

    return _transform


# -----------------------------
# Global migration map constant
# -----------------------------
# key: old JSON path, value: either
#   - str (new model path)
#   - tuple[str, Callable[[Any], Any]] (new path + transform)
#   - _KEEP_DEFAULT (keep new default if old value is none or not given)
#   - None (drop)
MIGRATION_MAP: Dict[
    str,
    Union[
        str,  # simple rename
        Tuple[str, Callable[[Any], Any]],  # rename + transform
        Tuple[str, object],  # rename + _KEEP_DEFAULT
        Tuple[str, object, Callable[[Any], Any]],  # rename + _KEEP_DEFAULT + transform
        None,  # drop
    ],
] = {
    # adapter
    # =======
    # - HomeAssistant
    "adapter/homeassistant/optimization_solution_entity_ids": (
        "adapter/homeassistant/solution_entity_ids",
        lambda v: v if isinstance(v, list) else None,
    ),
    # - NodeRed
    # devices
    # =======
    # List → dict migration (all device collections)
    # These must come *before* any sub-path entries that reference the old list indices,
    # so the whole collection is moved first; the sub-path None-drops clean up leftovers.
    # - batteries
    "devices/batteries": (
        "devices/batteries",
        _list_to_device_dict("bat"),
    ),
    "devices/batteries/0/initial_soc_percentage": None,
    # - electric_vehicles
    "devices/electric_vehicles": (
        "devices/electric_vehicles",
        _list_to_device_dict("ev"),
    ),
    "devices/electric_vehicles/0/initial_soc_percentage": None,
    # - inverters
    "devices/inverters": (
        "devices/inverters",
        _list_to_device_dict("inv"),
    ),
    # - home_appliances
    "devices/home_appliances": (
        "devices/home_appliances",
        _list_to_device_dict("appliance"),
    ),
    # elecfee
    # =======
    # - ElecFeeFixed
    # - ElecFeeImport
    # elecprice
    # =========
    "elecprice/charges_kwh": None,
    "elecprice/vat_rate": None,
    # - ElecPriceAkkudoktor
    # - ElecPriceEnergyCharts
    # - ElecPriceFixed
    "elecprice/elecpricefixed/time_windows": "elecprice/elecpricefixed/elecprice_marketprice_amt_kwh",
    # - ElecPriceImport
    "elecprice/provider_settings/ElecPriceImport/import_file_path": "elecprice/elecpriceimport/import_file_path",
    "elecprice/provider_settings/ElecPriceImport/import_json": "elecprice/elecpriceimport/import_json",
    "elecprice/provider_settings/import_file_path": "elecprice/elecpriceimport/import_file_path",
    "elecprice/provider_settings/import_json": "elecprice/elecpriceimport/import_json",
    # feedintariff
    # ============
    # - FeedInTariffFixed
    "feedintariff/feedintarifffixed/feed_in_tariff_kwh": (
        "feedintariff/feedintarifffixed/feed_in_tariff_amt_kwh",
        lambda v: {
            "windows": [
                {"start_time": "00:00", "duration": "24 hours", "value": float(v)},
            ],
        },
    ),
    "feedintariff/provider_settings/FeedInTariffFixed/feed_in_tariff_kwh": (
        "feedintariff/feedintarifffixed/feed_in_tariff_amt_kwh",
        lambda v: {
            "windows": [
                {"start_time": "00:00", "duration": "24 hours", "value": float(v)},
            ],
        },
    ),
    # - FeedInTariffImport
    "feedintariff/provider_settings/FeedInTariffImport/import_file_path": "feedintariff/feedintariffimport/import_file_path",
    "feedintariff/provider_settings/FeedInTariffImport/import_json": "feedintariff/feedintariffimport/import_json",
    # general
    # =======
    "general/data_folder_path": ("general/data_folder_path", _KEEP_DEFAULT),
    # load
    # ====
    # - LoadAkkudoktor
    "load/provider_settings/LoadAkkudoktor/loadakkudoktor_year_energy_kwh": "load/loadakkudoktor/loadakkudoktor_year_energy_kwh",
    "load/provider_settings/loadakkudoktor_year_energy": "load/loadakkudoktor/loadakkudoktor_year_energy_kwh",
    # - LoadVrm
    "load/loadvrm/load_vrm_idsite": "load/vrm/site_id",
    "load/loadvrm/load_vrm_token": "load/vrm/token",
    "load/provider_settings/load_vrm_idsite": "load/vrm/site_id",
    "load/provider_settings/load_vrm_token": "load/vrm/token",
    "load/provider_settings/LoadVrm/load_vrm_idsite": "load/vrm/site_id",
    "load/provider_settings/LoadVrm/load_vrm_token": "load/vrm/token",
    # - LoadImport
    "load/provider_settings/LoadImport/import_file_path": "load/loadimport/import_file_path",
    "load/provider_settings/LoadImport/import_json": "load/loadimport/import_json",
    "load/provider_settings/import_file_path": "load/loadimport/import_file_path",
    "load/provider_settings/import_json": "load/loadimport/import_json",
    # logging
    # =======
    "logging/level": "logging/console_level",
    "logging/root_level": None,
    # measurement
    # ===========
    "measurement/load0_name": "measurement/load_emr_keys/0",
    "measurement/load1_name": "measurement/load_emr_keys/1",
    "measurement/load2_name": "measurement/load_emr_keys/2",
    "measurement/load3_name": "measurement/load_emr_keys/3",
    "measurement/load4_name": "measurement/load_emr_keys/4",
    # optimization
    # ============
    "optimization/interval": None,
    "optimization/horizon_hours": "optimization/genetic0/horizon_hours",
    "optimization/ev_available_charge_rates_percent": (
        "devices/electric_vehicles/ev0/charge_rates",
        lambda v: [x / 100 for x in v],
    ),
    "optimization/hours": "optimization/genetic0/horizon_hours",
    "optimization/penalty": ("optimization/genetic0/penalties/ev_soc_miss", lambda v: float(v)),
    # pvforecast
    # ==========
    # - PVForecastAkkudoktor
    # - PVForecastVrm
    "pvforecast/provider_settings/PVForecastVrm/pvforecast_vrm_token": "pvforecast/vrm/token",
    "pvforecast/provider_settings/PVForecastVrm/pvforecast_vrm_idsite": "pvforecast/vrm/site_id",
    "pvforecast/provider_settings/load_vrm_idsite": "pvforecast/vrm/site_id",
    "pvforecast/provider_settings/load_vrm_token": "pvforecast/vrm/token",
    # - PVForecastPVNode
    "pvforecast/provider_settings/PVForecastPVNode/api_key": "pvforecast/pvnode/api_key",
    "pvforecast/provider_settings/PVForecastPVNode/site_id": "pvforecast/pvnode/site_id",
    "pvforecast/provider_settings/PVForecastPVNode/forecast_days": "pvforecast/pvnode/forecast_days",
    # - PVForecastForecastSolar
    "pvforecast/provider_settings/PVForecastForecastSolar/api_key": "pvforecast/forecastsolar/api_key",
    # - PVForecastSolcast
    "pvforecast/provider_settings/PVForecastSolcast/api_key": "pvforecast/solcast/api_key",
    "pvforecast/provider_settings/PVForecastSolcast/site_id": "pvforecast/solcast/site_id",
    # - PVForecastImport
    "pvforecast/provider_settings/PVForecastImport/import_file_path": "pvforecast/pvforecastimport/import_file_path",
    "pvforecast/provider_settings/PVForecastImport/import_json": "pvforecast/pvforecastimport/import_json",
    "pvforecast/provider_settings/import_file_path": "pvforecast/pvforecastimport/import_file_path",
    "pvforecast/provider_settings/import_json": "pvforecast/pvforecastimport/import_json",
    # weather
    # =======
    # - WeatherImport
    "weather/provider_settings/WeatherImport/import_file_path": "weather/weatherimport/import_file_path",
    "weather/provider_settings/WeatherImport/import_json": "weather/weatherimport/import_json",
    "weather/provider_settings/import_file_path": "weather/weatherimport/import_file_path",
    "weather/provider_settings/import_json": "weather/weatherimport/import_json",
}

# -----------------------------
# Global migration stats
# -----------------------------
migrated_source_paths: Set[str] = set()
mapped_count: int = 0
auto_count: int = 0
skipped_paths: List[str] = []


def _set_path(data: dict[str, Any], path: str, value: Any) -> None:
    """Set a migrated path in raw data before validating coupled fields."""
    current: Any = data
    parts = path.split("/")
    for i, part in enumerate(parts):
        key: Any = int(part) if isinstance(current, list) else part
        if isinstance(current, list):
            while len(current) <= key:
                current.append(None)
        if i == len(parts) - 1:
            current[key] = value
        else:
            if (isinstance(current, dict) and key not in current) or current[key] is None:
                current[key] = [] if parts[i + 1].isdigit() else {}
            current = current[key]


def _drop_path(data: Any, parts: list[str]) -> None:
    """Remove a consumed legacy field, including empty legacy containers."""
    if not parts or not isinstance(data, (dict, list)):
        return
    key: Any = int(parts[0]) if isinstance(data, list) and parts[0].isdigit() else parts[0]
    if isinstance(data, list):
        if not isinstance(key, int) or key >= len(data):
            return
    elif key not in data:
        return
    if len(parts) == 1:
        if isinstance(data, list):
            data[key] = None
        else:
            del data[key]
    else:
        _drop_path(data[key], parts[1:])
        if isinstance(data, dict) and data[key] == {}:
            del data[key]


def _reject_unknown(data: Any, validated: Any, prefix: str = "") -> None:
    """Reject silently ignored settings, traversing dynamic device/zone maps."""
    if isinstance(validated, BaseModel) and isinstance(data, dict):
        fields = type(validated).model_fields
        aliases = {field.alias or name: name for name, field in fields.items()}
        for key, value in data.items():
            name = aliases.get(key, key)
            if name not in fields:
                raise ValueError(f"Unknown configuration field: {prefix}{key}")
            _reject_unknown(value, getattr(validated, name), f"{prefix}{key}/")
    elif isinstance(validated, dict) and isinstance(data, dict):
        for key, value in data.items():
            if key not in validated:
                raise ValueError(f"Lost configuration entry: {prefix}{key}")
            _reject_unknown(value, validated[key], f"{prefix}{key}/")
    elif isinstance(validated, (list, tuple)) and isinstance(data, list):
        if len(data) != len(validated):
            raise ValueError(f"Changed configuration collection length: {prefix}")
        for i, value in enumerate(data):
            _reject_unknown(value, validated[i], f"{prefix}{i}/")


def migrate_config_data(config_data: Dict[str, Any]) -> "SettingsEOSDefaults":
    """Migrate legacy schema 0 as a complete object; reject unknown future schemas.

    Input is never mutated. Mapped or validation failures abort instead of
    returning defaults with missing configuration. Application versions are
    provenance only; they do not select the schema migration.
    """
    global migrated_source_paths, mapped_count, auto_count, skipped_paths
    migrated_source_paths, mapped_count, auto_count, skipped_paths = set(), 0, 0, []
    from akkudoktoreos.config.config import SettingsEOSDefaults
    from akkudoktoreos.optimization.genetic.geneticsettings import (
        normalize_genetic_settings,
    )
    from akkudoktoreos.prediction.pvforecastakkudoktorlocal import (
        normalize_akkudoktor_settings,
    )

    if not isinstance(config_data, dict):
        raise ValueError("Configuration must be an object")
    schema = config_data.get("general", {}).get("schema_version", 0)
    if type(schema) is not int or schema not in (0, CONFIG_SCHEMA_VERSION):
        raise ValueError("Unsupported configuration schema version")
    data = deepcopy(config_data)
    if schema == 0:
        if "optimization" in data:
            data["optimization"] = normalize_genetic_settings(data["optimization"])
        if "pvforecast" in data:
            data["pvforecast"] = normalize_akkudoktor_settings(data["pvforecast"])
        original = deepcopy(data)
        for old_path, mapping in MIGRATION_MAP.items():
            migrated_source_paths.add(old_path)
            old_value = _get_json_nested_value(original, old_path)
            if mapping is None:
                _drop_path(data, old_path.split("/"))
                continue
            new_path = mapping[0] if isinstance(mapping, tuple) else mapping
            options = mapping[1:] if isinstance(mapping, tuple) else ()
            if old_value is None:
                if _KEEP_DEFAULT in options or old_path != new_path:
                    _drop_path(data, old_path.split("/"))
                continue
            transformed = deepcopy(old_value)
            for option in options:
                if callable(option):
                    transformed = option(transformed)
            if old_path == "optimization/ev_available_charge_rates_percent":
                vehicles = data.get("devices", {}).get("electric_vehicles") or {
                    "ev0": {"device_id": "ev0"}
                }
                _set_path(data, "devices/electric_vehicles", vehicles)
                new_path = f"devices/electric_vehicles/{next(iter(vehicles))}/charge_rates"
            if old_path != new_path:
                existing = _get_json_nested_value(data, new_path)
                if existing is not None and existing != transformed:
                    raise ValueError(f"Conflicting old and new configuration paths: {old_path}")
                _drop_path(data, old_path.split("/"))
            _set_path(data, new_path, transformed)
            mapped_count += 1
        # Old optional provider containers have no current semantic value when empty.
        # Old dumps included inactive provider objects as null. Only consume
        # containers explicitly known through the migration map.
        for old_path in MIGRATION_MAP:
            parts = old_path.split("/")
            if "provider_settings" in parts:
                for length in range(2, len(parts)):
                    prefix = parts[:length]
                    if _get_json_nested_value(data, "/".join(prefix)) is None:
                        _drop_path(data, prefix)
        for section in data.values():
            if isinstance(section, dict) and section.get("provider_settings", False) is None:
                section.pop("provider_settings")
    data = _strip_computed_fields(data, SettingsEOSDefaults)
    data.setdefault("general", {})["schema_version"] = CONFIG_SCHEMA_VERSION
    data["general"]["version"] = __version__
    result = SettingsEOSDefaults.model_validate(data)
    _reject_unknown(data, result)
    auto_count = len(data)
    return result


def remove_empty(obj: dict) -> dict:
    """Recursively remove empty dicts and lists from a dictionary or list."""
    if isinstance(obj, dict):
        cleaned = {k: remove_empty(v) for k, v in obj.items()}
        # Remove keys where value is None, empty dict, or empty list
        return {k: v for k, v in cleaned.items() if v not in (None, {}, [])}
    elif isinstance(obj, list):
        cleaned = [remove_empty(v) for v in obj]
        # Remove empty elements
        return [v for v in cleaned if v not in (None, {}, [])]
    else:
        return obj


@contextmanager
def _migration_lock(path: Path) -> Iterator[None]:
    """Serialize cooperating migrations; OS releases the lock after a process crash."""
    lock_path = path.with_name(path.name + ".migration.lock")
    with lock_path.open("a+b") as handle:
        if sys.platform == "win32":
            import msvcrt

            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if sys.platform == "win32":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def migrate_config_file(config_file: Path, backup_file: Path) -> bool:
    """Validate, back up and atomically replace an older configuration schema.

    A failed migration never moves or truncates the input. The backup is created
    exclusively and flushed before replacement. Concurrent edits detected after
    preparation abort. Non-cooperating writers must be quiescent at activation.
    """
    temporary: str | None = None
    try:
        if config_file.resolve() == backup_file.resolve() or config_file.is_symlink():
            raise ValueError("Migration requires distinct regular config and backup paths")
        with _migration_lock(config_file):
            original = config_file.read_bytes()
            raw = json.loads(original)
            migrated = migrate_config_data(raw)
            if raw.get("general", {}).get("schema_version") == CONFIG_SCHEMA_VERSION:
                return True  # A code-only version change never rewrites the file.
            # Full serialization preserves explicit nulls and empty values. Defaults
            # may be elided, but the format marker must always be persisted.
            output = json.loads(migrated.model_dump_json(exclude_computed_fields=True))
            output.setdefault("general", {})["schema_version"] = CONFIG_SCHEMA_VERSION
            from akkudoktoreos.config.config import SettingsEOSDefaults

            SettingsEOSDefaults.model_validate(output)
            encoded = json.dumps(output, indent=4, ensure_ascii=False).encode("utf-8")
            descriptor, temporary = tempfile.mkstemp(prefix=".eos-migrate-", dir=config_file.parent)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, config_file.stat().st_mode & 0o777)
            with backup_file.open("xb") as backup:
                backup.write(original)
                backup.flush()
                os.fsync(backup.fileno())
            if config_file.read_bytes() != original:
                raise ValueError("Configuration changed while migration was being prepared")
            os.replace(temporary, config_file)
            temporary = None
            return True
    except Exception as error:
        # Do not log validation input values: configurations can contain secrets.
        logger.error("Configuration migration aborted ({})", type(error).__name__)
        return False
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def _get_json_nested_value(data: dict, path: str) -> Any:
    """Retrieve a nested value from a JSON-like dict using '/'-separated path."""
    current: Any = data
    for part in path.strip("/").split("/"):
        if isinstance(current, list):
            try:
                part_idx = int(part)
                current = current[part_idx]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        else:
            return None
    return current


def _unwrap_model_type(annotation: Any) -> type[BaseModel] | None:
    """Extract a BaseModel subclass from complex typing annotations.

    Supports:
    - Optional[T]
    - T | None
    - Union[T, ...]
    - Annotated[T, ...]
    """
    origin = get_origin(annotation)

    # Direct BaseModel
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation

    # Handle Union / Optional / |
    if origin in (Union, types.UnionType):
        for arg in get_args(annotation):
            model = _unwrap_model_type(arg)
            if model:
                return model
        return None

    # Handle Annotated[T, ...]
    if origin is not None:
        for arg in get_args(annotation):
            model = _unwrap_model_type(arg)
            if model:
                return model

    return None


def _strip_computed_fields(
    data: Any,
    model: type[BaseModel],
) -> Any:
    """Recursively remove computed fields from input data.

    This removes only fields declared via `@computed_field`
    in the Pydantic model hierarchy.

    Unknown fields are preserved to allow later migration handling.

    Args:
        data: Raw JSON-like data.
        model: Target Pydantic model class.

    Returns:
        Cleaned data structure.
    """
    if not isinstance(data, dict):
        return data

    cleaned = dict(data)

    # Remove computed fields at this level
    for field_name in model.model_computed_fields:
        cleaned.pop(field_name, None)

    # Recurse into declared model fields
    for field_name, field in model.model_fields.items():
        if field_name not in cleaned:
            continue

        value = cleaned[field_name]
        annotation = field.annotation
        origin = get_origin(annotation)
        if origin in (Union, types.UnionType):
            candidates = [arg for arg in get_args(annotation) if arg is not type(None)]
            if len(candidates) == 1:
                annotation = candidates[0]
                origin = get_origin(annotation)

        if isinstance(value, dict) and origin is dict:
            item_model = _unwrap_model_type(get_args(annotation)[1])
            if item_model:
                cleaned[field_name] = {
                    key: _strip_computed_fields(item, item_model) for key, item in value.items()
                }
            continue

        # 1️⃣ Direct nested model or Optional/Union-wrapped model
        nested_model = _unwrap_model_type(annotation)
        if nested_model and isinstance(value, dict):
            cleaned[field_name] = _strip_computed_fields(value, nested_model)
            continue

        # 2️⃣ List of models
        if origin is list:
            item_type = get_args(annotation)[0]
            nested_model = _unwrap_model_type(item_type)

            if nested_model and isinstance(value, list):
                cleaned[field_name] = [
                    _strip_computed_fields(v, nested_model) if isinstance(v, dict) else v
                    for v in value
                ]
            continue

    return cleaned
