#!/usr/bin/env python3
"""Commitizen commit message checker that is .venv aware.

Works for commits with -m or commit message file.

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


def main():
    cz = find_cz()

    # 1️⃣ Try commit-msg file (interactive commit)
    commit_msg_file = sys.argv[1] if len(sys.argv) > 1 else None

    # 2️⃣ If not file, fallback to -m message (Git sets GIT_COMMIT_MSG in some environments, or we create a temp file)
    if not commit_msg_file:
        msg = os.getenv("GIT_COMMIT_MSG") or ""
        if not msg:
            print("⚠️  No commit message file or environment message found. Skipping Commitizen check.")
            return 0
        import tempfile

        with tempfile.NamedTemporaryFile("w+", delete=False) as tmp:
            tmp.write(msg)
            tmp.flush()
            commit_msg_file = tmp.name

    print(f"🔍 Checking commit message using {cz}...")

    try:
        subprocess.check_call(cz + ["check", "--commit-msg-file", commit_msg_file])
        print("✅ Commit message follows Commitizen convention.")
        return 0
    except subprocess.CalledProcessError:
        print("❌ Commit message validation failed.")
        return 1
    finally:
        # Clean up temp file if we created one
        if 'tmp' in locals():
            os.unlink(tmp.name)


if __name__ == "__main__":
    sys.exit(main())
