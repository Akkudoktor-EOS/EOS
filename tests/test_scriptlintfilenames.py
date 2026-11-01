"""Tests for the EOS filename linter."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest


def _load_linter_module() -> ModuleType:
    """Load the filename linter directly from its source file.

    Returns:
        The imported filename linter module.

    Raises:
        ImportError: If the module cannot be loaded.
    """
    script_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "lint_filenames.py"
    )
    spec = importlib.util.spec_from_file_location(
        "lint_filenames",
        script_path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load filename linter from {script_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_linter = _load_linter_module()

_find_rule = _linter._find_rule
_glob_to_regex = _linter._glob_to_regex
_is_excluded = _linter._is_excluded
_load_config = _linter._load_config
_suggest_filename = _linter._suggest_filename
lint_paths = _linter.lint_paths


@pytest.fixture
def layered_config() -> dict[str, object]:
    """Provide a general tests rule plus a dedicated single-test rule.

    The general rule excludes ``single_test_*`` files so that the
    dedicated rule must be reached for them (regression guard for
    rule-level exclusions during rule selection).
    """
    return {
        "max_filename_length": 64,
        "exclude": [],
        "rules": [
            {
                "name": "tests",
                "paths": ["tests/**/*.py"],
                "pattern": r"test_[a-z0-9]+\.py",
                "allowed_names": ["conftest.py"],
                "exclude": ["tests/single_test_*.py"],
            },
            {
                "name": "single_test",
                "paths": ["tests/single_test_*.py"],
                "pattern": r"single_test_[a-z0-9]+\.py",
                "suggestion": "single_test_prefix_alnum",
            },
        ],
    }


class TestGlobMatching:
    """Tests for repository-relative glob matching."""

    def test_matches_direct_child(self) -> None:
        """Match a Python file directly inside a directory."""
        assert _glob_to_regex("tests/*.py").fullmatch("tests/testfoo.py")

    def test_matches_nested_child(self) -> None:
        """Match nested Python files using a double-star glob."""
        assert _glob_to_regex("tests/**/*.py").fullmatch(
            "tests/adapter/testfoo.py"
        )

    def test_matches_zero_nested_directories(self) -> None:
        """Allow a double-star glob to match files at its root."""
        assert _glob_to_regex("tests/**/*.py").fullmatch("tests/testfoo.py")

    def test_does_not_match_different_directory(self) -> None:
        """Reject paths outside the configured directory."""
        assert not _glob_to_regex("tests/**/*.py").fullmatch(
            "src/testfoo.py"
        )

class TestExclusions:
    """Tests for file and directory exclusions."""

    @pytest.mark.parametrize(
        ("path", "patterns", "expected"),
        [
            (".venv/lib/module.py", [".venv/**"], True),
            ("docs/_build/index.html", ["docs/_build/**"], True),
            ("tests/testfoo.py", ["docs/**"], False),
            ("docs/generated", ["docs/generated/**"], True),
            ("docs/generated/page.html", ["docs/generated/**"], True),
        ],
    )
    def test_exclusion_matching(
        self, path: str, patterns: list[str], expected: bool
    ) -> None:
        """Match exclusions against files and directory paths."""
        assert _is_excluded(path, patterns) is expected


class TestRuleMatching:
    """Tests for selecting the first applicable rule."""

    def test_selects_first_matching_rule(self) -> None:
        """Preserve configured rule precedence."""
        rules = [
            {"name": "tests", "paths": ["tests/**/*.py"]},
            {"name": "python", "paths": ["**/*.py"]},
        ]

        assert _find_rule("tests/testfoo.py", rules) is rules[0]

    def test_returns_none_without_matching_rule(self) -> None:
        """Ignore paths that have no applicable naming rule."""
        assert _find_rule("README.md", []) is None

    def test_skips_rule_that_excludes_the_path(self) -> None:
        """Fall through to the next rule when a rule excludes the path."""
        rules = [
            {
                "name": "tests",
                "paths": ["tests/**/*.py"],
                "exclude": ["tests/single_test_*.py"],
            },
            {
                "name": "single_test",
                "paths": ["tests/single_test_*.py"],
            },
        ]

        assert (
            _find_rule("tests/single_test_adapter.py", rules) is rules[1]
        )

    def test_returns_none_when_all_matching_rules_exclude_path(self) -> None:
        """Return None when every path-matching rule excludes the path."""
        rules = [
            {
                "name": "tests",
                "paths": ["tests/**/*.py"],
                "exclude": ["tests/single_test_*.py"],
            },
            {
                "name": "single_test",
                "paths": ["tests/single_test_*.py"],
                "exclude": ["tests/single_test_*.py"],
            },
        ]

        assert _find_rule("tests/single_test_adapter.py", rules) is None

    def test_does_not_skip_rule_whose_exclusion_does_not_match(self) -> None:
        """Keep the first rule when its exclusion does not apply."""
        rules = [
            {
                "name": "tests",
                "paths": ["tests/**/*.py"],
                "exclude": ["tests/single_test_*.py"],
            },
            {
                "name": "single_test",
                "paths": ["tests/single_test_*.py"],
            },
        ]

        assert _find_rule("tests/test_adapter.py", rules) is rules[0]


class TestFilenameSuggestions:
    """Tests for configuration-driven filename suggestions."""

    @pytest.mark.parametrize(
        ("filename", "rule", "expected"),
        [
            (
                "SMAControl.py",
                {
                    "name": "Python module",
                    "suggestion": "lowercase_snake",
                    "pattern": r"[a-z][a-z0-9_]*\.py",
                },
                "sma_control.py",
            ),
            (
                "test_adapter_device.py",
                {
                    "name": "Pytest module",
                    "suggestion": "test_prefix_alnum",
                    "pattern": r"test_[a-z0-9]+\.py",
                },
                "test_adapterdevice.py",
            ),
            (
                "test_Adapter.py",
                {
                    "name": "Pytest module",
                    "suggestion": "test_prefix_alnum",
                    "pattern": r"test_[a-z0-9]+\.py",
                },
                "test_adapter.py",
            ),
        ],
    )
    def test_suggests_filename_according_to_rule(
        self,
        filename: str,
        rule: dict[str, str],
        expected: str,
    ) -> None:
        """Generate a filename matching the configured naming convention."""
        assert _suggest_filename(filename, rule, 64) == expected

    def test_shortens_suggestion_to_maximum_length(self) -> None:
        """Ensure suggested filenames respect the configured limit."""
        rule = {
            "name": "Pytest module",
            "suggestion": "test_prefix_alnum",
            "pattern": r"test_[a-z0-9]+\.py",
        }

        suggestion = _suggest_filename(
            "test_" + "a" * 100 + ".py",
            rule,
            20,
        )

        assert suggestion is not None
        assert len(suggestion) <= 20
        assert re.fullmatch(rule["pattern"], suggestion)

    def test_returns_none_for_unknown_strategy(self) -> None:
        """Do not guess when no suggestion strategy is configured."""
        rule = {
            "name": "Custom rule",
            "suggestion": "unknown",
            "pattern": r"[a-z]+\.py",
        }

        assert _suggest_filename("BAD_NAME.py", rule, 64) is None

class TestConfiguration:
    """Tests for configuration loading and validation."""

    def test_loads_valid_configuration(self, tmp_path: Path) -> None:
        """Load a minimal valid TOML configuration."""
        config_path = tmp_path / "pyproject.toml"
        config_path.write_text(
            """
