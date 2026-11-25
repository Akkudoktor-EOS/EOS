"""BYD Battery-Box Premium (HVS, HVM, HVL): monitor of the battery management unit (BMU).

Unofficial protocol
-------------------
BYD publishes no register map. The registers were worked out by the community from the installer
software (``sarnau/BYD-Battery-Box-Infos``) and are used by the Home Assistant integration
``redpomodoro/byd_battery_box``. Every value is unchecked.

Transport: Modbus RTU frames (with CRC) over a TCP connection, port 8080, unit id 1. The BMU
accepts only one client: BE Connect Plus cannot connect while the monitor is connected.

What is read
------------
- Device information (``0x0000–0x0013``) once after every connect: serial number, model, firmware
  versions, number of towers and modules. Only HV towers are supported (serial prefix ``P03`` or
  ``E0P3``); anything else fails the connect with a clear error.
- Status of the BMU (``0x0500–0x0514``) every ``status_interval_s``.
- BMS information per tower every ``bms_info_interval_s``, through the mailbox at ``0x0550``.
- The newest page of the event log of the BMU (unit 0) and of every tower every
  ``event_log_interval_s``, through the mailbox at ``0x05A0``. Only while the diagnostic log is
  enabled: the entries go nowhere else.

Mailbox requests are the only writes (ADR 0013). ``_request_mailbox()`` accepts exactly
``0x0550 ← [bms_id, 0x8100]`` and ``0x05A0 ← [unit, 0x8100]``, with the unit checked against the
tower count, and raises otherwise. A mailbox that does not report ready (``0x8801``) within the
mailbox timeout makes that unit's readings stale and records ``mailbox_error``; it is not a lost
connection.

Sign convention: current and power are positive when the battery discharges and negative when it
charges.

Diagnostic events
-----------------
``device_info``, ``status_read``, ``bms<n>_info`` per tower n (with all cell voltages and
temperature sensor values),
``event_log_entry`` (``initial`` is True for the entries of the first page after the start),
``mailbox_error``, plus ``modbus_read``/``modbus_write`` of the Modbus base.
"""

import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, ClassVar, Final, Literal, Optional, Sequence, cast

from loguru import logger
from pydantic import Field
from pymodbus import FramerType

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
from akkudoktoreos.adapter.controllers.controlmodbus import ModbusTcpMonitorThreadBase
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime

BYD_PORT: Final = 8080
BYD_UNIT_ID: Final = 1

# ---------------------------------------------------------------------------
# Registers
# ---------------------------------------------------------------------------

DEVICE_INFO_ADDRESS: Final = 0x0000
DEVICE_INFO_COUNT: Final = 0x14  # 0x0000–0x0013
STATUS_ADDRESS: Final = 0x0500
STATUS_COUNT: Final = 21  # 0x0500–0x0514

MAILBOX_REQUEST_COMMAND: Final = 0x8100
MAILBOX_READY: Final = 0x8801
MAILBOX_PACKET_COUNT: Final = 65  # registers per packet read, the first one is a length word

# The current register (0x0504) is multiplied by this sign to get discharge (+), charge (-).
# Checked on an HVS tower (2026-11-16): the raw value was -96 (-9.6 A) while charging.
RAW_CURRENT_SIGN: Final = 1

# Highest number of towers of an HV system.
MAX_HV_TOWERS: Final = 3
# Cells the BMS info mailbox reports, at most (without the undocumented DEBUG switch).
MAX_REPORTED_CELLS: Final = 128
# Temperature sensors the BMS info mailbox reports, at most (one byte each).
MAX_REPORTED_TEMPERATURES: Final = 64


@dataclass(frozen=True, slots=True)
class BydMailbox:
    """A request-and-response area of the BMU.

    A request writes ``[unit, 0x8100]`` to ``request_address``. When ``ready_address`` reads
    ``0x8801`` the answer is read in ``packets`` packets of 65 registers from ``data_address``.
    """

    name: str
    request_address: int
    ready_address: int
    data_address: int
    packets: int
    first_unit: int  # 0: the BMU itself is a unit, 1: towers only


BMS_INFO_MAILBOX: Final = BydMailbox("bms_info", 0x0550, 0x0551, 0x0558, 4, first_unit=1)
EVENT_LOG_MAILBOX: Final = BydMailbox("event_log", 0x05A0, 0x05A1, 0x05A8, 5, first_unit=0)
_MAILBOXES: Final = (BMS_INFO_MAILBOX, EVENT_LOG_MAILBOX)

# ---------------------------------------------------------------------------
# Tables (community knowledge, unofficial)
# ---------------------------------------------------------------------------

# Low byte of 0x0011 for HV towers.
HV_MODELS: Final = {0: "HVL", 1: "HVM", 2: "HVS"}
# Cells and temperature sensors per module, 0: unknown.
MODULE_CELLS: Final = {"HVL": 0, "HVM": 16, "HVS": 32}
MODULE_TEMPERATURE_SENSORS: Final = {"HVL": 0, "HVM": 8, "HVS": 12}
HV_SERIAL_PREFIXES: Final = ("P03", "E0P3")
# Model by counted cells per module. HVL cannot be told apart from HVM by the count.
MODELS_BY_CELLS: Final = {32: "HVS", 16: "HVM"}
# Register 0x0011 is not reliable: an HVS tower was seen reporting 0 (HVL).
MODEL_SOURCE_SETTING: Final = "setting"
MODEL_SOURCE_CELL_COUNT: Final = "cell count"
MODEL_SOURCE_REGISTER: Final = "register 0x0011"

APPLICATIONS: Final = ("Off Grid", "On Grid", "Backup")
PHASES: Final = ("Single", "Three")

