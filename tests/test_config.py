from pathlib import Path

from modules.config import load_config


def test_config() -> None:
    """Test the configuration file"""
    example_config = Path(__file__).parent.parent.joinpath(
        "config", "example.config.json"
    )
    config = load_config(example_config)
    assert config.strafe == 10
    assert config.db_config.user == "eos"

    # test migration from dict usage to model dump
    old_db_config = {
        "user": "eos",
        "password": "eos",
        "host": "mariadb",
        "database": "eos",
    }
    assert old_db_config == config.db_config.model_dump()
