"""Disposable thermal solver process, independent of the control event loop.

Only JSON request/result data crosses the boundary. No EOS singleton, adapter,
server or measurement provider is initialized by the worker entry point.
"""

import asyncio
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from akkudoktoreos.thermal.thermalheatplanning import (
        ThermalHeatPlanningRequest,
        ThermalHeatPlanningSolution,
    )
    from akkudoktoreos.thermal.thermaloptimization import (
        ThermalShadowRequest,
        ThermalShadowSolution,
    )


def _terminate(process: subprocess.Popen) -> None:
    """Reap the dedicated worker and its Windows interpreter launcher children."""
    import psutil

    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    for child in reversed(children):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    if process.poll() is None:
        if sys.platform == "linux":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
    process.wait(timeout=5)
    psutil.wait_procs(children, timeout=5)


def solve_isolated(
    request: "ThermalHeatPlanningRequest | ThermalShadowRequest",
    *,
    memory_limit_mb: int = 1024,
    grace_seconds: float = 5.0,
    cancel: Event | None = None,
) -> "ThermalHeatPlanningSolution | ThermalShadowSolution":
    """Run a finite worker; timeout, crash and cancellation never return a plan.

    Wall time includes interpreter startup and JSON transport. Linux additionally
    limits virtual address space in the child; RSS is monitored on every platform.
    RSS monitoring is sampled, not a hard kernel memory boundary on Windows.
    """
    import psutil

    from akkudoktoreos.thermal.thermalheatplanning import (
        ThermalHeatPlanningRequest,
        ThermalHeatPlanningSolution,
    )
    from akkudoktoreos.thermal.thermaloptimization import ThermalShadowSolution

    if (
        type(memory_limit_mb) is not int
        or not 128 <= memory_limit_mb <= 8192
        or not 0 < grace_seconds <= 30
    ):
        raise ValueError("Invalid solver process resource limits")
    heat_pump = isinstance(request, ThermalHeatPlanningRequest)
    solution_type = ThermalHeatPlanningSolution if heat_pump else ThermalShadowSolution
    started = time.monotonic()
    if cancel is not None and cancel.is_set():
        return solution_type(
            status="limit",
            reason="Isolated solver cancelled before startup; no plan released",
            checks={
                "solver_process_isolated": True,
                "solver_process_outcome": "cancelled",
                "solver_process_elapsed_seconds": 0.0,
                "solver_process_peak_rss_mb": 0.0,
            },
        )
    deadline = started + request.planning_time_limit_seconds + grace_seconds
    peak = 0
    outcome = "completed"
    result = None
    with tempfile.TemporaryDirectory(prefix="eos-thermal-worker-") as folder:
        directory = Path(folder)
        input_path, output_path = directory / "request.json", directory / "result.json"
        input_path.write_text(
            json.dumps(
                {
                    "kind": "heatpump" if heat_pump else "thermal",
                    "request": request.model_dump(mode="json", exclude_computed_fields=True),
                }
            ),
            encoding="utf-8",
        )
        env = dict(os.environ)
        env.update(
            OPENBLAS_NUM_THREADS="1",
            OMP_NUM_THREADS="1",
            MKL_NUM_THREADS="1",
            PYTHONUTF8="1",
            EOS_DIR=folder,
            EOS_CONFIG_DIR=folder,
            EOS_DATA_DIR=folder,
        )
        # Preserve imports from this checkout even when the parent is run by pytest.
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        env["EOS_THERMAL_WORKER_CPU_LIMIT"] = str(
            math.ceil(request.planning_time_limit_seconds + grace_seconds)
        )
        command = [
            sys.executable,
            "-m",
            "akkudoktoreos.thermal.thermalworker",
            str(input_path),
            str(output_path),
            str(memory_limit_mb),
        ]
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW
        try:
            process = subprocess.Popen(  # noqa: S603 - fixed interpreter/module; no shell
                command,
                cwd=folder,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=sys.platform == "linux",
                creationflags=creation_flags,
            )
        except OSError:
            return solution_type(
                status="error",
                reason="Isolated solver could not start; no plan released",
                checks={
                    "solver_process_isolated": True,
                    "solver_process_outcome": "start_failed",
                    "solver_process_elapsed_seconds": time.monotonic() - started,
                },
            )
        try:
            while process.poll() is None:
                if cancel is not None and cancel.is_set():
                    outcome = "cancelled"
                    break
                if time.monotonic() >= deadline:
                    outcome = "timeout"
                    break
                try:
                    monitor = psutil.Process(process.pid)
                    rss = sum(
                        p.memory_info().rss for p in [monitor, *monitor.children(recursive=True)]
                    )
                    peak = max(peak, rss)
                    if rss > memory_limit_mb * 1024 * 1024:
                        outcome = "memory_limit"
                        break
                except psutil.NoSuchProcess:
                    pass
                time.sleep(0.05)
            if outcome == "completed":
                if cancel is not None and cancel.is_set():
                    outcome = "cancelled"
                elif time.monotonic() >= deadline:
                    outcome = "timeout"
                elif process.returncode != 0 or not output_path.exists():
                    outcome = "crashed"
                else:
                    try:
                        result = solution_type.model_validate_json(output_path.read_bytes())
                    except (ValueError, OSError):
                        outcome = "invalid_result"
        finally:
            _terminate(process)
    if time.monotonic() >= deadline or (cancel is not None and cancel.is_set()):
        result = None
        outcome = "cancelled" if cancel is not None and cancel.is_set() else "timeout"
    if result is None:
        result = solution_type(
            status="limit" if outcome in {"timeout", "memory_limit", "cancelled"} else "error",
            reason=f"Isolated solver {outcome}; no plan released",
        )
    result.checks.update(
        {
            "solver_process_isolated": True,
            "solver_process_outcome": outcome,
            "solver_process_elapsed_seconds": time.monotonic() - started,
            "solver_process_peak_rss_mb": peak / 1024 / 1024,
            "solver_process_returncode": str(process.returncode),
        }
    )
    return result


