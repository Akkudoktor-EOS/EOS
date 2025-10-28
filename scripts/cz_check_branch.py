#!/usr/bin/env python3
"""Branch name checker using regex (compatible with Commitizen v4.9.1).

Cross-platform + .venv aware.
"""

import os
import re
import subprocess
import sys
from pathlib import Path


def find_cz() -> str:
    venv = os.getenv("VIRTUAL_ENV")
    paths = [Path(venv)] if venv else []
    paths.append(Path.cwd() / ".venv")

    for base in paths:
        cz = base / ("Scripts" if os.name == "nt" else "bin") / ("cz.exe" if os.name == "nt" else "cz")
        if cz.exists():
            return str(cz)
    return "cz"


def main():
    # Get current branch name
    try:
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True).strip()
    except subprocess.CalledProcessError:
        print("❌ Could not determine current branch name.")
        return 1

    # Regex pattern
    pattern = r"^(feat|fix|chore|docs|refactor|test)/[a-z0-9._-]+$"

    print(f"🔍 Checking branch name '{branch}'...")
    if not re.match(pattern, branch):
        print(f"❌ Branch name '{branch}' does not match pattern '{pattern}'")
        return 1

    print("✅ Branch name is valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
