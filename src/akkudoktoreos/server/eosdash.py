import argparse
import os
from functools import reduce
from typing import Any, Union

import uvicorn
from fasthtml.common import H1, FastHTML, Table, Td, Th, Thead, Titled, Tr
from pydantic.fields import ComputedFieldInfo, FieldInfo
from pydantic_core import PydanticUndefined

from akkudoktoreos.config.config import get_config
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.core.pydantic import PydanticBaseModel

logger = get_logger(__name__)

config_eos = get_config()

# Command line arguments
args = None


def get_default_value(field_info: Union[FieldInfo, ComputedFieldInfo], regular_field: bool) -> Any:
    default_value = ""
    if regular_field:
        if (val := field_info.default) is not PydanticUndefined:
            default_value = val
    else:
        default_value = "N/A"
    return default_value


def resolve_nested_types(field_type: Any, parent_types: list[str]) -> list[tuple[Any, list[str]]]:
    resolved_types: list[tuple[Any, list[str]]] = []

    origin = getattr(field_type, "__origin__", field_type)
    if origin is Union:
        for arg in getattr(field_type, "__args__", []):
            if arg is not type(None):
                resolved_types.extend(resolve_nested_types(arg, parent_types))
    else:
        resolved_types.append((field_type, parent_types))

    return resolved_types


configs = []
inner_types: set[type[PydanticBaseModel]] = set()
for field_name, field_info in list(config_eos.model_fields.items()) + list(
    config_eos.model_computed_fields.items()
):

    def extract_nested_models(
        subfield_info: Union[ComputedFieldInfo, FieldInfo], parent_types: list[str]
    ) -> None:
        regular_field = isinstance(subfield_info, FieldInfo)
        subtype = subfield_info.annotation if regular_field else subfield_info.return_type

        if subtype in inner_types:
            return

        nested_types = resolve_nested_types(subtype, [])
        found_basic = False
        for nested_type, nested_parent_types in nested_types:
            if not isinstance(nested_type, type) or not issubclass(nested_type, PydanticBaseModel):
                if found_basic:
                    continue

                config = {}
                config["name"] = ".".join(parent_types)
                try:
                    config["value"] = reduce(getattr, [config_eos] + parent_types)
                except AttributeError:
                    # Parent value(s) are not set in current config
                    config["value"] = ""
                config["default"] = get_default_value(subfield_info, regular_field)
                config["description"] = (
                    subfield_info.description if subfield_info.description else ""
                )
                configs.append(config)
                found_basic = True
            else:
                new_parent_types = parent_types + nested_parent_types
                inner_types.add(nested_type)
                for nested_field_name, nested_field_info in list(
                    nested_type.model_fields.items()
                ) + list(nested_type.model_computed_fields.items()):
                    extract_nested_models(
                        nested_field_info,
                        new_parent_types + [nested_field_name],
                    )

    extract_nested_models(field_info, [field_name])


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
    return Titled("EOS Dashboard", H1("Configuration"), config_table())


def run_eosdash(host: str, port: int, log_level: str, access_log: bool, reload: bool) -> None:
    """Run the EOSdash server with the specified configurations.

    This function starts the EOSdash server using the Uvicorn ASGI server. It accepts
    arguments for the host, port, log level, access log, and reload options. The
    log level is converted to lowercase to ensure compatibility with Uvicorn's
    expected log level format. If an error occurs while attempting to bind the
    server to the specified host and port, an error message is logged and the
    application exits.

    Parameters:
    host (str): The hostname to bind the server to.
    port (int): The port number to bind the server to.
    log_level (str): The log level for the server. Options include "critical", "error",
                     "warning", "info", "debug", and "trace".
    access_log (bool): Whether to enable or disable the access log. Set to True to enable.
    reload (bool): Whether to enable or disable auto-reload. Set to True for development.

    Returns:
    None
    """
    # Make hostname Windows friendly
    if host == "0.0.0.0" and os.name == "nt":
        host = "localhost"
    try:
        uvicorn.run(
            "akkudoktoreos.server.eosdash:app",
            host=host,
            port=port,
            log_level=log_level.lower(),  # Convert log_level to lowercase
            access_log=access_log,
            reload=reload,
        )
    except Exception as e:
        logger.error(f"Could not bind to host {host}:{port}. Error: {e}")
        raise e


def main() -> None:
    """Parse command-line arguments and start the EOSdash server with the specified options.

    This function sets up the argument parser to accept command-line arguments for
    host, port, log_level, access_log, and reload. It uses default values from the
    config_eos module if arguments are not provided. After parsing the arguments,
    it starts the EOSdash server with the specified configurations.

    Command-line Arguments:
    --host (str): Host for the EOSdash server (default: value from config_eos).
    --port (int): Port for the EOSdash server (default: value from config_eos).
    --eos-host (str): Host for the EOS server (default: value from config_eos).
    --eos-port (int): Port for the EOS server (default: value from config_eos).
    --log_level (str): Log level for the server. Options: "critical", "error", "warning", "info", "debug", "trace" (default: "info").
    --access_log (bool): Enable or disable access log. Options: True or False (default: False).
    --reload (bool): Enable or disable auto-reload. Useful for development. Options: True or False (default: False).
    """
    parser = argparse.ArgumentParser(description="Start EOSdash server.")

    # Host and port arguments with defaults from config_eos
    parser.add_argument(
        "--host",
        type=str,
        default=str(config_eos.server.server_eosdash_host),
        help="Host for the EOSdash server (default: value from config_eos)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=config_eos.server.server_eosdash_port,
        help="Port for the EOSdash server (default: value from config_eos)",
    )

    # EOS Host and port arguments with defaults from config_eos
    parser.add_argument(
        "--eos-host",
        type=str,
        default=str(config_eos.server.server_eos_host),
        help="Host for the EOS server (default: value from config_eos)",
    )
    parser.add_argument(
        "--eos-port",
        type=int,
        default=config_eos.server.server_eos_port,
        help="Port for the EOS server (default: value from config_eos)",
    )

    # Optional arguments for log_level, access_log, and reload
    parser.add_argument(
        "--log_level",
        type=str,
        default="info",
        help='Log level for the server. Options: "critical", "error", "warning", "info", "debug", "trace" (default: "info")',
    )
    parser.add_argument(
        "--access_log",
        type=bool,
        default=False,
        help="Enable or disable access log. Options: True or False (default: True)",
    )
    parser.add_argument(
        "--reload",
        type=bool,
        default=False,
        help="Enable or disable auto-reload. Useful for development. Options: True or False (default: False)",
    )

    args = parser.parse_args()

    try:
        run_eosdash(args.host, args.port, args.log_level, args.access_log, args.reload)
    except:
        exit(1)


if __name__ == "__main__":
    main()
