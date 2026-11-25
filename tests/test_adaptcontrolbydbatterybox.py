"""Tests for the BYD Battery-Box Premium monitor (bydbatterybox.py) and its diagnostics tool.

No BMU and no network is needed: `FakeBmu` stands in for the device at register level. It
answers the holding register reads, runs the two mailboxes (request, ready word, answer packets)
and logs every write, so the tests can check that the mailbox requests are the only writes.

The event log fixture `bydbatterybox_eventlog_bms1.hex` is a real page of tower 1 (5 packets
without length words, 640 bytes).
"""

import json
from pathlib import Path
from typing import Any, Optional

import pytest
from pymodbus import FramerType

from akkudoktoreos.adapter.controllers import controlmodbus
from akkudoktoreos.adapter.controllers.byd import bydbatterybox as b
from akkudoktoreos.adapter.controllers.byd import bydbatteryboxdiagnostics as d
from akkudoktoreos.adapter.controllers.controlabc import (
    ControlDiagnosticLog,
    ControlSharedDataStore,
)
from akkudoktoreos.adapter.controllers.controlmodbus import ModbusTcpMonitorThreadBase

TESTDATA = Path(__file__).parent / "testdata"


def _words(data: bytes) -> list[int]:
    return [int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data), 2)]


def real_event_log_payload() -> list[int]:
    text = (TESTDATA / "bydbatterybox_eventlog_bms1.hex").read_text()
    return _words(bytes.fromhex("".join(text.split())))


def device_info_words(
    serial: str = "P030T020Z0000000000", model: int = 0, towers: int = 2, modules: int = 4
) -> list[int]:
    """Registers 0x0000–0x0013 of an HV tower.

    The defaults are those of a real HVS system (2 towers of 4 modules, serial masked), which
    reports 0 (HVL) as battery type in 0x0011.
    """
    words = _words(serial.encode("ascii").ljust(20, b"\x00"))
    words += [0, 0]  # 0x000A, 0x000B
    words += [0x0310, 0x0312, 0x0321, 0x0101]  # firmware BMU A/B, BMS, working areas
    words += [(19 << 8) | (towers << 4) | modules]  # 0x0010 inverter, towers, modules
    words += [(1 << 8) | model]  # 0x0011 application on grid, model
    words += [1 << 8]  # 0x0012 three phase
    words += [0]  # 0x0013
    return words


def status_words(
    raw_current: int = -96, battery_cv: int = 43520, cell_max_cv: int = 341, cell_min_cv: int = 339
) -> list[int]:
    """Registers 0x0500–0x0514, from a real HVS system while charging (2026-11-16).

    Changed from the real readings: the error bits (0x0041 instead of 0) and the min cell
    temperature (-3 instead of 27 °C), to cover decoding of bits and negative values.
    """
    words = [0] * b.STATUS_COUNT
    words[0x00] = 88  # SoC %
    words[0x01] = cell_max_cv  # max cell 3.41 V
    words[0x02] = cell_min_cv  # min cell 3.39 V
    words[0x03] = 96  # SoH %
    words[0x04] = raw_current & 0xFFFF  # -9.6 A while charging
    words[0x05] = battery_cv  # battery 435.20 V
    words[0x06] = 30
    words[0x07] = (-3) & 0xFFFF
    words[0x08] = 27
    words[0x0D] = 0x0041  # bits 0 and 6
    words[0x10] = 43550  # output 435.50 V
    words[0x11], words[0x12] = 51362 & 0xFFFF, 51362 >> 16  # charged 5136.2 kWh
    words[0x13], words[0x14] = 46671 & 0xFFFF, 46671 >> 16  # discharged 4667.1 kWh
    return words


def bms_info_payload(max_cell_mv: int = 3335) -> list[int]:
    """Payload of a BMS info answer (256 registers) with recognisable values."""
    p = [0] * 256
    p[0], p[1] = max_cell_mv, 3301
    p[2] = (17 << 8) | 42
    p[3], p[4] = 24, (-2) & 0xFFFF
    p[5] = (3 << 8) | 9
    p[6], p[7] = 0b101, 0x8000  # 3 balancing cells
    p[14], p[15] = 1000, 0  # 1.0 kWh
    p[16], p[17] = 500, 0
    p[20], p[23] = 4123, 4100
    p[24], p[25] = 573, 98
    p[26] = (-50) & 0xFFFF
    p[27], p[28], p[29], p[47] = 0x0001, 0, 0x4000, 0x0100
    for i in range(128):
        p[48 + i] = 3300 + i
    temperatures = bytes([20, 21, 0xFE] + [22] * 61)
    p[177:209] = _words(temperatures)
    return p


