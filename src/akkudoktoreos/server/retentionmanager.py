"""Retention Manager for Akkudoktor-EOS server.

This module provides a single long-running background task that owns the scheduling of all periodic
server-maintenance jobs (cache cleanup, DB autosave, config reload, …).

Responsibilities:
    - Run a fast "heartbeat" loop (default 5 s) — the *compaction tick*.
    - Maintain a registry of ``ManagedJob`` entries, each with its own interval.
    - Re-read the live configuration on every tick so interval changes take effect
      immediately without a server restart.
    - Track per-job state: last run time, last duration, last error, run count.
    - Expose that state for health-check / metrics endpoints.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Optional, Union

from loguru import logger
from starlette.concurrency import run_in_threadpool

NoArgsNoReturnAnyFuncT = Union[Callable[[], None], Callable[[], Coroutine[Any, Any, None]]]
ExcArgNoReturnAnyFuncT = Union[
    Callable[[Exception], None], Callable[[Exception], Coroutine[Any, Any, None]]
]
ConfigGetterFuncT = Callable[[str], Any]


# ---------------------------------------------------------------------------
# Job state — one per registered maintenance task
# ---------------------------------------------------------------------------


@dataclass
class JobState:
    """Runtime state tracked for a single managed job.

    Attributes:
        name: Unique human-readable job name used in logs and metrics.
        func: The maintenance callable. Must accept no arguments.
        interval_attr: Key passed to ``config_getter`` to retrieve the interval in seconds
            for this job.
        fallback_interval: Interval in seconds used when the key is not found or returns zero.
        config_getter: Callable that accepts a string key and returns the corresponding
            configuration value. Invoked with ``interval_attr`` to obtain the interval
            in seconds.
        on_exception: Optional callable invoked with the raised exception whenever
            ``func`` fails. May be sync or async.
        last_run_at: Monotonic timestamp of the last completed run; ``0.0`` means never run.
        last_duration: How long the last run took, in seconds.
        last_error: String representation of the last exception, or ``None`` if the last run succeeded.
        run_count: Total number of completed runs (successful or not).
        is_running: ``True`` while the job coroutine is currently executing.
    """

    name: str
    func: NoArgsNoReturnAnyFuncT
    interval_attr: str  # key passed to config_getter to obtain the interval in seconds
    fallback_interval: float  # used when the key is not found or returns zero
    config_getter: ConfigGetterFuncT  # callable(key: str) -> Any; returns interval in seconds
    on_exception: Optional[ExcArgNoReturnAnyFuncT] = None  # optional cleanup/alerting hook

    # mutable state
    last_run_at: float = 0.0  # monotonic timestamp; 0.0 means "never run"
    last_duration: float = 0.0  # seconds the job took
    last_error: Optional[str] = None
    run_count: int = 0
    is_running: bool = False

    def interval(self) -> Optional[float]:
        """Retrieve the current interval by calling ``config_getter`` with ``interval_attr``.

        Returns ``None`` when the config value is ``None``, which signals that the
        job is disabled and must never fire. Falls back to ``fallback_interval``
        when the key is not found.

        Returns:
            The interval in seconds, or ``None`` if the job is disabled.
        """
        try:
            value = self.config_getter(self.interval_attr)
            if value is None:
                return None
            return float(value) if value else self.fallback_interval
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RetentionManager: config key '{}' failed with {!r}, using fallback {}s",
                self.interval_attr,
                exc,
                self.fallback_interval,
            )
            return self.fallback_interval

    def is_due(self) -> bool:
        """Check whether enough time has elapsed since the last run to execute this job again.

        Returns ``False`` immediately when `interval` returns ``None``
        (job is disabled), so a disabled job never fires regardless of when it
        last ran.

        Returns:
            ``True`` if the job should be executed on this tick, ``False`` otherwise.
        """
        interval = self.interval()
        if interval is None:
            return False
        return (time.monotonic() - self.last_run_at) >= interval

    def summary(self) -> dict:
        """Build a serialisable snapshot of the job's current state.

        Returns:
            A dictionary suitable for JSON serialisation, containing the job name,
            interval key, last run timestamp, last duration, last error,
            run count, and whether the job is currently running.
        """
        return {
            "name": self.name,
            "interval_attr": self.interval_attr,
            "interval_s": self.interval(),
            "last_run_at": self.last_run_at,
            "last_duration_s": round(self.last_duration, 4),
            "last_error": self.last_error,
            "run_count": self.run_count,
            "is_running": self.is_running,
        }


# ---------------------------------------------------------------------------
# Retention Manager
# ---------------------------------------------------------------------------


class RetentionManager:
    """Orchestrates all periodic server-maintenance jobs.

    A ``config_getter`` callable — accepting a string key
    and returning the corresponding value — is supplied at initialisation and
    stored on every registered job, keeping the manager decoupled from any
    specific config implementation.

    Jobs are launched as independent ``asyncio.Task`` objects so they run
    concurrently without blocking the tick. Call `shutdown` during
    application teardown to wait for any in-flight tasks to complete before
    the event loop closes. A configurable shutdown_timeout prevents the
    wait from blocking indefinitely; jobs still running after the timeout are
    reported by name but not cancelled.
    """

    def __init__(
        self,
        config_getter: ConfigGetterFuncT,
        *,
        shutdown_timeout: float = 30.0,
    ) -> None:
        """Initialise the manager with a configuration accessor.

        Args:
            config_getter: Callable that accepts a string key and returns the
                corresponding configuration value. Used by each registered job
                to look up its interval in seconds.
            shutdown_timeout: Maximum number of seconds to wait for in-flight
                jobs to finish during `shutdown`. If the timeout elapses
                before all tasks complete, an error is logged and the names of
                the still-running jobs are reported. The tasks are not cancelled
                so they may continue running until the event loop closes.
                Defaults to 30.0.

        Example::

            manager = RetentionManager(get_config().get_nested_value, shutdown_timeout=60.0)
        """
        self._config_getter = config_getter
        self._shutdown_timeout = shutdown_timeout
        self._jobs: dict[str, JobState] = {}
        self._running_tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        func: NoArgsNoReturnAnyFuncT,
        *,
        interval_attr: str,
        fallback_interval: float = 300.0,
        on_exception: Optional[ExcArgNoReturnAnyFuncT] = None,
    ) -> None:
        """Register a maintenance function with the manager.

        Args:
            name: Unique human-readable job name used in logs and metrics.
            func: The maintenance callable. Must accept no arguments.
            interval_attr: Key passed to ``config_getter`` to retrieve the interval
                in seconds for this job. When the config value is ``None`` the job
                is treated as disabled and will never fire.
            fallback_interval: Seconds to use when the config attribute is missing or zero.
                Defaults to ``300.0``.
            on_exception: Optional callable invoked with the raised exception whenever
                ``func`` fails. Useful for cleanup or alerting. May be sync or async.

        Raises:
            ValueError: If a job with the given ``name`` is already registered.
        """
        if name in self._jobs:
            raise ValueError(f"RetentionManager: job '{name}' is already registered")

        # Validate the config key immediately so misconfiguration is caught at startup
        try:
            self._config_getter(interval_attr)
        except (KeyError, IndexError):
            logger.warning(
                "RetentionManager: config key '{}' not found at registration of job '{}', will use fallback {}s",
                interval_attr,
                name,
                fallback_interval,
            )
        except Exception as exc:
            raise ValueError(
                f"RetentionManager: interval_attr '{interval_attr}' for job '{name}' "
                f"is not accessible via config_getter: {exc}"
            ) from exc

        self._jobs[name] = JobState(
            name=name,
            func=func,
            interval_attr=interval_attr,
            fallback_interval=fallback_interval,
            config_getter=self._config_getter,
            on_exception=on_exception,
        )
        logger.info("RetentionManager: registered job '{}' (config: {})", name, interval_attr)

    def unregister(self, name: str) -> None:
        """Remove a previously registered job from the manager.

        If no job with the given name exists, this is a no-op.

        Args:
            name: The name of the job to remove.
        """
        self._jobs.pop(name, None)

    # ------------------------------------------------------------------
    # Tick — called by the external heartbeat loop
    # ------------------------------------------------------------------

    async def run(self, *, tick_interval: float = 5.0) -> None:
        """Run the RetentionManager tick loop indefinitely.

        Calls `tick` every ``tick_interval`` seconds until the task is
        cancelled (e.g. during application shutdown). On cancellation,
        `shutdown` is awaited so any in-flight jobs can finish cleanly
        before the loop exits.

        Args:
            tick_interval: Seconds between ticks. Defaults to ``5.0``.

        Example::

            @asynccontextmanager
            async def lifespan(app: FastAPI):
                task = asyncio.create_task(manager.run())
                yield
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        """
        logger.info("RetentionManager: tick loop started (interval={}s)", tick_interval)
        try:
            while True:
                try:
                    await self.tick()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("RetentionManager: unhandled exception in tick: {}", exc)
                await asyncio.sleep(tick_interval)
        except asyncio.CancelledError:
            logger.info("RetentionManager: tick loop cancelled, shutting down...")
            await self.shutdown()
            raise

    async def tick(self) -> None:
        """Single compaction tick: check every job and fire those that are due.

        Each job resolves its own interval via the ``config_getter`` captured at
        registration time. Jobs whose interval is ``None`` are silently skipped
        (disabled). Due jobs are launched as independent ``asyncio.Task`` objects
        so they run concurrently without blocking the tick. Each task is tracked
        in ``_running_tasks`` and removed automatically on completion, allowing
        `shutdown` to await all of them gracefully.

        Jobs that are still running from a previous tick are skipped to prevent
        overlapping executions.
        """
        logger.info("RetentionManager: tick")
        due = [job for job in self._jobs.values() if not job.is_running and job.is_due()]

        if not due:
            return

        logger.debug("RetentionManager: {} job(s) due this tick", len(due))
        for job in due:
            task = asyncio.ensure_future(self._run_job(job))
            task.set_name(job.name)  # used by shutdown() to report timed-out jobs by name
            self._running_tasks.add(task)
            task.add_done_callback(self._running_tasks.discard)

    async def shutdown(self) -> None:
        """Wait for all currently running job tasks to complete.

        Waits up to shutdown_timeout seconds (configured at initialisation)
        for in-flight tasks to finish. If the timeout elapses before all tasks
        complete, an error is logged listing the names of the jobs that are still
        running. Those tasks are **not** cancelled — they continue until the event
        loop closes — but `shutdown` returns so that application teardown
        is not blocked indefinitely.

        Returns immediately if no tasks are running.
        """
        if not self._running_tasks:
            return

        logger.info(
            "RetentionManager: shutdown — waiting up to {}s for {} task(s) to finish",
            self._shutdown_timeout,
            len(self._running_tasks),
        )

        done, pending = await asyncio.wait(self._running_tasks, timeout=self._shutdown_timeout)

        if pending:
            # Task names were set to the job name when the task was created in tick().
            pending_names = [t.get_name() for t in pending]
            logger.error(
                "RetentionManager: shutdown timed out after {}s — {} job(s) still running: {}",
                self._shutdown_timeout,
                len(pending),
                pending_names,
            )
        else:
            logger.info("RetentionManager: all tasks finished, shutdown complete")

        self._running_tasks.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_job(self, job: JobState) -> None:
        """Execute a single job and update its state regardless of outcome.

        Handles both async and sync callables for both the main function and the
        optional ``on_exception`` hook. Exceptions from ``func`` are caught, logged,
        stored on the job, and forwarded to ``on_exception`` if provided, so a
        failing job never disrupts other concurrent jobs or future ticks.

        Args:
            job: The `JobState` instance to execute.
        """
        job.is_running = True
        start = time.monotonic()
        logger.debug("RetentionManager: starting job '{}'", job.name)
        try:
            if asyncio.iscoroutinefunction(job.func):
                await job.func()
            else:
                await run_in_threadpool(job.func)

            job.last_error = None
            logger.debug(
                "RetentionManager: job '{}' completed in {:.3f}s",
                job.name,
                time.monotonic() - start,
            )

        except Exception as exc:  # noqa: BLE001
            job.last_error = str(exc)
            logger.exception("RetentionManager: job '{}' raised an exception: {}", job.name, exc)

            if job.on_exception is not None:
                if asyncio.iscoroutinefunction(job.on_exception):
                    await job.on_exception(exc)
                else:
                    await run_in_threadpool(job.on_exception, exc)

        finally:
            job.last_duration = time.monotonic() - start
            job.last_run_at = time.monotonic()
            job.run_count += 1
            job.is_running = False

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def status(self) -> list[dict]:
        """Return a snapshot of every job's state for health or metrics endpoints.

        Returns:
            A list of dictionaries, one per registered job, each produced by
            `JobState.summary`.
        """
        return [job.summary() for job in self._jobs.values()]

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RetentionManager jobs={list(self._jobs)}>"
