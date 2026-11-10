"""Adapter between the GENETIC optimization and the optional native (Rust) kernel.

The package ``akkudoktoreos-native`` (sources in ``native/`` of the EOS repository) is a port of
this package's GENETIC optimizer. For the same request and seed it returns the same
``GeneticSolution`` as ``GeneticOptimization._optimize_ems_run``. This module builds the
kernel request from a run's configuration snapshot, turns the response back into a
``GeneticSolution`` and compares native and Python results.

EOS does not use the kernel: nothing in EOS imports this module. The tests
(``tests/test_geneticnative.py``) and the compare job of the ``native`` CI workflow
(``native/scripts/genetic_compare_plugin.py``) use it to check that the port follows Python.

Numerical profile
-----------------
Python computes two operations with platform libraries whose rounding depends on the CPU: the
dot products of the self-consumption table (``np.dot`` -> OpenBLAS/Accelerate kernel) and, on
some builds, ``np.interp`` (fused multiply-add). With the same seed, Python can therefore return
different plans on different CPUs. The kernel computes with a fixed profile by default
(:data:`RELEASE_PROFILE`: sequential sums, no fused multiply-add) and can reproduce the
OpenBLAS kernels it knows. :func:`reference_profile` finds the profile that reproduces this
process' Python; with it, both results must be identical bit for bit. If no profile matches
(e.g. Accelerate on macOS, x86-64 OpenBLAS kernels), the comparison reports the differences as
expected. If the interpolator sums in a fixed order itself (no BLAS), the release profile
reproduces Python on every CPU.
"""

from __future__ import annotations

import importlib
import json
import math
import random
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Optional

import numpy as np
from loguru import logger

if TYPE_CHECKING:
    from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
    from akkudoktoreos.optimization.genetic.geneticparams import (
        GeneticOptimizationParameters,
    )
    from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution

NATIVE_PACKAGE = "akkudoktoreos_native"

# JSON layout spoken with the kernel (eos_genetic::run::INTERFACE_VERSION).
INTERFACE_VERSION = 1

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
SELF_CONSUMPTION_TABLE = "data/regular_grid_interpolator.pkl"


def _import_native() -> Optional[ModuleType]:
    try:
        return importlib.import_module(NATIVE_PACKAGE)
    except ImportError:
        return None


@lru_cache(maxsize=1)
def _table_sha256() -> str:
    return sha256((_PACKAGE_ROOT / SELF_CONSUMPTION_TABLE).read_bytes()).hexdigest()


def _check_package(native: Any) -> None:
    """Raise :class:`NativeUnsupported` if the installed package cannot serve this EOS."""
    version = str(getattr(native, "__version__", "unknown"))
    if getattr(native, "INTERFACE_VERSION", None) != INTERFACE_VERSION:
        raise NativeUnsupported(
            f"{NATIVE_PACKAGE} {version} speaks interface "
            f"{getattr(native, 'INTERFACE_VERSION', None)}, EOS needs {INTERFACE_VERSION}"
        )
    if getattr(native, "SELF_CONSUMPTION_TABLE_SHA256", None) != _table_sha256():
        raise NativeUnsupported(
            f"{NATIVE_PACKAGE} {version} bundles another self-consumption table"
        )


# --------------------------------------------------------------------------- numerical profile

RELEASE_PROFILE: dict[str, Any] = {"dot": "sequential", "interp_fma": False}


def sequential_dot(a: Any, b: Any) -> float:
    """Dot product summed left to right without fused multiply-add (the release profile).

    ``np.dot`` gives the same sum only up to the BLAS kernel's summation order.
    """
    total = 0.0
    for x, y in zip(np.asarray(a, dtype=float).tolist(), np.asarray(b, dtype=float).tolist()):
        total += x * y
    return total


class SequentialDotNumpy:
    """NumPy with :func:`sequential_dot` as ``dot`` - for verification only.

    Installed as ``akkudoktoreos.prediction.interpolator.np`` by the tests and the compare CI
    job: the live Python code keeps running, only its BLAS dot product sums in the order of the
    release profile, so Python and the kernel must agree bit for bit on every CPU. Without effect
    once the interpolator itself sums in a fixed order instead of calling ``np.dot``.
    """

    def __init__(self, numpy_module: Any = np) -> None:
        self._numpy = numpy_module

    def __getattr__(self, name: str) -> Any:
        return getattr(self._numpy, name)

    @staticmethod
    def dot(a: Any, b: Any) -> float:
        return sequential_dot(a, b)