def packets(payload: list[int]) -> list[list[int]]:
    """Split a payload into answer packets with their length word."""
    return [[128] + payload[i : i + 64] for i in range(0, len(payload), 64)]


class FakeBmu:
    """Register level stand-in for the BMU, with the two mailboxes."""

    def __init__(self, towers: int = 2, modules: int = 4) -> None:
        self.registers: dict[int, int] = {}
        for offset, word in enumerate(device_info_words(towers=towers, modules=modules)):
            self.registers[b.DEVICE_INFO_ADDRESS + offset] = word
        for offset, word in enumerate(status_words()):
            self.registers[b.STATUS_ADDRESS + offset] = word
        self.writes: list[tuple[int, tuple[int, ...]]] = []
        self.answers: dict[int, list[list[int]]] = {}  # data address -> pending packets
        self.never_ready: set[str] = set()
        self.bms_payload = bms_info_payload()
        self.event_payloads: dict[int, list[int]] = {}
        # Recorded answer packets (with length words) per unit, they take precedence.
        self.bms_packets: dict[int, list[list[int]]] = {}
        self.event_packets: dict[int, list[list[int]]] = {}

    def read(self, address: int, count: int = 2) -> list[int]:
        for mailbox in b._MAILBOXES:
            if address == mailbox.data_address and count == b.MAILBOX_PACKET_COUNT:
                return self.answers[address].pop(0)
        return [self.registers.get(address + i, 0) for i in range(count)]

    def write(self, address: int, values: tuple[int, ...]) -> None:
        self.writes.append((address, tuple(values)))
        mailbox = next(m for m in b._MAILBOXES if m.request_address == address)
        unit = values[0]
        if mailbox.name in self.never_ready:
            self.registers[mailbox.ready_address] = 0x0000
            return
        self.registers[mailbox.ready_address] = b.MAILBOX_READY
        if mailbox is b.BMS_INFO_MAILBOX:
            recorded = self.bms_packets.get(unit)
            self.answers[address + 8] = (
                [list(packet) for packet in recorded] if recorded else packets(self.bms_payload)
            )
        else:
            recorded = self.event_packets.get(unit)
            self.answers[address + 8] = (
                [list(packet) for packet in recorded]
                if recorded
                else packets(self.event_payloads.get(unit, [0] * 320))
            )


def recorded_bmu() -> FakeBmu:
    """A BMU that answers with the registers recorded on a real HVS system while discharging.

    Fixture `bydbatterybox_hvs_discharging.json`: the raw words of a diagnostics tool run
    (``--json``) on an HVS with 2 towers of 4 modules; the serial numbers of the BMU and the
    towers are masked.
    """
    recorded = json.loads((TESTDATA / "bydbatterybox_hvs_discharging.json").read_text())
    bmu = FakeBmu()
    for offset, word in enumerate(recorded["device_info"]):
        bmu.registers[b.DEVICE_INFO_ADDRESS + offset] = word
    for offset, word in enumerate(recorded["status"]):
        bmu.registers[b.STATUS_ADDRESS + offset] = word
    bmu.bms_packets = {int(unit): packets for unit, packets in recorded["bms_info"].items()}
    bmu.event_packets = {int(unit): packets for unit, packets in recorded["event_log"].items()}
    return bmu


class FakeResponse:
    def __init__(self, registers: list[int]) -> None:
        self.registers = registers

    def isError(self) -> bool:  # noqa: N802 - pymodbus API
        return False


class FakeClient:
    """Stands in for ``ModbusTcpClient``, so the Modbus base records events and statistics."""

    def __init__(self, bmu: FakeBmu) -> None:
        self.bmu = bmu

    def read_holding_registers(self, address: int, count: int = 1, **kwargs: Any) -> FakeResponse:
        return FakeResponse(self.bmu.read(address, count))

    def write_registers(self, address: int, values: list[int], **kwargs: Any) -> FakeResponse:
        self.bmu.write(address, tuple(values))
        return FakeResponse([])

    def close(self) -> None:
        pass


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture(autouse=True)
def fast_mailbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(b, "_MAILBOX_FIRST_POLL_S", 0.0)
    monkeypatch.setattr(b, "_MAILBOX_POLL_S", 0.0)


