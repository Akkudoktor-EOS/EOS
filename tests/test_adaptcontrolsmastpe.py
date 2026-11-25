"""Tests for the SMA STP-SE controller (smastpe.py).

No inverter and no network is needed: `FakeInverter` stands in for the device at register level.
It answers the register reads of the control thread from a small plant model (PV, load, battery)
and logs every register write, so the tests can check what is written and in which order.

The Modbus connection is replaced, the clock of the thread and of the shared data store is a
virtual clock, so the simulations of minutes and hours run in milliseconds.
"""

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional, cast

import pytest

from akkudoktoreos.adapter.controllers import controlabc
from akkudoktoreos.adapter.controllers.controlabc import (
    ControlDiagnosticLog,
    ControlSharedDataStore,
)
from akkudoktoreos.adapter.controllers.controlmodbus import (
    ModbusTcpControllerThreadBase,
)
from akkudoktoreos.adapter.controllers.sma import smastpe as s
from akkudoktoreos.adapter.controllers.sma.smastpepid import PvLimiterSettings
from akkudoktoreos.core.emplan import EnergyManagementPlan
from akkudoktoreos.devices.devicesabc import BatteryOperationMode as Mode
from akkudoktoreos.devices.profile.batteryprofile import (
    BatteryProfile,
    BatteryProfileSettings,
)
from akkudoktoreos.devices.settings.batterysettings import BatteriesCommonSettings
from akkudoktoreos.devices.settings.inverterresolution import (
    InverterOperatingParameters,
)
from akkudoktoreos.utils.datetimeutil import to_datetime

GRID_KEY = "meter1:grid_net_w"

# The battery profile shipped with EOS; read directly, so no configuration is needed.
GENERIC_LFP_FILE = (
    Path(s.__file__).parents[3] / "data" / "profile" / "battery" / "generic-lfp.json"
)
GENERIC_LFP_PROFILE: BatteryProfile = BatteryProfileSettings.from_json_file(GENERIC_LFP_FILE)


def generic_lfp_profile(**changes: Any) -> BatteryProfile:
    """The generic LFP profile with some document fields changed."""
    document = BatteryProfileSettings.model_validate_json(GENERIC_LFP_FILE.read_text())
    return BatteryProfileSettings.model_validate(
        {**document.model_dump(), **changes}
    ).to_limits()


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------
class Clock:
    """Virtual monotonic clock for the control thread and the shared data store."""

    def __init__(self) -> None:
        self.t = 1000.0

    def monotonic(self) -> float:
        return self.t


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(s, "time", clock)
    monkeypatch.setattr(controlabc, "time", clock)
    return clock


@pytest.fixture(autouse=True)
def no_modbus_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ModbusTcpControllerThreadBase, "_connect", lambda self: None)


def _log(capacity: int = 100) -> ControlDiagnosticLog:
    """Diagnostic log for tests; must be passed to every thread/controller."""
    return ControlDiagnosticLog(capacity=capacity)


def u32(value: float) -> list[int]:
    return list(s.ModbusTcpClient.convert_to_registers(int(value), s._DATATYPE.UINT32, word_order="big"))


def s32(value: float) -> list[int]:
    return list(s.ModbusTcpClient.convert_to_registers(int(value), s._DATATYPE.INT32, word_order="big"))


class FakeInverter:
    """STP-SE with a battery, PV and a house load.

    Power flows: battery discharge (+), charge (-). Grid export (+), import (-).
    The battery follows the external setpoint with a first-order lag, charging stops at 100 %
    SoC, the AC output is limited by the PV limit (percent of the rated power).
    """

    def __init__(
        self,
        *,
        pv_w: float = 0.0,
        load_w: float = 0.0,
        soc: float = 50.0,
        temp_c: float = 25.0,
        cap_wh: int = 10000,
        max_charge: int = 5000,
        max_discharge: int = 4500,
        rated_w: float = 10000.0,
        eff: float = 0.95,
        tau_s: float = 4.0,
        wmod: int = 1079,
        limit_percent: int = 100,
        limit_na: bool = False,
    ) -> None:
        self.pv_w, self.load_w, self.soc, self.temp_c = pv_w, load_w, soc, temp_c
        self.cap_wh, self.max_charge, self.max_discharge = cap_wh, max_charge, max_discharge
        self.rated_w, self.eff, self.tau_s = rated_w, eff, tau_s
        self.wmod, self.limit_percent, self.limit_na = wmod, limit_percent, limit_na
        self.bat_dc_w = 0.0
        self.setpoint_w = 0
        self.external_active = False
        self.writes: list[tuple[int, tuple[int, ...]]] = []
        self.limit_writes = 0
        self.advance(1e-9)  # derive AC and grid power

    def advance(self, dt: float) -> None:
        target = self.setpoint_w if self.external_active else 0
        self.bat_dc_w += (target - self.bat_dc_w) * (1.0 - math.exp(-dt / self.tau_s))
        if (self.soc >= 100 and self.bat_dc_w < 0) or (self.soc <= 0 and self.bat_dc_w > 0):
            self.bat_dc_w = 0.0
        self.soc = max(0.0, min(100.0, self.soc - self.bat_dc_w * dt / 3600.0 / self.cap_wh * 100.0))
        dc_w = self.pv_w + self.bat_dc_w
        ac_w = dc_w * self.eff if dc_w > 0 else dc_w / self.eff
        self.ac_w = min(ac_w, self.limit_percent / 100.0 * self.rated_w)
        self.grid_export_w = self.ac_w - self.load_w

    def registers(self, address: int, count: int) -> list[int]:
        regs: dict[int, int] = {}

        def put(at: int, words: list[int]) -> None:
            for k, word in enumerate(words):
                regs[at + k] = word

        put(s.REG_DC1_POWER, s32(self.pv_w))
        put(s.REG_AC_POWER, s32(self.ac_w))
        put(s.REG_DC2_POWER, s32(0))
        put(s.REG_BAT_SOC, u32(round(self.soc)))
        put(s.REG_BAT_ACT_CAPACITY, u32(self.cap_wh))
        put(s.REG_BAT_TEMPERATURE, s32(self.temp_c * 10))
        put(s.REG_BAT_CHARGE_POWER, u32(max(0.0, -self.bat_dc_w)))
        put(s.REG_BAT_DISCHARGE_POWER, u32(max(0.0, self.bat_dc_w)))
        put(s.REG_BAT_NOM_CAPACITY, u32(self.cap_wh))
        put(s.REG_BAT_MAX_CHARGE, u32(self.max_charge))
        put(s.REG_BAT_MAX_DISCHARGE, u32(self.max_discharge))
        put(s.REG_PV_WMOD, u32(self.wmod))
        regs[s.REG_PV_LIMIT_PERCENT] = 0x8000 if self.limit_na else self.limit_percent
        return [regs[address + k] for k in range(count)]

    def write(self, address: int, values: tuple[int, ...]) -> None:
        self.writes.append((address, tuple(values)))
        if address == s.REG_WCTL_SPT:
            self.setpoint_w = s._decode_s32(list(values[0:2])) or 0
            self.external_active = (values[2] << 16 | values[3]) == s.WCTL_ACTIVE
        elif address == s.REG_WCTL_ACT:
            self.external_active = (values[0] << 16 | values[1]) == s.WCTL_ACTIVE
        elif address == s.REG_PV_LIMIT_PERCENT:
            self.limit_percent = values[0]
            self.limit_writes += 1

    def addresses_written(self) -> list[int]:
        return [address for address, _ in self.writes]


def make_thread(
    inverter: FakeInverter,
    *,
    pv: Optional[PvLimiterSettings] = None,
    log: Optional[ControlDiagnosticLog] = None,
    profile: BatteryProfile | s.SmaBatterySource = GENERIC_LFP_PROFILE,
) -> tuple[s.SmaStpeControl, ControlSharedDataStore]:
    """Control thread on the fake inverter, connected (battery limits read).

    ``profile`` is a battery profile (a context without battery device), a battery context or
    a function returning one. ``log`` defaults to a fresh log; pass one if the test asserts on
    the log contents.
    """
    shared = ControlSharedDataStore()
    thread = s.SmaStpeControl(
        "inv1",
        shared,
        diagnostic_log=log or _log(),
        host="127.0.0.1",
        battery=s.SmaBatteryContext.from_profile(profile)
        if isinstance(profile, BatteryProfile)
        else profile,
        grid_power_key=GRID_KEY,
        pv_limiter_settings=pv,
    )
    thread._read_input_registers = lambda address, count=2: inverter.registers(address, count)  # type: ignore[method-assign]
    thread._read_holding_registers = lambda address, count=2: inverter.registers(address, count)  # type: ignore[method-assign]
    thread._write_registers = lambda address, values: inverter.write(address, values)  # type: ignore[method-assign]
    thread._connect()
    return thread, shared


def command(mode: Mode, factor: float = 1.0, **kwargs: Any) -> s.SmaCommand:
    return s.SmaCommand(date_time=to_datetime(), mode=mode, operation_factor=factor, **kwargs)


def publish_grid(shared: ControlSharedDataStore, inverter: FakeInverter) -> None:
    """What the energy meter does: grid power, positive when drawing from the grid."""
    shared.publish_sync({GRID_KEY: -inverter.grid_export_w})


def decode_block(values: tuple[int, ...]) -> list[Optional[int]]:
    """BatChaMinW, BatChaMaxW, BatDschMinW, BatDschMaxW, GridWSpt."""
    return [s._decode_u32(list(values[i : i + 2])) for i in range(0, 8, 2)] + [
        s._decode_s32(list(values[8:10]))
    ]


def last_delegated(inverter: FakeInverter) -> tuple[list[Optional[int]], Optional[int]]:
    """(window and grid setpoint, OpMod) of the last 6 registers method write."""
    writes = inverter.writes
    k = max(i for i, (address, _) in enumerate(writes) if address == s.REG_BMS_OPMOD)
    return decode_block(writes[k - 1][1]), s._decode_u32(list(writes[k][1]))