_reference_profile_cache: dict[str, Optional[dict[str, Any]]] = {}


def reset_caches() -> None:
    """Forget the detected numerical profile and the table hash (tests)."""
    _reference_profile_cache.clear()
    _table_sha256.cache_clear()


def reference_profile(native: Any = None) -> Optional[dict[str, Any]]:
    """Numerical profile that reproduces this process' Python arithmetic, if there is one.

    Probes ``np.interp`` (fused multiply-add or not) and the expected direct consumption of the
    self-consumption interpolator (table backend plus ``np.dot`` kernel) against the kernel's
    variants. ``None`` when no variant matches every probe.
    """
    from akkudoktoreos.prediction.interpolator import get_eos_load_interpolator
    from akkudoktoreos.utils.gridinterpolator import (
        configured_grid_interpolator_backend,
    )

    native = native if native is not None else _import_native()
    if native is None:
        return None
    key = str(configured_grid_interpolator_backend())
    if key in _reference_profile_cache:
        return _reference_profile_cache[key]

    rng = random.Random(20261110)  # noqa: S311 - fixed probes, not security relevant
    fma: Optional[bool] = None
    for candidate in (False, True):
        ok = True
        for _ in range(400):
            xp = sorted(rng.uniform(0.0, 1e4) for _ in range(rng.randint(2, 40)))
            if len(set(xp)) != len(xp):
                continue
            fp = [rng.uniform(-50.0, 50.0) for _ in xp]
            x = rng.uniform(xp[0], xp[-1])
            if float(np.interp(x, xp, fp)) != native.interp(x, xp, fp, candidate):
                ok = False
                break
        if ok:
            fma = candidate
            break

    interpolator = get_eos_load_interpolator()
    dot_kernel: Optional[str] = None
    for kernel in native.DOT_KERNELS:
        ok = True
        for _ in range(300):
            load_w = rng.uniform(50.0, 6000.0)
            pv_w = rng.uniform(1.0, 9000.0)
            expected = interpolator._expected_direct_consumption(load_w, pv_w)
            if expected != native.expected_direct_consumption(kernel, load_w, pv_w):
                ok = False
                break
        if ok:
            dot_kernel = kernel
            break

    profile = None if fma is None or dot_kernel is None else {"dot": dot_kernel, "interp_fma": fma}
    _reference_profile_cache[key] = profile
    return profile


# --------------------------------------------------------------------------- request / result


class NativeUnsupported(Exception):
    """The run cannot be done natively; the message says why."""


def _json_ready(value: Any) -> Any:
    """``value`` with NaN as ``None`` (the kernel reads ``null`` in a series as NaN).

    Python keeps NaN forecast values (a gap ends the usable forecast); JSON has no NaN.
    Infinite values have no JSON form the kernel reads, so such runs stay in Python.
    """
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if math.isinf(value):
            raise NativeUnsupported("the request contains an infinite value")
        return value
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _isoformat(value: Any) -> str:
    return value.isoformat()


def _timezone_name(value: Any) -> Optional[str]:
    name = getattr(value, "timezone_name", None)
    if name:
        return str(name)
    tzinfo = getattr(value, "tzinfo", None)
    name = getattr(tzinfo, "name", None)
    return str(name) if name else None


