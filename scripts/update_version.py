#!.venv/bin/python
"""General version replacement script.

Usage:
    python scripts/update_version.py <version> <file1> [file2 ...]
"""

#!/usr/bin/env python3
import re
import sys
from pathlib import Path
from typing import List

# --- Patterns to match version strings ---
VERSION_PATTERNS = [
    # Python: __version__ = "1.2.3"
    re.compile(
        r'(?<![A-Za-z0-9])(__version__\s*=\s*")'
        r'(?P<ver>\d+\.\d+\.\d+(?:\+[0-9A-Za-z\.]+)?)'
        r'(")'
    ),

    # Python: version = "1.2.3"
    re.compile(
        r'(?<![A-Za-z0-9])(version\s*=\s*")'
        r'(?P<ver>\d+\.\d+\.\d+(?:\+[0-9A-Za-z\.]+)?)'
        r'(")'
    ),

    # JSON: "version": "1.2.3"
    re.compile(
        r'(?<![A-Za-z0-9])("version"\s*:\s*")'
        r'(?P<ver>\d+\.\d+\.\d+(?:\+[0-9A-Za-z\.]+)?)'
        r'(")'
    ),

    # Makefile-style: VERSION ?= 1.2.3
    re.compile(
        r'(?<![A-Za-z0-9])(VERSION\s*\?=\s*)'
        r'(?P<ver>\d+\.\d+\.\d+(?:\+[0-9A-Za-z\.]+)?)'
    ),

    # YAML: version: "1.2.3"
    re.compile(
        r'(?m)^(version\s*:\s*["\']?)'
        r'(?P<ver>\d+\.\d+\.\d+(?:\+[0-9A-Za-z\.]+)?)'
        r'(["\']?)\s*$'
    ),
]


def update_version_in_file(file_path: Path, new_version: str) -> bool:
    """
    Replace version strings in a file based on VERSION_PATTERNS.
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
        file_path.write_text(new_content)

    return file_would_be_updated


def main(version: str, files: List[str]):
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
    else:
        print("No files updated.")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python update_version.py <version> <file1> [file2 ...]")
        sys.exit(1)

    version_arg = sys.argv[1]
    files_arg = sys.argv[2:]
    main(version_arg, files_arg)