def make_thread(
    bmu: FakeBmu,
    *,
    log_capacity: int = 1000,
    clock: Optional[Clock] = None,
    **kwargs: Any,
) -> b.BydBatteryBoxThread:
    thread = b.BydBatteryBoxThread(
        "byd",
        ControlSharedDataStore(),
        ControlDiagnosticLog(log_capacity),
        host="192.0.2.1",
        timezone="Europe/Berlin",
        monotonic=clock or Clock(),
        **kwargs,
    )
    client = FakeClient(bmu)

    def connect() -> None:
        thread._client = client  # type: ignore[assignment]

    thread._connect = connect  # type: ignore[method-assign]
    connect()
    return thread


def events(thread: b.BydBatteryBoxThread, name: str) -> list[dict[str, Any]]:
    return [dict(e.data) for e in thread.diagnostic_log.snapshot() if e.event == name]


# ---------------------------------------------------------------------------
# Modbus framing
# ---------------------------------------------------------------------------


class TestModbusFramer:
    @pytest.fixture
    def created(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []

        class RecordingClient:
            def __init__(self, host: str, **kwargs: Any) -> None:
                created.append({"host": host, **kwargs})

            def connect(self) -> bool:
                return True

            def close(self) -> None:
                pass

        monkeypatch.setattr(controlmodbus, "ModbusTcpClient", RecordingClient)
        return created

    def test_default_is_modbus_tcp(self, created: list[dict[str, Any]]) -> None:
        class Monitor(ModbusTcpMonitorThreadBase):
            def _read_status(self) -> dict[str, Any]:
                return {}

        Monitor("m", ControlSharedDataStore(), ControlDiagnosticLog(), host="h")._connect()
        assert created[0]["framer"] is FramerType.SOCKET

    def test_byd_uses_rtu_over_tcp(self, created: list[dict[str, Any]]) -> None:
        thread = b.BydBatteryBoxThread(
            "byd", ControlSharedDataStore(), ControlDiagnosticLog(), host="192.0.2.1"
        )
        thread._connect()
        assert created[0]["framer"] is FramerType.RTU
        assert created[0]["port"] == 8080


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


class TestDecodeDeviceInfo:
    def test_hvs(self) -> None:
        info = b.decode_device_info(device_info_words(model=2))
        assert info.serial == "P030T020Z0000000000"
        assert (info.model, info.tower_count, info.module_count) == ("HVS", 2, 4)
        assert info.model_source == b.MODEL_SOURCE_REGISTER
        assert (info.cells_per_module, info.temperature_sensors_per_module) == (32, 12)
        assert info.bmu_firmware_a == "3.16"
        assert (info.application, info.phase, info.inverter_id) == ("On Grid", "Three", 19)

    def test_hvm_and_second_hv_prefix(self) -> None:
        info = b.decode_device_info(device_info_words(serial="E0P3XYZ", model=1))
        assert info.model == "HVM"

    def test_cell_count_overrides_register(self) -> None:
        info = b.decode_device_info(device_info_words(model=0), counted_cells_per_module=32)
        assert (info.model, info.model_source) == ("HVS", b.MODEL_SOURCE_CELL_COUNT)
        assert info.register_model_id == 0

    def test_setting_overrides_everything(self) -> None:
        info = b.decode_device_info(
            device_info_words(model=2), counted_cells_per_module=16, model_override="HVL"
        )
        assert (info.model, info.model_source) == ("HVL", b.MODEL_SOURCE_SETTING)

    def test_unknown_setting(self) -> None:
        with pytest.raises(b.BydProtocolError, match="unknown model"):
            b.decode_device_info(device_info_words(), model_override="LVS")

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"serial": "P021ABC"}, "not an HV"),
            ({"model": 7}, "set the model"),
            ({"towers": 0}, "tower count"),
            ({"towers": 4}, "tower count"),
            ({"modules": 0}, "no modules"),
        ],
    )
    def test_refused(self, kwargs: dict[str, Any], message: str) -> None:
        with pytest.raises(b.BydProtocolError, match=message):
            b.decode_device_info(device_info_words(**kwargs))

    def test_too_few_registers(self) -> None:
        with pytest.raises(b.BydProtocolError):
            b.decode_device_info(device_info_words()[:10])


