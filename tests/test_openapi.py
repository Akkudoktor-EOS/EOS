import json
from pathlib import Path
from unittest.mock import patch

from akkudoktoreos.utils.openapiutil import openapi_to_markdown

DIR_PROJECT_ROOT = Path(__file__).parent.parent
DIR_TESTDATA = Path(__file__).parent / "testdata"


def insert_markdown(
    base_text: str, insert_text: str, start_line: str, end_line: str, heading_level: int = 0
) -> str:
    """Insert markdown text between specified lines in base text and adjust heading levels.

    Args:
        base_text (str): The original markdown text
        insert_text (str): The text to insert
        start_line (str): Line after which to insert the text
        end_line (str): Line before which to insert the text
        heading_level (int): Number of additional # to add to headings

    Returns:
        str: Modified markdown text
    """
    # Split texts into lines
    base_lines = base_text.split("\n")
    insert_lines = insert_text.split("\n")

    # Process headings in insert_text
    if heading_level > 0:
        processed_insert_lines = []
        for line in insert_lines:
            # Check if line starts with # (is a heading)
            if line.lstrip().startswith("#"):
                # Count leading spaces
                leading_spaces = len(line) - len(line.lstrip())
                # Add additional # characters
                line = " " * leading_spaces + "#" * heading_level + line.lstrip()
            processed_insert_lines.append(line)
        insert_lines = processed_insert_lines

    # Find the positions of start and end lines
    start_pos = -1
    end_pos = -1

    for i, line in enumerate(base_lines):
        if start_line in line and start_pos == -1:
            start_pos = i
        if end_line in line and end_pos == -1:
            end_pos = i
            break

    # Check if both lines were found
    if start_pos == -1 or end_pos == -1:
        raise ValueError("Start or end line not found in base text")

    # Construct the new text
    result = base_lines[: start_pos + 1]  # Include start line
    result.extend(insert_lines)  # Add insert text
    result.extend(base_lines[end_pos:])  # Add from end line to end

    return "\n".join(result)


def test_openapi_spec_current(config_eos):
    """Verify the openapi spec hasn´t changed."""
    old_spec_path = DIR_PROJECT_ROOT / "docs" / "akkudoktoreos" / "openapi.json"
    new_spec_path = DIR_TESTDATA / "openapi-new.json"
    old_readme_path = DIR_PROJECT_ROOT / "README.md"
    new_readme_path = DIR_TESTDATA / "README-new.md"
    # Patch get_config and import within guard to patch global variables within the fastapi_server module.
    with patch("akkudoktoreos.config.config.get_config", return_value=config_eos):
        from generate_openapi import generate_openapi

        generate_openapi(new_spec_path)
    with open(new_spec_path) as f_new:
        new_spec = json.load(f_new)
    with open(old_spec_path) as f_old:
        old_spec = json.load(f_old)

    # Serialize to ensure comparison is consistent
    new_spec = json.dumps(new_spec, indent=4, sort_keys=True)
    old_spec = json.dumps(old_spec, indent=4, sort_keys=True)

    assert new_spec == old_spec

    # Also create updates for Readme
    spec = json.loads(new_spec)
    markdown = openapi_to_markdown(spec)

    with open(old_readme_path) as f_old:
        old_readme = f_old.read()
    with open(new_readme_path, "w") as f_new:
        new_readme = insert_markdown(
            old_readme, "\n" + markdown, "See the Swagger API", "## Further resources", 2
        )
        f_new.write(new_readme)

    assert new_readme == old_readme
