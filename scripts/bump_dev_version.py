#!/usr/bin/env python3
"""
Update VERSION_BASE in version.py after a release tag.

Behavior:
- Read VERSION_BASE from version.py
- Strip ANY existing "+dev" suffix
- Append exactly one "+dev"
- Write back the updated file

This ensures:
    0.2.0        --> 0.2.0+dev
    0.2.0+dev    --> 0.2.0+dev
    0.2.0+dev+dev -> 0.2.0+dev
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "src" / "akkudoktoreos" / "core" / "version.py"


def bump_dev_version_file(file: Path) -> str:
    text = file.read_text(encoding="utf-8")

    # Extract current version
    m = re.search(r'^VERSION_BASE\s*=\s*["\']([^"\']+)["\']',
                  text, flags=re.MULTILINE)
    if not m:
        raise ValueError("VERSION_BASE not found")

    base_version = m.group(1)

    # Remove trailing +dev if present → ensure idempotency
    cleaned = re.sub(r'(\+dev)+$', '', base_version)

    # Append +dev
    new_version = f"{cleaned}+dev"

    # Replace inside file content
    new_text = re.sub(
        r'^VERSION_BASE\s*=\s*["\']([^"\']+)["\']',
        f'VERSION_BASE = "{new_version}"',
        text,
        flags=re.MULTILINE
    )

    file.write_text(new_text, encoding="utf-8")

    return new_version


def main():
    # Use CLI argument or fallback default path
    version_file = Path(sys.argv[1]) if len(sys.argv) > 1 else VERSION_FILE

    try:
        new_version = bump_dev_version_file(version_file)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # MUST print to stdout
    print(new_version)


if __name__ == "__main__":
    main()
