"""Version information for akkudoktoreos."""

# -----------------------------------------------------------------
# version.py may be used __BEFORE__ the dependencies are installed.
# Use only standard python libraries
#
# Several warnings/ erros are silenced because they are
# non-critical in this context - see noqa.
# -----------------------------------------------------------------

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import (  # Don't use akkudoktoreos.utils.datetimeutil (-> pendulum)
    datetime,
    timezone,
)
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional

# For development add `.dev` to previous release
# For release omit `.dev`.
VERSION_BASE = "0.2.0.dev"

# Project hash of relevant files
HASH_EOS = ""

# Number of hash digits to append to .dev to identify a development version
VERSION_DEV_HASH_PRECISION = 8

# File to hold the date of the latest commit.
VERSION_DATE_FILE = Path(__file__).parent / "_version_date.py"

# Hashing configuration
DIR_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
ALLOWED_SUFFIXES: set[str] = {".py", ".md", ".json"}
EXCLUDED_DIR_PATTERNS: set[str] = {"*_autosum", "*__pycache__", "*_generated"}
# Excluded from hash/date calculation to avoid self-referencing loop
EXCLUDED_FILES: set[Path] = {VERSION_DATE_FILE}

IS_GITHUB_ACTIONS = os.getenv("GITHUB_ACTIONS") == "true"


# ------------------------------
# Helpers for version generation
# ------------------------------


@dataclass
class HashConfig:
    """Configuration for file hashing."""

    paths: list[Path]
    allowed_suffixes: set[str]
    excluded_dir_patterns: set[str]
    excluded_files: set[Path]

    def __post_init__(self) -> None:
        """Validate configuration."""
        for path in self.paths:
            if not path.exists():
                raise ValueError(f"Path does not exist: {path}")
        # Normalize exclude files (for easy comparison)
        self.excluded_files = {p.resolve() for p in self.excluded_files}


def is_excluded_dir(path: Path, patterns: set[str]) -> bool:
    """Check if directory matches any exclusion pattern.

    Args:
        path: Directory path to check
        patterns: set of glob-like patterns (e.g., {``*__pycache__``, ``*_test``})

    Returns:
        True if directory should be excluded
    """
    dir_name = path.name
    return any(fnmatch(dir_name, pattern) for pattern in patterns)


def collect_files(config: HashConfig) -> list[Path]:
    """Collect all files that should be included in the hash.

    This function only collects files - it doesn't hash them.
    Makes it easy to inspect what will be hashed.

    Args:
        config: Hash configuration

    Returns:
        Sorted list of files to be hashed

    Example:
        >>> config = HashConfig(
        ...     paths=[Path('src')],
        ...     allowed_suffixes={'.py'},
        ...     excluded_dir_patterns={'*__pycache__'},
        ...     excluded_files=set()
        ... )
        >>> files = collect_files(config)
        >>> print(f"Will hash {len(files)} files")
        >>> for f in files[:5]:
        ...     print(f"  {f}")
    """
    collected_files: list[Path] = []

    for root in config.paths:
        for p in sorted(root.rglob("*")):
            # Skip directories that match exclusion
            if p.is_dir() and is_excluded_dir(p, config.excluded_dir_patterns):
                continue

            # Skip files inside excluded directories
            if any(is_excluded_dir(parent, config.excluded_dir_patterns) for parent in p.parents):
                continue

            if not p.is_file():
                continue

            if p.suffix.lower() not in config.allowed_suffixes:
                continue

            resolved_p = p.resolve()

            # Skip excluded files (already resolved in config)
            if resolved_p in config.excluded_files:
                continue

            collected_files.append(resolved_p)

    return sorted(collected_files)


def hash_files(files: list[Path]) -> str:
    """Calculate SHA256 hash of file contents.

    Args:
        files: list of files to hash (order matters!)

    Returns:
        SHA256 hex digest

    Example:
        >>> files = [Path('file1.py'), Path('file2.py')]
        >>> hash_value = hash_files(files)
    """
    h = hashlib.sha256()

    for file_path in files:
        if not file_path.exists():
            continue

        h.update(file_path.read_bytes())

    return h.hexdigest()


def hash_tree(
    paths: list[Path],
    allowed_suffixes: set[str],
    excluded_dir_patterns: set[str],
    excluded_files: Optional[set[Path]] = None,
) -> tuple[str, list[Path]]:
    """Return SHA256 hash for files under `paths` and the list of files hashed.

    Args:
        paths: list of root paths to hash
        allowed_suffixes: set of file suffixes to include (e.g., {'.py', '.json'})
        excluded_dir_patterns: set of directory patterns to exclude
        excluded_files: Optional set of specific files to exclude

    Returns:
        tuple of (hash_digest, list_of_hashed_files)

    Example:
        >>> hash_digest, files = hash_tree(
        ...     paths=[Path('src')],
        ...     allowed_suffixes={'.py'},
        ...     excluded_dir_patterns={'*__pycache__'},
        ... )
        >>> print(f"Hash: {hash_digest}")
        >>> print(f"Based on {len(files)} files")
    """
    config = HashConfig(
        paths=paths,
        allowed_suffixes=allowed_suffixes,
        excluded_dir_patterns=excluded_dir_patterns,
        excluded_files=excluded_files or set(),
    )

    files = collect_files(config)
    digest = hash_files(files)

    return digest, files


