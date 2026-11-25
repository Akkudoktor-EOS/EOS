"""SMA Sunny Tripower Smart Energy (STP-SE) inverter.

Battery operation modes of the energy management plan and how they are executed. The operation
mode factor (0.0 - 1.0) is the power rate relative to the maximum charge or discharge power the
battery allows at its current operating point (see "Battery power limits").

| Mode | Method | SMA handling |
|---|---|---|
| `IDLE` | delegated | OpMod "Off", charge and discharge window 0 W |
| `SELF_CONSUMPTION` | delegated | OpMod "Auto", grid setpoint 0 W. Factor limits the charge and discharge window |
| `PEAK_SHAVING` | delegated | As `SELF_CONSUMPTION` (GENETIC0 plans it for a discharge-only solution) |
| `NON_EXPORT` | pid + PV limit | Grid power controller holds the grid at 0 W. If the battery cannot take the surplus and export persists, the AC output of the inverter is limited (optional, see below) |
| `GRID_SUPPORT_EXPORT` | delegated | OpMod "Auto", grid setpoint = factor x maximum discharge power (export, +) |
| `GRID_SUPPORT_IMPORT` | delegated | OpMod "Auto", grid setpoint = factor x maximum charge power (import, -), at most the AC charge power of the inverter |
| `FORCED_CHARGE` | forced | Battery setpoint = factor x maximum charge power, at most the AC charge power of the inverter plus the PV power |
| `FORCED_DISCHARGE` | forced | Battery setpoint = factor x maximum discharge power |
| `FAULT` | delegated | Safe state: the inverter regulates itself ("Auto", grid setpoint 0 W) |
| `FREQUENCY_REGULATION`, `RAMP_RATE_CONTROL`, `RESERVE_BACKUP`, `OUTAGE_SUPPLY` | delegated | Not planned by EOS: safe state, as `FAULT` |

Battery power limits
--------------------
The limits are resolved like the parameters of the optimization, from the battery device
``battery_id`` (``devices/batteries/<battery_id>``) and its inverter device (the inverter of
``devices/inverters`` whose ``battery_id`` is this battery):

- The battery profile limits charging and discharging by temperature and SoC, including its
  hard temperature cutoffs. The ``profile_adjustment`` of the battery sets the nominal capacity
  and the power limits of the installation.
- The SoC range is the operational SoC range of the battery within the technical SoC bounds of
  the profile. For the periodic full charge, the explicit charge modes (``FORCED_CHARGE``,
  ``GRID_SUPPORT_IMPORT``) may charge up to the full charge SoC.
- The inverter device limits the discharge power to its maximum AC power and charging from the
  grid to its AC charge power (all charging, if its profile says so). 0 W AC charge power
  disables charging from the grid.
- The maximum powers the inverter reports cap the result.

The limits are evaluated at the measured temperature and SoC in every control interval. Without
SoC or temperature reading the battery is neither charged nor discharged. The configuration is
read again for every command, so changes of the battery or inverter device or the profiles apply
live. If it cannot be resolved (e.g. the battery is not configured or its profile is missing),
the last usable configuration is kept; without any, the battery is held at 0 W and an error is
logged, while the inverter readings are still provided.

Control methods (SMA: write all registers of a method within 10 s, rewrite at least every 300 s):

| Method      | Who closes the loop | SMA registers            |
|-------------|---------------------|--------------------------|
| delegated   | inverter            | 6 registers (CmpBMS)     |
| pid         | thread (PID)        | 2 registers + PID output |
| forced      | thread (open loop)  | 2 registers              |

- "delegated" (6 registers method, CmpBMS): the inverter regulates the grid connection point
  itself. The thread writes the window of the battery power, the grid setpoint and the OpMod.
  They are recalculated in every control interval, written when they change and at least every
  ``REGISTER_REFRESH_S``.
- "pid", "forced" (2 registers method): external battery power setpoint, written in every control
  interval. Used directly by the forced modes and, with the grid power controller on top, by
  `SELF_CONSUMPTION` and `NON_EXPORT` ("pid").

The OpMod "charge only" (2289) and "discharge only" (2290) are not used: they only charge when
the grid connection point exports and only discharge when it imports, so they cannot force
anything. The forced modes use the command method.

Switching between the two methods: the registers of the CmpBMS family are discarded while the
external setpoint is active. So the external setpoint is deactivated first, then the window and
the grid setpoint and then the OpMod are written. Entering the command method writes the open
window ("Auto", full power window) first.

Grid power
----------
The grid power for the "pid" method is not measured here. It is read from the shared data store
under the key configured by ``grid_power_key``, e.g. ``"meter1:grid_net_w"`` of an SMA Energy
Meter controller. Convention of that value: watts, positive when drawing from the grid. Missing
or stale data (older than ``grid_power_max_age_s``) holds the battery at 0 W.

PV limit (``pv_curtailment``)
-----------------------------
`NON_EXPORT` curtails PV production if the battery cannot take the surplus. The inverter limits
its AC output with the parameter "normalized active power limit by the plant control"
(register 41255, WNomPrc, percent of the rated AC power). It only takes effect if the inverter
parameter "active power limitation mode" (register 40210, WMod) is set to "plant control"
(1079). This is not changed here: it is checked on connect, and without it the limit is not used.

The limit is a persistent inverter parameter, SMA advises against cyclic writes. The limiter is
therefore slow and rate limited (see ``SmaPvCurtailmentSettings``), and the feature is off unless
``pv_curtailment`` is configured. The limit is released (100 %) when leaving `NON_EXPORT`, when
the battery can take the surplus again, in the safe state and on shutdown. If the controller
cannot do that (e.g. the process is killed), the limit stays until the inverter's own fallback
for a missing plant control (parameter "timeout") applies. Check that timeout on your inverter.

Register reference: https://github.com/Optic00/ha-modbus-akku-adapter/blob/main/docs/modbus-register-referenz.md

Inverter data
-------------
The inverter provides its readings under the keys of ``INVERTER_KEYS`` (``"<device_id>:<key>"``).

Diagnostics
-----------
The control thread records diagnostic events (see ``ControlDiagnosticLog``). The fields listed in
``SmaStpe.DIAGNOSTIC_SERIES`` are also collected as series for graphs in the dashboard
(``SmaStpe.diagnostic_view()``): the readings of the inverter, the grid power the controller
reads, the setpoint of the grid power controller and its limits, the operation mode and the PV
limit. ``readings_updated`` carries ``read_ms``, the duration of the whole inverter read (all
Modbus transactions), i.e. how old the data is when the controller computes a setpoint. The
series use the sign conventions of the controller: battery power discharge (+), charge (-); grid
power export (+), import (-).

Configuration example::

    "inverter1": {
        "controller_type": "sma:sunny-tripower-smart-energy",
        "host": "192.168.178.6",
        "battery_id": "battery1",
        "grid_power_key": "meter1:grid_net_w",
        "pv_curtailment": {}
    }

``pv_curtailment.rated_power_w`` defaults to the nominal power of the battery's inverter device.
"""

import math
import time
from dataclasses import dataclass
from time import perf_counter  # real duration, independent of a clock patched in tests
from typing import Any, Callable, ClassVar, Literal, Optional, Union, cast

from loguru import logger
from pydantic import Field
from pymodbus.client import ModbusTcpClient

from akkudoktoreos.adapter.controllers.controlabc import (
    ControlCommand,
    ControlDiagnosticLog,
    ControllerBase,
    ControllerBaseSettings,
    ControlSharedDataStore,
)
from akkudoktoreos.adapter.controllers.controldiagnostics import SeriesSpec
from akkudoktoreos.adapter.controllers.controlmodbus import (
    ModbusTcpControllerThreadBase,
)
from akkudoktoreos.adapter.controllers.sma.smastpepid import (
    ControlInputs,
    GridControllerSettings,
    GridPowerController,
    PvExportLimiter,
    PvLimiterSettings,
)
from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.emplan import EnergyManagementPlan, FRBCInstruction
from akkudoktoreos.devices.devicesabc import BatteryOperationMode
from akkudoktoreos.devices.profile.batteryprofile import BatteryProfile
from akkudoktoreos.devices.settings.batteryresolution import (
    BatteryOperatingPoint,
    resolve_battery_parameters,
    resolve_full_charge,
    resolve_nominal_capacity_wh,
)
from akkudoktoreos.devices.settings.batterysettings import BatteriesCommonSettings
from akkudoktoreos.devices.settings.inverterresolution import (
    InverterOperatingParameters,
)
from akkudoktoreos.devices.settings.invertersettings import InverterCommonSettings
from akkudoktoreos.utils.datetimeutil import DateTime