class TestDecodeStatus:
    def test_values(self) -> None:
        status = b.decode_status(status_words())
        assert status.soc_percent == 88
        assert status.cell_voltage_max_v == 3.41
        assert status.battery_voltage_v == 435.2
        assert status.output_voltage_v == 435.5
        assert status.raw_current == -96
        assert status.current_a == -9.6  # charging: negative
        assert status.power_w == -4180.8
        assert status.cell_temperature_min_c == -3
        assert status.charged_energy_kwh == 5136.2
        assert status.discharged_energy_kwh == 4667.1
        assert b.bit_names(status.bmu_error_bits, b.BMU_ERROR_BITS) == (
            "High temperature charging (cells)",
            "Cell imbalance",
        )

    def test_shared_values(self) -> None:
        values = b.decode_status(status_words()).shared_values()
        assert set(values) == set(b.STATUS_KEYS)
        assert values["soc_factor"] == 0.88


class TestCountCells:
    def test_real_hvs_tower(self) -> None:
        assert b.count_cells_per_module(b.decode_status(status_words()), 4) == 32

    def test_hvm(self) -> None:
        status = b.decode_status(status_words(battery_cv=21760))  # 64 cells
        assert b.count_cells_per_module(status, 4) == 16

    @pytest.mark.parametrize(
        ("kwargs", "modules"),
        [
            ({"battery_cv": 0}, 4),
            ({"cell_max_cv": 0, "cell_min_cv": 0}, 4),
            ({}, 0),
            ({}, 3),  # 128 cells do not divide into 3 modules
        ],
    )
    def test_not_countable(self, kwargs: dict[str, int], modules: int) -> None:
        assert b.count_cells_per_module(b.decode_status(status_words(**kwargs)), modules) == 0


class TestDecodeBmsInfo:
    def test_values(self) -> None:
        device = b.decode_device_info(device_info_words(model=2, modules=4))
        info = b.decode_bms_info(1, bms_info_payload(), device)
        assert (info.cell_voltage_max_v, info.cell_voltage_min_v) == (3.335, 3.301)
        assert (info.cell_voltage_max_number, info.cell_voltage_min_number) == (17, 42)
        assert info.cell_temperature_min_c == -2
        assert info.balancing_cells == 3
        assert info.soc_percent == 57.3
        assert info.current_a == -5.0 * b.RAW_CURRENT_SIGN
        assert len(info.cell_voltages_mv) == 128  # 4 HVS modules of 32 cells
        assert info.cell_voltages_mv[127] == 3427
        assert len(info.sensor_temperatures_c) == 48  # 4 modules of 12 sensors
        assert info.sensor_temperatures_c[:3] == (20, 21, -2)
        assert set(info.shared_values()) == set(b.tower_keys(1))

    def test_cells_capped_at_128(self) -> None:
        device = b.decode_device_info(device_info_words(model=2, modules=5))
        info = b.decode_bms_info(1, bms_info_payload(), device)
        assert len(info.cell_voltages_mv) == 128
        assert len(info.sensor_temperatures_c) == 60

    def test_hvl_has_no_cells(self) -> None:
        device = b.decode_device_info(device_info_words(model=0))
        info = b.decode_bms_info(1, bms_info_payload(), device)
        assert info.cell_voltages_mv == () and info.sensor_temperatures_c == ()

    def test_implausible_voltage(self) -> None:
        device = b.decode_device_info(device_info_words(model=2))
        with pytest.raises(b.BydProtocolError, match="implausible"):
            b.decode_bms_info(1, bms_info_payload(max_cell_mv=6000), device)

    def test_packet_length_checked(self) -> None:
        with pytest.raises(b.BydProtocolError):
            b.mailbox_payload([[128] * 10])


class TestDecodeEventLog:
    def test_real_page(self) -> None:
        entries = b.decode_event_log(1, real_event_log_payload(), "Europe/Berlin")
        assert len(entries) == 20
        first = entries[0]
        assert first.raw_time == (26, 11, 15, 8, 29, 13)
        assert first.date_time.isoformat() == "2026-11-15T08:29:13+01:00"
        assert (first.code, first.code_name) == (0x04, "Start charging")
        assert len(first.data_hex) == 46  # 23 data bytes
        assert entries[1].code_name == "Firmware update finish"

    def test_bmu_codes_and_end_of_page(self) -> None:
        payload = [0] * 320
        payload[0:4] = [0x001A, 0x0B0F, 0x081D, 0x0D00]  # Power ON 2026-11-15 08:29:13
        entries = b.decode_event_log(0, payload)
        assert [e.code_name for e in entries] == ["Power ON"]

    @pytest.mark.parametrize(
        ("code", "name"),
        [(0x22, "BMS update start"), (0x23, "BMS update done"), (0x29, "MCU list of BMS")],
    )
    def test_bmu_codes_seen_on_a_real_tower(self, code: int, name: str) -> None:
        payload = [0] * 320
        payload[0:4] = [(code << 8) | 0x1A, 0x0B0F, 0x0739, 0x0300]
        assert b.decode_event_log(0, payload)[0].code_name == name

    def test_invalid_time_skipped(self) -> None:
        payload = [0] * 320
        payload[0:4] = [0x041A, 0x0D0F, 0x081D, 0x0D00]  # month 13
        payload[15:19] = [0x051A, 0x0B0F, 0x081D, 0x0D00]
        entries = b.decode_event_log(1, payload)
        assert [e.code for e in entries] == [0x05]


