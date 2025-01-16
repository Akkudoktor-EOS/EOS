#!.venv/bin/python
"""Utility functions for Configuration specification generation."""

import argparse
import sys

from akkudoktoreos.config.config import get_config
from akkudoktoreos.core.logging import get_logger

logger = get_logger(__name__)

config_eos = get_config()

# Fixed set of prefixes to filter configuration values and their respective titles
CONFIG_PREFIXES = {
    "battery": "Battery Device Simulation Configuration",
    "ev": "Battery Electric Vehicle Device Simulation Configuration",
    "dishwasher": "Dishwasher Device Simulation Configuration",
    "inverter": "Inverter Device Simulation Configuration",
    "measurement": "Measurement Configuration",
    "optimization": "General Optimization Configuration",
    "server": "Server Configuration",
    "elecprice": "Electricity Price Prediction Configuration",
    "load": "Load Prediction Configuration",
    "logging": "Logging Configuration",
    "prediction": "General Prediction Configuration",
    "pvforecast": "PV Forecast Configuration",
    "weather": "Weather Forecast Configuration",
}

# Static set of configuration names to include in a separate table
GENERAL_CONFIGS = [
    "config_default_file_path",
    "config_file_path",
    "config_folder_path",
    "config_keys",
    "config_keys_read_only",
    "data_cache_path",
    "data_cache_subpath",
    "data_folder_path",
    "data_output_path",
    "data_output_subpath",
    "latitude",
    "longitude",
    "package_root_path",
    "timezone",
]


def generate_config_table_md(configs, title):
    """Generate a markdown table for given configurations.

    Args:
        configs (dict): Configuration values with keys and their descriptions.
        title (str): Title for the table.

    Returns:
        str: The markdown table as a string.
    """
    if not configs:
        return ""

    table = f"## {title}\n\n"
    table += ":::{table} " + f"{title}\n:widths: 10 10 5 5 30\n:align: left\n\n"
    table += "| Name | Type | Read-Only | Default | Description |\n"
    table += "| ---- | ---- | --------- | ------- | ----------- |\n"
    for name, config in sorted(configs.items()):
        type_name = config["type"]
        if type_name.startswith("typing."):
            type_name = type_name[len("typing.") :]
        table += f"| `{config['name']}` | `{type_name}` | `{config['read-only']}` | `{config['default']}` | {config['description']} |\n"
    table += ":::\n\n"  # Add an empty line after the table
    return table


def generate_config_md() -> str:
    """Generate configuration specification in Markdown with extra tables for prefixed values.

    Returns:
        str: The Markdown representation of the configuration spec.
    """
    configs = {}
    config_keys = config_eos.config_keys
    config_keys_read_only = config_eos.config_keys_read_only
    for config_key in config_keys:
        config = {}
        config["name"] = config_key
        config["value"] = getattr(config_eos, config_key)

        if config_key in config_keys_read_only:
            config["read-only"] = "ro"
            computed_field_info = config_eos.__pydantic_decorators__.computed_fields[
                config_key
            ].info
            config["default"] = "N/A"
            config["description"] = computed_field_info.description
            config["type"] = str(computed_field_info.return_type)
        else:
            config["read-only"] = "rw"
            field_info = config_eos.model_fields[config_key]
            config["default"] = field_info.default
            config["description"] = field_info.description
            config["type"] = str(field_info.annotation)

        configs[config_key] = config

    # Generate markdown for the main table
    markdown = "# Configuration Table\n\n"

    # Generate table for general configuration names
    general_configs = {k: v for k, v in configs.items() if k in GENERAL_CONFIGS}
    for k in general_configs.keys():
        del configs[k]  # Remove general configs from the main configs dictionary
    markdown += generate_config_table_md(general_configs, "General Configuration Values")

    non_prefixed_configs = {k: v for k, v in configs.items()}

    # Generate tables for each prefix (sorted by value) and remove prefixed configs from the main dictionary
    sorted_prefixes = sorted(CONFIG_PREFIXES.items(), key=lambda item: item[1])
    for prefix, title in sorted_prefixes:
        prefixed_configs = {k: v for k, v in configs.items() if k.startswith(prefix)}
        for k in prefixed_configs.keys():
            del non_prefixed_configs[k]
        markdown += generate_config_table_md(prefixed_configs, title)

    # Generate markdown for the remaining non-prefixed configs if any
    if non_prefixed_configs:
        markdown += generate_config_table_md(non_prefixed_configs, "Other Configuration Values")

    # Assure the is no double \n at end of file
    markdown = markdown.rstrip("\n")
    markdown += "\n"

    return markdown


def main():
    """Main function to run the generation of the Configuration specification as Markdown."""
    parser = argparse.ArgumentParser(description="Generate Configuration Specification as Markdown")
    parser.add_argument(
        "--output-file",
        type=str,
        default=None,
        help="File to write the Configuration Specification to",
    )

    args = parser.parse_args()

    try:
        config_md = generate_config_md()
        if args.output_file:
            # Write to file
            with open(args.output_file, "w", encoding="utf8") as f:
                f.write(config_md)
        else:
            # Write to std output
            print(config_md)

    except Exception as e:
        print(f"Error during Configuration Specification generation: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
