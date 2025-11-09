"""Update version strings in multiple project files only if the old version matches.

This script updates version information in:
- pyproject.toml
- src/akkudoktoreos/core/version.py
- src/akkudoktoreos/data/default.config.json
- Makefile

Supported version formats:
- __version__ = "<version>"
- version = "<version>"
- "version": "<version>"
- VERSION ?: <version>

It will:
- Replace VERSION → NEW_VERSION if the old version is found.
- Report which files were updated.
- Report which files contained mismatched versions.
- Report which files had no version.

Usage:
    python bump_version.py VERSION NEW_VERSION

Args:
    VERSION (str): Version expected before replacement.
    NEW_VERSION (str): Version to write.

"""
#!/usr/bin/env python3
import argparse
import glob
import os
import re
import shutil
from pathlib import Path
from typing import List, Tuple

# Patterns to match version strings
VERSION_PATTERNS = [
    re.compile(r'(__version__\s*=\s*")(?P<ver>[^"]+)(")'),
    re.compile(r'(version\s*=\s*")(?P<ver>[^"]+)(")'),
    re.compile(r'("version"\s*:\s*")(?P<ver>[^"]+)(")'),
    re.compile(r'(VERSION\s*\?=\s*)(?P<ver>[^\s]+)'),  # For Makefile: VERSION ?= 0.2.0
]

# Default files to process
DEFAULT_FILES = [
    "pyproject.toml",
    "src/akkudoktoreos/core/version.py",
    "src/akkudoktoreos/data/default.config.json",
    "Makefile",
]


def backup_file(file_path: str) -> str:
    """Create a backup of the given file with a .bak suffix.

    Args:
        file_path: Path to the file to backup.

    Returns:
        Path to the backup file.
    """
    backup_path = f"{file_path}.bak"
    shutil.copy2(file_path, backup_path)
    return backup_path


def replace_version_in_file(
    file_path: Path, old_version: str, new_version: str, dry_run: bool = False
) -> Tuple[bool, bool]:
    """
    Replace old_version with new_version in the given file if it matches.

    Args:
        file_path: Path to the file to modify.
        old_version: The old version to replace.
        new_version: The new version to set.
        dry_run: If True, don't actually modify files.

    Returns:
        Tuple[bool, bool]: (file_would_be_updated, old_version_found)
    """
    content = file_path.read_text()
    new_content = content
    old_version_found = False
    file_would_be_updated = False

    for pattern in VERSION_PATTERNS:
        def repl(match):
            nonlocal old_version_found, file_would_be_updated
            ver = match.group("ver")
            if ver == old_version:
                old_version_found = True
                file_would_be_updated = True
                # Some patterns have 3 groups (like quotes)
                if len(match.groups()) == 3:
                    return f"{match.group(1)}{new_version}{match.group(3)}"
                else:
                    return f"{match.group(1)}{new_version}"
            return match.group(0)

        new_content = pattern.sub(repl, new_content)

    if file_would_be_updated:
        if dry_run:
            print(f"[DRY-RUN] Would update {file_path}")
        else:
            backup_path = file_path.with_suffix(file_path.suffix + ".bak")
            shutil.copy(file_path, backup_path)
            file_path.write_text(new_content)
            print(f"Updated {file_path} (backup saved to {backup_path})")
    elif not old_version_found:
        print(f"[SKIP] {file_path}: old version '{old_version}' not found")

    return file_would_be_updated, old_version_found


def main():
    parser = argparse.ArgumentParser(description="Bump version across project files.")
    parser.add_argument("old_version", help="Old version to replace")
    parser.add_argument("new_version", help="New version to set")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be changed without modifying files"
    )
    parser.add_argument(
        "--glob", nargs="*", help="Optional glob patterns to include additional files"
    )
    args = parser.parse_args()

    updated_files = []
    not_found_files = []

    # Determine files to update
    files_to_update: List[Path] = [Path(f) for f in DEFAULT_FILES]
    if args.glob:
        for pattern in args.glob:
            files_to_update.extend(Path(".").glob(pattern))

    files_to_update = list(dict.fromkeys(files_to_update))  # remove duplicates

    any_updated = False
    for file_path in files_to_update:
        if file_path.exists() and file_path.is_file():
            updated, _ = replace_version_in_file(
                file_path, args.old_version, args.new_version, args.dry_run
            )
            any_updated |= updated
            if updated:
                updated_files.append(file_path)
        else:
            print(f"[SKIP] {file_path}: file does not exist")
            not_found_files.append(file_path)

    print("\nSummary:")
    if updated_files:
        print(f"Updated files ({len(updated_files)}):")
        for f in updated_files:
            print(f"  {f}")
    else:
        print("No files were updated.")

    if not_found_files:
        print(f"Files where old version was not found ({len(not_found_files)}):")
        for f in not_found_files:
            print(f"  {f}")


if __name__ == "__main__":
    main()