# ---------------------------------------------------------------------------
# Modbus register map (unit id 3 = "SMA Modbus" profile, 32 bit values big endian words)
# ---------------------------------------------------------------------------
SMA_UNIT_ID = 3

# 2 registers method (contiguous: one write of 4 registers) - 1 block
REG_WCTL_SPT = (
    40149  # S32 Inverter.WModCfg.WCtlComCfg.WSpt, battery DC power: + discharge, - charge
)
REG_WCTL_ACT = 40151  # U32 Inverter.WModCfg.WCtlComCfg.WCtlComAct (TAGLIST)
WCTL_ACTIVE = 802
WCTL_INACTIVE = 803

# 6 registers method (CmpBMS) - 1 block
REG_BMS_OPMOD = 40236  # U32 CmpBms.OpMod (TAGLIST). The family also exists at 41259.
REG_BMS_BLOCK = (
    40793  # 5 x 32 bit, contiguous: BatChaMinW, BatChaMaxW, BatDschMinW, BatDschMaxW (U32),
)
#                        GridWSpt (S32, + export, - import)
# BatChaMinW = 40793
# BatChaMaxW = 40795
# BatDschMinW = 40797
# BatDschMaxW = 40799
# GridWSpt = 40801
#
OPMOD_OFF = 303  # no charge, no discharge
OPMOD_AUTO = 1438  # the inverter regulates the grid connection point to GridWSpt

# PV limit
# - config read
REG_PV_WMOD = 30835  # U32 Inverter.WModCfg.WMod, RO, active power limitation mode (persistent)
WMOD_PLANT_CONTROL = 1079  # limit by plant control: required for REG_PV_LIMIT_PERCENT to act
# - control write
REG_PV_LIMIT_PERCENT = 41255  # S16/FIX2/RW Inverter.WModCfg.WCtlComCfg.WNomPrc, % of rated AC power
REG_PV_LIMIT_PERCENT_WO = (
    40023  # S16/FIX2/WO Inverter.WModCfg.WCtlComCfg.WNomPrc, % of rated AC power
)

# Measurements (read, input registers), low speed - 1 block
# - control read
REG_BAT_SOC = 30845  # U32 %
REG_BAT_ACT_CAPACITY = 30847  # U32 Wh, actual capacity
REG_BAT_TEMPERATURE = 30849  # S32 0.1 degC

# Measurements (read, input registers), high speed - 3 blocks
# - control read
REG_DC2_POWER = 30961  # S32 W
REG_DC1_POWER = 30773  # S32 W
REG_AC_POWER = 30775  # S32 W
REG_BAT_CHARGE_POWER = 31393  # U32/FIX0/RO BatChrg.CurBatCha, W
REG_BAT_DISCHARGE_POWER = 31395  # U32/FIX0/RO BatDsch.CurBatDsch, W

# Battery limits (read once on connect, holding registers)
# - config read
REG_BAT_NOM_CAPACITY = 40187  # U32 Wh, nominal capacity
REG_BAT_MAX_CHARGE = 40189  # U32 W, manufacturer's maximum charge power
REG_BAT_MAX_DISCHARGE = 40191  # U32 W, manufacturer's maximum discharge power

# ---------------------------------------------------------------------------
# Control
# ---------------------------------------------------------------------------
CONTROL_INTERVAL_S = 5.0  # control loop period of the command method (also the inverter data rate)
REGISTER_REFRESH_S = 30.0  # refresh of the registers method (SMA: at least every 300 s)

# The battery counts as saturated for the PV limiter this close to its charge power limit.
_SATURATION_MARGIN_W = 100.0

# Shared data keys the inverter provides (suffixes, without "<device_id>:").
AC_POWER_W = "ac_power_w"
DC_PV_POWER_W = "dc_pv_power_w"
BAT_POWER_W = "bat_power_w"  # discharge (+), charge (-)
BAT_SOC_PERCENT = "bat_soc_percent"
BAT_SOC_FACTOR = "bat_soc_factor"  # 0.0 .. 1.0, the unit of the EOS SoC measurement
BAT_TEMPERATURE_C = "bat_temperature_c"
INVERTER_KEYS = (
    AC_POWER_W,
    DC_PV_POWER_W,
    BAT_POWER_W,
    BAT_SOC_PERCENT,
    BAT_SOC_FACTOR,
    BAT_TEMPERATURE_C,
)

# Diagnostic event fields collected as series for graphs (see SmaStpe.DIAGNOSTIC_SERIES).
# Event name -> field names, None: all numeric, str and bool fields of the event.
# The unit of a numeric series follows from the field name suffix (_w, _percent, _c, _ms, ...).
SMA_DIAGNOSTIC_SERIES: SeriesSpec = {
    # ac_power_w, dc_pv_power_w, battery_power_w, soc_percent, temperature_c, read_ms
    "readings_updated": None,
    # grid power as the controller sees it (export +) and the age of the shared data value
    "grid_input": ("grid_power_w", "age_s"),
    # battery setpoint of the command method, the charge limit and whether the inputs were valid
    "setpoint_calculated": ("setpoint_w", "charge_limit_w", "inputs_valid"),
    # the operation mode and factor of the plan
    "command_received": ("mode", "operation_factor"),
    # the PV limit in force and whether the battery was saturated (NON_EXPORT with pv_curtailment)
    "pv_curtailment_decision": ("current_limit_percent", "saturated"),
}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
class SmaPvCurtailmentSettings(SettingsBaseModel):
    """PV curtailment of the mode NON_EXPORT. Configuring it enables the feature.

    The limit is a persistent parameter of the inverter, so the defaults write rarely.
    """

    rated_power_w: Optional[float] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": (
                "Rated AC power of the inverter in W, the 100 % of the power limit. None: the "
                "nominal power of the battery's inverter device (the inverter device whose "
                "'battery_id' is the battery of this controller)."
            ),
            "examples": [None, 10000],
        },
    )
    deadband_w: float = Field(
        default=50.0,
        ge=0,
        json_schema_extra={
            "description": "Export in W that is tolerated without limiting the PV production.",
            "examples": [50.0],
        },
    )
    hold_s: float = Field(
        default=60.0,
        gt=0,
        json_schema_extra={
            "description": (
                "Seconds the export must last, with the battery unable to take it, before the "
                "PV limit is changed."
            ),
            "examples": [60.0],
        },
    )
    min_write_interval_s: float = Field(
        default=60.0,
        ge=10.0,
        json_schema_extra={
            "description": "Minimum seconds between two writes of the PV limit to the inverter.",
            "examples": [60.0],
        },
    )
    step_percent: int = Field(
        default=2,
        ge=1,
        le=50,
        json_schema_extra={
            "description": "Smallest change of the PV limit in percent that is written.",
            "examples": [2],
        },
    )
    headroom_w: float = Field(
        default=100.0,
        ge=0,
        json_schema_extra={
            "description": (
                "Export in W that is left on purpose while the PV production is limited, so "
                "that the grid power controller keeps charging the battery at its maximum power."
            ),
            "examples": [100.0],
        },
    )
    keepalive_s: Optional[float] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": (
                "Write an unchanged PV limit this often while limiting. Needed if the inverter "
                "falls back from the plant control limit without regular communication "
                "(event 'Timeout in communication for active power limitation'). None: off."
            ),
            "examples": [None, 240.0],
        },
    )