BMU_ERROR_BITS: Final = (
    "High temperature charging (cells)",
    "Low temperature charging (cells)",
    "Discharging overcurrent (cells)",
    "Charging overcurrent (cells)",
    "Main circuit failure",
    "Short circuit",
    "Cell imbalance",
    "Current sensor error",
    "Battery overvoltage",
    "Battery undervoltage",
    "Cell overvoltage",
    "Cell undervoltage",
    "Voltage sensor failure",
    "Temperature sensor failure",
    "High temperature discharging (cells)",
    "Low temperature discharging (cells)",
)
BMS_ERROR_BITS: Final = (
    "Cells voltage sensor failure",
    "Temperature sensor failure",
    "BIC communication failure",
    "Pack voltage sensor failure",
    "Current sensor failure",
    "Charging MOS failure",
    "Discharging MOS failure",
    "Precharging MOS failure",
    "Main relay failure",
    "Precharging failed",
    "Heating device failure",
    "Radiator failure",
    "BIC balance failure",
    "Cells failure",
    "PCB temperature sensor failure",
    "Functional safety failure",
)
BMS_WARNING_BITS: Final = (
    "Battery overvoltage",
    "Battery undervoltage",
    "Cells overvoltage",
    "Cells undervoltage",
    "Cells imbalance",
    "Charging high temperature (cells)",
    "Charging low temperature (cells)",
    "Discharging high temperature (cells)",
    "Discharging low temperature (cells)",
    "Charging overcurrent (cells)",
    "Discharging overcurrent (cells)",
    "Charging overcurrent (hardware)",
    "Short circuit",
    "Inverse connection",
    "Interlock switch abnormal",
    "Air switch abnormal",
)
BMS_WARNING3_BITS: Final = (
    "Battery overvoltage",
    "Battery undervoltage",
    "Cell overvoltage",
    "Cell undervoltage",
    "Voltage sensor failure",
    "Temperature sensor failure",
    "High temperature discharging (cells)",
    "Low temperature discharging (cells)",
    "High temperature charging (cells)",
    "Low temperature charging (cells)",
    "Overcurrent discharging",
    "Overcurrent charging",
    "Main circuit failure",
    "Short circuit alarm",
    "Cells imbalance",
    "Current sensor failure",
)

# sarnau/BYD-Battery-Box-Infos, completed from redpomodoro/byd_battery_box (0x16–0x1D, 0x22,
# 0x23, 0x28, 0x29).
BMU_EVENT_CODES: Final = {
    0x00: "Power ON",
    0x01: "Power OFF",
    0x02: "Events record",
    0x04: "Start charging",
    0x05: "Stop charging",
    0x06: "Start discharging",
    0x07: "Stop discharging",
    0x16: "Firmware update started",
    0x17: "Firmware update finished",
    0x18: "Firmware update fails",
    0x19: "SN code was changed",
    0x1A: "Current calibration",
    0x1B: "Battery voltage calibration",
    0x1C: "Pack voltage calibration",
    0x1D: "SOC/SOH calibration",
    0x20: "System status changed",
    0x21: "Erase BMS firmware",
    0x22: "BMS update start",
    0x23: "BMS update done",
    0x24: "Functional safety info",
    0x26: "SOP info",
    0x27: "BCU hardware failure",
    0x28: "BMS firmware list",
    0x29: "MCU list of BMS",
    0x65: "Firmware start to update",
    0x66: "Firmware update successful",
    0x67: "Firmware update failure",
    0x68: "Firmware jump into other section",
    0x69: "Parameters table update",
    0x6A: "SN code changed",
    0x6F: "DateTime calibration",
    0x70: "BMS disconnected with BMU",
    0x71: "BMU F/W reset",
    0x72: "BMU watchdog reset",
    0x73: "PreCharge failed",
    0x74: "Address registration failed",
    0x75: "Parameters table load failed",
    0x76: "System timing log",
    0x78: "Parameters table updating done",
}
BMS_EVENT_CODES: Final = {
    0x00: "Power ON",
    0x01: "Power OFF",
    0x02: "Events record",
    0x03: "Timing record",
    0x04: "Start charging",
    0x05: "Stop charging",
    0x06: "Start discharging",
    0x07: "Stop discharging",
    0x08: "SOC calibration rough",
    0x09: "SOC calibration fine",
    0x0A: "SOC calibration stop",
    0x0B: "CAN communication failed",
    0x0C: "Serial communication failed",
    0x0D: "Receive PreCharge command",
    0x0E: "PreCharge successful",
    0x0F: "PreCharge failure",
    0x10: "Start end SOC calibration",
    0x11: "Start balancing",
    0x12: "Stop balancing",
    0x13: "Address registered",
    0x14: "System functional safety fault",
    0x15: "Events additional info",
    0x65: "Start firmware update",
    0x66: "Firmware update finish",
    0x67: "Firmware update fails",
    0x68: "Firmware jump into other section",
    0x69: "Parameters table update",
    0x6A: "SN code was changed",
    0x6B: "Current calibration",
    0x6C: "Battery voltage calibration",
    0x6D: "PackVoltage calibration",
    0x6E: "SOC/SOH calibration",
    0x6F: "DateTime calibration",
}

# ---------------------------------------------------------------------------
# Shared data key suffixes (without "<device_id>:"). The unit is part of the name.
# ---------------------------------------------------------------------------

STATUS_KEYS: Final = (
    "soc_factor",
    "soh_percent",
    "battery_voltage_v",
    "output_voltage_v",
    "current_a",
    "power_w",
    "cell_voltage_max_v",
    "cell_voltage_min_v",
    "cell_temperature_max_c",
    "cell_temperature_min_c",
    "bmu_temperature_c",
    "charged_energy_kwh",
    "discharged_energy_kwh",
    "bmu_error_bits",
)
DEVICE_KEYS: Final = ("tower_count", "module_count")
TOWER_KEY_SUFFIXES: Final = (
    "soc_percent",
    "cell_voltage_max_v",
    "cell_voltage_min_v",
    "cell_temperature_max_c",
    "cell_temperature_min_c",
    "warning1_bits",
    "warning2_bits",
    "warning3_bits",
    "error_bits",
    "balancing_cells",
)


def tower_keys(bms_id: int) -> tuple[str, ...]:
    """Shared data key suffixes of one tower, e.g. ``bms1_soc_percent``."""
    return tuple(f"bms{bms_id}_{suffix}" for suffix in TOWER_KEY_SUFFIXES)


PROVIDED_KEYS: Final = (
    STATUS_KEYS
    + DEVICE_KEYS
    + tuple(key for bms_id in range(1, MAX_HV_TOWERS + 1) for key in tower_keys(bms_id))
)

# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


class BydProtocolError(ValueError):
    """The BMU answered with data that does not fit the known protocol."""


