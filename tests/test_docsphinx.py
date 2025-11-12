import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from fnmatch import fnmatch
from pathlib import Path

import pytest

DIR_PROJECT_ROOT = Path(__file__).absolute().parent.parent
DIR_BUILD = DIR_PROJECT_ROOT / "build"
DIR_BUILD_DOCS = DIR_PROJECT_ROOT / "build" / "docs"
DIR_DOCS = DIR_PROJECT_ROOT / "docs"
DIR_SRC = DIR_PROJECT_ROOT / "src"

HASH_FILE = DIR_BUILD / ".sphinx_hash.json"

# Allowed file suffixes to consider
ALLOWED_SUFFIXES = {".py", ".md", ".json"}

# Directory patterns to exclude (glob-like)
EXCLUDED_DIR_PATTERNS = {"*_autosum", "*__pycache__"}


def is_excluded_dir(path: Path) -> bool:
    """Check whether a directory should be excluded based on name patterns."""
    return any(fnmatch(path.name, pattern) for pattern in EXCLUDED_DIR_PATTERNS)


def hash_tree(paths: list[Path], suffixes=ALLOWED_SUFFIXES) -> str:
    """Return SHA256 hash for files under `paths`.

    Restricted by suffix, excluding excluded directory patterns.
    """
    h = hashlib.sha256()

    for root in paths:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            # Skip excluded directories
            if p.is_dir() and is_excluded_dir(p):
                continue

            # Skip files inside excluded directories
            if any(is_excluded_dir(parent) for parent in p.parents):
                continue

            # Hash only allowed file types
            if p.is_file() and p.suffix.lower() in suffixes:
                h.update(p.read_bytes())

    return h.hexdigest()


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
def sphinx_changed() -> bool:
    """Returns True if any watched files have changed since last run.

    Hash is stored in .sphinx_hash.json.
    """
    # Directories whose changes should trigger rebuilding docs
    watched_paths = [Path("docs"), Path("src")]

    current_hash = hash_tree(watched_paths)

    # Load previous hash
    try:
        previous = json.loads(HASH_FILE.read_text())
        previous_hash = previous.get("hash")
    except Exception:
        previous_hash = None

    changed = (previous_hash != current_hash)

    # Update stored hash
    HASH_FILE.parent.mkdir(parents=True, exist_ok=True)
    HASH_FILE.write_text(json.dumps({"hash": current_hash}, indent=2))

    return changed


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

    def test_sphinx_build(self, sphinx_changed: bool, is_full_run: bool):
        """Build Sphinx documentation and ensure no major warnings appear in the build output."""
        if not is_full_run:
            pytest.skip("Skipping Sphinx test — not full run")

        if not sphinx_changed:
            pytest.skip(f"Skipping Sphinx build — no relevant file changes detected: {HASH_FILE}")

        # Ensure docs folder exists
        if not Path("docs").exists():
            pytest.skip(f"Skipping Sphinx build test - docs folder not present: {DIR_DOCS}")

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
