"""SMA Energy Meter.

Receives the Speedwire multicast telegrams of one SMA Energy Meter and publishes the grid
readings of each telegram as plain numbers into the shared data store of the device control
adapter, in one batch (one timestamp, visible together):

    <device_id>:grid_import_kwh   energy drawn from the grid (monotonic counter)
    <device_id>:grid_export_kwh   energy fed into the grid (monotonic counter)
    <device_id>:grid_import_w     instantaneous power drawn from the grid
    <device_id>:grid_export_w     instantaneous power fed into the grid
    <device_id>:grid_net_w        import minus export, positive when drawing from the grid

Consumers (e.g. the inverter controller) read them with the age, and must not regulate on
missing or stale data::

    entry = self.shared_data.get_with_age(f"{meter_id}:{GRID_NET_W}")
    if entry is None or entry[1] > max_age_s:
        ...  # no data or stale data
    net_w, _ = entry

Several values that belong together are read from one coherent image with ``get_many()``.

The energy readings can be written to the EOS measurement store by the Control adapter, e.g.
``adapter.control.grid_import_emr_data_keys = ["meter1:grid_import_kwh"]`` (stored as measurement
``meter1_grid_import_kwh``).

Complete readings are also recorded as diagnostic series when diagnostic logging is enabled.
The diagnostic view exposes separate graphs for instantaneous grid power and cumulative energy.
"""

import time
from typing import Any, ClassVar, Literal, cast

from loguru import logger
from pydantic import Field

from akkudoktoreos.adapter.controllers.controlabc import (
    ControlDiagnosticLog,
    ControllerBase,
    ControllerBaseSettings,
    ControlSharedDataStore,
    MonitorThreadBase,
)
from akkudoktoreos.adapter.controllers.controldiagnostics import (
    DiagnosticGraph,
    SeriesSpec,
)

from .smaspeedwire import ANY_INTERFACE_IP, MeterTelegram, SpeedwireListener

_IMPORT_ENERGY = "active_power_import_energy"  # OBIS 1:1.8.0, kWh
_EXPORT_ENERGY = "active_power_export_energy"  # OBIS 1:2.8.0, kWh
_IMPORT_POWER = "active_power_import"  # OBIS 1:1.4.0, W
_EXPORT_POWER = "active_power_export"  # OBIS 1:2.4.0, W
_REQUIRED = (_IMPORT_ENERGY, _EXPORT_ENERGY, _IMPORT_POWER, _EXPORT_POWER)

# Socket timeout of the listener. A blocked receive is not interruptible, so this bounds the
# latency of stopping the thread. It does not limit the data rate (the meter sends ~1 telegram/s).
_RECEIVE_TIMEOUT_S = 1.0

# Shared data key suffixes (without "<device_id>:"). The unit is part of the name.
GRID_IMPORT_KWH = "grid_import_kwh"
GRID_EXPORT_KWH = "grid_export_kwh"
GRID_IMPORT_W = "grid_import_w"
GRID_EXPORT_W = "grid_export_w"
GRID_NET_W = "grid_net_w"
GRID_KEYS = (GRID_IMPORT_KWH, GRID_EXPORT_KWH, GRID_IMPORT_W, GRID_EXPORT_W, GRID_NET_W)


class SmaEnergyMeterSettings(ControllerBaseSettings):
    """Settings of the SMA Energy Meter controller."""

    controller_type: Literal["sma:energy-meter"]
    serial: int = Field(
        json_schema_extra={
            "description": "Serial number of the SMA Energy Meter. Telegrams of other meters are ignored.",
            "examples": [1900123456],
        },
    )
    interface_ip: str = Field(
        default=ANY_INTERFACE_IP,
        json_schema_extra={
            "description": (
                "Local IPv4 address of the network interface that joins the Speedwire multicast "
                "group. 0.0.0.0 selects the default interface of the operating system."
            ),
            "examples": [ANY_INTERFACE_IP, "192.168.1.10"],
        },
    )
    rejoin_after: float = Field(
        default=60.0,
        gt=0,
        json_schema_extra={
            "description": "Seconds without any telegram after which the multicast group is re-joined.",
            "examples": [60.0],
        },
    )


