import argparse
import os
import sys
import traceback
from pathlib import Path

import psutil
import uvicorn
from fasthtml.common import FileResponse, JSONResponse
from loguru import logger
from monsterui.core import FastHTML, Theme

from akkudoktoreos.config.config import get_config
from akkudoktoreos.core.logabc import LOGGING_LEVELS
from akkudoktoreos.core.logging import track_logging_config
from akkudoktoreos.core.version import __version__
from akkudoktoreos.server.dash.about import About

# Pages
from akkudoktoreos.server.dash.admin import Admin
from akkudoktoreos.server.dash.bokeh import BokehJS
from akkudoktoreos.server.dash.components import Page
from akkudoktoreos.server.dash.configuration import ConfigKeyUpdate, Configuration
from akkudoktoreos.server.dash.footer import Footer
from akkudoktoreos.server.dash.plan import Plan
from akkudoktoreos.server.dash.prediction import Prediction
from akkudoktoreos.server.server import get_default_host, wait_for_port_free
from akkudoktoreos.utils.stringutil import str2bool

config_eos = get_config()


# ------------------------------------
# Logging configuration at import time
# ------------------------------------

logger.remove()
track_logging_config(config_eos, "logging", None, None)
config_eos.track_nested_value("/logging", track_logging_config)


# ----------------------------
# Safe argparse at import time
# ----------------------------

parser = argparse.ArgumentParser(description="Start EOSdash server.")

parser.add_argument(
    "--host",
    type=str,
    help="Host for the EOSdash server (default: value from config)",
)
parser.add_argument(
    "--port",
    type=int,
    help="Port for the EOSdash server (default: value from config)",
)
parser.add_argument(
    "--eos-host",
    type=str,
    help="Host of the EOS server (default: value from config)",
)
parser.add_argument(
    "--eos-port",
    type=int,
    help="Port of the EOS server (default: value from config)",
)
parser.add_argument(
    "--log_level",
    type=str,
    default="INFO",
    help='Log level for the server. Options: "critical", "error", "warning", "info", "debug", "trace" (default: "INFO")',
)
parser.add_argument(
    "--access_log",
    type=str2bool,
    default=False,
    help="Enable or disable access logging. Options: True or False (default: False)",
)
parser.add_argument(
    "--reload",
    type=str2bool,
    default=False,
    help="Enable or disable auto-reload. Useful for development. Options: True or False (default: False)",
)

# Command line arguments
args: argparse.Namespace
args_unknown: list[str]
args, args_unknown = parser.parse_known_args()


# -----------------------------
# Prepare config at import time
# -----------------------------

# Set EOS config to actual environment variable & config file content
config_eos.reset_settings()

# Setup parameters from args, config_eos and default
# Remember parameters in config
config_eosdash = {}

# Setup EOS logging level - first to have the other logging messages logged
# - log level
if args and args.log_level is not None:
    config_eosdash["log_level"] = args.log_level.upper()
else:
    config_eosdash["log_level"] = "info"
# Ensure log_level from command line is in config settings
if config_eosdash["log_level"] in LOGGING_LEVELS:
    # Setup console logging level using nested value
    # - triggers logging configuration by track_logging_config
    config_eos.set_nested_value("logging/console_level", config_eosdash["log_level"])
    logger.debug(
        f"logging/console_level configuration set by argument to {config_eosdash['log_level']}"
    )

# Setup EOS server host
if args and args.eos_host:
    config_eosdash["eos_host"] = args.eos_host
elif config_eos.server.host:
    config_eosdash["eos_host"] = str(config_eos.server.host)
else:
    config_eosdash["eos_host"] = get_default_host()

# Setup EOS server port
if args and args.eos_port:
    config_eosdash["eos_port"] = args.eos_port
elif config_eos.server.port:
    config_eosdash["eos_port"] = config_eos.server.port
else:
    config_eosdash["eos_port"] = 8503

# - EOSdash host
if args and args.host:
    config_eosdash["eosdash_host"] = args.host
elif config_eos.server.eosdash_host:
    config_eosdash["eosdash_host"] = str(config_eos.server.eosdash_host)
else:
    config_eosdash["eosdash_host"] = get_default_host()

# - EOS port
if args and args.port:
    config_eosdash["eosdash_port"] = args.port
elif config_eos.server.eosdash_port:
    config_eosdash["eosdash_port"] = config_eos.server.eosdash_port
else:
    config_eosdash["eosdash_port"] = 8504

# - access log
if args and args.access_log:
    config_eosdash["access_log"] = args.access_log
