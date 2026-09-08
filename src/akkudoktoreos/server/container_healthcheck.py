"""Lightweight container healthcheck using the port selected by the running server."""

import os
import sys
import tempfile
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

PORT_FILE_ENV = "EOS_HEALTHCHECK_PORT_FILE"


def publish_port(port: int) -> None:
    """Atomically publish the startup port when container healthchecks are enabled.

    Called after configuration resolution and privilege dropping. This preserves
    CLI, environment and config-file precedence without loading EOS in the probe.
    """
    filename = os.environ.get(PORT_FILE_ENV)
    if not filename:
        return
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as output:
            temporary_path = Path(output.name)
            output.write(str(port))
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    """Return success only when the selected EOS port serves a healthy response."""
    try:
        port = int(Path(os.environ[PORT_FILE_ENV]).read_text(encoding="utf-8"))
        if not 1 <= port <= 65535:
            raise ValueError(f"Invalid server port: {port}")
        # The probe is always local and must not use HTTP_PROXY from the container.
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/v1/health", timeout=3) as response:
            return 0 if response.status == 200 else 1
    except (KeyError, OSError, ValueError) as error:
        print(f"EOS healthcheck failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
