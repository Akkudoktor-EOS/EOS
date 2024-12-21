"""This module provides functionality to manage and handle configuration for the EOS system.

The module including loading, merging, and validating JSON configuration files.
It also provides utility functions for working directory setup and date handling.

Key features:
- Loading and merging configurations from default or custom JSON files
- Validating configurations using Pydantic models
- Managing directory setups for the application
- Utility to get prediction start and end dates
"""

import json
import os
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ValidationError

EOS_DIR = "EOS_DIR"
ENCODING = "UTF-8"
CONFIG_FILE_NAME = "EOS.config.json"
DEFAULT_CONFIG_FILE = Path(__file__).parent.joinpath("default.config.json")


class FolderConfig(BaseModel):
    """Folder configuration for the EOS system.

    Uses working_dir as root path.
    The working directory can be either cwd or
    a path or folder defined by the EOS_DIR environment variable.

    Attributes:
        output (str): Directory name for output files.
        cache (str): Directory name for cache files.
    """

    output: str
    cache: str


class EOSConfig(BaseModel):
    """EOS system-specific configuration.

    Attributes:
        prediction_hours (int): Number of hours for predictions.
        optimization_hours (int): Number of hours for optimizations.
        penalty (int): Penalty factor used in optimization.
        available_charging_rates_in_percentage (list[float]): List of available charging rates as percentages.
    """

    prediction_hours: int
    optimization_hours: int
    penalty: int
    available_charging_rates_in_percentage: list[float]
    feed_in_tariff_eur_per_wh: int
    electricty_price_fixed_fee: float


class BaseConfig(BaseModel):
    """Base configuration for the EOS system.

    Attributes:
        directories (FolderConfig): Configuration for directory paths (output, cache).
        eos (EOSConfig): Configuration for EOS-specific settings.
    """

    directories: FolderConfig
    eos: EOSConfig


class AppConfig(BaseConfig):
    """Application-level configuration that extends the base configuration with a working directory.

    Attributes:
        working_dir (Path): The root directory for the application.
    """

    working_dir: Path

    def run_setup(self) -> None:
        """Runs setup for the application by ensuring that required directories exist.

        If a directory does not exist, it is created.

        Raises:
            OSError: If directories cannot be created.
        """
        print("Checking directory settings and creating missing directories...")
        for key, value in self.directories.model_dump().items():
            if not isinstance(value, str):
                continue
            path = self.working_dir / value
            print(f"'{key}': {path}")
            os.makedirs(path, exist_ok=True)


class SetupIncomplete(Exception):
    """Exception class for errors related to incomplete setup of the EOS system."""


def _load_json(path: Path) -> dict[str, Any]:
    """Load a JSON file from a given path.

    Args:
        path (Path): Path to the JSON file.

    Returns:
        dict[str, Any]: Parsed JSON content.

    Raises:
        FileNotFoundError: If the JSON file does not exist.
        json.JSONDecodeError: If the file cannot be parsed as valid JSON.
    """
    with path.open("r") as f_in:
        return json.load(f_in)


