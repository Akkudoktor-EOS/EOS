"""Modbus TCP support for device controller threads.

``ModbusTcpMonitorThreadBase`` carries the transport for a device that is only read (e.g. a
battery management unit), ``ModbusTcpControllerThreadBase`` adds the command schedule of
``ControllerThreadBase`` for a device that is controlled (e.g. an inverter).

Framing
-------
The default is Modbus TCP (``FramerType.SOCKET``, MBAP header). Some devices carry Modbus RTU
frames (with CRC) over a TCP connection instead; they pass ``framer=FramerType.RTU``.

Command types stays device specific: a device that needs a declarative command translates it to
register writes itself (see the SMA STP-SE), so there is no generic "register write" command here.

Timing
------
Every transaction is timed. The statistics are always collected, independent of the diagnostic
log, and are available from any thread with ``modbus_stats()``. They are cumulative per
operation ("read": input and holding register reads, "write"). To aggregate over a window, take
two snapshots and subtract (``total_ms``, ``count``), or call ``reset_modbus_stats()``.

Diagnostics
-----------
Every register read and write is also recorded in the diagnostic log (if enabled), successful
or not:

- ``modbus_read``: ``kind`` ("input" or "holding"), ``address``, ``count``, ``words`` (tuple, None
  on error), ``error`` (None on success), ``ms`` (duration).
- ``modbus_write``: ``address``, ``values`` (tuple), ``error`` (None on success), ``ms``.
"""

import inspect
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal, Optional

from pymodbus import FramerType
from pymodbus.client import ModbusTcpClient

from akkudoktoreos.adapter.controllers.controlabc import (
    CommandT,
    ControlDiagnosticLog,
    ControllerThreadBase,
    ControlSharedDataStore,
    MonitorThreadBase,
)

# pymodbus >= 3.10 names the unit id keyword "device_id", older versions "slave".
UNIT_KWARG = (
    "device_id"
    if "device_id" in inspect.signature(ModbusTcpClient.read_input_registers).parameters
    else "slave"
)


@dataclass(frozen=True, slots=True)
class ModbusStats:
    """Cumulative timing of one kind of Modbus transaction. Immutable snapshot."""

    count: int = 0  # transactions, successful or not
    errors: int = 0  # of which failed
    total_ms: float = 0.0
    max_ms: float = 0.0
    last_ms: float = 0.0

    @property
    def mean_ms(self) -> float:
        """Mean duration, 0.0 without transactions."""
        return self.total_ms / self.count if self.count else 0.0

    def add(self, ms: float, failed: bool) -> "ModbusStats":
        """Return the statistics including one more transaction."""
        return ModbusStats(
            count=self.count + 1,
            errors=self.errors + int(failed),
            total_ms=self.total_ms + ms,
            max_ms=max(self.max_ms, ms),
            last_ms=ms,
        )


