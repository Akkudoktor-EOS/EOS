"""Tests for the monitor of the SMA STP-SE (smastpediagnostics.py).

The Modbus client of the control thread is replaced by `FakeClient`, which answers reads from a
register map and counts every attempt to write. In dry run (the default) the monitor never
writes: `test_never_writes` runs every mode and checks the counter. With `write_registers` the
writes are sent, see `TestLiveMode`.
"""

import json
import sys
import types
from typing import Any, Optional

import pytest

from akkudoktoreos.adapter.controllers import controlmodbus
from akkudoktoreos.adapter.controllers.controlabc import (
    ControlDiagnosticLog,
    ControlSharedDataStore,
)
from akkudoktoreos.adapter.controllers.sma import smastpe as s
from akkudoktoreos.adapter.controllers.sma import smastpediagnostics as m
from akkudoktoreos.devices.devicesabc import BatteryOperationMode as Mode


# ---------------------------------------------------------------------------
# Fake Modbus device
# ---------------------------------------------------------------------------
def u32(value: int) -> list[int]:
    return [(value >> 16) & 0xFFFF, value & 0xFFFF]


def s32(value: int) -> list[int]:
    return u32(value & 0xFFFFFFFF)


def default_registers() -> dict[int, int]:
    """A healthy STP-SE: 6 kW PV, 1.2 kW AC power, battery at 63 % charging with 800 W."""
    regs: dict[int, int] = {}

    def put(address: int, words: list[int]) -> None:
        for k, word in enumerate(words):
            regs[address + k] = word

    put(s.REG_DC1_POWER, s32(3000))
    put(s.REG_DC2_POWER, s32(1500))
    put(s.REG_AC_POWER, s32(-350))
    put(s.REG_BAT_SOC, u32(63))
    put(s.REG_BAT_ACT_CAPACITY, u32(9000))
    put(s.REG_BAT_TEMPERATURE, s32(215))
    put(s.REG_BAT_CHARGE_POWER, u32(800))
    put(s.REG_BAT_DISCHARGE_POWER, u32(0))
    put(s.REG_BAT_NOM_CAPACITY, u32(10000))
    put(s.REG_BAT_MAX_CHARGE, u32(5000))
    put(s.REG_BAT_MAX_DISCHARGE, u32(4500))
    put(s.REG_PV_WMOD, u32(s.WMOD_PLANT_CONTROL))
    regs[s.REG_PV_LIMIT_PERCENT] = 100
    # extras: some known to the device, others not (30051, 30057 and 41259 are missing)
    put(30053, u32(9356))
    put(30201, u32(307))
    put(30217, u32(51))
    put(30843, u32(51200))  # 512.00 V
    put(30881, u32(1780))
    put(30953, s32(412))
    put(31061, u32(1129))
    put(33003, u32(235))
    put(40035, u32(1785))
    put(40723, u32(5))
    # control registers read back as 0: they are volatile
    for address in (s.REG_WCTL_SPT, s.REG_WCTL_ACT, s.REG_BMS_OPMOD, 40793, 40795, 40797, 40799, 40801):
        put(address, u32(0))
    return regs


class FakeResponse:
    def __init__(self, registers: Optional[list[int]] = None) -> None:
        self.registers = registers or []
        self._error = registers is None

    def isError(self) -> bool:  # noqa: N802 (pymodbus API)
        return self._error


class FakeClient:
    """Replaces ModbusTcpClient. Counts the attempts to write.

    Writes are refused (AssertionError) unless `allow_writes` is set. `fail_writes` makes the
    device answer a write with an error. Sent writes are collected in `writes`.
    """

    registers: dict[int, int] = {}
    connectable = True
    write_attempts = 0
    allow_writes = False
    fail_writes = False
    writes: list[tuple[int, list[int]]] = []
    reads: list[tuple[str, int, int]] = []

    def __init__(
        self, host: str, port: int = 502, timeout: float = 3.0, framer: Any = None
    ) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.framer = framer

    def connect(self) -> bool:
        return type(self).connectable

    def close(self) -> None:
        pass

    def _read(self, kind: str, address: int, count: int) -> FakeResponse:
        type(self).reads.append((kind, address, count))
        words = [type(self).registers.get(address + k) for k in range(count)]
        if any(word is None for word in words):
            return FakeResponse(None)  # illegal data address
        return FakeResponse([w for w in words if w is not None])

    def read_input_registers(self, address: int, count: int = 1, **kwargs: Any) -> FakeResponse:
        return self._read("input", address, count)

    def read_holding_registers(self, address: int, count: int = 1, **kwargs: Any) -> FakeResponse:
        return self._read("holding", address, count)

    def write_register(self, *args: Any, **kwargs: Any) -> None:
        type(self).write_attempts += 1
        raise AssertionError("the monitor does not write single registers")

    def write_registers(self, address: int, values: list[int], **kwargs: Any) -> FakeResponse:
        cls = type(self)
        cls.write_attempts += 1
        if not cls.allow_writes:
            raise AssertionError("the monitor must not write in dry run")
        if cls.fail_writes:
            return FakeResponse(None)
        cls.writes.append((address, list(values)))
        return FakeResponse([])

    def write_coil(self, *args: Any, **kwargs: Any) -> None:
        type(self).write_attempts += 1
        raise AssertionError("the monitor does not write coils")


