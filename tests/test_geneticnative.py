"""Tests for the optional native GENETIC kernel (``akkudoktoreos-native``) and its adapter.

The tests without the package run everywhere. The tests marked ``needs_native`` run only when
``akkudoktoreos-native`` is installed; they compare native runs with Python runs at a fixed
seed. Python is put on the kernel's fixed numerical profile for that (sequential dot product in
the self-consumption interpolator, NumPy grid interpolation), because ``np.dot`` rounds
differently per CPU (see ``akkudoktoreos.optimization.genetic.native``).
"""

import importlib.util
import json
import math
import random
import shutil
import tokenize
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Optional

import numpy as np
import pytest
from loguru import logger
from pydantic import ValidationError

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.cache import CacheEnergyManagementStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.optimization.genetic import native
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticsettings import (
    GeneticBackend,
    GeneticCommonSettings,
)
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.prediction import interpolator as interpolator_module
from akkudoktoreos.utils.datetimeutil import to_datetime

DIR_TESTDATA = Path(__file__).parent / "testdata" / "genetic"
HAVE_NATIVE = importlib.util.find_spec(native.NATIVE_PACKAGE) is not None
needs_native = pytest.mark.skipif(not HAVE_NATIVE, reason="akkudoktoreos-native not installed")

START = "2026-09-16T10:00:00+02:00"


# --------------------------------------------------------------------------- helpers


@pytest.fixture(autouse=True)
def _fresh_native_state():
    """Every test starts without cached package status, profile or compare report."""
    native.reset_status_cache()
    native._logged_reasons.clear()
    native.last_compare_report = None
    yield
    native.reset_status_cache()


@pytest.fixture
def certified(monkeypatch):
    """Let the installed package run on these sources, certified or not."""
    kernel = pytest.importorskip(native.NATIVE_PACKAGE)
    monkeypatch.setattr(kernel, "is_certified", lambda fingerprint: True)
    native.reset_status_cache()


@pytest.fixture
def python_on_release_profile(monkeypatch, config_eos):
    """Python arithmetic as the kernel's fixed profile (needs a NumPy without FMA in interp)."""
    kernel = pytest.importorskip(native.NATIVE_PACKAGE)
    rng = random.Random(7)
    for _ in range(300):
        xp = sorted({rng.uniform(0.0, 1e4) for _ in range(rng.randint(2, 30))})
        if len(xp) < 2:
            continue
        fp = [rng.uniform(-50.0, 50.0) for _ in xp]
        x = rng.uniform(xp[0], xp[-1])
        if float(np.interp(x, xp, fp)) != kernel.interp(x, xp, fp, False):
            pytest.skip("this NumPy fuses multiply-add in np.interp; no release-profile reference")
    monkeypatch.setattr(interpolator_module, "np", native.SequentialDotNumpy())
    config_eos.merge_settings_from_dict({"optimization": {"self_consumption_interpolator": "numpy"}})
    CacheEnergyManagementStore().clear()


def _configure(config_eos: ConfigEOS, **genetic: Any) -> None:
    settings: dict[str, Any] = {
        "horizon_hours": 38,
        "individuals": 60,
        "generations": 25,
        "penalties": {"ev_soc_miss": 10, "ac_charge_break_even": 1},
    }
    prediction_hours = genetic.pop("prediction_hours", 48)
    direct_marketing = genetic.pop("direct_marketing", False)
    settings.update(genetic)
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": prediction_hours},
            "feedintariff": {"direct_marketing_enabled": direct_marketing},
            "optimization": {"algorithm": "GENETIC", "genetic": settings},
            "devices": {
                "max_electric_vehicles": 1,
                "electric_vehicles": {
                    "ev1": {"charge_rates": [0.0, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0]}
                },
            },
        }
    )


def _load_input(name: str) -> GeneticOptimizationParameters:
    with (DIR_TESTDATA / name).open("r") as f_in:
        return GeneticOptimizationParameters(**json.load(f_in))


