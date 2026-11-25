r"""Diagnostics controller of an SMA Sunny Tripower Smart Energy (STP-SE).

Reports what the controller reads and what it write/ would write:

1. Configuration: the effective settings and tuning of the controller.
2. Startup: the registers the controller reads on connect (battery limits and, if the PV limit
   is configured, the PV limit parameters), decoded, with the Modbus transactions.
3. Control cycle: in every cycle the registers the controller reads for active control
   (the same block reads), the values it derives from them, the grid power it takes from the
   shared data store, the decisions of the controller and the register writes the selected mode
   WOULD make (dry run) or makes (live).
4. Extra information: further readable registers of the STP-SE (status, voltages, temperatures,
   grid switch state, battery control availability, and the control registers read back), read
   individually. Registers the inverter does not know are reported as errors, not fatal.

Source of the report: the diagnostic log
----------------------------------------
The monitor runs the real control code (``SmaStpeControl``) against the live inverter with an
enabled ``ControlDiagnosticLog``. Every report is built from the events in that log, which is
drained after each step:

- ``readings_updated``, ``battery_limits``, ``pv_curtailment_initialized``, ``grid_input``,
  ``command_received``, ``control_decision``, ``pid_calculation``, ``setpoint_calculated``,
  ``pv_curtailment_decision``, ``register_write``, ``register_write_error``,
  ``battery_context_updated``, ``battery_context_error``, ``battery_limits_error``: recorded by
  the controller itself.

The battery profile is used without battery device (``SmaBatteryContext.from_profile``): full
SoC range, no power limits of the installation and no inverter device, with the nominal capacity
the inverter reports. A generic profile without own power limits gets the generic default power
limits of EOS.
- ``modbus_read`` and ``modbus_write``: recorded by ``ModbusTcpControllerThreadBase`` for every
  transaction (address, count or values, words, duration, error). The raw words are needed for
  the decoded register tables.
- ``register_write_suppressed``: recorded by ``DiagnosticsControl`` in a dry run, to tell it from
  a sent write (``modbus_write``).

How a dry run stays read-only
-----------------------------
The write path of the control thread, ``_write_regs()``, records ``register_write`` and then
calls ``_write_registers()``. ``DiagnosticsControl`` overrides that method: without
``--write-registers`` it only records ``register_write_suppressed``. There is no other way for
the control thread to write, and the monitor never calls a Modbus write method itself. The write
registers of the inverter are volatile and usually read back as 0 or "not available", so the
read-back is informational only.

With ``--write-registers`` the writes are sent to the inverter. On exit the monitor then puts the
inverter into its safe state like the real control thread does on shutdown.

The grid power for the "pid" modes comes from the shared data store. Give ``--meter-serial`` to
read it from an SMA Energy Meter (Speedwire multicast) while monitoring. Without it the
controller sees no grid power and holds the battery, which is reported as such.

Usage::

    uv run python -m akkudoktoreos.adapter.controllers.sma.smastpediagnostics --host 192.168.178.6 \\
        --meter-serial 3019226199 --interval 5

    uv run python -m ... --host 192.168.178.6 --mode FORCED_CHARGE --factor 0.5 --count 1
    uv run python -m ... --host 192.168.178.6 --pv-rated-power-w 10000   # also the PV limit registers
    uv run python -m ... --host 192.168.178.6 --json                     # one JSON object per line
    uv run python -m ... --host 192.168.178.6 --battery-profile my-battery.json  # profile file

``main()`` returns the exit code (0: ok, 2: connecting or the startup reads failed), so it can
be called from a program's own ``main``.
"""

import argparse
import itertools
import json
import sys
import time
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from akkudoktoreos.adapter.controllers.controlabc import (
    ControlDiagnosticEntry,
    ControlDiagnosticLog,
    ControlSharedDataStore,
)
from akkudoktoreos.adapter.controllers.sma import smastpe as s
from akkudoktoreos.adapter.controllers.sma.smaspeedwire import ANY_INTERFACE_IP
from akkudoktoreos.adapter.controllers.sma.smastpepid import (
    GridControllerSettings,
    PvLimiterSettings,
)
from akkudoktoreos.devices.devicesabc import BatteryOperationMode
from akkudoktoreos.devices.profile.batteryprofile import (
    BatteryProfile,
    BatteryProfileSettings,
)
from akkudoktoreos.utils.datetimeutil import to_datetime

# Capacity of the diagnostic log. The log is drained after every step, so this only has to hold
# the events of one control cycle (or of one read of the extra registers).
DIAGNOSTIC_LOG_CAPACITY = 2000

# Diagnostic events recorded by the Modbus base class (ModbusTcpControllerThreadBase).
EVENT_MODBUS_READ = "modbus_read"
EVENT_MODBUS_WRITE = "modbus_write"
# Diagnostic events recorded by the controller around the write.
EVENT_WRITE = "register_write"
EVENT_WRITE_ERROR = "register_write_error"
# Diagnostic event recorded by DiagnosticsControl in a dry run (the only event of its own).
EVENT_WRITE_SUPPRESSED = "register_write_suppressed"
# Events that have a section of their own in the cycle report. The remaining ones are listed
# as controller events.
_OWN_SECTION_EVENTS = frozenset(
    {
        EVENT_MODBUS_READ,
        EVENT_MODBUS_WRITE,
        EVENT_WRITE,
        EVENT_WRITE_SUPPRESSED,
        EVENT_WRITE_ERROR,
        "readings_updated",
        "grid_input",
    }
)