@pytest.fixture(autouse=True)
def fake_client(monkeypatch: pytest.MonkeyPatch) -> type[FakeClient]:
    FakeClient.registers = default_registers()
    FakeClient.connectable = True
    FakeClient.write_attempts = 0
    FakeClient.allow_writes = False
    FakeClient.fail_writes = False
    FakeClient.writes = []
    FakeClient.reads = []
    monkeypatch.setattr(controlmodbus, "ModbusTcpClient", FakeClient)
    return FakeClient


def run(
    *,
    out: Optional[list[str]] = None,
    shared: Optional[ControlSharedDataStore] = None,
    **options: Any,
) -> tuple[int, str]:
    """Run the monitor for the given options; return (exit code, output)."""
    lines: list[str] = [] if out is None else out
    options.setdefault("count", 1)
    code = m.run_monitor(
        m.MonitorOptions(host="192.168.178.6", **options),
        out=lines.append,
        shared=shared,
        sleep=lambda seconds: None,
    )
    return code, "\n".join(lines)


# ---------------------------------------------------------------------------
# Dry run: never writes
# ---------------------------------------------------------------------------
class TestReadOnly:
    @pytest.mark.parametrize("mode", [mode.value for mode in Mode])
    @pytest.mark.parametrize("pv", [None, 10000.0], ids=["no-pv-limit", "pv-limit"])
    def test_never_writes(self, fake_client: type[FakeClient], mode: str, pv: Optional[float]) -> None:
        shared = ControlSharedDataStore()
        shared.publish_sync({"meter:grid_net_w": -3000.0})  # export: all control paths are used
        code, output = run(mode=mode, pv_rated_power_w=pv, count=4, shared=shared)
        assert code == 0
        assert fake_client.write_attempts == 0
        assert "ERROR" not in output.split("== Extra")[0]

    def test_every_modbus_transaction_is_a_read(self, fake_client: type[FakeClient]) -> None:
        run(mode="FORCED_CHARGE", count=3)
        assert fake_client.reads
        assert {kind for kind, _, _ in fake_client.reads} <= {"input", "holding"}

    def test_dry_run_write_is_only_recorded(self, fake_client: type[FakeClient]) -> None:
        log = ControlDiagnosticLog(10)
        thread = m.DiagnosticsControl(
            "inv",
            ControlSharedDataStore(),
            log,
            host="h",
            battery=s.SmaBatteryContext.from_profile(m.load_battery_profile("generic-lfp")),
            grid_power_key="meter:grid_net_w",
        )
        log.drain()  # the battery context recorded at start
        thread._write_registers(40149, (1, 2, 3, 4))  # not connected: a real write would raise
        (entry,) = log.drain()
        assert entry.event == "register_write_suppressed"
        assert (entry.data["address"], entry.data["values"]) == (40149, (1, 2, 3, 4))
        assert fake_client.write_attempts == 0

    def test_monitor_code_has_one_write_path_and_it_is_guarded(self) -> None:
        import inspect
        import re

        source = inspect.getsource(m)
        # the module never calls a Modbus client write method itself
        assert not re.search(r"\.write_(registers?|coils?)\(", source)
        # the only way to the inverter is the write of the control thread, behind the switch
        assert source.count("super()._write_registers(") == 1
        guarded = inspect.getsource(m.DiagnosticsControl._write_registers)
        assert guarded.index("if not self.write_registers") < guarded.index("super()._write_registers(")


