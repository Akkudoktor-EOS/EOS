#!.venv/bin/python
"""Utility functions for Configuration specification generation."""

import argparse
import json
import sys
import textwrap
from typing import Any, Union

from pydantic.fields import FieldInfo

from akkudoktoreos.config.config import get_config
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.core.pydantic import PydanticBaseModel

logger = get_logger(__name__)

config_eos = get_config()


documented_types: set[PydanticBaseModel] = set()
undocumented_types: dict[PydanticBaseModel, list[str]] = dict()


def get_title(config: PydanticBaseModel) -> str:
    if config.__doc__ is None:
        raise NameError(f"Missing docstring: {config}")
    return config.__doc__.strip().splitlines()[0].strip(".")


def get_body(config: PydanticBaseModel) -> str:
    if config.__doc__ is None:
        raise NameError(f"Missing docstring: {config}")
    return textwrap.dedent("\n".join(config.__doc__.strip().splitlines()[1:])).strip()


def get_all_inner_types(nested_type: Any) -> list[Any]:
    if hasattr(nested_type, "__args__") and nested_type.__args__:
        inner_types = []
        for t in nested_type.__args__:
            inner_types.extend(get_all_inner_types(t))
        return inner_types
    return [nested_type]


def is_optional_base_model(field_type: Any) -> bool:
    if field_type is None:
        return False

    if isinstance(field_type, PydanticBaseModel):
        return True

    if hasattr(field_type, "__origin__") and field_type.__origin__ is Union:
        for field_subtype in field_type.__args__:
            if isinstance(field_subtype, PydanticBaseModel):
                return True
    return False


def get_constrained_default(field_name: str, field_info: FieldInfo) -> dict[str, Any]:
    """Generate a default value for a field, considering constraints."""
    if field_info.examples is not None:
        return field_info.examples[0]

    if field_info.default is not None:
        return field_info.default

    raise NotImplementedError(f"No default or example provided '{field_name}': {field_info}")


def create_model_with_constraints(model_class: PydanticBaseModel) -> PydanticBaseModel:
    """Create a model instance with default or example values, respecting constraints."""
    example_data = {}
    for field_name, field_info in model_class.model_fields.items():
        example_data[field_name] = get_constrained_default(field_name, field_info)
    return model_class(**example_data)


def build_nested_dict(toplevel_keys: list[str], value: dict[str, Any]) -> dict[str, Any]:
    nested_dict: dict[str, Any] = dict()

    current_dict = nested_dict
    for key in toplevel_keys[:-1]:  # Iterate until the second-to-last key
        current_dict[key] = {}
        current_dict = current_dict[key]

    # Assign the final dictionary as the value at the last key
    current_dict[toplevel_keys[-1]] = value

    return nested_dict


