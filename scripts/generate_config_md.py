#!.venv/bin/python
"""Utility functions for Configuration specification generation."""

import argparse
import json
import os
import re
import sys
import textwrap
from pathlib import Path
from typing import Any, Optional, Type, Union, get_args

from loguru import logger
from pydantic.fields import ComputedFieldInfo, FieldInfo
from pydantic_core import PydanticUndefined

from akkudoktoreos.config.config import ConfigEOS, default_data_folder_path
from akkudoktoreos.core.coreabc import get_config, singletons_init
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.utils.datetimeutil import to_datetime

documented_types: set[PydanticBaseModel] = set()
undocumented_types: dict[PydanticBaseModel, tuple[str, list[str]]] = dict()

global_config_dict: dict[str, Any] = dict()


def get_model_class_from_annotation(field_type: Any) -> type[PydanticBaseModel] | None:
    """Given a type annotation (possibly Optional or Union), return the first Pydantic model class."""
    origin = getattr(field_type, "__origin__", None)
    if origin is Union:
        # unwrap Union/Optional
        for arg in get_args(field_type):
            cls = get_model_class_from_annotation(arg)
            if cls is not None:
                return cls
        return None
    elif isinstance(field_type, type) and issubclass(field_type, PydanticBaseModel):
        return field_type
    else:
        return None


def get_title(config: type[PydanticBaseModel]) -> str:
    if config.__doc__ is None:
        raise NameError(f"Missing docstring: {config}")
    return config.__doc__.strip().splitlines()[0].strip(".")


def get_body(config: type[PydanticBaseModel]) -> str:
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


def get_example_or_default(field_name: str, field_info: FieldInfo, example_ix: int) -> Any:
    """Generate a default value for a field, considering constraints.

    Priority:
      1. field_info.examples
      2. field_info.example
      3. json_schema_extra['examples']
      4. json_schema_extra['example']
      5. field_info.default
    """
    # 1. Old-style examples attribute
    examples = getattr(field_info, "examples", None)
    if examples is not None:
        try:
            return examples[example_ix]
        except IndexError:
            return examples[-1]

    # 2. Old-style single example
    example = getattr(field_info, "example", None)
    if example is not None:
        return example

    # 3. Look into json_schema_extra (new style)
    extra = getattr(field_info, "json_schema_extra", {}) or {}

    examples = extra.get("examples")
    if examples is not None:
        try:
            return examples[example_ix]
        except IndexError:
            return examples[-1]

    example = extra.get("example")
    if example is not None:
        return example

    # 5. Default
    if getattr(field_info, "default", None) not in (None, ...):
        return field_info.default

    raise NotImplementedError(
        f"No default or example provided for field '{field_name}': {field_info}"
    )


def get_model_structure_from_examples(
    model_class: type[PydanticBaseModel], multiple: bool
) -> list[dict[str, Any]]:
    """Create a model instance with default or example values, respecting constraints."""
    example_max_length = 1

    # Get first field with examples (non-default) to get example_max_length
    if multiple:
        for _, field_info in model_class.model_fields.items():
            if field_info.examples is not None:
                example_max_length = len(field_info.examples)
                break

    example_data: list[dict[str, Any]] = [{} for _ in range(example_max_length)]

    for field_name, field_info in model_class.model_fields.items():
        if field_info.deprecated:
            continue
        for example_ix in range(example_max_length):
            example_data[example_ix][field_name] = get_example_or_default(
                field_name, field_info, example_ix
            )
    return example_data


def create_model_from_examples(
    model_class: type[PydanticBaseModel], multiple: bool
) -> list[PydanticBaseModel]:
    """Create a model instance with default or example values, respecting constraints."""
    return [
        model_class(**data) for data in get_model_structure_from_examples(model_class, multiple)
    ]


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
    type_name = str(field_type).replace("typing.", "").replace("pathlib._local", "pathlib")
    if type_name.startswith("<class"):
        type_name = field_type.__name__
    return type_name