class ModbusTcpMonitorThreadBase(MonitorThreadBase):
    """Monitor thread of a device that is read by Modbus TCP.

    Implements _connect() and _disconnect(). Subclasses implement _read_status() with the help
    of _read_input_registers(), _read_holding_registers() and, where the device needs it,
    _write_registers().

    The Modbus client is only ever touched by this thread. Modbus errors surface as
    exceptions, which the base class treats as a lost connection (disconnect, back off,
    reconnect).

    Thread-safe API (callable from the asyncio event loop):
        modbus_stats(), reset_modbus_stats()
    """

    def __init__(
        self,
        device_id: str,
        shared_data: ControlSharedDataStore,
        diagnostic_log: ControlDiagnosticLog,
        *,
        host: str,
        port: int = 502,
        unit_id: int = 1,
        timeout: float = 3.0,
        framer: FramerType = FramerType.SOCKET,
        **kwargs: Any,
    ) -> None:
        """Initialize the transport, the connection is opened by the thread.

        Args:
            device_id: Id of the device, prefix of its shared data keys.
            shared_data: Store for cross-device data.
            diagnostic_log: Diagnostic log of the controller.
            host: Host name or IP address of the device.
            port: TCP port of the device.
            unit_id: Modbus unit id of the device.
            timeout: Timeout of one Modbus transaction in seconds.
            framer: Modbus framing on the TCP connection, ``FramerType.RTU`` for RTU over TCP.
            **kwargs: Passed on to the thread base class (e.g. ``poll_interval``).
        """
        super().__init__(device_id, shared_data, diagnostic_log, **kwargs)
        self._host = host
        self._port = port
        self._unit_id = unit_id
        self._timeout = timeout
        self._framer = framer
        self._client: Optional[ModbusTcpClient] = None
        self._modbus_stats_lock = threading.Lock()
        self._modbus_stats: dict[str, ModbusStats] = {"read": ModbusStats(), "write": ModbusStats()}

    # ---------------- timing statistics (thread-safe) ----------------
    def modbus_stats(self) -> dict[str, ModbusStats]:
        """Return a snapshot of the cumulative transaction timing, keys "read" and "write"."""
        with self._modbus_stats_lock:
            return dict(self._modbus_stats)  # the values are immutable

    def reset_modbus_stats(self) -> None:
        """Start the statistics from zero."""
        with self._modbus_stats_lock:
            self._modbus_stats = {"read": ModbusStats(), "write": ModbusStats()}

    def _finish_transaction(
        self,
        operation: Literal["read", "write"],
        event: str,
        started: float,
        error: Optional[str],
        **data: Any,
    ) -> None:
        """Account the transaction in the statistics and record it in the diagnostic log."""
        ms = (time.perf_counter() - started) * 1000.0
        with self._modbus_stats_lock:
            self._modbus_stats[operation] = self._modbus_stats[operation].add(
                ms, failed=error is not None
            )
        self._record_diagnostic(event, error=error, ms=ms, **data)

    # ---------------- connection ----------------
    def _connect(self) -> None:
        client = ModbusTcpClient(
            self._host, port=self._port, timeout=self._timeout, framer=self._framer
        )
        if not client.connect():
            client.close()
            raise ConnectionError(f"{self.device_id}: cannot connect to {self._host}:{self._port}")
        self._client = client

    def _disconnect(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            client.close()

    # ---------------- register access ----------------
    def _require_client(self) -> ModbusTcpClient:
        if self._client is None:
            raise ConnectionError(f"{self.device_id}: not connected")
        return self._client

    def _read(self, kind: Literal["input", "holding"], address: int, count: int) -> list[int]:
        """Read registers, time the transaction and record it."""
        words: Optional[list[int]] = None
        error: Optional[str] = None
        started = time.perf_counter()
        try:
            client = self._require_client()
            method = (
                client.read_input_registers if kind == "input" else client.read_holding_registers
            )
            response = method(address, count=count, **self._unit_kwargs())
            if response.isError():
                raise OSError(f"{self.device_id}: Modbus read @{address} failed: {response}")
            words = list(response.registers)
            return words
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._finish_transaction(
                "read",
                "modbus_read",
                started,
                error,
                kind=kind,
                address=address,
                count=count,
                words=None if words is None else tuple(words),
            )

    def _unit_kwargs(self) -> dict[str, Any]:
        """Keyword argument of the unit id for the pymodbus request methods."""
        return {UNIT_KWARG: self._unit_id}

    def _read_input_registers(self, address: int, count: int = 2) -> list[int]:
        """Read input registers (function 04, 3xxxx on SMA devices).

        Raises OSError if the device answers with an error, e.g. for an address it does not know.
        """
        return self._read("input", address, count)

    def _read_holding_registers(self, address: int, count: int = 2) -> list[int]:
        """Read holding registers (function 03, 4xxxx on SMA devices).

        Raises OSError if the device answers with an error, e.g. for an address it does not know.
        """
        return self._read("holding", address, count)

    def _write_registers(self, address: int, values: tuple[int, ...]) -> None:
        """Write holding registers, time the transaction and record it.

        Raises OSError if the device answers with an error.
        """
        error: Optional[str] = None
        started = time.perf_counter()
        try:
            response = self._require_client().write_registers(
                address, list(values), **self._unit_kwargs()
            )
            if response.isError():
                raise OSError(f"{self.device_id}: Modbus write @{address} failed: {response}")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._finish_transaction(
                "write",
                "modbus_write",
                started,
                error,
                address=address,
                values=tuple(values),
            )


class ModbusTcpControllerThreadBase(ModbusTcpMonitorThreadBase, ControllerThreadBase[CommandT]):
    """Control thread of a device that is controlled by Modbus TCP.

    The transport of ``ModbusTcpMonitorThreadBase`` plus the command schedule of
    ``ControllerThreadBase``. Subclasses implement the remaining hooks (_read_status(),
    _apply_command(), _enter_safe_state()) with the help of _read_input_registers(),
    _read_holding_registers() and _write_registers().
    """