def build_request(
    optimization: GeneticOptimization,
    parameters: GeneticOptimizationParameters,
    worst_case: bool,
    ngen: Optional[int],
    individuals: Optional[int],
    profile: dict[str, Any],
) -> dict[str, Any]:
    """The kernel request for one run, from the run's configuration snapshot."""
    config = optimization.config
    timezone = config.general.timezone
    if timezone is None:
        raise NativeUnsupported("general.timezone is not set")
    ems = optimization.ems
    start = ems.start_datetime
    if start is None:
        raise NativeUnsupported("energy management start time is not set")

    def first_device(devices: Any) -> Any:
        if not devices:
            return None
        return next(iter(devices.values()), None)

    battery = first_device(config.devices.batteries)
    vehicle = first_device(config.devices.electric_vehicles)

    last_solution = None
    previous = ems.genetic_solution()
    if (
        previous is not None
        and previous.start_solution is not None
        and previous.start_solution_datetime is not None
    ):
        last_solution = {
            "start_solution": [float(v) for v in previous.start_solution],
            "start_solution_datetime": _isoformat(previous.start_solution_datetime),
        }

    observation = getattr(ems, "observation_datetime", None)
    return {
        "interface_version": INTERFACE_VERSION,
        "parameters": parameters.model_dump(mode="json"),
        "settings": optimization._genetic_cfg.model_dump(mode="json"),
        "prediction_hours": int(optimization._prediction_hours_cfg),
        "direct_marketing_enabled": bool(optimization._direct_marketing_enabled()),
        "timezone": str(timezone),
        "start_datetime": _isoformat(start),
        "start_datetime_timezone": _timezone_name(start),
        "observation_datetime": None if observation is None else _isoformat(observation),
        "fallback_battery_charge_rates": None if battery is None else battery.charge_rates,
        "fallback_battery_grid_export_rates": None
        if battery is None
        else battery.grid_export_rates,
        "fallback_ev_charge_rates": None if vehicle is None else vehicle.charge_rates,
        "last_solution": last_solution,
        "seed": optimization.fix_seed,
        "generations": ngen,
        "individuals": individuals,
        "worst_case": bool(worst_case),
        "threads": optimization._configured_evaluation_workers(),
        "numeric_profile": profile,
    }


def _solution_parameters(
    optimization: GeneticOptimization, parameters: GeneticOptimizationParameters
) -> GeneticOptimizationParameters:
    """The parameters as ``_optimize_ems_run`` reports them in the solution.

    The normalization onto the slot grid, plus the placeholder time windows that
    ``HomeAppliance`` writes into appliance parameters without windows.
    """
    from akkudoktoreos.devices.genetic.homeappliance import HomeAppliance

    normalized = optimization._parameters_for_slot_grid(
        optimization._parameters_for_config(parameters)
    )
    for appliance_parameters in normalized.resolved_home_appliances():
        HomeAppliance(
            parameters=appliance_parameters,
            optimization_hours=optimization._genetic_cfg.horizon_hours,
            prediction_hours=optimization.control_end_slot,
            slot_duration_h=optimization.slot_duration_h,
        )
    return normalized


@dataclass
class NativeRun:
    """A finished native run."""

    solution: GeneticSolution
    diagnostics: dict[str, Any]
    elapsed_s: float


def run_native(
    optimization: GeneticOptimization,
    parameters: GeneticOptimizationParameters,
    worst_case: bool,
    ngen: Optional[int],
    individuals: Optional[int],
    profile: dict[str, Any] = RELEASE_PROFILE,
) -> NativeRun:
    """Run the optimization in the kernel; raises on any failure.

    Needs the configuration snapshot of ``GeneticOptimization.optimize_ems`` in place, i.e.
    it replaces ``GeneticOptimization._optimize_ems_run``.
    """
    from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution

    native = _import_native()
    if native is None:
        raise NativeUnsupported(f"package '{NATIVE_PACKAGE}' is not installed")
    _check_package(native)
    request = build_request(optimization, parameters, worst_case, ngen, individuals, profile)
    started = time.perf_counter()
    try:
        response = json.loads(native.run_genetic(json.dumps(_json_ready(request), allow_nan=False)))
    except native.NativeRunError as exc:
        code, message = (exc.args + ("", ""))[:2] if exc.args else ("error", "")
        raise NativeUnsupported(f"kernel rejected the run ({code}): {message}") from exc
    elapsed = time.perf_counter() - started
    data = response["solution"]
    data["parameters"] = _solution_parameters(optimization, parameters)
    solution = GeneticSolution(**data)
    _use_python_timezones(optimization, solution)
    return NativeRun(solution, response.get("diagnostics", {}), elapsed)