# ---------------------------------------------------------------------------
# Live mode: writes are sent
# ---------------------------------------------------------------------------
class TestLiveMode:
    def test_writes_are_sent_to_the_inverter(self, fake_client: type[FakeClient]) -> None:
        fake_client.allow_writes = True
        code, output = run(mode="FORCED_CHARGE", factor=0.6, write_registers=True)
        assert code == 0
        assert "LIVE" in output and "[written]" in output and "[dry run]" not in output
        setpoints = [values for address, values in fake_client.writes if address == s.REG_WCTL_SPT]
        assert setpoints and s._decode_s32(setpoints[0][:2]) == -3000

    def test_safe_state_is_written_on_exit(self, fake_client: type[FakeClient]) -> None:
        fake_client.allow_writes = True
        run(mode="FORCED_CHARGE", write_registers=True)
        addresses = [address for address, _ in fake_client.writes]
        # last: external control inactive, open window, OpMod "Auto"
        assert addresses[-3:] == [s.REG_WCTL_ACT, s.REG_BMS_BLOCK, s.REG_BMS_OPMOD]
        assert s._decode_u32(fake_client.writes[-1][1]) == s.OPMOD_AUTO

    def test_failed_write_is_reported(self, fake_client: type[FakeClient]) -> None:
        fake_client.allow_writes = True
        fake_client.fail_writes = True
        code, output = run(mode="FORCED_CHARGE", write_registers=True)
        assert code == 0
        assert "[failed]" in output
        assert "ERROR entering the safe state" in output
        assert fake_client.writes == []

    def test_configuration_says_that_writes_are_sent(self) -> None:
        _, output = run(write_registers=True)
        assert "LIVE — writes are sent to the inverter" in output


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
class TestStartupReport:
    def test_battery_limits_are_reported(self) -> None:
        _, output = run()
        assert "== Startup: read on connect" in output
        assert "Bat.CapacRtgWh" in output and "10000 Wh" in output
        assert "5000 W" in output and "4500 W" in output
        assert "'capacity_wh': 10000" in output

    def test_limits_are_read_in_one_holding_block(self, fake_client: type[FakeClient]) -> None:
        run()
        assert fake_client.reads[0] == ("holding", s.REG_BAT_NOM_CAPACITY, 6)

    def test_pv_limit_registers_only_if_configured(self) -> None:
        _, without = run()
        _, with_pv = run(pv_rated_power_w=10000.0)
        startup = lambda text: text.split("== Startup")[1].split("== Extra")[0]  # noqa: E731
        assert "WNomPrc" not in startup(without)
        assert "WNomPrc" in startup(with_pv) and "plant control" in startup(with_pv)
        assert "PV limit usable: True, in force: 100 %" in with_pv

    def test_pv_limit_not_usable_without_plant_control(self, fake_client: type[FakeClient]) -> None:
        fake_client.registers.update({s.REG_PV_WMOD + 1: 303})
        _, output = run(pv_rated_power_w=10000.0)
        assert "PV limit usable: False" in output

    def test_pv_limit_in_force_is_reported(self, fake_client: type[FakeClient]) -> None:
        fake_client.registers[s.REG_PV_LIMIT_PERCENT] = 40
        _, output = run(pv_rated_power_w=10000.0)
        assert "in force: 40 %" in output

    def test_transactions_are_listed_with_their_time(self) -> None:
        _, output = run()
        assert "holding  40187 x6" in output and " ms " in output

    def test_unreachable_inverter_is_an_error_with_exit_code_2(self, fake_client: type[FakeClient]) -> None:
        fake_client.connectable = False
        code, output = run()
        assert code == 2
        assert "ERROR connecting" in output

    def test_missing_battery_is_an_error_with_exit_code_2(self, fake_client: type[FakeClient]) -> None:
        fake_client.registers.update(dict(zip((s.REG_BAT_MAX_CHARGE, s.REG_BAT_MAX_CHARGE + 1), u32(0xFFFFFFFF))))
        code, output = run()
        assert code == 2 and "battery limits" in output

    def test_configuration_is_reported_first(self) -> None:
        _, output = run(mode="NON_EXPORT", factor=0.5, grid_power_key="meter1:grid_net_w")
        assert output.index("== Configuration") < output.index("== Startup")
        assert "mode: NON_EXPORT" in output and "operation factor: 0.5" in output
        assert "grid power key: meter1:grid_net_w" in output
        assert "kp = 0.7" in output  # the tuning of the grid controller
        assert "none: the controller sees no grid power" in output
        assert "register write mode: DRY RUN" in output