# ---------------------------------------------------------------------------
# Register catalogue
# ---------------------------------------------------------------------------
_SIZE = {"u16": 1, "s16": 1, "u32": 2, "s32": 2}
_NOT_AVAILABLE = {"u16": 0xFFFF, "s16": -(1 << 15), "u32": 0xFFFFFFFF, "s32": -(1 << 31)}
# SMA uses 16777213 (0x00FFFFFD) for "information not available" in tag lists
_NO_INFORMATION = 16777213

_OPMOD = {
    303: "pause",
    308: "on",
    1438: "auto (dynamic)",
    2289: "charge only",
    2290: "discharge only",
    2424: "default",
}


@dataclass(frozen=True)
class RegisterSpec:
    """A register of the inverter: where it is and how to read the value."""

    name: str
    address: int
    dtype: str  # u16, s16, u32, s32
    unit: str = ""
    scale: float = 1.0
    kind: str = "input"  # input registers (3xxxx) or holding registers (4xxxx)
    enum: Optional[Mapping[int, str]] = None
    note: str = ""

    @property
    def count(self) -> int:
        """Number of 16 bit registers the value occupies."""
        return _SIZE[self.dtype]


# What the controller reads on connect (holding registers).
STARTUP_REGISTERS = (
    RegisterSpec(
        "Bat.CapacRtgWh",
        s.REG_BAT_NOM_CAPACITY,
        "u32",
        "Wh",
        kind="holding",
        note="battery nominal capacity",
    ),
    RegisterSpec(
        "WMaxCha", s.REG_BAT_MAX_CHARGE, "u32", "W", kind="holding", note="maximum charge power"
    ),
    RegisterSpec(
        "WMaxDsch",
        s.REG_BAT_MAX_DISCHARGE,
        "u32",
        "W",
        kind="holding",
        note="maximum discharge power",
    ),
)

# Read on connect only if the PV limit is configured.
PV_LIMIT_REGISTERS = (
    RegisterSpec(
        "Inverter.WModCfg.WMod",
        s.REG_PV_WMOD,
        "u32",
        kind="holding",
        enum={s.WMOD_PLANT_CONTROL: "plant control (required for the PV limit)"},
        note="active power limitation mode, persistent parameter",
    ),
    RegisterSpec(
        "Inverter.WModCfg.WCtlComCfg.WNomPrc",
        s.REG_PV_LIMIT_PERCENT,
        "s16",
        "%",
        kind="holding",
        note="PV limit in percent of the rated AC power, persistent parameter",
    ),
)

# What the controller reads in every control cycle (input registers, in blocks).
CONTROL_REGISTERS = (
    RegisterSpec("DC power A", s.REG_DC1_POWER, "s32", "W"),
    RegisterSpec("GridMs.TotW", s.REG_AC_POWER, "s32", "W", note="total AC active power"),
    RegisterSpec("DC power B", s.REG_DC2_POWER, "s32", "W"),
    RegisterSpec("Bat.ChaStt", s.REG_BAT_SOC, "u32", "%", note="battery SoC"),
    RegisterSpec(
        "Bat.Diag.ActlBatCha",
        s.REG_BAT_ACT_CAPACITY,
        "u32",
        note="read with the SoC block, not used",
    ),
    RegisterSpec(
        "Bat.Diag.TmpVal", s.REG_BAT_TEMPERATURE, "s32", "degC", 0.1, note="battery temperature"
    ),
    RegisterSpec(
        "BatChrg.CurBatCha", s.REG_BAT_CHARGE_POWER, "u32", "W", note="current charge power"
    ),
    RegisterSpec(
        "BatDsch.CurBatDsch", s.REG_BAT_DISCHARGE_POWER, "u32", "W", note="current discharge power"
    ),
)

