# tests/test_version.py
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from akkudoktoreos.core.version import _version_calculate, _version_hash

DIR_PROJECT_ROOT = Path(__file__).parent.parent
GET_VERSION_SCRIPT = DIR_PROJECT_ROOT / "scripts" / "get_version.py"
BUMP_DEV_SCRIPT = DIR_PROJECT_ROOT / "scripts" / "bump_dev_version.py"
UPDATE_SCRIPT = DIR_PROJECT_ROOT / "scripts" / "update_version.py"


# --- Helper to create test files ---
def write_file(path: Path, content: str):
    path.write_text(content, encoding="utf-8")
    return path


# --- Test version helpers ---
def test_version_non_dev(monkeypatch):
    """If VERSION_BASE does not end with 'dev', no hash digits are appended."""
    monkeypatch.setattr("akkudoktoreos.core.version.VERSION_BASE", "0.2.0")
    result = _version_calculate()
    assert result == "0.2.0"


def test_version_dev_precision_8(monkeypatch):
    """Test that a dev version appends exactly 8 digits derived from the hash."""
    fake_hash = "abcdef1234567890"  # deterministic fake digest

    monkeypatch.setattr("akkudoktoreos.core.version._version_hash", lambda: fake_hash)
    monkeypatch.setattr("akkudoktoreos.core.version.VERSION_BASE", "0.2.0.dev")
    monkeypatch.setattr("akkudoktoreos.core.version.VERSION_DEV_PRECISION", 8)

    result = _version_calculate()

    # compute expected suffix
    hash_value = int(fake_hash, 16)
    expected_digits = str(hash_value % (10 ** 8)).zfill(8)

    expected = f"0.2.0.dev{expected_digits}"

    assert result == expected
    assert len(expected_digits) == 8
    assert result.startswith("0.2.0.dev")
    assert result == expected


def test_version_dev_precision_8_different_hash(monkeypatch):
    """A different hash must produce a different 8-digit suffix."""
    fake_hash = "1234abcd9999ffff"

    monkeypatch.setattr("akkudoktoreos.core.version._version_hash", lambda: fake_hash)
    monkeypatch.setattr("akkudoktoreos.core.version.VERSION_BASE", "0.2.0.dev")
    monkeypatch.setattr("akkudoktoreos.core.version.VERSION_DEV_PRECISION", 8)

    result = _version_calculate()

    hash_value = int(fake_hash, 16)
    expected_digits = str(hash_value % (10 ** 8)).zfill(8)
    expected = f"0.2.0.dev{expected_digits}"

    assert result == expected
    assert len(expected_digits) == 8


# --- 1️⃣ Test get_version.py ---
def test_get_version_prints_non_empty():
    result = subprocess.run(
        [sys.executable, str(GET_VERSION_SCRIPT)],
        capture_output=True,
        text=True,
        check=True
    )
    version = result.stdout.strip()
    assert version, "get_version.py should print a non-empty version"
    assert len(version.split(".")) >= 3, "Version should have at least MAJOR.MINOR.PATCH"


# --- 2️⃣ Test update_version.py on multiple file types ---
def test_update_version_multiple_formats(tmp_path):
    py_file = write_file(tmp_path / "version.py", '__version__ = "0.1.0"\n')
    yaml_file = write_file(tmp_path / "config.yaml", 'version: "0.1.0"\n')
    json_file = write_file(tmp_path / "package.json", '{"version": "0.1.0"}\n')

    new_version = "0.2.0"
    files = [py_file, yaml_file, json_file]

    subprocess.run(
        [sys.executable, str(UPDATE_SCRIPT), new_version] + [str(f.resolve()) for f in files],
        check=True
    )

    # Verify updates
    assert f'__version__ = "{new_version}"' in py_file.read_text()
    assert yaml.safe_load(yaml_file.read_text())["version"] == new_version
    assert f'"version": "{new_version}"' in json_file.read_text()


# --- 3️⃣ Test bump_dev_version.py ---
def test_bump_dev_version_appends_dev(tmp_path):
    version_file = write_file(tmp_path / "version.py", 'VERSION_BASE = "0.2.0"\n')

    result = subprocess.run(
        [sys.executable, str(BUMP_DEV_SCRIPT), str(version_file.resolve())],
        capture_output=True,
        text=True,
        check=True
    )
    new_version = result.stdout.strip()
    assert new_version == "0.2.0.dev"

    content = version_file.read_text()
    assert f'VERSION_BASE = "{new_version}"' in content


# --- 4️⃣ Full workflow simulation with git ---
def test_workflow_git(tmp_path):
    # Create git repo
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)

    # Create files
    version_file = write_file(tmp_path / "version.py", 'VERSION_BASE = "0.1.0"\n')
    config_file = write_file(tmp_path / "config.yaml", 'version: "0.1.0"\n')

    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=tmp_path, check=True)

    # --- Step 1: Calculate version (mock) ---
    new_version = "0.2.0"

    # --- Step 2: Update files ---
    subprocess.run(
        [sys.executable, str(UPDATE_SCRIPT), new_version, str(config_file.resolve()), str(version_file.resolve())],
        cwd=tmp_path,
        check=True
    )

    # --- Step 3: Commit updated files if needed ---
    subprocess.run(["git", "add", str(config_file.resolve()), str(version_file.resolve())], cwd=tmp_path, check=True)
    diff_result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=tmp_path)
    assert diff_result.returncode == 1, "There should be staged changes to commit"
    subprocess.run(["git", "commit", "-m", f"chore: bump version to {new_version}"], cwd=tmp_path, check=True)

    # --- Step 4: Tag version ---
    tag_name = f"v{new_version}"
    subprocess.run(["git", "tag", "-a", tag_name, "-m", f"Release {new_version}"], cwd=tmp_path, check=True)
    tags = subprocess.run(["git", "tag"], cwd=tmp_path, capture_output=True, text=True, check=True).stdout
    assert tag_name in tags

    # --- Step 5: Bump dev version ---
    result = subprocess.run(
        [sys.executable, str(BUMP_DEV_SCRIPT), str(version_file.resolve())],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True
    )
    dev_version = result.stdout.strip()
    assert dev_version.endswith(".dev")
    assert dev_version.count(".dev") == 1
    content = version_file.read_text()
    assert f'VERSION_BASE = "{dev_version}"' in content
