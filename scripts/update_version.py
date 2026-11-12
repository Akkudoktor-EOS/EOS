#!/usr/bin/env python3
"""General version replacement script.

Usage:
    python scripts/update_version.py <version> [<file1> [file2 ...]]

Without files, the version is replaced in DEFAULT_VERSION_FILES.

If the version string changed in one of the project's version files
(DEFAULT_VERSION_FILES), a new version was created and
``src/akkudoktoreos/core/_version_date.py`` is regenerated. Its
VERSION_DATE is the date ``_version_date_hash()`` computes: the date of the
last commit touching package files, or the current UTC time if package files
have uncommitted changes (a commit is expected soon).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from datetime import datetime  # type of the dates of akkudoktoreos.core.version

PROJECT_ROOT = Path(__file__).parent.parent

# Add the src directory to sys.path so import akkudoktoreos works in all cases
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

DEFAULT_VERSION_FILES = [
    PROJECT_ROOT / ".env",          # Docker compose default environment
    PROJECT_ROOT / "config.yaml",   # Home Assistant config
]


# --- Patterns to match version strings ---
VERSION_PATTERNS = [
    # Python: __version__ = "1.2.3"
    re.compile(
        r'(?<![A-Za-z0-9_])(__version__\s*=\s*")'
        r'(?P<ver>\d+\.\d+\.\d+(?:[\.\+\-][0-9A-Za-z]+)?)'
        r'(")'
    ),

    # Python: version = "1.2.3"
    re.compile(
        r'(?<![A-Za-z0-9_])(version\s*=\s*")'
        r'(?P<ver>\d+\.\d+\.\d+(?:[\.\+\-][0-9A-Za-z]+)?)'
        r'(")'
    ),

    # JSON: "version": "1.2.3"
    re.compile(
        r'(?<![A-Za-z0-9_])("version"\s*:\s*")'
        r'(?P<ver>\d+\.\d+\.\d+(?:[\.\+\-][0-9A-Za-z]+)?)'
        r'(")'
    ),

    # Makefile-style: VERSION ?= 1.2.3
    re.compile(
        r'(?<![A-Za-z0-9_])(VERSION\s*\?=\s*)'
        r'(?P<ver>\d+\.\d+\.\d+(?:[\.\+\-][0-9A-Za-z]+)?)'
    ),

    # Environment-style: VERSION = 1.2.3
    re.compile(
        r'(?<![A-Za-z0-9_])(VERSION\s*\=\s*)'
        r'(?P<ver>\d+\.\d+\.\d+(?:[\.\+\-][0-9A-Za-z]+)?)'
    ),

    # YAML: version: "1.2.3"
    re.compile(
        r'(?m)^(version\s*:\s*["\']?)'
        r'(?P<ver>\d+\.\d+\.\d+(?:[\.\+\-][0-9A-Za-z]+)?)'
        r'(["\']?)\s*$'
    ),
]


def update_version_in_file(file_path: Path, new_version: str) -> bool:
    """Replace version strings in a file based on VERSION_PATTERNS.

    Returns True if the file was updated.
    """
    content = file_path.read_text()
    new_content = content
    file_would_be_updated = False

    for pattern in VERSION_PATTERNS:
        def repl(match):
            nonlocal file_would_be_updated
            ver = match.group("ver")
            if ver != new_version:
                file_would_be_updated = True

                # Three-group patterns (__version__, JSON, YAML)
                if len(match.groups()) == 3:
                    return f"{match.group(1)}{new_version}{match.group(3)}"

                # Two-group patterns (Makefile)
                return f"{match.group(1)}{new_version}"

            return match.group(0)

        new_content = pattern.sub(repl, new_content)

    if file_would_be_updated:
        with file_path.open("w", encoding="utf-8", newline="\n") as file:
            file.write(new_content)

    return file_would_be_updated


def write_version_date(version_date: datetime, date_file: Path) -> bool:
    """Write VERSION_DATE to a version date file if the version date changes.

    The file is left untouched when it already holds a date in the
    VERSION_DATE_FORMAT that gives the same dev version date digits (the same
    hour) as ``version_date``.

    Args:
        version_date: Version date to write; naive datetimes are taken as UTC.
        date_file: Path of the version date file.

    Returns:
        True if the file was written.
    """
    from akkudoktoreos.core.version import (
        format_version_date_file,
        parse_version_date_file,
        version_date_digits,
    )

    if date_file.exists():
        stored_content = date_file.read_text(encoding="utf-8")
        stored_date = parse_version_date_file(stored_content)
        if (
            stored_date is not None
            and stored_content == format_version_date_file(stored_date)
            and version_date_digits(stored_date) == version_date_digits(version_date)
        ):
            return False

    with date_file.open("w", encoding="utf-8", newline="\n") as file:
        file.write(format_version_date_file(version_date))
    return True


def update_version_date_file() -> None:
    """Write the current version date to _version_date.py if it changes."""
    from akkudoktoreos.core.version import VERSION_DATE_FILE, _version_date_hash

    version_date, _ = _version_date_hash()
    if write_version_date(version_date, VERSION_DATE_FILE):
        print(f"Updated {VERSION_DATE_FILE}: {VERSION_DATE_FILE.read_text(encoding='utf-8').strip()}")
    else:
        print(f"No change to {VERSION_DATE_FILE}")


def main(version: str, files: List[str]) -> None:
    """Replace the version string in the given files.

    If the version string changed in one of the project's version files
    (DEFAULT_VERSION_FILES), the version date file is regenerated as well.

    Args:
        version: New version string.
        files: Files to update.

    Raises:
        ValueError: If no version or no files are given.
    """
    if not version:
        raise ValueError("No version provided")
    if not files:
        raise ValueError("No files provided")

    updated_files = []
    for f in files:
        path = Path(f)
        if not path.exists():
            print(f"Warning: {path} does not exist, skipping")
            continue
        if update_version_in_file(path, version):
            updated_files.append(str(path))

    if updated_files:
        print(f"Updated files: {', '.join(updated_files)}")
        version_files = {path.resolve() for path in DEFAULT_VERSION_FILES}
        if any(Path(f).resolve() in version_files for f in updated_files):
            update_version_date_file()
    else:
        print("No files updated.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python update_version.py <version> [<file1> [file2 ...]]")
        sys.exit(1)

    version_arg = sys.argv[1]
    if len(sys.argv) == 2:
        files_arg = [str(f) for f in DEFAULT_VERSION_FILES]
    else:
        files_arg = sys.argv[2:]
    main(version_arg, files_arg)
