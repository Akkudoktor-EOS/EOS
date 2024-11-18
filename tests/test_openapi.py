import json
from pathlib import Path

from generate_openapi import generate_openapi

DIR_PROJECT_ROOT = Path(__file__).parent.parent
DIR_TESTDATA = Path(__file__).parent / "testdata"


def test_openapi_spec_current():
    """Verify the openapi spec hasn´t changed."""
    old_spec_path = DIR_PROJECT_ROOT / "docs" / "akkudoktoreos" / "openapi.json"
    new_spec_path = DIR_TESTDATA / "openapi-new.json"
    generate_openapi(new_spec_path)
    with open(new_spec_path) as f_new:
        new_spec = json.load(f_new)
    with open(old_spec_path) as f_old:
        old_spec = json.load(f_old)
    assert new_spec == old_spec
