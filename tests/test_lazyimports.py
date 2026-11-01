"""Heavy optional dependencies must not be imported by a plain server start.

A GENETIC server with external (import) providers and EOSdash disabled should not
load the PDF report stack, the price fallback forecast, pvlib-based providers,
the HTML scraper, the GENETIC0 optimizer or the timezone polygon lookup. Each of
these costs several to tens of MB of resident memory on small devices.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from akkudoktoreos.config.config import GeneralSettings

LAZY_MODULES = [
    "matplotlib",
    "statsmodels",
    "pvlib",
    "bs4",
    "lxml",
    "scipy",
    "tzfpy",
    "akkudoktoreos.optimization.genetic0.genetic0",
    "akkudoktoreos.optimization.genetic0.genetic0visualize",
    "akkudoktoreos.optimization.genetic.geneticvisualize",
]

PROBE = """
import json
import sys

import akkudoktoreos.server.eos  # noqa: F401
from akkudoktoreos.core.coreabc import get_config, get_prediction, singletons_init

config = get_config(init=True)
singletons_init()
get_prediction()
assert config.general.timezone == "Europe/Berlin", config.general.timezone
loaded = sorted(name for name in {modules} if name in sys.modules)
print("LOADED_MODULES=" + json.dumps(loaded))
"""


def _loaded_modules(tmp_path: Path, extra_env: dict[str, str]) -> list[str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("EOS_")}
    env.update(
        {
            "EOS_DIR": str(tmp_path),
            "EOS_CONFIG_DIR": str(tmp_path),
            "EOS_GENERAL__DATA_FOLDER_PATH": str(tmp_path / "data"),
            "EOS_SERVER__STARTUP_EOSDASH": "false",
            "EOS_OPTIMIZATION__ALGORITHM": "GENETIC",
            "EOS_LOGGING__CONSOLE_LEVEL": "ERROR",
        }
    )
    env.update(extra_env)
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", PROBE.format(modules=LAZY_MODULES)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    marker = "LOADED_MODULES="
    lines = [line for line in result.stdout.splitlines() if line.startswith(marker)]
    assert len(lines) == 1, result.stdout[-4000:]
    return json.loads(lines[0][len(marker) :])


def test_server_start_does_not_import_heavy_optional_modules(tmp_path: Path):
    loaded = _loaded_modules(tmp_path, {"EOS_GENERAL__TIMEZONE_OVERRIDE": "Europe/Berlin"})
    assert loaded == []


def test_timezone_lookup_still_used_without_override(tmp_path: Path):
    # Default latitude/longitude are in Berlin, so the lookup gives the same name.
    loaded = _loaded_modules(tmp_path, {})
    assert loaded == ["tzfpy"]


def test_timezone_override_semantics():
    general = GeneralSettings()
    assert general.timezone_override is None
    assert general.timezone == "Europe/Berlin"

    general = GeneralSettings(latitude=40.7128, longitude=-74.0060)
    assert general.timezone == "America/New_York"

    general = GeneralSettings(
        latitude=40.7128, longitude=-74.0060, timezone_override="Europe/Berlin"
    )
    assert general.timezone == "Europe/Berlin"

    # Without coordinates the override still applies.
    general = GeneralSettings(latitude=None, longitude=None, timezone_override="Asia/Tokyo")
    assert general.timezone == "Asia/Tokyo"
    assert GeneralSettings(latitude=None, longitude=None).timezone is None

    assert GeneralSettings(timezone_override="Etc/UTC").timezone == "UTC"


def test_timezone_override_rejects_unknown_names():
    with pytest.raises(ValueError):
        GeneralSettings(timezone_override="Mars/Olympus_Mons")