# Further readable registers (register reference of the community, standard SMA registers).
EXTRA_REGISTERS = (
    RegisterSpec("DevClss", 30051, "u32", note="device class (standard SMA register)"),
    RegisterSpec("DevTypeId", 30053, "u32", note="device type (standard SMA register)"),
    RegisterSpec("SerNum", 30057, "u32", note="serial number (standard SMA register)"),
    RegisterSpec(
        "Operation.Health",
        30201,
        "u32",
        enum={35: "fault", 303: "off", 307: "ok", 455: "warning"},
        note="standard SMA register",
    ),
    RegisterSpec(
        "Operation.GriSwStt", 30217, "u32", enum={51: "closed", 311: "open"}, note="grid switch"
    ),
    RegisterSpec("Bat.Vol", 30843, "u32", "V", 0.01, note="battery voltage"),
    RegisterSpec("Bat.Diag.VolMeas", 30851, "u32", "V", 0.01, note="measured battery voltage"),
    RegisterSpec("Metering.GridMs.TotWIn", 30865, "u32", "W", note="grid import"),
    RegisterSpec("Metering.GridMs.TotWOut", 30867, "u32", "W", note="grid export"),
    RegisterSpec(
        "Operation.PvGriConn",
        30881,
        "u32",
        enum={1779: "disconnected", 1780: "public grid", 1781: "island grid"},
    ),
    RegisterSpec(
        "Coolsys.Cab.TmpVal", 30953, "s32", "degC", 0.1, note="inverter inner temperature"
    ),
    RegisterSpec(
        "Bat.ChaCtlComAval",
        31061,
        "u32",
        enum={1129: "yes", 1130: "no"},
        note="battery control available",
    ),
    RegisterSpec(
        "Operation.RunStt",
        33003,
        "u32",
        enum={
            235: "grid parallel operation",
            1463: "backup",
            1469: "shutting down",
            2119: "CURTAILMENT",
        },
        note="operating status; 2119 = the inverter curtails the PV production",
    ),
    RegisterSpec("Bat.Type", 40035, "u32", kind="holding", enum={1785: "lithium"}),
    RegisterSpec(
        "BatUsDm.BckDmMin",
        40723,
        "u32",
        "%",
        kind="holding",
        note="backup range, persistent parameter",
    ),
    RegisterSpec(
        "Inverter.WModCfg.WMod",
        s.REG_PV_WMOD,
        "u32",
        kind="holding",
        enum={s.WMOD_PLANT_CONTROL: "plant control"},
        note="persistent parameter",
    ),
    RegisterSpec(
        "Inverter.WModCfg.WCtlComCfg.WNomPrc",
        s.REG_PV_LIMIT_PERCENT,
        "s16",
        "%",
        kind="holding",
        note="PV limit, persistent parameter",
    ),
)

# Control registers read back. They are volatile: usually 0 or "not available".
CONTROL_STATE_REGISTERS = (
    RegisterSpec(
        "external battery setpoint",
        s.REG_WCTL_SPT,
        "s32",
        "W",
        kind="holding",
        note="write register",
    ),
    RegisterSpec(
        "external control",
        s.REG_WCTL_ACT,
        "u32",
        kind="holding",
        enum={s.WCTL_ACTIVE: "active", s.WCTL_INACTIVE: "inactive"},
        note="write register",
    ),
    RegisterSpec(
        "CmpBMS.OpMod (40236)",
        s.REG_BMS_OPMOD,
        "u32",
        kind="holding",
        enum=_OPMOD,
        note="write register",
    ),
    RegisterSpec(
        "CmpBMS.OpMod (41259)", 41259, "u32", kind="holding", enum=_OPMOD, note="write register"
    ),
    RegisterSpec(
        "CmpBMS.BatChaMinW", s.REG_BMS_BLOCK, "u32", "W", kind="holding", note="write register"
    ),
    RegisterSpec(
        "CmpBMS.BatChaMaxW", s.REG_BMS_BLOCK + 2, "u32", "W", kind="holding", note="write register"
    ),
    RegisterSpec(
        "CmpBMS.BatDschMinW", s.REG_BMS_BLOCK + 4, "u32", "W", kind="holding", note="write register"
    ),
    RegisterSpec(
        "CmpBMS.BatDschMaxW", s.REG_BMS_BLOCK + 6, "u32", "W", kind="holding", note="write register"
    ),
    RegisterSpec(
        "CmpBMS.GridWSpt", s.REG_BMS_BLOCK + 8, "s32", "W", kind="holding", note="write register"
    ),
)


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------
def decode_raw(dtype: str, words: Sequence[int]) -> int:
    """The register words as an integer (big endian words, two's complement for signed types)."""
    value = 0
    for word in words[: _SIZE[dtype]]:
        value = (value << 16) | (word & 0xFFFF)
    bits = 16 * _SIZE[dtype]
    if dtype.startswith("s") and value >= 1 << (bits - 1):
        value -= 1 << bits
    return value


@dataclass
class RegisterValue:
    """A decoded register."""

    name: str
    address: int
    kind: str
    raw: Optional[int] = None
    value: Optional[float] = None
    unit: str = ""
    text: str = ""
    error: Optional[str] = None
    note: str = ""

    def display(self) -> str:
        """Return the text to show: the decoded value, or the read error."""
        if self.error:
            return f"ERROR {self.error}"
        return self.text


def make_value(spec: RegisterSpec, words: Sequence[int]) -> RegisterValue:
    """Decode the register words read for a register.

    Args:
        spec: The register.
        words: The 16 bit words read, ``spec.count`` of them.

    Returns:
        The decoded value; ``text`` is ``"n/a"`` for the SMA "not available" values.
    """
    raw = decode_raw(spec.dtype, words)
    result = RegisterValue(
        spec.name, spec.address, spec.kind, raw=raw, unit=spec.unit, note=spec.note
    )
    if raw == _NOT_AVAILABLE[spec.dtype] or raw == _NO_INFORMATION:
        result.text = "n/a"
        return result
    result.value = raw * spec.scale
    number = f"{result.value:g}" if spec.scale != 1.0 else str(raw)
    label = spec.enum.get(raw) if spec.enum else None
    result.text = f"{number} {spec.unit}".strip() + (f" = {label}" if label else "")
    return result