# ---------------------------------------------------------------------------
# Control cycle
# ---------------------------------------------------------------------------
class TestControlCycle:
    def test_registers_read_for_control_are_decoded(self) -> None:
        _, output = run()
        cycle = output.split("== Control cycle 1")[1]
        assert "GridMs.TotW" in cycle and "-350 W" in cycle
        assert "Bat.ChaStt" in cycle and "63 %" in cycle
        assert "Bat.Diag.TmpVal" in cycle and "21.5 degC" in cycle
        assert "BatChrg.CurBatCha" in cycle and "800 W" in cycle

    def test_derived_values_are_the_ones_the_controller_uses(self) -> None:
        _, output = run()
        cycle = output.split("== Control cycle 1")[1]
        assert "PV DC power W (A + B): 4500" in cycle
        assert "battery power W (discharge +, charge -): -800" in cycle
        assert "SoC %: 63" in cycle

    def test_the_controller_reads_with_four_block_transactions(self) -> None:
        _, output = run()
        cycle = output.split("== Control cycle 1")[1]
        assert "input    30773 x4" in cycle
        assert "input    30961 x2" in cycle
        assert "input    30845 x6" in cycle
        assert "input    31393 x4" in cycle
        assert "'modbus transactions': 4" in cycle

    def test_every_cycle_reports_its_reads_even_without_a_pause(self) -> None:
        # the sleep is stubbed: a cycle follows the previous one immediately, the controller
        # would reuse a reading younger than 0.5 s, the report must still show real reads
        _, output = run(count=3)
        cycles = output.split("== Control cycle")[1:]
        assert len(cycles) == 3
        for cycle in cycles:
            assert "ERROR not read" not in cycle
            assert "'modbus transactions': 4" in cycle

    def test_timing_is_reported(self) -> None:
        _, output = run()
        assert "'modbus ms'" in output and "'cycle ms'" in output
        assert "'inverter read ms'" in output and "modbus stats since start" in output

    def test_cycles_are_numbered_and_counted(self) -> None:
        _, output = run(count=3)
        assert "== Control cycle 1" in output and "== Control cycle 3" in output
        assert "== Control cycle 4" not in output

    def test_published_values_are_reported(self) -> None:
        _, output = run()
        assert "bat_soc_factor" in output

    def test_error_in_a_cycle_is_reported_not_raised(self, fake_client: type[FakeClient]) -> None:
        del fake_client.registers[s.REG_DC2_POWER]
        code, output = run(count=2)
        assert code == 0
        assert output.count("ERROR OSError") >= 2  # in the cycle and in the transaction list


