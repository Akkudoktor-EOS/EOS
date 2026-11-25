"""Device control: base classes for controllers.

Layout
------
- ``ControlSharedDataStore``: thread- and async-safe store for cross-device data.
- ``ControlDiagnosticLog``: bounded diagnostic event buffer of a controller. While recording it
  also folds selected event fields into per series ring buffers for graphs, see
  ``controldiagnostics``.
- ``ControllerBaseSettings``: base of all device controller settings.
- ``MonitorThreadBase``: thread running the connect/poll/reconnect loop of one device.
  Sufficient for receive-only devices (e.g. an energy meter).
- ``ControllerThreadBase``: ``MonitorThreadBase`` plus a time-based command schedule
  and a safe state. For devices that are controlled (e.g. an inverter).
- ``ControllerBase``: EOS-server side (event loop) of a device controller.
  Owns the thread, starts and stops it and feeds it the schedule. The Control adapter writes
  its readings into the EOS measurement store.

This module must not import the device control adapter. The adapter imports this module and
hands the shared data store to the controllers it creates (dependency injection).

Transport specific command types (e.g. Modbus register writes) belong into transport specific
modules, they subclass ``ControlCommand``.
"""

import asyncio
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import (
    Any,
    Callable,
    ClassVar,
    Generic,
    Iterable,
    Mapping,
    Optional,
    Sequence,
    TypeVar,
)

from loguru import logger
from pydantic import Field, computed_field

from akkudoktoreos.adapter.controllers.controldiagnostics import (
    DEFAULT_SERIES_CAPACITY,
    DiagnosticGraph,
    DiagnosticSeriesStore,
    DiagnosticView,
    SeriesSpec,
)
from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import ConfigMixin
from akkudoktoreos.core.emplan import EnergyManagementPlan
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime

# Lower bound for the sleep of a control cycle. Protects against busy looping if a command
# boundary is hit repeatedly within the clock resolution.
_MIN_WAIT_S = 0.05


# --------------------------------------------------------------------------
# Shared data store
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Entry:
    """A published value and the time it was published."""

    value: Any
    published: float  # time.monotonic()


class ControlSharedDataStore:
    """Thread- and async-safe shared key-value store for cross-device data.

    Design:
      - All mutations go through a threading.Lock. The critical section is a dict merge plus
        a single reference swap, so it is short and contains no awaits and no blocking I/O.
      - Readers (snapshot, get, get_with_age, get_many) are lock-free: writers only swap the
        image reference, they never mutate the existing dict in place. A single reference read
        is atomic, so a reader always sees a coherent image (before or after a write, never a
        partial one).
      - Async callers use publish()/replace(), thread callers publish_sync()/replace_sync().
      - Every value is stamped with its publish time, so consumers can reject stale data
        (a device that lost its connection leaves its last value behind). All values of one
        publish_sync() batch share one timestamp and become visible together.

    Values are shared by reference and are NOT copied. Only store immutable values, preferably
    plain numbers. Unit conventions are part of the key name (``grid_import_w``).

    Reading several related values: separate get() calls can straddle a publish (``import_w``
    of one telegram, ``export_w`` of the next). Use get_many() to read them from one image.

    Suggested key convention: ``"<device_id>:<name>"``, e.g. ``"meter1:grid_import_w"``.
    """

    def __init__(self) -> None:
        self._image: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    # ---------------- readers (lock-free) ----------------
    def snapshot(self) -> dict[str, Any]:
        """Return a coherent copy of all shared values (without their age)."""
        image = self._image
        return {key: entry.value for key, entry in image.items()}

    def get(self, key: str, default: Any = None) -> Any:
        """Read a single value.

        This is safe without a lock. It does not tell how old the value is,
        use get_with_age() if that matters.
        """
        entry = self._image.get(key)
        return default if entry is None else entry.value

    def get_with_age(self, key: str) -> Optional[tuple[Any, float]]:
        """Read a single value together with its age.

        Returns:
            (value, seconds since it was published), or None if the key does not exist.
        """
        entry = self._image.get(key)
        if entry is None:
            return None
        return entry.value, time.monotonic() - entry.published

    def get_many(self, keys: Iterable[str]) -> dict[str, tuple[Any, float]]:
        """Read several values from one coherent image, each with its age.

        Returns:
            {key: (value, seconds since it was published)} for the keys that exist.
        """
        image = self._image
        now = time.monotonic()
        return {
            key: (entry.value, now - entry.published)
            for key in keys
            if (entry := image.get(key)) is not None
        }

    # ---------------- writers, sync (threads and event loop) ----------------
    def publish_sync(self, updates: dict[str, Any]) -> None:
        """Atomically merge a batch of updates into the shared image.

        The entire batch becomes visible at once and carries one timestamp.
        """
        now = time.monotonic()
        stamped = {key: _Entry(value, now) for key, value in updates.items()}
        with self._lock:
            self._image = {**self._image, **stamped}  # single reference swap

    def replace_sync(self, full_image: dict[str, Any]) -> None:
        """Atomically replace the entire shared image."""
        now = time.monotonic()
        stamped = {key: _Entry(value, now) for key, value in full_image.items()}
        with self._lock:
            self._image = stamped

    def remove_sync(self, keys: Iterable[str]) -> None:
        """Atomically remove keys from the shared image. Unknown keys are ignored."""
        drop = set(keys)
        with self._lock:
            self._image = {k: e for k, e in self._image.items() if k not in drop}

    # ---------------- writers, async (event loop) ----------------
    async def publish(self, updates: dict[str, Any]) -> None:
        """Async variant of publish_sync().

        Never yields inside the critical section.
        """
        self.publish_sync(updates)

    async def replace(self, full_image: dict[str, Any]) -> None:
        """Async variant of replace_sync()."""
        self.replace_sync(full_image)