class BydMailboxTimeout(TimeoutError):
    """A mailbox did not report ready within the mailbox timeout."""


def _int16(word: int) -> int:
    return word - 0x10000 if word & 0x8000 else word


def _int8(byte: int) -> int:
    return byte - 0x100 if byte & 0x80 else byte


def _version(word: int) -> str:
    return f"{word >> 8}.{word & 0xFF}"


def _uint32_low_word_first(low: int, high: int) -> int:
    return (high << 16) | low


def _bytes(words: Sequence[int]) -> bytes:
    return b"".join(bytes((word >> 8, word & 0xFF)) for word in words)


def bit_names(bits: int, names: Sequence[str]) -> tuple[str, ...]:
    """Names of the set bits of a 16 bit mask, bit 0 first."""
    return tuple(name for bit, name in enumerate(names) if bits & (1 << bit))


@dataclass(frozen=True, slots=True)
class BydDeviceInfo:
    """Hardware information and configuration of the BMU."""

    serial: str
    model: str  # HVL, HVM, HVS
    model_source: str  # MODEL_SOURCE_SETTING, MODEL_SOURCE_CELL_COUNT or MODEL_SOURCE_REGISTER
    register_model_id: int  # low byte of 0x0011, as read
    counted_cells_per_module: int  # from the status, 0 if it could not be counted
    bmu_firmware_a: str
    bmu_firmware_b: str
    bms_firmware: str
    bmu_working_area: int
    bms_working_area: int
    tower_count: int
    module_count: int
    inverter_id: int
    application: str
    phase: str

    @property
    def cells_per_module(self) -> int:
        """Cells per module, 0 if unknown (HVL)."""
        return MODULE_CELLS[self.model]

    @property
    def temperature_sensors_per_module(self) -> int:
        """Temperature sensors per module, 0 if unknown (HVL)."""
        return MODULE_TEMPERATURE_SENSORS[self.model]


def count_cells_per_module(status: "BydStatus", module_count: int) -> int:
    """Cells per module from the battery voltage and the mean cell voltage, 0 if not countable.

    The cells of all modules of a tower are in series: battery voltage / mean cell voltage is
    the number of cells, e.g. 435.2 V / 3.40 V = 128 cells, 32 per module with 4 modules.
    """
    mean_cell_v = (status.cell_voltage_max_v + status.cell_voltage_min_v) / 2.0
    if mean_cell_v <= 0.0 or module_count < 1 or status.battery_voltage_v <= 0.0:
        return 0
    cells = round(status.battery_voltage_v / mean_cell_v)
    return cells // module_count if cells % module_count == 0 else 0


def decode_device_info(
    words: Sequence[int],
    *,
    counted_cells_per_module: int = 0,
    model_override: Optional[str] = None,
) -> BydDeviceInfo:
    """Decode the registers ``0x0000–0x0013`` and resolve the model.

    The model is taken from ``model_override`` if given, else from the counted cells per module
    (32: HVS, 16: HVM), else from the low byte of ``0x0011``.

    Args:
        words: The registers ``0x0000–0x0013``.
        counted_cells_per_module: Result of ``count_cells_per_module()``, 0: not counted.
        model_override: Model set by the user (HVS, HVM, HVL), None: detect.

    Raises:
        BydProtocolError: Too few registers, not an HV tower or no model could be resolved.
    """
    if len(words) < DEVICE_INFO_COUNT:
        raise BydProtocolError(f"device info: {len(words)} registers, {DEVICE_INFO_COUNT} needed")
    serial = _bytes(words[0:10]).decode("ascii", errors="replace").strip("\x00 ")
    if not serial.startswith(HV_SERIAL_PREFIXES):
        raise BydProtocolError(
            f"serial number {serial[:4]!r}... is not an HV Battery-Box Premium "
            f"(expected prefix {' or '.join(HV_SERIAL_PREFIXES)}); only HVS, HVM and HVL are supported"
        )
    model_id = words[0x11] & 0xFF
    counted_model = MODELS_BY_CELLS.get(counted_cells_per_module)
    if model_override is not None:
        if model_override not in MODULE_CELLS:
            raise BydProtocolError(f"unknown model {model_override!r}, expected HVS, HVM or HVL")
        model, source = model_override, MODEL_SOURCE_SETTING
    elif counted_model is not None:
        model, source = counted_model, MODEL_SOURCE_CELL_COUNT
    elif model_id in HV_MODELS:
        model, source = HV_MODELS[model_id], MODEL_SOURCE_REGISTER
    else:
        raise BydProtocolError(
            f"unknown HV battery type {model_id} in register 0x0011 and the cells per module "
            f"could not be counted ({counted_cells_per_module}); set the model"
        )
    towers = (words[0x10] >> 4) & 0x0F
    modules = words[0x10] & 0x0F
    if not 1 <= towers <= MAX_HV_TOWERS:
        raise BydProtocolError(f"unexpected tower count {towers} in register 0x0010")
    if modules < 1:
        raise BydProtocolError("no modules reported in register 0x0010")
    application_id = words[0x11] >> 8
    phase_id = words[0x12] >> 8
    return BydDeviceInfo(
        serial=serial,
        model=model,
        model_source=source,
        register_model_id=model_id,
        counted_cells_per_module=counted_cells_per_module,
        bmu_firmware_a=_version(words[0x0C]),
        bmu_firmware_b=_version(words[0x0D]),
        bms_firmware=_version(words[0x0E]),
        bmu_working_area=words[0x0F] >> 8,
        bms_working_area=words[0x0F] & 0xFF,
        tower_count=towers,
        module_count=modules,
        inverter_id=words[0x10] >> 8,
        application=(
            APPLICATIONS[application_id]
            if application_id < len(APPLICATIONS)
            else str(application_id)
        ),
        phase=PHASES[phase_id] if phase_id < len(PHASES) else str(phase_id),
    )


