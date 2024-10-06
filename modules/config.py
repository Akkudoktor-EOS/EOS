import json
import sys
from pathlib import Path

from pydantic import BaseModel, ValidationError

ENCODING = "UTF-8"
CONFIG_DIR = Path(__file__).parent.parent.joinpath("config")
CONFIG_FILE = CONFIG_DIR.joinpath("config.json")


class DBConfig(BaseModel):
    """
    The database configuration
    """

    user: str
    password: str
    host: str
    database: str


class AppConfig(BaseModel):
    """
    The base configuration.
    """

    prediction_hours: int
    optimization_hours: int
    strafe: int
    moegliche_ladestroeme_in_prozent: list[float]
    db_config: DBConfig


def load_config(file: Path = CONFIG_FILE) -> AppConfig:
    if not file.is_file():
        print(f"Configuration file {file} does not exist.")
        sys.exit(1)

    with file.open("r", encoding=ENCODING) as f_in:
        try:
            config: AppConfig = AppConfig.model_validate(json.load(f_in))
        except ValidationError as exc:
            print(f"Configuration is incomplete or not valid: {exc}")
            sys.exit(1)
    return config