# --------------------------------------------------------------------------
# Per controller diagnostics log buffer
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ControlDiagnosticEntry:
    """One structured diagnostic event produced by a controller thread.

    ``seq`` is assigned by the log when the entry is recorded: strictly increasing, starting
    with 1. 0 means not recorded yet.
    """

    date_time: DateTime
    event: str
    data: Mapping[str, Any]
    seq: int = 0


class ControlDiagnosticLog:
    """Bounded, thread-safe diagnostic event buffer that outlives controller restarts.

    Owned by the controller, written by the control thread and by the controller on the event loop,
    read from the event loop.

    The buffer holds the detailed events of the last minutes. While an event is recorded, the
    fields the controller class declared in ``ControllerBase.DIAGNOSTIC_SERIES`` are also folded
    into per series ring buffers, which hold hours and feed ``series_view()``. Series are
    collected while the log is enabled (capacity > 0). Their capacity is independent of the
    capacity of the event buffer, a small event buffer is sufficient for graphs.
    """

    def __init__(self, capacity: int = 0, series_capacity: int = DEFAULT_SERIES_CAPACITY) -> None:
        if capacity < 0:
            raise ValueError("capacity must be >= 0")
        self._lock = threading.Lock()
        self._capacity = capacity
        self._entries: deque[ControlDiagnosticEntry] = deque(maxlen=capacity)
        self._seq = 0  # sequence number of the newest recorded entry, written under _lock
        self._series = DiagnosticSeriesStore(series_capacity)
        self._graphs: Optional[tuple[DiagnosticGraph, ...]] = None  # None: by unit

    @property
    def capacity(self) -> int:
        """Maximum number of events kept in the buffer, 0 when the log is disabled."""
        return self._capacity

    @property
    def enabled(self) -> bool:
        """Whether events and series are recorded (capacity > 0)."""
        return self._capacity > 0

    @property
    def last_seq(self) -> int:
        """Sequence number of the newest recorded entry, 0 if nothing was recorded."""
        return self._seq

    def record(self, entry: ControlDiagnosticEntry) -> None:
        """Record an event and fold its series fields, unless the log is disabled.

        Args:
            entry: The event. Its ``seq`` is replaced by the next sequence number.
        """
        if not self._capacity:  # lock-free fast path, capacity changes only via resize()
            return
        with self._lock:
            self._seq += 1
            entry = replace(entry, seq=self._seq)
            self._entries.append(entry)
            # Inside the lock, so the points of a series are in sequence order.
            self._series.fold(entry.seq, entry.date_time.timestamp(), entry.event, entry.data)

    def snapshot(self) -> tuple[ControlDiagnosticEntry, ...]:
        """Return the buffered events, oldest first, without removing them.

        Returns:
            The buffered events.
        """
        with self._lock:
            return tuple(self._entries)

    def drain(self) -> tuple[ControlDiagnosticEntry, ...]:
        """Return and clear the events. The series are not affected."""
        with self._lock:
            entries = tuple(self._entries)
            self._entries.clear()
            return entries

    def resize(self, capacity: int) -> None:
        """Change the capacity live. Shrinking keeps the newest entries."""
        if capacity < 0:
            raise ValueError("capacity must be >= 0")
        with self._lock:
            self._entries = deque(self._entries, maxlen=capacity)  # drops oldest on shrink
            self._capacity = capacity

    def configure_series(
        self, spec: SeriesSpec, graphs: Optional[Sequence[DiagnosticGraph]] = None
    ) -> None:
        """Set which event fields are folded into series and how they are graphed.

        Kept in the log, so that every reader of the log (also the REST API, without the
        controller) gets the graphs the controller declared.

        Args:
            spec: Event fields to fold, see ``ControllerBase.DIAGNOSTIC_SERIES``.
            graphs: Explicit graph definitions, see ``ControllerBase.DIAGNOSTIC_GRAPHS``.
                ``None`` builds graphs automatically by unit.
        """
        self._series.configure(spec)
        self._graphs = None if graphs is None else tuple(graphs)

    def series_view(
        self, since_seq: int = 0, max_points: int = 300, tz: Optional[str] = None
    ) -> DiagnosticView:
        """Series and graphs of the controller, independent of the device.

        The graphs are the ones given to ``configure_series()``.

        Args:
            since_seq: Only points of entries newer than this. Pass the ``last_seq`` of the
                previous view to get new points only. 0: everything held.
            max_points: Upper bound of points per series (numeric: min/max preserving,
                vector: the last reading per bucket).
            tz: Timezone name of the series index. None: UTC.
        """
        return self._series.view(self._seq, since_seq, max_points, tz, self._graphs)