@dataclass(frozen=True, slots=True)
class BydStatus:
    """Status of the BMU (``0x0500–0x0514``). Current and power: discharge (+)."""

    soc_percent: int
    soh_percent: int
    battery_voltage_v: float
    output_voltage_v: float
    current_a: float
    power_w: float
    cell_voltage_max_v: float
    cell_voltage_min_v: float
    cell_temperature_max_c: int
    cell_temperature_min_c: int
    bmu_temperature_c: int
    charged_energy_kwh: float
    discharged_energy_kwh: float
    bmu_error_bits: int
    raw_current: int  # register 0x0504 as signed 16 bit value, before the sign convention

    def shared_values(self) -> dict[str, float]:
        """Values by shared data key suffix."""
        values: dict[str, float] = {
            key: getattr(self, key) for key in STATUS_KEYS if key != "soc_factor"
        }
        values["soc_factor"] = self.soc_percent / 100.0
        return values


def decode_status(words: Sequence[int]) -> BydStatus:
    """Decode the registers ``0x0500–0x0514``.

    Raises:
        BydProtocolError: Too few registers.
    """
    if len(words) < STATUS_COUNT:
        raise BydProtocolError(f"status: {len(words)} registers, {STATUS_COUNT} needed")
    raw_current = _int16(words[0x04])
    current_a = round(RAW_CURRENT_SIGN * raw_current * 0.1, 1)
    output_voltage_v = round(words[0x10] * 0.01, 2)
    return BydStatus(
        soc_percent=words[0x00],
        soh_percent=words[0x03],
        battery_voltage_v=round(words[0x05] * 0.01, 2),
        output_voltage_v=output_voltage_v,
        current_a=current_a,
        power_w=round(current_a * output_voltage_v, 1),
        cell_voltage_max_v=round(words[0x01] * 0.01, 2),
        cell_voltage_min_v=round(words[0x02] * 0.01, 2),
        cell_temperature_max_c=_int16(words[0x06]),
        cell_temperature_min_c=_int16(words[0x07]),
        bmu_temperature_c=_int16(words[0x08]),
        charged_energy_kwh=round(_uint32_low_word_first(words[0x11], words[0x12]) * 0.1, 1),
        discharged_energy_kwh=round(_uint32_low_word_first(words[0x13], words[0x14]) * 0.1, 1),
        bmu_error_bits=words[0x0D],
        raw_current=raw_current,
    )


@dataclass(frozen=True, slots=True)
class BydBmsInfo:
    """Information of one tower from the BMS info mailbox. Cells 1..128 at most."""

    bms_id: int
    soc_percent: float
    soh_percent: int
    battery_voltage_v: float
    output_voltage_v: float
    current_a: float
    cell_voltage_max_v: float
    cell_voltage_min_v: float
    cell_voltage_max_number: int
    cell_voltage_min_number: int
    cell_temperature_max_c: int
    cell_temperature_min_c: int
    cell_temperature_max_number: int
    cell_temperature_min_number: int
    charged_energy_kwh: float
    discharged_energy_kwh: float
    warning1_bits: int
    warning2_bits: int
    warning3_bits: int
    error_bits: int
    balancing_flags: tuple[int, ...]  # one bit mask per module
    cell_voltages_mv: tuple[int, ...]
    sensor_temperatures_c: tuple[int, ...]

    @property
    def balancing_cells(self) -> int:
        """Number of cells that are balancing."""
        return sum(bin(flags).count("1") for flags in self.balancing_flags)

    def shared_values(self) -> dict[str, float]:
        """Values by shared data key suffix, e.g. ``bms1_soc_percent``."""
        return {
            f"bms{self.bms_id}_{suffix}": (
                self.balancing_cells if suffix == "balancing_cells" else getattr(self, suffix)
            )
            for suffix in TOWER_KEY_SUFFIXES
        }


def mailbox_payload(packets: Sequence[Sequence[int]]) -> list[int]:
    """Join the packets of a mailbox answer, without the length word of each packet."""
    payload: list[int] = []
    for packet in packets:
        if len(packet) != MAILBOX_PACKET_COUNT:
            raise BydProtocolError(
                f"mailbox packet of {len(packet)} registers, {MAILBOX_PACKET_COUNT} expected"
            )
        payload.extend(packet[1:])
    return payload


def decode_bms_info(bms_id: int, payload: Sequence[int], device: BydDeviceInfo) -> BydBmsInfo:
    """Decode the payload of a BMS info answer (4 packets without length words, 256 registers).

    The cells and temperature sensors are cut to the number the device has, as far as the
    mailbox reports them (128 cells, 64 sensors). For HVL the number is unknown: no cells.

    Raises:
        BydProtocolError: Wrong payload length or an implausible cell voltage.
    """
    expected = BMS_INFO_MAILBOX.packets * (MAILBOX_PACKET_COUNT - 1)
    if len(payload) != expected:
        raise BydProtocolError(f"BMS info: {len(payload)} registers, {expected} expected")
    p = payload
    if _int16(p[0]) > 5000:
        raise BydProtocolError(f"BMS {bms_id}: implausible max cell voltage {p[0]} mV")
    cells = min(device.module_count * device.cells_per_module, MAX_REPORTED_CELLS)
    sensors = min(
        device.module_count * device.temperature_sensors_per_module, MAX_REPORTED_TEMPERATURES
    )
    temperature_bytes = _bytes(p[177:209])
    modules = min(device.module_count, 8)
    return BydBmsInfo(
        bms_id=bms_id,
        cell_voltage_max_v=round(_int16(p[0]) * 0.001, 3),
        cell_voltage_min_v=round(_int16(p[1]) * 0.001, 3),
        cell_voltage_max_number=p[2] >> 8,
        cell_voltage_min_number=p[2] & 0xFF,
        cell_temperature_max_c=_int16(p[3]),
        cell_temperature_min_c=_int16(p[4]),
        cell_temperature_max_number=p[5] >> 8,
        cell_temperature_min_number=p[5] & 0xFF,
        balancing_flags=tuple(p[6 : 6 + modules]),
        charged_energy_kwh=round(_uint32_low_word_first(p[14], p[15]) * 0.001, 3),
        discharged_energy_kwh=round(_uint32_low_word_first(p[16], p[17]) * 0.001, 3),
        battery_voltage_v=round(_int16(p[20]) * 0.1, 1),
        output_voltage_v=round(_int16(p[23]) * 0.1, 1),
        soc_percent=round(_int16(p[24]) * 0.1, 1),
        soh_percent=_int16(p[25]),
        current_a=round(RAW_CURRENT_SIGN * _int16(p[26]) * 0.1, 1),
        warning1_bits=p[27],
        warning2_bits=p[28],
        warning3_bits=p[29],
        error_bits=p[47],
        cell_voltages_mv=tuple(_int16(word) for word in p[48 : 48 + cells]),
        sensor_temperatures_c=tuple(_int8(byte) for byte in temperature_bytes[:sensors]),
    )