class TestRegisterWrites:
    """What the controller writes (dry run: would write), from the diagnostic log."""

    # NON_EXPORT uses the grid power controller ("pid" method), SELF_CONSUMPTION lets the
    # inverter regulate itself ("delegated" method).

    def test_first_pid_cycle_opens_the_window_and_sets_the_setpoint(self) -> None:
        shared = ControlSharedDataStore()
        shared.publish_sync({"meter:grid_net_w": 800.0})  # import of 800 W
        _, output = run(mode="NON_EXPORT", shared=shared)
        cycle = output.split("== Control cycle 1")[1]
        assert "register writes:" in cycle
        assert cycle.count("[dry run]") == 4  # deactivate, window, OpMod, setpoint
        assert "external control inactive" in cycle
        assert "charge window 0..5000 W, discharge window 0..4500 W, grid setpoint 0 W" in cycle
        assert "OpMod 1438 = auto (dynamic)" in cycle
        assert "external control active" in cycle
        assert "grid power in the shared data store: 800.0 W (import +)" in cycle
        assert "'grid_power_w': -800.0" in cycle  # what the controller sees: export (+)

    def test_later_cycles_write_only_the_setpoint(self) -> None:
        shared = ControlSharedDataStore()
        shared.publish_sync({"meter:grid_net_w": 800.0})
        _, output = run(mode="NON_EXPORT", count=2, shared=shared)
        second = output.split("== Control cycle 2")[1]
        assert second.count("battery setpoint") == 1
        assert "charge window" not in second

    def test_self_consumption_writes_the_delegated_window(self) -> None:
        shared = ControlSharedDataStore()
        shared.publish_sync({"meter:grid_net_w": 800.0})
        _, output = run(mode="SELF_CONSUMPTION", shared=shared)
        cycle = output.split("== Control cycle 1")[1]
        assert cycle.count("[dry run]") == 3  # deactivate, window, OpMod
        assert "external control inactive" in cycle
        # generic-lfp at 21.5 degC allows more than the inverter limits: full window
        assert "charge window 0..5000 W, discharge window 0..4500 W, grid setpoint 0 W" in cycle
        assert "OpMod 1438 = auto (dynamic)" in cycle
        assert "battery setpoint" not in cycle
        assert "external control active" not in cycle

    def test_self_consumption_does_not_read_the_grid_power(self) -> None:
        _, output = run(mode="SELF_CONSUMPTION")  # no grid power in the shared data store
        cycle = output.split("== Control cycle 1")[1]
        assert "grid power read by the controller: not read in this mode" in cycle
        assert "'reason': 'missing'" not in cycle
        assert "discharge window 0..4500 W" in cycle  # the inverter still regulates

    def test_self_consumption_window_is_limited_by_the_battery_profile(
        self, fake_client: type[FakeClient]
    ) -> None:
        # generic-lfp at 5 degC: charge 0.1 C, discharge 0.3 C of 10000 Wh
        put = dict(zip((s.REG_BAT_TEMPERATURE, s.REG_BAT_TEMPERATURE + 1), s32(50)))
        fake_client.registers.update(put)
        _, output = run(mode="SELF_CONSUMPTION")
        cycle = output.split("== Control cycle 1")[1]
        assert "charge window 0..1000 W, discharge window 0..3000 W, grid setpoint 0 W" in cycle

    def test_self_consumption_decision_is_listed_from_the_diagnostic_log(self) -> None:
        _, output = run(mode="SELF_CONSUMPTION")
        cycle = output.split("== Control cycle 1")[1]
        assert "command_received: mode=SELF_CONSUMPTION" in cycle
        assert "control_decision: mode=SELF_CONSUMPTION, lane=delegated" in cycle
        assert "pid_calculation:" not in cycle

    def test_forced_charge_writes_the_derated_setpoint(self) -> None:
        _, output = run(mode="FORCED_CHARGE", factor=0.6)
        assert "battery setpoint -3000 W" in output

    def test_forced_discharge_writes_the_setpoint(self) -> None:
        _, output = run(mode="FORCED_DISCHARGE", factor=0.5)
        assert "battery setpoint 2250 W" in output

    def test_idle_would_pause_the_battery(self) -> None:
        _, output = run(mode="IDLE")
        assert "OpMod 303 = pause" in output
        assert "charge window 0..0 W, discharge window 0..0 W" in output

    def test_grid_support_would_set_the_grid_setpoint(self) -> None:
        _, export = run(mode="GRID_SUPPORT_EXPORT", factor=0.5)
        _, imported = run(mode="GRID_SUPPORT_IMPORT", factor=0.4)
        assert "grid setpoint 2250 W" in export
        assert "grid setpoint -2000 W" in imported

    def test_without_grid_power_the_battery_is_held(self) -> None:
        _, output = run(mode="NON_EXPORT")
        cycle = output.split("== Control cycle 1")[1]
        assert "grid power: not in the shared data store" in cycle
        assert "'reason': 'missing'" in cycle
        assert "battery setpoint 0 W" in cycle
        assert "controller inputs complete: False" in cycle

    def test_stale_grid_power_is_not_usable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from akkudoktoreos.adapter.controllers import controlabc

        clock: Any = types.SimpleNamespace(t=1000.0)
        clock.monotonic = lambda: clock.t
        monkeypatch.setattr(controlabc, "time", clock)
        monkeypatch.setattr(s, "time", clock)
        shared = ControlSharedDataStore()
        shared.publish_sync({"meter:grid_net_w": 800.0})
        clock.t += 10.0
        _, output = run(mode="NON_EXPORT", shared=shared)
        assert "'reason': 'stale'" in output
        assert "battery setpoint 0 W" in output

    def test_non_export_with_pv_limit_dry_run_runs(self) -> None:
        shared = ControlSharedDataStore()
        shared.publish_sync({"meter:grid_net_w": -3000.0})
        code, output = run(mode="NON_EXPORT", pv_rated_power_w=10000.0, count=3, shared=shared)
        assert code == 0 and "PV limit" in output

    def test_controller_decisions_are_listed_from_the_diagnostic_log(self) -> None:
        shared = ControlSharedDataStore()
        shared.publish_sync({"meter:grid_net_w": 800.0})
        _, output = run(mode="NON_EXPORT", shared=shared)
        cycle = output.split("== Control cycle 1")[1]
        assert "controller events (diagnostic log):" in cycle
        assert "command_received: mode=NON_EXPORT" in cycle
        assert "pid_calculation:" in cycle and "setpoint_calculated:" in cycle

    def test_describe_write_of_unknown_register(self) -> None:
        assert m.describe_write(12345, (1, 2)) == "2 register(s): [1, 2]"

    def test_describe_write_of_the_pv_limit(self) -> None:
        assert m.describe_write(s.REG_PV_LIMIT_PERCENT, (11,)) == "PV limit 11 % of the rated AC power"