@dataclass
class Transaction:
    """One Modbus read, rebuilt from a ``modbus_read`` diagnostic event."""

    kind: str
    address: int
    count: int
    words: Optional[list[int]] = None
    error: Optional[str] = None
    ms: float = 0.0


@dataclass
class WriteRecord:
    """One register write, rebuilt from the ``register_write*`` diagnostic events."""

    address: int
    values: tuple[int, ...]
    meaning: str = ""  # what the write does, in words (describe_write)
    action: str = "attempted"  # "dry run" (not sent), "written" or "failed"
    error: Optional[str] = None
    ms: Optional[float] = None


def values_from_transactions(
    specs: Sequence[RegisterSpec], transactions: Sequence[Transaction]
) -> list[RegisterValue]:
    """Decode the registers from the transactions that covered them."""
    values = []
    for spec in specs:
        covering = [
            t
            for t in transactions
            if t.kind == spec.kind
            and t.address <= spec.address
            and spec.address + spec.count <= t.address + t.count
        ]
        done = next((t for t in covering if t.words is not None), None)
        if done is not None and done.words is not None:
            offset = spec.address - done.address
            values.append(make_value(spec, done.words[offset : offset + spec.count]))
        else:
            reason = next((t.error for t in covering if t.error), "not read")
            values.append(
                RegisterValue(
                    spec.name, spec.address, spec.kind, error=reason, note=spec.note, unit=spec.unit
                )
            )
    return values


# ---------------------------------------------------------------------------
# Diagnostic log evaluation
# ---------------------------------------------------------------------------
def transactions_from_events(entries: Sequence[ControlDiagnosticEntry]) -> list[Transaction]:
    """The Modbus reads recorded in the log, in order."""
    transactions = []
    for entry in entries:
        if entry.event != EVENT_MODBUS_READ:
            continue
        data = entry.data
        words = data.get("words")
        transactions.append(
            Transaction(
                kind=data["kind"],
                address=data["address"],
                count=data["count"],
                words=None if words is None else list(words),
                error=data.get("error"),
                ms=data.get("ms", 0.0),
            )
        )
    return transactions


def writes_from_events(entries: Sequence[ControlDiagnosticEntry]) -> list[WriteRecord]:
    """The register writes recorded in the log, in order, with their outcome.

    The controller records ``register_write`` before the transport write. It is followed by
    ``register_write_suppressed`` (dry run) or ``modbus_write`` (sent, with ``error`` set if it
    failed). A failed write is also followed by the controller's ``register_write_error``.
    """
    writes: list[WriteRecord] = []
    for entry in entries:
        data = entry.data
        if entry.event == EVENT_WRITE:
            values = tuple(data["values"])
            writes.append(
                WriteRecord(
                    data["address"], values, meaning=describe_write(data["address"], values)
                )
            )
        elif not writes:
            continue
        elif entry.event == EVENT_WRITE_SUPPRESSED:
            writes[-1].action = "dry run"
        elif entry.event == EVENT_MODBUS_WRITE:
            writes[-1].ms = data.get("ms")
            if data.get("error"):
                writes[-1].action = "failed"
                writes[-1].error = data["error"]
            else:
                writes[-1].action = "written"
        elif entry.event == EVENT_WRITE_ERROR:
            writes[-1].action = "failed"
            writes[-1].error = writes[-1].error or data.get("error")
    return writes


def last_event(
    entries: Sequence[ControlDiagnosticEntry], event: str
) -> Optional[Mapping[str, Any]]:
    """Data of the last event of that name, None if it was not recorded."""
    for entry in reversed(entries):
        if entry.event == event:
            return entry.data
    return None