# ---------------------------------------------------------------------------
# Monitor thread
# ---------------------------------------------------------------------------


class TestMonitorThread:
    def test_cycle_reads_and_publishes(self) -> None:
        bmu = FakeBmu(towers=2)
        thread = make_thread(bmu)
        thread._on_connected()
        thread._cycle()

        shared = thread.shared_data.snapshot()
        assert shared["byd:tower_count"] == 2
        assert shared["byd:soc_factor"] == 0.88
        assert shared["byd:bms2_balancing_cells"] == 3
        assert "byd:bms3_soc_percent" not in shared
        assert set(shared) <= set(thread.published_keys())
        assert thread.get_status().data["soc_percent"] == 88
        assert [e["bms_id"] for e in events(thread, "bms1_info") + events(thread, "bms2_info")] == [
            1,
            2,
        ]
        assert len(events(thread, "event_log_entry")) == 0  # empty pages

    def test_only_mailbox_requests_are_written(self) -> None:
        bmu = FakeBmu(towers=2)
        thread = make_thread(bmu)
        thread._on_connected()
        thread._cycle()
        assert bmu.writes == [
            (0x0550, (1, 0x8100)),
            (0x0550, (2, 0x8100)),
            (0x05A0, (0, 0x8100)),
            (0x05A0, (1, 0x8100)),
            (0x05A0, (2, 0x8100)),
        ]

    def test_intervals(self) -> None:
        clock = Clock()
        bmu = FakeBmu(towers=1)
        thread = make_thread(bmu, clock=clock, status_interval_s=30, bms_info_interval_s=600)
        thread._on_connected()
        assert thread._cycle() == 30
        clock.t += 30
        thread._cycle()
        assert len(events(thread, "status_read")) == 2
        assert len(events(thread, "bms1_info")) == 1

    def test_read_only_writes_nothing(self) -> None:
        bmu = FakeBmu()
        thread = make_thread(bmu, mailbox_requests=False)
        thread._on_connected()
        thread._cycle()
        assert bmu.writes == []
        assert events(thread, "status_read")

    def test_event_log_skipped_without_diagnostic_log(self) -> None:
        bmu = FakeBmu(towers=1)
        thread = make_thread(bmu, log_capacity=0)
        thread._on_connected()
        thread._cycle()
        assert [address for address, _ in bmu.writes] == [0x0550]
        thread.diagnostic_log.resize(100)  # raised live
        thread._cycle()
        assert 0x05A0 in [address for address, _ in bmu.writes]

    def test_event_log_initial_then_new_only(self) -> None:
        bmu = FakeBmu(towers=1)
        page = real_event_log_payload()
        bmu.event_payloads[1] = page
        thread = make_thread(bmu)
        thread._on_connected()

        first = thread.read_event_log(1)
        assert len(first) == 20
        recorded = events(thread, "event_log_entry")
        assert all(e["initial"] for e in recorded)
        assert recorded[-1]["code_name"] == "Start charging"  # newest recorded last

        assert thread.read_event_log(1) == []  # same page again

        newer = [0x061A, 0x0B0F, 0x0900, 0x0000] + [0] * 11  # Start discharging 09:00:00
        bmu.event_payloads[1] = newer + page[:-15]
        new = thread.read_event_log(1)
        assert [e.code_name for e in new] == ["Start discharging"]
        assert events(thread, "event_log_entry")[-1]["initial"] is False

    def test_mailbox_timeout_leaves_readings_stale(self) -> None:
        bmu = FakeBmu(towers=1)
        bmu.never_ready.add("bms_info")
        thread = make_thread(bmu, mailbox_timeout_s=0.0)
        thread._on_connected()
        thread._cycle()  # does not raise
        assert "byd:bms1_soc_percent" not in thread.shared_data.snapshot()
        errors = events(thread, "mailbox_error")
        assert errors[0]["mailbox"] == "bms_info" and errors[0]["unit"] == 1
        assert thread.shared_data.snapshot()["byd:soc_factor"] == 0.88

    def test_guard_rejects_unknown_units_and_mailboxes(self) -> None:
        bmu = FakeBmu(towers=2)
        thread = make_thread(bmu)
        thread._on_connected()
        with pytest.raises(ValueError, match="outside"):
            thread._request_mailbox(b.BMS_INFO_MAILBOX, 0)
        with pytest.raises(ValueError, match="outside"):
            thread._request_mailbox(b.EVENT_LOG_MAILBOX, 3)
        rogue = b.BydMailbox("debug", 0x0100, 0x0101, 0x0102, 1, first_unit=0)
        with pytest.raises(ValueError, match="unknown"):
            thread._request_mailbox(rogue, 1)
        assert bmu.writes == []

    def test_guard_rejects_when_switched_off(self) -> None:
        bmu = FakeBmu()
        thread = make_thread(bmu, mailbox_requests=False)
        thread._on_connected()
        with pytest.raises(ValueError, match="switched off"):
            thread._request_mailbox(b.BMS_INFO_MAILBOX, 1)

    def test_error_bits_remembered_per_key(self) -> None:
        bmu = FakeBmu(towers=1)
        thread = make_thread(bmu, mailbox_requests=False)
        thread._on_connected()
        thread.read_status()
        thread.read_status()
        assert thread._reported_bits["bmu_error_bits"] == 0x0041

    def test_real_hvs_detected_by_cell_count(self) -> None:
        thread = make_thread(FakeBmu())
        thread._on_connected()
        info = thread.device_info
        assert info is not None
        assert (info.model, info.model_source) == ("HVS", b.MODEL_SOURCE_CELL_COUNT)
        device_info = events(thread, "device_info")[0]
        assert device_info["register_model_id"] == 0
        assert device_info["counted_cells_per_module"] == 32

    def test_model_setting(self) -> None:
        thread = make_thread(FakeBmu(), model="HVM")
        thread._on_connected()
        assert thread.device_info is not None and thread.device_info.model == "HVM"

    def test_not_hv_fails_connect(self) -> None:
        bmu = FakeBmu()
        for offset, word in enumerate(device_info_words(serial="P021LV")):
            bmu.registers[offset] = word
        thread = make_thread(bmu)
        with pytest.raises(b.BydProtocolError):
            thread._on_connected()


