#!.venv/bin/python
"""Get version of EOS"""

import sys
from pathlib import Path

# Add the src directory to sys.path so Sphinx can import akkudoktoreos
PROJECT_ROOT = Path(__file__).parent.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from akkudoktoreos.core.version import __version__

if __name__ == "__main__":
    print(__version__)
