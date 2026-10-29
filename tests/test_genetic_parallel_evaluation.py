"""Parallel fitness evaluation gives the same result as evaluating in one process."""

import multiprocessing
import os
import signal
import sys
import threading
import time
from types import SimpleNamespace

import pytest
import uvicorn

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.cache import CacheEnergyManagementStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.optimization.genetic import genetic as genetic_module
from akkudoktoreos.optimization.genetic.genetic import (
    GeneticOptimization,
    auto_evaluation_workers,
)
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.utils.datetimeutil import to_datetime

ems_eos = get_ems(init=True)  # init once

HOURS = 24


@pytest.mark.parametrize(
    "cores,cpu_limit,expected",
    [
        (1, None, 1),
        (2, None, 1),  # one core stays free
        (3, None, 2),
        (4, None, 2),
        (16, None, 2),  # at most two
        (4, 1, 1),  # container CPU limit
        (4, 2, 1),  # a quarter core stays free for the server process
        (4, 2.5, 2),
        (4, 3, 2),
        (2, 4, 1),
    ],
)
def test_auto_workers_keep_one_core_free(cores, cpu_limit, expected):
    assert auto_evaluation_workers(cores=cores, cpu_limit=cpu_limit) == expected


def _parameters(
    config_eos: ConfigEOS, workers: int, cache_entries
) -> GeneticOptimizationParameters:
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": HOURS},
            "optimization": {
                "genetic": {
                    "individuals": 30,
                    "generations": 10,
                    "tail_horizon_hours": 0,
                    "horizon_hours": HOURS,
                    "interval_sec": 3600,
                    "workers": workers,
                    "fitness_cache_max_entries": cache_entries,
                }
            },
            "feedintariff": {"direct_marketing_enabled": True},
            "devices": {
                "max_batteries": 1,
                "batteries": {
                    "battery1": {"device_id": "battery1", "grid_export_rates": [0.5, 1.0]}
                },
            },
        }
    )
    ems_eos.set_start_datetime(to_datetime().set(hour=0, minute=0))
    CacheEnergyManagementStore().clear()
    return GeneticOptimizationParameters.model_validate(
        dict(
            ems={
                "pv_prognose_wh": [0.0] * 8 + [3000.0] * 8 + [0.0] * 8,
                "strompreis_euro_pro_wh": [0.0003] * 6 + [0.0001] * 6 + [0.0004] * 12,
                "einspeiseverguetung_euro_pro_wh": [0.0001] * 12 + [0.0009] * 12,
                "preis_euro_pro_wh_akku": 0.0,
                "gesamtlast": [400.0] * HOURS,
            },
            pv_battery={
                "device_id": "battery1",
                "capacity_wh": 10000,
                "initial_soc_percentage": 40,
                "min_soc_percentage": 5,
                "max_charge_power_w": 5000,
            },
            inverter={
                "device_id": "inverter1",
                "max_power_wh": 10000,
                "battery_id": "battery1",
            },
            ev=None,
        )
    )


def _run(config_eos: ConfigEOS, workers: int, cache_entries):
    parameters = _parameters(config_eos, workers, cache_entries)
    optimization = GeneticOptimization(fixed_seed=7)
    used: list[int] = []
    original_start = optimization._start_evaluation_pool

    def start_and_record():
        original_start()
        used.append(
            0 if optimization._evaluation_pool is None else optimization._evaluation_workers
        )

    optimization._start_evaluation_pool = start_and_record  # type: ignore[method-assign]
    solution = optimization.optimize_ems(parameters=parameters, start_hour=0, ngen=6)
    assert optimization._evaluation_pool is None  # stopped after the run
    return solution, used


@pytest.mark.parametrize("cache_entries", [None, 0])
@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="requires fork")
def test_parallel_evaluation_matches_serial(config_eos: ConfigEOS, cache_entries):
    serial, serial_used = _run(config_eos, 1, cache_entries)
    parallel, parallel_used = _run(config_eos, 2, cache_entries)

    assert serial_used == [0]
    assert parallel_used == [2]
    assert parallel.start_solution == serial.start_solution
    assert parallel.result.total_balance == serial.result.total_balance
    assert parallel.fitness_history == serial.fitness_history
    assert parallel.ac_charge == serial.ac_charge
    assert parallel.dc_charge == serial.dc_charge
    assert parallel.discharge_allowed == serial.discharge_allowed
    assert parallel.battery_grid_export_factor == serial.battery_grid_export_factor


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="requires fork")
def test_failed_batch_falls_back_to_serial(config_eos: ConfigEOS, monkeypatch):
    serial, _ = _run(config_eos, 1, None)

    def broken_worker(genome):
        raise RuntimeError("worker lost")

    monkeypatch.setattr(genetic_module, "_evaluation_worker", broken_worker)
    fallback, used = _run(config_eos, 2, None)

    assert used == [2]
    assert fallback.start_solution == serial.start_solution
    assert fallback.ac_charge == serial.ac_charge