# --------------------------------------------------------------------------
# Data exchanged between the EOS server side and the control thread
# --------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ControlCommand:
    """Transport independent command, valid from `date_time` on.

    A command stays active until the next command in the schedule starts or `valid_until`
    passes. After that the device is put into its safe state.

    Subclass to add the transport specific payload, e.g. Modbus register writes.
    """

    date_time: DateTime
    valid_until: Optional[DateTime] = None
    # Re-apply the command after this many seconds while it is active (device keep-alive).
    # None: use the refresh interval of the thread. If both are None: apply once.
    refresh_interval: Optional[float] = None
    label: str = ""  # e.g. "charge", "discharge", for logging/status


@dataclass(frozen=True)
class ControlStatus:
    """Immutable snapshot published by the thread."""

    date_time: DateTime
    connected: bool
    active_command: Optional[ControlCommand] = None
    data: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))  # readings
    error: Optional[str] = None


CommandT = TypeVar("CommandT", bound=ControlCommand)


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


class ControllerBaseSettings(SettingsBaseModel):
    """Base device controller configuration.

    Every concrete settings class declares its type as discriminator, without a default, e.g.

        controller_type: Literal["sma:energy-meter"]

    The adapter settings use a discriminated union over ``controller_type``. The type has no
    default, so it is always given and always saved: the configuration file is written without
    default values, and an entry without its type cannot be loaded again.

    ``shared_data_keys`` is a read-only computed field and a direct function of
    the controller class: the controller binds itself to its settings class via
    ``bind_controller_class`` when the controller subclass is defined, and the
    computed field reads ``type(self).__controller_class__.PROVIDED_DATA_KEYS``.
    The source of truth is ``ControllerBase.PROVIDED_DATA_KEYS``.

    Attributes:
        __controller_class__: The controller class bound to this settings class.
            ``ClassVar`` so pydantic does not treat it as a model field. ``None``
            on the unbound base or a settings class not yet linked.
    """

    __controller_class__: ClassVar[type | None] = None

    measurement_max_age_s: float = Field(
        default=60.0,
        gt=0,
        json_schema_extra={
            "description": (
                "Maximum age in seconds of a reading of this controller to be written to the "
                "measurement store (see `adapter.control.*_data_keys`). Older values are "
                "skipped."
            ),
            "examples": [60.0],
        },
    )
    diagnostic_log_capacity: int = Field(
        default=0,
        ge=0,
        json_schema_extra={
            "description": (
                "Maximum number of controller diagnostic events retained in memory. "
                "Zero disables diagnostic collection."
            ),
            "examples": [0, 500],
        },
    )

    @classmethod
    def bind_controller_class(cls, controller_cls: type) -> None:
        """Link this settings class to its controller class.

        Idempotent: re-binding to the same controller is a no-op. Rebinding to a
        different controller raises, so one settings class cannot be silently
        shared between two controllers.

        Args:
            controller_cls: The concrete ``ControllerBase`` subclass that
                is configured with this settings class.

        Raises:
            RuntimeError: If the class is already bound to a different controller.
        """
        existing = cls.__dict__.get("__controller_class__")  # not the inherited binding
        if existing is not None and existing is not controller_cls:
            raise RuntimeError(
                f"{cls.__name__} already bound to {existing.__name__}, "
                f"refusing to rebind to {controller_cls.__name__}"
            )
        cls.__controller_class__ = controller_cls

    @classmethod
    def controller_class(cls) -> "type[ControllerBase]":
        """Return the controller class bound to this settings class.

        Raises:
            RuntimeError: If no controller class is bound to this settings class.
        """
        controller_cls = cls.__dict__.get("__controller_class__")
        if controller_cls is None:
            raise RuntimeError(f"No controller class bound to {cls.__name__}")
        return controller_cls

    @computed_field(  # type: ignore[prop-decorator]
        description=(
            "Read-only: key suffixes (without the '<device_id>:' prefix) this "
            "controller provides in the device control shared data store."
        ),
    )
    @property
    def shared_data_keys(self) -> tuple[str, ...]:
        """Return the key suffixes this device provides in the shared data store.

        Returns:
            tuple[str, ...]: Suffixes (no ``<device_id>:`` prefix) declared by the
            bound controller class via ``PROVIDED_DATA_KEYS``. Empty if the
            settings class is not bound (e.g. the base class itself).
        """
        controller_cls = type(self).__dict__.get("__controller_class__")
        if controller_cls is None:
            return ()
        return tuple(controller_cls.PROVIDED_DATA_KEYS)


# --------------------------------------------------------------------------
# Control thread side
# --------------------------------------------------------------------------


