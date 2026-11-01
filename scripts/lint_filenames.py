"""Lint filenames against repository-specific naming conventions.

This script validates filenames using rules configured in ``pyproject.toml``.
Rules can target directories, enforce regular-expression patterns, allow
specific filenames, set maximum filename lengths, and exclude paths.

Usage:
    Check all applicable repository files::

        uv run python scripts/lint_filenames.py

    Check selected files::

        uv run python scripts/lint_filenames.py tests/test_adapter.py

    Specify a configuration file::

        uv run python scripts/lint_filenames.py --config pyproject.toml

    Run the pre-commit hook::

        uv run pre-commit run eos-filename-linter --all-files

Configuration:
    Configure ``[tool.eos-filename-linter]`` in ``pyproject.toml``.

    Global ``exclude`` patterns exclude files and directories.
    ``max_filename_length`` sets the default maximum filename length,
    including the extension. The default is 64 characters.

    Each ``[[tool.eos-filename-linter.rules]]`` entry defines a rule with:
        name: Unique descriptive rule name.
        paths: Repository-relative glob patterns.
        pattern: Regular expression matched against the filename.
        allowed_names: Optional filenames exempt from the pattern.
        exclude: Optional additional exclusion patterns.
        max_filename_length: Optional rule-specific length limit.
        suggestion: Optional filename normalization strategy:
           test_prefix_alnum: "test_" followed by lowercase letters and digits.
           single_test_prefix_alnum: "single_test_" followed by lowercase
               letters and digits.
           lowercase_alnum: Lowercase letters and digits only, beginning
               with a letter.
           lowercase_snake: Lowercase snake_case.

    Rules are evaluated in configuration order; the first applicable rule wins.
    A rule that excludes a path is skipped, allowing a later rule to apply.
    Glob patterns support ``*``, ``?``, and ``**``.

    Test filenames must use the ``test_`` prefix followed by lowercase
    letters and digits only. For example, ``test_adapter123.py`` is valid,
    while ``test_adapter_device.py`` is not.

    Excluded paths are not checked. During repository-wide scans, excluded
    directories are pruned to avoid traversing their contents.

Exit status:
    0: No violations found.
    1: One or more filename violations found.
    2: Invalid configuration, invalid arguments, or an operational error.

The linter never renames files. Suggestions are diagnostic hints only.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

DEFAULT_MAX_FILENAME_LENGTH = 64
PYTHON_EXTENSION = ".py"

def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a repository-relative glob into a regular expression.

    Args:
        pattern: A slash-separated glob pattern.

    Returns:
        A regular expression that matches the entire relative path.
    """
    pattern = pattern.replace("\\", "/")
    parts: list[str] = []
    index = 0

    while index < len(pattern):
        if pattern[index : index + 2] == "**":
            index += 2
            if index < len(pattern) and pattern[index] == "/":
                parts.append("(?:.*/)?")
                index += 1
            else:
                parts.append(".*")
        elif pattern[index] == "*":
            parts.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(pattern[index]))
            index += 1

    return re.compile("^" + "".join(parts) + "$")

def _matches_any(path: str, patterns: list[str]) -> bool:
    """Check whether a path matches at least one glob pattern.

    Args:
        path: A repository-relative POSIX path.
        patterns: Glob patterns to evaluate.

    Returns:
        True if at least one pattern matches.
    """
    return any(_glob_to_regex(pattern).fullmatch(path) for pattern in patterns)

def _is_excluded(path: str, patterns: list[str]) -> bool:
    """Check whether a file or directory is excluded.

    A directory exclusion also excludes every descendant of that directory.

    Args:
        path: A repository-relative POSIX path.
        patterns: Glob patterns to evaluate.

    Returns:
        True if the path is excluded.
    """
    normalized = path.strip("/")
    for pattern in patterns:
        normalized_pattern = pattern.replace("\\", "/").strip("/")
        if _matches_any(normalized, [normalized_pattern]):
            return True

        # An exclusion such as "docs/generated/**" should also match
        # the directory "docs/generated" so that it can be pruned.
        prefix = normalized_pattern.removesuffix("/**").rstrip("/")
        if prefix and (
            normalized == prefix or normalized.startswith(prefix + "/")
        ):
            return True

    return False


def _normalize_alphanumeric(stem: str) -> str:
    """Convert a filename stem to lowercase alphanumeric characters.

    CamelCase word boundaries are handled before punctuation and underscores
    are removed.

    Args:
        stem: Filename stem without its extension.

    Returns:
        A lowercase alphanumeric string, or an empty string if no characters
        remain.
    """
    stem = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", stem)
    stem = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", stem)
    return re.sub(r"[^a-zA-Z0-9]", "", stem).lower()


def _normalize_snake_case(stem: str) -> str:
    """Convert a filename stem to lowercase snake_case.

    Args:
        stem: Filename stem without its extension.

    Returns:
        A lowercase snake_case string.
    """
    stem = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", stem)
    stem = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", stem)
    stem = re.sub(r"[^a-zA-Z0-9]+", "_", stem)
    return stem.strip("_").lower()


