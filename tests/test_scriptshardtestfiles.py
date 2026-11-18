"""Tests for the script that splits the pytest modules into CI shards."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load_shard_module() -> ModuleType:
    """Load the shard script directly from its source file.

    Returns:
        The imported shard script module.

    Raises:
        ImportError: If the module cannot be loaded.
    """
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "shard_test_files.py"
    spec = importlib.util.spec_from_file_location("shard_test_files", script_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load the shard script from {script_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_shard = _load_shard_module()

MODULES = [f"tests/test_m{index}.py" for index in range(11)]
DURATIONS = {"tests/test_m0.py": 60.0, "tests/test_m1.py": 50.0, "tests/test_m2.py": 5.0}


class TestSplit:
    """Distribution of the test modules to the shards."""

    @pytest.mark.parametrize(
        ("shards", "durations"),
        [
            (1, DURATIONS),
            (2, {}),
            (4, DURATIONS),
            (20, DURATIONS),
        ],
    )
    def test_every_module_is_in_exactly_one_shard(
        self, shards: int, durations: dict[str, float]
    ) -> None:
        """No module is dropped or run twice, with or without runtimes."""
        result = _shard.split(MODULES, durations, shards)

        assert len(result) == shards
        assert sorted(module for shard in result for module in shard) == sorted(MODULES)

    def test_heavy_modules_go_to_different_shards(self) -> None:
        """The two modules that dominate the runtime do not share a shard."""
        result = _shard.split(MODULES, DURATIONS, 2)

        assert ["tests/test_m0.py" in shard for shard in result] == [True, False]
        assert ["tests/test_m1.py" in shard for shard in result] == [False, True]

    def test_split_does_not_depend_on_the_input_order(self) -> None:
        """All jobs of a workflow run compute the same distribution."""
        assert _shard.split(MODULES, DURATIONS, 4) == _shard.split(
            list(reversed(MODULES)), DURATIONS, 4
        )

    def test_non_positive_shard_count_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            _shard.split(MODULES, DURATIONS, 0)


class TestModuleList:
    """The modules the script distributes."""

    def test_modules_are_the_test_files_directly_in_tests(self) -> None:
        tests_dir = Path(__file__).resolve().parent
        modules = _shard.list_test_modules()

        assert f"tests/{Path(__file__).name}" in modules
        assert "tests/conftest.py" not in modules
        assert modules == sorted(set(modules))
        for module in modules:
            path = tests_dir / Path(module).name
            assert module == f"tests/{path.name}"
            assert path.name.startswith("test_") and path.is_file()


class TestDurations:
    """The stored runtimes and their update from junit reports."""

    def test_missing_file_counts_every_module_alike(self, tmp_path: Path) -> None:
        assert _shard.load_durations(tmp_path / "missing.json") == {}

    def test_stored_runtimes_are_seconds_by_module_path(self) -> None:
        durations = _shard.load_durations()

        assert durations
        assert all(
            name.startswith("tests/test_") and seconds >= 0 for name, seconds in durations.items()
        )

    def test_junit_cases_are_summed_by_module(self, tmp_path: Path) -> None:
        report = tmp_path / "junit.xml"
        report.write_text(
            '<?xml version="1.0" encoding="utf-8"?>'
            '<testsuites><testsuite name="pytest">'
            '<testcase classname="tests.test_alpha.TestThing" name="test_a" time="1.25"/>'
            '<testcase classname="tests.test_alpha" name="test_b" time="0.5"/>'
            '<testcase classname="tests.test_beta.TestOther" name="test_c" time="2.004"/>'
            '<testcase classname="other.package" name="test_d" time="9"/>'
            "</testsuite></testsuites>",
            encoding="utf-8",
        )

        assert _shard.durations_from_junit([report]) == {
            "tests/test_alpha.py": 1.75,
            "tests/test_beta.py": 2.0,
        }