OPEN_WINDOW = [0, 5000, 0, 4500, 0]  # of the default fake battery, grid setpoint 0


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------
class TestRegisterHelpers:
    @pytest.mark.parametrize("value", [0, 1, 65535, 65536, 123456789, 0xFFFFFFFE])
    def test_u32_round_trip(self, value: int) -> None:
        assert s._decode_u32(list(s._encode_u32(value))) == value

    @pytest.mark.parametrize("value", [0, 1, -1, 1500, -2500, 2**31 - 1, -(2**31) + 1])
    def test_s32_round_trip(self, value: int) -> None:
        assert s._decode_s32(list(s._encode_s32(value))) == value

    def test_words_are_big_endian(self) -> None:
        assert s._encode_u32(0x00010002) == (1, 2)

    def test_not_available_values_are_none(self) -> None:
        assert s._decode_u32([0xFFFF, 0xFFFF]) is None
        assert s._decode_s32([0x8000, 0x0000]) is None
        assert s._decode_s16([0x8000]) is None

    def test_negative_s16(self) -> None:
        assert s._decode_s16([0xFFFF]) == -1

    def test_values_are_picked_from_a_block_by_their_address(self) -> None:
        block = (1000, 6)
        regs = u32(11) + u32(22) + s32(-33)
        assert s._u32_at(regs, block, 1000) == 11
        assert s._u32_at(regs, block, 1002) == 22
        assert s._s32_at(regs, block, 1004) == -33


class TestOpenDelegated:
    def test_full_window_and_grid_setpoint(self) -> None:
        limits = s._BatteryLimits(10000, 5000, 4500)
        assert s._open_delegated_params(limits) == s._DelegatedParams(s.OPMOD_AUTO, 0, 5000, 0, 4500, 0)
        assert s._open_delegated_params(limits, grid_w=-300).grid_w == -300


class TestDelegatedForMode:
    LIMITS = s._BatteryLimits(10000, 5000, 4500)

    def params(
        self,
        mode: Mode,
        factor: float = 1.0,
        *,
        charge_max_w: int = 5000,
        discharge_max_w: int = 4500,
    ) -> s._DelegatedParams:
        # The thread scales the grid setpoints by the factor from the battery power limits.
        return s._delegated_params_for_mode(
            mode,
            charge_max_w=charge_max_w,
            discharge_max_w=discharge_max_w,
            export_w=round(factor * self.LIMITS.max_discharge_w),
            import_w=round(factor * self.LIMITS.max_charge_w),
        )

    def test_idle_closes_both_battery_windows(self) -> None:
        assert self.params(Mode.IDLE) == s._DelegatedParams(
            s.OPMOD_OFF, 0, 0, 0, 0, 0
        )

    def test_self_consumption_uses_auto_with_zero_grid_setpoint(self) -> None:
        assert self.params(
            Mode.SELF_CONSUMPTION,
            0.5,
            charge_max_w=2500,
            discharge_max_w=2250,
        ) == s._DelegatedParams(
            s.OPMOD_AUTO, 0, 2500, 0, 2250, 0
        )

    def test_grid_support_export_scales_grid_setpoint(self) -> None:
        params = self.params(Mode.GRID_SUPPORT_EXPORT, 0.5)
        assert params == s._DelegatedParams(
            s.OPMOD_AUTO, 0, 5000, 0, 4500, 2250
        )

    def test_grid_support_import_scales_grid_setpoint(self) -> None:
        params = self.params(Mode.GRID_SUPPORT_IMPORT, 0.4)
        assert params == s._DelegatedParams(
            s.OPMOD_AUTO, 0, 5000, 0, 4500, -2000
        )

    def test_fault_hands_over_to_the_inverter(self) -> None:
        assert self.params(Mode.FAULT) == s._open_delegated_params(self.LIMITS)

    @pytest.mark.parametrize(
        "mode",
        [Mode.NON_EXPORT, Mode.FORCED_CHARGE, Mode.FORCED_DISCHARGE],
    )
    def test_non_delegated_modes_are_rejected(self, mode: Mode) -> None:
        with pytest.raises(ValueError):
            self.params(mode)

    @pytest.mark.parametrize("factor", [0.0, 0.25, 1.0])
    def test_grid_support_scales_linearly(self, factor: float) -> None:
        assert (
            self.params(Mode.GRID_SUPPORT_EXPORT, factor).grid_w
            == round(factor * 4500)
        )
        assert (
            self.params(Mode.GRID_SUPPORT_IMPORT, factor).grid_w
            == -round(factor * 5000)
        )




# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def make_settings(**overrides: Any) -> s.SmaStpeSettings:
    values: dict[str, Any] = {
        "controller_type": "sma:sunny-tripower-smart-energy",
        "host": "192.168.178.6",
        "battery_id": "battery1",
        "grid_power_key": GRID_KEY,
    }
    values.update(overrides)
    return s.SmaStpeSettings(**values)


class TestSettings:
    def test_defaults(self) -> None:
        settings = make_settings()
        assert settings.controller_type == "sma:sunny-tripower-smart-energy"
        assert (settings.port, settings.unit_id, settings.grid_power_max_age_s) == (502, 3, 3.0)
        assert settings.pv_curtailment is None

    def test_controller_is_bound_to_the_settings(self) -> None:
        assert s.SmaStpeSettings.controller_class() is s.SmaStpe

    def test_provided_keys_are_reported(self) -> None:
        assert make_settings().shared_data_keys == s.INVERTER_KEYS

    @pytest.mark.parametrize("key", ["no-colon", ":missing-device", "missing-key:", "two:colons:x", "a b:c"])
    def test_malformed_grid_power_key_is_rejected(self, key: str) -> None:
        with pytest.raises(ValueError):
            make_settings(grid_power_key=key)

    def test_battery_id_and_host_are_required(self) -> None:
        with pytest.raises(ValueError, match="battery_id"):
            s.SmaStpeSettings(  # type: ignore[call-arg]
                controller_type="sma:sunny-tripower-smart-energy", host="h", grid_power_key=GRID_KEY
            )
        with pytest.raises(ValueError, match="host"):
            s.SmaStpeSettings(  # type: ignore[call-arg]
                controller_type="sma:sunny-tripower-smart-energy",
                battery_id="b",
                grid_power_key=GRID_KEY,
            )

    def test_pv_curtailment_defaults(self) -> None:
        pv = make_settings(pv_curtailment={"rated_power_w": 10000}).pv_curtailment
        assert pv is not None
        assert (pv.headroom_w, pv.deadband_w, pv.hold_s) == (100.0, 50.0, 60.0)
        assert (pv.min_write_interval_s, pv.step_percent, pv.keepalive_s) == (60.0, 2, None)

    @pytest.mark.parametrize(
        "invalid",
        [
            {"rated_power_w": 0},
            {"rated_power_w": 10000, "min_write_interval_s": 5},
            {"rated_power_w": 10000, "step_percent": 0},
            {"rated_power_w": 10000, "hold_s": 0},
            {"rated_power_w": 10000, "keepalive_s": 0},
            {"rated_power_w": 10000, "headroom_w": -1},
        ],
    )
    def test_invalid_pv_curtailment_is_rejected(self, invalid: dict[str, float]) -> None:
        with pytest.raises(ValueError):
            make_settings(pv_curtailment=invalid)

    def test_settings_have_the_same_field_values_if_created_alike(self) -> None:
        # Model instances are not compared with ==: the settings base class of EOS may make two
        # instances with the same values unequal. The controller compares the dumped values.
        assert make_settings().model_dump() == make_settings().model_dump()
        assert make_settings().model_dump() != make_settings(host="192.168.178.7").model_dump()
        pv = {"rated_power_w": 10000}
        assert make_settings().model_dump() != make_settings(pv_curtailment=pv).model_dump()


class TestSettingsSnapshot:
    """The adapter restarts a controller when its configuration changed."""

    @staticmethod
    def make_controller(settings: s.SmaStpeSettings) -> s.SmaStpe:
        return s.SmaStpe(
            "inv1",
            ControlSharedDataStore(),
            _log(),
            settings,
        )

    def test_same_values_are_not_a_change(self) -> None:
        controller = self.make_controller(make_settings())
        assert not controller.settings_changed(make_settings())

    @pytest.mark.parametrize(
        "change",
        [
            {"host": "192.168.178.7"},
            {"port": 1502},
            {"battery_id": "battery2"},
            {"grid_power_key": "meter2:grid_net_w"},
            {"pv_curtailment": {"rated_power_w": 10000}},
            {"measurement_max_age_s": 5.0},
        ],
    )
    def test_changed_values_are_a_change(self, change: dict[str, Any]) -> None:
        controller = self.make_controller(make_settings())
        assert controller.settings_changed(make_settings(**change))

    def test_controller_keeps_a_snapshot_of_its_settings(self) -> None:
        configured = make_settings()
        controller = self.make_controller(configured)
        assert controller.settings is not configured

    def test_change_of_the_configuration_in_place_is_noticed(self) -> None:
        configured = make_settings()
        controller = self.make_controller(configured)
        configured.host = "192.168.178.99"  # the configuration is edited in place
        assert controller.settings.host == "192.168.178.6"
        assert controller.settings_changed(configured)

    def test_change_of_a_nested_setting_in_place_is_noticed(self) -> None:
        configured = make_settings(pv_curtailment={"rated_power_w": 10000})
        controller = self.make_controller(configured)
        assert configured.pv_curtailment is not None
        configured.pv_curtailment.hold_s = 120.0
        assert controller.settings_changed(configured)

    def test_settings_instances_that_are_not_equal_objects_are_still_no_change(self) -> None:
        # what the settings base class of EOS may do: private state per instance
        class Unequal(s.SmaStpeSettings):
            def __eq__(self, other: object) -> bool:  # noqa: D105
                return False

            __hash__ = None  # type: ignore[assignment]

        controller = self.make_controller(
            Unequal(
                controller_type="sma:sunny-tripower-smart-energy",
                host="h",
                battery_id="b",
                grid_power_key=GRID_KEY,
            )
        )
        assert not controller.settings_changed(
            Unequal(
                controller_type="sma:sunny-tripower-smart-energy",
                host="h",
                battery_id="b",
                grid_power_key=GRID_KEY,
            )
        )