def _synthetic_input(
    *,
    slots: int,
    interval: int,
    seed: int,
    ev: bool = True,
    appliance: bool = False,
    feed_in_series: bool = False,
    deadline: Optional[str] = None,
) -> GeneticOptimizationParameters:
    """A day-and-a-half of plausible PV, load and prices on the native slot grid."""
    rng = random.Random(seed)
    per_hour = 3600 // interval
    pv, load, price, feed_in = [], [], [], []
    for slot in range(slots):
        hour = (slot / per_hour) % 24
        sun = max(0.0, math.sin((hour - 6.0) / 14.0 * math.pi))
        pv.append(round(8000.0 * sun * rng.uniform(0.6, 1.0) / per_hour, 3))
        load.append(round((450.0 + 600.0 * (18 <= hour < 22) + rng.uniform(0, 300)) / per_hour, 3))
        price.append(round((0.22 + 0.12 * math.cos((hour - 19) / 24 * 2 * math.pi)) / 1000.0, 9))
        feed_in.append(round((0.081 - 0.1 * (11 <= hour < 14) * rng.random()) / 1000.0, 9))
    data: dict[str, Any] = {
        "ems": {
            "pv_forecast_wh": pv,
            "total_load": load,
            "electricity_price_per_wh": price,
            "feed_in_tariff_per_wh": feed_in if feed_in_series else 7e-05,
            "price_per_wh_battery": 0.0,
        },
        "pv_battery": {
            "device_id": "battery1",
            "capacity_wh": 12000,
            "initial_soc_percentage": 35,
            "min_soc_percentage": 5,
            "max_charge_power_w": 5000,
            "charge_rates": [0.0, 0.25, 0.5, 1.0],
            "grid_export_rates": [0.5, 1.0],
        },
        "inverter": {"device_id": "inverter1", "max_power_wh": 10000, "battery_id": "battery1"},
        "forecast_interval_seconds": interval,
    }
    if ev:
        data["ev"] = {
            "device_id": "ev1",
            "capacity_wh": 60000,
            "charging_efficiency": 0.9,
            "max_charge_power_w": 11000,
            "initial_soc_percentage": 30,
            "min_soc_percentage": 70,
            "min_soc_deadline_datetime": deadline,
        }
    if appliance:
        data["home_appliances"] = [
            {"device_id": "dishwasher1", "consumption_wh": 1800, "duration_h": 2},
        ]
    return GeneticOptimizationParameters(**data)


def _run(
    parameters: GeneticOptimizationParameters,
    *,
    seed: int = 42,
    ngen: Optional[int] = None,
    start: str = START,
) -> GeneticSolution:
    get_ems().set_start_datetime(to_datetime(start, in_timezone="Europe/Berlin"))
    CacheEnergyManagementStore().clear()
    optimization = GeneticOptimization(fixed_seed=seed)
    return optimization.optimize_ems(parameters=parameters, ngen=ngen)


@contextmanager
def _backend(mode: str) -> Iterator[None]:
    """Run ``GeneticOptimization._optimize_ems_run`` in the kernel or in compare mode."""
    original = GeneticOptimization._optimize_ems_run

    def run(
        self: GeneticOptimization,
        parameters: GeneticOptimizationParameters,
        start_hour: Optional[int],
        worst_case: bool,
        ngen: Optional[int],
        individuals: Optional[int],
    ) -> GeneticSolution:
        if mode == "native":
            return native.run_native(self, parameters, worst_case, ngen, individuals).solution

        def python_run() -> GeneticSolution:
            return original(self, parameters, start_hour, worst_case, ngen, individuals)

        return native.compare_run(
            self, python_run, parameters, start_hour, worst_case, ngen, individuals
        )

    GeneticOptimization._optimize_ems_run = run  # type: ignore[method-assign]
    try:
        yield
    finally:
        GeneticOptimization._optimize_ems_run = original  # type: ignore[method-assign]


def _set_backend(config_eos: ConfigEOS, backend: str) -> None:
    config_eos.merge_settings_from_dict({"optimization": {"genetic": {"backend": backend}}})


def _run_both(config_eos: ConfigEOS, parameters_factory: Any, **run: Any) -> tuple[Any, Any]:
    python = _run(parameters_factory(), **run)
    with _backend("native"):
        native_solution = _run(parameters_factory(), **run)
    return python, native_solution


def _with_time_windows(data: dict[str, Any]) -> None:
    """Give the appliance of a parameter dump time windows (not supported natively)."""
    appliance = data["dishwasher"] or data["home_appliances"][0]
    appliance["time_windows"] = {"windows": [{"start_time": "12:00", "duration": "4 hours"}]}


class _FakeKernel(SimpleNamespace):
    """Stand-in for the compiled package."""

    class NativeRunError(RuntimeError):
        pass

    def is_certified(self, fingerprint: str) -> bool:
        return fingerprint in self.certified_fingerprints