class _PoolOnly(GeneticOptimization):
    """Just the evaluation pool of an optimization: two unpinned workers."""

    def _configured_evaluation_workers(self) -> int:
        return 2

    def _pin_workers(self) -> bool:
        return False


def _square(value: int) -> int:
    return value * value


_original_evaluation_worker = genetic_module._evaluation_worker


def _slow_evaluation_worker(genome):
    time.sleep(5)  # far beyond the (patched) batch timeout
    return _original_evaluation_worker(genome)


@pytest.fixture
def server_signal_handlers():
    """Run the test under the signal handlers uvicorn.run() installs for the server.

    uvicorn's handler only asks the server to shut down; the evaluation workers
    are forked from that process and inherit it.
    """
    server = uvicorn.Server(uvicorn.Config("akkudoktoreos.server.eos:app"))
    with server.capture_signals():
        assert signal.getsignal(signal.SIGTERM) == server.handle_exit
        yield


def _in_thread(function, timeout: float, rescue) -> bool:
    """Run ``function`` in a thread; on timeout call ``rescue`` and report a hang."""
    thread = threading.Thread(target=function, daemon=True)
    thread.start()
    thread.join(timeout)
    if not thread.is_alive():
        return True
    rescue()  # kill the stuck workers so the test run itself does not hang
    thread.join(timeout)
    return False


def _kill(processes) -> None:
    for process in processes:
        if process.is_alive():
            os.kill(process.pid, signal.SIGKILL)


def _resume(pid: int) -> None:
    """SIGCONT a paused worker; the stop may already have killed and reaped it."""
    try:
        os.kill(pid, signal.SIGCONT)
    except ProcessLookupError:
        pass


def _worker_signal_dispositions(_task: int) -> tuple[bool, bool]:
    """In a worker: (SIGTERM is ignored, SIGINT is ignored)."""
    return (
        signal.getsignal(signal.SIGTERM) is signal.SIG_IGN,
        signal.getsignal(signal.SIGINT) is signal.SIG_IGN,
    )


def _slow_square(value: int) -> int:
    time.sleep(0.5)
    return value * value


_original_evaluation_worker_init = genetic_module._evaluation_worker_init