[tool.eos-filename-linter]
max_filename_length = 64
exclude = [".venv/**"]

[[tool.eos-filename-linter.rules]]
name = "pytest_module"
paths = ["tests/**/*.py"]
pattern = "^test_[a-z0-9]+\\\\.py$"
allowed_names = ["conftest.py"]
""",
            encoding="utf-8",
        )

        config = _load_config(config_path)

        assert config["max_filename_length"] == 64
        assert config["rules"][0]["name"] == "pytest_module"

    def test_rejects_invalid_regex(self, tmp_path: Path) -> None:
        """Reject an invalid regular expression."""
        config_path = tmp_path / "pyproject.toml"
        config_path.write_text(
            """
[tool.eos-filename-linter]

[[tool.eos-filename-linter.rules]]
name = "invalid"
paths = ["**/*.py"]
pattern = "["
""",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="invalid regex"):
            _load_config(config_path)

class TestLintPaths:
    """Tests for filename validation and diagnostic output."""

    @pytest.fixture
    def config(self) -> dict[str, object]:
        """Provide a minimal linter configuration."""
        return {
            "max_filename_length": 64,
            "exclude": ["ignored/**"],
            "rules": [
                {
                    "name": "pytest_module",
                    "paths": ["tests/**/*.py"],
                    "pattern": r"test_[a-z0-9]+\.py",
                    "allowed_names": ["conftest.py"],
                }
            ],
        }

    def test_accepts_valid_test_filename(
        self, tmp_path: Path, config: dict[str, object], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Accept a filename that satisfies the test naming rule."""
        (tmp_path / "tests").mkdir()
        path = tmp_path / "tests" / "test_adapter123.py"
        path.touch()

        assert lint_paths([str(path)], tmp_path, config) == 0
        assert capsys.readouterr().out == ""

    @pytest.mark.parametrize(
        "filename",
        ["test_adapter_device.py", "test_Adapter.py", "test-adapter.py"],
    )
    def test_rejects_invalid_test_filenames(
        self,
        tmp_path: Path,
        config: dict[str, object],
        filename: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Reject test filenames that violate the naming convention."""
        (tmp_path / "tests").mkdir()
        path = tmp_path / "tests" / filename
        path.touch()

        assert lint_paths([str(path)], tmp_path, config) == 1
        output = capsys.readouterr().out
        assert "ERROR" in output
        assert "Suggested name:" in output

    def test_rejects_overlong_filename(
        self,
        tmp_path: Path,
        config: dict[str, object],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Report a filename that exceeds the configured limit."""
        (tmp_path / "tests").mkdir()
        filename = "test_" + "a" * 60 + ".py"
        path = tmp_path / "tests" / filename
        path.touch()

        assert lint_paths([str(path)], tmp_path, config) == 1
        output = capsys.readouterr().out
        assert "maximum is 64" in output
        assert "Suggested name:" in output

    def test_ignores_excluded_file(
        self,
        tmp_path: Path,
        config: dict[str, object],
    ) -> None:
        """Skip a file covered by a global exclusion."""
        (tmp_path / "ignored").mkdir()
        path = tmp_path / "ignored" / "test_bad_name.py"
        path.touch()

        assert lint_paths([str(path)], tmp_path, config) == 0

    def test_reports_violation_via_dedicated_rule_after_exclusion(
        self,
        tmp_path: Path,
        layered_config: dict[str, object],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Apply the dedicated rule when the general rule excludes the file.

        Regression test: an excluded general rule must not shadow the
        dedicated ``single_test`` rule for ``tests/single_test_*.py``.
        """
        (tmp_path / "tests").mkdir()
        path = tmp_path / "tests" / "single_test_BAD_name.py"
        path.touch()

        assert lint_paths([str(path)], tmp_path, layered_config) == 1
        output = capsys.readouterr().out
        assert "single_test" in output
        assert "Suggested name:" in output
        assert "single_test_badname.py" in output

    def test_accepts_valid_single_test_filename_via_dedicated_rule(
        self,
        tmp_path: Path,
        layered_config: dict[str, object],
    ) -> None:
        """Accept a compliant filename selected via the dedicated rule."""
        (tmp_path / "tests").mkdir()
        path = tmp_path / "tests" / "single_test_adapter.py"
        path.touch()

        assert lint_paths([str(path)], tmp_path, layered_config) == 0


_iter_repository_files = _linter._iter_repository_files

class TestRepositoryScan:
    """Tests for repository-wide file discovery."""

    def test_includes_file_reachable_only_via_later_rule(
        self,
        tmp_path: Path,
        layered_config: dict[str, object],
    ) -> None:
        """Include files selected by a later rule after an exclusion."""
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "single_test_BAD_name.py").touch()

        files = _iter_repository_files(tmp_path, layered_config)

        assert "tests/single_test_BAD_name.py" in files