class TestWritesFromEvents:
    """The register writes are rebuilt from the diagnostic events."""

    @staticmethod
    def entry(event: str, **data: Any) -> Any:
        from akkudoktoreos.adapter.controllers.controlabc import ControlDiagnosticEntry
        from akkudoktoreos.utils.datetimeutil import to_datetime

        return ControlDiagnosticEntry(to_datetime(), event, data)

    def test_outcomes(self) -> None:
        e = self.entry
        writes = m.writes_from_events(
            [
                e("register_write", address=40151, values=(0, 803)),
                e("register_write_suppressed", address=40151, values=(0, 803)),
                e("register_write", address=40149, values=(0, 100, 0, 802)),
                e("modbus_write", address=40149, values=(0, 100, 0, 802), error=None, ms=2.5),
                e("register_write", address=40236, values=(0, 1438)),
                e("modbus_write", address=40236, values=(0, 1438), error="OSError: x", ms=1.0),
                e("register_write_error", address=40236, values=(0, 1438), error="x"),
            ]
        )
        assert [(w.address, w.action) for w in writes] == [
            (40151, "dry run"),
            (40149, "written"),
            (40236, "failed"),
        ]
        assert writes[1].ms == 2.5 and writes[2].error == "OSError: x"
        assert writes[0].meaning == "external control inactive"

    def test_events_without_a_preceding_write_are_ignored(self) -> None:
        e = self.entry
        assert m.writes_from_events([e("modbus_write", address=1, values=(1,), error=None, ms=1.0)]) == []


# ---------------------------------------------------------------------------
# Extra information
# ---------------------------------------------------------------------------
class TestExtras:
    def test_known_registers_are_decoded(self) -> None:
        _, output = run()
        extras = output.split("== Extra information")[1].split("== Control cycle")[0]
        assert "Operation.Health" in extras and "307 = ok" in extras
        assert "Operation.GriSwStt" in extras and "51 = closed" in extras
        assert "Bat.Vol" in extras and "512 V" in extras
        assert "Coolsys.Cab.TmpVal" in extras and "41.2 degC" in extras
        assert "Bat.ChaCtlComAval" in extras and "1129 = yes" in extras
        assert "Bat.Type" in extras and "1785 = lithium" in extras
        assert "Operation.RunStt" in extras and "235 = grid parallel operation" in extras

    def test_registers_the_inverter_does_not_know_are_reported_not_fatal(self) -> None:
        code, output = run()
        extras = output.split("== Extra information")[1].split("== Control cycle")[0]
        assert code == 0
        assert "SerNum" in extras and "ERROR OSError" in extras

    def test_curtailment_is_flagged(self, fake_client: type[FakeClient]) -> None:
        fake_client.registers.update(dict(zip((33003, 33004), u32(2119))))
        _, output = run()
        assert "!! the inverter reports CURTAILMENT" in output
        assert "2119 = CURTAILMENT" in output

    def test_no_curtailment_flag_in_normal_operation(self) -> None:
        _, output = run()
        assert "!! the inverter reports CURTAILMENT" not in output

    def test_control_registers_are_read_back_and_labelled_volatile(self) -> None:
        _, output = run()
        extras = output.split("== Extra information")[1].split("== Control cycle")[0]
        assert "external battery setpoint" in extras and "CmpBMS.GridWSpt" in extras
        assert "volatile" in extras

    def test_not_available_values_are_shown_as_n_a(self, fake_client: type[FakeClient]) -> None:
        fake_client.registers.update(dict(zip((33003, 33004), u32(16777213))))
        _, output = run()
        assert "Operation.RunStt" in output and "n/a" in output

    def test_extras_are_read_again_after_the_extra_interval(self, fake_client: type[FakeClient]) -> None:
        _, once = run(count=3, extra_interval_s=1e9)
        _, always = run(count=3, extra_interval_s=0.0)
        assert once.count("== Extra information") == 1
        assert always.count("== Extra information") == 3

    def test_reading_the_extras_does_not_show_up_in_a_cycle(self) -> None:
        _, output = run()
        cycle = output.split("== Control cycle 1")[1]
        assert "'modbus transactions': 4" in cycle  # the reads of the extras were drained


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------
class TestDecoding:
    @pytest.mark.parametrize(
        "dtype, words, expected",
        [
            ("u16", [65535], 65535),
            ("s16", [65535], -1),
            ("s16", [100], 100),
            ("u32", [1, 2], 65538),
            ("s32", [0xFFFF, 0xFFFF], -1),
            ("s32", [0x0000, 0x0064], 100),
            ("s32", [0x8000, 0x0000], -(2**31)),
        ],
    )
    def test_decode_raw(self, dtype: str, words: list[int], expected: int) -> None:
        assert m.decode_raw(dtype, words) == expected

    def test_not_available_values(self) -> None:
        spec = m.RegisterSpec("x", 1, "s32", "W")
        assert m.make_value(spec, [0x8000, 0]).text == "n/a"
        assert m.make_value(m.RegisterSpec("x", 1, "u32"), [0xFFFF, 0xFFFF]).text == "n/a"
        assert m.make_value(m.RegisterSpec("x", 1, "u32"), u32(16777213)).text == "n/a"

    def test_scale_unit_and_enum(self) -> None:
        spec = m.RegisterSpec("t", 1, "s32", "degC", 0.1, enum={215: "x"})
        value = m.make_value(spec, s32(215))
        assert (value.raw, value.value, value.text) == (215, pytest.approx(21.5), "21.5 degC = x")

    def test_enum_without_a_match_shows_only_the_number(self) -> None:
        spec = m.RegisterSpec("m", 1, "u32", enum={1: "one"})
        assert m.make_value(spec, u32(7)).text == "7"

    def test_values_from_transactions_picks_the_covering_block(self) -> None:
        block = m.Transaction("input", 100, 4, words=u32(11) + s32(-22))
        spec_a = m.RegisterSpec("a", 100, "u32")
        spec_b = m.RegisterSpec("b", 102, "s32")
        a, b = m.values_from_transactions([spec_a, spec_b], [block])
        assert (a.raw, b.raw) == (11, -22)

    def test_values_from_transactions_reports_the_error_of_a_failed_block(self) -> None:
        failed = m.Transaction("input", 100, 4, error="OSError: illegal data address")
        value = m.values_from_transactions([m.RegisterSpec("a", 100, "u32")], [failed])[0]
        assert value.error == "OSError: illegal data address"

    def test_values_from_transactions_of_a_register_that_was_not_read(self) -> None:
        value = m.values_from_transactions([m.RegisterSpec("a", 100, "u32")], [])[0]
        assert value.error == "not read"

    def test_kind_must_match(self) -> None:
        block = m.Transaction("holding", 100, 2, words=u32(5))
        value = m.values_from_transactions([m.RegisterSpec("a", 100, "u32", kind="input")], [block])[0]
        assert value.error == "not read"

    def test_catalogue_uses_the_register_addresses_of_the_controller(self) -> None:
        addresses = {spec.address for spec in m.CONTROL_REGISTERS}
        assert {s.REG_DC1_POWER, s.REG_AC_POWER, s.REG_DC2_POWER, s.REG_BAT_SOC, s.REG_BAT_TEMPERATURE} <= addresses
        assert {spec.address for spec in m.STARTUP_REGISTERS} == {
            s.REG_BAT_NOM_CAPACITY,
            s.REG_BAT_MAX_CHARGE,
            s.REG_BAT_MAX_DISCHARGE,
        }

    def test_control_catalogue_is_what_the_controller_reads(self) -> None:
        # every register the controller reads in a cycle is in the catalogue, and the other way round
        lines: list[str] = []
        run(out=lines, as_json=True)
        cycle = next(json.loads(line) for line in lines if json.loads(line)["section"] == "cycle")["data"]
        for transaction in cycle["transactions"]:
            first, count = transaction["address"], transaction["count"]
            assert any(first <= spec.address < first + count for spec in m.CONTROL_REGISTERS), transaction
        assert all(register["error"] is None for register in cycle["registers"])
        assert len(cycle["registers"]) == len(m.CONTROL_REGISTERS)