def _late_evaluation_worker_init(*args):
    time.sleep(2)  # a worker that starts late on a busy machine
    _original_evaluation_worker_init(*args)


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="requires fork")
def test_workers_do_not_run_server_signal_handlers(server_signal_handlers):
    """Workers ignore SIGTERM/SIGINT instead of running the server's handlers."""
    optimization = _PoolOnly.__new__(_PoolOnly)
    starter = threading.Thread(target=optimization._start_evaluation_pool)
    starter.start()  # like the server: the run is not on the main thread
    starter.join()
    pool = optimization._evaluation_pool
    assert pool is not None
    try:
        workers = list(pool._pool)
        dispositions = pool.map(_worker_signal_dispositions, range(8), chunksize=1)
    finally:
        stopped = _in_thread(
            optimization._stop_evaluation_pool, 30, lambda: _kill(list(pool._pool))
        )
    assert set(dispositions) == {(True, True)}
    assert stopped, "stopping the pool hung"
    assert not any(worker.is_alive() for worker in workers)
    # The server process itself keeps its handlers.
    assert signal.getsignal(signal.SIGTERM) not in (signal.SIG_DFL, signal.SIG_IGN)


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="requires fork")
def test_sigterm_to_the_process_group_does_not_break_a_batch(server_signal_handlers):
    """A service manager stops the server with SIGTERM to all its processes.

    The workers finish the running batch; the server process decides when the
    run ends and stops them.
    """
    optimization = _PoolOnly.__new__(_PoolOnly)
    optimization._start_evaluation_pool()
    pool = optimization._evaluation_pool
    assert pool is not None
    try:
        pool.map(_square, range(4))
        workers = list(pool._pool)
        batch = pool.map_async(_slow_square, range(8), chunksize=1)
        time.sleep(0.2)
        for worker in workers:
            os.kill(worker.pid, signal.SIGTERM)
        results = batch.get(timeout=30)
        alive = [worker.is_alive() for worker in workers]
    finally:
        stopped = _in_thread(
            optimization._stop_evaluation_pool, 30, lambda: _kill(list(pool._pool))
        )
    assert results == [value * value for value in range(8)]
    assert alive == [True, True]
    assert stopped, "stopping the pool hung"


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="needs Linux semaphores and /proc"
)
def test_pool_stop_with_a_late_idle_worker_does_not_hang(server_signal_handlers):
    """Stopping the pool must not depend on which process wins the task-queue lock.

    Pool.terminate() takes the read lock of the task queue for good. A worker
    that has not got its stop sentinel by then (on a busy machine it may simply
    not have been scheduled) can only end through SIGTERM. Freezing that worker
    for a moment makes the parent win the lock every time.
    """
    optimization = _PoolOnly.__new__(_PoolOnly)
    optimization._start_evaluation_pool()
    pool = optimization._evaluation_pool
    assert pool is not None
    pool.map(_square, range(20))
    workers = list(pool._pool)
    time.sleep(0.5)  # both idle: one reads the task pipe, one waits for its lock

    def waits_for_lock(worker) -> bool:
        try:
            with open(f"/proc/{worker.pid}/syscall") as syscall:
                return syscall.read().split()[0] in ("202", "98")  # futex x86_64/aarch64
        except (OSError, IndexError):
            return False

    late = next((worker for worker in workers if waits_for_lock(worker)), None)
    if late is None:
        _in_thread(optimization._stop_evaluation_pool, 30, lambda: _kill(list(pool._pool)))
        pytest.skip("cannot tell which worker waits for the lock")
    os.kill(late.pid, signal.SIGSTOP)
    resume = threading.Timer(1.0, _resume, (late.pid,))
    resume.start()
    try:
        stopped = _in_thread(
            optimization._stop_evaluation_pool, 30, lambda: _kill(list(pool._pool))
        )
    finally:
        resume.join()
    assert stopped, "stopping the pool hung"


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="needs Linux semaphores and /proc"
)
def test_pool_stop_never_kills_the_worker_holding_the_task_queue(server_signal_handlers):
    """The idle worker that reads the task pipe holds the queue's read lock.

    Killing it would leave the lock taken for good and terminate() waiting for
    it forever. While it is paused, the stop has to wait until it resumes,
    takes its sentinel and releases the lock.
    """
    optimization = _PoolOnly.__new__(_PoolOnly)
    optimization._start_evaluation_pool()
    pool = optimization._evaluation_pool
    assert pool is not None
    pool.map(_square, range(20))
    workers = list(pool._pool)
    time.sleep(0.5)  # both idle: one reads the task pipe, one waits for its lock

    def reads_task_pipe(worker) -> bool:
        try:
            with open(f"/proc/{worker.pid}/syscall") as syscall:
                return syscall.read().split()[0] in ("0", "63")  # read x86_64/aarch64
        except (OSError, IndexError):
            return False

    reader = next((worker for worker in workers if reads_task_pipe(worker)), None)
    if reader is None:
        _in_thread(optimization._stop_evaluation_pool, 30, lambda: _kill(list(pool._pool)))
        pytest.skip("cannot tell which worker reads the task pipe")
    os.kill(reader.pid, signal.SIGSTOP)
    resume = threading.Timer(3.0, _resume, (reader.pid,))
    started = time.monotonic()
    resume.start()
    try:
        stopped = _in_thread(
            optimization._stop_evaluation_pool, 30, lambda: _kill(list(pool._pool))
        )
    finally:
        resume.join()
    # Killing the paused reader would leave the lock taken and the stop hung
    # (caught above). Once it resumed and released the lock, terminate() may
    # still kill it before it exits on its own - that is fine.
    assert stopped, "stopping the pool hung"
    assert time.monotonic() - started >= 2.5, "the stop did not wait for the paused reader"
    assert reader.exitcode is not None


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="requires fork")
def test_batch_timeout_under_server_signal_handlers_falls_back_to_serial(
    config_eos: ConfigEOS, monkeypatch, server_signal_handlers
):
    """A batch that times out stops the busy workers and the run continues serially."""
    serial, _ = _run(config_eos, 1, None)

    monkeypatch.setattr(genetic_module, "EVALUATION_BATCH_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(genetic_module, "_evaluation_worker", _slow_evaluation_worker)
    outcome: dict = {}

    def run_parallel() -> None:
        outcome["result"] = _run(config_eos, 2, None)

    finished = _in_thread(run_parallel, 60, lambda: _kill(multiprocessing.active_children()))

    assert finished, "stopping the busy workers after the batch timeout hung"
    fallback, used = outcome["result"]
    assert used == [2]
    assert fallback.start_solution == serial.start_solution
    assert fallback.ac_charge == serial.ac_charge


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="requires fork")
def test_pool_stop_kills_a_worker_that_got_sigterm_before_its_init(
    server_signal_handlers, monkeypatch
):
    """A worker still starting up when the pool stops runs the server's handler.

    It would survive SIGTERM, finish its start and then wait for the task queue
    that terminate() keeps locked. The pool stops its workers with SIGKILL.
    """
    monkeypatch.setattr(genetic_module, "_evaluation_worker_init", _late_evaluation_worker_init)
    optimization = _PoolOnly.__new__(_PoolOnly)
    optimization._start_evaluation_pool()
    pool = optimization._evaluation_pool
    assert pool is not None
    workers = list(pool._pool)
    started = time.monotonic()
    stopped = _in_thread(optimization._stop_evaluation_pool, 30, lambda: _kill(list(pool._pool)))
    assert stopped, "stopping the pool hung"
    assert time.monotonic() - started < 10
    assert not any(worker.is_alive() for worker in workers)