def newest_commit_or_dirty_datetime(files: list[Path]) -> datetime:
    """Return the newest relevant datetime for the given files in UTC.

    Checks for uncommitted changes among the given files first. If any file
    has staged or unstaged modifications, the current UTC datetime is returned
    to reflect that the working tree is ahead of the last commit. Otherwise,
    the datetime of the most recent git commit touching any of the given files
    is returned. If git is unavailable (e.g. after pip install), falls back to
    reading the date from VERSION_DATE_FILE.

    Args:
        files: List of file paths to check for changes and commit history.

    Returns:
        The current UTC datetime if any file has uncommitted changes, otherwise
        the UTC datetime of the most recent commit touching any of the given
        files, or the datetime stored in VERSION_DATE_FILE as a last resort.

    Raises:
        RuntimeError: If no version date can be determined from any source.
    """
    # Check for uncommitted changes among watched files.
    # When running on GitHub, only the version date file is checked. The
    # development tag is merely a label, so any date set during development suffices.
    if not IS_GITHUB_ACTIONS:
        try:
            status_result = subprocess.run(  # noqa: S603
                ["git", "status", "--porcelain", "--"] + [str(f) for f in files],
                capture_output=True,
                text=True,
                check=True,
                cwd=DIR_PACKAGE_ROOT,
            )
            if status_result.stdout.strip():
                return datetime.now(tz=timezone.utc)

            result = subprocess.run(  # noqa: S603
                ["git", "log", "-1", "--format=%ct", "--"] + [str(f) for f in files],
                capture_output=True,
                text=True,
                check=True,
                cwd=DIR_PACKAGE_ROOT,
            )
            ts = result.stdout.strip()
            if ts:
                return datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except (subprocess.CalledProcessError, FileNotFoundError, ValueError):  # noqa: S110
            pass

    # Fallback to VERSION_DATE_FILE
    if VERSION_DATE_FILE.exists():
        try:
            ns: dict[str, str] = {}
            exec(VERSION_DATE_FILE.read_text(), {}, ns)  # noqa: S102
            date_str = ns.get("VERSION_DATE")
            if date_str:
                dt = datetime.fromisoformat(date_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)  # treat naive as UTC, don't convert
                return dt.astimezone(timezone.utc)
        except Exception:  # noqa: S110
            pass

    raise RuntimeError("This should not happen - No version date info available")


# ---------------------
# Version hash function
# ---------------------


def _version_date_hash() -> tuple[datetime, str]:
    """Calculate project date and hash.

    Only package files in src/akkudoktoreos can be hashed to make it work also for packages.

    Returns:
        lattest commit date and SHA256 hash of the project files
    """
    if not str(DIR_PACKAGE_ROOT).endswith("src/akkudoktoreos"):
        error_msg = f"DIR_PACKAGE_ROOT does not end with src/akkudoktoreos: {DIR_PACKAGE_ROOT}"
        raise ValueError(error_msg)

    # Configuration
    watched_paths = [DIR_PACKAGE_ROOT]

    # Collect files and calculate hash
    hash_digest, hashed_files = hash_tree(
        watched_paths,
        ALLOWED_SUFFIXES,
        EXCLUDED_DIR_PATTERNS,
        excluded_files=EXCLUDED_FILES,
    )

    date = newest_commit_or_dirty_datetime(hashed_files)

    return date, hash_digest


def _version_calculate() -> str:
    """Calculate the full version string.

    For release versions: "x.y.z"
    For dev versions: "x.y.z.dev<date><hash>"

    Returns:
        Full version string
    """
    if VERSION_BASE.endswith(".dev"):
        # After dev only digits are allowed - convert hexdigest to digits
        version_date, version_hash = _version_date_hash()
        hash_value = int(version_hash, 16)
        hash_digits = str(hash_value % (10**VERSION_DEV_HASH_PRECISION)).zfill(
            VERSION_DEV_HASH_PRECISION
        )
        date_digits = version_date.strftime("%y%m%d%H") if version_date else "00000000"
        return f"{VERSION_BASE}{date_digits}{hash_digits}"
    else:
        # Release version - use base as-is
        return VERSION_BASE


# ---------------------------
# Project version information
# ---------------------------

# The version
__version__ = _version_calculate()


# -------------------
# Version info access
# -------------------

# Regular expression to split the version string into pieces
VERSION_RE = re.compile(
    r"""
    ^(?P<base>\d+\.\d+\.\d+)       # x.y.z
    (?:\.dev                       # literal '.dev' for development versions
        (?P<date>\d{8})            # 8-digit date: YYMMDDHH
        (?P<hash>[a-f0-9]+)?       # hex hash
    )?
    $
    """,
    re.VERBOSE,
)


def version() -> dict[str, Optional[str]]:
    """Parses the version string.

    The version string shall be of the form:
        x.y.z
        x.y.z.dev
        x.y.z.dev<date><hash>

    Returns:
        .. code-block:: python

            {
                "version": "0.2.0.dev.a96a65",
                "base": "x.y.z",
                "dev": "dev" or None,
                "hash": "<hash>" or None,
            }
    """
    global __version__

    match = VERSION_RE.match(__version__)
    if not match:
        raise ValueError(f"Invalid version format: {__version__}")  # Fixed: was 'version'

    info = match.groupdict()
    info["version"] = __version__

    return info