# ---------------------------------------------------------------------------
# Plan conversion (controller, EOS server side)
# ---------------------------------------------------------------------------
@dataclass
class FakeInstruction:
    resource_id: str
    execution_time: Any
    operation_mode_id: str
    operation_mode_factor: float = 1.0


@dataclass
class FakePlan:
    instructions: list[Any]
    valid_until: Any = None


@pytest.fixture
def controller(monkeypatch: pytest.MonkeyPatch) -> s.SmaStpe:
    monkeypatch.setattr(s, "FRBCInstruction", FakeInstruction)
    return s.SmaStpe(
        "inv1",
        ControlSharedDataStore(),
        _log(),
        make_settings(),
    )


def plan_commands(controller: s.SmaStpe, plan: Optional[FakePlan]) -> list[s.SmaCommand]:
    """Commands of a fake plan (duck-typed as an EnergyManagementPlan)."""
    return cast(
        list[s.SmaCommand],
        controller._emplan_to_commands(cast(Optional[EnergyManagementPlan], plan)),
    )


def at(minutes: int) -> Any:
    return to_datetime("2026-10-26 12:00:00").add(minutes=minutes)


class TestPlanConversion:
    def test_controller_provides_the_inverter_keys(self, controller: s.SmaStpe) -> None:
        assert controller.shared_data_keys() == tuple("inv1:" + key for key in s.INVERTER_KEYS)

    def test_thread_publishes_what_the_controller_declares(self, controller: s.SmaStpe) -> None:
        thread = controller._create_control_thread()
        assert sorted(thread.published_keys()) == sorted(controller.shared_data_keys())

    def test_empty_plan_gives_an_empty_schedule(self, controller: s.SmaStpe) -> None:
        assert plan_commands(controller, None) == []
        assert plan_commands(controller, FakePlan([], valid_until=at(60))) == []

    def test_instructions_of_other_resources_are_ignored(self, controller: s.SmaStpe) -> None:
        plan = FakePlan([FakeInstruction("battery2", at(0), "IDLE")], valid_until=at(60))
        assert plan_commands(controller, plan) == []

    def test_each_instruction_is_valid_until_the_next_one(self, controller: s.SmaStpe) -> None:
        plan = FakePlan(
            [
                FakeInstruction("battery1", at(30), "FORCED_CHARGE", 0.5),
                FakeInstruction("battery1", at(0), "SELF_CONSUMPTION"),
            ],
            valid_until=at(60),
        )
        first, second = plan_commands(controller, plan)
        assert (first.mode, first.date_time, first.valid_until) == (Mode.SELF_CONSUMPTION, at(0), at(30))
        assert (second.mode, second.date_time, second.valid_until) == (Mode.FORCED_CHARGE, at(30), at(60))
        assert second.operation_factor == 0.5

    def test_label_is_the_mode_name(self, controller: s.SmaStpe) -> None:
        plan = FakePlan([FakeInstruction("battery1", at(0), "IDLE")], valid_until=at(60))
        assert plan_commands(controller, plan)[0].label == "IDLE"

    @pytest.mark.parametrize("mode", [mode.value for mode in Mode])
    def test_every_mode_runs_in_every_control_interval(
        self, controller: s.SmaStpe, mode: str
    ) -> None:
        # Delegated modes follow the battery limits too; they only write on change.
        plan = FakePlan([FakeInstruction("battery1", at(0), mode)], valid_until=at(60))
        assert plan_commands(controller, plan)[0].refresh_interval == s.CONTROL_INTERVAL_S

    def test_every_mode_is_accepted(self, controller: s.SmaStpe) -> None:
        for mode in Mode:
            plan = FakePlan([FakeInstruction("battery1", at(0), mode.value)], valid_until=at(60))
            assert plan_commands(controller, plan)[0].mode is mode

    def test_unknown_mode_is_rejected(self, controller: s.SmaStpe) -> None:
        plan = FakePlan([FakeInstruction("battery1", at(0), "NOT_A_MODE")], valid_until=at(60))
        with pytest.raises(ValueError, match="Unsupported battery operation mode"):
            plan_commands(controller, plan)

    @pytest.mark.parametrize("factor", [-0.1, 1.1])
    def test_factor_out_of_range_is_rejected(self, controller: s.SmaStpe, factor: float) -> None:
        plan = FakePlan([FakeInstruction("battery1", at(0), "IDLE", factor)], valid_until=at(60))
        with pytest.raises(ValueError, match="factor"):
            plan_commands(controller, plan)

    def test_instruction_after_the_plan_end_is_rejected(self, controller: s.SmaStpe) -> None:
        plan = FakePlan([FakeInstruction("battery1", at(90), "IDLE")], valid_until=at(60))
        with pytest.raises(ValueError, match="expire after"):
            plan_commands(controller, plan)

    def test_plan_without_end_is_rejected(self, controller: s.SmaStpe) -> None:
        plan = FakePlan([FakeInstruction("battery1", at(0), "IDLE")], valid_until=None)
        with pytest.raises(ValueError, match="no end"):
            plan_commands(controller, plan)


# ---------------------------------------------------------------------------
# Control thread: modes, register writes
# ---------------------------------------------------------------------------
class TestDelegatedModes:
    @pytest.mark.parametrize(
        "mode, factor, window, op_mod",
        [
            (Mode.IDLE, 1.0, [0, 0, 0, 0, 0], s.OPMOD_OFF),
            (
                Mode.SELF_CONSUMPTION,
                0.5,
                [0, 2500, 0, 2250, 0],
                s.OPMOD_AUTO,
            ),
            (
                Mode.GRID_SUPPORT_EXPORT,
                0.5,
                [0, 5000, 0, 4500, 2250],
                s.OPMOD_AUTO,
            ),
            (
                Mode.GRID_SUPPORT_IMPORT,
                0.4,
                [0, 5000, 0, 4500, -2000],
                s.OPMOD_AUTO,
            ),
            (Mode.FAULT, 1.0, OPEN_WINDOW, s.OPMOD_AUTO),
        ],
    )
    def test_registers_written(
        self, mode: Mode, factor: float, window: list[int], op_mod: int
    ) -> None:
        inverter = FakeInverter()
        thread, _ = make_thread(inverter)
        thread._apply_command(command(mode, factor))
        assert last_delegated(inverter) == (window, op_mod)

    def test_self_consumption_limits_windows_by_the_battery_profile(self) -> None:
        # generic-lfp at 5 degC: charge 0.1 C, discharge 0.3 C of 10 kWh
        inverter = FakeInverter(temp_c=5.0)
        thread, _ = make_thread(inverter)
        thread._apply_command(command(Mode.SELF_CONSUMPTION))
        assert last_delegated(inverter) == (
            [0, 1000, 0, 3000, 0],
            s.OPMOD_AUTO,
        )

    @pytest.mark.parametrize("factor", [1.0, 0.5])
    def test_peak_shaving_is_driven_like_self_consumption(self, factor: float) -> None:
        # GENETIC0 plans PEAK_SHAVING for a discharge-only solution (GENETIC0 is frozen).
        expected = FakeInverter(temp_c=5.0)
        thread, _ = make_thread(expected)
        thread._apply_command(command(Mode.SELF_CONSUMPTION, factor))

        inverter = FakeInverter(temp_c=5.0)
        thread, _ = make_thread(inverter)
        thread._apply_command(command(Mode.PEAK_SHAVING, factor))

        assert last_delegated(inverter) == last_delegated(expected)

    @pytest.mark.parametrize(
        "mode",
        [
            Mode.FREQUENCY_REGULATION,
            Mode.RAMP_RATE_CONTROL,
            Mode.RESERVE_BACKUP,
            Mode.OUTAGE_SUPPLY,
        ],
    )
    def test_modes_not_planned_by_eos_use_the_safe_state(self, mode: Mode) -> None:
        assert s.SMA_MODE_MAPPING[mode] == s.SMA_MODE_MAPPING[Mode.FAULT]

    def test_self_consumption_closes_both_windows_without_temperature(self) -> None:
        # Without temperature the profile and its hard cutoffs cannot be checked.
        inverter = FakeInverter()
        inverter.temp_c = -(2**31) / 10.0
        thread, _ = make_thread(inverter)

        thread._apply_command(command(Mode.SELF_CONSUMPTION))

        window, op_mod = last_delegated(inverter)
        assert window == [0, 0, 0, 0, 0]
        assert op_mod == s.OPMOD_AUTO

    def test_grid_support_limits_windows_by_the_battery_profile(self) -> None:
        # The factor refers to the discharge power the battery allows at 5 degC (3000 W).
        inverter = FakeInverter(temp_c=5.0)
        thread, _ = make_thread(inverter)
        thread._apply_command(command(Mode.GRID_SUPPORT_EXPORT, 0.5))
        assert last_delegated(inverter) == (
            [0, 1000, 0, 3000, 1500],
            s.OPMOD_AUTO,
        )

    def test_hard_temperature_cutoff_of_the_profile_closes_both_windows(self) -> None:
        inverter = FakeInverter(temp_c=65.0)  # generic-lfp: no charge or discharge above 60 degC
        thread, _ = make_thread(inverter)
        thread._apply_command(command(Mode.SELF_CONSUMPTION))
        assert last_delegated(inverter)[0] == [0, 0, 0, 0, 0]

    def test_inverter_limits_cap_the_profile_limits(self) -> None:
        # generic-lfp at 25 degC: 1.0 C charge, 0.5 C discharge of 10 kWh
        inverter = FakeInverter(temp_c=25.0, max_charge=5000, max_discharge=4500)
        thread, _ = make_thread(inverter)
        thread._apply_command(command(Mode.SELF_CONSUMPTION))
        assert last_delegated(inverter)[0][:4] == [0, 5000, 0, 4500]

    def test_model_power_limits_of_the_profile_apply(self) -> None:
        profile = generic_lfp_profile(max_charge_power_w=2500.0, max_discharge_power_w=1200.0)
        inverter = FakeInverter(temp_c=25.0)
        thread, _ = make_thread(inverter, profile=profile)
        thread._apply_command(command(Mode.SELF_CONSUMPTION))
        assert last_delegated(inverter)[0][:4] == [0, 2500, 0, 1200]

    def test_technical_soc_bounds_of_the_profile_apply(self) -> None:
        profile = generic_lfp_profile(technical_min_soc_percent=20.0, technical_max_soc_percent=90.0)
        for soc, window in ((90.0, [0, 0, 0, 4500]), (20.0, [0, 5000, 0, 0])):
            inverter = FakeInverter(temp_c=25.0, soc=soc)
            thread, _ = make_thread(inverter, profile=profile)
            thread._apply_command(command(Mode.SELF_CONSUMPTION))
            assert last_delegated(inverter)[0][:4] == window

    def test_external_setpoint_is_deactivated_first_then_window_then_opmod(
        self,
    ) -> None:
        inverter = FakeInverter()
        thread, _ = make_thread(inverter)
        thread._apply_command(command(Mode.IDLE))
        assert inverter.addresses_written() == [
            s.REG_WCTL_ACT,
            s.REG_BMS_BLOCK,
            s.REG_BMS_OPMOD,
        ]
        assert inverter.writes[0][1] == tuple(s._encode_u32(s.WCTL_INACTIVE))

    def test_fault_is_not_treated_as_a_normal_mode(self) -> None:
        inverter = FakeInverter()
        thread, _ = make_thread(inverter)
        thread._apply_command(command(Mode.FAULT))
        assert not inverter.external_active
        assert thread._lane == s._LANE_DELEGATED


