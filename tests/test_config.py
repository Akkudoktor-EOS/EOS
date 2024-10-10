import json
from pathlib import Path
from pydantic import ValidationError
import pytest
from akkudoktoreos.config import (
    CONFIG_FILE_NAME,
    DEFAULT_CONFIG_FILE,
    get_config_file,
    load_config,
)


def test_config() -> None:
    "Test the default config file."

    try:
        load_config(DEFAULT_CONFIG_FILE)
    except ValidationError as exc:
        pytest.fail(f"Default configuration is not valid: {exc}")


def test_config_copy(tmp_path: Path) -> None:
    "Test if the config is copied to the provided path."
    assert DEFAULT_CONFIG_FILE == get_config_file(None)
    assert DEFAULT_CONFIG_FILE == get_config_file(Path("does", "not", "exist"))

    load_config(tmp_path)
    expected_config = tmp_path.joinpath(CONFIG_FILE_NAME)

    assert expected_config == get_config_file(tmp_path)
    assert expected_config.is_file()


def test_config_merge(tmp_path: Path) -> None:
    "Test if config is merged and updated correctly."

    config_file = tmp_path.joinpath(CONFIG_FILE_NAME)
    custom_config = {
        "optimization_hours": 48,
        "strafe": 20,
        "does_not_exist": "nope",
        "possible_charge_values": "False entry",
    }
    with config_file.open("w") as f_out:
        json.dump(custom_config, f_out)

    assert config_file.exists()

    with pytest.raises(ValueError):
        # custom configuration is broken but not updated.
        load_config(tmp_path, False)

    with config_file.open("r") as f_in:
        # custom configuration is not changed.
        assert json.load(f_in) == custom_config

    config = load_config(tmp_path)

    assert config.optimization_hours == 48
    assert config.strafe == 20
