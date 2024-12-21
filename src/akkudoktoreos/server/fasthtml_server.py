import os
import sys

import uvicorn
from fasthtml.common import H1, Table, Td, Th, Thead, Titled, Tr, fast_app

from akkudoktoreos.config.config import get_config
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)

config_eos = get_config()


configs = []
for field_name in config_eos.model_fields:
    config = {}
    config["name"] = field_name
    config["value"] = getattr(config_eos, field_name)
    config["default"] = config_eos.model_fields[field_name].default
    config["description"] = config_eos.model_fields[field_name].description
    configs.append(config)


app, rt = fast_app(
    secret_key=os.getenv("SESSKEY"), live=bool(config_eos.server_fasthtml_development)
)


def config_table() -> Table:
    rows = [
        Tr(
            Td(config["name"]),
            Td(config["value"]),
            Td(config["default"]),
            Td(config["description"]),
            cls="even:bg-purple/5",
        )
        for config in configs
    ]
    flds = "Name", "Value", "Default", "Description"
    head = Thead(*map(Th, flds), cls="bg-purple/10")
    return Table(head, *rows, cls="w-full")


@rt("/")
def get():  # type: ignore
    return Titled("EOS Config App", H1("Configuration"), config_table())


def start_fasthtml_server() -> None:
    """STart FastHTML server."""
    try:
        uvicorn.run(
            "__main__:app",
            host=str(config_eos.server_fasthtml_host),
            port=config_eos.server_fasthtml_port,
            log_level="debug",
            access_log=True,
            reload=bool(config_eos.server_fasthtml_development),
        )
    except Exception as e:
        logger.error(
            f"Could not bind to host {config_eos.server_fasthtml_host}:{config_eos.server_fasthtml_port}. Error: {e}"
        )
        sys.exit(1)


if __name__ == "__main__":
    start_fasthtml_server()
