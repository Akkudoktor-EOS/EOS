import json
from pathlib import Path

from fastapi.openapi.utils import get_openapi

from akkudoktoreos.server.fastapi_server import app


def generate_openapi(filename: str | Path = "openapi.json"):
    with open(filename, "w") as f:
        spec_dict = get_openapi(
            title=app.title,
            version=app.version,
            openapi_version=app.openapi_version,
            description=app.description,
            routes=app.routes,
        )
        # Filter proxy functions
        del spec_dict["paths"]["/{path}"]

        json.dump(spec_dict, f, indent=2)


if __name__ == "__main__":
    generate_openapi()
