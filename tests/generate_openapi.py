import json
from pathlib import Path

from fastapi.openapi.utils import get_openapi

from akkudoktoreosserver.fastapi_server import app


def generate_openapi(filename: str | Path = "openapi.json"):
    with open(filename, "w") as f:
        json.dump(
            get_openapi(
                title=app.title,
                version=app.version,
                openapi_version=app.openapi_version,
                description=app.description,
                routes=app.routes,
            ),
            f,
        )


if __name__ == "__main__":
    generate_openapi()
