"""Tests for RetentionManager and JobState."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from loguru import logger

import akkudoktoreos.server.retentionmanager
from akkudoktoreos.server.retentionmanager import JobState, RetentionManager

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

INTERVAL = 10.0
DUE_INTERVAL = 0.001  # non-zero so interval() does not fall back to fallback_interval
FALLBACK = 300.0


def make_config_getter(interval: float = INTERVAL) -> Any:
    """Return a simple config getter that always yields ``interval`` for any key."""
    return lambda key: interval


def make_config_getter_none() -> Any:
    """Return a config getter that always yields ``None`` (job disabled)."""
    return lambda key: None


def make_manager(interval: float = INTERVAL, shutdown_timeout: float = 5.0) -> RetentionManager:
    """Return a ``RetentionManager`` backed by a fixed-interval config getter."""
    return RetentionManager(make_config_getter(interval), shutdown_timeout=shutdown_timeout)


def make_manager_none(shutdown_timeout: float = 5.0) -> RetentionManager:
    """Return a ``RetentionManager`` whose config getter always returns None (all jobs disabled)."""
    return RetentionManager(make_config_getter_none(), shutdown_timeout=shutdown_timeout)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRetentionManager:
    """Tests for :class:`RetentionManager` and :class:`JobState`."""

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def test_init_stores_config_getter(self) -> None:
        """The config getter passed to __init__ is stored and forwarded to jobs."""
        getter = make_config_getter()
        manager = RetentionManager(getter)
        assert manager._config_getter is getter

    def test_init_empty_job_registry(self) -> None:
        """A newly created manager has no registered jobs."""
        manager = make_manager()
        assert manager._jobs == {}

    # ------------------------------------------------------------------
    # register / unregister
    # ------------------------------------------------------------------

    def test_register_adds_job(self) -> None:
        """Registering a function adds a JobState entry."""
        manager = make_manager()
        func = MagicMock()
        manager.register("job1", func, interval_attr="some/key")
        assert "job1" in manager._jobs

    def test_register_job_state_fields(self) -> None:
        """Registered JobState carries the correct initial field values."""
        manager = make_manager()
        func = MagicMock()
        manager.register("job1", func, interval_attr="some/key", fallback_interval=60.0)
        job = manager._jobs["job1"]
        assert job.name == "job1"
        assert job.func is func
        assert job.interval_attr == "some/key"
        assert job.fallback_interval == 60.0
        assert job.config_getter is manager._config_getter
        assert job.on_exception is None
        assert job.last_run_at == 0.0
        assert job.run_count == 0
        assert job.is_running is False

    def test_register_stores_on_exception(self) -> None:
        """The on_exception callback is stored on the JobState."""
        manager = make_manager()
        handler = MagicMock()
        manager.register("job1", MagicMock(), interval_attr="k", on_exception=handler)
        assert manager._jobs["job1"].on_exception is handler

    def test_register_duplicate_raises(self) -> None:
        """Registering the same name twice raises ValueError."""
        manager = make_manager()
        manager.register("job1", MagicMock(), interval_attr="k")
        with pytest.raises(ValueError, match="job1"):
            manager.register("job1", MagicMock(), interval_attr="k")

    def test_unregister_removes_job(self) -> None:
        """Unregistering a job removes it from the registry."""
        manager = make_manager()
        manager.register("job1", MagicMock(), interval_attr="k")
        manager.unregister("job1")
        assert "job1" not in manager._jobs

    def test_unregister_missing_job_is_noop(self) -> None:
        """Unregistering a non-existent job does not raise."""
        manager = make_manager()
        manager.unregister("nonexistent")  # must not raise

    # ------------------------------------------------------------------
    # JobState.interval()
    # ------------------------------------------------------------------

    def test_job_interval_from_config_getter(self) -> None:
        """JobState.interval() returns the value provided by config_getter."""
        manager = make_manager(interval=42.0)
        manager.register("job1", MagicMock(), interval_attr="k")
        assert manager._jobs["job1"].interval() == 42.0

    def test_job_interval_none_when_config_returns_none(self) -> None:
        """JobState.interval() returns None when config_getter returns None (job disabled)."""
        manager = make_manager_none()
        manager.register("job1", MagicMock(), interval_attr="k", fallback_interval=FALLBACK)
        assert manager._jobs["job1"].interval() is None

    def test_job_interval_none_does_not_fall_back(self) -> None:
        """A None config value must NOT fall back to fallback_interval -- None means disabled."""
        manager = make_manager_none()
        manager.register("job1", MagicMock(), interval_attr="k", fallback_interval=99.0)
        # If None incorrectly fell back, this would return 99.0 instead of None
        assert manager._jobs["job1"].interval() is None

    def test_job_interval_fallback_on_key_error(self) -> None:
        """JobState.interval() uses fallback_interval when config_getter raises KeyError."""
        manager = RetentionManager(lambda key: (_ for _ in ()).throw(KeyError(key)))
        manager.register("job1", MagicMock(), interval_attr="k", fallback_interval=99.0)
        assert manager._jobs["job1"].interval() == 99.0

    def test_job_interval_fallback_on_index_error(self) -> None:
        """JobState.interval() uses fallback_interval when config_getter raises IndexError."""
        manager = RetentionManager(lambda key: (_ for _ in ()).throw(IndexError()))
        manager.register("job1", MagicMock(), interval_attr="k", fallback_interval=77.0)
        assert manager._jobs["job1"].interval() == 77.0

    def test_job_interval_fallback_on_zero_value(self) -> None:
        """JobState.interval() uses fallback_interval when config_getter returns zero."""
        manager = RetentionManager(lambda key: 0)
        manager.register("job1", MagicMock(), interval_attr="k", fallback_interval=55.0)
        assert manager._jobs["job1"].interval() == 55.0

    # ------------------------------------------------------------------
    # JobState.is_due()
    # ------------------------------------------------------------------

    def test_job_is_due_when_never_run(self) -> None:
        """A job is always due when it has never been run (last_run_at == 0.0)."""
        manager = make_manager(interval=INTERVAL)
        manager.register("job1", MagicMock(), interval_attr="k")
        assert manager._jobs["job1"].is_due() is True

    def test_job_is_not_due_immediately_after_run(self) -> None:
        """A job is not due immediately after last_run_at is set to now."""
        manager = make_manager(interval=INTERVAL)
        manager.register("job1", MagicMock(), interval_attr="k")
        manager._jobs["job1"].last_run_at = time.monotonic()
        assert manager._jobs["job1"].is_due() is False

    def test_job_is_due_after_interval_elapsed(self) -> None:
        """A job becomes due once the interval has passed since last_run_at."""
        manager = make_manager(interval=1.0)
        manager.register("job1", MagicMock(), interval_attr="k")
        manager._jobs["job1"].last_run_at = time.monotonic() - 2.0  # 2 s ago > 1 s interval
        assert manager._jobs["job1"].is_due() is True

    def test_job_is_never_due_when_interval_is_none(self) -> None:
        """is_due() returns False when interval() is None, even if last_run_at is 0."""
        manager = make_manager_none()
        manager.register("job1", MagicMock(), interval_attr="k")
        job = manager._jobs["job1"]
        # last_run_at == 0.0 would make any enabled job due immediately
        assert job.last_run_at == 0.0
        assert job.is_due() is False

    def test_job_is_never_due_when_disabled_regardless_of_last_run(self) -> None:
        """is_due() stays False for a disabled job even long after its last run."""
        manager = make_manager_none()
        manager.register("job1", MagicMock(), interval_attr="k")
        job = manager._jobs["job1"]
        job.last_run_at = time.monotonic() - 365 * 24 * 3600  # "ran" a year ago
        assert job.is_due() is False

    # ------------------------------------------------------------------
    # JobState.summary()
    # ------------------------------------------------------------------

    def test_summary_keys(self) -> None:
        """summary() returns all expected keys including interval_s."""
        manager = make_manager()
        manager.register("job1", MagicMock(), interval_attr="k")
        summary = manager._jobs["job1"].summary()
        assert set(summary.keys()) == {
            "name", "interval_attr", "interval_s", "last_run_at",
            "last_duration_s", "last_error", "run_count", "is_running",
        }

    def test_summary_interval_s_reflects_config(self) -> None:
        """summary()['interval_s'] matches the value returned by interval()."""
        manager = make_manager(interval=42.0)
        manager.register("job1", MagicMock(), interval_attr="k")
        assert manager._jobs["job1"].summary()["interval_s"] == 42.0

    def test_summary_interval_s_is_none_when_disabled(self) -> None:
        """summary()['interval_s'] is None when the job is disabled via config."""
        manager = make_manager_none()
        manager.register("job1", MagicMock(), interval_attr="k")
        assert manager._jobs["job1"].summary()["interval_s"] is None

    def test_summary_values(self) -> None:
        """summary() reflects the current JobState values."""
        manager = make_manager()
        manager.register("job1", MagicMock(), interval_attr="my/key")
        job = manager._jobs["job1"]
        job.last_run_at = 1234.5
        job.last_duration = 0.12345
        job.last_error = "oops"
        job.run_count = 3
        job.is_running = True
        s = job.summary()
        assert s["name"] == "job1"
        assert s["interval_attr"] == "my/key"
        assert s["last_run_at"] == 1234.5
        assert s["last_duration_s"] == 0.1235  # rounded to 4 dp
        assert s["last_error"] == "oops"
        assert s["run_count"] == 3
        assert s["is_running"] is True

    # ------------------------------------------------------------------
    # status()
    # ------------------------------------------------------------------

    def test_status_empty(self) -> None:
        """status() returns an empty list when no jobs are registered."""
        assert make_manager().status() == []

    def test_status_contains_all_jobs(self) -> None:
        """status() returns one entry per registered job."""
        manager = make_manager()
        manager.register("a", MagicMock(), interval_attr="k1")
        manager.register("b", MagicMock(), interval_attr="k2")
        names = {s["name"] for s in manager.status()}
        assert names == {"a", "b"}

    def test_status_shows_disabled_job(self) -> None:
        """status() includes disabled jobs with interval_s == None."""
        manager = make_manager_none()
        manager.register("disabled", MagicMock(), interval_attr="k")
        entries = manager.status()
        assert len(entries) == 1
        assert entries[0]["interval_s"] is None

    # ------------------------------------------------------------------
    # tick() -- job dispatch
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_tick_runs_due_sync_job(self) -> None:
        """tick() executes a sync job that is due."""
        manager = make_manager(interval=DUE_INTERVAL)
        func = MagicMock()
        manager.register("job1", func, interval_attr="k")
        await manager.tick()
        await asyncio.sleep(0)  # yield so ensure_future tasks are scheduled
        await asyncio.sleep(0)  # second yield ensures tasks have started
        await manager.shutdown()
        func.assert_called_once()

    @pytest.mark.asyncio
    async def test_tick_runs_due_async_job(self) -> None:
        """tick() executes an async job that is due."""
        manager = make_manager(interval=DUE_INTERVAL)
        func = AsyncMock()
        manager.register("job1", func, interval_attr="k")
        await manager.tick()
        await asyncio.sleep(0)  # yield so ensure_future tasks are scheduled
        await asyncio.sleep(0)  # second yield ensures tasks have started
        await manager.shutdown()
        func.assert_called_once()

    @pytest.mark.asyncio
    async def test_tick_skips_not_due_job(self) -> None:
        """tick() does not execute a job whose interval has not yet elapsed."""
        manager = make_manager(interval=9999.0)
        func = MagicMock()
        manager.register("job1", func, interval_attr="k")
        manager._jobs["job1"].last_run_at = time.monotonic()  # just ran
        await manager.tick()
        await asyncio.sleep(0)
        await manager.shutdown()
        func.assert_not_called()

    @pytest.mark.asyncio
    async def test_tick_skips_disabled_job(self) -> None:
        """tick() never executes a job whose interval is None, even if never run before."""
        manager = make_manager_none()
        func = MagicMock()
        manager.register("disabled", func, interval_attr="k")
        job = manager._jobs["disabled"]
        # last_run_at == 0.0 would fire any enabled job immediately
        assert job.last_run_at == 0.0
        await manager.tick()
        await asyncio.sleep(0)
        await manager.shutdown()
        func.assert_not_called()

    @pytest.mark.asyncio
    async def test_tick_skips_disabled_job_adds_no_task(self) -> None:
        """tick() adds no task to _running_tasks for a disabled job."""
        manager = make_manager_none()
        manager.register("disabled", AsyncMock(), interval_attr="k")
        await manager.tick()
        await asyncio.sleep(0)
        assert len(manager._running_tasks) == 0

    @pytest.mark.asyncio
    async def test_tick_enabled_and_disabled_jobs_mixed(self) -> None:
        """tick() fires enabled jobs and silently skips disabled ones in the same manager."""
        results: list[str] = []

        async def enabled_job() -> None:
            results.append("ran")

        manager = RetentionManager(
            lambda key: DUE_INTERVAL if key == "enabled/interval" else None,
            shutdown_timeout=5.0,
        )
        manager.register("enabled", enabled_job, interval_attr="enabled/interval")
        manager.register("disabled", AsyncMock(), interval_attr="disabled/interval")

        await manager.tick()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await manager.shutdown()

        assert results == ["ran"], "Only the enabled job must have run"

    @pytest.mark.asyncio
    async def test_tick_skips_already_running_job(self) -> None:
        """tick() does not start a job that is still marked as running."""
        manager = make_manager(interval=DUE_INTERVAL)
        func = MagicMock()
        manager.register("job1", func, interval_attr="k")
        manager._jobs["job1"].is_running = True
        await manager.tick()
        await asyncio.sleep(0)
        await manager.shutdown()
        func.assert_not_called()

    @pytest.mark.asyncio
    async def test_tick_runs_multiple_jobs_concurrently(self) -> None:
        """tick() fires all due jobs as independent tasks."""
        manager = make_manager(interval=DUE_INTERVAL)
        results: list[str] = []

        async def job_a() -> None:
            results.append("a")

        async def job_b() -> None:
            results.append("b")

        manager.register("a", job_a, interval_attr="k")
        manager.register("b", job_b, interval_attr="k")
        await manager.tick()
        await asyncio.sleep(0)  # yield so ensure_future tasks are scheduled
        await asyncio.sleep(0)  # second yield ensures tasks have started
        await manager.shutdown()
        assert sorted(results) == ["a", "b"]

    @pytest.mark.asyncio
    async def test_tick_adds_tasks_to_running_set(self) -> None:
        """tick() adds a task to _running_tasks for each due job."""
        barrier = asyncio.Event()
        manager = make_manager(interval=DUE_INTERVAL)

        async def blocking_job() -> None:
            await barrier.wait()

        manager.register("job1", blocking_job, interval_attr="k")
        await manager.tick()
        await asyncio.sleep(0)  # yield so ensure_future tasks are scheduled
        await asyncio.sleep(0)  # second yield ensures tasks have started
        # Task is still running (barrier not set), so it must be in the set.
        assert len(manager._running_tasks) == 1
        barrier.set()
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_tick_removes_task_from_running_set_on_completion(self) -> None:
        """Completed tasks are removed from _running_tasks automatically."""
        manager = make_manager(interval=DUE_INTERVAL)
        manager.register("job1", AsyncMock(), interval_attr="k")
        await manager.tick()
        await asyncio.sleep(0)  # yield so ensure_future tasks are scheduled
        await manager.shutdown()
        assert len(manager._running_tasks) == 0

    # ------------------------------------------------------------------
    # shutdown()
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_shutdown_returns_immediately_when_no_tasks(self) -> None:
        """shutdown() completes without blocking when no tasks are running."""
        manager = make_manager()
        await manager.shutdown()  # must return promptly without raising

    @pytest.mark.asyncio
    async def test_shutdown_waits_for_in_flight_task(self) -> None:
        """shutdown() blocks until a long-running job task finishes."""
        barrier = asyncio.Event()
        finished: list[bool] = []
        manager = make_manager(interval=DUE_INTERVAL)

        async def slow_job() -> None:
            await barrier.wait()
            finished.append(True)

        manager.register("job1", slow_job, interval_attr="k")
        await manager.tick()
        await asyncio.sleep(0)  # yield so ensure_future tasks are scheduled
        await asyncio.sleep(0)  # second yield ensures tasks have started
        assert finished == []        # job still blocked
        barrier.set()
        await manager.shutdown()
        assert finished == [True]    # job completed before shutdown returned

    @pytest.mark.asyncio
    async def test_shutdown_waits_for_multiple_in_flight_tasks(self) -> None:
        """shutdown() waits for all concurrently running job tasks."""
        barrier = asyncio.Event()
        finished: list[str] = []
        manager = make_manager(interval=DUE_INTERVAL)

        async def slow_a() -> None:
            await barrier.wait()
            finished.append("a")

        async def slow_b() -> None:
            await barrier.wait()
            finished.append("b")

        manager.register("a", slow_a, interval_attr="k")
        manager.register("b", slow_b, interval_attr="k")
        await manager.tick()
        await asyncio.sleep(0)  # yield so ensure_future tasks are scheduled
        await asyncio.sleep(0)  # second yield ensures tasks have started
        assert finished == []
        barrier.set()
        await manager.shutdown()
        assert sorted(finished) == ["a", "b"]

    @pytest.mark.asyncio
    async def test_shutdown_does_not_raise_when_job_failed(self) -> None:
        """shutdown() completes without raising even if a job task raised an exception."""
        manager = make_manager(interval=DUE_INTERVAL)

        def failing_func() -> None:
            raise RuntimeError("job error")

        manager.register("job1", failing_func, interval_attr="k")
        await manager.tick()
        await asyncio.sleep(0)  # yield so ensure_future tasks are scheduled
        await manager.shutdown()  # must not raise

    @pytest.mark.asyncio
    async def test_shutdown_clears_running_tasks_set(self) -> None:
        """_running_tasks is empty after shutdown() completes."""
        manager = make_manager(interval=DUE_INTERVAL)
        manager.register("job1", AsyncMock(), interval_attr="k")
        await manager.tick()
        await asyncio.sleep(0)  # yield so ensure_future tasks are scheduled
        await manager.shutdown()
        assert manager._running_tasks == set()

    @pytest.mark.asyncio
    async def test_shutdown_timeout_returns_without_blocking(self) -> None:
        """shutdown() returns once the timeout elapses even if a job is still running."""
        stuck = asyncio.Event()  # never set -- job blocks forever
        manager = RetentionManager(make_config_getter(DUE_INTERVAL), shutdown_timeout=0.05)

        async def forever() -> None:
            await stuck.wait()

        manager.register("stuck", forever, interval_attr="k")
        await manager.tick()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # Must return within the timeout, not block forever.
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_timeout_logs_error_for_pending_jobs(self) -> None:
        """An error is logged listing jobs still running after the timeout."""
        stuck = asyncio.Event()
        manager = RetentionManager(make_config_getter(DUE_INTERVAL), shutdown_timeout=0.05)

        async def forever() -> None:
            await stuck.wait()

        manager.register("stuck_job", forever, interval_attr="k")
        await manager.tick()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        with patch.object(logger, "error") as mock_error:
            await manager.shutdown()
            assert mock_error.called, "Expected logger.error to be called on timeout"
            # All positional args joined: the stuck job name must appear.
            logged = str(mock_error.call_args_list)
            assert "stuck_job" in logged

    @pytest.mark.asyncio
    async def test_shutdown_timeout_clears_running_tasks_set(self) -> None:
        """_running_tasks is cleared even when the timeout elapses."""
        stuck = asyncio.Event()
        manager = RetentionManager(make_config_getter(DUE_INTERVAL), shutdown_timeout=0.05)

        async def forever() -> None:
            await stuck.wait()

        manager.register("stuck", forever, interval_attr="k")
        await manager.tick()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await manager.shutdown()
        assert manager._running_tasks == set()

    @pytest.mark.asyncio
    async def test_shutdown_no_error_logged_when_all_finish_in_time(self) -> None:
        """No error is logged when all tasks complete within the timeout."""
        manager = RetentionManager(make_config_getter(DUE_INTERVAL), shutdown_timeout=5.0)
        manager.register("job1", AsyncMock(), interval_attr="k")
        await manager.tick()
        await asyncio.sleep(0)

        with patch.object(logger, "error") as mock_error:
            await manager.shutdown()
            mock_error.assert_not_called()

    def test_init_stores_shutdown_timeout(self) -> None:
        """The shutdown_timeout passed to __init__ is stored on the instance."""
        manager = RetentionManager(make_config_getter(), shutdown_timeout=99.0)
        assert manager._shutdown_timeout == 99.0

    def test_init_default_shutdown_timeout(self) -> None:
        """The default shutdown_timeout is 30 seconds."""
        manager = RetentionManager(make_config_getter())
        assert manager._shutdown_timeout == 30.0

    # ------------------------------------------------------------------
    # _run_job() -- state updates
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_run_job_increments_run_count(self) -> None:
        """_run_job() increments run_count after each execution."""
        manager = make_manager()
        manager.register("job1", MagicMock(), interval_attr="k")
        job = manager._jobs["job1"]
        await manager._run_job(job)
        await manager._run_job(job)
        assert job.run_count == 2

    @pytest.mark.asyncio
    async def test_run_job_updates_last_run_at(self) -> None:
        """_run_job() sets last_run_at to a recent monotonic timestamp."""
        manager = make_manager()
        manager.register("job1", MagicMock(), interval_attr="k")
        job = manager._jobs["job1"]
        before = time.monotonic()
        await manager._run_job(job)
        assert job.last_run_at >= before

    @pytest.mark.asyncio
    async def test_run_job_updates_last_duration(self) -> None:
        """_run_job() records a non-negative last_duration."""
        manager = make_manager()
        manager.register("job1", MagicMock(), interval_attr="k")
        job = manager._jobs["job1"]
        await manager._run_job(job)
        assert job.last_duration >= 0.0

    @pytest.mark.asyncio
    async def test_run_job_clears_is_running_on_success(self) -> None:
        """is_running is False after a successful job execution."""
        manager = make_manager()
        manager.register("job1", MagicMock(), interval_attr="k")
        job = manager._jobs["job1"]
        await manager._run_job(job)
        assert job.is_running is False

    @pytest.mark.asyncio
    async def test_run_job_clears_last_error_on_success(self) -> None:
        """last_error is set to None after a successful execution."""
        manager = make_manager()
        manager.register("job1", MagicMock(), interval_attr="k")
        job = manager._jobs["job1"]
        job.last_error = "stale error"
        await manager._run_job(job)
        assert job.last_error is None

    # ------------------------------------------------------------------
    # _run_job() -- exception handling
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_run_job_stores_exception_message(self) -> None:
        """last_error is set to the exception message when the job raises."""
        manager = make_manager()

        def failing_func() -> None:
            raise RuntimeError("boom")

        manager.register("job1", failing_func, interval_attr="k")
        job = manager._jobs["job1"]
        await manager._run_job(job)
        assert job.last_error == "boom"

    @pytest.mark.asyncio
    async def test_run_job_still_updates_state_after_exception(self) -> None:
        """run_count and last_run_at are updated even when the job raises."""
        manager = make_manager()

        def failing_func() -> None:
            raise RuntimeError("boom")

        manager.register("job1", failing_func, interval_attr="k")
        job = manager._jobs["job1"]
        before = time.monotonic()
        await manager._run_job(job)
        assert job.run_count == 1
        assert job.last_run_at >= before
        assert job.is_running is False

    @pytest.mark.asyncio
    async def test_run_job_calls_sync_on_exception_handler(self) -> None:
        """A sync on_exception handler is called with the raised exception."""
        manager = make_manager()
        handler = MagicMock()
        exc = RuntimeError("oops")

        def failing_func() -> None:
            raise exc

        manager.register("job1", failing_func, interval_attr="k", on_exception=handler)
        await manager._run_job(manager._jobs["job1"])
        handler.assert_called_once_with(exc)

    @pytest.mark.asyncio
    async def test_run_job_calls_async_on_exception_handler(self) -> None:
        """An async on_exception handler is awaited with the raised exception."""
        manager = make_manager()
        handler = AsyncMock()
        exc = RuntimeError("oops")

        def failing_func() -> None:
            raise exc

        manager.register("job1", failing_func, interval_attr="k", on_exception=handler)
        await manager._run_job(manager._jobs["job1"])
        handler.assert_called_once_with(exc)

    @pytest.mark.asyncio
    async def test_run_job_no_on_exception_handler_does_not_raise(self) -> None:
        """A failing job without on_exception does not propagate the exception."""
        manager = make_manager()

        def failing_func() -> None:
            raise RuntimeError("silent failure")

        manager.register("job1", failing_func, interval_attr="k")
        await manager._run_job(manager._jobs["job1"])  # must not raise

    @pytest.mark.asyncio
    async def test_run_job_on_exception_not_called_on_success(self) -> None:
        """on_exception is not called when the job succeeds."""
        manager = make_manager()
        handler = MagicMock()
        manager.register("job1", MagicMock(), interval_attr="k", on_exception=handler)
        await manager._run_job(manager._jobs["job1"])
        handler.assert_not_called()