def generate_config_table_md(
    config: type[PydanticBaseModel],
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
            "<!-- pyml disable line-length -->\n"
            ":::{table} "
            + f"{'::'.join(toplevel_keys)}\n:widths: 10 {env_width}10 5 5 30\n:align: left\n\n"
        )
        table += f"| Name {env_header}| Type | Read-Only | Default | Description |\n"
        table += f"| ---- {env_header_underline}| ---- | --------- | ------- | ----------- |\n"


    fields = {}
    for field_name, field_info in config.model_fields.items():
        fields[field_name] = field_info
    for field_name, field_info in config.model_computed_fields.items():
        fields[field_name] = field_info
    for field_name in sorted(fields.keys()):
        field_info = fields[field_name]
        regular_field = isinstance(field_info, FieldInfo)

        config_name = field_name if extra_config else field_name.upper()
        field_type = field_info.annotation if regular_field else field_info.return_type
        default_value = get_default_value(field_info, regular_field)
        description = config.field_description(field_name)
        deprecated = config.field_deprecated(field_name)
        read_only = "rw" if regular_field else "ro"
        type_name = get_type_name(field_type)

        env_entry = ""
        if not extra_config:
            if regular_field:
                env_entry = f"| `{prefix}{config_name}` "
            else:
                env_entry = "| "
        if deprecated:
            if isinstance(deprecated, bool):
                description = "Deprecated!"
            else:
                description = deprecated
        table += f"| {field_name} {env_entry}| `{type_name}` | `{read_only}` | `{default_value}` | {description} |\n"

        # inner_types: dict[type[PydanticBaseModel], tuple[str, list[str]]] = dict()
        inner_types: dict[Any, tuple[str, list[str]]] = dict()

        def extract_nested_models(subtype: Any, subprefix: str, parent_types: list[str]):
            """Extract nested models."""
            if subtype in inner_types.keys():
                return
            nested_types = resolve_nested_types(subtype, [])
            for nested_type, nested_parent_types in nested_types:
                # Nested type may be of type class, enum, typing.Any
                if isinstance(nested_type, type) and issubclass(nested_type, PydanticBaseModel):
                    # Nested type is a subclass of PydanticBaseModel
                    new_parent_types = parent_types + nested_parent_types
                    if "list" in parent_types:
                        new_prefix = ""
                    else:
                        new_prefix = f"{subprefix}"
                    inner_types.setdefault(nested_type, (new_prefix, new_parent_types))

                    # Handle normal fields
                    for nested_field_name, nested_field_info in nested_type.model_fields.items():
                        nested_field_type = nested_field_info.annotation
                        if new_prefix:
                            new_prefix += f"{nested_field_name.upper()}__"
                        extract_nested_models(
                            nested_field_type,
                            new_prefix,
                            new_parent_types + [nested_field_name],
                        )

                    # Do not extract computed fields

        extract_nested_models(field_type, f"{prefix}{config_name}__", toplevel_keys + [field_name])

        for new_type, info in inner_types.items():
            if new_type not in documented_types:
                undocumented_types.setdefault(new_type, (info[0], info[1]))

    if toplevel:
        table += ":::\n<!-- pyml enable line-length -->\n\n"  # Add an empty line after the table

        has_examples_list = toplevel_keys[-1] == "list"
        instance_list = create_model_from_examples(config, has_examples_list)
        if instance_list:
            ins_dict_list = []
            ins_out_dict_list = []
            for ins in instance_list:
                # Transform to JSON (and manually to dict) to use custom serializers and then merge with parent keys
                ins_json = ins.model_dump_json(include_computed_fields=False)
                ins_dict_list.append(json.loads(ins_json))

                ins_out_json = ins.model_dump_json(include_computed_fields=True)
                ins_out_dict_list.append(json.loads(ins_out_json))

            same_output = ins_out_dict_list == ins_dict_list
            same_output_str = "/Output" if same_output else ""

            # -- code block heading
            table += "<!-- pyml disable no-emphasis-as-heading -->\n"
            table += f"**Example Input{same_output_str}**\n"
            table += "<!-- pyml enable no-emphasis-as-heading -->\n\n"
            # -- code block
            table += "<!-- pyml disable line-length -->\n"
            table += "```json\n"
            if has_examples_list:
                input_dict = build_nested_structure(toplevel_keys[:-1], ins_dict_list)
                if not extra_config:
                    global_config_dict[toplevel_keys[0]] = ins_dict_list
            else:
                input_dict = build_nested_structure(toplevel_keys, ins_dict_list[0])
                if not extra_config:
                    global_config_dict[toplevel_keys[0]] = ins_dict_list[0]
            table += textwrap.indent(json.dumps(input_dict, indent=4), "   ")
            table += "\n```\n<!-- pyml enable line-length -->\n\n"
            # -- end code block

            if not same_output:
                # -- code block heading
                table += "<!-- pyml disable no-emphasis-as-heading -->\n"
                table += f"**Example Output**\n"
                table += "<!-- pyml enable no-emphasis-as-heading -->\n\n"
                # -- code block
                table += "<!-- pyml disable line-length -->\n"
                table += "```json\n"
                if has_examples_list:
                    output_dict = build_nested_structure(toplevel_keys[:-1], ins_out_dict_list)
                else:
                    output_dict = build_nested_structure(toplevel_keys, ins_out_dict_list[0])
                table += textwrap.indent(json.dumps(output_dict, indent=4), "   ")
                table += "\n```\n<!-- pyml enable line-length -->\n\n"
                # -- end code block

        while undocumented_types:
            extra_config_type, extra_info = undocumented_types.popitem()
            documented_types.add(extra_config_type)
            table += generate_config_table_md(
                extra_config_type, extra_info[1], extra_info[0], True, True
            )

    return table