class SmaEnergyMeterThread(MonitorThreadBase):
    """Receives EMETER telegrams and publishes the grid readings into the shared data store.

    The socket is only ever touched by this thread. The status data stays empty: the
    readings are published into the shared data store instead of being sent through the
    status callback, which would wake the event loop with every telegram.
    """

    def __init__(
        self,
        device_id: str,
        shared_data: ControlSharedDataStore,
        diagnostic_log: ControlDiagnosticLog,
        *,
        serial: int,
        interface_ip: str = ANY_INTERFACE_IP,
        rejoin_after: float = 60.0,
    ) -> None:
        # poll_interval 0: the blocking receive in _read_status() paces the loop.
        super().__init__(device_id, shared_data, diagnostic_log, poll_interval=0.0)
        self._prefix = f"{device_id}:"
        self._serial = serial
        self._rejoin_after = rejoin_after
        self._listener = SpeedwireListener(interface_ip, timeout=_RECEIVE_TIMEOUT_S)

        # thread-private state
        self._last_telegram = time.monotonic()  # last decoded telegram of any meter
        self._foreign_serials: set[int] = set()
        self._warned_incomplete = False

    # ---------------- hooks ----------------
    def published_keys(self) -> tuple[str, ...]:
        """Return the fully qualified shared data keys this thread publishes."""
        return tuple(self._prefix + key for key in GRID_KEYS)

    def _connect(self) -> None:
        self._listener.open()

    def _disconnect(self) -> None:
        self._listener.close()

    def _on_connected(self) -> None:
        self._last_telegram = time.monotonic()

    def _read_status(self) -> dict[str, Any]:
        """Receive one telegram and publish its readings; blocks up to the socket timeout.

        A socket error (OSError) propagates: the base class backs off and reconnects.
        """
        telegram = self._listener.receive()  # None on timeout or malformed datagram
        now = time.monotonic()

        if telegram is not None:
            self._last_telegram = now
            readings = self._to_readings(telegram)
            if readings is not None:
                self.shared_data.publish_sync(
                    {self._prefix + key: value for key, value in readings.items()}
                )
                self._record_diagnostic("readings_updated", **readings)
        elif now - self._last_telegram > self._rejoin_after:
            logger.warning(
                "{}: no telegram for {:.0f} s, re-joining multicast group",
                self.device_id,
                now - self._last_telegram,
            )
            self._listener.close()  # next receive() reopens and re-joins
            self._last_telegram = now
        return {}

    # ---------------- internals ----------------
    def _to_readings(self, telegram: MeterTelegram) -> dict[str, float] | None:
        """Return readings (by key suffix) if the telegram is from our meter and complete."""
        if telegram.serial_number != self._serial:
            if telegram.serial_number not in self._foreign_serials:
                self._foreign_serials.add(telegram.serial_number)
                logger.warning(
                    "{}: ignoring telegrams of meter {} (configured: {})",
                    self.device_id,
                    telegram.serial_number,
                    self._serial,
                )
            return None

        values = telegram.values()
        missing = [name for name in _REQUIRED if name not in values]
        if missing:
            if not self._warned_incomplete:
                self._warned_incomplete = True
                logger.warning("{}: telegram lacks required values: {}", self.device_id, missing)
            return None

        import_w = values[_IMPORT_POWER]
        export_w = values[_EXPORT_POWER]
        return {
            GRID_IMPORT_KWH: values[_IMPORT_ENERGY],
            GRID_EXPORT_KWH: values[_EXPORT_ENERGY],
            GRID_IMPORT_W: import_w,
            GRID_EXPORT_W: export_w,
            GRID_NET_W: import_w - export_w,
        }


class SmaEnergyMeter(ControllerBase):
    """SMA Energy Meter.

    Receive-only: it has no schedule, the plan is not used. It provides the grid readings
    listed in ``GRID_KEYS`` under ``"<device_id>:<key>"``.
    """

    # Bound to the settings class by ControllerBase.__init_subclass__. This makes
    # SmaEnergyMeterSettings.shared_data_keys report PROVIDED_DATA_KEYS.
    Settings: ClassVar[type[SmaEnergyMeterSettings]] = SmaEnergyMeterSettings
    PROVIDED_DATA_KEYS: ClassVar[tuple[str, ...]] = GRID_KEYS

    #: Fields folded into diagnostic time series for each complete meter telegram.
    DIAGNOSTIC_SERIES: ClassVar[SeriesSpec] = {
        "readings_updated": GRID_KEYS,
    }

    #: Dashboard graph layout, split into instantaneous power and cumulative energy.
    DIAGNOSTIC_GRAPHS: ClassVar[tuple[DiagnosticGraph, ...]] = (
        DiagnosticGraph(
            title="Grid power",
            unit="W",
            kind="numeric",
            series=[
                "readings_updated.grid_import_w",
                "readings_updated.grid_export_w",
                "readings_updated.grid_net_w",
            ],
        ),
        DiagnosticGraph(
            title="Grid energy",
            unit="kWh",
            kind="numeric",
            series=[
                "readings_updated.grid_import_kwh",
                "readings_updated.grid_export_kwh",
            ],
        ),
    )

    @property
    def settings(self) -> SmaEnergyMeterSettings:
        """Settings snapshot, typed for this controller (checked against Settings in __init__)."""
        return cast(SmaEnergyMeterSettings, self._settings)

    def _create_control_thread(self) -> MonitorThreadBase:
        """Build a fresh, not yet started thread."""
        return SmaEnergyMeterThread(
            self.device_id,
            self.shared_data,
            self.diagnostic_log,
            serial=self.settings.serial,
            interface_ip=self.settings.interface_ip,
            rejoin_after=self.settings.rejoin_after,
        )