def _suggest_filename(
    filename: str,
    rule: dict[str, Any],
    max_length: int = DEFAULT_MAX_FILENAME_LENGTH,
) -> str | None:
    """Suggest a filename that complies with a configured naming rule.

    The rule's ``suggestion`` setting determines the normalization strategy:

    - ``test_prefix_alnum``: Require ``test_`` followed by lowercase letters
      and digits.
    - ``lowercase_alnum``: Use lowercase letters and digits only.
    - ``lowercase_snake``: Use lowercase snake_case.

    The generated candidate is checked against the rule's regular expression
    and maximum filename length before it is returned.

    Args:
        filename: Original filename, including its extension.
        rule: Applicable naming rule.
        max_length: Maximum filename length, including the extension.

    Returns:
        A compliant suggested filename, or None if a valid suggestion cannot
        be generated.
    """
    path = Path(filename)
    strategy = rule.get("suggestion")
    pattern = rule.get("pattern")

    if not isinstance(pattern, str):
        return None

    extension = path.suffix.lower()
    stem = path.stem

    if strategy == "test_prefix_alnum":
        prefix = "test_"
        if stem.startswith(prefix):
            stem = stem[len(prefix):]

        normalized = _normalize_alphanumeric(stem) or "module"
        available = max_length - len(prefix) - len(extension)

        if available < 1:
            return None

        candidate = f"{prefix}{normalized[:available]}{extension}"

    elif strategy == "single_test_prefix_alnum":
        prefix = "single_test_"
        if stem.startswith(prefix):
            stem = stem[len(prefix):]

        normalized = _normalize_alphanumeric(stem) or "module"
        available = max_length - len(prefix) - len(extension)

        if available < 1:
            return None

        candidate = f"{prefix}{normalized[:available]}{extension}"

    elif strategy == "lowercase_alnum":
        normalized = _normalize_alphanumeric(stem) or "module"

        if not normalized[0].isalpha():
            normalized = f"module{normalized}"

        available = max_length - len(extension)
        if available < 1:
            return None

        candidate = f"{normalized[:available]}{extension}"

    elif strategy == "lowercase_snake":
        normalized = _normalize_snake_case(stem) or "module"
        available = max_length - len(extension)

        if available < 1:
            return None

        candidate = f"{normalized[:available].rstrip('_') or 'm'}{extension}"

    else:
        # Unknown strategies should not silently produce misleading names.
        return None

    if len(candidate) > max_length:
        return None

    if re.fullmatch(pattern, candidate) is None:
        return None

    return candidate


def _load_config(config_path: Path) -> dict[str, Any]:
    """Load and validate the filename linter configuration.

    Args:
        config_path: Path to the TOML configuration file.

    Returns:
        The linter's configuration dictionary.

    Raises:
        ValueError: If required configuration values are invalid.
        OSError: If the configuration cannot be read.
        tomllib.TOMLDecodeError: If TOML parsing fails.
    """
    with config_path.open("rb") as config_file:
        document = tomllib.load(config_file)

    config = document.get("tool", {}).get("eos-filename-linter")
    if not isinstance(config, dict):
        raise ValueError("Missing [tool.eos-filename-linter] configuration.")

    global_limit = config.get(
        "max_filename_length", DEFAULT_MAX_FILENAME_LENGTH
    )
    if type(global_limit) is not int or global_limit < 1:
        raise ValueError("max_filename_length must be a positive integer.")

    for key in ("exclude",):
        if key in config and (
            not isinstance(config[key], list)
            or not all(isinstance(item, str) for item in config[key])
        ):
            raise ValueError(f"{key} must be a list of strings.")

    rules = config.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("At least one filename linter rule is required.")

    seen_names: set[str] = set()
    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            raise ValueError(f"Rule {index} must be a TOML table.")

        name = rule.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"Rule {index} requires a non-empty name.")
        if name in seen_names:
            raise ValueError(f"Duplicate rule name: {name!r}.")
        seen_names.add(name)

        paths = rule.get("paths")
        if not isinstance(paths, list) or not paths or not all(
            isinstance(item, str) and item for item in paths
        ):
            raise ValueError(f"Rule {name!r} requires non-empty path patterns.")

        pattern = rule.get("pattern")
        if not isinstance(pattern, str):
            raise ValueError(f"Rule {name!r} requires a regex pattern.")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"Rule {name!r} has invalid regex: {exc}") from exc

        for key in ("allowed_names", "exclude"):
            if key in rule and (
                not isinstance(rule[key], list)
                or not all(isinstance(item, str) for item in rule[key])
            ):
                raise ValueError(f"Rule {name!r}: {key} must be a list of strings.")

        limit = rule.get("max_filename_length", global_limit)
        if type(limit) is not int or limit < 1:
            raise ValueError(
                f"Rule {name!r}: max_filename_length must be a positive integer."
            )

        suggestion = rule.get("suggestion")
        valid_suggestions = {
            "test_prefix_alnum",
            "single_test_prefix_alnum",
            "lowercase_alnum",
            "lowercase_snake",
        }

        if suggestion is not None and suggestion not in valid_suggestions:
            raise ValueError(
                f"Rule {name!r}: unknown suggestion strategy "
                f"{suggestion!r}. Expected one of "
                f"{sorted(valid_suggestions)}."
            )

    return config

