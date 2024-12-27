import json
from pathlib import Path
from unittest.mock import patch

DIR_PROJECT_ROOT = Path(__file__).parent.parent
DIR_TESTDATA = Path(__file__).parent / "testdata"


def test_openapi_spec_current(config_eos):
    """Verify the openapi spec hasn't changed."""
    old_spec_path = DIR_PROJECT_ROOT / "docs" / "akkudoktoreos" / "openapi.json"
    new_spec_path = DIR_TESTDATA / "openapi-new.json"
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