def _fake_kernel(**overrides: Any) -> _FakeKernel:
    values: dict[str, Any] = {
        "__version__": "0.0-test",
        "INTERFACE_VERSION": native.INTERFACE_VERSION,
        "SELF_CONSUMPTION_TABLE_SHA256": native._table_sha256(),
        "DOT_KERNELS": ["sequential"],
        "certified_fingerprints": (),
    }
    values.update(overrides)
    return _FakeKernel(**values)


# --------------------------------------------------------------------------- adapter


class TestAdapter:
    def test_missing_package_is_reported(self, config_eos, monkeypatch):
        _configure(config_eos)
        monkeypatch.setattr(native, "_import_native", lambda: None)
        with pytest.raises(native.NativeUnsupported, match="not installed"):
            with _backend("native"):
                _run(_load_input("optimize_input_1.json"), ngen=3)

    @pytest.mark.parametrize(
        ("override", "reason"),
        [({"INTERFACE_VERSION": 99}, "interface"), ({"SELF_CONSUMPTION_TABLE_SHA256": "0" * 64}, "table")],
    )
    def test_incompatible_package_is_rejected(self, override, reason):
        with pytest.raises(native.NativeUnsupported, match=reason):
            native._check_package(_fake_kernel(**override))
        native._check_package(_fake_kernel())

    def test_kernel_error_is_reported(self, config_eos, monkeypatch):
        _configure(config_eos)

        class Kernel(_FakeKernel):
            def run_genetic(self, request: str) -> str:
                raise self.NativeRunError("run_failed", "boom")

        monkeypatch.setattr(native, "_import_native", lambda: Kernel(**vars(_fake_kernel())))
        with pytest.raises(native.NativeRunFailed, match="run_failed"):
            with _backend("native"):
                _run(_load_input("optimize_input_1.json"), ngen=3)

    @pytest.mark.parametrize(
        ("code", "error"),
        [("unsupported", native.NativeUnsupported), ("panic", native.NativeRunFailed)],
    )
    def test_kernel_errors_are_classified(self, config_eos, monkeypatch, code, error):
        """Rejections of unsupported requests are expected; every other error is a defect."""
        _configure(config_eos)

        class Kernel(_FakeKernel):
            def run_genetic(self, request: str) -> str:
                raise self.NativeRunError(code, "boom")

        monkeypatch.setattr(native, "_import_native", lambda: Kernel(**vars(_fake_kernel())))
        with pytest.raises(error, match=code):
            with _backend("native"):
                _run(_load_input("optimize_input_1.json"), ngen=3)
        with _backend("compare"):
            _run(_load_input("optimize_input_1.json"), ngen=3)
        report = native.last_compare_report
        assert report is not None and code in report.native_error
        assert report.native_unsupported is (error is native.NativeUnsupported)

    def test_invalid_kernel_response_is_a_failure(self, config_eos, monkeypatch):
        _configure(config_eos)

        class Kernel(_FakeKernel):
            def run_genetic(self, request: str) -> str:
                return json.dumps({"solution": {"ac_charge": "not a list"}})

        monkeypatch.setattr(native, "_import_native", lambda: Kernel(**vars(_fake_kernel())))
        with pytest.raises(native.NativeRunFailed, match="invalid kernel response"):
            with _backend("native"):
                _run(_load_input("optimize_input_1.json"), ngen=3)

    def test_compare_returns_python_when_the_kernel_fails(self, config_eos, monkeypatch):
        _configure(config_eos)
        reference = _run(_load_input("optimize_input_1.json"), ngen=3)
        monkeypatch.setattr(native, "_import_native", lambda: None)
        with _backend("compare"):
            solution = _run(_load_input("optimize_input_1.json"), ngen=3)
        assert native.compare_solutions(reference, solution) == []
        report = native.last_compare_report
        assert report is not None and not report.identical
        assert "not installed" in report.native_error

    def test_unsynchronized_start_hour_still_raises(self, config_eos, monkeypatch):
        _configure(config_eos)
        monkeypatch.setattr(native, "_import_native", lambda: _fake_kernel())
        get_ems().set_start_datetime(to_datetime(START, in_timezone="Europe/Berlin"))
        optimization = GeneticOptimization(fixed_seed=1)
        with _backend("compare"), pytest.raises(ValueError, match="Start hour not synced"):
            optimization.optimize_ems(
                parameters=_load_input("optimize_input_1.json"), start_hour=3, ngen=2
            )

    def test_request_without_nan_and_infinity(self):
        assert native._json_ready({"a": [1.0, math.nan, 2]}) == {"a": [1.0, None, 2]}
        with pytest.raises(native.NativeUnsupported):
            native._json_ready([math.inf])

    def test_differences_are_listed(self):
        python = {"a": [1.0, 2.0], "b": {"c": 1}, "d": "x"}
        other = {"a": [1.0, 2.0000000000000004], "b": {"c": 2}, "e": "x"}
        differences = native.diff_values(python, other)
        assert ".a[1]: 2.0 != 2.0000000000000004" in differences
        assert ".b.c: 1 != 2" in differences
        assert ".d: only in python" in differences
        assert ".e: only in native" in differences
        assert native.diff_values([math.nan, -0.0], [math.nan, 0.0]) == ["[1]: -0.0 != 0.0"]

    def test_sequential_dot_sums_in_index_order(self):
        assert native.sequential_dot([1e16, 1.0, -1e16], [1.0, 1.0, 1.0]) == 0.0
        assert native.sequential_dot([1e16, -1e16, 1.0], [1.0, 1.0, 1.0]) == 1.0