def _relative_path(path: str, root: Path) -> str | None:
    """Resolve a path to a repository-relative POSIX path.

    Args:
        path: The supplied path.
        root: The repository root.

    Returns:
        A relative path, or None if the path is outside the repository.
    """
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None

def _find_rule(
    relative_path: str, rules: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Return the first applicable rule matching a repository-relative path.

    A rule is applicable when its ``paths`` patterns match the path and the
    path is not excluded by that rule's ``exclude`` patterns. Excluded rules
    are skipped so that a later, more specific rule can still apply to the
    same path. (For example, a general ``tests/**/*.py`` rule that excludes
    ``tests/single_test_*`` must not shadow the dedicated single-test rule.)

    Args:
        relative_path: The path to match.
        rules: Rules in configuration order.

    Returns:
        The first applicable rule, or None.
    """
    for rule in rules:
        if not _matches_any(relative_path, rule["paths"]):
            continue
        if _is_excluded(relative_path, rule.get("exclude", [])):
            continue
        return rule
    return None

def _iter_repository_files(
    root: Path, config: dict[str, Any]
) -> list[str]:
    """Find repository files while pruning excluded directories.

    Args:
        root: Repository root.
        config: Validated linter configuration.

    Returns:
        Repository-relative paths for candidate files.
    """
    global_excludes = config.get("exclude", [])
    rules = config["rules"]
    paths: list[str] = []

    for current_root, directory_names, filenames in os.walk(root):
        current = Path(current_root)
        relative_dir = current.relative_to(root).as_posix()
        if relative_dir == ".":
            relative_dir = ""

        directory_names[:] = [
            name
            for name in directory_names
            if not _is_excluded(
                f"{relative_dir}/{name}".lstrip("/"), global_excludes
            )
        ]

        for filename in filenames:
            relative_path = f"{relative_dir}/{filename}".lstrip("/")
            if _is_excluded(relative_path, global_excludes):
                continue
            if _find_rule(relative_path, rules) is not None:
                paths.append(relative_path)

    return paths

def lint_paths(
    paths: list[str],
    root: Path,
    config: dict[str, Any],
) -> int:
    """Validate filenames and print diagnostics for violations.

    Args:
        paths: Files to check.
        root: Repository root.
        config: Validated linter configuration.

    Returns:
        The number of violations found.
    """
    global_excludes = config.get("exclude", [])
    global_limit = config.get(
        "max_filename_length", DEFAULT_MAX_FILENAME_LENGTH
    )
    rules = config["rules"]
    violations = 0

    for supplied_path in paths:
        relative_path = _relative_path(supplied_path, root)
        if relative_path is None:
            print(f"ERROR: {supplied_path}: outside repository root {root}", file=sys.stderr)
            violations += 1
            continue

        if _is_excluded(relative_path, global_excludes):
            continue

        # ``_find_rule`` already skips rules whose own ``exclude`` patterns
        # match the path, so a rule returned here is applicable.
        rule = _find_rule(relative_path, rules)
        if rule is None:
            continue

        filename = Path(relative_path).name
        max_length = rule.get("max_filename_length", global_limit)
        allowed_names = rule.get("allowed_names", [])
        pattern_matches = re.fullmatch(rule["pattern"], filename) is not None
        too_long = len(filename) > max_length

        if filename in allowed_names and not too_long:
            continue
        if pattern_matches and not too_long:
            continue

        suggestion = _suggest_filename(filename, rule, max_length)

        print(f"{relative_path}")
        if not pattern_matches and filename not in allowed_names:
            print(f"  ERROR: Filename violates rule {rule['name']!r}.")
            print(f"  Expected pattern: {rule['pattern']}")

        if too_long:
            print(
                f"  ERROR: Filename is {len(filename)} characters long; "
                f"maximum is {max_length}."
            )

        if suggestion is not None:
            print(f"  Suggested name:   {suggestion}")
        else:
            print("  Suggested name:   No safe suggestion available.")

        violations += 1

    return violations

def main(argv: list[str] | None = None) -> int:
    """Run the filename linter command-line interface.

    Args:
        argv: Optional command-line arguments.

    Returns:
        Exit status: 0 for success, 1 for violations, or 2 for errors.
    """
    parser = argparse.ArgumentParser(
        description="Lint filenames using Akkudoktor-EOS naming conventions."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("pyproject.toml"),
        help="Path to pyproject.toml.",
    )
    parser.add_argument("paths", nargs="*", help="Files to check.")
    args = parser.parse_args(argv)

    config_path = args.config.resolve()
    try:
        config = _load_config(config_path)
        root = config_path.parent.resolve()
        paths = args.paths or _iter_repository_files(root, config)
        violations = lint_paths(paths, root, config)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"Filename linter error: {exc}", file=sys.stderr)
        return 2

    if violations:
        print(f"\nFound {violations} filename violation(s).", file=sys.stderr)
        return 1

    print("Filename lint passed.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
