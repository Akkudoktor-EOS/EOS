import argparse
import os
import sys
import traceback
from pathlib import Path

import psutil
import uvicorn
from fasthtml.common import Base, FileResponse, JSONResponse
from loguru import logger
from monsterui.core import FastHTML, Theme
from starlette.middleware import Middleware
from starlette.requests import Request

from akkudoktoreos.config.config import get_config
from akkudoktoreos.core.logabc import LOGGING_LEVELS
from akkudoktoreos.core.logging import logging_track_config
from akkudoktoreos.core.version import __version__

# Pages
from akkudoktoreos.server.dash.about import About
from akkudoktoreos.server.dash.admin import Admin

# helpers
from akkudoktoreos.server.dash.bokeh import BokehJS
from akkudoktoreos.server.dash.components import Page
from akkudoktoreos.server.dash.configuration import Configuration
from akkudoktoreos.server.dash.context import (
    IngressMiddleware,
    safe_asset_path,
)
from akkudoktoreos.server.dash.footer import Footer
from akkudoktoreos.server.dash.plan import Plan
from akkudoktoreos.server.dash.prediction import Prediction
from akkudoktoreos.server.server import (
    drop_root_privileges,
    get_default_host,
    wait_for_port_free,
)
from akkudoktoreos.utils.stringutil import str2bool

config_eos = get_config()


# ------------------------------------
# Logging configuration at import time
# ------------------------------------

logger.remove()
logging_track_config(config_eos, "logging", None, None)
config_eos.track_nested_value("/logging", logging_track_config)


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
parser.add_argument(
    "--run_as_user",
    type=str,
    help="The unprivileged user account the EOSdash server shall run if started in root-level.",
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
    # - triggers logging configuration by logging_track_config
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
    title="EOSdash",  # Default page title
    hdrs=hdrs,  # Additional FT elements to add to <HEAD>
    # htmx=True,  # Include HTMX header?
    middleware=[Middleware(IngressMiddleware)],
    secret_key=os.getenv("EOS_SERVER__EOSDASH_SESSKEY"),  # Signing key for sessions
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


# -------------------------------------------------------------------
# Routes
# -------------------------------------------------------------------


@app.get("/favicon.ico")
def get_eosdash_favicon(request: Request):  # type: ignore
    """Get the EOSdash favicon.

    Args:
        request (Request): The incoming FastHTML request.

    Returns:
        FileResponse: The favicon file.
    """
    return FileResponse(path=favicon_filepath)


@app.get("/")
def get_eosdash(request: Request):  # type: ignore
    """Serve the main EOSdash page with navigation links.

    Args:
        request (Request): The incoming FastHTML request.

    Returns:
        Page: The main dashboard page with navigation links and footer.
    """
    root_path: str = request.scope.get("root_path", "")

    return (
        Base(href=f"{root_path}/") if root_path else None,
        Page(
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
        ),
    )


@app.get("/eosdash/footer")
def get_eosdash_footer(request: Request):  # type: ignore
    """Serve the EOSdash Footer information.

    Args:
        request (Request): The incoming FastHTML request.

    Returns:
        Footer: The Footer component.
    """
    return Footer(*eos_server())


@app.get("/eosdash/about")
def get_eosdash_about(request: Request):  # type: ignore
    """Serve the EOSdash About page.

    Args:
        request (Request): The incoming FastHTML request.

    Returns:
        About: The About page component.
    """
    return About()


@app.get("/eosdash/admin")
def get_eosdash_admin(request: Request):  # type: ignore
    """Serve the EOSdash Admin page.

    Args:
        request (Request): The incoming FastHTML request.

    Returns:
        Admin: The Admin page component.
    """
    return Admin(*eos_server())


@app.post("/eosdash/admin")
def post_eosdash_admin(request: Request, data: dict):  # type: ignore
    """Provide control data to the Admin page.

    Args:
        request (Request): The incoming FastHTML request.
        data (dict): User-submitted data from the Admin page.

    Returns:
        Admin: The Admin page component.
    """
    return Admin(*eos_server(), data)


@app.get("/eosdash/configuration")
def get_eosdash_configuration(request: Request):  # type: ignore
    """Serve the EOSdash Configuration page.

    Args:
        request (Request): The incoming FastHTML request.

    Returns:
        Configuration: The Configuration page component.
    """
    return Configuration(*eos_server())


@app.put("/eosdash/configuration")
def put_eosdash_configuration(request: Request, data: dict):  # type: ignore
    """Update a configuration key/value pair.

    Args:
        request (Request): The incoming FastHTML request.
        data (dict): Dictionary containing 'key' and 'value' to trigger configuration update.

    Returns:
        Configuration: The Configuration page component with updated configuration.
    """
    return Configuration(*eos_server(), data)


@app.post("/eosdash/configuration")
def post_eosdash_configuration(request: Request, data: dict):  # type: ignore
    """Provide control data to the configuration page.

    Args:
        request (Request): The incoming FastHTML request.
        data (dict): User-submitted data from the configuration page.

    Returns:
        Configuration: The Configuration page component with updated configuration.
    """
    return Configuration(*eos_server(), data)


@app.get("/eosdash/plan")
def get_eosdash_plan(request: Request, data: dict):  # type: ignore
    """Serve the EOSdash Plan page.

    Args:
        request (Request): The incoming FastHTML request.
        data (dict): Optional query data.

    Returns:
        Plan: The Plan page component.
    """
    return Plan(*eos_server(), data)


@app.post("/eosdash/plan")
def post_eosdash_plan(request: Request, data: dict):  # type: ignore
    """Provide control data to the Plan page.

    Args:
        request (Request): The incoming FastHTML request.
        data (dict): User-submitted data from the Plan page.

    Returns:
        Plan: The Plan page component.
    """
    return Plan(*eos_server(), data)


@app.get("/eosdash/prediction")
def get_eosdash_prediction(request: Request, data: dict):  # type: ignore
    """Serve the EOSdash Prediction page.

    Args:
        request (Request): The incoming FastHTML request.
        data (dict): Optional query data.

    Returns:
        Prediction: The Prediction page component.
    """
    return Prediction(*eos_server(), data)


@app.get("/eosdash/health")
def get_eosdash_health(request: Request):  # type: ignore
    """Health check endpoint to verify the EOSdash server is alive.

    Args:
        request (Request): The incoming FastHTML request.

    Returns:
        JSONResponse: Server status including PID and version.
    """
    return JSONResponse(
        {
            "status": "alive",
            "pid": psutil.Process().pid,
            "version": __version__,
        }
    )


@app.get("/eosdash/assets/{filepath:path}")
def get_eosdash_assets(request: Request, filepath: str):  # type: ignore
    """Serve static assets for EOSdash safely.

    Args:
        request (Request): The incoming FastHTML request.
        filepath (str): Relative path of the asset under dash/assets/.

    Returns:
        FileResponse: The requested asset file if it exists.

    Raises:
        404: If the file does not exist.
        403: If the file path is forbidden (directory traversal attempt).
    """
    try:
        asset_filepath = safe_asset_path(filepath)
    except ValueError:
        return {"error": "Forbidden"}, 403

    if not asset_filepath.exists() or not asset_filepath.is_file():
        return {"error": "File not found"}, 404

    return FileResponse(path=asset_filepath)


# ----------------------
# Run the EOSdash server
# ----------------------


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
    if args:
        run_as_user = args.run_as_user
    else:
        run_as_user = None

    # Drop root privileges if running as root
    drop_root_privileges(run_as_user=run_as_user)

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
            proxy_headers=True,
            forwarded_allow_ips="*",
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
