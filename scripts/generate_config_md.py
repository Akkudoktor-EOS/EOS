#!.venv/bin/python
"""Utility functions for Configuration specification generation."""

import argparse
import json
import sys
import textwrap
from typing import Any, Union

from pydantic.fields import ComputedFieldInfo, FieldInfo
from pydantic_core import PydanticUndefined

from akkudoktoreos.config.config import get_config
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.core.pydantic import PydanticBaseModel

logger = get_logger(__name__)

config_eos = get_config()


documented_types: set[PydanticBaseModel] = set()
undocumented_types: dict[PydanticBaseModel, tuple[str, list[str]]] = dict()


def get_title(config: PydanticBaseModel) -> str:
    if config.__doc__ is None:
        raise NameError(f"Missing docstring: {config}")
    return config.__doc__.strip().splitlines()[0].strip(".")


def get_body(config: PydanticBaseModel) -> str:
    if config.__doc__ is None:
        raise NameError(f"Missing docstring: {config}")
    return textwrap.dedent("\n".join(config.__doc__.strip().splitlines()[1:])).strip()


def resolve_nested_types(field_type: Any, parent_types: list[str]) -> list[tuple[Any, list[str]]]:
    resolved_types: list[tuple[type, list[str]]] = []

    origin = getattr(field_type, "__origin__", field_type)
    if origin is Union:
        for arg in getattr(field_type, "__args__", []):
            resolved_types.extend(resolve_nested_types(arg, parent_types))
    elif origin is list:
        for arg in getattr(field_type, "__args__", []):
            resolved_types.extend(resolve_nested_types(arg, parent_types + ["list"]))
    else:
        resolved_types.append((field_type, parent_types))

    return resolved_types


def get_example_or_default(field_name: str, field_info: FieldInfo) -> dict[str, Any]:
    """Generate a default value for a field, considering constraints."""
    if field_info.examples is not None:
        return field_info.examples[0]

    if field_info.default is not None:
        return field_info.default

    raise NotImplementedError(f"No default or example provided '{field_name}': {field_info}")


def create_model_from_examples(model_class: PydanticBaseModel) -> PydanticBaseModel:
    """Create a model instance with default or example values, respecting constraints."""
    example_data = {}
    for field_name, field_info in model_class.model_fields.items():
        example_data[field_name] = get_example_or_default(field_name, field_info)
    return model_class(**example_data)


def build_nested_structure(keys: list[str], value: Any) -> Any:
    if not keys:
        return value

    current_key = keys[0]
    if current_key == "list":
        return [build_nested_structure(keys[1:], value)]
    else:
        return {current_key: build_nested_structure(keys[1:], value)}


def get_default_value(field_info: Union[FieldInfo, ComputedFieldInfo], regular_field: bool) -> Any:
    default_value = ""
    if regular_field:
        if (val := field_info.default) is not PydanticUndefined:
            default_value = val
        else:
            default_value = "required"
    else:
        default_value = "N/A"
    return default_value


def get_type_name(field_type: type) -> str:
    type_name = str(field_type).replace("typing.", "")
    if type_name.startswith("<class"):
        type_name = field_type.__name__
    return type_name


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
        default_value = get_default_value(field_info, regular_field)
        description = field_info.description if field_info.description else "-"
        read_only = "rw" if regular_field else "ro"
        type_name = get_type_name(field_type)

        env_entry = ""
        if not extra_config:
            if regular_field:
                env_entry = f"| `{prefix}{config_name}` "
            else:
                env_entry = "| "
        table += f"| {field_name} {env_entry}| `{type_name}` | `{read_only}` | `{default_value}` | {description} |\n"

        inner_types: dict[PydanticBaseModel, tuple[str, list[str]]] = dict()

        def extract_nested_models(subtype: Any, subprefix: str, parent_types: list[str]):
            if subtype in inner_types.keys():
                return
            nested_types = resolve_nested_types(subtype, [])
            for nested_type, nested_parent_types in nested_types:
                if issubclass(nested_type, PydanticBaseModel):
                    new_parent_types = parent_types + nested_parent_types
                    if "list" in parent_types:
                        new_prefix = ""
                    else:
                        new_prefix = f"{subprefix}"
                    inner_types.setdefault(nested_type, (new_prefix, new_parent_types))
                    for nested_field_name, nested_field_info in list(
                        nested_type.model_fields.items()
                    ) + list(nested_type.model_computed_fields.items()):
                        nested_field_type = nested_field_info.annotation
                        if new_prefix:
                            new_prefix += f"{nested_field_name.upper()}__"
                        extract_nested_models(
                            nested_field_type,
                            new_prefix,
                            new_parent_types + [nested_field_name],
                        )

        extract_nested_models(field_type, f"{prefix}{config_name}__", toplevel_keys + [field_name])

        for new_type, info in inner_types.items():
            if new_type not in documented_types:
                undocumented_types.setdefault(new_type, (info[0], info[1]))

    if toplevel:
        table += ":::\n\n"  # Add an empty line after the table

        ins = create_model_from_examples(config)
        if ins:
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
            input_dict = build_nested_structure(toplevel_keys, ins_dict)
            table += textwrap.indent(json.dumps(input_dict, indent=4), "   ")
            table += "\n"
            table += "```\n\n"

            if not same_output:
                table += f"#{heading_level} Example Output\n\n"
                table += "```{eval-rst}\n"
                table += ".. code-block:: json\n\n"
                output_dict = build_nested_structure(toplevel_keys, ins_out_dict)
                table += textwrap.indent(json.dumps(output_dict, indent=4), "   ")
                table += "\n"
                table += "```\n\n"

        while undocumented_types:
            extra_config_type, extra_info = undocumented_types.popitem()
            documented_types.add(extra_config_type)
            table += generate_config_table_md(
                extra_config_type, extra_info[1], extra_info[0], True, True
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