def profile_source(current: dict[str, Any]) -> Callable[[], s.SmaBatteryContext]:
    """Source of a context without battery device for the profile in ``current["profile"]``."""
    return lambda: s.SmaBatteryContext.from_profile(current["profile"])


class TestLiveBatteryProfile:
    """The control thread fetches the battery context for every command."""

    COLD = 5.0  # generic-lfp at 5 degC: discharge 0.3 C of 10 kWh = 3000 W

    def test_updated_profile_applies_to_the_next_command(self) -> None:
        current = {"profile": GENERIC_LFP_PROFILE}
        inverter = FakeInverter(temp_c=self.COLD)
        thread, _ = make_thread(inverter, profile=profile_source(current))
        thread._apply_command(command(Mode.SELF_CONSUMPTION))
        assert last_delegated(inverter)[0][3] == 3000

        current["profile"] = generic_lfp_profile(max_discharge_power_w=1200.0)
        thread._apply_command(command(Mode.SELF_CONSUMPTION))
        assert last_delegated(inverter)[0][3] == 1200

    def test_update_is_recorded(self) -> None:
        current = {"profile": GENERIC_LFP_PROFILE}
        log = _log()
        thread, _ = make_thread(FakeInverter(), log=log, profile=profile_source(current))
        current["profile"] = generic_lfp_profile(battery_model="Edited")
        thread._apply_command(command(Mode.SELF_CONSUMPTION))
        updates = [e.data for e in log.drain() if e.event == "battery_context_updated"]
        assert updates[-1]["battery_model"] == "Edited"

    def test_unchanged_context_is_not_recorded_again(self) -> None:
        current = {"profile": GENERIC_LFP_PROFILE}
        log = _log()
        thread, _ = make_thread(FakeInverter(), log=log, profile=profile_source(current))
        log.drain()
        for _ in range(3):
            thread._apply_command(command(Mode.SELF_CONSUMPTION))
        assert not [e for e in log.drain() if e.event == "battery_context_updated"]

    @pytest.mark.parametrize(
        "error",
        [
            FileNotFoundError("No battery profile for 'x'"),
            PermissionError("Permission denied: 'x.json'"),
            ValueError("invalid battery profile"),
        ],
    )
    def test_unusable_context_keeps_the_last_one_and_is_recorded_once(
        self, error: Exception
    ) -> None:
        calls = {"n": 0}

        def source() -> s.SmaBatteryContext:
            calls["n"] += 1
            if calls["n"] == 1:
                return s.SmaBatteryContext.from_profile(
                    generic_lfp_profile(max_discharge_power_w=1200.0)
                )
            raise error

        log = _log()
        inverter = FakeInverter(temp_c=self.COLD)
        thread, _ = make_thread(inverter, log=log, profile=source)
        for _ in range(3):
            thread._apply_command(command(Mode.SELF_CONSUMPTION))
            assert last_delegated(inverter)[0][3] == 1200
        errors = [e for e in log.drain() if e.event == "battery_context_error"]
        assert len(errors) == 1
        assert errors[0].data["error_type"] == type(error).__name__

    def test_without_usable_context_at_start_the_battery_is_held(self) -> None:
        current: dict[str, Any] = {"profile": None}

        def source() -> s.SmaBatteryContext:
            if current["profile"] is None:
                raise FileNotFoundError("No battery profile for 'x'")
            return s.SmaBatteryContext.from_profile(current["profile"])

        log = _log()
        inverter = FakeInverter(temp_c=self.COLD)
        thread, shared = make_thread(inverter, log=log, profile=source)

        thread._apply_command(command(Mode.FORCED_DISCHARGE))
        assert inverter.setpoint_w == 0
        thread._apply_command(command(Mode.SELF_CONSUMPTION))
        assert last_delegated(inverter)[0][:4] == [0, 0, 0, 0]
        # The readings are still provided.
        thread._read_status()
        assert shared.get("inv1:" + s.BAT_SOC_PERCENT) == 50
        assert [e for e in log.drain() if e.event == "battery_context_error"]

        current["profile"] = GENERIC_LFP_PROFILE  # e.g. the profile is saved again
        thread._apply_command(command(Mode.SELF_CONSUMPTION))
        assert last_delegated(inverter)[0][3] == 3000

    def test_profile_saved_to_the_store_applies_live(self, config_eos) -> None:
        from akkudoktoreos.devices.profile.profilestore import get_profile_store

        store = get_profile_store()
        document = BatteryProfileSettings.model_validate_json(GENERIC_LFP_FILE.read_text())
        store.save("edited", document)
        inverter = FakeInverter(temp_c=self.COLD)
        thread, _ = make_thread(
            inverter,
            profile=lambda: s.SmaBatteryContext.from_profile(
                store.get(BatteryProfileSettings, "edited")
            ),
        )
        thread._apply_command(command(Mode.SELF_CONSUMPTION))
        assert last_delegated(inverter)[0][3] == 3000

        store.save("edited", document.model_copy(update={"max_discharge_power_w": 800.0}))
        thread._apply_command(command(Mode.SELF_CONSUMPTION))
        assert last_delegated(inverter)[0][3] == 800


def battery_context(
    *,
    profile: BatteryProfile = GENERIC_LFP_PROFILE,
    capacity_wh: Optional[int] = 10000,
    full_charge_soc_percent: Optional[int] = None,
    inverter: Optional[InverterOperatingParameters] = None,
    **battery: Any,
) -> s.SmaBatteryContext:
    """Context of a battery device ``battery1`` with the given battery settings."""
    return s.SmaBatteryContext(
        battery=BatteriesCommonSettings(
            device_id="battery1", battery_profile="generic-lfp", **battery
        ),
        profile=profile,
        capacity_wh=capacity_wh,
        full_charge_soc_percent=full_charge_soc_percent,
        inverter=inverter,
    )


def inverter_parameters(
    *,
    max_power_w: float = 10000.0,
    max_ac_charge_power_w: float = 10000.0,
    ac_charge_limits_total_charge: bool = False,
) -> InverterOperatingParameters:
    """Effective parameters of an inverter device with a flat efficiency."""
    flat = ((0.0, 1.0), (1.0, 1.0))
    return InverterOperatingParameters(
        temperature_degC=45.0,
        nominal_power_w=10000.0,
        power_fraction=1.0,
        max_power_w=max_power_w,
        max_ac_charge_power_w=max_ac_charge_power_w,
        ac_charge_limits_total_charge=ac_charge_limits_total_charge,
        dc_to_ac_efficiency_curve=flat,
        ac_to_dc_efficiency_curve=flat,
        dc_to_ac_efficiency=1.0,
        ac_to_dc_efficiency=1.0,
    )


def apply(
    context: s.SmaBatteryContext, mode: Mode, factor: float = 1.0, **inverter_args: Any
) -> FakeInverter:
    """Apply one command with the given context to a fresh fake inverter."""
    inverter = FakeInverter(**inverter_args)
    thread, _ = make_thread(inverter, profile=context)
    thread._apply_command(command(mode, factor))
    return inverter


