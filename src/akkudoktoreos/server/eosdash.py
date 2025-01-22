import argparse
import os
import sys
import traceback
from pathlib import Path
from typing import Optional

import psutil
import uvicorn
from fasthtml.common import FileResponse, JSONResponse
from monsterui.core import FastHTML, Theme

from akkudoktoreos.config.config import get_config
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.server.dash.bokeh import BokehJS
from akkudoktoreos.server.dash.components import Page

# Pages
from akkudoktoreos.server.dash.configuration import Configuration
from akkudoktoreos.server.dash.demo import Demo
from akkudoktoreos.server.dash.footer import Footer
from akkudoktoreos.server.dash.hello import Hello
from akkudoktoreos.server.server import get_default_host, wait_for_port_free

# from akkudoktoreos.server.dash.altair import AltairJS

logger = get_logger(__name__)
config_eos = get_config()

# The favicon for EOSdash
favicon_filepath = Path(__file__).parent.joinpath("dash/assets/favicon/favicon.ico")
if not favicon_filepath.exists():
    raise ValueError(f"Does not exist {favicon_filepath}")

# Command line arguments
args: Optional[argparse.Namespace] = None


# Get frankenui and tailwind headers via CDN using Theme.green.headers()
# Add altair headers
# hdrs=(Theme.green.headers(highlightjs=True), AltairJS,)
hdrs = (
    Theme.green.headers(highlightjs=True),
    BokehJS,
)

# The EOSdash application
app: FastHTML = FastHTML(
    title="EOSdash",
    hdrs=hdrs,
    secret_key=os.getenv("EOS_SERVER__EOSDASH_SESSKEY"),
)


def eos_server() -> tuple[str, int]:
    """Retrieves the EOS server host and port configuration.

    If `args` is provided, it uses the `eos_host` and `eos_port` from `args`.
    Otherwise, it falls back to the values from `config_eos.server`.

    Returns:
        tuple[str, int]: A tuple containing:
            - `eos_host` (str): The EOS server hostname or IP.
            - `eos_port` (int): The EOS server port.
    """
    if args is None:
        eos_host = str(config_eos.server.host)
        eos_port = config_eos.server.port
    else:
        eos_host = args.eos_host
        eos_port = args.eos_port
    eos_host = eos_host if eos_host else get_default_host()
    eos_port = eos_port if eos_port else 8503

    return eos_host, eos_port


@app.get("/favicon.ico")
def get_eosdash_favicon():  # type: ignore
    """Get favicon."""
    return FileResponse(path=favicon_filepath)


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
        Footer(*eos_server()),
        "/eosdash/footer",
    )


@app.get("/eosdash/footer")
def get_eosdash_footer():  # type: ignore
    """Serves the EOSdash Foooter information.

    Returns:
        Footer: The Footer component.
    """
    return Footer(*eos_server())


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
    return Configuration(*eos_server())


@app.get("/eosdash/demo")
def get_eosdash_demo():  # type: ignore
    """Serves the EOSdash Demo page.

    Returns:
        Demo: The Demo page component.
    """
    return Demo(*eos_server())


@app.get("/eosdash/health")
def get_eosdash_health():  # type: ignore
    """Health check endpoint to verify that the EOSdash server is alive."""
    return JSONResponse(
        {
            "status": "alive",
            "pid": psutil.Process().pid,
        }
    )


@app.get("/eosdash/assets/{fname:path}.{ext:static}")
def get_eosdash_assets(fname: str, ext: str):  # type: ignore
    """Get assets."""
    asset_filepath = Path(__file__).parent.joinpath(f"dash/assets/{fname}.{ext}")
    return FileResponse(path=asset_filepath)


def run_eosdash() -> None:
    """Run the EOSdash server with the specified configurations.

    This function starts the EOSdash server using the Uvicorn ASGI server. It accepts
    arguments for the host, port, log level, access log, and reload options. The
    log level is converted to lowercase to ensure compatibility with Uvicorn's
    expected log level format. If an error occurs while attempting to bind the
    server to the specified host and port, an error message is logged and the
    application exits.

    Returns:
        None
    """
    # Setup parameters from args, config_eos and default
    # Remember parameters that are also in config
    # - EOS host
    if args and args.eos_host:
        eos_host = args.eos_host
    elif config_eos.server.host:
        eos_host = config_eos.server.host
    else:
        eos_host = get_default_host()
    config_eos.server.host = eos_host
    # - EOS port
    if args and args.eos_port:
        eos_port = args.eos_port
    elif config_eos.server.port:
        eos_port = config_eos.server.port
    else:
        eos_port = 8503
    config_eos.server.port = eos_port
    # - EOSdash host
    if args and args.host:
        eosdash_host = args.host
    elif config_eos.server.eosdash.host:
        eosdash_host = config_eos.server.eosdash_host
    else:
        eosdash_host = get_default_host()
    config_eos.server.eosdash_host = eosdash_host
    # - EOS port
    if args and args.port:
        eosdash_port = args.port
    elif config_eos.server.eosdash_port:
        eosdash_port = config_eos.server.eosdash_port
    else:
        eosdash_port = 8504
    config_eos.server.eosdash_port = eosdash_port
    # - log level
    if args and args.log_level:
        log_level = args.log_level
    else:
        log_level = "info"
    # - access log
    if args and args.access_log:
        access_log = args.access_log
    else:
        access_log = False
    # - reload
    if args and args.reload:
        reload = args.reload
    else:
        reload = False

    # Make hostname Windows friendly
    if eosdash_host == "0.0.0.0" and os.name == "nt":
        eosdash_host = "localhost"

    # Wait for EOSdash port to be free - e.g. in case of restart
    wait_for_port_free(eosdash_port, timeout=120, waiting_app_name="EOSdash")

    try:
        uvicorn.run(
            "akkudoktoreos.server.eosdash:app",
            host=eosdash_host,
            port=eosdash_port,
            log_level=log_level.lower(),
            access_log=access_log,
            reload=reload,
        )
    except Exception as e:
        logger.error(f"Could not bind to host {eosdash_host}:{eosdash_port}. Error: {e}")
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
        help="Host of the EOS server (default: value from config)",
    )
    parser.add_argument(
        "--eos-port",
        type=int,
        default=config_eos.server.port,
        help="Port of the EOS server (default: value from config)",
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
        run_eosdash()
    except Exception as ex:
        error_msg = f"Failed to run EOSdash: {ex}"
        logger.error(error_msg)
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