def generate_config_table_md(
    config: PydanticBaseModel,
    toplevel_keys: list[str],
    prefix: str,
    toplevel: bool = False,
    extra_config: bool = False,
) -> str:
    """Generate a markdown table for given configurations.

    Args:
        config (PydanticBaseModel): PydanticBaseModel configuration definition.
        prefix (str): Prefix for table entries.

    Returns:
        str: The markdown table as a string.
    """
    table = ""
    if toplevel:
        title = get_title(config)

        heading_level = "###" if extra_config else "##"
        env_header = ""
        env_header_underline = ""
        env_width = ""
        if not extra_config:
            env_header = "| Environment Variable "
            env_header_underline = "| -------------------- "
            env_width = "20 "

        table += f"{heading_level} {title}\n\n"

        body = get_body(config)
        if body:
            table += body
            table += "\n\n"

        table += (
            ":::{table} "
            + f"{'::'.join(toplevel_keys)}\n:widths: 10 {env_width}10 5 5 30\n:align: left\n\n"
        )
        table += f"| Name {env_header}| Type | Read-Only | Default | Description |\n"
        table += f"| ---- {env_header_underline}| ---- | --------- | ------- | ----------- |\n"

    for field_name, field_info in list(config.model_fields.items()) + list(
        config.model_computed_fields.items()
    ):
        regular_field = isinstance(field_info, FieldInfo)

        config_name = field_name if extra_config else field_name.upper()
        field_type = field_info.annotation if regular_field else field_info.return_type
        default_value = field_info.default if regular_field else "N/A"
        description = field_info.description if field_info.description else "-"

        read_only = "rw" if regular_field else "ro"
        type_name = str(field_type)
        if type_name.startswith("typing."):
            type_name = type_name[len("typing.") :]
        elif type_name.startswith("<class"):
            type_name = field_type.__name__

        env_entry = ""
        if not extra_config:
            if regular_field:
                env_entry = f"| `{prefix}{config_name}` "
            else:
                env_entry = "| "
        table += f"| {field_name} {env_entry}| `{type_name}` | `{read_only}` | `{default_value}` | {description} |\n"

        # Nested PydanticBaseModel
        if isinstance(field_type, PydanticBaseModel):
            table += generate_config_table_md(
                field_type,
                toplevel_keys + [field_name],
                f"{prefix}{config_name}__",
                False,
                extra_config,
            )

        # Nested Optional/Union PydanticBaseModel
        elif hasattr(field_type, "__origin__") and field_type.__origin__ is Union:
            for field_subtype in field_type.__args__:
                # Skip further nested/composed fields like dict/list etc.
                if not hasattr(field_subtype, "__origin__") and issubclass(
                    field_subtype, PydanticBaseModel
                ):
                    table += generate_config_table_md(
                        field_subtype,
                        toplevel_keys + [field_name],
                        f"{prefix}{config_name}__",
                        False,
                        extra_config,
                    )

        inner_types = {
            config_type
            for config_type in get_all_inner_types(field_type)
            if issubclass(config_type, PydanticBaseModel)
        }
        for new_type in inner_types - documented_types:
            undocumented_types.setdefault(new_type, toplevel_keys + [field_name])

    if toplevel:
        table += ":::\n\n"  # Add an empty line after the table

        if not extra_config or True:
            ins = create_model_with_constraints(config)
            if ins is not None:
                # Transform to JSON (and manually to dict) to use custom serializers and then merge with parent keys
                ins_json = ins.model_dump_json(include_computed_fields=False)
                ins_dict = json.loads(ins_json)

                ins_out_json = ins.model_dump_json(include_computed_fields=True)
                ins_out_dict = json.loads(ins_out_json)
                same_output = ins_out_dict == ins_dict
                same_output_str = "/Output" if same_output else ""

                table += f"#{heading_level} Example Input{same_output_str}\n\n"
                table += "```{eval-rst}\n"
                table += ".. code-block:: json\n\n"
                input_dict = build_nested_dict(toplevel_keys, ins_dict)
                table += textwrap.indent(json.dumps(input_dict, indent=4), "   ")
                table += "\n"
                table += "```\n\n"

                if not same_output:
                    table += f"#{heading_level} Example Output\n\n"
                    table += "```{eval-rst}\n"
                    table += ".. code-block:: json\n\n"
                    output_dict = build_nested_dict(toplevel_keys, ins_out_dict)
                    table += textwrap.indent(json.dumps(output_dict, indent=4), "   ")
                    table += "\n"
                    table += "```\n\n"

        while not extra_config and undocumented_types:
            extra_config_type, extra_toplevel_keys = undocumented_types.popitem()
            documented_types.add(extra_config_type)
            table += generate_config_table_md(
                extra_config_type, extra_toplevel_keys, "", True, True
            )

    return table


def generate_config_md() -> str:
    """Generate configuration specification in Markdown with extra tables for prefixed values.

    Returns:
        str: The Markdown representation of the configuration spec.
    """
    markdown = "# Configuration Table\n\n"

    # Generate tables for each top level config
    for field_name, field_info in config_eos.model_fields.items():
        field_type = field_info.annotation
        markdown += generate_config_table_md(
            field_type, [field_name], f"EOS_{field_name.upper()}__", True
        )

    # Assure there is no double \n at end of file
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