class TestBatteryDeviceLimits:
    """The battery device settings limit the battery power like for the optimization."""

    def test_installation_power_limits_of_the_profile_adjustment_apply(self) -> None:
        context = battery_context(
            profile_adjustment={"max_charge_power_w": 3000, "max_discharge_power_w": 2000}
        )
        window = last_delegated(apply(context, Mode.SELF_CONSUMPTION))[0]
        assert window[:4] == [0, 3000, 0, 2000]
        assert apply(context, Mode.FORCED_CHARGE).setpoint_w == -3000
        assert apply(context, Mode.FORCED_DISCHARGE).setpoint_w == 2000

    def test_generic_profile_without_power_limits_gets_the_generic_default_limit(self) -> None:
        # generic-lfp at 25 degC: 1.0 C charge, 0.5 C discharge of 20 kWh, the generic
        # profile has no own power limits: 5000 W each, below the inverter limits.
        context = battery_context(capacity_wh=20000)
        inverter = apply(context, Mode.SELF_CONSUMPTION, max_charge=9000, max_discharge=9000)
        assert last_delegated(inverter)[0][:4] == [0, 5000, 0, 5000]

    def test_c_rates_refer_to_the_resolved_capacity_not_the_reported_one(self) -> None:
        # generic-lfp at 5 degC: charge 0.1 C, discharge 0.3 C of 20 kWh (inverter: 10 kWh)
        context = battery_context(capacity_wh=20000)
        inverter = apply(context, Mode.SELF_CONSUMPTION, temp_c=5.0, cap_wh=10000)
        assert last_delegated(inverter)[0][:4] == [0, 2000, 0, 4500]

    def test_soc_range_of_the_battery_applies(self) -> None:
        context = battery_context(min_soc_percentage=20, max_soc_percentage=80)
        assert apply(context, Mode.FORCED_DISCHARGE, soc=20.0).setpoint_w == 0
        assert apply(context, Mode.FORCED_DISCHARGE, soc=30.0).setpoint_w == 4500
        assert apply(context, Mode.FORCED_CHARGE, soc=80.0).setpoint_w == 0
        window = last_delegated(apply(context, Mode.SELF_CONSUMPTION, soc=80.0))[0]
        assert window[:4] == [0, 0, 0, 4500]

    def test_explicit_charge_modes_may_charge_to_the_full_charge_soc(self) -> None:
        context = battery_context(max_soc_percentage=80, full_charge_soc_percent=100)
        assert apply(context, Mode.FORCED_CHARGE, soc=85.0).setpoint_w == -5000
        assert last_delegated(apply(context, Mode.GRID_SUPPORT_IMPORT, soc=85.0))[0][4] == -5000
        # Other modes stop at the maximum SoC of the battery.
        assert last_delegated(apply(context, Mode.SELF_CONSUMPTION, soc=85.0))[0][1] == 0

    def test_without_periodic_full_charge_charging_stops_at_the_maximum_soc(self) -> None:
        context = battery_context(max_soc_percentage=80)
        assert apply(context, Mode.FORCED_CHARGE, soc=85.0).setpoint_w == 0
        assert last_delegated(apply(context, Mode.GRID_SUPPORT_IMPORT, soc=85.0))[0][4] == 0

    def test_unresolvable_limits_hold_the_battery_and_are_recorded_once(self) -> None:
        # The configured SoC range lies above the technical maximum SoC of the profile.
        context = battery_context(
            profile=generic_lfp_profile(technical_max_soc_percent=90.0), min_soc_percentage=95
        )
        log = _log()
        inverter = FakeInverter()
        thread, _ = make_thread(inverter, log=log, profile=context)
        for _ in range(2):
            thread._apply_command(command(Mode.FORCED_DISCHARGE))
            assert inverter.setpoint_w == 0
        errors = [e for e in log.drain() if e.event == "battery_limits_error"]
        assert len(errors) == 1


class TestInverterDeviceLimits:
    """The battery's inverter device limits the AC power and the charging from the grid."""

    def test_no_ac_charge_power_disables_charging_from_the_grid(self) -> None:
        context = battery_context(inverter=inverter_parameters(max_ac_charge_power_w=0.0))
        assert last_delegated(apply(context, Mode.GRID_SUPPORT_IMPORT))[0][4] == 0
        assert apply(context, Mode.FORCED_CHARGE).setpoint_w == 0
        # PV may still charge the battery.
        assert apply(context, Mode.FORCED_CHARGE, pv_w=1500.0).setpoint_w == -1500

    def test_ac_charge_power_caps_charging_from_the_grid(self) -> None:
        context = battery_context(inverter=inverter_parameters(max_ac_charge_power_w=2000.0))
        inverter = apply(context, Mode.GRID_SUPPORT_IMPORT)
        # The charge window stays open for PV, the grid setpoint is capped.
        assert last_delegated(inverter)[0] == [0, 5000, 0, 4500, -2000]
        assert apply(context, Mode.FORCED_CHARGE, pv_w=1000.0).setpoint_w == -3000

    def test_ac_charge_power_that_limits_the_total_charge_caps_the_charge_window(self) -> None:
        context = battery_context(
            inverter=inverter_parameters(
                max_ac_charge_power_w=2000.0, ac_charge_limits_total_charge=True
            )
        )
        assert last_delegated(apply(context, Mode.SELF_CONSUMPTION))[0][1] == 2000
        assert apply(context, Mode.FORCED_CHARGE, pv_w=1000.0).setpoint_w == -2000

    def test_maximum_ac_power_caps_the_discharge(self) -> None:
        context = battery_context(inverter=inverter_parameters(max_power_w=3000.0))
        assert last_delegated(apply(context, Mode.SELF_CONSUMPTION))[0][3] == 3000
        assert last_delegated(apply(context, Mode.GRID_SUPPORT_EXPORT))[0][4] == 3000
        assert apply(context, Mode.FORCED_DISCHARGE).setpoint_w == 3000


class TestDelegatedRefresh:
    """Delegated modes follow the battery limits, but write only on change."""

    @staticmethod
    def bms_writes(inverter: FakeInverter) -> int:
        return inverter.addresses_written().count(s.REG_BMS_OPMOD)

    def test_unchanged_values_are_written_at_the_register_refresh(self, clock: Clock) -> None:
        inverter = FakeInverter()
        thread, _ = make_thread(inverter)
        thread._apply_command(command(Mode.SELF_CONSUMPTION))
        clock.t += s.CONTROL_INTERVAL_S
        thread._apply_command(command(Mode.SELF_CONSUMPTION))
        assert self.bms_writes(inverter) == 1
        clock.t += s.REGISTER_REFRESH_S
        thread._apply_command(command(Mode.SELF_CONSUMPTION))
        assert self.bms_writes(inverter) == 2

    def test_changed_battery_limits_are_written_in_the_next_control_interval(
        self, clock: Clock
    ) -> None:
        inverter = FakeInverter(temp_c=25.0)
        thread, _ = make_thread(inverter)
        thread._apply_command(command(Mode.SELF_CONSUMPTION))
        inverter.temp_c = 65.0  # above the hard cutoff of generic-lfp
        clock.t += s.CONTROL_INTERVAL_S
        thread._apply_command(command(Mode.SELF_CONSUMPTION))
        assert self.bms_writes(inverter) == 2
        assert last_delegated(inverter)[0][:4] == [0, 0, 0, 0]

    def test_values_are_written_again_after_the_command_method(self, clock: Clock) -> None:
        inverter = FakeInverter()
        thread, _ = make_thread(inverter)
        thread._apply_command(command(Mode.SELF_CONSUMPTION))
        thread._apply_command(command(Mode.FORCED_CHARGE))
        writes = self.bms_writes(inverter)
        thread._apply_command(command(Mode.SELF_CONSUMPTION))
        assert self.bms_writes(inverter) == writes + 1
        assert not inverter.external_active


@pytest.fixture
def configured_devices(config_eos: Any) -> Any:
    """Battery ``battery1`` with its inverter device ``inverter1``."""
    config_eos.merge_settings_from_dict(
        {
            "devices": {
                "max_batteries": 1,
                "batteries": {
                    "battery1": {
                        "device_id": "battery1",
                        "min_soc_percentage": 10,
                        "max_soc_percentage": 90,
                        "profile_adjustment": {
                            "nominal_capacity_wh": 12000,
                            "max_charge_power_w": 3000,
                        },
                    }
                },
                "max_inverters": 2,
                "inverters": {
                    "inverter1": {
                        "device_id": "inverter1",
                        "battery_id": "battery1",
                        "profile_adjustment": {
                            "nominal_power_w": 8000,
                            "max_ac_charge_power_w": 2000,
                        },
                    }
                },
            }
        }
    )
    return config_eos


