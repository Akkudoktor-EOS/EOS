from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
from typing import Any, Optional

from pydantic import BaseModel, ValidationError

ENCODING = "UTF-8"
CONFIG_FILE_NAME = "EOS.config.json"
DEFAULT_CONFIG_FILE = Path(__file__).parent.joinpath("default.config.json")


class AppConfig(BaseModel):
    """
    The base configuration.
    """

    prediction_hours: int
    optimization_hours: int
    strafe: int
    possible_charge_values: list[float]


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r") as f_in:
        return json.load(f_in)


def _merge_json(
    default_data: dict[str, Any], custom_data: dict[str, Any]
) -> dict[str, Any]:
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


def _config_update_available(
    merged_data: dict[str, Any], custom_data: dict[str, Any]
) -> bool:
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


def get_config_file(config_path: Optional[Path]) -> Path:
    if config_path is None:
        print("No config path defined. Using default config...")
    else:
        if config_path.is_dir():
            print("Found EOS user directory.")
            config = config_path.joinpath(CONFIG_FILE_NAME)
            if config.is_file():
                return config
            else:
                print(
                    f"No configuration file found. Copying default config to provided path: {config_path}"
                )
                try:
                    return Path(shutil.copy2(DEFAULT_CONFIG_FILE, config))
                except Exception as exc:
                    print(
                        f"Could not copy default config: {exc}. Using default copy..."
                    )
        else:
            print(
                f"Provided path is not a directory: {config_path}. Using default config..."
            )
    return DEFAULT_CONFIG_FILE


def merge_and_update(custom_config: Path, update_outdated: bool = False) -> None:
    if custom_config == DEFAULT_CONFIG_FILE:
        return
    default_data = _load_json(DEFAULT_CONFIG_FILE)
    custom_data = _load_json(custom_config)
    merged_data = _merge_json(default_data, custom_data)

    if not _config_update_available(merged_data, custom_data):
        print(f"Custom config {custom_config} is up-to-date...")
        return
    print(f"Custom config {custom_config} is outdated...")
    if update_outdated:
        with custom_config.open("w") as f_out:
            json.dump(merged_data, f_out)


def load_config(
    config_path: Optional[Path] = None, update_outdated: bool = True
) -> AppConfig:
    "Load AppConfig from provided path or default"
    config = get_config_file(config_path)
    merge_and_update(config, update_outdated)

    with config.open("r", encoding=ENCODING) as f_in:
        try:
            return AppConfig.model_validate(json.load(f_in))
        except ValidationError as exc:
            raise ValueError(
                f"Configuration {config} is incomplete or not valid: {exc}"
            )


def get_start_enddate(
    prediction_hours: int, startdate: Optional[datetime] = None
) -> tuple[str, str]:
    ############
    # Parameter
    ############
    if startdate is None:
        date = (datetime.now().date() + timedelta(hours=prediction_hours)).strftime(
            "%Y-%m-%d"
        )
        date_now = datetime.now().strftime("%Y-%m-%d")
    else:
        date = (startdate + timedelta(hours=prediction_hours)).strftime("%Y-%m-%d")
        date_now = startdate.strftime("%Y-%m-%d")
    return date_now, date
