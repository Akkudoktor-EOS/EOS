"""Release candidates remain identifiable and can advance without stale suffixes."""

# ruff: noqa: S101

import importlib
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("value", ["0.4.0rc1", "0.4.0rc12", "0.4.0", "0.4.0.dev260918120001"])
def test_version_accepts_release_candidate(monkeypatch, value):
    module = importlib.import_module("akkudoktoreos.core.version")
    monkeypatch.setattr(module, "__version__", value)
    info = module.version()
    assert info["version"] == value
    assert info["base"] == "0.4.0"
    assert info["rc"] == (value[5:] if "rc" in value else None)


@pytest.mark.parametrize("value", ["0.4.0rc", "0.4.0rc0", "0.4.0rc1junk"])
def test_version_rejects_incomplete_candidate(monkeypatch, value):
    module = importlib.import_module("akkudoktoreos.core.version")
    monkeypatch.setattr(module, "__version__", value)
    with pytest.raises(ValueError, match="Invalid version"):
        module.version()


def test_version_update_advances_candidate_and_stable(tmp_path):
    target = tmp_path / "versions.txt"
    templates = [
        '__version__ = "{value}"',
        'version = "{value}"',
        '"version": "{value}"',
        "VERSION ?= {value}",
        "VERSION = {value}",
        'version: "{value}"',
    ]

    def contents(value):
        return "\n".join(template.format(value=value) for template in templates) + "\n"

    target.write_text(contents("0.4.0rc1"), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts" / "update_version.py"
    for value in ["0.4.0rc2", "0.4.0", "0.4.1.dev260918120001", "0.4.1rc1"]:
        subprocess.run([sys.executable, str(script), value, str(target)], check=True)  # noqa: S603
        assert target.read_text(encoding="utf-8") == contents(value)