class SmaStpeSettings(ControllerBaseSettings):
    """Settings of one SMA Sunny Tripower Smart Energy."""

    controller_type: Literal["sma:sunny-tripower-smart-energy"]
    host: str = Field(
        json_schema_extra={
            "description": "Host name or IP address of the inverter (Modbus TCP).",
            "examples": ["192.168.178.6"],
        },
    )
    port: int = Field(
        default=502,
        ge=1,
        le=65535,
        json_schema_extra={"description": "Modbus TCP port.", "examples": [502]},
    )
    unit_id: int = Field(
        default=SMA_UNIT_ID,
        ge=0,
        le=255,
        json_schema_extra={
            "description": "Modbus unit id of the SMA Modbus register profile.",
            "examples": [3],
        },
    )
    battery_id: str = Field(
        json_schema_extra={
            "description": (
                "Resource id of the battery in the energy management plan whose instructions "
                "this inverter executes. The battery device 'devices/batteries/<battery_id>' "
                "and its inverter device (the inverter device with this 'battery_id') limit "
                "the battery power."
            ),
            "examples": ["battery1"],
        },
    )
    grid_power_key: str = Field(
        pattern=r"^[^:\s]+:[^:\s]+$",
        json_schema_extra={
            "description": (
                "Shared data key '<device_id>:<key>' of the grid power in W, positive when "
                "drawing from the grid. Used by the grid power controller, typically the "
                "'grid_net_w' value of an SMA Energy Meter device."
            ),
            "examples": ["meter1:grid_net_w"],
        },
    )
    grid_power_max_age_s: float = Field(
        default=3.0,
        gt=0,
        json_schema_extra={
            "description": (
                "Maximum age in seconds of the grid power. Older values are not used for "
                "control, the battery is held at 0 W."
            ),
            "examples": [3.0],
        },
    )
    pv_curtailment: Optional[SmaPvCurtailmentSettings] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Let the mode NON_EXPORT curtail PV production when the battery cannot take the "
                "surplus. Needs the inverter parameter 'active power limitation mode' (register "
                "40210) set to plant control (1079). None: no PV curtailment."
            ),
            "examples": [None, {"rated_power_w": 10000}],
        },
    )


# ---------------------------------------------------------------------------
# Register helpers (SMA big endian words, NaN values as defined by SMA)
# ---------------------------------------------------------------------------
_DATATYPE = ModbusTcpClient.DATATYPE
_NAN_U32 = 0xFFFFFFFF
_NAN_S32 = -(1 << 31)
_NAN_S16 = -(1 << 15)


def _decode_u32(regs: list[int]) -> Optional[int]:
    value = cast(
        int, ModbusTcpClient.convert_from_registers(regs, _DATATYPE.UINT32, word_order="big")
    )
    return None if value == _NAN_U32 else value


def _decode_s32(regs: list[int]) -> Optional[int]:
    value = cast(
        int, ModbusTcpClient.convert_from_registers(regs, _DATATYPE.INT32, word_order="big")
    )
    return None if value == _NAN_S32 else value


def _decode_s16(regs: list[int]) -> Optional[int]:
    value = cast(
        int, ModbusTcpClient.convert_from_registers(regs, _DATATYPE.INT16, word_order="big")
    )
    return None if value == _NAN_S16 else value


# Block reads: one Modbus transaction instead of one per value. Each block is contiguous in the
# register map: (first address, number of registers). A value is picked from a block by its own
# address, see _u32_at() and _s32_at().
_READ_POWER_BLOCK = (REG_DC1_POWER, 4)  # DC1 power, AC power
_READ_BATTERY_BLOCK = (REG_BAT_SOC, 6)  # SoC, actual capacity, temperature
_READ_BATTERY_POWER_BLOCK = (REG_BAT_CHARGE_POWER, 4)  # charge power, discharge power
_READ_LIMITS_BLOCK = (REG_BAT_NOM_CAPACITY, 6)  # nominal capacity, max charge/discharge power


def _u32_at(regs: list[int], block: tuple[int, int], address: int) -> Optional[int]:
    """Decode the U32 value at `address` from the registers read for `block`."""
    offset = address - block[0]
    return _decode_u32(regs[offset : offset + 2])


def _s32_at(regs: list[int], block: tuple[int, int], address: int) -> Optional[int]:
    """Decode the S32 value at `address` from the registers read for `block`."""
    offset = address - block[0]
    return _decode_s32(regs[offset : offset + 2])


def _encode_u32(value: int) -> tuple[int, int]:
    high, low = ModbusTcpClient.convert_to_registers(value, _DATATYPE.UINT32, word_order="big")
    return high, low


def _encode_s32(value: int) -> tuple[int, int]:
    high, low = ModbusTcpClient.convert_to_registers(value, _DATATYPE.INT32, word_order="big")
    return high, low


# ---------------------------------------------------------------------------
# Commands and data
# ---------------------------------------------------------------------------
@dataclass(frozen=True, kw_only=True)
class SmaCommand(ControlCommand):
    """Declarative command.

    The thread translates it to registers using the battery power limits at the current
    operating point (see ``_battery_power_limits``).
    """

    mode: BatteryOperationMode
    operation_factor: float = 1.0  # power rate relative to the maximum battery power
    # Optional narrowing of the SoC range of the battery for this command.
    soc_min_percent: Optional[float] = None  # no discharge at or below
    soc_max_percent: Optional[float] = None  # no charge at or above


@dataclass(frozen=True, eq=False)
class SmaBatteryContext:
    """Battery and inverter data the control thread limits the battery power with.

    Attributes:
        battery: Settings of the battery device: profile adjustment (nominal capacity, power
            limits of the installation) and operational SoC range.
        profile: Compiled battery profile of the battery.
        capacity_wh: Effective nominal capacity [Wh]. None: the nominal capacity the inverter
            reports (only for a profile without battery device, e.g. the diagnostics tool).
        full_charge_soc_percent: SoC the explicit charge modes may charge to for the periodic
            full charge [%], None if the battery has no periodic full charge.
        inverter: Effective parameters of the battery's inverter device, None if no inverter
            device is configured for the battery.
    """

    battery: BatteriesCommonSettings
    profile: BatteryProfile
    capacity_wh: Optional[int] = None
    full_charge_soc_percent: Optional[int] = None
    inverter: Optional[InverterOperatingParameters] = None

    @classmethod
    def from_profile(cls, profile: BatteryProfile) -> "SmaBatteryContext":
        """Context of a battery profile without battery device.

        Full SoC range (within the technical SoC bounds of the profile), no power limits of
        the installation, no inverter device; the capacity is the one the inverter reports.
        """
        return cls(
            battery=BatteriesCommonSettings(
                device_id="battery", battery_profile=profile.battery_model or "battery"
            ),
            profile=profile,
        )

    def same_as(self, other: Optional["SmaBatteryContext"]) -> bool:
        """Whether ``other`` holds the same data.

        The profile is compared by identity (the profile store returns the same object until
        the profile changes), the battery settings by their values: settings instances with
        the same values need not compare equal.
        """
        return (
            other is not None
            and self.profile is other.profile
            and self.capacity_wh == other.capacity_wh
            and self.full_charge_soc_percent == other.full_charge_soc_percent
            and self.inverter == other.inverter
            and self.battery.model_dump() == other.battery.model_dump()
        )


# The thread gets a fixed context or a function returning the context in effect.
SmaBatterySource = Union[SmaBatteryContext, Callable[[], SmaBatteryContext]]


@dataclass(frozen=True)
class _BatteryLimits:
    capacity_wh: int
    max_charge_w: int
    max_discharge_w: int


@dataclass(frozen=True)
class _Readings:
    t: float  # monotonic time of the reading
    ac_w: int
    dc_pv_w: int
    bat_w: int  # discharge (+), charge (-)
    soc_percent: Optional[int]
    temp_c: Optional[float]


@dataclass(frozen=True)
class _DelegatedParams:
    """Values of the 6 registers method."""

    op_mod: int
    cha_min_w: int
    cha_max_w: int
    dsch_min_w: int
    dsch_max_w: int
    grid_w: int  # export (+), import (-)


# ---------------------------------------------------------------------------
# Battery mode to SMA control mapping
# ---------------------------------------------------------------------------

SmaControlMethod = Literal["delegated", "pid", "forced"]
SmaGridSetpoint = Literal["zero", "export", "import"]


@dataclass(frozen=True)
class SmaModeMapping:
    """Mapping of an EOS battery operation mode to SMA control commands."""

    method: SmaControlMethod
    op_mod: Optional[int] = None
    open_battery_window: bool = True
    grid_setpoint: Optional[SmaGridSetpoint] = None
    factor_limits_window: bool = False


