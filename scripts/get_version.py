"""Get version of EOS"""

import sys

if sys.version_info < (3, 11):
    print(
        f"ERROR: Python >=3.11 is required. Found {sys.version_info.major}.{sys.version_info.minor}",
        file=sys.stderr,
    )
    sys.exit(1)

from pathlib import Path

# Add the src directory to sys.path so import akkudoktoreos works in all cases
PROJECT_ROOT = Path(__file__).parent.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

if __name__ == "__main__":
    # Import here to prevent mypy to execute the functions that evaluate __version__
    try:
        from akkudoktoreos.core.version import __version__
        version = __version__
    except Exception:
        # This may be a first time install
        raise RuntimeError("Can not find out version!")
        version = "0.0.0"

    print(version)
