#!/usr/bin/env python3
"""Pre-push hook: Commitizen check for *new commits only*.

Cross-platform + uv/.venv aware:
- Prefers activated virtual environment (VIRTUAL_ENV)
- Falls back to uv-managed .uv/venv
- Falls back to .venv
- Falls back to global cz
"""

import os
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
    cz = find_cz()
    base = get_merge_base()

    if not base:
        print("⚠️  No upstream found; skipping Commitizen check {cz} for new commits.")
        return 0

    print(f"🔍 Using {cz} to check new commits from {base}..HEAD ...")

    try:
        subprocess.check_call(cz + ["check", "--rev-range", f"{base}..HEAD"])
        print("✅ All new commits follow Commitizen conventions.")
        return 0
    except subprocess.CalledProcessError as e:
        print("❌ Commitizen check failed for one or more new commits.")
        return e.returncode


if __name__ == "__main__":
    sys.exit(main())