# ---------------------------------------------------------------------------
# Diagnostics tool
# ---------------------------------------------------------------------------


class TestDiagnosticsTool:
    def run(self, bmu: FakeBmu, *args: str) -> tuple[int, str]:
        options = d.parse_args(["--host", "192.0.2.1", "--count", "1", *args])
        thread = make_thread(bmu, log_capacity=d.DIAGNOSTIC_LOG_CAPACITY)
        thread.mailbox_requests = not options.read_only
        lines: list[str] = []
        code = d.run_diagnostics(options, out=lines.append, thread=thread, sleep=lambda s: None)
        return code, "\n".join(lines)

    def test_text_report(self) -> None:
        bmu = FakeBmu(towers=1)
        bmu.event_payloads[1] = real_event_log_payload()
        code, text = self.run(bmu)
        assert code == 0
        assert "Device information" in text and "HVS" in text
        assert "model              HVS" in text
        assert "current -9.6 A (raw -96)" in text
        assert "Cell imbalance" in text
        assert "BMS 1" in text and "module 4 cells mV:" in text
        assert "write 0x0550 <- 0x0001 0x8100" in text
        assert "Start charging" not in text  # initial page hidden
        assert "Modbus read:" in text and "write: 3 (0 errors)" in text

    def test_event_log_history(self) -> None:
        bmu = FakeBmu(towers=1)
        bmu.event_payloads[1] = real_event_log_payload()
        _, text = self.run(bmu, "--event-log-history")
        assert "Event BMS 1 2026-11-15T08:29:13+01:00  0x04 Start charging" in text

    def test_model_option(self) -> None:
        options = d.parse_args(["--host", "h", "--model", "HVM"])
        assert options.model == "HVM"
        with pytest.raises(SystemExit):
            d.parse_args(["--host", "h", "--model", "LVS"])

    def test_read_only(self) -> None:
        bmu = FakeBmu()
        code, text = self.run(bmu, "--read-only")
        assert code == 0 and bmu.writes == [] and "BMS 1" not in text

    def test_json(self) -> None:
        code, text = self.run(FakeBmu(towers=1), "--json")
        assert code == 0
        names = [json.loads(line)["event"] for line in text.splitlines()]
        assert "device_info" in names and "status_read" in names and "modbus_stats" in names

    def test_connect_failure(self) -> None:
        bmu = FakeBmu()
        for offset, word in enumerate(device_info_words(serial="P021LV")):
            bmu.registers[offset] = word
        code, text = self.run(bmu)
        assert code == 2 and "not an HV" in text