class MonitorThreadBase(threading.Thread, ABC):
    """Thread running the connect/poll/reconnect loop of one device.

    Thread-safe API (callable from the asyncio event loop):
        get_status(), bind_loop(), stop()
    Abstract hooks (called only from this thread):
        _connect(), _disconnect(), _read_status()

    Hook contract:
        _connect()      Open the connection. Raise on failure.
        _disconnect()   Close the connection. Must be idempotent and must not raise; it is also
                        called after a failed _connect().
        _read_status()  Read and decode the device state. Raise on failure. A raise is treated
                        as a lost connection (disconnect, back off, reconnect).

    Subclasses must not reuse the attribute names ``_lock``, ``_wakeup``, ``_stop_event``,
    ``_status`` and ``_connected``.
    """

    def __init__(
        self,
        device_id: str,
        shared_data: ControlSharedDataStore,
        diagnostic_log: ControlDiagnosticLog,
        *,
        poll_interval: float = 5.0,
        reconnect_backoff: Sequence[float] = (1, 2, 5, 10, 30),
        clock: Callable[[], DateTime] = to_datetime,
    ) -> None:
        if poll_interval < 0:
            raise ValueError("poll_interval must be >= 0")
        if not reconnect_backoff:
            raise ValueError("reconnect_backoff must not be empty")

        # daemon: never block interpreter exit. The orderly path is ControllerBase.stop().
        super().__init__(name=f"control-{device_id}", daemon=True)
        self.device_id = device_id
        self.shared_data = shared_data
        self.diagnostic_log = diagnostic_log  # Bounded diagnostic log.

        self.poll_interval = poll_interval
        self.reconnect_backoff = tuple(reconnect_backoff)
        self._clock = clock

        self._lock = threading.Lock()
        self._wakeup = threading.Event()
        self._stop_event = threading.Event()

        self._status = ControlStatus(date_time=clock(), connected=False)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._status_callback: Optional[Callable[[ControlStatus], None]] = None

        self._connected = False  # thread-private

    # ---------------- thread-safe API (event loop side) ----------------
    def get_status(self) -> ControlStatus:
        """Return the latest status snapshot."""
        with self._lock:
            return self._status

    def bind_loop(
        self,
        loop: asyncio.AbstractEventLoop,
        callback: Callable[[ControlStatus], None],
    ) -> None:
        """Have `callback(status)` invoked on `loop` whenever the status changes.

        Must be called before start().
        """
        self._loop = loop
        self._status_callback = callback

    def stop(self, timeout: Optional[float] = 10.0) -> None:
        """Stop the thread and wait for it. Blocking: call off-loop (asyncio.to_thread)."""
        self._stop_event.set()
        self._wakeup.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout)
            if self.is_alive():
                logger.error(f"{self.device_id}: control thread did not stop within {timeout} s")

    def published_keys(self) -> tuple[str, ...]:
        """Return the fully qualified shared data keys this thread publishes.

        Defaults to the empty tuple: a thread that only produces ``ControlStatus``
        and never touches the shared store publishes nothing. Threads that do
        publish override this so the controller can verify, at start time, that
        the thread and the controller class agree on the published key set.

        Returns:
            tuple[str, ...]: Fully qualified keys (``"<device_id>:<suffix>"``).
        """
        return ()

    # ---------------- abstract hooks (thread side) ----------------
    @abstractmethod
    def _connect(self) -> None:
        """Open the connection. Raise on failure."""

    @abstractmethod
    def _disconnect(self) -> None:
        """Close the connection. Idempotent, must not raise."""

    @abstractmethod
    def _read_status(self) -> dict[str, Any]:
        """Read and decode the device state (SoC, power, mode, ...)."""

    # ---------------- optional hooks (thread side) ----------------
    def _on_connected(self) -> None:
        """Called after every successful _connect()."""

    def _on_shutdown(self) -> None:
        """Called once on stop while connected, before _disconnect()."""

    def _active_command(self) -> Optional[ControlCommand]:
        """Command currently applied to the device, for the status."""
        return None

    # ---------------- internals ----------------
    def _now(self) -> DateTime:
        return self._clock()

    def _cycle(self) -> float:
        """Run one cycle while connected. Return the seconds to wait for the next one."""
        self._publish(data=self._read_status())
        return self.poll_interval

    def _publish(
        self, error: Optional[str] = None, data: Optional[Mapping[str, Any]] = None
    ) -> None:
        """Publish a new status; notify the event loop only if something changed.

        `data=None` keeps the previous readings (e.g. when publishing an error).
        """
        with self._lock:
            previous = self._status
            status = ControlStatus(
                date_time=self._now(),
                connected=self._connected,
                active_command=self._active_command(),
                data=MappingProxyType(dict(data)) if data is not None else previous.data,
                error=error,
            )
            self._status = status
        changed = (
            previous.connected != status.connected
            or previous.error != status.error
            or previous.active_command != status.active_command
            or previous.data != status.data
        )
        if changed:
            self._notify(status)

    def _notify(self, status: ControlStatus) -> None:
        loop, callback = self._loop, self._status_callback
        if loop is None or callback is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(callback, status)
        except RuntimeError:
            pass  # loop closed between the check and the call (server shutdown)

    def _publish_quietly(self, error: Optional[str] = None) -> None:
        """Publish from error and shutdown paths, where a failure must not kill the thread."""
        try:
            self._publish(error=error)
        except Exception:  # noqa: BLE001
            logger.exception(f"{self.device_id}: status publish failed")

    def _disconnect_quietly(self) -> None:
        try:
            self._disconnect()
        except Exception:  # noqa: BLE001
            logger.exception(f"{self.device_id}: disconnect failed")
        self._connected = False

    def _record_diagnostic(self, event: str, **data: Any) -> None:
        """Record a diagnostic event if diagnostic logging is enabled.

        Subclasses call this from the controller thread at relevant decision
        points. Events are discarded automatically when the rolling buffer
        reaches its configured capacity.

        Args:
            event: Short event identifier, e.g. ``"pid_calculation"``.
            **data: Structured event data. Prefer scalar values and immutable
                objects so that retrieved entries remain stable.
        """
        log = self.diagnostic_log
        if not log.enabled:
            return
        # A subclass passing an unhashable or odd data value cannot break dict(data), but
        # self._now() calls the injected clock. If a test clock raises, the thread dies in the
        # middle of the control loop. Since this is a diagnostic path, wrapping it in the same
        # try/except … logger.exception pattern as _publish_quietly.
        try:
            log.record(ControlDiagnosticEntry(self._now(), event, MappingProxyType(dict(data))))
        except Exception:  # noqa: BLE001
            logger.exception(f"{self.device_id}: diagnostic record failed")

    def run(self) -> None:
        """Run the thread: connect, run cycles until stopped, reconnect with backoff on errors.

        On stop the shutdown hook runs (if connected), the connection is closed and the final
        status is published.
        """
        backoff_idx = 0
        try:
            while not self._stop_event.is_set():
                # Clear first: a wake-up (e.g. set_schedule) arriving during the cycle must
                # not be lost.
                self._wakeup.clear()
                try:
                    if not self._connected:
                        self._connect()
                        self._connected = True
                        self._on_connected()
                    wait = self._cycle()
                    backoff_idx = 0
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"{self.device_id}: control error: {exc}")
                    self._disconnect_quietly()  # also cleans up after a half-open _connect()
                    self._publish_quietly(error=str(exc))
                    wait = self.reconnect_backoff[min(backoff_idx, len(self.reconnect_backoff) - 1)]
                    backoff_idx += 1
                self._wakeup.wait(wait)
        finally:
            if self._connected:
                try:
                    self._on_shutdown()
                except Exception:  # noqa: BLE001
                    logger.exception(f"{self.device_id}: shutdown hook failed")
                self._disconnect_quietly()
            self._publish_quietly()


