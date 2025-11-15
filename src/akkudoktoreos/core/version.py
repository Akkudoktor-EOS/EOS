"""Version information for akkudoktoreos."""

import hashlib
import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional

# For development add `+dev` to previous release
# For release omit `+dev`.
VERSION_BASE = "0.2.0+dev"

# Project hash of relevant files
HASH_EOS = ""


# ------------------------------
# Helpers for version generation
# ------------------------------


def is_excluded_dir(path: Path, excluded_dir_patterns: set[str]) -> bool:
    """Check whether a directory should be excluded based on name patterns."""
    return any(fnmatch(path.name, pattern) for pattern in excluded_dir_patterns)


def hash_tree(
    paths: list[Path],
    allowed_suffixes: set[str],
    excluded_dir_patterns: set[str],
    excluded_files: Optional[set[Path]] = None,
) -> str:
    """Return SHA256 hash for files under `paths`.

    Restricted by suffix, excluding excluded directory patterns and excluded_files.
    """
    h = hashlib.sha256()
    excluded_files = excluded_files or set()

    for root in paths:
        if not root.exists():
            raise ValueError(f"Root path does not exist: {root}")
        for p in sorted(root.rglob("*")):
            # Skip excluded directories
            if p.is_dir() and is_excluded_dir(p, excluded_dir_patterns):
                continue

            # Skip files inside excluded directories
            if any(is_excluded_dir(parent, excluded_dir_patterns) for parent in p.parents):
                continue

            # Skip excluded files
            if p.resolve() in excluded_files:
                continue

            # Hash only allowed file types
            if p.is_file() and p.suffix.lower() in allowed_suffixes:
                h.update(p.read_bytes())

    digest = h.hexdigest()

    return digest


def _version_hash() -> str:
    """Calculate project hash.

    Only package file ins src/akkudoktoreos can be hashed to make it work also for packages.
    """
    DIR_PACKAGE_ROOT = Path(__file__).resolve().parent.parent

    # Allowed file suffixes to consider
    ALLOWED_SUFFIXES: set[str] = {".py", ".md", ".json"}

    # Directory patterns to exclude (glob-like)
    EXCLUDED_DIR_PATTERNS: set[str] = {"*_autosum", "*__pycache__", "*_generated"}

    # Files to exclude
    EXCLUDED_FILES: set[Path] = set()

    # Directories whose changes shall be part of the project hash
    watched_paths = [DIR_PACKAGE_ROOT]

    hash_current = hash_tree(
        watched_paths, ALLOWED_SUFFIXES, EXCLUDED_DIR_PATTERNS, excluded_files=EXCLUDED_FILES
    )
    return hash_current


def _version_calculate() -> str:
    """Compute version."""
    global HASH_EOS
    HASH_EOS = _version_hash()
    if VERSION_BASE.endswith("+dev"):
        return f"{VERSION_BASE}.{HASH_EOS[:6]}"
    else:
        return VERSION_BASE


# ---------------------------
# Project version information
# ----------------------------

# The version
__version__ = _version_calculate()


# -------------------
# Version info access
# -------------------


# Regular expression to split the version string into pieces
VERSION_RE = re.compile(
    r"""
    ^(?P<base>\d+\.\d+\.\d+)            # x.y.z
    (?:\+                               # +dev.hash starts here
        (?:
            (?P<dev>dev)                # literal 'dev'
            (?:\.(?P<hash>[A-Za-z0-9]+))?  # optional .hash
        )
    )?
    $
    """,
    re.VERBOSE,
)


def version() -> dict[str, Optional[str]]:
    """Parses the version string.

    The version string shall be of the form:
        x.y.z
        x.y.z+dev
        x.y.z+dev.HASH

    Returns:
        .. code-block:: python

            {
                "version": "0.2.0+dev.a96a65",
                "base": "x.y.z",
                "dev": "dev" or None,
                "hash": "<hash>" or None,
            }
    """
    global __version__

    match = VERSION_RE.match(__version__)
    if not match:
        raise ValueError(f"Invalid version format: {version}")

    info = match.groupdict()
    info["version"] = __version__

    return info