# Central mapping: EOS battery operation mode -> SMA control method and commands.
SMA_MODE_MAPPING: dict[BatteryOperationMode, SmaModeMapping] = {
    # Six-register method: inverter regulates the grid connection point.
    BatteryOperationMode.IDLE: SmaModeMapping(
        method="delegated",
        op_mod=OPMOD_OFF,
        open_battery_window=False,
        grid_setpoint="zero",
    ),
    BatteryOperationMode.SELF_CONSUMPTION: SmaModeMapping(
        method="delegated",
        op_mod=OPMOD_AUTO,
        grid_setpoint="zero",
        factor_limits_window=True,
    ),
    # GENETIC0 plans PEAK_SHAVING for a discharge-only solution: cover the load from the battery.
    BatteryOperationMode.PEAK_SHAVING: SmaModeMapping(
        method="delegated",
        op_mod=OPMOD_AUTO,
        grid_setpoint="zero",
        factor_limits_window=True,
    ),
    BatteryOperationMode.GRID_SUPPORT_EXPORT: SmaModeMapping(
        method="delegated",
        op_mod=OPMOD_AUTO,
        grid_setpoint="export",
    ),
    BatteryOperationMode.GRID_SUPPORT_IMPORT: SmaModeMapping(
        method="delegated",
        op_mod=OPMOD_AUTO,
        grid_setpoint="import",
    ),
    BatteryOperationMode.FAULT: SmaModeMapping(
        method="delegated",
        op_mod=OPMOD_AUTO,
        grid_setpoint="zero",
    ),
    # Modes no optimizer of EOS plans: the safe state, as for FAULT.
    **{
        mode: SmaModeMapping(method="delegated", op_mod=OPMOD_AUTO, grid_setpoint="zero")
        for mode in (
            BatteryOperationMode.FREQUENCY_REGULATION,
            BatteryOperationMode.RAMP_RATE_CONTROL,
            BatteryOperationMode.RESERVE_BACKUP,
            BatteryOperationMode.OUTAGE_SUPPLY,
        )
    },
    # Two-register method: external battery setpoint with grid power PID controller.
    BatteryOperationMode.NON_EXPORT: SmaModeMapping(method="pid"),
    # Two-register method: external battery setpoint.
    BatteryOperationMode.FORCED_CHARGE: SmaModeMapping(method="forced"),
    BatteryOperationMode.FORCED_DISCHARGE: SmaModeMapping(method="forced"),
}


# Check for unmapped modes
_unmapped_modes = set(BatteryOperationMode) - set(SMA_MODE_MAPPING)
if _unmapped_modes:
    raise RuntimeError(
        f"Missing SMA control mappings for battery operation modes: {_unmapped_modes}"
    )

# Derived mode groups. There is only one source of truth: SMA_MODE_MAPPING.
PID_MODES = frozenset(mode for mode, mapping in SMA_MODE_MAPPING.items() if mapping.method == "pid")
FORCED_MODES = frozenset(
    mode for mode, mapping in SMA_MODE_MAPPING.items() if mapping.method == "forced"
)


def _open_delegated_params(limits: _BatteryLimits, grid_w: int = 0) -> _DelegatedParams:
    """The inverter regulates the grid connection point to `grid_w`, full battery power window."""
    return _DelegatedParams(OPMOD_AUTO, 0, limits.max_charge_w, 0, limits.max_discharge_w, grid_w)


def _delegated_params_for_mode(
    mode: BatteryOperationMode,
    *,
    charge_max_w: int,
    discharge_max_w: int,
    export_w: int,
    import_w: int,
) -> _DelegatedParams:
    """Translate a delegated mode into the values of the 6 registers method.

    Args:
        mode: Battery operation mode with a delegated mapping.
        charge_max_w: Upper end of the charge window [W], used if the mapping opens it.
        discharge_max_w: Upper end of the discharge window [W], used if the mapping opens it.
        export_w: Grid setpoint of a mapping with "export" setpoint, as positive power [W].
        import_w: Grid setpoint of a mapping with "import" setpoint, as positive power [W].
    """
    mapping = SMA_MODE_MAPPING[mode]

    if mapping.method != "delegated":
        raise ValueError(f"Battery operation mode {mode} does not use delegated control")

    if mapping.op_mod is None or mapping.grid_setpoint is None:
        raise ValueError(f"Incomplete SMA delegated mapping for {mode}")

    if mapping.open_battery_window:
        cha_min_w = 0
        cha_max_w = charge_max_w
        dsch_min_w = 0
        dsch_max_w = discharge_max_w
    else:
        cha_min_w = cha_max_w = 0
        dsch_min_w = dsch_max_w = 0

    grid_setpoints = {
        "zero": 0,
        "export": export_w,
        "import": -import_w,
    }

    return _DelegatedParams(
        op_mod=mapping.op_mod,
        cha_min_w=cha_min_w,
        cha_max_w=cha_max_w,
        dsch_min_w=dsch_min_w,
        dsch_max_w=dsch_max_w,
        grid_w=grid_setpoints[mapping.grid_setpoint],
    )


# ---------------------------------------------------------------------------
# Battery power limits
# ---------------------------------------------------------------------------

# Modes that charge on purpose; they may charge up to the SoC of the periodic full charge.
FULL_CHARGE_MODES = frozenset(
    {BatteryOperationMode.FORCED_CHARGE, BatteryOperationMode.GRID_SUPPORT_IMPORT}
)


@dataclass(frozen=True)
class _BatteryPowerLimits:
    """Battery power limits at the current operating point, before the operation factor.

    Attributes:
        charge_w: Maximum charge power [W], 0 at or above the maximum SoC.
        discharge_w: Maximum discharge power [W], 0 at or below the minimum SoC.
        soc_min_percent: No discharge at or below [%].
        soc_max_percent: No charge at or above [%].
        capacity_wh: Nominal capacity the limits refer to [Wh].
        ac_charge_w: Maximum power the battery may charge from the grid [W], None for no
            limit besides ``charge_w``.
    """

    charge_w: int
    discharge_w: int
    soc_min_percent: float
    soc_max_percent: float
    capacity_wh: int
    ac_charge_w: Optional[int] = None


def _no_power_limits(command: SmaCommand, limits: _BatteryLimits) -> _BatteryPowerLimits:
    """Limits that neither charge nor discharge, e.g. without SoC or temperature reading."""
    return _BatteryPowerLimits(
        charge_w=0,
        discharge_w=0,
        soc_min_percent=command.soc_min_percent if command.soc_min_percent is not None else 0.0,
        soc_max_percent=command.soc_max_percent if command.soc_max_percent is not None else 100.0,
        capacity_wh=limits.capacity_wh,
    )


def _battery_power_limits(
    command: SmaCommand,
    readings: _Readings,
    limits: _BatteryLimits,
    context: Optional[SmaBatteryContext],
) -> _BatteryPowerLimits:
    """Calculate the battery power limits at the current operating point.

    The battery parameters are resolved like for the optimization (``resolve_battery_parameters``)
    at the measured temperature and SoC: battery profile (temperature and SoC dependent limits,
    hard temperature cutoffs, model power limits, technical SoC bounds) and profile adjustment of
    the battery (nominal capacity, power limits of the installation), with the operational SoC
    range of the battery. The inverter device caps the discharge power by its maximum AC power
    and the charging from the grid by its AC charge power. The maximum powers the inverter
    reports cap the result.

    Missing SoC or temperature, or no context, disables both directions: the limits and the hard
    temperature cutoffs cannot be checked without them.

    Args:
        command: Requested battery operation.
        readings: Latest inverter readings.
        limits: Battery limits reported by the inverter.
        context: Battery and inverter data, None if none is usable.

    Returns:
        The limits, before the operation factor.

    Raises:
        BatteryResolutionError: If the battery parameters cannot be resolved (configuration
            error, e.g. an empty SoC range).
    """
    if context is None or readings.soc_percent is None or readings.temp_c is None:
        return _no_power_limits(command, limits)

    capacity_wh = context.capacity_wh if context.capacity_wh is not None else limits.capacity_wh
    parameters = resolve_battery_parameters(
        context.battery,
        context.profile,
        capacity_wh,
        BatteryOperatingPoint(
            temperature_degC=readings.temp_c, soc_factor=readings.soc_percent / 100.0
        ),
    )

    soc_min = float(parameters.min_soc_percentage)
    soc_max = float(parameters.max_soc_percentage)
    if command.mode in FULL_CHARGE_MODES and context.full_charge_soc_percent is not None:
        soc_max = max(soc_max, float(context.full_charge_soc_percent))
    if command.soc_min_percent is not None:
        soc_min = max(soc_min, command.soc_min_percent)
    if command.soc_max_percent is not None:
        soc_max = min(soc_max, command.soc_max_percent)

    charge_w = min(parameters.max_charge_power_w, float(limits.max_charge_w))
    discharge_w = min(parameters.max_discharge_power_w, float(limits.max_discharge_w))
    ac_charge_w: Optional[float] = None
    inverter = context.inverter
    if inverter is not None:
        discharge_w = min(discharge_w, inverter.max_power_w)
        ac_charge_w = max(0.0, inverter.max_ac_charge_power_w)
        if inverter.ac_charge_limits_total_charge:
            charge_w = min(charge_w, ac_charge_w)

    if readings.soc_percent >= soc_max:
        charge_w = 0.0
    if readings.soc_percent <= soc_min:
        discharge_w = 0.0

    return _BatteryPowerLimits(
        charge_w=max(0, round(charge_w)),
        discharge_w=max(0, round(discharge_w)),
        soc_min_percent=soc_min,
        soc_max_percent=soc_max,
        capacity_wh=capacity_wh,
        ac_charge_w=None if ac_charge_w is None else round(ac_charge_w),
    )