@dataclass(frozen=True, slots=True)
class BydEventLogEntry:
    """One entry of the event log of the BMU (unit 0) or of a tower (unit n)."""

    unit: int
    date_time: DateTime  # BMU clock, read as local time of the configured time zone
    code: int
    code_name: str
    data_hex: str
    raw_time: tuple[int, int, int, int, int, int]  # year (+2000), month, day, hour, min, s

    @property
    def identity(self) -> tuple[tuple[int, ...], int, str]:
        """What tells entries apart: the raw time, the code and the data."""
        return (self.raw_time, self.code, self.data_hex)


ENTRY_BYTES: Final = 30
ENTRIES_PER_PAGE: Final = 20


def decode_event_log(
    unit: int, payload: Sequence[int], timezone: Optional[str] = None
) -> list[BydEventLogEntry]:
    """Decode the payload of an event log answer (5 packets without length words).

    Entries come newest first. Decoding stops at the first empty entry. Entries with an
    impossible time are skipped.

    Args:
        unit: 0 for the BMU, n for tower n.
        payload: 320 registers, 20 entries of 30 bytes.
        timezone: Time zone the BMU clock is read in, None: local time zone.

    Raises:
        BydProtocolError: Wrong payload length.
    """
    expected = EVENT_LOG_MAILBOX.packets * (MAILBOX_PACKET_COUNT - 1)
    if len(payload) != expected:
        raise BydProtocolError(f"event log: {len(payload)} registers, {expected} expected")
    raw = _bytes(payload)
    codes = BMU_EVENT_CODES if unit == 0 else BMS_EVENT_CODES
    entries: list[BydEventLogEntry] = []
    for index in range(ENTRIES_PER_PAGE):
        record = raw[index * ENTRY_BYTES : (index + 1) * ENTRY_BYTES]
        code, year, month, day, hour, minute, second = record[:7]
        if not any((code, year, month, day)):
            break
        raw_time = (year, month, day, hour, minute, second)
        try:
            date_time = to_datetime(
                f"{2000 + year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}",
                in_timezone=timezone,
            )
        except Exception:  # noqa: BLE001 - impossible date from the device, skip the entry
            logger.debug("BYD event log unit {}: invalid time {}", unit, raw_time)
            continue
        entries.append(
            BydEventLogEntry(
                unit=unit,
                date_time=date_time,
                code=code,
                code_name=codes.get(code, f"Unknown 0x{code:02X}"),
                data_hex=record[7:].hex(),
                raw_time=raw_time,
            )
        )
    return entries


# ---------------------------------------------------------------------------
# Monitor thread
# ---------------------------------------------------------------------------

# Polling of the mailbox ready register.
_MAILBOX_FIRST_POLL_S: Final = 0.2
_MAILBOX_POLL_S: Final = 0.2
# Lower bound of the wait between cycles.
_MIN_CYCLE_WAIT_S: Final = 0.1


