#!.venv/bin/python
"""This module generates the OpenAPI specification for the EOS application  defined in `akkudoktoreos.server.eos`.

The script can be executed directly to generate the OpenAPI specification
either to the standard output or to a specified file.

Usage:
    scripts/generate_openapi.py [--output-file OUTPUT_FILE]

Arguments:
    --output-file : Optional. The file path to write the OpenAPI specification to.

Example:
    scripts/generate_openapi.py --output-file openapi.json
"""

import argparse
import json
import sys

from fastapi.openapi.utils import get_openapi

from akkudoktoreos.server.eos import app


def generate_openapi() -> dict:
    """Generate the OpenAPI specification.

    Returns:
        openapi_spec (dict): OpenAPI specification.
    """
    openapi_spec = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        description=app.description,
        routes=app.routes,
    )

    return openapi_spec


def main():
    """Main function to run the generation of the OpenAPI specification."""
    parser = argparse.ArgumentParser(description="Generate OpenAPI Specification")
    parser.add_argument(
        "--output-file", type=str, default=None, help="File to write the OpenAPI Specification to"
    )

    args = parser.parse_args()

    try:
        openapi_spec = generate_openapi()
        openapi_spec_str = json.dumps(openapi_spec, indent=2)
        if args.output_file:
            # Write to file
            with open(args.output_file, "w", encoding="utf8") as f:
                f.write(openapi_spec_str)
        else:
            # Write to std output
            print(openapi_spec_str)

    except Exception as e:
        print(f"Error during OpenAPI specification generation: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
