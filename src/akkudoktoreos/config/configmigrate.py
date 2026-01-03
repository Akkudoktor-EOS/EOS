"""Migrate config file to actual version."""

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Set, Tuple, Union, cast

from loguru import logger

from akkudoktoreos.core.version import __version__

if TYPE_CHECKING:
    # There are circular dependencies - only import here for type checking
    from akkudoktoreos.config.config import SettingsEOSDefaults


_KEEP_DEFAULT = object()

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
    # 0.2.0.dev -> 0.2.0.dev
    "adapter/homeassistant/optimization_solution_entity_ids": (
        "adapter/homeassistant/solution_entity_ids",
        lambda v: v if isinstance(v, list) else None,
    ),
    "general/data_folder_path": ("general/data_folder_path", _KEEP_DEFAULT),
    # 0.2.0 -> 0.2.0+dev
    "elecprice/provider_settings/ElecPriceImport/import_file_path": "elecprice/elecpriceimport/import_file_path",
    "elecprice/provider_settings/ElecPriceImport/import_json": "elecprice/elecpriceimport/import_json",
    # 0.1.0 -> 0.2.0+dev
    "devices/batteries/0/initial_soc_percentage": None,
    "devices/electric_vehicles/0/initial_soc_percentage": None,
    "elecprice/provider_settings/import_file_path": "elecprice/elecpriceimport/import_file_path",
    "elecprice/provider_settings/import_json": "elecprice/elecpriceimport/import_json",
    "load/provider_settings/import_file_path": "load/provider_settings/LoadImport/import_file_path",
    "load/provider_settings/import_json": "load/provider_settings/LoadImport/import_json",
    "load/provider_settings/loadakkudoktor_year_energy": "load/provider_settings/LoadAkkudoktor/loadakkudoktor_year_energy_kwh",
    "load/provider_settings/load_vrm_idsite": "load/provider_settings/LoadVrm/load_vrm_idsite",
    "load/provider_settings/load_vrm_token": "load/provider_settings/LoadVrm/load_vrm_token",
    "logging/level": "logging/console_level",
    "logging/root_level": None,
    "measurement/load0_name": "measurement/load_emr_keys/0",
    "measurement/load1_name": "measurement/load_emr_keys/1",
    "measurement/load2_name": "measurement/load_emr_keys/2",
    "measurement/load3_name": "measurement/load_emr_keys/3",
    "measurement/load4_name": "measurement/load_emr_keys/4",
    "optimization/ev_available_charge_rates_percent": (
        "devices/electric_vehicles/0/charge_rates",
        lambda v: [x / 100 for x in v],
    ),
    "optimization/hours": "optimization/horizon_hours",
    "optimization/penalty": ("optimization/genetic/penalties/ev_soc_miss", lambda v: float(v)),
    "pvforecast/provider_settings/import_file_path": "pvforecast/provider_settings/PVForecastImport/import_file_path",
    "pvforecast/provider_settings/import_json": "pvforecast/provider_settings/PVForecastImport/import_json",
    "pvforecast/provider_settings/load_vrm_idsite": "pvforecast/provider_settings/PVForecastVrm/load_vrm_idsite",
    "pvforecast/provider_settings/load_vrm_token": "pvforecast/provider_settings/PVForecastVrm/load_vrm_token",
    "weather/provider_settings/import_file_path": "weather/provider_settings/WeatherImport/import_file_path",
    "weather/provider_settings/import_json": "weather/provider_settings/WeatherImport/import_json",
}

# -----------------------------
# Global migration stats
# -----------------------------
migrated_source_paths: Set[str] = set()
mapped_count: int = 0
auto_count: int = 0
skipped_paths: List[str] = []