# --------------------------------------------------------------------------- configuration


class TestConfiguration:
    def test_default_is_python(self, config_eos):
        assert config_eos.optimization.genetic.backend == GeneticBackend.PYTHON
        assert GeneticCommonSettings().backend == "python"

    @pytest.mark.parametrize("value", ["python", "native", "compare"])
    def test_round_trip(self, config_eos, value):
        _set_backend(config_eos, value)
        assert config_eos.optimization.genetic.backend == GeneticBackend(value)
        dumped = config_eos.optimization.genetic.model_dump(mode="json")
        assert dumped["backend"] == value
        assert GeneticCommonSettings(**dumped).backend == GeneticBackend(value)

    def test_invalid_value_is_rejected(self, config_eos):
        with pytest.raises(ValidationError):
            GeneticCommonSettings.model_validate({"backend": "rust"})
        assert config_eos.optimization.genetic.backend == GeneticBackend.PYTHON

    def test_environment_variable(self, config_eos, monkeypatch):
        monkeypatch.setenv("EOS_OPTIMIZATION__GENETIC__BACKEND", "compare")
        config_eos.reset_settings()
        assert config_eos.optimization.genetic.backend == GeneticBackend.COMPARE
        monkeypatch.delenv("EOS_OPTIMIZATION__GENETIC__BACKEND")
        config_eos.reset_settings()
        assert config_eos.optimization.genetic.backend == GeneticBackend.PYTHON


# --------------------------------------------------------------------------- guard


def _copy_sources(tmp_path: Path) -> Path:
    root = tmp_path / "akkudoktoreos"
    for relative in (*native.FINGERPRINT_SOURCES, *native.FINGERPRINT_DATA):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(native._PACKAGE_ROOT / relative, target)
    return root


