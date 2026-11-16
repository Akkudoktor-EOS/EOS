"""Isolated process failures, event-loop responsiveness and recovery."""
import asyncio
import subprocess
import sys
import time
from threading import Event

import psutil
import pytest
from test_thermalperformance import performance_case

from akkudoktoreos.thermal import thermalworker as worker
from akkudoktoreos.thermal.thermalheatplanning import ThermalHeatPlanningRequest


def request():
    return ThermalHeatPlanningRequest.model_validate(performance_case("direct", 96, 10.0))


@pytest.mark.parametrize("fault,expected", [("hang", "timeout"), ("crash", "crashed"), ("memory", "memory_limit"), ("invalid", "invalid_result")])
def test_worker_fault_is_reaped_without_plan(tmp_path, monkeypatch, fault, expected):
    original = subprocess.Popen
    processes = []
    def spawn(command, **kwargs):
        if fault == "hang":
            code = "import time; time.sleep(60)"
        elif fault == "memory":
            code = "import time; data=bytearray(256*1024*1024); time.sleep(60)"
        elif fault == "invalid":
            code = "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('{}')"
        else:
            code = "import os; os._exit(17)"
        child = original([sys.executable, "-c", code, command[-2]], **kwargs)
        processes.append(child)
        return child
    monkeypatch.setattr(worker.subprocess, "Popen", spawn)
    payload = request().model_copy(update={"planning_time_limit_seconds": 0.5 if fault == "hang" else 5.0})
    started = time.monotonic()
    result = worker.solve_isolated(payload, memory_limit_mb=128, grace_seconds=0.5)
    assert time.monotonic() - started < 8
    assert result.plan is None
    assert result.checks["solver_process_outcome"] == expected
    assert all(p.poll() is not None for p in processes)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["task", "manual_off_event"])
async def test_cancellation_reaps_worker_while_event_loop_stays_responsive(monkeypatch, method):
    original = subprocess.Popen
    started = Event()
    processes = []
    def spawn(command, **kwargs):
        child = original([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        processes.append(child)
        started.set()
        return child
    monkeypatch.setattr(worker.subprocess, "Popen", spawn)
    cancel = Event()
    task = asyncio.create_task(worker.solve_isolated_async(request(), cancel=cancel))
    assert await asyncio.to_thread(started.wait, 5)
    ticks = 0
    for _ in range(10):
        await asyncio.sleep(0.01)
        ticks += 1
    assert ticks == 10 and not task.done()
    descendants = [child for p in processes for child in psutil.Process(p.pid).children(recursive=True)]
    if method == "task":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    else:
        cancel.set()
        result = await asyncio.wait_for(task, 5)
        assert result.plan is None
        assert result.checks["solver_process_outcome"] == "cancelled"
    assert all(p.poll() is not None for p in processes)
    assert all(not child.is_running() for child in descendants)


def test_pre_cancelled_worker_returns_no_plan(monkeypatch):
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **kw: pytest.fail("Cancelled work started"))
    cancel = Event()
    cancel.set()
    result = worker.solve_isolated(request(), cancel=cancel)
    assert result.plan is None
    assert result.checks["solver_process_outcome"] == "cancelled"


def test_real_worker_recovers_after_a_failed_process(monkeypatch):
    from conftest import fleet_request_data as request_data

    from akkudoktoreos.thermal.thermaloptimization import ThermalShadowRequest
    original = subprocess.Popen
    with monkeypatch.context() as patch:
        patch.setattr(worker.subprocess, "Popen", lambda command, **kwargs: original(
            [sys.executable, "-c", "import os; os._exit(17)"], **kwargs))
        assert worker.solve_isolated(request()).plan is None
    result = worker.solve_isolated(ThermalShadowRequest.model_validate(request_data(4, constant=True)))
    assert result.plan is not None, result.reason
    assert result.checks["solver_process_outcome"] == "completed"


def test_process_start_failure_is_a_typed_no_plan_result(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("synthetic process limit")
    monkeypatch.setattr(worker.subprocess, "Popen", fail)
    result = worker.solve_isolated(request())
    assert result.plan is None and result.status == "error"
    assert result.checks["solver_process_outcome"] == "start_failed"


def test_linux_limits_are_configured_before_solver_import(monkeypatch):
    import types
    calls = []
    process = types.SimpleNamespace(cpu_affinity=lambda value=None: calls.append(("cpu", value)) if value else [2, 3])
    monkeypatch.setattr("psutil.Process", lambda: process)
    monkeypatch.setattr(worker.sys, "platform", "linux")
    monkeypatch.setattr(worker.os, "nice", lambda value: calls.append(("nice", value)), raising=False)
    monkeypatch.setenv("EOS_THERMAL_WORKER_CPU_LIMIT", "65")
    monkeypatch.setitem(sys.modules, "resource", types.SimpleNamespace(
        RLIMIT_AS="address", RLIMIT_CPU="cpu_time",
        setrlimit=lambda name, value: calls.append((name,value))))
    worker._configure_worker_limits(1024)
    assert ("cpu", [3]) in calls
    assert ("address", (1024**3,1024**3)) in calls
    assert ("cpu_time", (65,65)) in calls
    assert ("nice", 10) in calls