class BydBatteryBoxThread(ModbusTcpMonitorThreadBase):
    """Monitor thread of one BYD Battery-Box Premium (HV) BMU.

    Publishes the readings into the shared data store under ``"<device_id>:<key>"``, keys
    ``PROVIDED_KEYS``. Per tower keys are published only for towers that exist.

    The read steps (``read_device_info()``, ``read_status()``, ``read_bms_info()``,
    ``read_event_log()``) are public for the diagnostics tool; in EOS only the thread calls them.
    """

    def __init__(
        self,
        device_id: str,
        shared_data: ControlSharedDataStore,
        diagnostic_log: ControlDiagnosticLog,
        *,
        host: str,
        port: int = BYD_PORT,
        unit_id: int = BYD_UNIT_ID,
        timeout: float = 5.0,
        status_interval_s: float = 30.0,
        bms_info_interval_s: float = 600.0,
        event_log_interval_s: float = 600.0,
        mailbox_timeout_s: float = 5.0,
        mailbox_requests: bool = True,
        model: Optional[str] = None,
        timezone: Optional[str] = None,
        monotonic: Callable[[], float] = time.monotonic,
        **kwargs: Any,
    ) -> None:
        """Initialize the monitor.

        Args:
            device_id: Id of the device, prefix of its shared data keys.
            shared_data: Store for cross-device data.
            diagnostic_log: Diagnostic log of the controller.
            host: Host name or IP address of the BMU.
            port: TCP port of the BMU.
            unit_id: Modbus unit id of the BMU.
            timeout: Timeout of one Modbus transaction in seconds.
            status_interval_s: Seconds between status reads.
            bms_info_interval_s: Seconds between BMS info requests of a tower.
            event_log_interval_s: Seconds between event log requests of a unit.
            mailbox_timeout_s: Seconds to wait for a mailbox to report ready.
            mailbox_requests: False: no mailbox requests, i.e. no writes at all.
            model: Model of the towers (HVS, HVM, HVL), None: detect it.
            timezone: Time zone the BMU clock is read in, None: local time zone.
            monotonic: Clock of the intervals, for tests.
            **kwargs: Passed on to the thread base class.
        """
        super().__init__(
            device_id,
            shared_data,
            diagnostic_log,
            host=host,
            port=port,
            unit_id=unit_id,
            timeout=timeout,
            framer=FramerType.RTU,
            **kwargs,
        )
        self._prefix = f"{device_id}:"
        self.status_interval_s = status_interval_s
        self.bms_info_interval_s = bms_info_interval_s
        self.event_log_interval_s = event_log_interval_s
        self.mailbox_timeout_s = mailbox_timeout_s
        self.mailbox_requests = mailbox_requests
        self.model = model
        self._timezone = timezone
        self._monotonic = monotonic

        # thread-private state
        self.device_info: Optional[BydDeviceInfo] = None
        self._next_status = 0.0
        self._next_bms_info: dict[int, float] = {}
        self._next_event_log: dict[int, float] = {}
        self._last_event: dict[int, tuple[tuple[int, ...], int, str]] = {}
        self._reported_bits: dict[str, int] = {}
        self._intervals_changed = threading.Event()

    # ---------------- thread-safe API (event loop side) ----------------
    def set_intervals(
        self, *, status_interval_s: float, bms_info_interval_s: float, event_log_interval_s: float
    ) -> None:
        """Change the read intervals of the running thread.

        A shorter interval applies right away, a longer one after the next read.

        Args:
            status_interval_s: Seconds between status reads.
            bms_info_interval_s: Seconds between BMS info requests of a tower.
            event_log_interval_s: Seconds between event log requests of a unit.
        """
        self.status_interval_s = status_interval_s
        self.bms_info_interval_s = bms_info_interval_s
        self.event_log_interval_s = event_log_interval_s
        self._intervals_changed.set()
        self._wakeup.set()

    # ---------------- hooks ----------------
    def published_keys(self) -> tuple[str, ...]:
        """Return the fully qualified shared data keys this thread may publish."""
        return tuple(self._prefix + key for key in PROVIDED_KEYS)

    def _on_connected(self) -> None:
        self.read_device_info()
        now = self._monotonic()
        self._next_status = now
        self._next_bms_info = dict.fromkeys(self._towers(), now)
        self._next_event_log = dict.fromkeys(range(0, len(self._towers()) + 1), now)

    def _read_status(self) -> dict[str, Any]:
        return asdict(self.read_status())

    def _cycle(self) -> float:
        """Run the reads that are due, return the seconds to the next one."""
        now = self._monotonic()
        if self._intervals_changed.is_set():
            self._intervals_changed.clear()
            self._next_status = min(self._next_status, now + self.status_interval_s)
            for bms_id, due in self._next_bms_info.items():
                self._next_bms_info[bms_id] = min(due, now + self.bms_info_interval_s)
            for unit, due in self._next_event_log.items():
                self._next_event_log[unit] = min(due, now + self.event_log_interval_s)
        if now >= self._next_status:
            self._publish(data=self._read_status())
            self._next_status = now + self.status_interval_s
        dues = [self._next_status]
        if self.mailbox_requests:
            for bms_id, due in self._next_bms_info.items():
                if now >= due:
                    self._try_mailbox(BMS_INFO_MAILBOX, bms_id, self.read_bms_info)
                    self._next_bms_info[bms_id] = self._monotonic() + self.bms_info_interval_s
            dues.extend(self._next_bms_info.values())
            if self.diagnostic_log.enabled:
                for unit, due in self._next_event_log.items():
                    if now >= due:
                        self._try_mailbox(EVENT_LOG_MAILBOX, unit, self.read_event_log)
                        self._next_event_log[unit] = self._monotonic() + self.event_log_interval_s
                dues.extend(self._next_event_log.values())
        return max(_MIN_CYCLE_WAIT_S, min(dues) - self._monotonic())

    # ---------------- read steps ----------------
    def read_device_info(self) -> BydDeviceInfo:
        """Read the device information, resolve the model, publish the tower and module count.

        The status is read once (not published) to count the cells per module.
        """
        words = self._read_holding_registers(DEVICE_INFO_ADDRESS, DEVICE_INFO_COUNT)
        status = decode_status(self._read_holding_registers(STATUS_ADDRESS, STATUS_COUNT))
        modules = words[0x10] & 0x0F if len(words) > 0x10 else 0
        info = decode_device_info(
            words,
            counted_cells_per_module=count_cells_per_module(status, modules),
            model_override=self.model,
        )
        register_model = HV_MODELS.get(info.register_model_id)
        if info.model_source != MODEL_SOURCE_REGISTER and register_model != info.model:
            logger.warning(
                "{}: model {} from the {}, register 0x0011 says {} ({})",
                self.device_id,
                info.model,
                info.model_source,
                register_model or "unknown",
                info.register_model_id,
            )
        self.device_info = info
        self._record_diagnostic("device_info", **asdict(info))
        self.shared_data.publish_sync(
            {
                self._prefix + "tower_count": info.tower_count,
                self._prefix + "module_count": info.module_count,
            }
        )
        return info

    def read_status(self) -> BydStatus:
        """Read the status of the BMU and publish it."""
        status = decode_status(self._read_holding_registers(STATUS_ADDRESS, STATUS_COUNT))
        self._record_diagnostic("status_read", **asdict(status))
        self.shared_data.publish_sync(
            {self._prefix + key: value for key, value in status.shared_values().items()}
        )
        self._report_bits("bmu_error_bits", status.bmu_error_bits, BMU_ERROR_BITS)
        return status

    def read_bms_info(self, bms_id: int) -> BydBmsInfo:
        """Request and read the BMS information of one tower and publish it.

        Raises:
            BydMailboxTimeout: The mailbox did not report ready in time.
        """
        device = self._require_device_info()
        payload = self._request_mailbox(BMS_INFO_MAILBOX, bms_id)
        info = decode_bms_info(bms_id, payload, device)
        data = asdict(info)
        data["balancing_cells"] = info.balancing_cells
        self._record_diagnostic(f"bms{bms_id}_info", **data)
        self.shared_data.publish_sync(
            {self._prefix + key: value for key, value in info.shared_values().items()}
        )
        self._report_bits(f"bms{bms_id}_error_bits", info.error_bits, BMS_ERROR_BITS)
        for number, bits in ((1, info.warning1_bits), (2, info.warning2_bits)):
            self._report_bits(f"bms{bms_id}_warning{number}_bits", bits, BMS_WARNING_BITS)
        self._report_bits(f"bms{bms_id}_warning3_bits", info.warning3_bits, BMS_WARNING3_BITS)
        return info

    def read_event_log(self, unit: int) -> list[BydEventLogEntry]:
        """Request the newest event log page of a unit, record the entries not seen yet.

        The first page of a unit after the start is recorded completely, marked ``initial``.

        Returns:
            The newly recorded entries, newest first.

        Raises:
            BydMailboxTimeout: The mailbox did not report ready in time.
        """
        payload = self._request_mailbox(EVENT_LOG_MAILBOX, unit)
        entries = decode_event_log(unit, payload, self._timezone)
        initial = unit not in self._last_event
        last = self._last_event.get(unit)
        new: list[BydEventLogEntry] = []
        for entry in entries:  # newest first
            if entry.identity == last:
                break
            new.append(entry)
        if entries:
            self._last_event[unit] = entries[0].identity
        elif initial:
            self._last_event[unit] = ((), -1, "")
        for entry in reversed(new):  # record oldest first
            self._record_diagnostic(
                "event_log_entry",
                unit=entry.unit,
                event_date_time=to_datetime(
                    entry.date_time, as_string=True, in_timezone=self._timezone
                ),
                code=entry.code,
                code_name=entry.code_name,
                data_hex=entry.data_hex,
                raw_time=entry.raw_time,
                initial=initial,
            )
        return new

    # ---------------- mailbox ----------------
    def _request_mailbox(self, mailbox: BydMailbox, unit: int) -> list[int]:
        """Request a mailbox answer and return its payload (without length words).

        This is the only write of the monitor (ADR 0013): ``[unit, 0x8100]`` to the request
        register of one of the two known mailboxes, for a unit the device has.

        Raises:
            ValueError: Not a known mailbox, or a unit the device does not have.
            BydMailboxTimeout: The mailbox did not report ready in time.
        """
        if not any(mailbox is known for known in _MAILBOXES):
            raise ValueError(f"{self.device_id}: unknown BYD mailbox {mailbox!r}")
        if not self.mailbox_requests:
            raise ValueError(f"{self.device_id}: mailbox requests are switched off")
        device = self._require_device_info()
        if not mailbox.first_unit <= unit <= device.tower_count:
            raise ValueError(
                f"{self.device_id}: {mailbox.name} unit {unit} outside "
                f"{mailbox.first_unit}..{device.tower_count}"
            )
        self._write_registers(mailbox.request_address, (unit, MAILBOX_REQUEST_COMMAND))
        self._wait_ready(mailbox, unit)
        return mailbox_payload(
            [
                self._read_holding_registers(mailbox.data_address, MAILBOX_PACKET_COUNT)
                for _ in range(mailbox.packets)
            ]
        )

    def _wait_ready(self, mailbox: BydMailbox, unit: int) -> None:
        deadline = self._monotonic() + self.mailbox_timeout_s
        self._stop_event.wait(_MAILBOX_FIRST_POLL_S)
        while True:
            word = self._read_holding_registers(mailbox.ready_address, 1)[0]
            if word == MAILBOX_READY:
                return
            if self._monotonic() >= deadline or self._stop_event.is_set():
                raise BydMailboxTimeout(
                    f"{self.device_id}: {mailbox.name} unit {unit} not ready within "
                    f"{self.mailbox_timeout_s} s (last 0x{word:04X})"
                )
            self._stop_event.wait(_MAILBOX_POLL_S)

    def _try_mailbox(self, mailbox: BydMailbox, unit: int, read: Callable[[int], object]) -> None:
        """Run a mailbox read; a timeout or odd answer leaves the unit's readings stale."""
        try:
            read(unit)
        except (BydMailboxTimeout, BydProtocolError) as exc:
            logger.warning("{}: {}", self.device_id, exc)
            self._record_diagnostic(
                "mailbox_error", mailbox=mailbox.name, unit=unit, error=str(exc)
            )

    # ---------------- internals ----------------
    def _towers(self) -> range:
        return range(1, self._require_device_info().tower_count + 1)

    def _require_device_info(self) -> BydDeviceInfo:
        if self.device_info is None:
            raise ConnectionError(f"{self.device_id}: device information not read yet")
        return self.device_info

    def _report_bits(self, key: str, bits: int, names: Sequence[str]) -> None:
        """Log the active bits of a mask at WARNING once per change."""
        if self._reported_bits.get(key, 0) == bits:
            return
        self._reported_bits[key] = bits
        if bits:
            logger.warning(
                "{}: {} 0x{:04X}: {}", self.device_id, key, bits, ", ".join(bit_names(bits, names))
            )
        else:
            logger.info("{}: {} cleared", self.device_id, key)


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------