class TestGuard:
    def test_fingerprint_ignores_comments_and_docstrings(self, tmp_path):
        root = _copy_sources(tmp_path)
        reference = native.compute_fingerprint(root)
        assert reference == native.source_fingerprint()

        genetic_py = root / "optimization/genetic/genetic.py"
        text = genetic_py.read_text(encoding="utf-8")
        cosmetic = text.replace(
            '"""GENETIC algorithm to solve energy optimization."""',
            '"""GENETIC algorithm to solve energy optimization (reworded)."""\n    # a comment\n',
        )
        assert cosmetic != text
        genetic_py.write_text(cosmetic, encoding="utf-8")
        assert native.compute_fingerprint(root) == reference

        changed = text.replace("MUTATION_PROBABILITY = 0.55", "MUTATION_PROBABILITY = 0.56")
        assert changed != text
        genetic_py.write_text(changed, encoding="utf-8")
        assert native.compute_fingerprint(root) != reference

    def test_fingerprint_ignores_added_docstrings(self, tmp_path):
        root = _copy_sources(tmp_path)
        reference = native.compute_fingerprint(root)
        battery_py = root / "devices/genetic/battery.py"
        text = battery_py.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        # A docstring for the first function that has none, and one at the top of the module.
        for index, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith("def ") and not lines[index + 1].strip().startswith('"""'):
                indent = " " * (len(line) - len(stripped) + 4)
                lines.insert(index + 1, f'{indent}"""Added docstring."""\n')
                break
        else:
            pytest.fail("no function without docstring found")
        battery_py.write_text('"""Added module docstring."""\n' + "".join(lines), encoding="utf-8")
        assert native.compute_fingerprint(root) == reference

    def test_fstring_statement_changes_fingerprint(self, tmp_path):
        """A standalone f-string evaluates its expressions; it is not a docstring."""
        root = _copy_sources(tmp_path)
        reference = native.compute_fingerprint(root)
        battery_py = root / "devices/genetic/battery.py"
        text = battery_py.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        for index, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith("return "):
                indent = line[: len(line) - len(stripped)]
                lines.insert(index, f'{indent}f"{{self.reset()}}"\n')
                break
        battery_py.write_text("".join(lines), encoding="utf-8")
        assert native.compute_fingerprint(root) != reference
        assert native._is_constant_string('"""doc"""')
        assert native._is_constant_string("r'doc'")
        assert not native._is_constant_string('f"{x}"')
        assert not native._is_constant_string("RF'{x}'")

    def test_fstring_is_one_token_as_on_python_311(self):
        """Python >= 3.12 splits f-strings into several tokens; the fingerprint must not."""
        source = 'x = f"a{b!r:>{width}}c" + f"{f\'{d}\'}"\n'
        assert native._significant_tokens(source)[2:5] == [
            (tokenize.STRING, 'f"a{b!r:>{width}}c"'),
            (tokenize.OP, "+"),
            (tokenize.STRING, 'f"{f\'{d}\'}"'),
        ]

    def test_table_change_changes_fingerprint(self, tmp_path):
        root = _copy_sources(tmp_path)
        reference = native.compute_fingerprint(root)
        table = root / native.SELF_CONSUMPTION_TABLE
        table.write_bytes(table.read_bytes() + b"\x00")
        assert native.compute_fingerprint(root) != reference

    def test_missing_package(self, monkeypatch):
        monkeypatch.setattr(native, "_import_native", lambda: None)
        status = native.native_status()
        assert not status.available
        assert "not installed" in status.reason

    def test_interface_mismatch(self, monkeypatch):
        monkeypatch.setattr(native, "_import_native", lambda: _fake_kernel(INTERFACE_VERSION=99))
        status = native.native_status()
        assert not status.available
        assert "interface" in status.reason

    def test_other_table(self, monkeypatch):
        monkeypatch.setattr(
            native, "_import_native", lambda: _fake_kernel(SELF_CONSUMPTION_TABLE_SHA256="0" * 64)
        )
        status = native.native_status()
        assert not status.available
        assert "table" in status.reason

    def test_uncertified_sources(self, monkeypatch):
        monkeypatch.setattr(native, "_import_native", lambda: _fake_kernel())
        status = native.native_status()
        assert not status.available
        assert native.source_fingerprint() in status.reason

    def test_certified_sources(self, monkeypatch):
        kernel = _fake_kernel(certified_fingerprints=(native.source_fingerprint(),))
        monkeypatch.setattr(native, "_import_native", lambda: kernel)
        status = native.native_status()
        assert status.available
        assert status.version == "0.0-test"

    @needs_native
    def test_package_certificate_lookup(self, monkeypatch):
        kernel = pytest.importorskip(native.NATIVE_PACKAGE)
        assert not kernel.is_certified("sha256:" + "0" * 64)
        assert kernel.certified_entries(), "the package ships no certificate"
        for entry in kernel.certified_entries():
            assert entry["fingerprint"].startswith("sha256:")
            assert kernel.is_certified(entry["fingerprint"])
        # An entry verified with another kernel version does not certify this one.
        fingerprint = kernel.certified_entries()[0]["fingerprint"]
        monkeypatch.setattr(
            kernel,
            "certified_entries",
            lambda: ({"fingerprint": fingerprint, "kernel_version": "0.0.0"},),
        )
        assert not kernel.is_certified(fingerprint)


# --------------------------------------------------------------------------- fallback


