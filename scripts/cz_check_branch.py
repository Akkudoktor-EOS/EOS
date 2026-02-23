#!/usr/bin/env python3
"""Branch name checker using regex (compatible with Commitizen v4.9.1).

Cross-platform + .venv aware.
"""

import os
import re
import subprocess
import sys
from pathlib import Path


def find_cz() -> list[str]:
    """Return command to invoke Commitizen via virtualenv or globally."""
    candidates = []

    # 1️⃣ Currently active virtualenv
    venv = os.getenv("VIRTUAL_ENV")
    if venv:
        candidates.append(Path(venv))

    # 2️⃣ uv-managed virtualenv
    uv_venv = Path(".uv") / "venv"
    if uv_venv.exists():
        candidates.append(uv_venv)

    # 3️⃣ traditional .venv
    dot_venv = Path(".venv")
    if dot_venv.exists():
        candidates.append(dot_venv)

    # Check each candidate for Commitizen binary
    for base in candidates:
        cz_path = base / ("Scripts" if os.name == "nt" else "bin") / ("cz.exe" if os.name == "nt" else "cz")
        if cz_path.exists():
            return [str(cz_path)]

    # 4️⃣ fallback to uv run cz
    try:
        subprocess.run(["uv", "run", "cz", "--version"], check=True, stdout=subprocess.DEVNULL)
        return ["uv", "run", "cz"]
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # 5️⃣ fallback to system cz
    return ["cz"]


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
