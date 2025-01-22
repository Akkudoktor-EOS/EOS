import argparse
import os
import sys
from typing import Optional

import uvicorn
from monsterui.core import FastHTML, Theme

from akkudoktoreos.config.config import get_config
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.server.dash.components import Page

# Pages
from akkudoktoreos.server.dash.configuration import Configuration
from akkudoktoreos.server.dash.demo import Demo
from akkudoktoreos.server.dash.hello import Hello

logger = get_logger(__name__)
config_eos = get_config()

# Command line arguments
args: Optional[argparse.Namespace] = None

# The EOSdash application
app: FastHTML = FastHTML(
    title="EOSdash",
    hdrs=Theme.green.headers(highlightjs=True),
    secret_key=os.getenv("EOS_SERVER__EOSDASH_SESSKEY"),
)


@app.get("/")
def get_eosdash():  # type: ignore
    """Serves the main EOSdash page.

    Returns:
        Page: The main dashboard page with navigation links and footer.
    """
    return Page(
        None,
        {
            "EOSdash": "/eosdash/hello",
            "Config": "/eosdash/configuration",
            "Demo": "/eosdash/demo",
        },
        Hello(),
        "Footer_Info",
    )


@app.get("/eosdash/hello")
def get_eosdash_hello():  # type: ignore
    """Serves the EOSdash Hello page.

    Returns:
        Hello: The Hello page component.
    """
    return Hello()


@app.get("/eosdash/configuration")
def get_eosdash_configuration():  # type: ignore
    """Serves the EOSdash Configuration page.

    Returns:
        Configuration: The Configuration page component.
    """
    if args is None:
        eos_host = None
        eos_port = None
    else:
        eos_host = args.eos_host
        eos_port = args.eos_port
    return Configuration(eos_host, eos_port)


@app.get("/eosdash/demo")
def get_eosdash_demo():  # type: ignore
    """Serves the EOSdash Demo page.

    Returns:
        Demo: The Demo page component.
    """
    return Demo()


def run_eosdash(host: str, port: int, log_level: str, access_log: bool, reload: bool) -> None:
    """Run the EOSdash server with the specified configurations.

    This function starts the EOSdash server using the Uvicorn ASGI server. It accepts
    arguments for the host, port, log level, access log, and reload options. The
    log level is converted to lowercase to ensure compatibility with Uvicorn's
    expected log level format. If an error occurs while attempting to bind the
    server to the specified host and port, an error message is logged and the
    application exits.

    Args:
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
            log_level=log_level.lower(),
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
    config module if arguments are not provided. After parsing the arguments,
    it starts the EOSdash server with the specified configurations.

    Command-line Arguments:
    --host (str): Host for the EOSdash server (default: value from config).
    --port (int): Port for the EOSdash server (default: value from config).
    --eos-host (str): Host for the EOS server (default: value from config).
    --eos-port (int): Port for the EOS server (default: value from config).
    --log_level (str): Log level for the server. Options: "critical", "error", "warning", "info", "debug", "trace" (default: "info").
    --access_log (bool): Enable or disable access log. Options: True or False (default: False).
    --reload (bool): Enable or disable auto-reload. Useful for development. Options: True or False (default: False).
    """
    parser = argparse.ArgumentParser(description="Start EOSdash server.")

    parser.add_argument(
        "--host",
        type=str,
        default=str(config_eos.server.eosdash_host),
        help="Host for the EOSdash server (default: value from config)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=config_eos.server.eosdash_port,
        help="Port for the EOSdash server (default: value from config)",
    )
    parser.add_argument(
        "--eos-host",
        type=str,
        default=str(config_eos.server.host),
        help="Host for the EOS server (default: value from config)",
    )
    parser.add_argument(
        "--eos-port",
        type=int,
        default=config_eos.server.port,
        help="Port for the EOS server (default: value from config)",
    )
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
        help="Enable or disable access log. Options: True or False (default: False)",
    )
    parser.add_argument(
        "--reload",
        type=bool,
        default=False,
        help="Enable or disable auto-reload. Useful for development. Options: True or False (default: False)",
    )

    global args
    args = parser.parse_args()

    try:
        run_eosdash(args.host, args.port, args.log_level, args.access_log, args.reload)
    except:
        sys.exit(1)


if __name__ == "__main__":
    main()
