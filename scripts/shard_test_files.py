#!/usr/bin/env python3
"""Split the pytest modules into shards of about equal runtime.

CI runs the tests that do not start a server in several jobs ("shards"), see
``.github/workflows/pytest.yml``. This script prints the test modules of one shard::

    python scripts/shard_test_files.py 4 0

The modules are distributed by their measured runtime, heaviest first, each to the shard
with the smallest load so far. The runtimes are stored in ``scripts/test_durations.json``
(seconds per module). A module without an entry counts one second. Every module ends up
in exactly one shard whatever the runtimes say, so stale runtimes cost balance only.

Update the runtimes from the junit reports of the shards of a CI run. Pass the reports of
all shards: the file is rewritten from them, and a module that is in none of them counts
one second again::

    python scripts/shard_test_files.py --update junit-shard-*.xml
"""

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DURATIONS_FILE = Path(__file__).resolve().with_name("test_durations.json")
DEFAULT_DURATION = 1.0

_JUNIT_MODULE = re.compile(r"^tests\.(test_[a-z0-9]+)")


def list_test_modules(repo_root: Path = REPO_ROOT) -> list[str]:
    """List the pytest modules directly in ``tests/``.

    Subdirectories are not searched; there are none with test modules. The ``test-count``
    job of ``pytest.yml`` fails if pytest collects a test that no shard runs.

    Args:
        repo_root: The root directory of the repository.

    Returns:
        The module paths relative to the repository root, sorted, with forward slashes.
    """
    return sorted(
        path.relative_to(repo_root).as_posix() for path in (repo_root / "tests").glob("test_*.py")
    )


def load_durations(path: Path = DURATIONS_FILE) -> dict[str, float]:
    """Read the stored runtimes.

    Args:
        path: The JSON file with the seconds per module.

    Returns:
        The runtimes by module path; empty if the file does not exist.
    """
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fd:
        return {str(name): float(seconds) for name, seconds in json.load(fd).items()}


def split(modules: list[str], durations: dict[str, float], shards: int) -> list[list[str]]:
    """Distribute the modules to shards of about equal runtime.

    Args:
        modules: The module paths to distribute.
        durations: The runtime in seconds by module path.
        shards: The number of shards.

    Returns:
        One list of module paths per shard. Every module is in exactly one of them.

    Raises:
        ValueError: If the number of shards is not positive.
    """
    if shards < 1:
        raise ValueError(f"The number of shards must be positive, got {shards}.")
    load = [0.0] * shards
    result: list[list[str]] = [[] for _ in range(shards)]
    # Heaviest first; the module name breaks ties, so the result is deterministic.
    for module in sorted(modules, key=lambda name: (-durations.get(name, DEFAULT_DURATION), name)):
        lightest = min(range(shards), key=lambda index: (load[index], index))
        result[lightest].append(module)
        load[lightest] += durations.get(module, DEFAULT_DURATION)
    return result


def durations_from_junit(reports: list[Path]) -> dict[str, float]:
    """Sum the test times of junit reports by pytest module.

    Args:
        reports: The junit XML files written by ``pytest --junitxml``.

    Returns:
        The runtime in seconds by module path, rounded to two decimals.
    """
    seconds: dict[str, float] = {}
    for report in reports:
        for case in ET.parse(report).getroot().iter("testcase"):
            match = _JUNIT_MODULE.match(case.get("classname", ""))
            if match:
                module = f"tests/{match.group(1)}.py"
                seconds[module] = seconds.get(module, 0.0) + float(case.get("time") or 0.0)
    return {module: round(value, 2) for module, value in sorted(seconds.items())}


def main(argv: list[str] | None = None) -> int:
    """Print the modules of one shard, or update the stored runtimes.

    Args:
        argv: The command line arguments; ``sys.argv[1:]`` if omitted.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(
        description="Split the pytest modules into shards of about equal runtime."
    )
    parser.add_argument("shards", nargs="?", type=int, help="number of shards")
    parser.add_argument("index", nargs="?", type=int, help="shard to print, counted from 0")
    parser.add_argument(
        "--update",
        nargs="+",
        type=Path,
        metavar="JUNIT_XML",
        help="rewrite the stored runtimes from these junit reports",
    )
    args = parser.parse_args(argv)

    if args.update:
        durations = durations_from_junit(args.update)
        if not durations:
            parser.error("the junit reports contain no test of a tests/test_*.py module")
        with DURATIONS_FILE.open("w", encoding="utf-8", newline="\n") as fd:
            json.dump(durations, fd, indent=1)
            fd.write("\n")
        print(f"Wrote the runtimes of {len(durations)} modules to {DURATIONS_FILE}.")
        return 0

    if args.shards is None or args.index is None:
        parser.error("give the number of shards and the index of the shard to print")
    if not 0 <= args.index < args.shards:
        parser.error(f"the shard index must be between 0 and {args.shards - 1}")
    print(" ".join(split(list_test_modules(), load_durations(), args.shards)[args.index]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