class ControllerThreadBase(MonitorThreadBase, Generic[CommandT]):
    """Control thread: applies a time-based schedule of commands to one device.

    Additional thread-safe API (callable from the asyncio event loop):
        set_schedule()
    Additional abstract hooks (called only from this thread):
        _apply_command(), _enter_safe_state()

    Behavior:
      - The command with the latest `date_time` <= now is active, unless its `valid_until`
        has passed.
      - No active command: the device is put into its safe state (once).
      - A new active command, or an active command whose refresh interval elapsed, is applied.
      - After a reconnect the device state is unknown: the active command (or the safe state)
        is applied again.
    """

    def __init__(
        self,
        device_id: str,
        shared_data: ControlSharedDataStore,
        diagnostic_log: ControlDiagnosticLog,
        *,
        refresh_interval: Optional[float] = None,  # seconds; None: apply a command only once
        **kwargs: Any,
    ) -> None:
        if refresh_interval is not None and refresh_interval <= 0:
            raise ValueError("refresh_interval must be > 0")
        super().__init__(device_id, shared_data, diagnostic_log, **kwargs)
        self.refresh_interval = refresh_interval
        self._schedule: tuple[CommandT, ...] = ()  # guarded by _lock

        # thread-private state
        self._applied: Optional[CommandT] = None
        self._applied_at: Optional[DateTime] = None
        self._in_safe_state = False

    # ---------------- thread-safe API (event loop side) ----------------
    def set_schedule(self, commands: Sequence[CommandT]) -> None:
        """Replace the whole schedule. Non-blocking; safe to call from the event loop."""
        ordered = tuple(sorted(commands, key=lambda c: c.date_time))
        with self._lock:
            self._schedule = ordered
        self._wakeup.set()

    # ---------------- abstract hooks (thread side) ----------------
    @abstractmethod
    def _apply_command(self, command: CommandT) -> None:
        """Write the command to the device. Raise on failure."""

    @abstractmethod
    def _enter_safe_state(self) -> None:
        """Put the device in a safe/autonomous mode. Raise on failure."""

    # ---------------- internals ----------------
    def _active_command(self) -> Optional[CommandT]:
        return self._applied

    def _on_connected(self) -> None:
        # Device state unknown after (re)connect: force re-apply of command or safe state.
        self._applied, self._applied_at = None, None
        self._in_safe_state = False

    def _on_shutdown(self) -> None:
        self._enter_safe_state()

    def _refresh_interval_of(self, command: CommandT) -> Optional[float]:
        if command.refresh_interval is not None:
            return command.refresh_interval
        return self.refresh_interval

    def _select(self, now: DateTime) -> tuple[Optional[CommandT], Optional[DateTime]]:
        """Return (currently valid command or None, date_time of the next command)."""
        with self._lock:
            schedule = self._schedule
        current: Optional[CommandT] = None
        upcoming: Optional[DateTime] = None
        for command in schedule:
            if command.date_time <= now:
                current = command
            else:
                upcoming = command.date_time
                break
        if current is not None and current.valid_until is not None and current.valid_until <= now:
            current = None
        return current, upcoming

    def _wait_time(self, now: DateTime, upcoming: Optional[DateTime]) -> float:
        """Seconds until the next thing to do: poll, next command, expiry or refresh."""
        wait = self.poll_interval
        if upcoming is not None:
            wait = min(wait, (upcoming - now).total_seconds())
        applied = self._applied
        if applied is not None:
            if applied.valid_until is not None:
                wait = min(wait, (applied.valid_until - now).total_seconds())
            interval = self._refresh_interval_of(applied)
            if interval is not None and self._applied_at is not None:
                wait = min(wait, interval - (now - self._applied_at).total_seconds())
        return max(wait, _MIN_WAIT_S)

    def _cycle(self) -> float:
        now = self._now()
        command, upcoming = self._select(now)

        if command is None:
            if not self._in_safe_state:
                self._enter_safe_state()
                self._in_safe_state = True
                self._applied, self._applied_at = None, None
        else:
            interval = self._refresh_interval_of(command)
            refresh_due = (
                interval is not None
                and self._applied_at is not None
                and (now - self._applied_at).total_seconds() >= interval
            )
            if command != self._applied or refresh_due:
                self._apply_command(command)
                self._applied, self._applied_at = command, now
                self._in_safe_state = False
                logger.info(
                    f"{self.device_id}: applied command '{command.label}' (t={command.date_time})"
                )

        self._publish(data=self._read_status())
        return self._wait_time(self._now(), upcoming)