def _merge_json(default_data: dict[str, Any], custom_data: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge two dictionaries, using values from `custom_data` when available.

    Args:
        default_data (dict[str, Any]): The default configuration values.
        custom_data (dict[str, Any]): The custom configuration values.

    Returns:
        dict[str, Any]: Merged configuration data.
    """
    merged_data = {}
    for key, default_value in default_data.items():
        if key in custom_data:
            custom_value = custom_data[key]
            if isinstance(default_value, dict) and isinstance(custom_value, dict):
                merged_data[key] = _merge_json(default_value, custom_value)
            elif type(default_value) is type(custom_value):
                merged_data[key] = custom_value
            else:
                # use default value if types differ
                merged_data[key] = default_value
        else:
            merged_data[key] = default_value
    return merged_data


def _config_update_available(merged_data: dict[str, Any], custom_data: dict[str, Any]) -> bool:
    """Check if the configuration needs to be updated by comparing merged data and custom data.

    Args:
        merged_data (dict[str, Any]): The merged configuration data.
        custom_data (dict[str, Any]): The custom configuration data.

    Returns:
        bool: True if there is a difference indicating that an update is needed, otherwise False.
    """
    if merged_data.keys() != custom_data.keys():
        return True

    for key in merged_data:
        value1 = merged_data[key]
        value2 = custom_data[key]

        if isinstance(value1, dict) and isinstance(value2, dict):
            if _config_update_available(value1, value2):
                return True
        elif value1 != value2:
            return True
    return False


def get_config_file(path: Path, copy_default: bool) -> Path:
    """Get the valid configuration file path. If the custom config is not found, it uses the default config.

    Args:
        path (Path): Path to the working directory.
        copy_default (bool): If True, copy the default configuration if custom config is not found.

    Returns:
        Path: Path to the valid configuration file.
    """
    config = path.resolve() / CONFIG_FILE_NAME
    if config.is_file():
        print(f"Using configuration from: {config}")
        return config

    if not path.is_dir():
        print(f"Path does not exist: {path}. Using default configuration...")
        return DEFAULT_CONFIG_FILE

    if not copy_default:
        print("No custom configuration provided. Using default configuration...")
        return DEFAULT_CONFIG_FILE

    try:
        return Path(shutil.copy2(DEFAULT_CONFIG_FILE, config))
    except Exception as exc:
        print(f"Could not copy default config: {exc}. Using default copy...")
    return DEFAULT_CONFIG_FILE


def _merge_and_update(custom_config: Path, update_outdated: bool = False) -> bool:
    """Merge custom and default configurations, and optionally update the custom config if outdated.

    Args:
        custom_config (Path): Path to the custom configuration file.
        update_outdated (bool): If True, update the custom config if it is outdated.

    Returns:
        bool: True if the custom config was updated, otherwise False.
    """
    if custom_config == DEFAULT_CONFIG_FILE:
        return False
    default_data = _load_json(DEFAULT_CONFIG_FILE)
    custom_data = _load_json(custom_config)
    merged_data = _merge_json(default_data, custom_data)

    if not _config_update_available(merged_data, custom_data):
        print(f"Custom config {custom_config} is up-to-date...")
        return False
    print(f"Custom config {custom_config} is outdated...")
    if update_outdated:
        with custom_config.open("w") as f_out:
            json.dump(merged_data, f_out, indent=2)
        return True
    return False


def load_config(
    working_dir: Path, copy_default: bool = False, update_outdated: bool = True
) -> AppConfig:
    """Load the application configuration from the specified directory, merging with defaults if needed.

    Args:
        working_dir (Path): Path to the working directory.
        copy_default (bool): Whether to copy the default configuration if custom config is missing.
        update_outdated (bool): Whether to update outdated custom configuration.

    Returns:
        AppConfig: Loaded application configuration.

    Raises:
        ValueError: If the configuration is incomplete or not valid.
    """
    # make sure working_dir is always a full path
    working_dir = working_dir.resolve()

    config = get_config_file(working_dir, copy_default)
    _merge_and_update(config, update_outdated)

    with config.open("r", encoding=ENCODING) as f_in:
        try:
            base_config = BaseConfig.model_validate(json.load(f_in))
            return AppConfig.model_validate(
                {"working_dir": working_dir, **base_config.model_dump()}
            )
        except ValidationError as exc:
            raise ValueError(f"Configuration {config} is incomplete or not valid: {exc}")


def get_working_dir() -> Path:
    """Get the working directory for the application, either from an environment variable or the current working directory.

    Returns:
        Path: The path to the working directory.
    """
    custom_dir = os.getenv(EOS_DIR)
    if custom_dir is None:
        working_dir = Path.cwd()
        print(f"No custom directory provided. Setting working directory to: {working_dir}")
    else:
        working_dir = Path(custom_dir).resolve()
        print(f"Custom directory provided. Setting working directory to: {working_dir}")
    return working_dir


def get_start_enddate(prediction_hours: int, startdate: Optional[date] = None) -> tuple[str, str]:
    """Calculate the start and end dates based on the given prediction hours and optional start date.

    Args:
        prediction_hours (int): Number of hours for predictions.
        startdate (Optional[datetime]): Optional starting datetime.

    Returns:
        tuple[str, str]: The current date (start date) and end date in the format 'YYYY-MM-DD'.
    """
    if startdate is None:
        date = (datetime.now().date() + timedelta(hours=prediction_hours)).strftime("%Y-%m-%d")
        date_now = datetime.now().strftime("%Y-%m-%d")
    else:
        date = (startdate + timedelta(hours=prediction_hours)).strftime("%Y-%m-%d")
        date_now = startdate.strftime("%Y-%m-%d")
    return date_now, date
