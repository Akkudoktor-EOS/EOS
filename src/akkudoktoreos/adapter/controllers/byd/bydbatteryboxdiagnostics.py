r"""Diagnostics tool of a BYD Battery-Box Premium (HV): shows what the monitor reads.

Not used by EOS. It runs the real monitor code (``BydBatteryBoxThread``) against the live BMU
with an enabled ``ControlDiagnosticLog``: it connects, reads the device information and then runs
the monitor cycles (``_cycle()``) in the foreground. After every cycle it drains the diagnostic
log and prints the events:

- ``device_info``: serial number, model, firmware, towers and modules.
- ``status_read``: the status of the BMU, the error bits decoded, and the raw current register
  (to check the sign convention: during a known charge the published current must be negative).
- ``bms<n>_info``: per tower, including all cell voltages and temperatures and the decoded warning
  and error bits.
- ``event_log_entry``: new entries of the event log. The entries of the first page after the
  start are printed only with ``--event-log-history``.
- ``mailbox_error``, and ``modbus_write`` (the mailbox requests, the only writes).
- The Modbus timings after every cycle.

With ``--json`` every event, including the raw words of every Modbus read (``modbus_read``), is
printed as one JSON object per line, which also gives register images for tests.

``--read-only`` switches the mailbox requests off: no write at all, only device information and
status.

The BMU accepts only one client: close BE Connect Plus and stop the BYD controller of EOS first.

Usage::

    uv run python -m akkudoktoreos.adapter.controllers.byd.bydbatteryboxdiagnostics \
        --host 192.168.16.254 --interval 10 --count 6

    uv run python -m ... --host 192.168.178.7 --read-only --count 1
    uv run python -m ... --host 192.168.178.7 --json --event-log-history > byd.jsonl

``main()`` returns the exit code (0: ok, 2: connecting or reading the device information failed).
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from akkudoktoreos.adapter.controllers.byd.bydbatterybox import (
    BMS_ERROR_BITS,
    BMS_WARNING3_BITS,
    BMS_WARNING_BITS,
    BMU_ERROR_BITS,
    BYD_PORT,
    BYD_UNIT_ID,
    MODULE_CELLS,
    BydBatteryBoxThread,
    bit_names,
)
from akkudoktoreos.adapter.controllers.controlabc import (
    ControlDiagnosticEntry,
    ControlDiagnosticLog,
    ControlSharedDataStore,
)
from akkudoktoreos.utils.datetimeutil import to_datetime

DIAGNOSTIC_LOG_CAPACITY = 10_000


@dataclass(frozen=True, slots=True)
class DiagnosticsOptions:
    """Command line options of the diagnostics tool."""

    host: str
    port: int = BYD_PORT
    unit_id: int = BYD_UNIT_ID
    timeout_s: float = 5.0
    interval_s: float = 30.0
    bms_info_interval_s: float = 600.0
    event_log_interval_s: float = 600.0
    count: int = 0
    as_json: bool = False
    read_only: bool = False
    event_log_history: bool = False
    model: Optional[str] = None


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _bits(bits: int, names: Sequence[str]) -> str:
    if not bits:
        return "0x0000 (none)"
    return f"0x{bits:04X} ({', '.join(bit_names(bits, names))})"


def format_device_info(data: Mapping[str, Any]) -> list[str]:
    """Lines of a ``device_info`` event."""
    return ["Device information"] + [f"  {key:<18} {value}" for key, value in data.items()]


def format_status(data: Mapping[str, Any]) -> list[str]:
    """Lines of a ``status_read`` event."""
    return [
        "Status",
        f"  SoC {data['soc_percent']} %   SoH {data['soh_percent']} %",
        f"  battery {data['battery_voltage_v']} V   output {data['output_voltage_v']} V",
        f"  current {data['current_a']} A (raw {data['raw_current']})   "
        f"power {data['power_w']} W (discharge +)",
        f"  cell voltage {data['cell_voltage_min_v']} .. {data['cell_voltage_max_v']} V   "
        f"cell temperature {data['cell_temperature_min_c']} .. {data['cell_temperature_max_c']} °C"
        f"   BMU {data['bmu_temperature_c']} °C",
        f"  charged {data['charged_energy_kwh']} kWh   discharged "
        f"{data['discharged_energy_kwh']} kWh",
        f"  errors {_bits(data['bmu_error_bits'], BMU_ERROR_BITS)}",
    ]


def _chunks(values: Sequence[Any], size: int) -> list[Sequence[Any]]:
    if size <= 0:
        return [values] if values else []
    return [values[i : i + size] for i in range(0, len(values), size)]


def format_bms_info(data: Mapping[str, Any], modules: int) -> list[str]:
    """Lines of a ``bms_info`` event; cells and sensors are split evenly per module."""
    cells = list(data["cell_voltages_mv"])
    temperatures = list(data["sensor_temperatures_c"])
    lines = [
        f"BMS {data['bms_id']}",
        f"  SoC {data['soc_percent']} %   SoH {data['soh_percent']} %   current "
        f"{data['current_a']} A   battery {data['battery_voltage_v']} V   output "
        f"{data['output_voltage_v']} V",
        f"  cell voltage {data['cell_voltage_min_v']} V (#{data['cell_voltage_min_number']}) .. "
        f"{data['cell_voltage_max_v']} V (#{data['cell_voltage_max_number']})",
        f"  cell temperature {data['cell_temperature_min_c']} °C "
        f"(#{data['cell_temperature_min_number']}) .. {data['cell_temperature_max_c']} °C "
        f"(#{data['cell_temperature_max_number']})",
        f"  charged {data['charged_energy_kwh']} kWh   discharged "
        f"{data['discharged_energy_kwh']} kWh   balancing cells {data['balancing_cells']}",
        f"  warnings 1 {_bits(data['warning1_bits'], BMS_WARNING_BITS)}",
        f"  warnings 2 {_bits(data['warning2_bits'], BMS_WARNING_BITS)}",
        f"  warnings 3 {_bits(data['warning3_bits'], BMS_WARNING3_BITS)}",
        f"  errors     {_bits(data['error_bits'], BMS_ERROR_BITS)}",
    ]
    per_module_cells = len(cells) // modules if modules and cells else 0
    for number, chunk in enumerate(_chunks(cells, per_module_cells), start=1):
        lines.append(f"  module {number} cells mV: {' '.join(str(v) for v in chunk)}")
    per_module_sensors = len(temperatures) // modules if modules and temperatures else 0
    for number, chunk in enumerate(_chunks(temperatures, per_module_sensors), start=1):
        lines.append(f"  module {number} temperatures °C: {' '.join(str(v) for v in chunk)}")
    for number, flags in enumerate(data["balancing_flags"], start=1):
        lines.append(f"  module {number} balancing flags: 0x{flags:04X}")
    return lines


def format_entry(entry: ControlDiagnosticEntry, modules: int) -> list[str]:
    """Lines of one diagnostic event, empty for events that are not shown as text."""
    data = entry.data
    if entry.event == "device_info":
        return format_device_info(data)
    if entry.event == "status_read":
        return format_status(data)
    if entry.event.startswith("bms") and entry.event.endswith("_info"):
        return format_bms_info(data, modules)
    if entry.event == "event_log_entry":
        unit = "BMU" if data["unit"] == 0 else f"BMS {data['unit']}"
        return [
            f"Event {unit:<5} {data['event_date_time']}  0x{data['code']:02X} "
            f"{data['code_name']}  {data['data_hex']}"
        ]
    if entry.event == "mailbox_error":
        return [f"ERROR mailbox {data['mailbox']} unit {data['unit']}: {data['error']}"]
    if entry.event == "modbus_write":
        values = " ".join(f"0x{v:04X}" for v in data["values"])
        error = f"  ERROR {data['error']}" if data["error"] else ""
        return [f"write 0x{data['address']:04X} <- {values}{error}"]
    return []


def _is_hidden_history(entry: ControlDiagnosticEntry, options: DiagnosticsOptions) -> bool:
    return (
        entry.event == "event_log_entry"
        and bool(entry.data.get("initial"))
        and not options.event_log_history
    )


def _emit_entries(
    entries: Sequence[ControlDiagnosticEntry],
    options: DiagnosticsOptions,
    modules: int,
    out: Callable[[str], None],
) -> None:
    for entry in entries:
        if _is_hidden_history(entry, options):
            continue
        if options.as_json:
            out(
                json.dumps(
                    {
                        "date_time": to_datetime(entry.date_time, as_string=True),
                        "event": entry.event,
                        "data": _jsonable(entry.data),
                    }
                )
            )
            continue
        lines = format_entry(entry, modules)
        if lines:
            out("\n".join(lines))


def _emit_timings(
    thread: BydBatteryBoxThread, options: DiagnosticsOptions, out: Callable[[str], None]
) -> None:
    stats = thread.modbus_stats()
    if options.as_json:
        out(
            json.dumps(
                {
                    "event": "modbus_stats",
                    "data": {
                        op: {
                            "count": s.count,
                            "errors": s.errors,
                            "mean_ms": round(s.mean_ms, 1),
                            "max_ms": round(s.max_ms, 1),
                        }
                        for op, s in stats.items()
                    },
                }
            )
        )
        return
    out(
        "Modbus "
        + "   ".join(
            f"{op}: {s.count} ({s.errors} errors) mean {s.mean_ms:.0f} ms max {s.max_ms:.0f} ms"
            for op, s in stats.items()
        )
    )


def run_diagnostics(
    options: DiagnosticsOptions,
    *,
    out: Callable[[str], None] = print,
    thread: Optional[BydBatteryBoxThread] = None,
    sleep: Optional[Callable[[float], None]] = None,
) -> int:
    """Run the monitor in the foreground and print what it reads.

    Args:
        options: The options.
        out: Output of one line or block.
        thread: The monitor thread, for tests. None: created from the options.
        sleep: Wait between cycles, for tests. None: ``time.sleep``.

    Returns:
        0, or 2 if connecting or reading the device information failed.
    """
    sleep = sleep or time.sleep
    if thread is None:
        thread = BydBatteryBoxThread(
            "byd",
            ControlSharedDataStore(),
            ControlDiagnosticLog(DIAGNOSTIC_LOG_CAPACITY),
            host=options.host,
            port=options.port,
            unit_id=options.unit_id,
            timeout=options.timeout_s,
            status_interval_s=options.interval_s,
            bms_info_interval_s=options.bms_info_interval_s,
            event_log_interval_s=options.event_log_interval_s,
            mailbox_requests=not options.read_only,
            model=options.model,
        )
    log = thread.diagnostic_log
    try:
        try:
            thread._connect()
            thread._on_connected()
        except Exception as exc:  # noqa: BLE001 - report any failure of the first contact
            _emit_entries(log.drain(), options, 0, out)
            out(f"ERROR connecting or reading the device information: {type(exc).__name__}: {exc}")
            return 2
        device = thread.device_info
        modules = device.module_count if device is not None else 0
        _emit_entries(log.drain(), options, modules, out)
        status_reads = 0
        try:
            while True:
                wait = thread._cycle()
                entries = log.drain()
                status_reads += sum(1 for entry in entries if entry.event == "status_read")
                _emit_entries(entries, options, modules, out)
                if any(entry.event == "status_read" for entry in entries):
                    _emit_timings(thread, options, out)
                if options.count and status_reads >= options.count:
                    break
                sleep(wait)
        except KeyboardInterrupt:
            out("stopped")
        except Exception as exc:  # noqa: BLE001 - a lost connection ends the tool
            _emit_entries(log.drain(), options, modules, out)
            out(f"ERROR: {type(exc).__name__}: {exc}")
            return 2
        return 0
    finally:
        thread._disconnect()


def parse_args(argv: Optional[Sequence[str]] = None) -> DiagnosticsOptions:
    """Parse the command line.

    Args:
        argv: The arguments without the program name, ``None`` for ``sys.argv[1:]``.

    Returns:
        The options.
    """
    parser = argparse.ArgumentParser(
        description="Diagnostics of a BYD Battery-Box Premium (HV): shows what the EOS monitor "
        "reads. Close BE Connect Plus first, the BMU accepts only one client.",
    )
    parser.add_argument("--host", required=True, help="host name or IP address of the BMU")
    parser.add_argument("--port", type=int, default=BYD_PORT)
    parser.add_argument("--unit-id", type=int, default=BYD_UNIT_ID)
    parser.add_argument(
        "--timeout", type=float, default=5.0, dest="timeout_s", help="Modbus timeout in s"
    )
    parser.add_argument(
        "--interval", type=float, default=30.0, dest="interval_s", help="s between status reads"
    )
    parser.add_argument(
        "--bms-info-interval",
        type=float,
        default=600.0,
        dest="bms_info_interval_s",
        help="s between BMS info requests of a tower",
    )
    parser.add_argument(
        "--event-log-interval",
        type=float,
        default=600.0,
        dest="event_log_interval_s",
        help="s between event log requests of a unit",
    )
    parser.add_argument(
        "--count", type=int, default=0, help="number of status reads, 0: until interrupted"
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json", help="one JSON object per line"
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="no mailbox requests: no write at all, only device information and status",
    )
    parser.add_argument(
        "--event-log-history",
        action="store_true",
        help="also print the entries of the first event log page after the start",
    )
    parser.add_argument(
        "--model",
        choices=sorted(MODULE_CELLS),
        help="model of the towers, default: detected from the cell count",
    )
    args = parser.parse_args(argv)
    if args.interval_s <= 0 or args.bms_info_interval_s <= 0 or args.event_log_interval_s <= 0:
        parser.error("intervals must be > 0")
    return DiagnosticsOptions(**vars(args))


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point. Returns the exit code."""
    return run_diagnostics(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