else:
    config_eosdash["access_log"] = False

# - reload
if args is None and args.reload is None:
    config_eosdash["reload"] = False
else:
    config_eosdash["reload"] = args.reload


# ---------------------
# Prepare FastHTML app
# ---------------------

# The favicon for EOSdash
favicon_filepath = Path(__file__).parent.joinpath("dash/assets/favicon/favicon.ico")
if not favicon_filepath.exists():
    raise ValueError(f"Does not exist {favicon_filepath}")


# Add Bokeh headers
# Get frankenui and tailwind headers via CDN using Theme.green.headers()
hdrs = (
    *BokehJS,
    Theme.green.headers(highlightjs=True),
)

# The EOSdash application
app: FastHTML = FastHTML(
    title="EOSdash",
    hdrs=hdrs,
    secret_key=os.getenv("EOS_SERVER__EOSDASH_SESSKEY"),
)


def eos_server() -> tuple[str, int]:
    """Retrieves the EOS server host and port configuration.

    Takes values from `config_eos.server` or default.

    Returns:
        tuple[str, int]: A tuple containing:
            - `eos_host` (str): The EOS server hostname or IP.
            - `eos_port` (int): The EOS server port.
    """
    return config_eosdash["eos_host"], config_eosdash["eos_port"]


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
            "Plan": "/eosdash/plan",
            "Prediction": "/eosdash/prediction",
            "Config": "/eosdash/configuration",
            "Admin": "/eosdash/admin",
            "About": "/eosdash/about",
        },
        About(),
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


@app.get("/eosdash/about")
def get_eosdash_about():  # type: ignore
    """Serves the EOSdash About page.

    Returns:
        About: The About page component.
    """
    return About()


@app.get("/eosdash/admin")
def get_eosdash_admin():  # type: ignore
    """Serves the EOSdash Admin page.

    Returns:
        Admin: The Admin page component.
    """
    return Admin(*eos_server())


@app.post("/eosdash/admin")
def post_eosdash_admin(data: dict):  # type: ignore
    """Provide control data to the Admin page.

    This endpoint is called from within the Admin page on user actions.

    Returns:
        Admin: The Admin page component.
    """
    return Admin(*eos_server(), data)


@app.get("/eosdash/configuration")
def get_eosdash_configuration():  # type: ignore
    """Serves the EOSdash Configuration page.

    Returns:
        Configuration: The Configuration page component.
    """
    return Configuration(*eos_server())


@app.put("/eosdash/configuration")
def put_eosdash_configuration(data: dict):  # type: ignore
    return ConfigKeyUpdate(*eos_server(), data["key"], data["value"])


@app.get("/eosdash/plan")
def get_eosdash_plan(data: dict):  # type: ignore
    """Serves the EOSdash Plan page.

    Returns:
        Plan: The Plan page component.
    """
    return Plan(*eos_server(), data)


@app.post("/eosdash/plan")
def post_eosdash_plan(data: dict):  # type: ignore
    """Provide control data to the Plan page.

    This endpoint is called from within the Plan page on user actions.

    Returns:
        Plan: The Plan page component.
    """
    return Plan(*eos_server(), data)


@app.get("/eosdash/prediction")
def get_eosdash_prediction(data: dict):  # type: ignore
    """Serves the EOSdash Prediction page.

    Returns:
        Prediction: The Prediction page component.
    """
    return Prediction(*eos_server(), data)


@app.get("/eosdash/health")
def get_eosdash_health():  # type: ignore
    """Health check endpoint to verify that the EOSdash server is alive."""
    return JSONResponse(
        {
            "status": "alive",
            "pid": psutil.Process().pid,
            "version": __version__,
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
    # Wait for EOSdash port to be free - e.g. in case of restart
    wait_for_port_free(config_eosdash["eosdash_port"], timeout=120, waiting_app_name="EOSdash")

    try:
        uvicorn.run(
            "akkudoktoreos.server.eosdash:app",
            host=config_eosdash["eosdash_host"],
            port=config_eosdash["eosdash_port"],
            log_level=config_eosdash["log_level"].lower(),
            access_log=config_eosdash["access_log"],
            reload=config_eosdash["reload"],
        )
    except Exception as e:
        logger.error(
            f"Could not bind to host {config_eosdash['eosdash_host']}:{config_eosdash['eosdash_port']}. Error: {e}"
        )
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
    try:
        run_eosdash()
    except Exception as ex:
        error_msg = f"Failed to run EOSdash: {ex}"
        logger.error(error_msg)
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
