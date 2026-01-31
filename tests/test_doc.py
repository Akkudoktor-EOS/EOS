import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

DIR_PROJECT_ROOT = Path(__file__).parent.parent
DIR_TESTDATA = Path(__file__).parent / "testdata"

DIR_DOCS_GENERATED = DIR_PROJECT_ROOT / "docs" / "_generated"
DIR_TEST_GENERATED = DIR_TESTDATA / "docs" / "_generated"


def test_openapi_spec_current(config_eos):
    """Verify the openapi spec hasn´t changed."""
    expected_spec_path = DIR_PROJECT_ROOT / "openapi.json"
    new_spec_path = DIR_TESTDATA / "openapi-new.json"

    with expected_spec_path.open("r", encoding="utf-8", newline=None) as f_expected:
        expected_spec = json.load(f_expected)

    # Patch get_config and import within guard to patch global variables within the eos module.
    with patch("akkudoktoreos.config.config.get_config", return_value=config_eos):
        # Ensure the script works correctly as part of a package
        root_dir = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(root_dir))
        from scripts import generate_openapi

        spec = generate_openapi.generate_openapi()
        spec_str = json.dumps(spec, indent=4, sort_keys=True)

    with new_spec_path.open("w", encoding="utf-8", newline="\n") as f_new:
        f_new.write(spec_str)

    # Serialize to ensure comparison is consistent
    expected_spec_str = json.dumps(expected_spec, indent=4, sort_keys=True)

    try:
        assert spec_str == expected_spec_str
    except AssertionError as e:
        pytest.fail(
            f"Expected {new_spec_path} to equal {expected_spec_path}.\n"
            + f"If ok: `make gen-docs` or `cp {new_spec_path} {expected_spec_path}`\n"
        )


def test_openapi_md_current(config_eos):
    """Verify the generated openapi markdown hasn´t changed."""
    expected_spec_md_path = DIR_PROJECT_ROOT / "docs" / "_generated" / "openapi.md"
    new_spec_md_path = DIR_TESTDATA / "openapi-new.md"

    with expected_spec_md_path.open("r", encoding="utf-8", newline=None) as f_expected:
        expected_spec_md = f_expected.read()

    # Patch get_config and import within guard to patch global variables within the eos module.
    with patch("akkudoktoreos.config.config.get_config", return_value=config_eos):
        # Ensure the script works correctly as part of a package
        root_dir = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(root_dir))
        from scripts import generate_openapi_md

        spec_md = generate_openapi_md.generate_openapi_md()

    with new_spec_md_path.open("w", encoding="utf-8", newline="\n") as f_new:
        f_new.write(spec_md)

    try:
        assert spec_md == expected_spec_md
    except AssertionError as e:
        pytest.fail(
            f"Expected {new_spec_md_path} to equal {expected_spec_md_path}.\n"
            + f"If ok: `make gen-docs` or `cp {new_spec_md_path} {expected_spec_md_path}`\n"
        )


def test_config_md_current(config_eos):
    """Verify the generated configuration markdown hasn´t changed."""
    assert DIR_DOCS_GENERATED.exists()

    # Remove any leftover files from last run
    if DIR_TEST_GENERATED.exists():
        shutil.rmtree(DIR_TEST_GENERATED)

    # Ensure test dir exists
    DIR_TEST_GENERATED.mkdir(parents=True, exist_ok=True)

    # Patch get_config and import within guard to patch global variables within the eos module.
    with patch("akkudoktoreos.config.config.get_config", return_value=config_eos):
        # Ensure the script works correctly as part of a package
        root_dir = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(root_dir))
        from scripts import generate_config_md

        # Get all the top level fields
        field_names = sorted(config_eos.__class__.model_fields.keys())

        # Create the file paths
        expected = [ DIR_DOCS_GENERATED / "config.md", DIR_DOCS_GENERATED / "configexample.md", ]
        tested = [ DIR_TEST_GENERATED / "config.md", DIR_TEST_GENERATED / "configexample.md", ]
        for field_name in field_names:
            file_name = f"config{field_name.lower()}.md"
            expected.append(DIR_DOCS_GENERATED / file_name)
            tested.append(DIR_TEST_GENERATED / file_name)

        # Create test files
        config_md = generate_config_md.generate_config_md(tested[0], config_eos)

    # Check test files are the same as the expected files
    for i, expected_path in enumerate(expected):
        tested_path = tested[i]

        with expected_path.open("r", encoding="utf-8", newline=None) as f_expected:
            expected_config_md = f_expected.read()
        with tested_path.open("r", encoding="utf-8", newline=None) as f_expected:
            tested_config_md = f_expected.read()

        try:
            assert tested_config_md == expected_config_md
        except AssertionError as e:
            pytest.fail(
                f"Expected {tested_path} to equal {expected_path}.\n"
                + f"If ok: `make gen-docs` or `cp {tested_path} {expected_path}`\n"
            )
