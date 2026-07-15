import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import pytest

from akkudoktoreos.core.coreabc import singletons_init

DIR_PROJECT_ROOT = Path(__file__).absolute().parent.parent
DIR_BUILD = DIR_PROJECT_ROOT / "build"
DIR_BUILD_DOCS = DIR_PROJECT_ROOT / "build" / "docs"
DIR_DOCS = DIR_PROJECT_ROOT / "docs"
DIR_SRC = DIR_PROJECT_ROOT / "src"

HASH_FILE = DIR_BUILD / ".sphinx_hash.json"


import os
import subprocess
from pathlib import Path


def find_sphinx_build() -> list[str]:
    """Return command to invoke sphinx-build via virtualenv, uv, or globally."""
    candidates = []

    # 1️⃣ Currently active virtualenv
    venv = os.getenv("VIRTUAL_ENV")
    if venv:
        candidates.append(Path(venv))

    # 2️⃣ uv‑managed virtualenv
    uv_venv = Path(".uv") / "venv"
    if uv_venv.exists():
        candidates.append(uv_venv)

    # 3️⃣ traditional .venv
    dot_venv = Path(".venv")
    if dot_venv.exists():
        candidates.append(dot_venv)

    # Check each candidate for the sphinx‑build binary
    for base in candidates:
        sphinx_build_path = base / ("Scripts" if os.name == "nt" else "bin") / (
            "sphinx-build.exe" if os.name == "nt" else "sphinx-build"
        )
        if sphinx_build_path.exists():
            return [str(sphinx_build_path)]

    # 4️⃣ fallback to uv run sphinx‑build
    try:
        subprocess.run(
            ["uv", "run", "sphinx-build", "--version"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return ["uv", "run", "sphinx-build"]
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # 5️⃣ final fallback to system sphinx‑build
    return ["sphinx-build"]


@pytest.fixture(scope="session")
def sphinx_changed(version_and_hash) -> Optional[str]:
    """Returns new hash if any watched files have changed since last run.

    Hash is stored in .sphinx_hash.json.
    """
    new_hash = None

    # Load previous hash
    try:
        previous = json.loads(HASH_FILE.read_text())
        previous_hash = previous.get("hash")
    except Exception:
        previous_hash = None

    changed = (previous_hash != version_and_hash["hash_current"])

    if changed:
        new_hash = version_and_hash["hash_current"]

    return new_hash


class TestSphinxDocumentation:
    """Test class to verify Sphinx documentation generation.

    Ensures no major warnings are emitted.
    """

    SPHINX_CMD = find_sphinx_build() + [
        "-M",
        "html",
        str(DIR_DOCS),
        str(DIR_BUILD_DOCS),
    ]

    def _cleanup_autosum_dirs(self):
        """Delete all *_autosum folders inside docs/."""
        for folder in DIR_DOCS.rglob("*_autosum"):
            if folder.is_dir():
                shutil.rmtree(folder)

    def _cleanup_build_dir(self):
        """Delete build/docs directory if present."""
        if DIR_BUILD_DOCS.exists():
            shutil.rmtree(DIR_BUILD_DOCS)

    def test_sphinx_build(self, sphinx_changed: Optional[str], is_finalize: bool):
        """Build Sphinx documentation and ensure no major warnings appear in the build output."""

        # Ensure docs folder exists
        if not DIR_DOCS.exists():
            pytest.skip(f"Skipping Sphinx build test - docs folder not present: {DIR_DOCS}")

        if not sphinx_changed:
            pytest.skip(f"Skipping Sphinx build — no relevant file changes detected: {HASH_FILE}")

        if not is_finalize:
            pytest.skip("Skipping Sphinx test — not finalize")

        # Clean directories
        self._cleanup_autosum_dirs()
        self._cleanup_build_dir()

        # Set environment for sphinx run (sphinx will make eos create a config file)
        eos_tmp_dir = tempfile.TemporaryDirectory()
        eos_dir = str(eos_tmp_dir.name)
        env = os.environ.copy()
        env["EOS_DIR"] = eos_dir
        env["EOS_CONFIG_DIR"] = eos_dir

        try:
            process = subprocess.run(
                self.SPHINX_CMD,
                check=True,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=DIR_PROJECT_ROOT,          # use the existing constant
            )
            output = process.stdout + "\n" + process.stderr
            returncode = process.returncode
        except subprocess.CalledProcessError as e:
            output = e.stdout + "\n" + e.stderr if e.stdout else ""
            returncode = e.returncode
        except Exception as e:
            output = f"Failed to execute command: {e}"
            returncode = -1

        # Remove temporary EOS_DIR
        eos_tmp_dir.cleanup()

        if returncode != 0:
            pytest.fail(
                f"Sphinx build failed with exit code {returncode}.\n"
                f"{output}\n"
            )

        # Possible markers: ERROR: WARNING: TRACEBACK:
        major_markers = ("ERROR:", "TRACEBACK:")

        bad_lines = [
            line for line in output.splitlines()
            if any(marker in line for marker in major_markers)
        ]

        assert not bad_lines, f"Sphinx build contained errors:\n" + "\n".join(bad_lines)

        # Update stored hash
        HASH_FILE.parent.mkdir(parents=True, exist_ok=True)
        HASH_FILE.write_text(json.dumps({"hash": sphinx_changed}, indent=2))