class TestBatteryContextFromConfig:
    """The controller resolves the battery context from the battery and its inverter device."""

    def test_context_is_resolved_from_the_battery_and_its_inverter(
        self, configured_devices: Any, controller: s.SmaStpe
    ) -> None:
        context = controller._battery_context()
        assert context.capacity_wh == 12000
        assert context.battery.profile_adjustment.max_charge_power_w == 3000
        assert (context.battery.min_soc_percentage, context.battery.max_soc_percentage) == (
            10,
            90,
        )
        assert context.inverter is not None
        assert context.inverter.nominal_power_w == 8000
        assert context.inverter.max_ac_charge_power_w == 2000

    def test_controller_gives_the_thread_a_live_context_source(
        self, configured_devices: Any, controller: s.SmaStpe
    ) -> None:
        thread = controller._create_control_thread()
        assert callable(thread._battery_source)
        assert thread._battery is not None and thread._battery.capacity_wh == 12000

    def test_battery_without_inverter_device_has_no_inverter_limits(
        self, configured_devices: Any, controller: s.SmaStpe
    ) -> None:
        configured_devices.merge_settings_from_dict(
            {"devices": {"inverters": {"inverter1": {"battery_id": None}}}}
        )
        assert controller._battery_context().inverter is None

    def test_battery_that_is_not_configured_is_an_error(
        self, configured_devices: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(s, "FRBCInstruction", FakeInstruction)
        controller = s.SmaStpe(
            "inv1", ControlSharedDataStore(), _log(), make_settings(battery_id="batery1")
        )
        with pytest.raises(ValueError, match="'batery1' is not configured"):
            controller._battery_context()
        # The thread starts anyway: readings without battery control.
        assert controller._create_control_thread()._battery is None

    def test_several_inverters_of_the_battery_are_an_error(
        self, configured_devices: Any, controller: s.SmaStpe
    ) -> None:
        configured_devices.merge_settings_from_dict(
            {
                "devices": {
                    "inverters": {
                        "inverter2": {"device_id": "inverter2", "battery_id": "battery1"}
                    }
                }
            }
        )
        with pytest.raises(ValueError, match="several inverter devices"):
            controller._battery_context()

    def test_pv_rated_power_defaults_to_the_inverter_nominal_power(
        self, configured_devices: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(s, "FRBCInstruction", FakeInstruction)
        controller = s.SmaStpe(
            "inv1", ControlSharedDataStore(), _log(), make_settings(pv_curtailment={})
        )
        limiter = controller._create_control_thread()._pv_limiter
        assert limiter is not None and limiter.s.rated_power_w == 8000

    def test_pv_curtailment_without_rated_power_is_disabled(
        self, configured_devices: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configured_devices.merge_settings_from_dict(
            {"devices": {"inverters": {"inverter1": {"battery_id": None}}}}
        )
        monkeypatch.setattr(s, "FRBCInstruction", FakeInstruction)
        controller = s.SmaStpe(
            "inv1", ControlSharedDataStore(), _log(), make_settings(pv_curtailment={})
        )
        assert controller._create_control_thread()._pv_limiter is None


class TestForcedModes:
    def apply(self, mode: Mode, factor: float, **inverter_args: Any) -> FakeInverter:
        inverter = FakeInverter(**inverter_args)
        thread, _ = make_thread(inverter)
        thread._apply_command(command(mode, factor))
        return inverter

    def test_forced_charge_sets_the_battery_setpoint(self) -> None:
        inverter = self.apply(Mode.FORCED_CHARGE, 0.6)
        assert inverter.setpoint_w == -3000 and inverter.external_active

    def test_forced_discharge_sets_the_battery_setpoint(self) -> None:
        assert self.apply(Mode.FORCED_DISCHARGE, 0.5).setpoint_w == 2250

    def test_forced_charge_is_derated_when_cold(self) -> None:
        assert self.apply(Mode.FORCED_CHARGE, 1.0, temp_c=5.0, soc=20.0).setpoint_w == -1000

    def test_forced_discharge_is_derated_by_the_battery_profile(self) -> None:
        # generic-lfp at 5 degC: 0.3 C of 10 kWh
        assert self.apply(Mode.FORCED_DISCHARGE, 1.0, temp_c=5.0).setpoint_w == 3000

    def test_forced_charge_stops_when_full_and_forced_discharge_when_empty(self) -> None:
        # The SoC range of a context without battery device is the full range.
        assert self.apply(Mode.FORCED_CHARGE, 1.0, soc=100.0).setpoint_w == 0
        assert self.apply(Mode.FORCED_DISCHARGE, 1.0, soc=0.0).setpoint_w == 0

    def test_soc_bounds_of_the_command_narrow_the_soc_range(self) -> None:
        inverter = FakeInverter(soc=5.0)
        thread, _ = make_thread(inverter)
        thread._apply_command(command(Mode.FORCED_DISCHARGE, 1.0, soc_min_percent=5.0))
        assert inverter.setpoint_w == 0

    def test_entering_the_command_method_opens_the_window_first(self) -> None:
        inverter = self.apply(Mode.FORCED_CHARGE, 1.0)
        assert inverter.addresses_written() == [
            s.REG_WCTL_ACT,
            s.REG_BMS_BLOCK,
            s.REG_BMS_OPMOD,
            s.REG_WCTL_SPT,
        ]
        assert decode_block(inverter.writes[1][1]) == OPEN_WINDOW
        assert s._decode_u32(list(inverter.writes[2][1])) == s.OPMOD_AUTO

    def test_the_setpoint_is_written_together_with_the_activation(self) -> None:
        inverter = self.apply(Mode.FORCED_CHARGE, 1.0)
        address, values = inverter.writes[-1]
        assert address == s.REG_WCTL_SPT and len(values) == 4
        assert s._decode_s32(list(values[:2])) == -5000
        assert (values[2] << 16 | values[3]) == s.WCTL_ACTIVE


class TestGridPowerControlModes:
    def test_import_is_answered_with_discharge_limited_by_the_factor(self) -> None:
        inverter = FakeInverter(load_w=6000)
        thread, shared = make_thread(inverter)
        publish_grid(shared, inverter)
        thread._apply_command(command(Mode.NON_EXPORT, 0.2))
        assert 0 < inverter.setpoint_w <= 900

    def test_export_is_answered_with_charge_limited_by_the_factor(self) -> None:
        inverter = FakeInverter(pv_w=9000, load_w=500)
        thread, shared = make_thread(inverter)
        publish_grid(shared, inverter)
        thread._apply_command(command(Mode.NON_EXPORT, 0.2))
        assert -1000 <= inverter.setpoint_w < 0

    def test_without_grid_power_the_battery_is_held_in_non_export(self) -> None:
        inverter = FakeInverter(load_w=3000)
        thread, _ = make_thread(inverter)
        thread._apply_command(command(Mode.NON_EXPORT))
        assert inverter.setpoint_w == 0

    def test_stale_grid_power_holds_the_battery_in_non_export(self, clock: Clock) -> None:
        inverter = FakeInverter(load_w=3000)
        thread, shared = make_thread(inverter)
        publish_grid(shared, inverter)
        clock.t += 10.0
        thread._apply_command(command(Mode.NON_EXPORT))
        assert inverter.setpoint_w == 0

    def test_without_grid_power_the_battery_is_held(self) -> None:
        inverter = FakeInverter(load_w=3000)
        thread, _ = make_thread(inverter)
        thread._apply_command(command(Mode.SELF_CONSUMPTION))
        assert inverter.setpoint_w == 0

    def test_stale_grid_power_holds_the_battery(self, clock: Clock) -> None:
        inverter = FakeInverter(load_w=3000)
        thread, shared = make_thread(inverter)
        publish_grid(shared, inverter)
        clock.t += 10.0  # the limit is 3 s
        thread._apply_command(command(Mode.SELF_CONSUMPTION))
        assert inverter.setpoint_w == 0

    def test_grid_power_sign_is_converted(self) -> None:
        # the store holds import as positive, the controller works with export positive
        inverter = FakeInverter()
        thread, shared = make_thread(inverter)
        shared.publish_sync({GRID_KEY: 1234.0})
        assert thread._grid_power_w() == -1234.0

    @pytest.mark.parametrize("value", ["x", True, float("nan"), float("inf")])
    def test_unusable_grid_power_is_ignored(self, value: Any) -> None:
        inverter = FakeInverter()
        thread, shared = make_thread(inverter)
        shared.publish_sync({GRID_KEY: value})
        assert thread._grid_power_w() is None


class TestMethodSwitching:
    def test_repeated_non_export_updates_only_the_setpoint(self) -> None:
        inverter = FakeInverter(load_w=1000)
        thread, shared = make_thread(inverter)
        publish_grid(shared, inverter)

        thread._apply_command(command(Mode.NON_EXPORT))
        first = len(inverter.writes)

        thread._apply_command(command(Mode.NON_EXPORT))
        second = len(inverter.writes)

        assert first == 4  # deactivate, window, OpMod, setpoint
        assert second - first == 1  # only the setpoint

    def test_leaving_the_command_method_deactivates_the_setpoint_first(self) -> None:
        inverter = FakeInverter(load_w=1000)
        thread, shared = make_thread(inverter)
        publish_grid(shared, inverter)
        thread._apply_command(command(Mode.NON_EXPORT))
        before = len(inverter.writes)
        thread._apply_command(command(Mode.IDLE))
        assert inverter.writes[before][0] == s.REG_WCTL_ACT
        assert not inverter.external_active

    def test_the_window_is_reopened_after_idle(self) -> None:
        inverter = FakeInverter()
        thread, _ = make_thread(inverter)
        thread._apply_command(command(Mode.IDLE))
        thread._apply_command(command(Mode.FORCED_DISCHARGE, 0.5))
        assert inverter.addresses_written()[-4:] == [
            s.REG_WCTL_ACT,
            s.REG_BMS_BLOCK,
            s.REG_BMS_OPMOD,
            s.REG_WCTL_SPT,
        ]
        assert decode_block(inverter.writes[-3][1]) == OPEN_WINDOW

    def test_after_a_reconnect_the_method_is_set_up_from_scratch(self) -> None:
        inverter = FakeInverter()
        thread, _ = make_thread(inverter)
        thread._apply_command(command(Mode.FORCED_CHARGE))
        thread._connect()
        before = len(inverter.writes)
        thread._apply_command(command(Mode.FORCED_CHARGE))
        assert len(inverter.writes) - before == 4

    def test_safe_state_hands_over_to_the_inverter(self) -> None:
        inverter = FakeInverter()
        thread, _ = make_thread(inverter)
        thread._apply_command(command(Mode.FORCED_CHARGE))
        thread._enter_safe_state()
        assert not inverter.external_active
        assert last_delegated(inverter) == (OPEN_WINDOW, s.OPMOD_AUTO)


# ---------------------------------------------------------------------------
# Connect, status, shared data
# ---------------------------------------------------------------------------
class TestConnect:
    def test_battery_limits_are_read(self) -> None:
        thread, _ = make_thread(FakeInverter(cap_wh=9000, max_charge=4000, max_discharge=3500))
        assert thread._limits == s._BatteryLimits(9000, 4000, 3500)

    def test_missing_battery_is_an_error(self) -> None:
        inverter = FakeInverter()
        thread, _ = make_thread(inverter)
        inverter.max_charge = 0xFFFFFFFF  # not available
        with pytest.raises(ConnectionError, match="battery limits"):
            thread._connect()

    def test_limits_are_read_from_holding_registers(self) -> None:
        inverter = FakeInverter()
        thread, _ = make_thread(inverter)
        calls: list[str] = []

        def read_holding(address: int, count: int = 2) -> list[int]:
            calls.append("holding")
            return inverter.registers(address, count)

        def read_input(address: int, count: int = 2) -> list[int]:
            calls.append("input")
            return inverter.registers(address, count)

        thread._read_holding_registers = read_holding  # type: ignore[method-assign]
        thread._read_input_registers = read_input  # type: ignore[method-assign]
        thread._connect()
        assert calls == ["holding"]

    def test_inverter_readings_use_four_block_reads(self) -> None:
        inverter = FakeInverter(pv_w=1200, soc=63, temp_c=21.5)
        thread, _ = make_thread(inverter)
        calls: list[tuple[int, int]] = []
        original = thread._read_input_registers

        def read_input(address: int, count: int = 2) -> list[int]:
            calls.append((address, count))
            return original(address, count)

        thread._read_input_registers = read_input  # type: ignore[method-assign]
        readings = thread._read_inverter()
        assert len(calls) == 4
        assert (readings.dc_pv_w, readings.soc_percent, readings.temp_c) == (1200, 63, 21.5)

    def test_a_recent_reading_is_reused(self, clock: Clock) -> None:
        inverter = FakeInverter()
        thread, _ = make_thread(inverter)
        first = thread._read_inverter()
        assert thread._read_inverter() is first
        clock.t += 1.0
        assert thread._read_inverter() is not first

    def test_not_available_temperature_is_none(self) -> None:
        inverter = FakeInverter()
        thread, _ = make_thread(inverter)
        inverter.temp_c = -(2**31) / 10.0  # the "not available" value of SMA
        assert thread._read_inverter().temp_c is None


class TestStatusAndSharedData:
    def test_readings_are_published_with_the_device_id(self) -> None:
        inverter = FakeInverter(pv_w=1000, soc=63, temp_c=21.5)
        thread, shared = make_thread(inverter)
        thread._read_status()
        assert shared.get("inv1:bat_soc_percent") == 63
        assert shared.get("inv1:bat_soc_factor") == pytest.approx(0.63)
        assert shared.get("inv1:bat_temperature_c") == 21.5
        assert shared.get("inv1:dc_pv_power_w") == 1000

    def test_battery_power_is_discharge_positive(self) -> None:
        inverter = FakeInverter(pv_w=0)
        thread, shared = make_thread(inverter)
        inverter.bat_dc_w = -800.0
        thread._read_status()
        assert shared.get("inv1:bat_power_w") == -800

    def test_unavailable_values_are_left_out(self) -> None:
        inverter = FakeInverter()
        thread, shared = make_thread(inverter)
        inverter.temp_c = -(2**31) / 10.0
        thread._read_status()
        assert shared.get("inv1:bat_temperature_c") is None
        assert shared.get("inv1:bat_soc_percent") is not None

    def test_thread_declares_exactly_the_keys_it_publishes(self) -> None:
        inverter = FakeInverter(soc=50, temp_c=25.0)
        thread, shared = make_thread(inverter)
        thread._read_status()
        assert sorted(thread.published_keys()) == sorted("inv1:" + key for key in s.INVERTER_KEYS)
        assert set(shared.snapshot()) >= set(thread.published_keys())

    def test_status_shows_the_pv_limit_only_if_it_is_in_use(self) -> None:
        inverter = FakeInverter()
        thread, _ = make_thread(inverter)
        assert "pv_limit_percent" not in thread._read_status()
        thread, _ = make_thread(FakeInverter(), pv=PvLimiterSettings(rated_power_w=10000))
        assert thread._read_status()["pv_limit_percent"] == 100


# ---------------------------------------------------------------------------
# PV limit
# ---------------------------------------------------------------------------
PV = PvLimiterSettings(rated_power_w=10000)


def run(
    inverter: FakeInverter,
    thread: s.SmaStpeControl,
    shared: ControlSharedDataStore,
    clock: Clock,
    seconds: float,
    *,
    mode: Mode = Mode.NON_EXPORT,
    dt: float = 5.0,
    events: Optional[dict[float, Callable[[FakeInverter], None]]] = None,
) -> list[tuple[float, float, int, float]]:
    """Advance plant and controller; return (time, grid export, limit percent, battery power)."""
    trace = []
    events = dict(events or {})
    t0 = clock.t
    for _ in range(int(seconds / dt)):
        elapsed = clock.t - t0
        for at_time in [e for e in events if e <= elapsed]:
            events.pop(at_time)(inverter)
        inverter.advance(dt)
        clock.t += dt
        publish_grid(shared, inverter)
        thread._apply_command(command(mode))
        thread._read_status()
        trace.append((elapsed, inverter.grid_export_w, inverter.limit_percent, inverter.bat_dc_w))
    return trace


def limited(inverter: FakeInverter, clock: Clock, **inverter_args: Any) -> tuple[FakeInverter, s.SmaStpeControl, ControlSharedDataStore]:
    thread, shared = make_thread(inverter, pv=PV)
    return inverter, thread, shared


class TestPvLimitAvailability:
    def test_not_configured_the_limit_register_is_never_touched(self, clock: Clock) -> None:
        inverter = FakeInverter(pv_w=6000, load_w=1000, soc=100)
        thread, shared = make_thread(inverter)
        run(inverter, thread, shared, clock, 600)
        assert inverter.limit_writes == 0
        assert not thread._pv_limit_available
        assert inverter.grid_export_w > 3000  # the battery is full: the surplus is exported

    def test_plant_control_mode_is_required(self, clock: Clock) -> None:
        inverter = FakeInverter(pv_w=6000, load_w=1000, soc=100, wmod=303)
        thread, shared = make_thread(inverter, pv=PV)
        run(inverter, thread, shared, clock, 600)
        assert not thread._pv_limit_available
        assert inverter.limit_writes == 0

    def test_register_that_is_not_available_disables_the_limiter(self, clock: Clock) -> None:
        inverter = FakeInverter(pv_w=6000, load_w=1000, soc=100, limit_na=True)
        thread, shared = make_thread(inverter, pv=PV)
        run(inverter, thread, shared, clock, 600)
        assert not thread._pv_limit_available
        assert inverter.limit_writes == 0

    def test_device_that_refuses_the_register_only_loses_the_feature(self, clock: Clock) -> None:
        inverter = FakeInverter(pv_w=6000, load_w=1000, soc=100)
        thread, shared = make_thread(inverter, pv=PV)

        def refuse(address: int, count: int = 2) -> list[int]:
            if address in (s.REG_PV_WMOD, s.REG_PV_LIMIT_PERCENT):
                raise OSError("illegal data address")
            return inverter.registers(address, count)

        thread._read_holding_registers = refuse  # type: ignore[method-assign]
        thread._connect()  # must not raise
        assert not thread._pv_limit_available
        assert thread._limits is not None

    def test_limit_found_on_connect_is_taken_over(self) -> None:
        thread, _ = make_thread(FakeInverter(limit_percent=40), pv=PV)
        assert thread._pv_limiter is not None
        assert thread._pv_limiter.percent == 40 and thread._pv_limit_available

    def test_leftover_limit_is_released_when_not_needed(self, clock: Clock) -> None:
        inverter = FakeInverter(pv_w=1000, load_w=3000, soc=50, limit_percent=40)
        thread, shared = make_thread(inverter, pv=PV)
        run(inverter, thread, shared, clock, 150)
        assert inverter.limit_percent == 100


class TestPvLimitControl:
    def test_writing_a_limit_without_limiter_is_an_error(self) -> None:
        inverter = FakeInverter()
        thread, _ = make_thread(inverter)  # no PV curtailment configured
        with pytest.raises(RuntimeError, match="no PV limiter configured"):
            thread._write_pv_limit(50)
        assert inverter.limit_writes == 0

    def test_battery_full_surplus_is_curtailed_to_the_headroom(self, clock: Clock) -> None:
        inverter = FakeInverter(pv_w=6000, load_w=1000, soc=100)
        thread, shared = make_thread(inverter, pv=PV)
        trace = run(inverter, thread, shared, clock, 1800)
        late = [grid for elapsed, grid, *_ in trace if elapsed > 300]
        assert all(-60 < grid < 300 for grid in late)
        assert 10 <= inverter.limit_percent <= 13  # (load + headroom) / rated

    def test_limit_is_written_rarely_and_only_after_the_hold_time(self, clock: Clock) -> None:
        inverter = FakeInverter(pv_w=6000, load_w=1000, soc=100)
        thread, shared = make_thread(inverter, pv=PV)
        trace = run(inverter, thread, shared, clock, 1800)
        first = next(elapsed for elapsed, _, limit, _ in trace if limit < 100)
        assert first >= 60
        assert inverter.limit_writes <= 3

    def test_surplus_beyond_the_charge_power_keeps_the_battery_charging_at_its_limit(
        self, clock: Clock
    ) -> None:
        inverter = FakeInverter(pv_w=9000, load_w=500, soc=50, max_charge=3000)
        thread, shared = make_thread(inverter, pv=PV)
        trace = run(inverter, thread, shared, clock, 1800)
        late = [(grid, bat) for elapsed, grid, _, bat in trace if elapsed > 400]
        assert all(-60 < grid < 300 for grid, _ in late)
        assert min(-bat for _, bat in late) > 2700  # no cycling: the battery stays at its limit
        assert inverter.limit_writes <= 4

    def test_limit_follows_a_load_that_rises(self, clock: Clock) -> None:
        inverter = FakeInverter(pv_w=6000, load_w=1000, soc=100)
        thread, shared = make_thread(inverter, pv=PV)
        trace = run(
            inverter, thread, shared, clock, 1800, events={900.0: lambda d: setattr(d, "load_w", 3000.0)}
        )
        before = [limit for elapsed, _, limit, _ in trace if 880 <= elapsed < 900][-1]
        settled = [grid for elapsed, grid, *_ in trace if elapsed > 1300]
        assert inverter.limit_percent > before + 10
        assert all(-60 < grid < 300 for grid in settled)
        assert min(grid for elapsed, grid, *_ in trace if elapsed >= 900) > -2500

    def test_battery_that_can_charge_again_does_not_make_the_limit_cycle(self, clock: Clock) -> None:
        inverter = FakeInverter(pv_w=6000, load_w=1000, soc=100)
        thread, shared = make_thread(inverter, pv=PV)
        run(inverter, thread, shared, clock, 600)
        writes = inverter.limit_writes
        trace = run(
            inverter, thread, shared, clock, 900, events={0.0: lambda d: setattr(d, "soc", 50.0)}
        )
        late = [(grid, bat) for elapsed, grid, _, bat in trace if elapsed > 300]
        assert all(bat < -1000 for _, bat in late)  # charging
        assert all(grid > -100 for grid, _ in late)  # no import
        assert inverter.limit_writes - writes <= 2

    def test_limit_is_released_when_the_pv_collapses(self, clock: Clock) -> None:
        inverter = FakeInverter(pv_w=6000, load_w=1000, soc=100)
        thread, shared = make_thread(inverter, pv=PV)
        run(inverter, thread, shared, clock, 600)
        assert inverter.limit_percent < 100
        run(inverter, thread, shared, clock, 300, events={0.0: lambda d: setattr(d, "pv_w", 500.0)})
        assert inverter.limit_percent == 100
        run(inverter, thread, shared, clock, 300)
        assert inverter.grid_export_w > -150  # the battery covers the load

    def test_battery_does_not_charge_from_the_grid_when_the_surplus_disappears(
        self, clock: Clock
    ) -> None:
        inverter = FakeInverter(pv_w=9000, load_w=500, soc=50, max_charge=3000)
        thread, shared = make_thread(inverter, pv=PV)
        run(inverter, thread, shared, clock, 900)
        assert inverter.limit_percent < 100
        trace = run(inverter, thread, shared, clock, 600, events={0.0: lambda d: setattr(d, "pv_w", 2000.0)})
        assert min(grid for elapsed, grid, *_ in trace if elapsed > 150) > -150
        assert inverter.limit_percent == 100

    def test_non_export_modes_other_than_non_export_do_not_curtail(self, clock: Clock) -> None:
        inverter = FakeInverter(pv_w=6000, load_w=1000, soc=100)
        thread, shared = make_thread(inverter, pv=PV)
        run(inverter, thread, shared, clock, 600, mode=Mode.SELF_CONSUMPTION)
        assert inverter.limit_writes == 0 and inverter.limit_percent == 100

    def test_write_rate_stays_low_with_a_fluctuating_load(self, clock: Clock) -> None:
        loads = [500, 1500, 1000, 3500, 2500, 800, 1200, 3000, 600, 2000, 1000, 3500]

        def set_load(load_w: float) -> Callable[[FakeInverter], None]:
            def event(inverter: FakeInverter) -> None:
                inverter.load_w = load_w

            return event

        events = {k * 300.0: set_load(float(loads[k % len(loads)])) for k in range(48)}
        inverter = FakeInverter(pv_w=7000, load_w=1000, soc=100)
        thread, shared = make_thread(inverter, pv=PV)
        run(inverter, thread, shared, clock, 4 * 3600, events=events)
        assert inverter.limit_writes / 4 <= 30  # per hour, new load every 5 minutes


class TestPvLimitRelease:
    def limited_inverter(self, clock: Clock) -> tuple[FakeInverter, s.SmaStpeControl, ControlSharedDataStore]:
        inverter = FakeInverter(pv_w=6000, load_w=1000, soc=100)
        thread, shared = make_thread(inverter, pv=PV)
        run(inverter, thread, shared, clock, 600)
        assert inverter.limit_percent < 100
        return inverter, thread, shared

    def test_other_mode_releases_before_the_registers_are_written(self, clock: Clock) -> None:
        inverter, thread, _ = self.limited_inverter(clock)
        before = len(inverter.writes)
        thread._apply_command(command(Mode.IDLE))
        new = inverter.writes[before:]
        assert new[0] == (s.REG_PV_LIMIT_PERCENT, (100,))
        assert new[1][0] == s.REG_WCTL_ACT

    def test_forced_mode_releases(self, clock: Clock) -> None:
        inverter, thread, _ = self.limited_inverter(clock)
        thread._apply_command(command(Mode.FORCED_CHARGE))
        assert inverter.limit_percent == 100

    def test_self_consumption_releases(self, clock: Clock) -> None:
        inverter, thread, shared = self.limited_inverter(clock)
        run(inverter, thread, shared, clock, 30, mode=Mode.SELF_CONSUMPTION)
        assert inverter.limit_percent == 100

    def test_safe_state_releases_first(self, clock: Clock) -> None:
        inverter, thread, _ = self.limited_inverter(clock)
        before = len(inverter.writes)
        thread._enter_safe_state()
        assert inverter.writes[before] == (s.REG_PV_LIMIT_PERCENT, (100,))
        assert inverter.limit_percent == 100

    def test_nothing_is_written_if_there_is_no_limit(self) -> None:
        inverter = FakeInverter()
        thread, _ = make_thread(inverter, pv=PV)
        thread._enter_safe_state()
        assert inverter.limit_writes == 0

    def test_failed_write_is_not_remembered_as_done(self, clock: Clock) -> None:
        inverter, thread, _ = self.limited_inverter(clock)
        original = thread._write_registers

        def fail(address: int, values: tuple[int, ...]) -> None:
            if address == s.REG_PV_LIMIT_PERCENT:
                raise OSError("write failed")
            original(address, values)

        thread._write_registers = fail  # type: ignore[method-assign]
        with pytest.raises(OSError):
            thread._release_pv_limit()
        assert thread._pv_limiter is not None and thread._pv_limiter.percent < 100


#----------------------------
# Dianostic log
#----------------------------

"""Diagnostic series of the SMA STP-SE controller.

Paste into test_adaptcontrolsmastpe.py, after the PV limit tests. It uses the helpers of that
module (FakeInverter, make_thread, make_settings, publish_grid, command, PV, _log, GRID_KEY, s,
Mode) and needs no imports of its own.
"""


@dataclass
class _SmaDiag:
    controller: s.SmaStpe
    thread: s.SmaStpeControl
    shared: ControlSharedDataStore
    inverter: FakeInverter


class TestSmaStpeDiagnostics:
    """The series the SMA controller declares are recorded by its control thread."""

    @pytest.fixture
    def diag(self) -> _SmaDiag:
        log = _log(50)
        inverter = FakeInverter(pv_w=6000, load_w=1000, soc=50, temp_c=25.0)  # exports surplus
        thread, shared = make_thread(inverter, pv=PV, log=log)
        # The controller configures which event fields the log collects as series.
        controller = s.SmaStpe("inv1", shared, log, make_settings())
        publish_grid(shared, inverter)
        return _SmaDiag(controller, thread, shared, inverter)

    def test_declared_series_are_recorded(self, diag: _SmaDiag) -> None:
        diag.thread._apply_command(command(Mode.NON_EXPORT, 0.8))
        diag.thread._read_status()
        view = diag.controller.diagnostic_view()
        assert view.device_id == "inv1"
        assert set(view.series) == {
            "readings_updated.ac_power_w",
            "readings_updated.dc_pv_power_w",
            "readings_updated.battery_power_w",
            "readings_updated.soc_percent",
            "readings_updated.temperature_c",
            "readings_updated.read_ms",
            "grid_input.grid_power_w",
            "grid_input.age_s",
            "setpoint_calculated.setpoint_w",
            "setpoint_calculated.charge_limit_w",
            "setpoint_calculated.inputs_valid",
            "command_received.mode",
            "command_received.operation_factor",
            "pv_curtailment_decision.current_limit_percent",
            "pv_curtailment_decision.saturated",
        }

    def test_values_and_units(self, diag: _SmaDiag) -> None:
        inverter = diag.inverter
        diag.thread._apply_command(command(Mode.NON_EXPORT, 0.8))
        diag.thread._read_status()
        view = diag.controller.diagnostic_view()
        assert view.get_series("readings_updated.ac_power_w").iloc[-1] == pytest.approx(inverter.ac_w, abs=1)
        assert view.get_series("readings_updated.dc_pv_power_w").iloc[-1] == inverter.pv_w
        assert view.get_series("readings_updated.soc_percent").iloc[-1] == 50.0
        assert view.get_series("readings_updated.temperature_c").iloc[-1] == 25.0
        # the controller works with export (+), the shared store holds import (+)
        assert view.get_series("grid_input.grid_power_w").iloc[-1] == pytest.approx(inverter.grid_export_w)
        assert view.get_series("command_received.mode").iloc[-1] == "NON_EXPORT"
        assert view.get_series("command_received.operation_factor").iloc[-1] == 0.8
        graphs = {graph.title: graph for graph in view.graphs}
        assert {"Power (W)", "Percent (%)", "Temperature (°C)", "Duration (ms)", "Time (s)"} <= set(graphs)
        assert "Modes and states" in graphs

    def test_read_duration_is_recorded(self, diag: _SmaDiag) -> None:
        diag.thread._read_status()
        view = diag.controller.diagnostic_view()
        assert view.get_series("readings_updated.read_ms").iloc[-1] >= 0.0
        durations = next(graph for graph in view.graphs if graph.title == "Duration (ms)")
        assert "readings_updated.read_ms" in durations.series

    def test_missing_grid_power_holds_the_battery(self, diag: _SmaDiag) -> None:
        diag.shared.remove_sync([GRID_KEY])
        diag.thread._apply_command(command(Mode.NON_EXPORT))
        view = diag.controller.diagnostic_view()
        # no value, no point: the series has a gap instead of a made up number
        assert "grid_input.grid_power_w" not in view.series
        assert list(view.get_series("setpoint_calculated.inputs_valid").values) == [False]
        assert list(view.get_series("setpoint_calculated.setpoint_w").values) == [0.0]
        assert "setpoint_calculated.charge_limit_w" not in view.series  # None while inputs missing

    def test_register_mode_has_no_setpoint(self, diag: _SmaDiag) -> None:
        diag.thread._apply_command(command(Mode.NON_EXPORT))
        since = diag.controller.diagnostic_view().last_seq
        diag.thread._apply_command(command(Mode.IDLE))
        view = diag.controller.diagnostic_view(since_seq=since)
        with_points = {key for key in view.series if len(view.get_series(key)) > 0}
        assert with_points == {"command_received.mode", "command_received.operation_factor"}
        assert view.get_series("command_received.mode").iloc[-1] == "IDLE"
