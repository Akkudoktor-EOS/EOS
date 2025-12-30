import os
from pathlib import Path
from typing import Awaitable, Callable, Optional

from loguru import logger
from platformdirs import user_config_dir
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Home assistant token, if running under Home Assistant
HASSIO_TOKEN = os.environ.get("HASSIO_TOKEN")

# Compute global root path at startup
# Will be replaced on first request if Ingress is active
ROOT_PATH = "/"

# EOSdash path prefix
EOSDASH_ROOT = "eosdash/"

# Directory to export files to, or to import files from
export_import_directory = (
    Path(os.environ.get("EOS_DATA_DIR", user_config_dir("net.akkudoktor.eosdash", "akkudoktor")))
    if not HASSIO_TOKEN
    else Path("/data")
)


class IngressMiddleware(BaseHTTPMiddleware):
    """Middleware to handle Home Assistant Ingress path prefixes.

    This middleware enables FastHTML applications to work seamlessly both with
    and without Home Assistant Ingress. When deployed as a Home Assistant add-on
    with Ingress enabled, it automatically handles the path prefix routing.

    Home Assistant Ingress proxies add-on traffic through paths like
    `/api/hassio_ingress/<token>/`, which requires setting the application's
    root_path for correct URL generation. This middleware detects the Ingress
    path from the X-Ingress-Path header and configures the request scope
    accordingly.

    When running standalone (development or direct access), the middleware
    passes requests through unchanged, allowing normal operation.

    Attributes:
        None

    Examples:
        >>> from fasthtml.common import FastHTML
        >>> from starlette.middleware import Middleware
        >>>
        >>> app = FastHTML(middleware=[Middleware(IngressMiddleware)])
        >>>
        >>> @app.get("/")
        >>> def home():
        ...     return "Hello World"

    Notes:
        - All htmx and route URLs should use relative paths (e.g., "/api/data")
        - The middleware automatically adapts to both Ingress and direct access
        - No code changes needed when switching between deployment modes
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Process the request and set root_path if running under Ingress.

        Args:
            request: The incoming Starlette Request object.
            call_next: Callable to invoke the next middleware or route handler.

        Returns:
            Response: The response from the application after processing.

        Note:
            The X-Ingress-Path header is automatically added by Home Assistant
            when proxying requests through Ingress.
        """
        global ROOT_PATH

        # Home Assistant passes the ingress path in this header
        # Try multiple header variations (case-insensitive)
        ingress_path = (
            request.headers.get("X-Ingress-Path", "")
            or request.headers.get("x-ingress-path", "")
            or request.headers.get("X-INGRESS-PATH", "")
        )

        # Debug logging - remove after testing
        logger.debug(f"All headers: {dict(request.headers)}")
        logger.debug(f"Ingress path: {ingress_path}")
        logger.debug(f"Request path: {request.url.path}")

        # Only set root_path if we have an ingress path
        if ingress_path:
            ROOT_PATH = ingress_path
            request.scope["root_path"] = ingress_path
        # Otherwise, root_path remains empty (normal operation)

        response = await call_next(request)

        return response


# Helper functions
def request_url_for(path: str, root_path: Optional[str] = None) -> str:
    """Generate a full URL including the root_path.

    Args:
        path: Relative path **inside the app** (e.g., "eosdash/footer" or "eosdash/assets/logo.png").
        root_path: Root path.

    Returns:
        str: Absolute URL including the root_path.
    """
    global ROOT_PATH, EOSDASH_ROOT

    # Step 1: fallback to global root
    if root_path is None:
        root_path = ROOT_PATH

    # Normalize root path
    root_path = root_path.rstrip("/") + "/"

    # Normalize path
    if path.startswith(root_path):
        # Strip root_path prefix
        path = path[len(root_path) :]

    # Remove leading / if any
    path = path.lstrip("/")

    # Strip EOSDASH_ROOT if present
    if path.startswith(EOSDASH_ROOT):
        path = path[len(EOSDASH_ROOT) :]

    # Build final URL
    result = root_path + EOSDASH_ROOT + path.lstrip("/")

    # Normalize accidental double slashes (except leading)
    while "//" in result[1:]:
        result = result.replace("//", "/")

    logger.debug(f"URL for path '{path}' with root path '{root_path}': '{result}'")

    return result


def safe_asset_path(filepath: str) -> Path:
    """Return a safe filesystem path for an asset under dash/assets/.

    This prevents directory traversal attacks by restricting paths to
    the assets folder.

    Args:
        filepath (str): Relative asset path requested by the client.

    Returns:
        Path: Absolute Path object pointing to the asset file.

    Raises:
        ValueError: If the filepath attempts to traverse directories using '../'.
    """
    if ".." in filepath or filepath.startswith("/"):
        raise ValueError(f"Forbidden file path: {filepath}")

    asset_path = Path(__file__).parent / "dash/assets" / filepath
    return asset_path