class TestFallback:
    def test_python_backend_never_touches_the_package(self, config_eos, monkeypatch):
        _configure(config_eos)

        def fail() -> None:
            raise AssertionError("native backend consulted")

        monkeypatch.setattr(native, "native_status", fail)
        monkeypatch.setattr(native, "_import_native", fail)
        solution = _run(_load_input("optimize_input_1.json"), ngen=3)
        assert isinstance(solution, GeneticSolution)

    @pytest.mark.parametrize("backend", ["native", "compare"])
    def test_missing_package_falls_back_to_python(self, config_eos, monkeypatch, backend):
        _configure(config_eos)
        reference = _run(_load_input("optimize_input_1.json"), ngen=3)
        monkeypatch.setattr(native, "_import_native", lambda: None)
        messages: list[str] = []
        handler = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
        try:
            _set_backend(config_eos, backend)
            first = _run(_load_input("optimize_input_1.json"), ngen=3)
            second = _run(_load_input("optimize_input_1.json"), ngen=3)
        finally:
            logger.remove(handler)
        assert native.compare_solutions(reference, first) == []
        assert native.compare_solutions(reference, second) == []
        warnings = [m for m in messages if "native backend not used" in m]
        assert len(warnings) == 1  # repeated reasons go to debug level
        assert native.last_compare_report is None

    def test_uncertified_sources_fall_back_to_python(self, config_eos, monkeypatch):
        _configure(config_eos)
        reference = _run(_load_input("optimize_input_1.json"), ngen=3)

        class Kernel(_FakeKernel):
            def run_genetic(self, request: str) -> str:
                raise AssertionError("an uncertified kernel must not run")

        monkeypatch.setattr(native, "_import_native", lambda: Kernel(**vars(_fake_kernel())))
        _set_backend(config_eos, "native")
        solution = _run(_load_input("optimize_input_1.json"), ngen=3)
        assert native.compare_solutions(reference, solution) == []

    def test_kernel_error_falls_back_to_python(self, config_eos, monkeypatch):
        _configure(config_eos)
        reference = _run(_load_input("optimize_input_1.json"), ngen=3)

        class Kernel(_FakeKernel):
            def run_genetic(self, request: str) -> str:
                raise self.NativeRunError("run_failed", "boom")

        kernel = Kernel(
            **{**vars(_fake_kernel()), "certified_fingerprints": (native.source_fingerprint(),)}
        )
        monkeypatch.setattr(native, "_import_native", lambda: kernel)
        _set_backend(config_eos, "native")
        solution = _run(_load_input("optimize_input_1.json"), ngen=3)
        assert native.compare_solutions(reference, solution) == []

    def test_unsynchronized_start_hour_still_raises(self, config_eos, monkeypatch):
        _configure(config_eos, backend="native")
        kernel = _fake_kernel(certified_fingerprints=(native.source_fingerprint(),))
        monkeypatch.setattr(native, "_import_native", lambda: kernel)
        get_ems().set_start_datetime(to_datetime(START, in_timezone="Europe/Berlin"))
        optimization = GeneticOptimization(fixed_seed=1)
        with pytest.raises(ValueError, match="Start hour not synced"):
            optimization.optimize_ems(
                parameters=_load_input("optimize_input_1.json"), start_hour=3, ngen=2
            )

    def test_broken_package_falls_back_to_python(self, config_eos, monkeypatch):
        _configure(config_eos)
        reference = _run(_load_input("optimize_input_1.json"), ngen=3)

        class Broken(_FakeKernel):
            def is_certified(self, fingerprint: str) -> bool:
                raise ValueError("certified.json is broken")

        monkeypatch.setattr(native, "_import_native", lambda: Broken(**vars(_fake_kernel())))
        status = native.native_status()
        assert not status.available and "unusable" in status.reason
        for backend in ("native", "compare"):
            native.reset_status_cache()
            _set_backend(config_eos, backend)
            solution = _run(_load_input("optimize_input_1.json"), ngen=3)
            assert native.compare_solutions(reference, solution) == []


@needs_native
@pytest.mark.usefixtures("certified", "python_on_release_profile")
class TestBackendSetting:
    """``optimization.genetic.backend`` runs the kernel through ``optimize_ems``."""

    def test_native_backend_equals_python(self, config_eos):
        _configure(config_eos, interval_sec=900, horizon_hours=24, tail_horizon_hours=12)

        def make() -> GeneticOptimizationParameters:
            return _synthetic_input(slots=192, interval=900, seed=11, appliance=True)

        python = _run(make(), seed=9)
        _set_backend(config_eos, "native")
        native_solution = _run(make(), seed=9)
        assert native.compare_solutions(python, native_solution) == []

    def test_compare_backend_reports(self, config_eos):
        _configure(config_eos, backend="compare")
        _run(_load_input("optimize_input_2.json"))
        report = native.last_compare_report
        assert report is not None
        assert report.identical, report.differences[:5]

    def test_unsupported_request_falls_back(self, config_eos):
        _configure(config_eos)
        data = _load_input("optimize_input_2.json").model_dump(mode="json")
        _with_time_windows(data)
        python = _run(GeneticOptimizationParameters(**data))
        _set_backend(config_eos, "native")
        native_solution = _run(GeneticOptimizationParameters(**data))
        assert native.compare_solutions(python, native_solution) == []