class TestRecordedSystem:
    """A whole monitor cycle against the registers of a real HVS system while discharging."""

    def test_device_and_model(self) -> None:
        thread = make_thread(recorded_bmu())
        thread._on_connected()
        info = thread.device_info
        assert info is not None
        assert (info.model, info.model_source, info.register_model_id) == (
            "HVS",
            b.MODEL_SOURCE_CELL_COUNT,
            0,
        )
        assert (info.tower_count, info.module_count, info.counted_cells_per_module) == (2, 4, 32)
        assert info.serial == "P030T020Z0000000000"

    def test_readings_while_discharging(self) -> None:
        thread = make_thread(recorded_bmu())
        thread._on_connected()
        thread._cycle()
        shared = thread.shared_data.snapshot()
        assert shared["byd:current_a"] == 3.7
        assert shared["byd:power_w"] == 1561.0
        assert shared["byd:soc_factor"] == 0.72
        # The tower currents add up to the BMU current.
        assert shared["byd:bms1_soc_percent"] == 69.9
        tower_currents = [e["current_a"] for e in events(thread, "bms1_info") + events(thread, "bms2_info")]
        assert tower_currents == [1.0, 2.7]

    def test_cells_and_temperatures_of_the_towers(self) -> None:
        thread = make_thread(recorded_bmu())
        thread._on_connected()
        thread._cycle()
        for name in ("bms1_info", "bms2_info"):
            (info,) = events(thread, name)
            cells = info["cell_voltages_mv"]
            temperatures = info["sensor_temperatures_c"]
            assert len(cells) == 128 and len(temperatures) == 48
            assert max(cells) == round(info["cell_voltage_max_v"] * 1000)
            assert max(temperatures) == info["cell_temperature_max_c"]
            assert min(temperatures) == info["cell_temperature_min_c"]
        (bms1,) = events(thread, "bms1_info")
        assert bms1["cell_voltage_max_number"] == 56
        assert bms1["cell_voltages_mv"][55] == 3301
        assert bms1["balancing_flags"] == (0, 0, 32, 0)
        assert thread.shared_data.snapshot()["byd:bms1_balancing_cells"] == 1

    def test_event_logs_of_all_units(self) -> None:
        thread = make_thread(recorded_bmu())
        thread._on_connected()
        thread._cycle()
        entries = events(thread, "event_log_entry")
        assert len(entries) == 60  # 20 per unit: BMU, tower 1, tower 2
        assert {e["unit"] for e in entries} == {0, 1, 2}
        assert all(e["initial"] for e in entries)
        assert not [e for e in entries if e["code_name"].startswith("Unknown") and e["unit"] == 2]

    def test_cells_and_temperature_sensors_are_graphed(self) -> None:
        thread = make_thread(recorded_bmu())
        log = thread.diagnostic_log
        log.configure_series(b.BYD_DIAGNOSTIC_SERIES, b.BYD_DIAGNOSTIC_GRAPHS)
        thread._on_connected()
        thread._cycle()
        view = log.series_view()
        (bms1,) = events(thread, "bms1_info")
        cells = view.vectors["bms1_info.cell_voltages_mv"]
        assert cells.latest() == [float(mv) for mv in bms1["cell_voltages_mv"]]
        assert len(view.vectors["bms2_info.sensor_temperatures_c"].latest()) == 48
        assert "bms3_info.cell_voltages_mv" not in view.vectors  # two towers only
        graphs = {graph.title: graph for graph in view.graphs}
        profile = graphs["Cell voltages"]
        assert (profile.kind, profile.unit, profile.index_label) == ("profile", "mV", "Cell")
        assert profile.series[:2] == ["bms1_info.cell_voltages_mv", "bms2_info.cell_voltages_mv"]
        heatmap = graphs["Tower 2 temperature sensors"]
        assert (heatmap.kind, heatmap.series) == ("heatmap", ["bms2_info.sensor_temperatures_c"])


# ---------------------------------------------------------------------------
# Settings and controller
# ---------------------------------------------------------------------------


