"""pytest plugin: compare every GENETIC optimization of a test session with the native kernel.

Each ``GeneticOptimization._optimize_ems_run`` call runs Python (the result the test sees, so
the tests behave as usual) and the native kernel (``native.compare_run``) and records the
comparison. At the end the plugin writes all reports to ``$GENETIC_COMPARE_OUT`` (JSON) and
prints a summary.

    PYTHONPATH=native/scripts pytest -p genetic_compare_plugin tests/test_genetic*.py

``GENETIC_COMPARE_DOT=sequential`` makes the self-consumption interpolator's ``np.dot`` a
sequential sum (``native.SequentialDotNumpy``; the rest of the Python code runs unchanged), so
the kernel can reproduce Python on any CPU (needed on x86-64, whose OpenBLAS kernels the kernel
does not reproduce). Without it, the comparison uses the profile it detects and only CPUs with a
known kernel (e.g. Neoverse N1, Cortex-A53/A55) give bit-identical results. If the interpolator
already sums in a fixed order (no ``np.dot``), the switch has no effect and every CPU must give
bit-identical results.

The summary records the fingerprint of the GENETIC sources and the kernel version;
``certify.py`` certifies exactly that. The comparison runs whether or not the sources are
certified, because that is the question it answers.

Tests that replace parts of the Python optimizer (``patch.object(opt, "optimize", ...)``,
broken worker pools) differ by design; they are listed under ``patched``. A request the kernel
does not support counts as ``unsupported``; any other native error (a panic, an invalid
response) is listed under ``failed`` and fails the CI job like a difference.
``tests/test_geneticnative.py`` runs the kernel itself and is left alone.
"""

from __future__ import annotations

import json
import os
from typing import Any

REPORTS: list[dict[str, Any]] = []
# Tests that run the kernel themselves.
SELF_MANAGED = "tests/test_geneticnative.py"
RUN_INFO: dict[str, Any] = {}


def pytest_configure(config: Any) -> None:
    from unittest import mock

    from akkudoktoreos.optimization.genetic import genetic as genetic_module
    from akkudoktoreos.optimization.genetic import native
    from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization

    native.reset_caches()
    if os.environ.get("GENETIC_COMPARE_DOT", "") == "sequential":
        from akkudoktoreos.prediction import interpolator

        interpolator.np = native.SequentialDotNumpy()  # type: ignore[assignment]
    original = GeneticOptimization._optimize_ems_run
    pristine = {
        "_evaluation_worker": genetic_module._evaluation_worker,
        "_evaluation_worker_init": genetic_module._evaluation_worker_init,
    }

    def patched(self: Any) -> bool:
        if isinstance(getattr(self, "optimize", None), mock.Mock):
            return True
        return any(getattr(genetic_module, name) is not fn for name, fn in pristine.items())

    def optimize_ems_run(self: Any, parameters: Any, start_hour: Any, worst_case: bool,
                         ngen: Any, individuals: Any) -> Any:
        def python_run() -> Any:
            return original(self, parameters, start_hour, worst_case, ngen, individuals)

        if os.environ.get("PYTEST_CURRENT_TEST", "").startswith(SELF_MANAGED):
            return python_run()
        native.last_compare_report = None
        try:
            return native.compare_run(self, python_run, parameters, start_hour, worst_case, ngen,
                                      individuals)
        finally:
            report = native.last_compare_report
            kernel = native._import_native()
            RUN_INFO.setdefault("fingerprint", native.source_fingerprint())
            RUN_INFO.setdefault("kernel_version", getattr(kernel, "__version__", None))
            RUN_INFO.setdefault("dot", os.environ.get("GENETIC_COMPARE_DOT", "") or "detected")
            REPORTS.append({
                "test": os.environ.get("PYTEST_CURRENT_TEST", "?").rsplit(" ", 1)[0],
                "patched": patched(self),
                "report": None if report is None else {
                    "identical": report.identical,
                    "differences": len(report.differences),
                    "first": report.differences[:8],
                    "ignored": len(report.ignored_differences),
                    "native_error": report.native_error,
                    "native_unsupported": report.native_unsupported,
                    "profile": report.profile,
                    "profile_reproduces_python": report.profile_reproduces_python,
                    "python_s": report.python_s,
                    "native_s": report.native_s,
                    "python_net_euro": report.python_net_euro,
                    "native_net_euro": report.native_net_euro,
                },
            })

    GeneticOptimization._optimize_ems_run = optimize_ems_run  # type: ignore[method-assign]


def summarize(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts per outcome; ``different`` lists the unpatched runs that differ."""
    summary: dict[str, Any] = {"runs": len(reports), "identical": 0, "unsupported": 0,
                               "patched": 0, "no_report": 0, "different": [], "failed": [],
                               **RUN_INFO}
    for entry in reports:
        report = entry["report"]
        if report is None:
            summary["no_report"] += 1
        elif report["identical"]:
            summary["identical"] += 1
        elif entry["patched"]:
            summary["patched"] += 1
        elif report["native_error"] and report["native_unsupported"]:
            summary["unsupported"] += 1
        elif report["native_error"]:
            summary["failed"].append(entry)
        else:
            summary["different"].append(entry)
    return summary


def pytest_unconfigure(config: Any) -> None:
    summary = summarize(REPORTS)
    out = os.environ.get("GENETIC_COMPARE_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            json.dump({"summary": summary, "reports": REPORTS}, handle, indent=1)
    print(
        "\nGENETIC compare: {runs} runs, {identical} identical, {unsupported} not supported natively, "
        "{patched} with patched Python, {no_report} without report, {n} different, "
        "{f} failed natively".format(
            n=len(summary["different"]),
            f=len(summary["failed"]),
            **{k: summary[k] for k in ("runs", "identical", "unsupported", "patched", "no_report")},
        )
    )
    for entry in summary["different"]:
        print("  DIFFERENT", entry["test"], entry["report"]["first"][:3])
    for entry in summary["failed"]:
        print("  FAILED", entry["test"], entry["report"]["native_error"][:200])