BYD_CONTROLLER_TYPE: Final = "byd:battery-box-premium"


class BydBatteryBoxSettings(ControllerBaseSettings):
    """Settings of the BYD Battery-Box Premium (HVS, HVM, HVL) monitor."""

    controller_type: Literal["byd:battery-box-premium"]
    host: str = Field(
        json_schema_extra={
            "description": (
                "Host name or IP address of the battery management unit (BMU). Its fixed "
                "Ethernet address is 192.168.16.254; EOS needs a route to 192.168.16.0/24 or a "
                "forwarded address."
            ),
            "examples": ["192.168.16.254"],
        },
    )
    port: int = Field(
        default=BYD_PORT,
        ge=1,
        le=65535,
        json_schema_extra={
            "description": "TCP port of the BMU (Modbus RTU over TCP).",
            "examples": [BYD_PORT],
        },
    )
    unit_id: int = Field(
        default=BYD_UNIT_ID,
        ge=0,
        le=255,
        json_schema_extra={"description": "Modbus unit id of the BMU.", "examples": [BYD_UNIT_ID]},
    )
    timeout_s: float = Field(
        default=5.0,
        gt=0,
        json_schema_extra={
            "description": "Timeout of one Modbus transaction and of a mailbox request [s].",
            "examples": [5.0],
        },
    )
    status_interval_s: float = Field(
        default=30.0,
        ge=10.0,
        json_schema_extra={
            "description": "Seconds between two reads of the BMU status.",
            "examples": [30.0],
        },
    )
    bms_info_interval_s: float = Field(
        default=600.0,
        ge=60.0,
        json_schema_extra={
            "description": (
                "Seconds between two requests of the BMS information (cells, warnings, faults) "
                "of a tower."
            ),
            "examples": [600.0],
        },
    )
    event_log_interval_s: float = Field(
        default=600.0,
        ge=120.0,
        json_schema_extra={
            "description": (
                "Seconds between two requests of the newest event log page of the BMU and of "
                "each tower. The event log is only read while the diagnostic log is enabled."
            ),
            "examples": [600.0],
        },
    )
    model: Optional[Literal["HVS", "HVM", "HVL"]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Model of the towers. Empty: detected from the cell count (32 cells per module: "
                "HVS, 16: HVM), else from the BMU configuration, which is not always right."
            ),
            "examples": [None, "HVS"],
        },
    )


