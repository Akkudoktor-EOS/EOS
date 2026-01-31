#!/usr/bin/env python3
"""Commitizen commit message checker that is .venv aware.

Works for commits with -m or commit message file.
"""

import os
import subprocess
import sys
from pathlib import Path


def find_cz() -> str:
    """Find Commitizen executable, preferring virtualenv."""
    venv = os.getenv("VIRTUAL_ENV")
    paths = []
    if venv:
        paths.append(Path(venv))
    paths.append(Path.cwd() / ".venv")

    for base in paths:
        cz = base / ("Scripts" if os.name == "nt" else "bin") / ("cz.exe" if os.name == "nt" else "cz")
        if cz.exists():
            return str(cz)
    return "cz"


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
        subprocess.check_call([cz, "check", "--commit-msg-file", commit_msg_file])
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