def generate_config_md(file_path: Optional[Union[str, Path]], config_eos: ConfigEOS) -> str:
    """Generate configuration specification in Markdown with extra tables for prefixed values.

    Returns:
        str: The Markdown representation of the configuration spec.
    """
    markdown = ""

    if file_path:
        file_path = Path(file_path)
        # -- table of content
        markdown += "```{toctree}\n"
        markdown += ":maxdepth: 1\n"
        markdown += ":caption: Configuration Table\n\n"
    else:
        markdown += "# Configuration Table\n\n"
        markdown += (
            "The configuration table describes all the configuration options of Akkudoktor-EOS\n\n"
        )

    # Generate tables for each top level config
    for field_name in sorted(config_eos.__class__.model_fields.keys()):
        field_info = config_eos.__class__.model_fields[field_name]
        field_type = field_info.annotation
        model_class = get_model_class_from_annotation(field_type)
        if model_class is None:
            raise ValueError(f"Can not find class of top level field {field_name}.")
        table = generate_config_table_md(
            model_class, [field_name], f"EOS_{field_name.upper()}__", True
        )
        if file_path:
            # Write table to extra document
            table_path = file_path.with_name(file_path.stem + f"{field_name.lower()}.md")
            write_to_file(table_path, table)
            markdown += f"../_generated/{table_path.name}\n"
        else:
            # We will write to stdout
            markdown += "---\n\n"
            markdown += table

    # Generate full example
    example = ""
    # Full config
    example += "## Full example Config\n\n"
    # -- code block
    example += "<!-- pyml disable line-length -->\n"
    example += "```json\n"
    # Test for valid config first
    config_eos.merge_settings_from_dict(global_config_dict)
    example += textwrap.indent(json.dumps(global_config_dict, indent=4), "   ")
    example += "\n"
    example += "```\n<!-- pyml enable line-length -->\n\n"
    # -- end code block end
    if file_path:
        example_path = file_path.with_name(file_path.stem + f"example.md")
        write_to_file(example_path, example)
        markdown += f"../_generated/{example_path.name}\n"
        markdown += "```\n\n"
        # -- end table of content
    else:
        markdown += "---\n\n"
        markdown += example

    # Assure there is no double \n at end of file
    markdown = markdown.rstrip("\n")
    markdown += "\n"

    markdown += "\nAuto generated from source code.\n"

    # Write markdown to file or stdout
    write_to_file(file_path, markdown)

    return markdown


def write_to_file(file_path: Optional[Union[str, Path]], config_md: str):
    if os.name == "nt":
       config_md = config_md.replace("\\\\", "/")

    # Assure log path does not leak to documentation
    config_md = re.sub(
        r'(?<=["\'])/[^"\']*/output/eos\.log(?=["\'])',
        '/home/user/.local/share/net.akkudoktor.eos/output/eos.log',
        config_md
    )
    # Assure pathes are set to default for documentation
    replacements = [
        ("data_folder_path", "/home/user/.local/share/net.akkudoktoreos.net"),
        ("data_output_path", "/home/user/.local/share/net.akkudoktoreos.net/output"),
        ("config_folder_path", "/home/user/.config/net.akkudoktoreos.net"),
        ("config_file_path", "/home/user/.config/net.akkudoktoreos.net/EOS.config.json"),
    ]
    for key, value in replacements:
        config_md = re.sub(
            rf'("{key}":\s*)"[^"]*"',
            rf'\1"{value}"',
            config_md
        )

    # Assure timezone name does not leak to documentation
    tz_name = to_datetime().timezone_name
    config_md = re.sub(re.escape(tz_name), "Europe/Berlin", config_md, flags=re.IGNORECASE)
    # Also replace UTC, as GitHub CI always is on UTC
    config_md = re.sub(re.escape("UTC"), "Europe/Berlin", config_md, flags=re.IGNORECASE)

    # Assure no extra lines at end of file
    config_md = config_md.rstrip("\n")
    config_md += "\n"

    if file_path:
        # Write to file
        with open(Path(file_path), "w", encoding="utf-8", newline="\n") as f:
            f.write(config_md)
    else:
        # Write to std output
        print(config_md)


def main():
    """Main function to run the generation of the Configuration specification as Markdown."""
    parser = argparse.ArgumentParser(description="Generate Configuration Specification as Markdown")
    parser.add_argument(
        "--output-file",
        type=str,
        default=None,
        help="File to write the top level configuration specification to.",
    )

    args = parser.parse_args()

    # Ensure we are in documentation mode
    ConfigEOS._force_documentation_mode = True

    # Make minimal config to make the generation reproducable
    config_eos = get_config(init={
            "with_init_settings": True,
            "with_env_settings": False,
            "with_dotenv_settings": False,
            "with_file_settings": False,
            "with_file_secret_settings": False,
        })

    # Also init other singletons to get same list of e.g. providers
    singletons_init()

    try:
        config_md = generate_config_md(args.output_file, config_eos)
    except Exception as e:
        print(f"Error during Configuration Specification generation: {e}", file=sys.stderr)
        # keep throwing error to debug potential problems (e.g. invalid examples)
        raise e
    finally:
        # Ensure we are out of documentation mode
        ConfigEOS._force_documentation_mode = False

if __name__ == "__main__":
    main()