def _use_python_timezones(optimization: GeneticOptimization, solution: GeneticSolution) -> None:
    """Give the solution's timestamps the timezone objects Python uses.

    JSON carries only UTC offsets, which validate into fixed-offset zones; the plan and the
    solution data frames need the named zones ``_optimize_ems_run`` uses: the EMS start for
    ``start_solution_datetime`` and ``general.timezone`` for the appliance starts.
    """
    start = optimization.ems.start_datetime
    native_start = solution.start_solution_datetime
    # Instants, not datetimes: across zones, Python never equates times in a DST fold.
    if native_start is None or native_start.timestamp() != start.timestamp():
        raise NativeUnsupported(
            f"kernel solution starts at {native_start}, the energy management run at {start}"
        )
    solution.start_solution_datetime = start
    timezone = optimization.config.general.timezone
    solution.appliance_starts = {
        device_id: [moment.in_timezone(timezone) for moment in starts]
        for device_id, starts in solution.appliance_starts.items()
    }


# --------------------------------------------------------------------------- comparison


def _same_float(a: float, b: float) -> bool:
    if math.isnan(a) and math.isnan(b):
        return True
    return struct.pack("<d", a) == struct.pack("<d", b)


def diff_values(
    left: Any, right: Any, path: str = "", out: Optional[list[str]] = None
) -> list[str]:
    """Paths where two JSON-like values differ (floats bit for bit)."""
    if out is None:
        out = []
    if isinstance(left, bool) or isinstance(right, bool):
        if left is not right:
            out.append(f"{path}: {left!r} != {right!r}")
    elif isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if isinstance(left, int) and isinstance(right, int):
            if left != right:
                out.append(f"{path}: {left!r} != {right!r}")
        elif not _same_float(float(left), float(right)):
            out.append(f"{path}: {left!r} != {right!r}")
    elif isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right), key=str):
            if key not in left or key not in right:
                out.append(f"{path}.{key}: only in {'python' if key in left else 'native'}")
            else:
                diff_values(left[key], right[key], f"{path}.{key}", out)
    elif isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        if len(left) != len(right):
            out.append(f"{path}: length {len(left)} != {len(right)}")
        for index, (a, b) in enumerate(zip(left, right)):
            diff_values(a, b, f"{path}[{index}]", out)
    elif left != right:
        out.append(f"{path}: {left!r} != {right!r}")
    return out


def _timezone_names(solution: GeneticSolution) -> dict[str, Any]:
    def name(moment: Any) -> Optional[str]:
        return None if moment is None else _timezone_name(moment)

    return {
        "start_solution_datetime": name(solution.start_solution_datetime),
        "appliance_starts": {
            device_id: [name(moment) for moment in starts]
            for device_id, starts in solution.appliance_starts.items()
        },
    }


def compare_solutions(python: GeneticSolution, native: GeneticSolution) -> list[str]:
    """Differences between two solutions (``model_dump`` paths, and timezones of timestamps)."""
    differences = diff_values(python.model_dump(mode="json"), native.model_dump(mode="json"))
    return differences + diff_values(
        _timezone_names(python), _timezone_names(native), path=".<timezone>"
    )


# With a bounded fitness cache, Python's hit/miss counters depend on the number of
# evaluation workers (a batch looks up all genomes before it stores any); the kernel
# counts like one worker. The plan does not depend on it.
CACHE_COUNTERS = ".fitness_history.fitness_cache."


def _counters_depend_on_workers(optimization: GeneticOptimization) -> bool:
    limit = optimization._genetic_cfg.fitness_cache_max_entries
    return limit is not None and limit > 0 and optimization._configured_evaluation_workers() > 1


@dataclass
class CompareReport:
    """Outcome of a ``compare`` run."""

    identical: bool
    differences: list[str]
    profile: dict[str, Any]
    profile_reproduces_python: bool
    python_s: float
    native_s: float
    python_net_euro: float
    native_net_euro: float
    native_error: str = ""
    # Differences that are expected for this configuration and not counted (see CACHE_COUNTERS).
    ignored_differences: list[str] = field(default_factory=list)


last_compare_report: Optional[CompareReport] = None


