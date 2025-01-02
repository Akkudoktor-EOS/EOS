import uvicorn
from fasthtml.common import H1, FastHTML, Table, Td, Th, Thead, Titled, Tr

from akkudoktoreos.config.config import get_config
from akkudoktoreos.core.logging import get_logger

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


app = FastHTML()
rt = app.route


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


if __name__ == "__main__":
    try:
        logger.info(
            f"Starting {config_eos.server_fasthtml_host}:{config_eos.server_fasthtml_port}."
        )
        uvicorn.run(
            app, host=str(config_eos.server_fasthtml_host), port=config_eos.server_fasthtml_port
        )
    except Exception as e:
        # Error handling for binding issues
        logger.error(
            f"Could not bind to host {config_eos.server_fasthtml_host}:{config_eos.server_fasthtml_port}. Error: {e}"
        )
        exit(1)
