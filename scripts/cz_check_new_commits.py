#!/usr/bin/env python3
"""Pre-push hook: Commitizen check for *new commits only*.

Cross-platform + virtualenv-aware:
- Prefers activated virtual environment (VIRTUAL_ENV)
- Falls back to ./.venv if found
- Falls back to global cz otherwise
"""

import os
import subprocess
import sys
from pathlib import Path


def find_cz_executable() -> str:
    """Return path to Commitizen executable, preferring virtual environments."""
    # 1️⃣ Active virtual environment (if running inside one)
    venv_env = os.getenv("VIRTUAL_ENV")
    if venv_env:
        cz_path = Path(venv_env) / ("Scripts" if os.name == "nt" else "bin") / ("cz.exe" if os.name == "nt" else "cz")
        if cz_path.exists():
            return str(cz_path)

    # 2️⃣ Local .venv in repo root
    repo_venv = Path.cwd() / ".venv"
    cz_path = repo_venv / ("Scripts" if os.name == "nt" else "bin") / ("cz.exe" if os.name == "nt" else "cz")
    if cz_path.exists():
        return str(cz_path)

    # 3️⃣ Global fallback
    return "cz"


def get_merge_base() -> str | None:
    """Return merge-base between HEAD and upstream branch, or None if unavailable."""
    try:
        return (
            subprocess.check_output(
                ["git", "merge-base", "@{u}", "HEAD"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            .strip()
        )
    except subprocess.CalledProcessError:
        return None


def main() -> int:
    cz = find_cz_executable()
    base = get_merge_base()

    if not base:
        print("⚠️  No upstream found; skipping Commitizen check for new commits.")
        return 0

    print(f"🔍 Using {cz} to check new commits from {base}..HEAD ...")

    try:
        subprocess.check_call([cz, "check", "--rev-range", f"{base}..HEAD"])
        print("✅ All new commits follow Commitizen conventions.")
        return 0
    except subprocess.CalledProcessError as e:
        print("❌ Commitizen check failed for one or more new commits.")
        return e.returncode


if __name__ == "__main__":
    sys.exit(main())