def _net_euro(solution: GeneticSolution) -> float:
    credited = solution.terminal_value.credited_euro if solution.terminal_value else 0.0
    return float(solution.result.total_balance) - float(credited)


def compare_run(
    optimization: GeneticOptimization,
    python_run: Callable[[], GeneticSolution],
    parameters: GeneticOptimizationParameters,
    start_hour: Optional[int],
    worst_case: bool,
    ngen: Optional[int],
    individuals: Optional[int],
) -> GeneticSolution:
    """Run the kernel and ``python_run``, return the Python solution and record the comparison.

    ``python_run`` is ``GeneticOptimization._optimize_ems_run`` for these arguments; the
    configuration snapshot of ``optimize_ems`` must be in place. The report is kept in
    :data:`last_compare_report` and logged. Without a seed both runs use the same random one.
    """
    global last_compare_report
    if start_hour is not None and start_hour != optimization.ems.start_datetime.hour:
        # Python raises the error for an unsynchronized start hour.
        return python_run()
    try:
        profile = reference_profile()
    except Exception as exc:  # noqa: BLE001 - the probe must not stop the Python run
        logger.warning("GENETIC compare: numerical profile probe failed: {}", exc)
        profile = None
    reproduces = profile is not None
    if optimization.fix_seed is None:
        # Both runs need the same random numbers to be comparable; Python reseeds from
        # fix_seed at the start of optimize().
        optimization.fix_seed = random.SystemRandom().randint(1, 100000000000)
        logger.info(
            "GENETIC compare: no seed configured, both runs use seed {}.", optimization.fix_seed
        )
    native_run: Optional[NativeRun] = None
    native_error = ""
    try:
        native_run = run_native(
            optimization,
            parameters,
            worst_case,
            ngen,
            individuals,
            profile if profile is not None else RELEASE_PROFILE,
        )
    except Exception as exc:  # noqa: BLE001 - reported, Python result is returned
        native_error = str(exc) or type(exc).__name__
    started = time.perf_counter()
    solution = python_run()
    python_s = time.perf_counter() - started
    if native_run is None:
        last_compare_report = CompareReport(
            identical=False,
            differences=[],
            profile=profile or RELEASE_PROFILE,
            profile_reproduces_python=reproduces,
            python_s=python_s,
            native_s=0.0,
            python_net_euro=_net_euro(solution),
            native_net_euro=math.nan,
            native_error=native_error,
        )
        logger.warning("GENETIC compare: native run failed: {}", native_error)
        return solution
    differences = compare_solutions(solution, native_run.solution)
    ignored: list[str] = []
    if _counters_depend_on_workers(optimization):
        ignored = [d for d in differences if d.startswith(CACHE_COUNTERS)]
        differences = [d for d in differences if not d.startswith(CACHE_COUNTERS)]
    report = CompareReport(
        identical=not differences,
        differences=differences,
        profile=profile or RELEASE_PROFILE,
        profile_reproduces_python=reproduces,
        python_s=python_s,
        native_s=native_run.elapsed_s,
        python_net_euro=_net_euro(solution),
        native_net_euro=_net_euro(native_run.solution),
        ignored_differences=ignored,
    )
    last_compare_report = report
    summary = (
        "GENETIC compare: {} (profile {}, python {:.2f} s, native {:.2f} s, "
        "net python {:.6f} EUR, native {:.6f} EUR)"
    )
    if report.identical:
        logger.info(
            summary,
            "identical",
            report.profile,
            report.python_s,
            report.native_s,
            report.python_net_euro,
            report.native_net_euro,
        )
    elif not reproduces:
        logger.info(
            summary + "; this CPU's Python arithmetic cannot be reproduced, so differences "
            "are expected. First: {}",
            f"{len(differences)} differences",
            report.profile,
            report.python_s,
            report.native_s,
            report.python_net_euro,
            report.native_net_euro,
            differences[:5],
        )
    else:
        logger.warning(
            summary + ". First: {}",
            f"{len(differences)} differences",
            report.profile,
            report.python_s,
            report.native_s,
            report.python_net_euro,
            report.native_net_euro,
            differences[:5],
        )
    return solution