def optimize_thermal_isolated(
    request: "ThermalShadowRequest", **kwargs: Any
) -> "ThermalShadowSolution":
    """Return the legacy typed result through the process boundary."""
    from akkudoktoreos.thermal.thermaloptimization import ThermalShadowSolution

    return ThermalShadowSolution.model_validate(solve_isolated(request, **kwargs).model_dump())


def optimize_heat_pump_isolated(
    request: "ThermalHeatPlanningRequest", **kwargs: Any
) -> "ThermalHeatPlanningSolution":
    """Return the heat-pump typed result through the process boundary."""
    from akkudoktoreos.thermal.thermalheatplanning import ThermalHeatPlanningSolution

    return ThermalHeatPlanningSolution.model_validate(
        solve_isolated(request, **kwargs).model_dump()
    )


async def solve_isolated_async(
    request: "ThermalHeatPlanningRequest | ThermalShadowRequest", **kwargs: Any
) -> "ThermalHeatPlanningSolution | ThermalShadowSolution":
    """Keep the event loop responsive and reap the child before cancellation exits."""
    cancel = kwargs.pop("cancel", None) or Event()
    task = asyncio.create_task(asyncio.to_thread(solve_isolated, request, cancel=cancel, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancel.set()
        await asyncio.shield(task)
        raise


def _configure_worker_limits(memory_limit_mb: int) -> None:
    """Limit only the disposable child before loading numerical libraries."""
    import psutil

    process = psutil.Process()
    if sys.platform in {"linux", "win32"}:
        available = process.cpu_affinity()
        process.cpu_affinity([available[-1]])
    if sys.platform == "win32":
        process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    if sys.platform == "linux":
        import resource

        limit = memory_limit_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        cpu_limit = int(os.environ["EOS_THERMAL_WORKER_CPU_LIMIT"])
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
        os.nice(10)


def _main() -> None:
    """Apply resource limits before importing the numeric solver, then solve JSON."""
    input_path, output_path = Path(sys.argv[1]), Path(sys.argv[2])
    _configure_worker_limits(int(sys.argv[3]))
    from akkudoktoreos.thermal.thermalheatplanning import (
        ThermalHeatPlanningRequest,
        optimize_heat_pump_shadow,
    )
    from akkudoktoreos.thermal.thermaloptimization import (
        ThermalShadowRequest,
        optimize_thermal_shadow,
    )

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    result: ThermalHeatPlanningSolution | ThermalShadowSolution
    if payload["kind"] == "heatpump":
        result = optimize_heat_pump_shadow(
            ThermalHeatPlanningRequest.model_validate(payload["request"])
        )
    elif payload["kind"] == "thermal":
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(payload["request"]))
    else:
        raise ValueError("Unknown thermal worker request kind")
    output_path.write_text(result.model_dump_json(exclude_computed_fields=True), encoding="utf-8")


if __name__ == "__main__":
    _main()