def migrate_config_data(config_data: Dict[str, Any]) -> "SettingsEOSDefaults":
    """Migrate configuration data to the current version settings.

    Returns:
        SettingsEOSDefaults: The migrated settings.
    """
    global migrated_source_paths, mapped_count, auto_count, skipped_paths

    # Reset globals at the start of each migration
    migrated_source_paths = set()
    mapped_count = 0
    auto_count = 0
    skipped_paths = []

    from akkudoktoreos.config.config import SettingsEOSDefaults

    new_config = SettingsEOSDefaults()

    # 1) Apply explicit migration map
    for old_path, mapping in MIGRATION_MAP.items():
        new_path = None
        transform = None
        keep_default = False

        if mapping is None:
            migrated_source_paths.add(old_path.strip("/"))
            logger.debug(f"🗑️ Migration map: dropping '{old_path}'")
            continue
        if isinstance(mapping, tuple):
            new_path = mapping[0]
            for m in mapping[1:]:
                if m is _KEEP_DEFAULT:
                    keep_default = True
                elif callable(m):
                    transform = cast(Callable[[Any], Any], m)
        else:
            new_path = mapping

        old_value = _get_json_nested_value(config_data, old_path)
        if old_value is None:
            if keep_default:
                migrated_source_paths.add(old_path.strip("/"))
                mapped_count += 1
                logger.debug(f"✅ Migrated mapped '{old_path}' → keeping new default")
            else:
                migrated_source_paths.add(old_path.strip("/"))
                mapped_count += 1
                logger.debug(f"✅ Migrated mapped '{old_path}' → 'None'")
            continue

        try:
            if transform:
                old_value = transform(old_value)
            new_config.set_nested_value(new_path, old_value)
            migrated_source_paths.add(old_path.strip("/"))
            mapped_count += 1
            logger.debug(f"✅ Migrated mapped '{old_path}' → '{new_path}' = {old_value!r}")
        except Exception as e:
            logger.opt(exception=True).warning(
                f"Failed mapped migration '{old_path}' -> '{new_path}': {e}"
            )

    # 2) Automatic migration for remaining fields
    auto_count += _migrate_matching_fields(
        config_data, new_config, migrated_source_paths, skipped_paths
    )

    # 3) Ensure version
    try:
        new_config.set_nested_value("general/version", __version__)
    except Exception as e:
        logger.warning(f"Could not set version on new configuration model: {e}")

    # 4) Log final migration summary
    logger.info(
        f"Migration summary: "
        f"mapped fields: {mapped_count}, automatically migrated: {auto_count}, skipped: {len(skipped_paths)}"
    )
    if skipped_paths:
        logger.debug(f"Skipped paths: {', '.join(skipped_paths)}")

    logger.success(f"Configuration successfully migrated to version {__version__}.")
    return new_config


def migrate_config_file(config_file: Path, backup_file: Path) -> bool:
    """Migrate configuration file to the current version.

    Returns:
        bool: True if up-to-date or successfully migrated, False on failure.
    """
    global migrated_source_paths, mapped_count, auto_count, skipped_paths

    # Reset globals at the start of each migration
    migrated_source_paths = set()
    mapped_count = 0
    auto_count = 0
    skipped_paths = []

    try:
        with config_file.open("r", encoding="utf-8") as f:
            config_data: Dict[str, Any] = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Failed to read configuration file '{config_file}': {e}")
        return False

    match config_data:
        case {"general": {"version": v}} if v == __version__:
            logger.debug(f"Configuration file '{config_file}' is up to date (v{v}).")
            return True
        case _:
            logger.info(
                f"Configuration file '{config_file}' is missing current version info. "
                f"Starting migration to v{__version__}..."
            )

    try:
        # Backup existing file - we already know it is existing
        try:
            config_file.replace(backup_file)
            logger.info(f"Backed up old configuration to '{backup_file}'.")
        except Exception as e_replace:
            try:
                shutil.copy(config_file, backup_file)
                logger.info(
                    f"Could not replace; copied old configuration to '{backup_file}' instead."
                )
            except Exception as e_copy:
                logger.warning(
                    f"Failed to backup existing config (replace: {e_replace}; copy: {e_copy}). Continuing without backup."
                )

        # Migrate config data
        new_config = migrate_config_data(config_data)

        # Write migrated configuration
        try:
            with config_file.open("w", encoding="utf-8", newline=None) as f_out:
                json_str = new_config.model_dump_json(indent=4)
                f_out.write(json_str)
        except Exception as e_write:
            logger.error(f"Failed to write migrated configuration to '{config_file}': {e_write}")
            return False

        return True

    except Exception as e:
        logger.exception(f"Unexpected error during migration: {e}")
        return False


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


def _migrate_matching_fields(
    source: Dict[str, Any],
    target_model: Any,
    migrated_source_paths: Set[str],
    skipped_paths: List[str],
    prefix: str = "",
) -> int:
    """Recursively copy matching keys from source dict into target_model using set_nested_value.

    Returns:
        int: number of fields successfully auto-migrated
    """
    count: int = 0
    for key, value in source.items():
        full_path = f"{prefix}/{key}".strip("/")

        if full_path in migrated_source_paths:
            continue

        if isinstance(value, dict):
            count += _migrate_matching_fields(
                value, target_model, migrated_source_paths, skipped_paths, full_path
            )
        else:
            try:
                target_model.set_nested_value(full_path, value)
                count += 1
            except Exception:
                skipped_paths.append(full_path)
                continue
    return count
