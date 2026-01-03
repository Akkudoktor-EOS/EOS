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
import os
import sys

from akkudoktoreos.core.coreabc import get_config
from akkudoktoreos.server.eos import app


def generate_openapi() -> dict:
    # Make minimal config to make the generation reproducable
    config_eos = get_config(init={
            "with_init_settings": True,
            "with_env_settings": False,
            "with_dotenv_settings": False,
            "with_file_settings": False,
            "with_file_secret_settings": False,
        })

    openapi_spec = app.openapi()

    config_schema = (
        openapi_spec
        .get("components", {})
        .get("schemas", {})
        .get("ConfigEOS", {})
        .get("properties", {})
    )

    # ---- General settings ----
    general = config_schema.get("general", {}).get("default")
    if general:
        general.update({
            "config_file_path": "/home/user/.config/net.akkudoktoreos.net/EOS.config.json",
            "config_folder_path": "/home/user/.config/net.akkudoktoreos.net",
            "data_folder_path": "/home/user/.local/share/net.akkudoktoreos.net",
            "data_output_path": "/home/user/.local/share/net.akkudoktoreos.net/output",
        })

    # ---- Logging settings ----
    logging_cfg = config_schema.get("logging", {}).get("default")
    if logging_cfg:
        logging_cfg["file_path"] = "/home/user/.local/share/net.akkudoktoreos.net/output/eos.log"

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
            with open(args.output_file, "w", encoding="utf-8", newline="\n") as f:
                f.write(openapi_spec_str)
        else:
            # Write to std output
            print(openapi_spec_str)

    except Exception as e:
        print(f"Error during OpenAPI specification generation: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