# ---------------------------------------------------------------------------
# Output formats and command line
# ---------------------------------------------------------------------------
class TestOutput:
    def test_json_output_is_one_object_per_line(self) -> None:
        lines: list[str] = []
        code, _ = run(out=lines, as_json=True, count=2)
        assert code == 0
        objects = [json.loads(line) for line in lines]
        sections = [o["section"] for o in objects]
        assert sections[:3] == ["configuration", "startup", "extras"]
        assert sections.count("cycle") == 2

    def test_json_cycle_contains_the_values_and_the_writes(self) -> None:
        lines: list[str] = []
        run(out=lines, as_json=True, mode="FORCED_CHARGE", factor=0.6)
        cycle = next(json.loads(line) for line in lines if json.loads(line)["section"] == "cycle")["data"]
        assert cycle["registers"][1]["name"] == "GridMs.TotW" and cycle["registers"][1]["raw"] == -350
        assert any("battery setpoint -3000 W" in w["meaning"] for w in cycle["writes"])
        assert {w["action"] for w in cycle["writes"]} == {"dry run"}
        assert cycle["timing"]["modbus transactions"] == 4

    def test_json_cycle_contains_the_diagnostic_events(self) -> None:
        lines: list[str] = []
        run(out=lines, as_json=True, mode="FORCED_CHARGE")
        cycle = next(json.loads(line) for line in lines if json.loads(line)["section"] == "cycle")["data"]
        events = {event["event"] for event in cycle["events"]}
        assert {"command_received", "setpoint_calculated", "readings_updated", "modbus_read"} <= events
        assert cycle["events"][0]["date_time"]  # ISO string

    def test_json_startup_contains_the_transactions(self) -> None:
        lines: list[str] = []
        run(out=lines, as_json=True)
        startup = next(json.loads(line) for line in lines if json.loads(line)["section"] == "startup")["data"]
        assert startup["transactions"][0]["address"] == s.REG_BAT_NOM_CAPACITY
        assert startup["battery limits"]["capacity_wh"] == 10000