# ---------------------------------------------------------------------------
# The control thread with traced and optionally suppressed register access
# ---------------------------------------------------------------------------
class DiagnosticsControl(s.SmaStpeControl):
    """The real control thread, with writes suppressed unless ``write_registers`` is set.

    Reads and live writes are recorded by the Modbus base class (``modbus_read``,
    ``modbus_write``). A suppressed write is recorded as ``register_write_suppressed``.
    """

    def __init__(
        self,
        *args: Any,
        write_registers: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.write_registers = write_registers

    def _write_registers(self, address: int, values: tuple[int, ...]) -> None:
        """Send the write only when live writing is enabled, otherwise record it."""
        if not self.write_registers:
            self._record_diagnostic(EVENT_WRITE_SUPPRESSED, address=address, values=tuple(values))
            return
        super()._write_registers(address, values)


def describe_write(address: int, values: Sequence[int]) -> str:
    """What a register write does, in words."""
    if address == s.REG_WCTL_SPT and len(values) == 4:
        setpoint = decode_raw("s32", values[0:2])
        active = decode_raw("u32", values[2:4]) == s.WCTL_ACTIVE
        return f"battery setpoint {setpoint} W (charge -, discharge +), {address + 2}: external control {'active' if active else 'inactive'}"
    if address == s.REG_WCTL_ACT:
        return "external control " + {s.WCTL_ACTIVE: "active", s.WCTL_INACTIVE: "inactive"}.get(
            decode_raw("u32", values), "?"
        )
    if address == s.REG_BMS_BLOCK and len(values) == 10:
        cha_min, cha_max, dsch_min, dsch_max = (
            decode_raw("u32", values[i : i + 2]) for i in (0, 2, 4, 6)
        )
        grid = decode_raw("s32", values[8:10])
        return (
            f"charge window {cha_min}..{cha_max} W, discharge window {dsch_min}..{dsch_max} W, "
            f"grid setpoint {grid} W (export +, import -)"
        )
    if address == s.REG_BMS_OPMOD:
        mode = decode_raw("u32", values)
        return f"OpMod {mode} = {_OPMOD.get(mode, '?')}"
    if address == s.REG_PV_LIMIT_PERCENT:
        return f"PV limit {decode_raw('s16', values)} % of the rated AC power"
    return f"{len(values)} register(s): {list(values)}"


def _jsonable(value: Any) -> Any:
    """Convert reports (dataclasses, diagnostic entries, mappings, datetimes) for json.dumps."""
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _jsonable(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, MappingABC):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


# ---------------------------------------------------------------------------
# Options and the run
# ---------------------------------------------------------------------------


@dataclass
class MonitorOptions:
    """Options of the monitor, as given on the command line."""

    host: str
    port: int = 502
    unit_id: int = s.SMA_UNIT_ID
    timeout_s: float = 3.0
    write_registers: bool = False  # False: dry run; True: send writes to inverter
    mode: str = BatteryOperationMode.SELF_CONSUMPTION.value
    factor: float = 1.0
    grid_power_key: str = "meter:grid_net_w"
    grid_power_max_age_s: float = 3.0
    meter_serial: Optional[int] = None
    meter_interface_ip: str = ANY_INTERFACE_IP
    pv_rated_power_w: Optional[float] = None
    interval_s: float = 5.0
    count: int = 0
    extra_interval_s: float = 30.0
    as_json: bool = False
    battery_profile: str = "generic-lfp"  # profile name in the profile store, or a JSON file


# Former name of the options.
DiagnosticsOptions = MonitorOptions


def _now() -> str:
    return to_datetime().isoformat(timespec="seconds")


def configuration(options: MonitorOptions) -> dict[str, Any]:
    """The effective settings and tuning of the controller."""
    tuning = GridControllerSettings()
    config: dict[str, Any] = {
        "inverter": f"{options.host}:{options.port}, unit id {options.unit_id}, timeout {options.timeout_s} s",
        "mode": options.mode,
        "operation factor": options.factor,
        "register write mode": "LIVE — writes are sent to the inverter"
        if options.write_registers
        else "DRY RUN — writes are only recorded",
        "grid power key": options.grid_power_key,
        "grid power max age s": options.grid_power_max_age_s,
        "control interval s": s.CONTROL_INTERVAL_S,
        "register refresh s": s.REGISTER_REFRESH_S,
        "pid modes": sorted(m.value for m in s.PID_MODES),
        "grid controller tuning": {k: getattr(tuning, k) for k in tuning.__dataclass_fields__},
        "grid power source": (
            f"SMA Energy Meter {options.meter_serial} (Speedwire, interface {options.meter_interface_ip})"
            if options.meter_serial is not None
            else "none: the controller sees no grid power"
        ),
        "PV limit": (
            f"rated power {options.pv_rated_power_w} W"
            + ("" if options.write_registers else " (dry run)")
            if options.pv_rated_power_w
            else "not configured"
        ),
        "diagnostic log capacity": DIAGNOSTIC_LOG_CAPACITY,
        "battery profile": options.battery_profile,
    }
    return config


def load_battery_profile(name_or_path: str) -> BatteryProfile:
    """Load the battery profile the controller limits the battery power with.

    Args:
        name_or_path: Path of a battery profile JSON file, or the name of a battery profile in
            the profile store (e.g. ``"generic-lfp"``).

    Raises:
        FileNotFoundError: If neither a file nor a profile of that name exists.
        ValueError: If the profile document is invalid.
    """
    path = Path(name_or_path)
    if path.suffix == ".json" or path.is_file():
        return BatteryProfileSettings.from_json_file(path)
    return BatteryProfileSettings.from_profile_name(name_or_path)


def read_extras(thread: DiagnosticsControl, log: ControlDiagnosticLog) -> list[RegisterValue]:
    """Read the extra registers one by one; a register the inverter refuses is reported."""
    values = []
    for spec in EXTRA_REGISTERS + CONTROL_STATE_REGISTERS:
        read = (
            thread._read_input_registers if spec.kind == "input" else thread._read_holding_registers
        )
        try:
            values.append(make_value(spec, read(spec.address, spec.count)))
        except Exception as exc:
            values.append(
                RegisterValue(
                    spec.name,
                    spec.address,
                    spec.kind,
                    error=f"{type(exc).__name__}: {exc}",
                    note=spec.note,
                )
            )
    log.drain()  # the reads were traced into the log, they are not part of a cycle report
    return values


def startup_report(
    thread: DiagnosticsControl, log: ControlDiagnosticLog, options: MonitorOptions
) -> dict[str, Any]:
    """Connect and report what the controller reads on connect."""
    log.drain()
    thread._connect()  # raises if the inverter is not reachable or has no battery
    entries = log.drain()
    transactions = transactions_from_events(entries)
    specs = STARTUP_REGISTERS + (PV_LIMIT_REGISTERS if options.pv_rated_power_w else ())
    limits = last_event(entries, "battery_limits")
    report: dict[str, Any] = {
        "registers": values_from_transactions(specs, transactions),
        "transactions": transactions,
        "battery limits": None if limits is None else dict(limits),
        "events": entries,
    }
    pv = last_event(entries, "pv_curtailment_initialized")
    if options.pv_rated_power_w and pv is not None:
        report["PV limit usable"] = pv["available"]
        report["PV limit in force %"] = pv["current_limit_percent"]
    return report


def cycle_report(
    thread: DiagnosticsControl,
    shared: ControlSharedDataStore,
    log: ControlDiagnosticLog,
    options: MonitorOptions,
) -> dict[str, Any]:
    """One control cycle: the reads of the controller and the writes it makes or would make.

    Same order as the control thread: apply the command, then read and publish the status.
    """
    log.drain()
    # A cycle reports what it reads. Without this a reading younger than the cache time of the
    # controller (0.5 s) is reused, e.g. with a short --interval, and the cycle shows no reads.
    thread._readings = None
    started = time.perf_counter()
    error: Optional[str] = None
    status: dict[str, Any] = {}
    try:
        command = s.SmaCommand(
            date_time=to_datetime(),
            mode=BatteryOperationMode(options.mode),
            operation_factor=options.factor,
        )
        thread._apply_command(command)
        status = thread._read_status()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    entries = log.drain()
    transactions = transactions_from_events(entries)

    readings = last_event(entries, "readings_updated")
    stored = shared.get_with_age(options.grid_power_key)
    seen = last_event(entries, "grid_input")
    inputs_valid = last_event(
        [e for e in entries if e.event == "setpoint_calculated" and "inputs_valid" in e.data],
        "setpoint_calculated",
    )

    report: dict[str, Any] = {
        "time": _now(),
        "error": error,
        "registers": values_from_transactions(CONTROL_REGISTERS, transactions),
        "transactions": transactions,
        "battery limits": s_limits(thread),
        "derived": None
        if readings is None
        else {
            "AC power W": readings["ac_power_w"],
            "PV DC power W (A + B)": readings["dc_pv_power_w"],
            "battery power W (discharge +, charge -)": readings["battery_power_w"],
            "SoC %": readings["soc_percent"],
            "battery temperature degC": readings["temperature_c"],
            "inverter read ms (all transactions)": _round_ms(readings.get("read_ms")),
        },
        "published": status,
        "grid power": {
            "shared data value W (import +)": None if stored is None else stored[0],
            "age s": None if stored is None else round(stored[1], 2),
            "controller input": None if seen is None else dict(seen),
        },
        "controller inputs complete": None
        if inputs_valid is None
        else inputs_valid["inputs_valid"],
        "controller events": [e for e in entries if e.event not in _OWN_SECTION_EVENTS],
        "write mode": "live" if thread.write_registers else "dry run",
        "writes": writes_from_events(entries),
        "timing": {
            "modbus transactions": len(transactions),
            "modbus ms": round(sum(t.ms for t in transactions), 1),
            "inverter read ms": None if readings is None else _round_ms(readings.get("read_ms")),
            "cycle ms": round(elapsed_ms, 1),
            "control interval ms": round(s.CONTROL_INTERVAL_S * 1000.0, 1),
            "modbus stats since start": {
                operation: {
                    "count": st.count,
                    "errors": st.errors,
                    "mean ms": round(st.mean_ms, 1),
                    "max ms": round(st.max_ms, 1),
                }
                for operation, st in thread.modbus_stats().items()
            },
        },
        "events": entries,
    }
    return report


def _round_ms(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 1)


def s_limits(thread: DiagnosticsControl) -> Optional[dict[str, Any]]:
    """The battery limits the controller currently uses."""
    limits = thread._limits
    if limits is None:
        return None
    return {
        "capacity_wh": limits.capacity_wh,
        "max_charge_w": limits.max_charge_w,
        "max_discharge_w": limits.max_discharge_w,
    }


# ---------------------------------------------------------------------------
# Text output
# ---------------------------------------------------------------------------
def _rows(values: Sequence[RegisterValue]) -> list[str]:
    lines = []
    for v in values:
        note = f"  ({v.note})" if v.note else ""
        lines.append(f"  {v.address:>6}  {v.name:<38} {v.display()}{note}")
    return lines


def _transaction_lines(transactions: Sequence[Transaction]) -> list[str]:
    return [
        f"  {t.kind:<7} {t.address:>6} x{t.count}  {t.ms:6.1f} ms  "
        + (f"ERROR {t.error}" if t.error else "ok")
        for t in transactions
    ]


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return str(round(value, 3))
    return str(value)


def _event_lines(entries: Sequence[ControlDiagnosticEntry]) -> list[str]:
    lines = []
    for entry in entries:
        stamp = entry.date_time.strftime("%H:%M:%S.%f")[:-3]
        data = ", ".join(f"{k}={_fmt(v)}" for k, v in entry.data.items())
        lines.append(f"    {stamp}  {entry.event}: {data}")
    return lines


def format_configuration(config: Mapping[str, Any]) -> list[str]:
    """Format the configuration section of the monitor output.

    Args:
        config: The configuration, nested one level at most.

    Returns:
        The lines of the section.
    """
    lines = ["== Configuration"]
    for key, value in config.items():
        if isinstance(value, dict):
            lines.append(f"  {key}:")
            lines += [f"      {k} = {v}" for k, v in value.items()]
        else:
            lines.append(f"  {key}: {value}")
    return lines


def format_startup(report: Mapping[str, Any]) -> list[str]:
    """Format the report of what the controller reads on connect.

    Args:
        report: The startup report of the monitor.

    Returns:
        The lines of the section.
    """
    lines = ["== Startup: read on connect"]
    lines += _rows(report["registers"])
    limits = report["battery limits"]
    if limits:
        lines.append(f"  -> battery limits used: {limits}")
    if "PV limit usable" in report:
        lines.append(
            f"  -> PV limit usable: {report['PV limit usable']}, in force: {report['PV limit in force %']} %"
        )
    lines.append("  transactions:")
    lines += _transaction_lines(report["transactions"])
    return lines


def format_extras(values: Sequence[RegisterValue]) -> list[str]:
    """Format the extra registers that are read for information only.

    Args:
        values: The values of the extra registers.

    Returns:
        The lines of the section.
    """
    lines = ["== Extra information (read individually, not used by the controller)"]
    lines += _rows(values)
    run_state = next((v for v in values if v.name == "Operation.RunStt"), None)
    if run_state is not None and run_state.raw == 2119:
        lines.append("  !! the inverter reports CURTAILMENT (operating status 2119)")
    lines.append("  (control registers are volatile: reading 0 or n/a is expected)")
    return lines


def format_cycle(number: int, report: Mapping[str, Any]) -> list[str]:
    """Format the report of one control cycle.

    Args:
        number: The number of the cycle, counted from 1.
        report: The cycle report of the monitor.

    Returns:
        The lines of the section.
    """
    lines = [f"== Control cycle {number}  {report['time']}"]
    if report["error"]:
        lines.append(f"  ERROR {report['error']}")
    lines.append("  read for active control:")
    lines += _rows(report["registers"])
    if report["derived"]:
        lines.append("  derived (what the controller uses):")
        lines += [f"    {k}: {v}" for k, v in report["derived"].items()]
    lines.append(f"  write mode: {report['write mode']}")
    limits = report["battery limits"]
    if limits:
        lines.append("  battery limits used by controller:")
        lines += [f"    {key}: {value}" for key, value in limits.items()]
    else:
        lines.append("  battery limits: unavailable")
    if report["published"]:
        lines.append("  published to the shared data store as <device>:<key>:")
        lines += [f"    {k}: {v}" for k, v in report["published"].items()]
    grid = report["grid power"]
    if grid["shared data value W (import +)"] is None:
        lines.append("  grid power: not in the shared data store")
    else:
        lines.append(
            f"  grid power in the shared data store: {grid['shared data value W (import +)']} W "
            f"(import +), age {grid['age s']} s"
        )
    if grid["controller input"] is None:
        lines.append("  grid power read by the controller: not read in this mode")
    else:
        lines.append(f"  grid power read by the controller: {grid['controller input']}")
    if report["controller inputs complete"] is not None:
        lines.append(f"  controller inputs complete: {report['controller inputs complete']}")
    if report["controller events"]:
        lines.append("  controller events (diagnostic log):")
        lines += _event_lines(report["controller events"])
    lines.append("  register writes:")
    if report["writes"]:
        for write in report["writes"]:
            line = (
                f"    {write.address}: {describe_write(write.address, write.values)}"
                f" [{write.action}]"
            )
            if write.error:
                line += f" ERROR {write.error}"
            elif write.ms is not None:
                line += f" ({write.ms:.1f} ms)"
            lines.append(line)
    else:
        lines.append("    nothing")
    lines.append("  transactions:")
    lines += _transaction_lines(report["transactions"])
    lines.append(f"  timing: {report['timing']}")
    return lines


# ---------------------------------------------------------------------------
# Run and main
# ---------------------------------------------------------------------------
def run_monitor(
    options: MonitorOptions,
    *,
    out: Callable[[str], None] = print,
    shared: Optional[ControlSharedDataStore] = None,
    diagnostic_log: Optional[ControlDiagnosticLog] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Monitor the inverter. Returns 0, or 2 if connecting or the startup reads failed."""
    shared = shared or ControlSharedDataStore()
    log = diagnostic_log or ControlDiagnosticLog(DIAGNOSTIC_LOG_CAPACITY)
    if not log.enabled:
        log.resize(DIAGNOSTIC_LOG_CAPACITY)
    pv = (
        None
        if not options.pv_rated_power_w
        else PvLimiterSettings(rated_power_w=options.pv_rated_power_w)
    )
    try:
        battery_profile = load_battery_profile(options.battery_profile)
    except (FileNotFoundError, ValueError) as exc:
        out(f"ERROR loading the battery profile: {type(exc).__name__}: {exc}")
        return 2
    thread = DiagnosticsControl(
        "inverter",
        shared,
        log,
        host=options.host,
        battery=s.SmaBatteryContext.from_profile(battery_profile),
        grid_power_key=options.grid_power_key,
        grid_power_max_age_s=options.grid_power_max_age_s,
        port=options.port,
        unit_id=options.unit_id,
        timeout=options.timeout_s,
        pv_limiter_settings=pv,
        poll_interval=s.CONTROL_INTERVAL_S,
        write_registers=options.write_registers,
    )

    def emit(section: str, data: Any, lines: list[str]) -> None:
        if options.as_json:
            out(json.dumps({"section": section, "data": _jsonable(data)}))
        else:
            out("\n".join(lines))

    config = configuration(options)
    emit("configuration", config, format_configuration(config))

    meter = None
    if options.meter_serial is not None:
        from akkudoktoreos.adapter.controllers.sma.smaenergymeter import (
            SmaEnergyMeterThread,
        )

        # The meter gets a diagnostic log of its own (capacity 0: disabled), the log of the
        # inverter thread only holds the events of the inverter.
        meter = SmaEnergyMeterThread(
            options.grid_power_key.split(":")[0],
            shared,
            ControlDiagnosticLog(),
            serial=options.meter_serial,
            interface_ip=options.meter_interface_ip,
        )
        meter.start()
    connected = False
    try:
        try:
            startup = startup_report(thread, log, options)
        except Exception as exc:
            out(f"ERROR connecting or reading the startup registers: {type(exc).__name__}: {exc}")
            return 2
        connected = True
        emit("startup", startup, format_startup(startup))

        last_extras = -1e18
        try:
            for number in itertools.count(1):
                now = time.monotonic()
                if now - last_extras >= options.extra_interval_s:
                    extras = read_extras(thread, log)
                    emit("extras", extras, format_extras(extras))
                    last_extras = now
                report = cycle_report(thread, shared, log, options)
                emit("cycle", report, format_cycle(number, report))
                if options.count and number >= options.count:
                    break
                sleep(options.interval_s)
        except KeyboardInterrupt:
            out("stopped")
        return 0
    finally:
        if meter is not None:
            meter.stop()
        if connected and options.write_registers:
            # Like the control thread on shutdown: do not leave an external setpoint or a
            # PV limit behind.
            try:
                thread._enter_safe_state()
            except Exception as exc:
                out(f"ERROR entering the safe state: {type(exc).__name__}: {exc}")
        thread._disconnect()


def parse_args(argv: Optional[Sequence[str]] = None) -> MonitorOptions:
    """Parse the command line of the monitor.

    Args:
        argv: The arguments without the program name, ``None`` for ``sys.argv[1:]``.

    Returns:
        The monitor options.
    """
    parser = argparse.ArgumentParser(
        description="Monitor of an SMA STP-SE: shows what the controller reads and would write "
        "(dry run) or writes (--write-registers).",
    )
    parser.add_argument("--host", required=True, help="host name or IP address of the inverter")
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--unit-id", type=int, default=s.SMA_UNIT_ID)
    parser.add_argument(
        "--timeout", type=float, default=3.0, dest="timeout_s", help="Modbus timeout in s"
    )
    parser.add_argument(
        "--write-registers",
        action="store_true",
        help="actually send register writes to the inverter (default: dry run). "
        "The safe state is written on exit.",
    )
    parser.add_argument(
        "--mode",
        default=BatteryOperationMode.SELF_CONSUMPTION.value,
        choices=[m.value for m in BatteryOperationMode],
        help="battery operation mode to show the writes of",
    )
    parser.add_argument(
        "--factor", type=float, default=1.0, help="operation mode factor 0.0 .. 1.0"
    )
    parser.add_argument(
        "--grid-power-key", default="meter:grid_net_w", help="shared data key of the grid power"
    )
    parser.add_argument(
        "--meter-serial", type=int, help="read the grid power from this SMA Energy Meter"
    )
    parser.add_argument("--meter-interface-ip", default=ANY_INTERFACE_IP)
    parser.add_argument(
        "--pv-rated-power-w",
        type=float,
        help="also read the PV limit registers and show the PV limiter",
    )
    parser.add_argument(
        "--interval", type=float, default=5.0, dest="interval_s", help="seconds between cycles"
    )
    parser.add_argument(
        "--count", type=int, default=0, help="number of cycles, 0: until interrupted"
    )
    parser.add_argument(
        "--extra-interval",
        type=float,
        default=30.0,
        dest="extra_interval_s",
        help="seconds between reads of the extra registers",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json", help="one JSON object per line"
    )
    parser.add_argument(
        "--battery-profile",
        default="generic-lfp",
        help="battery profile that limits the battery power: a profile name or a JSON file",
    )
    args = parser.parse_args(argv)
    if not 0.0 <= args.factor <= 1.0:
        parser.error("--factor must be in [0, 1]")
    return MonitorOptions(**{k: v for k, v in vars(args).items()})


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point. Returns the exit code."""
    return run_monitor(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