def test_unsupported_fork_falls_back_to_serial(config_eos: ConfigEOS, monkeypatch):
    serial, _ = _run(config_eos, 1, None)

    def unsupported_context(method):
        assert method == "fork"
        raise ValueError("fork is unavailable")

    monkeypatch.setattr(genetic_module.multiprocessing, "get_context", unsupported_context)
    fallback, used = _run(config_eos, 2, None)

    assert used == [0]
    assert fallback.start_solution == serial.start_solution
    assert fallback.result.total_balance == serial.result.total_balance
    assert fallback.ac_charge == serial.ac_charge
    assert fallback.dc_charge == serial.dc_charge


def test_cgroup_cpu_limit_reads_own_and_parent_cgroups(tmp_path, monkeypatch):
    from akkudoktoreos.optimization.genetic.genetic import _cgroup_cpu_limit

    service = tmp_path / "system.slice" / "eos.service"
    service.mkdir(parents=True)
    (tmp_path / "cpu.max").write_text("max 100000\n")
    (tmp_path / "system.slice" / "cpu.max").write_text("max 100000\n")
    (service / "cpu.max").write_text("150000 100000\n")

    real_open = open

    def fake_open(path, *args, **kwargs):
        if path == "/proc/self/cgroup":
            import io

            return io.StringIO("0::/system.slice/eos.service\n")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    assert _cgroup_cpu_limit(str(tmp_path)) == 1.5
    (service / "cpu.max").write_text("max 100000\n")
    assert _cgroup_cpu_limit(str(tmp_path)) is None
    (tmp_path / "cpu.max").write_text("100000 100000\n")
    assert _cgroup_cpu_limit(str(tmp_path)) == 1  # a parent limit counts too


@pytest.mark.parametrize(
    "workers,allowed,expected",
    [
        (2, {0, 1, 2, 3}, {2, 3}),  # core 0/1 stay free for the server and the system
        (3, {0, 1, 2, 3}, {1, 2, 3}),
        (1, {0, 1}, {1}),
        (2, {0, 1}, None),  # no spare core -> no pinning
        (2, {4, 5, 6, 7}, {6, 7}),  # restricted container cpuset
        (0, {0, 1, 2, 3}, None),
    ],
)
def test_worker_cpu_set(workers, allowed, expected):
    from akkudoktoreos.optimization.genetic.genetic import worker_cpu_set

    assert worker_cpu_set(workers, allowed) == expected


class _Fitness:
    def __init__(self) -> None:
        self.values: tuple[float, ...] = ()

    @property
    def valid(self):
        return bool(self.values)


class _Individual(list):
    extra_data: tuple[float, float, float]

    def __init__(self, genes):
        super().__init__(genes)
        self.fitness = _Fitness()


def test_identical_genomes_of_a_batch_are_evaluated_once_without_cache():
    """Repeats get the repaired genome and results of the one evaluation."""
    optimization = GeneticOptimization.__new__(GeneticOptimization)
    evaluated: list[list[int]] = []

    def evaluate(individual):
        evaluated.append(list(individual))
        individual[0] = 9  # an evaluation may repair the genome in place
        individual.extra_data = (float(sum(individual)), 0.0, 0.0)
        return (float(sum(individual)),)

    optimization.toolbox = SimpleNamespace(map=map, evaluate=evaluate)
    population = [_Individual(genes) for genes in ([1, 2, 3], [4, 5, 6], [1, 2, 3], [7, 8, 9])]
    done = _Individual([0, 0, 0])
    done.fitness.values = (1.0,)

    assert optimization._evaluate_invalid(population + [done]) == 4

    assert evaluated == [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
    assert optimization._duplicate_evaluations_skipped == 1
    assert population[2] == population[0] == [9, 2, 3]
    assert population[2].fitness.values == population[0].fitness.values == (14.0,)
    assert population[2].extra_data == population[0].extra_data
    assert population[1].fitness.values == (20.0,)
    assert done == [0, 0, 0] and done.fitness.values == (1.0,)
