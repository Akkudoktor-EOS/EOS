import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import pytest

DIR_PROJECT_ROOT = Path(__file__).absolute().parent.parent
DIR_BUILD = DIR_PROJECT_ROOT / "build"
DIR_BUILD_DOCS = DIR_PROJECT_ROOT / "build" / "docs"
DIR_DOCS = DIR_PROJECT_ROOT / "docs"
DIR_SRC = DIR_PROJECT_ROOT / "src"

HASH_FILE = DIR_BUILD / ".sphinx_hash.json"


def find_sphinx_build() -> str:
    venv = os.getenv("VIRTUAL_ENV")
    paths = [Path(venv)] if venv else []
    paths.append(DIR_PROJECT_ROOT / ".venv")

    for base in paths:
        cmd = base / ("Scripts" if os.name == "nt" else "bin") / ("sphinx-build.exe" if os.name == "nt" else "sphinx-build")
        if cmd.exists():
            return str(cmd)
    return "sphinx-build"


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

    SPHINX_CMD = [
        find_sphinx_build(),
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
            pytest.skip("Skipping Sphinx test — not full run")

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
            # Run sphinx-build
            project_dir = Path(__file__).parent.parent
            process = subprocess.run(
                self.SPHINX_CMD,
                check=True,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=project_dir,
            )
            # Combine output
            output = process.stdout + "\n" + process.stderr
            returncode = process.returncode
        except:
            output = f"ERROR: Could not start sphinx-build - {self.SPHINX_CMD}"
            returncode = -1

        # Remove temporary EOS_DIR
        eos_tmp_dir.cleanup()

        assert returncode == 0

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