_TOWER_IDS: Final = range(1, MAX_HV_TOWERS + 1)

# Diagnostic event fields collected as series for graphs (see BydBatteryBox.DIAGNOSTIC_SERIES).
_STATUS_SERIES: Final = (
    "soc_percent",
    "soh_percent",
    "battery_voltage_v",
    "output_voltage_v",
    "current_a",
    "power_w",
    "cell_voltage_max_v",
    "cell_voltage_min_v",
    "cell_temperature_max_c",
    "cell_temperature_min_c",
    "bmu_temperature_c",
    "charged_energy_kwh",
    "discharged_energy_kwh",
    "bmu_error_bits",
    "raw_current",
)
_BMS_SERIES: Final = (
    "soc_percent",
    "current_a",
    "cell_voltage_max_v",
    "cell_voltage_min_v",
    "cell_temperature_max_c",
    "cell_temperature_min_c",
    "balancing_cells",
    # Vector series: all values of the tower.
    "cell_voltages_mv",
    "sensor_temperatures_c",
)
BYD_DIAGNOSTIC_SERIES: Final[SeriesSpec] = {
    "status_read": _STATUS_SERIES,
    **{f"bms{bms_id}_info": _BMS_SERIES for bms_id in _TOWER_IDS},
}


def _graph(title: str, unit: str, status: Sequence[str], bms: Sequence[str]) -> DiagnosticGraph:
    return DiagnosticGraph(
        title=title,
        unit=unit,
        kind="numeric",
        series=[f"status_read.{name}" for name in status]
        + [f"bms{bms_id}_info.{name}" for bms_id in _TOWER_IDS for name in bms],
    )


# Explicit graphs: by unit alone, the pack voltage (~400 V) and the cell voltages (~3.4 V) would
# share one graph.
BYD_DIAGNOSTIC_GRAPHS: Final = (
    _graph("Battery power", "W", ("power_w",), ()),
    _graph("State of charge and health", "%", ("soc_percent", "soh_percent"), ("soc_percent",)),
    _graph("Battery voltage", "V", ("battery_voltage_v", "output_voltage_v"), ()),
    _graph(
        "Cell voltage",
        "V",
        ("cell_voltage_max_v", "cell_voltage_min_v"),
        ("cell_voltage_max_v", "cell_voltage_min_v"),
    ),
    _graph(
        "Temperature",
        "°C",
        ("cell_temperature_max_c", "cell_temperature_min_c", "bmu_temperature_c"),
        ("cell_temperature_max_c", "cell_temperature_min_c"),
    ),
    _graph("Current", "A", ("current_a",), ("current_a",)),
    _graph(
        "Charged and discharged energy", "kWh", ("charged_energy_kwh", "discharged_energy_kwh"), ()
    ),
    _graph("Balancing cells", "", (), ("balancing_cells",)),
    # The latest value of every cell and sensor, the towers overlaid.
    DiagnosticGraph(
        title="Cell voltages",
        unit="mV",
        kind="profile",
        series=[f"bms{bms_id}_info.cell_voltages_mv" for bms_id in _TOWER_IDS],
        index_label="Cell",
    ),
    DiagnosticGraph(
        title="Temperature sensors",
        unit="°C",
        kind="profile",
        series=[f"bms{bms_id}_info.sensor_temperatures_c" for bms_id in _TOWER_IDS],
        index_label="Sensor",
    ),
    # Every cell and sensor over time, one heatmap per tower.
    *(
        DiagnosticGraph(
            title=f"Tower {bms_id} {title}",
            unit=unit,
            kind="heatmap",
            series=[f"bms{bms_id}_info.{field}"],
            index_label=index_label,
        )
        for field, title, unit, index_label in (
            ("cell_voltages_mv", "cell voltages", "mV", "Cell"),
            ("sensor_temperatures_c", "temperature sensors", "°C", "Sensor"),
        )
        for bms_id in _TOWER_IDS
    ),
)


class BydBatteryBox(ControllerBase):
    """BYD Battery-Box Premium (HVS, HVM, HVL).

    Monitor only: it has no schedule, the plan is not used. It provides the readings listed in
    ``PROVIDED_KEYS`` under ``"<device_id>:<key>"``; the per tower keys only for towers that
    exist. The battery itself is controlled through its inverter.

    The read intervals apply live, without a restart.
    """

    Settings: ClassVar[type[BydBatteryBoxSettings]] = BydBatteryBoxSettings
    PROVIDED_DATA_KEYS: ClassVar[tuple[str, ...]] = PROVIDED_KEYS
    DIAGNOSTIC_SERIES: ClassVar[SeriesSpec] = BYD_DIAGNOSTIC_SERIES
    DIAGNOSTIC_GRAPHS: ClassVar[Optional[tuple[DiagnosticGraph, ...]]] = BYD_DIAGNOSTIC_GRAPHS
    _LIVE_FIELDS: ClassVar[set[str]] = ControllerBase._LIVE_FIELDS | {
        "status_interval_s",
        "bms_info_interval_s",
        "event_log_interval_s",
    }

    @property
    def settings(self) -> BydBatteryBoxSettings:
        """Settings snapshot, typed for this controller (checked against Settings in __init__)."""
        return cast(BydBatteryBoxSettings, self._settings)

    def _create_control_thread(self) -> MonitorThreadBase:
        """Build a fresh, not yet started thread."""
        settings = self.settings
        return BydBatteryBoxThread(
            self.device_id,
            self.shared_data,
            self.diagnostic_log,
            host=settings.host,
            port=settings.port,
            unit_id=settings.unit_id,
            timeout=settings.timeout_s,
            status_interval_s=settings.status_interval_s,
            bms_info_interval_s=settings.bms_info_interval_s,
            event_log_interval_s=settings.event_log_interval_s,
            mailbox_timeout_s=settings.timeout_s,
            model=settings.model,
            timezone=self.config.general.timezone,
        )

    def _on_live_settings_changed(self) -> None:
        thread = self._thread
        if isinstance(thread, BydBatteryBoxThread):
            settings = self.settings
            thread.set_intervals(
                status_interval_s=settings.status_interval_s,
                bms_info_interval_s=settings.bms_info_interval_s,
                event_log_interval_s=settings.event_log_interval_s,
            )
