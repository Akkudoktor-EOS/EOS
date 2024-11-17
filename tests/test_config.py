import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from akkudoktoreos.config import (
    CONFIG_FILE_NAME,
    DEFAULT_CONFIG_FILE,
    get_config_file,
    load_config,
)


def test_config() -> None:
    """Test the default config file."""
    try:
        load_config(Path.cwd())
    except ValidationError as exc:
        pytest.fail(f"Default configuration is not valid: {exc}")


def test_config_copy(tmp_path: Path) -> None:
    """Test if the config is copied to the provided path."""
    assert DEFAULT_CONFIG_FILE == get_config_file(Path("does", "not", "exist"), False)

    load_config(tmp_path, True)
    expected_config = tmp_path.joinpath(CONFIG_FILE_NAME)

    assert expected_config == get_config_file(tmp_path, False)
    assert expected_config.is_file()


def test_config_merge(tmp_path: Path) -> None:
    """Test if config is merged and updated correctly."""
    config_file = tmp_path.joinpath(CONFIG_FILE_NAME)
    custom_config = {
        "eos": {
            "optimization_hours": 30,
            "penalty": 21,
            "does_not_exist": "nope",
            "available_charging_rates_in_percentage": "False entry",
        }
    }
    with config_file.open("w") as f_out:
        json.dump(custom_config, f_out)

    assert config_file.exists()

    with pytest.raises(ValueError):
        # custom configuration is broken but not updated.
        load_config(tmp_path, True, False)

    with config_file.open("r") as f_in:
        # custom configuration is not changed.
        assert json.load(f_in) == custom_config

    config = load_config(tmp_path)

    assert config.eos.optimization_hours == 30
    assert config.eos.penalty == 21


def test_setup(tmp_path: Path) -> None:
    """Test setup."""
    config = load_config(tmp_path, True)
    config.run_setup()

    assert tmp_path.joinpath(CONFIG_FILE_NAME).is_file()
    assert tmp_path.joinpath(config.directories.cache).is_dir()
    assert tmp_path.joinpath(config.directories.output).is_dir()