# --------------------------------------------------------------------------- native runs


@needs_native
@pytest.mark.usefixtures("python_on_release_profile")
class TestNativeEqualsPython:
    """With a fixed seed, native and Python return the same solution bit for bit."""

    @pytest.mark.parametrize("name", ["optimize_input_1.json", "optimize_input_2.json"])
    @pytest.mark.parametrize("tail", [0, 10])
    def test_reference_inputs(self, config_eos, name, tail):
        _configure(
            config_eos,
            tail_horizon_hours=tail,
            terminal_value_mode="AUTO" if tail else "FIXED",
        )
        python, native_solution = _run_both(config_eos, lambda: _load_input(name))
        assert native.compare_solutions(python, native_solution) == []

    @pytest.mark.parametrize("interval", [3600, 900])
    @pytest.mark.parametrize("direct_marketing", [False, True])
    def test_synthetic_inputs(self, config_eos, interval, direct_marketing):
        _configure(
            config_eos,
            interval_sec=interval,
            horizon_hours=24,
            tail_horizon_hours=12,
            direct_marketing=direct_marketing,
        )
        slots = 48 * 3600 // interval

        def make() -> GeneticOptimizationParameters:
            return _synthetic_input(
                slots=slots,
                interval=interval,
                seed=interval + direct_marketing,
                appliance=True,
                feed_in_series=True,
                deadline="2026-09-16T18:00:00+02:00",
            )

        python, native_solution = _run_both(config_eos, make, seed=7)
        assert native.compare_solutions(python, native_solution) == []
        assert native_solution.appliance_starts
        if direct_marketing:
            assert native_solution.battery_grid_export_factor

    @pytest.mark.asyncio
    async def test_plan_and_solution_frames(self, config_eos):
        """The native solution yields the same plan and solution frames as Python's."""
        _configure(config_eos, interval_sec=900, horizon_hours=24, tail_horizon_hours=12)

        def make() -> GeneticOptimizationParameters:
            return _synthetic_input(slots=192, interval=900, seed=11, appliance=True)

        python, native_solution = _run_both(config_eos, make, seed=9)
        assert native.compare_solutions(python, native_solution) == []
        python_frames = (await python.optimization_solution()).model_dump(mode="json")
        native_frames = (await native_solution.optimization_solution()).model_dump(mode="json")
        for frames in (python_frames, native_frames):
            frames.pop("generated_at", None)
            frames.pop("id", None)
        assert native.diff_values(python_frames, native_frames) == []

        def plan(solution: GeneticSolution) -> dict[str, Any]:
            dump = solution.energy_management_plan().model_dump(mode="json")
            dump.pop("generated_at", None)
            dump.pop("id", None)
            for instruction in dump["instructions"]:
                instruction.pop("id", None)
            return dump

        assert native.diff_values(plan(python), plan(native_solution)) == []

    @pytest.mark.parametrize("dated", [False, True])
    def test_warm_start_from_previous_run(self, config_eos, dated):
        """A warm start from an earlier run, shifted by the elapsed slots when it is dated."""
        _configure(config_eos, horizon_hours=24, tail_horizon_hours=12)
        first = _run(_synthetic_input(slots=48, interval=3600, seed=3), seed=5)

        def make() -> GeneticOptimizationParameters:
            parameters = _synthetic_input(slots=48, interval=3600, seed=3)
            update: dict[str, Any] = {"start_solution": first.start_solution}
            if dated:
                update["start_solution_datetime"] = first.start_solution_datetime
            return parameters.model_copy(update=update)

        python, native_solution = _run_both(
            config_eos, make, seed=6, start="2026-09-16T12:00:00+02:00"
        )
        assert native.compare_solutions(python, native_solution) == []

    @pytest.mark.parametrize("limit", [None, 0, 7])
    def test_fitness_cache_limit(self, config_eos, limit):
        _configure(config_eos, fitness_cache_max_entries=limit, workers=1)
        python, native_solution = _run_both(
            config_eos, lambda: _load_input("optimize_input_2.json")
        )
        assert native.compare_solutions(python, native_solution) == []

    def test_worst_case(self, config_eos):
        _configure(config_eos)
        results = []
        for mode in ("python", "native"):
            get_ems().set_start_datetime(to_datetime(START, in_timezone="Europe/Berlin"))
            CacheEnergyManagementStore().clear()
            optimization = GeneticOptimization(fixed_seed=3)
            if mode == "python":
                solution = optimization.optimize_ems(
                    parameters=_load_input("optimize_input_1.json"), worst_case=True
                )
            else:
                with _backend("native"):
                    solution = optimization.optimize_ems(
                        parameters=_load_input("optimize_input_1.json"), worst_case=True
                    )
            results.append(solution)
        assert native.compare_solutions(*results) == []

    def test_full_length_run(self, config_eos, is_finalize):
        if not is_finalize:
            pytest.skip("full-length run only with --finalize")
        _configure(config_eos, individuals=300, generations=400, tail_horizon_hours=10)
        python, native_solution = _run_both(
            config_eos, lambda: _load_input("optimize_input_2.json")
        )
        assert native.compare_solutions(python, native_solution) == []

    def test_battery_without_inverter_is_rejected(self, config_eos):
        _configure(config_eos)
        data = _load_input("optimize_input_1.json").model_dump(mode="json")
        data["inverter"] = None
        with pytest.raises(native.NativeUnsupported, match="without an inverter"):
            with _backend("native"):
                _run(GeneticOptimizationParameters(**data))

    def test_unsupported_request_is_rejected(self, config_eos):
        _configure(config_eos)
        data = _load_input("optimize_input_2.json").model_dump(mode="json")
        _with_time_windows(data)
        with pytest.raises(native.NativeUnsupported, match="time_windows"):
            with _backend("native"):
                _run(GeneticOptimizationParameters(**data))