# --------------------------------------------------------------------------
# EOS server side
# --------------------------------------------------------------------------


class ControllerBase(ABC, ConfigMixin):
    """EOS-server side of a device controller.

    Owns the thread, starts and stops it and feeds it schedules. Its readings reach the EOS
    measurement store through the Control adapter (``adapter.control.*_data_keys``). One
    instance per configured device. All public methods are called from the event loop.
    """

    #: Suffixes (without the ``<device_id>:`` prefix) of the keys this device
    #: publishes into the shared data store. The source of truth for
    #: ``<SettingsClass>.shared_data_keys``. Pure consumers leave this empty.
    #: Override in concrete controllers, e.g. ``PROVIDED_DATA_KEYS = ("grid_import_w",)``.
    PROVIDED_DATA_KEYS: ClassVar[tuple[str, ...]] = ()

    #: Diagnostic event fields that are folded into series for graphs, see
    #: ``diagnostic_view()``. Maps an event name of the control thread to the field names to
    #: track, or to None to track all numeric, str and bool fields of that event. Fields with a
    #: tuple of numbers (e.g. all cell voltages) are tracked as vector series, only when named.
    #: The unit of a numeric or vector series is taken from the field name suffix (``_w``,
    #: ``_percent``, ``_c``, ``_ms``, ``_mv``, ...). Override in concrete controllers, e.g.
    #: ``DIAGNOSTIC_SERIES = {"readings_updated": None, "setpoint_calculated": ("setpoint_w",)}``.
    DIAGNOSTIC_SERIES: ClassVar[SeriesSpec] = {}

    #: Optional explicit graph layout for ``diagnostic_view()``. When ``None``, graphs are
    #: generated automatically by unit (profiles for vector series, never heatmaps). An empty
    #: tuple suppresses graph suggestions.
    #: The graph series keys must match keys declared in ``DIAGNOSTIC_SERIES``.
    DIAGNOSTIC_GRAPHS: ClassVar[Optional[tuple[DiagnosticGraph, ...]]] = None

    #: Concrete settings class this controller is configured with. Declared by
    #: each concrete subclass as a ClassVar, e.g.
    #:     Settings: ClassVar[type[SmaEnergyMeterSettings]] = SmaEnergyMeterSettings
    #: The base binds it to the settings class so that
    #: ``<SettingsClass>.shared_data_keys`` is a direct function of
    #: ``PROVIDED_DATA_KEYS``. Distinct from ``self.settings``, which is the
    #: per-instance settings *snapshot*.
    Settings: ClassVar[type[ControllerBaseSettings]]

    #: Configuration settings that are changed live - no restart necessary
    #: To be extended by sub classes according to their settings.
    _LIVE_FIELDS: ClassVar[set[str]] = {"diagnostic_log_capacity"}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Bind the concrete ``Settings`` class declared by this subclass.

        Runs when a ``ControllerBase`` subclass is defined. Each concrete
        controller declares its settings class as a ``ClassVar``::

            class SmaEnergyMeter(ControllerBase):
                Settings: ClassVar[type[SmaEnergyMeterSettings]] = SmaEnergyMeterSettings

        This hook reads that ``ClassVar`` and calls
        ``Settings.bind_controller_class(cls)`` so the settings'
        ``shared_data_keys`` computed field becomes a direct function of the
        controller's ``PROVIDED_DATA_KEYS``.

        Subclasses that do not redeclare ``Settings`` (abstract intermediates) are
        skipped. Redeclaring it as the base ``ControllerBaseSettings`` is
        ignored. Redeclaring it with a non-settings subclass raises, so a typo in
        the settings type fails at import time rather than at first use.

        Args:
            **kwargs: Forwarded to ``super().__init_subclass__``.

        Raises:
            RuntimeError: If ``Settings`` is not a ``ControllerBaseSettings``
                subclass.
        """
        super().__init_subclass__(**kwargs)

        annotated = cls.__dict__.get("Settings")
        if annotated is None:
            return
        if not (isinstance(annotated, type) and issubclass(annotated, ControllerBaseSettings)):
            raise RuntimeError(
                f"{cls.__name__}.Settings must be a ControllerBaseSettings "
                f"subclass, got {annotated!r}"
            )
        if annotated is ControllerBaseSettings:
            return
        annotated.bind_controller_class(cls)

    def __init__(
        self,
        device_id: str,
        shared_data: ControlSharedDataStore,
        diagnostic_log: ControlDiagnosticLog,
        settings: ControllerBaseSettings,
    ) -> None:
        settings_cls = getattr(type(self), "Settings", None)
        if settings_cls is None:
            raise TypeError(f"{type(self).__name__} must declare a Settings class variable")
        if not isinstance(settings, settings_cls):
            raise TypeError(
                f"{device_id}: {type(self).__name__} expects {settings_cls.__name__}, "
                f"got {type(settings).__name__}"
            )
        self.device_id = device_id
        # A snapshot: the configuration object may be changed in place, the controller keeps
        # the settings it was started with. The adapter restarts it if they differ, see
        # settings_changed().
        self._settings = settings.model_copy(deep=True)
        self.shared_data = shared_data
        self._thread: Optional[MonitorThreadBase] = None
        self._last_status: Optional[ControlStatus] = None

        # The diagnostic log. Kept in adapter to survive controller and thread restart and stop.
        self.diagnostic_log = diagnostic_log
        # Tell the log which event fields to fold into series and how to graph them (replaces
        # the spec of a previous controller instance, the series collected so far are kept).
        self.diagnostic_log.configure_series(
            type(self).DIAGNOSTIC_SERIES, type(self).DIAGNOSTIC_GRAPHS
        )

    def _thread_kwargs(self) -> dict[str, Any]:
        """Common keyword arguments every control thread must receive."""
        return {"diagnostic_log": self.diagnostic_log}

    @property
    def settings(self) -> ControllerBaseSettings:
        """Read-only snapshot of the settings this controller was started with.

        Immutable for the controller's lifetime: the adapter stops and
        restarts the controller when the configured settings change, rather
        than mutating this object.
        """
        return self._settings

    def settings_changed(self, settings: ControllerBaseSettings) -> bool:
        """Whether `settings` differs from the settings this controller was started with.

        Compares the values of the fields, not the model instances: two instances with the same
        values are not necessarily equal as objects (private attributes of the settings base
        class, identity of nested objects).

        Excludes configuration settings that are changed live.
        """
        return settings.model_dump(exclude=self._LIVE_FIELDS) != self._settings.model_dump(
            exclude=self._LIVE_FIELDS
        )

    def apply_live_settings(self, settings: ControllerBaseSettings) -> None:
        """Take over the fields in ``_LIVE_FIELDS`` from ``settings``, without a restart.

        Called by the adapter for a running controller whose other settings did not change
        (see ``settings_changed()``). Calls ``_on_live_settings_changed()`` if a live field
        changed.

        Args:
            settings: The configured settings of this controller.
        """
        live = {name: getattr(settings, name) for name in self._LIVE_FIELDS}
        if all(getattr(self._settings, name) == value for name, value in live.items()):
            return
        self._settings = self._settings.model_copy(update=live, deep=True)
        self._on_live_settings_changed()

    def _on_live_settings_changed(self) -> None:
        """Hand changed live settings (``self.settings``) to the running thread.

        Override in controllers that extend ``_LIVE_FIELDS`` with fields the thread uses. The
        diagnostic log capacity is applied by the adapter itself.
        """

    # ---------------- device specific ----------------
    def shared_data_keys(self) -> tuple[str, ...]:
        """Return the fully qualified keys this device provides.

        Derived from ``type(self).PROVIDED_DATA_KEYS`` by prefixing each suffix
        with ``f"{self.device_id}:"``. Devices that publish nothing return an
        empty tuple.

        Returns:
            tuple[str, ...]: ``f"{device_id}:{suffix}"`` for each suffix in
            ``type(self).PROVIDED_DATA_KEYS``. Empty for pure consumers.
        """
        prefix = f"{self.device_id}:"
        return tuple(prefix + suffix for suffix in type(self).PROVIDED_DATA_KEYS)

    @abstractmethod
    def _create_control_thread(self) -> MonitorThreadBase:
        """Build a fresh, not yet started thread (host/port/register map from self.settings).

        Called on every start(), because a threading.Thread cannot be restarted. A thread that
        produces data for other devices gets `self.shared_data` and publishes into it itself
        (publish_sync()).
        """

    def _emplan_to_commands(self, plan: Optional[EnergyManagementPlan]) -> list[ControlCommand]:
        """Map the energy management plan to this device's control commands.

        Must be overridden by controllers whose thread is a ControllerThreadBase.
        An empty list (e.g. for plan None) makes the device fall back to its safe state.
        Not called for receive-only devices.
        """
        raise NotImplementedError(f"{type(self).__name__} must implement _emplan_to_commands()")

    # ---------------- lifecycle ----------------
    async def start(self) -> None:
        """Create and start the control thread.

        Verifies that the thread publishes exactly the keys this controller class
        declares via ``PROVIDED_DATA_KEYS`` (using ``shared_data_keys()`` for the
        fully qualified form). A mismatch is a programming error in the
        controller and raises ``RuntimeError`` before the thread is started, so
        the adapter never registers a controller whose thread writes keys it does
        not announce.

        Raises:
            RuntimeError: If the controller was already started, or if the thread
                publishes a key set that differs from ``shared_data_keys()``.
        """
        if self._thread is not None:
            raise RuntimeError(f"{self.device_id}: already started")

        thread = self._create_control_thread()

        declared_keys = sorted(self.shared_data_keys())
        published_keys = sorted(thread.published_keys())
        if published_keys != declared_keys:
            raise RuntimeError(
                f"{self.device_id}: thread {type(thread).__name__} publishes {published_keys}, "
                f"controller {type(self).__name__} declares {declared_keys}"
            )

        thread.bind_loop(asyncio.get_running_loop(), self._on_status)
        thread.start()
        self._thread = thread

    async def stop(self) -> None:
        """Stop the control thread.

        For a controlled device the thread writes the safe state to the device
        before disconnecting (``_on_shutdown`` -> ``_enter_safe_state``). The
        adapter uses this to replace a controller whose settings changed: the old
        controller stops (entering the safe state), a new controller is
        constructed from the new settings and started. Receive-only devices just
        close their connection.
        """
        thread, self._thread = self._thread, None
        if thread is not None:
            # stop() joins the thread; run it off-loop so the event loop is not blocked for up
            # to the join timeout.
            await asyncio.to_thread(thread.stop)

    # ---------------- server -> thread ----------------
    def update(self, plan: Optional[EnergyManagementPlan]) -> None:
        """Replace the schedule of the control thread. No-op for receive-only devices."""
        if self._thread is None:
            raise RuntimeError(f"{self.device_id}: not started")
        if isinstance(self._thread, ControllerThreadBase):
            self._thread.set_schedule(self._emplan_to_commands(plan))

    # ---------------- thread -> server ----------------
    @property
    def status(self) -> ControlStatus:
        """Latest status snapshot of the control thread."""
        if self._thread is None:
            raise RuntimeError(f"{self.device_id}: not started")
        return self._thread.get_status()

    def _on_status(self, status: ControlStatus) -> None:
        """Runs on the event loop whenever the status changes.

        Override to feed readings (SoC etc.) back into EOS; call super() to keep the logging.
        """
        last, self._last_status = self._last_status, status
        if last is None or (last.connected, last.error) != (status.connected, status.error):
            logger.info(f"{self.device_id}: connected={status.connected} error={status.error}")

    def get_diagnostic_log(self) -> tuple[ControlDiagnosticEntry, ...]:
        """Return a snapshot of the control thread's diagnostic log.

        The log is not modified. Entries remain available until the log
        reaches its configured capacity or is explicitly drained.

        Returns:
            tuple[ControlDiagnosticEntry, ...]: Diagnostic entries in
                chronological order. Empty if diagnostic logging is disabled.

        Raises:
            RuntimeError: If the controller has not been started.
        """
        return self.diagnostic_log.snapshot()

    def drain_diagnostic_log(self) -> tuple[ControlDiagnosticEntry, ...]:
        """Return and clear the control thread's diagnostic log.

        This is useful for periodic collection from EOS: each entry is
        returned only once, unless it is recorded again by the thread.

        Returns:
            tuple[ControlDiagnosticEntry, ...]: Drained diagnostic entries
                in chronological order. Empty if diagnostic logging is disabled.

        Raises:
            RuntimeError: If the controller has not been started.
        """
        return self.diagnostic_log.drain()

    def diagnostic_view(
        self, since_seq: int = 0, max_points: int = 300, tz: Optional[str] = None
    ) -> DiagnosticView:
        """Series and suggested graphs of the control operation, independent of the device.

        Built from the fields declared in ``DIAGNOSTIC_SERIES``. The series are collected while
        the diagnostic log is enabled (``diagnostic_log_capacity`` > 0) and survive ``drain``
        and controller restarts. Poll with the ``last_seq`` of the previous view as `since_seq`
        to get new points only.

        Args:
            since_seq: Only points newer than this sequence number. 0: everything held.
            max_points: Upper bound of points per series (numeric: min/max preserving,
                vector: the last reading per bucket).
            tz: Timezone name of the series index. None: UTC.

        Returns:
            DiagnosticView: Series (pandas based) and suggested graphs. Empty while nothing was
                collected.
        """
        view = self.diagnostic_log.series_view(since_seq, max_points, tz)
        return view.model_copy(update={"device_id": self.device_id})