def _grid_charge_capped_w(charge_w: float, power_limits: _BatteryPowerLimits) -> int:
    """Charge power from the grid, capped by the AC charge power of the inverter."""
    if power_limits.ac_charge_w is not None:
        charge_w = min(charge_w, power_limits.ac_charge_w)
    return max(0, round(charge_w))


# ---------------------------------------------------------------------------
# Control thread
# ---------------------------------------------------------------------------
_LANE_COMMAND = "command"  # external battery setpoint active (2 registers method)
_LANE_DELEGATED = "delegated"  # the inverter regulates itself (6 registers method)


class SmaStpeControl(ModbusTcpControllerThreadBase[SmaCommand]):
    """Control thread of one STP-SE.

    Publishes the inverter readings into the shared data store (INVERTER_KEYS) and reads the
    grid power for the "pid" method from it.

    The battery and inverter data (``SmaBatteryContext``) are given as a fixed context or as a
    function that returns the context in effect. The function is called for every command, so
    configuration and profile updates (e.g. by the REST API of the profile store) apply live.
    If it fails, the last usable context is kept; without any, the battery is held at 0 W.
    """

    def __init__(
        self,
        device_id: str,
        shared_data: ControlSharedDataStore,
        diagnostic_log: ControlDiagnosticLog,
        *,
        host: str,
        battery: SmaBatterySource,
        grid_power_key: str,
        grid_power_max_age_s: float = 3.0,
        port: int = 502,
        unit_id: int = SMA_UNIT_ID,
        controller_settings: Optional[GridControllerSettings] = None,
        pv_limiter_settings: Optional[PvLimiterSettings] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            device_id, shared_data, diagnostic_log, host=host, port=port, unit_id=unit_id, **kwargs
        )
        self._prefix = f"{device_id}:"
        self._battery_source = battery
        # The context in effect; None: none usable yet, the battery is held at 0 W.
        self._battery: Optional[SmaBatteryContext] = None
        self._battery_ok = True
        self._limits_ok = True
        self._grid_power_key = grid_power_key
        self._grid_power_max_age_s = grid_power_max_age_s
        self._controller = GridPowerController(controller_settings)
        self._pv_limiter = PvExportLimiter(pv_limiter_settings) if pv_limiter_settings else None
        self._pv_limit_available = False  # set in _connect()
        self._limits: Optional[_BatteryLimits] = None
        self._readings: Optional[_Readings] = None
        self._lane: Optional[str] = None
        # Last values of the 6 registers method and when they were written (monotonic).
        self._delegated_written: Optional[tuple[_DelegatedParams, float]] = None
        self._pid_active = False
        self._inputs_ok = True
        self._refresh_battery()

    def published_keys(self) -> tuple[str, ...]:
        """Return the fully qualified shared data keys this thread publishes."""
        return tuple(self._prefix + key for key in INVERTER_KEYS)

    # ---------------- Modbus helpers ----------------
    def _read_regs(self, address: int, count: int = 2) -> list[int]:
        return self._read_input_registers(address, count)

    def _write_regs(self, address: int, values: tuple[int, ...]) -> None:
        """Write registers.

        Record the attempt in the diagnostic log.
        """
        self._record_diagnostic(
            "register_write",
            address=address,
            values=values,
        )
        try:
            self._write_registers(address, values)
        except Exception as exc:
            self._record_diagnostic(
                "register_write_error",
                address=address,
                values=values,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise

    def _require_limits(self) -> _BatteryLimits:
        if self._limits is None:  # set in _connect()
            raise RuntimeError(f"{self.device_id}: battery limits not read")
        return self._limits

    def _read_inverter(self, max_age: float = 0.5) -> _Readings:
        """Read the inverter measurements.

        A reading younger than max_age is reused, so the control step and the status of one cycle
        share one set of Modbus reads.
        """
        cached = self._readings
        if cached is not None and time.monotonic() - cached.t <= max_age:
            return cached
        started = perf_counter()
        power = self._read_regs(*_READ_POWER_BLOCK)
        dc2 = self._read_regs(REG_DC2_POWER)  # not adjacent to the others
        battery = self._read_regs(*_READ_BATTERY_BLOCK)
        battery_power = self._read_regs(*_READ_BATTERY_POWER_BLOCK)

        temp_raw = _s32_at(battery, _READ_BATTERY_BLOCK, REG_BAT_TEMPERATURE)
        charge_w = _u32_at(battery_power, _READ_BATTERY_POWER_BLOCK, REG_BAT_CHARGE_POWER) or 0
        discharge_w = (
            _u32_at(battery_power, _READ_BATTERY_POWER_BLOCK, REG_BAT_DISCHARGE_POWER) or 0
        )
        readings = _Readings(
            t=time.monotonic(),
            ac_w=_s32_at(power, _READ_POWER_BLOCK, REG_AC_POWER) or 0,
            dc_pv_w=max(0, _s32_at(power, _READ_POWER_BLOCK, REG_DC1_POWER) or 0)
            + max(0, _decode_s32(dc2) or 0),
            bat_w=discharge_w - charge_w,
            soc_percent=_u32_at(battery, _READ_BATTERY_BLOCK, REG_BAT_SOC),
            temp_c=None if temp_raw is None else temp_raw / 10.0,
        )
        read_ms = (perf_counter() - started) * 1000.0
        self._readings = readings
        # Record diagnostics
        self._record_diagnostic(
            "readings_updated",
            ac_power_w=readings.ac_w,
            dc_pv_power_w=readings.dc_pv_w,
            battery_power_w=readings.bat_w,
            soc_percent=readings.soc_percent,
            temperature_c=readings.temp_c,
            read_ms=read_ms,  # all Modbus transactions of the read, age of the data
        )
        return readings

    def _grid_power_w(self) -> Optional[float]:
        """Grid power for the controller, export (+), import (-); None if missing or stale.

        The shared data value is positive when drawing from the grid, so the sign is flipped.
        """
        entry = self.shared_data.get_with_age(self._grid_power_key)
        if entry is None:
            self._record_diagnostic(
                "grid_input",
                source=self._grid_power_key,
                grid_power_w=None,
                valid=False,
                reason="missing",
            )
            return None
        if entry[1] > self._grid_power_max_age_s:
            self._record_diagnostic(
                "grid_input",
                source=self._grid_power_key,
                grid_power_w=None,
                valid=False,
                reason="stale",
                age_s=entry[1],
                max_age_s=self._grid_power_max_age_s,
            )
            return None
        value = entry[0]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            self._record_diagnostic(
                "grid_input",
                source=self._grid_power_key,
                grid_power_w=None,
                source_value_w=value,
                valid=False,
                reason="invalid",
                age_s=entry[1],
                max_age_s=self._grid_power_max_age_s,
            )
            return None
        grid_w = -float(value)
        self._record_diagnostic(
            "grid_input",
            source=self._grid_power_key,
            grid_power_w=grid_w,
            source_value_w=float(value),
            age_s=entry[1],
            max_age_s=self._grid_power_max_age_s,
            valid=True,
        )
        return grid_w

    # ---------------- hooks (control thread side) ----------------
    def _connect(self) -> None:
        super()._connect()
        limits = self._read_holding_registers(*_READ_LIMITS_BLOCK)
        capacity = _u32_at(limits, _READ_LIMITS_BLOCK, REG_BAT_NOM_CAPACITY)
        max_charge = _u32_at(limits, _READ_LIMITS_BLOCK, REG_BAT_MAX_CHARGE)
        max_discharge = _u32_at(limits, _READ_LIMITS_BLOCK, REG_BAT_MAX_DISCHARGE)
        if capacity is None or max_charge is None or max_discharge is None:
            raise ConnectionError(f"{self.device_id}: battery limits not available (no battery?)")
        self._limits = _BatteryLimits(capacity, max_charge, max_discharge)
        self._lane = None  # unknown after (re)connect: the first command sets it up from scratch
        self._delegated_written = None
        self._pid_active = False  # start the control loop from scratch after a (re)connect
        self._init_pv_limit()
        # Record diagnostics
        self._record_diagnostic(
            "battery_limits",
            capacity_wh=capacity,
            max_charge_w=max_charge,
            max_discharge_w=max_discharge,
            battery_model=None if self._battery is None else self._battery.profile.battery_model,
        )
        self._record_diagnostic(
            "pv_curtailment_initialized",
            configured=self._pv_limiter is not None,
            available=self._pv_limit_available,
            current_limit_percent=(
                self._pv_limiter.percent
                if self._pv_limiter is not None and self._pv_limit_available
                else None
            ),
        )

    def _init_pv_limit(self) -> None:
        """Check that the PV limit can be used and read the limit in force.

        The feature is optional: a device that refuses the registers only loses the feature,
        the battery control goes on. Connection errors still raise.
        """
        self._pv_limit_available = False
        if self._pv_limiter is None:
            return
        try:
            wmod = _decode_u32(self._read_holding_registers(REG_PV_WMOD, 2))
            if wmod != WMOD_PLANT_CONTROL:
                logger.error(
                    "{}: PV curtailment not used: active power limitation mode (register {}) is "
                    "{}, it must be {} (plant control)",
                    self.device_id,
                    REG_PV_WMOD,
                    wmod,
                    WMOD_PLANT_CONTROL,
                )
                return
            percent = _decode_s16(self._read_holding_registers(REG_PV_LIMIT_PERCENT, 1))
        except OSError as exc:
            logger.error("{}: PV curtailment not used: {}", self.device_id, exc)
            return
        if percent is None:
            logger.error(
                "{}: PV curtailment not used: register {} is not available",
                self.device_id,
                REG_PV_LIMIT_PERCENT,
            )
            return
        self._pv_limiter.reset(percent)
        self._pv_limit_available = True
        if percent < 100:
            logger.warning(
                "{}: PV limit of {} % found, it is released if not needed", self.device_id, percent
            )

    def _read_status(self) -> dict[str, Any]:
        """Read the inverter, publish the readings into the shared data store.

        Values the inverter reports as not available are left out: they age out in the store
        instead of being replaced by a made-up number.
        """
        readings = self._read_inverter()
        data: dict[str, Any] = {
            AC_POWER_W: readings.ac_w,
            DC_PV_POWER_W: readings.dc_pv_w,
            BAT_POWER_W: readings.bat_w,
        }
        if readings.soc_percent is not None:
            data[BAT_SOC_PERCENT] = readings.soc_percent
            data[BAT_SOC_FACTOR] = readings.soc_percent / 100.0
        if readings.temp_c is not None:
            data[BAT_TEMPERATURE_C] = readings.temp_c
        self.shared_data.publish_sync({self._prefix + key: value for key, value in data.items()})
        if self._pv_limiter is not None and self._pv_limit_available:
            return {**data, "pv_limit_percent": self._pv_limiter.percent}
        return data

    def _refresh_battery(self) -> Optional[SmaBatteryContext]:
        """Fetch the battery context in effect from the source.

        A context that cannot be resolved (e.g. the battery is not configured, or its profile is
        missing, unreadable or invalid while being edited) keeps the last usable one; without
        any, the battery is held at 0 W. This is logged once until the context is usable again.
        """
        source = self._battery_source
        if isinstance(source, SmaBatteryContext):
            context = source
        else:
            try:
                context = source()
            except (OSError, ValueError) as exc:
                if self._battery_ok:
                    kept = (
                        f"keeping battery profile '{self._battery.profile.battery_model}'"
                        if self._battery is not None
                        else "battery held at 0 W"
                    )
                    logger.error(
                        "{}: battery configuration not usable, {}: {}", self.device_id, kept, exc
                    )
                    self._record_diagnostic(
                        "battery_context_error",
                        battery_model=None
                        if self._battery is None
                        else self._battery.profile.battery_model,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                self._battery_ok = False
                return self._battery
        if not self._battery_ok:
            logger.info("{}: battery configuration usable again", self.device_id)
            self._battery_ok = True
        if not context.same_as(self._battery):
            if self._battery is not None:
                logger.info(
                    "{}: battery configuration updated, battery profile '{}'",
                    self.device_id,
                    context.profile.battery_model,
                )
            self._record_diagnostic(
                "battery_context_updated",
                battery_model=context.profile.battery_model,
                capacity_wh=context.capacity_wh,
                min_soc_percent=context.battery.min_soc_percentage,
                max_soc_percent=context.battery.max_soc_percentage,
                full_charge_soc_percent=context.full_charge_soc_percent,
                inverter_max_power_w=None
                if context.inverter is None
                else context.inverter.max_power_w,
                inverter_max_ac_charge_power_w=None
                if context.inverter is None
                else context.inverter.max_ac_charge_power_w,
            )
            self._battery = context
        return context

    def _power_limits(self, command: SmaCommand, readings: _Readings) -> _BatteryPowerLimits:
        """Battery power limits at the current operating point (see ``_battery_power_limits``).

        A configuration the limits cannot be resolved from holds the battery at 0 W; this is
        logged once until the limits can be resolved again.
        """
        limits = self._require_limits()
        try:
            power_limits = _battery_power_limits(command, readings, limits, self._battery)
        except ValueError as exc:
            if self._limits_ok:
                logger.error(
                    "{}: battery power limits not resolvable, battery held at 0 W: {}",
                    self.device_id,
                    exc,
                )
                self._record_diagnostic(
                    "battery_limits_error", error=str(exc), error_type=type(exc).__name__
                )
            self._limits_ok = False
            return _no_power_limits(command, limits)
        if not self._limits_ok:
            logger.info("{}: battery power limits resolvable again", self.device_id)
            self._limits_ok = True
        return power_limits

    def _apply_command(self, command: SmaCommand) -> None:
        """Apply an EOS battery operation mode using its SMA mapping."""
        mapping = SMA_MODE_MAPPING[command.mode]
        self._refresh_battery()

        self._record_diagnostic(
            "command_received",
            mode=command.mode.name,
            operation_factor=command.operation_factor,
            soc_min_percent=command.soc_min_percent,
            soc_max_percent=command.soc_max_percent,
            label=command.label,
        )

        if mapping.method == "pid":
            self._apply_pid(command)
            return

        self._pid_active = False
        self._release_pv_limit()

        if mapping.method == "forced":
            self._apply_forced(command)
        elif mapping.method == "delegated":
            self._apply_delegated(command)
        else:
            raise ValueError(f"Unsupported SMA control method: {mapping.method}")

    def _enter_safe_state(self) -> None:
        """Hand over to the inverter: external setpoint inactive, "Auto" with grid setpoint 0 W."""
        self._pid_active = False
        self._release_pv_limit()
        self._write_delegated(_open_delegated_params(self._require_limits()))

    # ---------------- control methods ----------------
    def _write_delegated(self, p: _DelegatedParams) -> None:
        """6 registers method.

        The external setpoint is deactivated first, the registers of this method are discarded
        while it is active. Then the windows and the grid setpoint, then the OpMod.
        """
        self._write_regs(REG_WCTL_ACT, _encode_u32(WCTL_INACTIVE))
        self._write_regs(
            REG_BMS_BLOCK,
            (
                *_encode_u32(p.cha_min_w),
                *_encode_u32(p.cha_max_w),
                *_encode_u32(p.dsch_min_w),
                *_encode_u32(p.dsch_max_w),
                *_encode_s32(p.grid_w),
            ),
        )
        self._write_regs(REG_BMS_OPMOD, _encode_u32(p.op_mod))
        self._lane = _LANE_DELEGATED
        self._delegated_written = (p, time.monotonic())

    def _enter_command_lane(self) -> None:
        """Prepare the 2 registers method: open window and "Auto", written once."""
        if self._lane == _LANE_COMMAND:
            return
        self._write_delegated(_open_delegated_params(self._require_limits()))
        self._lane = _LANE_COMMAND

    def _write_setpoint(self, setpoint_w: int) -> None:
        """2 registers method: external battery power setpoint, active."""
        self._write_regs(REG_WCTL_SPT, (*_encode_s32(setpoint_w), *_encode_u32(WCTL_ACTIVE)))

    def _apply_forced(self, command: SmaCommand) -> None:
        """Forced charge or discharge. Called in every control interval."""
        self._enter_command_lane()
        readings = self._read_inverter()
        power_limits = self._power_limits(command, readings)

        if command.mode is BatteryOperationMode.FORCED_CHARGE:
            charge_w = command.operation_factor * power_limits.charge_w
            if power_limits.ac_charge_w is not None:
                # What the PV does not provide comes from the grid.
                charge_w = min(charge_w, power_limits.ac_charge_w + max(0, readings.dc_pv_w))
            setpoint_w = -max(0, round(charge_w))
        elif command.mode is BatteryOperationMode.FORCED_DISCHARGE:
            setpoint_w = round(command.operation_factor * power_limits.discharge_w)
        else:
            raise ValueError(f"Unsupported forced mode: {command.mode}")

        self._record_diagnostic(
            "setpoint_calculated",
            mode=command.mode.name,
            operation_factor=command.operation_factor,
            soc_percent=readings.soc_percent,
            temperature_c=readings.temp_c,
            max_charge_w=power_limits.charge_w,
            max_discharge_w=power_limits.discharge_w,
            ac_charge_limit_w=power_limits.ac_charge_w,
            setpoint_w=setpoint_w,
        )
        self._write_setpoint(setpoint_w)

    def _apply_pid(self, command: SmaCommand) -> None:
        """Grid power control on the 2 registers method. Called in every control interval."""
        limits = self._require_limits()
        self._enter_command_lane()
        readings = self._read_inverter()
        grid_w = self._grid_power_w()
        power_limits = self._power_limits(command, readings)
        # the operation factor limits the battery power in both directions
        max_charge_w = round(command.operation_factor * power_limits.charge_w)
        max_discharge_w = round(command.operation_factor * power_limits.discharge_w)

        if grid_w is None or readings.soc_percent is None or readings.temp_c is None:
            if self._inputs_ok:
                logger.warning(
                    "{}: control inputs missing (grid power '{}': {}, soc: {}, temperature: {}), "
                    "battery held at 0 W",
                    self.device_id,
                    self._grid_power_key,
                    grid_w is not None,
                    readings.soc_percent is not None,
                    readings.temp_c is not None,
                )
            self._inputs_ok = False
            self._pid_active = False
            setpoint_w = 0
            charge_limit_w = None
        else:
            if not self._inputs_ok:
                logger.info("{}: control inputs available again", self.device_id)
            self._inputs_ok = True
            # Already derated by the battery limits (temperature, SoC, operation factor).
            charge_limit_w = max_charge_w
            if not self._pid_active:
                self._controller.reset()
                self._pid_active = True
            inputs = ControlInputs(
                now=time.monotonic(),
                grid_w=grid_w,
                ac_w=readings.ac_w,
                dc_pv_w=readings.dc_pv_w,
                bat_dc_w=readings.bat_w,
                soc_percent=readings.soc_percent,
                temp_c=readings.temp_c,
                capacity_wh=power_limits.capacity_wh,
                hw_max_charge_w=limits.max_charge_w,
                hw_max_discharge_w=limits.max_discharge_w,
                soc_min_percent=power_limits.soc_min_percent,
                soc_max_percent=power_limits.soc_max_percent,
                # the battery limits replace the built-in tables of the controller
                max_charge_w=max_charge_w,
                max_discharge_w=max_discharge_w,
            )
            setpoint_w = self._controller.step(inputs)
            self._record_diagnostic(
                "pid_calculation",
                mode=command.mode.name,
                grid_power_w=inputs.grid_w,
                ac_power_w=inputs.ac_w,
                dc_pv_power_w=inputs.dc_pv_w,
                battery_power_w=inputs.bat_dc_w,
                soc_percent=inputs.soc_percent,
                temperature_c=inputs.temp_c,
                max_charge_w=max_charge_w,
                max_discharge_w=max_discharge_w,
                charge_limit_w=charge_limit_w,
                setpoint_w=setpoint_w,
            )
        self._record_diagnostic(
            "setpoint_calculated",
            mode=command.mode.name,
            setpoint_w=setpoint_w,
            inputs_valid=self._inputs_ok,
            charge_limit_w=charge_limit_w,
        )
        self._write_setpoint(setpoint_w)

        if command.mode is BatteryOperationMode.NON_EXPORT:
            self._update_pv_limit(
                grid_w, readings, setpoint_w, charge_limit_w, power_limits.soc_max_percent
            )
        else:
            self._release_pv_limit()

    def _apply_delegated(self, command: SmaCommand) -> None:
        """Apply a delegated mode with the 6 registers method.

        Called in every control interval. The battery windows and the grid setpoint are
        calculated from the battery power limits at the current operating point. They are
        written when they change, and at least every ``REGISTER_REFRESH_S``.
        """
        mapping = SMA_MODE_MAPPING[command.mode]
        factor = command.operation_factor

        readings: Optional[_Readings] = None
        if mapping.open_battery_window or mapping.grid_setpoint in ("export", "import"):
            readings = self._read_inverter()
            power_limits = self._power_limits(command, readings)
        else:
            power_limits = _no_power_limits(command, self._require_limits())

        window_factor = factor if mapping.factor_limits_window else 1.0
        params = _delegated_params_for_mode(
            command.mode,
            charge_max_w=round(window_factor * power_limits.charge_w),
            discharge_max_w=round(window_factor * power_limits.discharge_w),
            export_w=round(factor * power_limits.discharge_w),
            import_w=_grid_charge_capped_w(factor * power_limits.charge_w, power_limits),
        )

        written = self._delegated_written
        if (
            self._lane == _LANE_DELEGATED
            and written is not None
            and written[0] == params
            and time.monotonic() - written[1] < REGISTER_REFRESH_S
        ):
            return

        self._write_delegated(params)

        self._record_diagnostic(
            "control_decision",
            mode=command.mode.name,
            lane=_LANE_DELEGATED,
            operation_factor=factor,
            op_mod=params.op_mod,
            charge_min_w=params.cha_min_w,
            charge_max_w=params.cha_max_w,
            discharge_min_w=params.dsch_min_w,
            discharge_max_w=params.dsch_max_w,
            grid_setpoint_w=params.grid_w,
            soc_percent=None if readings is None else readings.soc_percent,
            temperature_c=None if readings is None else readings.temp_c,
        )

    # ---------------- PV limit ----------------
    def _update_pv_limit(
        self,
        grid_w: Optional[float],
        readings: _Readings,
        setpoint_w: int,
        charge_limit_w: Optional[float],
        soc_max_percent: float,
    ) -> None:
        """Limit the PV production if the battery cannot take the surplus (mode NON_EXPORT)."""
        if (
            self._pv_limiter is None
            or not self._pv_limit_available
            or grid_w is None
            or charge_limit_w is None
            or readings.soc_percent is None
        ):
            return  # without all inputs the limit stays as it is
        # The battery cannot take more surplus. A battery that discharges takes none, but it
        # also means there is no surplus to hold back.
        saturated = setpoint_w <= 0 and (
            readings.soc_percent >= soc_max_percent
            or charge_limit_w <= _SATURATION_MARGIN_W
            or -setpoint_w >= charge_limit_w - _SATURATION_MARGIN_W
        )
        percent = self._pv_limiter.propose(
            time.monotonic(), export_w=grid_w, ac_w=readings.ac_w, saturated=saturated
        )
        self._record_diagnostic(
            "pv_curtailment_decision",
            export_w=grid_w,
            ac_power_w=readings.ac_w,
            battery_power_w=readings.bat_w,
            soc_percent=readings.soc_percent,
            saturated=saturated,
            proposed_limit_percent=percent,
            current_limit_percent=self._pv_limiter.percent,
        )
        if percent is not None:
            self._write_pv_limit(percent)

    def _write_pv_limit(self, percent: int) -> None:
        if self._pv_limiter is None:
            raise RuntimeError(
                f"{self.device_id}: no PV limiter configured, cannot set a PV limit."
            )
        self._write_regs(REG_PV_LIMIT_PERCENT, (percent,))
        self._pv_limiter.committed(time.monotonic(), percent)
        logger.info("{}: PV limit set to {} %", self.device_id, percent)

    def _release_pv_limit(self) -> None:
        """Back to 100 %, if the limiter is in use and a limit is in force."""
        if (
            self._pv_limiter is not None
            and self._pv_limit_available
            and self._pv_limiter.percent < 100
        ):
            self._write_pv_limit(100)


# ---------------------------------------------------------------------------
# Controller (EOS server side)
# ---------------------------------------------------------------------------
class SmaStpe(ControllerBase):
    """SMA Sunny Tripower Smart Energy.

    Executes the battery instructions of the energy management plan and provides the inverter
    readings under the keys of INVERTER_KEYS. The grid power for the "pid" method comes from
    the shared data store (settings.grid_power_key).
    """

    # Bound to the settings class by ControllerBase.__init_subclass__.
    Settings: ClassVar[type[SmaStpeSettings]] = SmaStpeSettings
    PROVIDED_DATA_KEYS: ClassVar[tuple[str, ...]] = INVERTER_KEYS
    # Event fields of the control thread that are collected as series for graphs.
    DIAGNOSTIC_SERIES: ClassVar[SeriesSpec] = SMA_DIAGNOSTIC_SERIES

    @property
    def settings(self) -> SmaStpeSettings:
        """Settings snapshot, typed for this controller (checked against Settings in __init__)."""
        return cast(SmaStpeSettings, self._settings)

    # ---------------- plan conversion ----------------
    def _plan_end(self, plan: EnergyManagementPlan) -> DateTime:
        """End of the plan: the last battery instruction expires then.

        TODO: confirm that EnergyManagementPlan.valid_until is the right attribute.
        """
        end = plan.valid_until
        if end is None:
            raise ValueError("The energy management plan has no end (valid_until)")
        return end

    def _frbc_to_command(
        self, instruction: FRBCInstruction, *, valid_until: DateTime
    ) -> SmaCommand:
        """Convert a battery FRBC instruction into an SMA command.

        Raises:
            ValueError: If the mode is unknown or the factor is out of range.
        """
        try:
            mode = BatteryOperationMode(instruction.operation_mode_id)
        except ValueError as exc:
            raise ValueError(
                f"Unsupported battery operation mode: {instruction.operation_mode_id!r}"
            ) from exc

        factor = instruction.operation_mode_factor
        if not 0.0 <= factor <= 1.0:
            raise ValueError(f"Battery operation factor must be in [0, 1], got {factor}")

        return SmaCommand(
            date_time=instruction.execution_time,
            valid_until=valid_until,
            # Every method runs in every control interval: the command methods write their
            # setpoint, the registers method follows the battery limits (temperature, SoC) and
            # writes only on change. Without this the command would be applied once.
            refresh_interval=CONTROL_INTERVAL_S,
            label=mode.name,
            mode=mode,
            operation_factor=factor,
        )

    def _plan_to_commands(self, plan: EnergyManagementPlan) -> list[ControlCommand]:
        """Extract the battery commands of this inverter from the plan.

        Each instruction remains active until the next instruction for the same resource.
        The final instruction expires at the plan's end.
        """
        instructions = sorted(
            (
                instruction
                for instruction in plan.instructions
                if isinstance(instruction, FRBCInstruction)
                and instruction.resource_id == self.settings.battery_id
            ),
            key=lambda instruction: instruction.execution_time,
        )
        if not instructions:
            return []

        plan_end = self._plan_end(plan)
        commands: list[ControlCommand] = []
        for index, instruction in enumerate(instructions):
            if index + 1 < len(instructions):
                valid_until = instructions[index + 1].execution_time
            else:
                valid_until = plan_end
            if valid_until <= instruction.execution_time:
                raise ValueError("Battery instruction must expire after its execution time")
            commands.append(self._frbc_to_command(instruction, valid_until=valid_until))
        return commands

    # ---------------- device specific ----------------
    def _battery_inverter(self) -> Optional[InverterCommonSettings]:
        """Inverter device of the battery ``battery_id``: the inverter whose ``battery_id`` it is.

        Returns:
            The inverter device settings, None if no inverter device is configured for the
            battery.

        Raises:
            ValueError: If several inverter devices are configured for the battery.
        """
        battery_id = self.settings.battery_id
        inverters = [
            inverter
            for inverter in (self.config.devices.inverters or {}).values()
            if inverter.battery_id == battery_id
        ]
        if len(inverters) > 1:
            raise ValueError(
                f"{self.device_id}: several inverter devices are configured for battery "
                f"'{battery_id}': {', '.join(inverter.device_id for inverter in inverters)}"
            )
        return inverters[0] if inverters else None

    def _battery_context(self) -> SmaBatteryContext:
        """Battery and inverter data of the battery ``battery_id`` (``devices/batteries``).

        Resolved like the parameters of the optimization: battery profile and profile
        adjustment of the battery, its SoC range and periodic full charge, and the effective
        parameters of the battery's inverter device at the reference operating point. The
        result is checked at the reference operating point of the battery, so a configuration
        error is reported here and not in the control loop.

        Raises:
            ValueError: If the battery is not configured, several inverter devices are
                configured for it, or the battery or inverter parameters cannot be resolved
                (including a missing or invalid profile).
            OSError: If a profile cannot be read.
        """
        battery_id = self.settings.battery_id
        battery = (self.config.devices.batteries or {}).get(battery_id)
        if battery is None:
            raise ValueError(
                f"{self.device_id}: battery '{battery_id}' is not configured in "
                "'devices/batteries'; its plan instructions cannot be executed"
            )
        profile = battery.get_battery_profile()
        capacity_wh = resolve_nominal_capacity_wh(battery, profile)
        resolve_battery_parameters(battery, profile, capacity_wh, BatteryOperatingPoint())
        full_charge = resolve_full_charge(battery, profile)
        inverter = self._battery_inverter()
        return SmaBatteryContext(
            battery=battery,
            profile=profile,
            capacity_wh=capacity_wh,
            full_charge_soc_percent=None
            if full_charge.interval_days is None
            else full_charge.soc_percentage,
            inverter=None if inverter is None else inverter.resolve_operating_parameters(),
        )

    def _pv_rated_power_w(self, pv: SmaPvCurtailmentSettings) -> Optional[float]:
        """Rated AC power for the PV limit: configured, else the nominal power of the inverter.

        Returns:
            The rated power [W], None if it is neither configured nor resolvable from the
            battery's inverter device (logged as error: the PV curtailment is disabled).
        """
        if pv.rated_power_w is not None:
            return pv.rated_power_w
        try:
            inverter = self._battery_inverter()
            if inverter is not None:
                return inverter.resolve_operating_parameters().nominal_power_w
            reason = f"no inverter device is configured for battery '{self.settings.battery_id}'"
        except (OSError, ValueError) as exc:
            reason = str(exc)
        logger.error(
            "{}: PV curtailment disabled, 'pv_curtailment.rated_power_w' is not set and the "
            "nominal power of the inverter is not available: {}",
            self.device_id,
            reason,
        )
        return None

    def _create_control_thread(self) -> SmaStpeControl:
        """Build a fresh, not yet started control thread."""
        pv = self.settings.pv_curtailment
        rated_power_w = None if pv is None else self._pv_rated_power_w(pv)
        return SmaStpeControl(
            self.device_id,
            self.shared_data,
            self.diagnostic_log,
            host=self.settings.host,
            # Fetched for every command: configuration and profile updates apply live.
            battery=self._battery_context,
            grid_power_key=self.settings.grid_power_key,
            grid_power_max_age_s=self.settings.grid_power_max_age_s,
            port=self.settings.port,
            unit_id=self.settings.unit_id,
            pv_limiter_settings=None
            if pv is None or rated_power_w is None
            else PvLimiterSettings(
                rated_power_w=rated_power_w,
                deadband_w=pv.deadband_w,
                hold_s=pv.hold_s,
                min_write_interval_s=pv.min_write_interval_s,
                step_percent=pv.step_percent,
                headroom_w=pv.headroom_w,
                keepalive_s=pv.keepalive_s,
            ),
            poll_interval=CONTROL_INTERVAL_S,
        )

    def _emplan_to_commands(self, plan: Optional[EnergyManagementPlan]) -> list[ControlCommand]:
        """Map the energy management plan to commands.

        An empty plan (None) leads to an empty schedule, which makes the inverter fall back to
        its safe state.
        """
        if plan is None:
            return []
        return self._plan_to_commands(plan)