@needs_native
class TestCompareMode:
    def test_returns_python_solution_and_reports(self, config_eos, python_on_release_profile):
        _configure(config_eos)
        python = _run(_load_input("optimize_input_2.json"))
        with _backend("compare"):
            compared = _run(_load_input("optimize_input_2.json"))
        assert native.compare_solutions(python, compared) == []
        report = native.last_compare_report
        assert report is not None
        assert report.identical, report.differences[:5]
        assert report.native_error == ""
        assert report.python_net_euro == report.native_net_euro

    def test_reference_profile_reproduces_this_python(self, config_eos):
        """Without patches, compare uses the profile it detects; identical if it found one."""
        _configure(config_eos)
        with _backend("compare"):
            _run(_load_input("optimize_input_1.json"))
        report = native.last_compare_report
        assert report is not None and report.native_error == ""
        if report.profile_reproduces_python:
            assert report.identical, report.differences[:5]

    def test_without_seed_both_runs_share_one(self, config_eos, python_on_release_profile):
        _configure(config_eos)
        get_ems().set_start_datetime(to_datetime(START, in_timezone="Europe/Berlin"))
        CacheEnergyManagementStore().clear()
        optimization = GeneticOptimization()
        assert GeneticOptimization().fix_seed is None
        with _backend("compare"):
            solution = optimization.optimize_ems(parameters=_load_input("optimize_input_2.json"))
        report = native.last_compare_report
        assert report is not None
        assert report.identical, report.differences[:5]
        assert solution.fixed_seed is not None
        assert solution.fixed_seed == optimization.fix_seed

    @pytest.mark.parametrize("offset", ["+02:00", "+01:00"])
    def test_start_in_the_repeated_dst_hour(self, config_eos, python_on_release_profile, offset):
        """Both occurrences of 02:30 on 2026-10-25 (Europe/Berlin) run natively."""
        _configure(config_eos, interval_sec=900, horizon_hours=12, tail_horizon_hours=6)
        start = f"2026-10-25T02:30:00{offset}"
        with _backend("compare"):
            _run(_synthetic_input(slots=96, interval=900, seed=5), seed=4, start=start)
        report = native.last_compare_report
        assert report is not None
        assert report.native_error == ""
        assert report.identical, report.differences[:5]

    def test_native_failure_is_reported(self, config_eos):
        _configure(config_eos)
        data = _load_input("optimize_input_2.json").model_dump(mode="json")
        _with_time_windows(data)
        with _backend("compare"):
            solution = _run(GeneticOptimizationParameters(**data))
        assert isinstance(solution, GeneticSolution)
        report = native.last_compare_report
        assert report is not None and not report.identical
        assert "time_windows" in report.native_error
        assert report.native_unsupported