class TestMeter:
    def test_meter_thread_is_started_and_stopped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events: list[str] = []
        created: list[dict[str, Any]] = []

        class FakeMeter:
            def __init__(self, device_id: str, shared: Any, diagnostic_log: Any, **kwargs: Any) -> None:
                arguments = {
                    "shared": shared,
                    "diagnostic_log": diagnostic_log,
                }
                arguments.update(kwargs)
                events.append(f"create {device_id} {arguments}")
                created.append(arguments)

            def start(self) -> None:
                events.append("start")

            def stop(self) -> None:
                events.append("stop")

        module = types.ModuleType("akkudoktoreos.adapter.controllers.sma.smaenergymeter")
        module.SmaEnergyMeterThread = FakeMeter  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, module.__name__, module)
        code, output = run(meter_serial=1900123456, meter_interface_ip="192.168.178.10", grid_power_key="meter1:grid_net_w")
        assert code == 0
        assert events[0].startswith("create meter1") and "1900123456" in events[0] and "192.168.178.10" in events[0]
        assert events[1:] == ["start", "stop"]
        assert "SMA Energy Meter 1900123456" in output
        # the meter has a diagnostic log of its own, disabled: it does not mix into the inverter's
        meter_log = created[0]["diagnostic_log"]
        assert isinstance(meter_log, ControlDiagnosticLog) and not meter_log.enabled

    def test_meter_is_stopped_even_if_the_inverter_is_unreachable(
        self, monkeypatch: pytest.MonkeyPatch, fake_client: type[FakeClient]
    ) -> None:
        events: list[str] = []

        class FakeMeter:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            def start(self) -> None:
                events.append("start")

            def stop(self) -> None:
                events.append("stop")

        module = types.ModuleType("akkudoktoreos.adapter.controllers.sma.smaenergymeter")
        module.SmaEnergyMeterThread = FakeMeter  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, module.__name__, module)
        fake_client.connectable = False
        code, _ = run(meter_serial=1)
        assert code == 2 and events == ["start", "stop"]


class TestCommandLine:
    def test_parse_arguments(self) -> None:
        options = m.parse_args(
            ["--host", "h", "--port", "1502", "--unit-id", "5", "--mode", "NON_EXPORT", "--factor", "0.5",
             "--meter-serial", "42", "--pv-rated-power-w", "8000", "--interval", "2", "--count", "3", "--json"]
        )
        assert (options.host, options.port, options.unit_id) == ("h", 1502, 5)
        assert (options.mode, options.factor, options.meter_serial) == ("NON_EXPORT", 0.5, 42)
        assert (options.pv_rated_power_w, options.interval_s, options.count, options.as_json) == (8000.0, 2.0, 3, True)
        assert not options.write_registers

    def test_write_registers_switch(self) -> None:
        assert m.parse_args(["--host", "h", "--write-registers"]).write_registers

    def test_defaults(self) -> None:
        options = m.parse_args(["--host", "h"])
        assert options.mode == "SELF_CONSUMPTION" and options.interval_s == 5.0 and options.count == 0
        assert options.meter_serial is None and options.pv_rated_power_w is None and not options.as_json

    def test_host_is_required(self) -> None:
        with pytest.raises(SystemExit):
            m.parse_args([])

    @pytest.mark.parametrize("factor", ["-0.1", "1.5"])
    def test_factor_must_be_in_range(self, factor: str) -> None:
        with pytest.raises(SystemExit):
            m.parse_args(["--host", "h", "--factor", factor])

    def test_unknown_mode_is_rejected(self) -> None:
        with pytest.raises(SystemExit):
            m.parse_args(["--host", "h", "--mode", "NOT_A_MODE"])

    def test_old_name_of_the_options_is_still_available(self) -> None:
        assert m.DiagnosticsOptions is m.MonitorOptions

    def test_main_returns_the_exit_code(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert m.main(["--host", "h", "--count", "1", "--interval", "0"]) == 0
        assert "== Control cycle 1" in capsys.readouterr().out

    def test_main_returns_2_if_the_inverter_is_unreachable(
        self, capsys: pytest.CaptureFixture[str], fake_client: type[FakeClient]
    ) -> None:
        fake_client.connectable = False
        assert m.main(["--host", "h", "--count", "1"]) == 2
        assert "ERROR connecting" in capsys.readouterr().out