def _settings(**kwargs: Any) -> b.BydBatteryBoxSettings:
    values: dict[str, Any] = {"host": "192.0.2.1", **kwargs}
    return b.BydBatteryBoxSettings(controller_type="byd:battery-box-premium", **values)


class TestSettings:
    def test_defaults(self) -> None:
        settings = _settings()
        assert (settings.port, settings.unit_id, settings.timeout_s) == (8080, 1, 5.0)
        assert (
            settings.status_interval_s,
            settings.bms_info_interval_s,
            settings.event_log_interval_s,
        ) == (30.0, 600.0, 600.0)
        assert settings.model is None

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"status_interval_s": 9.0},
            {"bms_info_interval_s": 59.0},
            {"event_log_interval_s": 119.0},
            {"model": "LVS"},
            {"port": 0},
        ],
    )
    def test_refused(self, kwargs: dict[str, Any]) -> None:
        with pytest.raises(ValueError):
            _settings(**kwargs)

    def test_in_the_controller_union(self) -> None:
        from akkudoktoreos.adapter.control import ControlAdapterCommonSettings

        common = ControlAdapterCommonSettings.model_validate(
            {
                "controllers": {
                    "battery": {"controller_type": "byd:battery-box-premium", "host": "192.0.2.1"}
                }
            }
        )
        assert isinstance(common.controllers["battery"], b.BydBatteryBoxSettings)
        assert "battery:bms3_error_bits" in common.shared_data_keys
        assert "battery:soc_factor" in common.shared_data_keys


class TestController:
    def make(self, settings: Optional[b.BydBatteryBoxSettings] = None) -> b.BydBatteryBox:
        return b.BydBatteryBox(
            "battery", ControlSharedDataStore(), ControlDiagnosticLog(100), settings or _settings()
        )

    def test_thread_from_settings(self, config_eos: Any) -> None:
        config_eos.merge_settings_from_dict({"general": {"timezone": "Europe/Berlin"}})
        controller = self.make(_settings(port=8081, model="HVS", timeout_s=7.0))
        thread = controller._create_control_thread()
        assert isinstance(thread, b.BydBatteryBoxThread)
        assert thread._framer is FramerType.RTU
        assert (thread._port, thread._timeout, thread.mailbox_timeout_s) == (8081, 7.0, 7.0)
        assert thread.model == "HVS"
        assert thread._timezone == "Europe/Berlin"
        assert sorted(thread.published_keys()) == sorted(controller.shared_data_keys())

    def test_intervals_apply_live(self, config_eos: Any) -> None:
        controller = self.make()
        thread = controller._create_control_thread()
        assert isinstance(thread, b.BydBatteryBoxThread)
        controller._thread = thread  # not started

        changed = _settings(status_interval_s=10.0, bms_info_interval_s=60.0)
        assert not controller.settings_changed(changed)
        controller.apply_live_settings(changed)
        assert (thread.status_interval_s, thread.bms_info_interval_s) == (10.0, 60.0)
        assert controller.settings.status_interval_s == 10.0

    def test_connection_settings_restart(self) -> None:
        controller = self.make()
        assert controller.settings_changed(_settings(host="192.0.2.2"))
        assert controller.settings_changed(_settings(model="HVM"))

    def test_graphs_use_collected_series(self) -> None:
        spec = b.BYD_DIAGNOSTIC_SERIES
        for graph in b.BYD_DIAGNOSTIC_GRAPHS:
            for key in graph.series:
                event, field = key.split(".")
                fields = spec[event]
                assert fields is not None and field in fields, key


class TestLiveIntervals:
    def test_shorter_interval_applies_right_away(self) -> None:
        clock = Clock()
        thread = make_thread(FakeBmu(towers=1), clock=clock, mailbox_requests=False)
        thread._on_connected()
        assert thread._cycle() == 30.0
        clock.t += 5
        thread.set_intervals(
            status_interval_s=10.0, bms_info_interval_s=600.0, event_log_interval_s=600.0
        )
        assert thread._wakeup.is_set()
        assert thread._cycle() == 10.0  # next status at +15 s instead of +30 s

    def test_longer_interval_applies_after_next_read(self) -> None:
        clock = Clock()
        thread = make_thread(FakeBmu(towers=1), clock=clock, mailbox_requests=False)
        thread._on_connected()
        thread._cycle()
        thread.set_intervals(
            status_interval_s=60.0, bms_info_interval_s=600.0, event_log_interval_s=600.0
        )
        assert thread._cycle() == 30.0
        clock.t += 30
        assert thread._cycle() == 60.0
