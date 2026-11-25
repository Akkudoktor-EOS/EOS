"""Grid power controller for the SMA STP-SE "2 registers" method.

Port of the battery control loop of the Home Assistant configuration
(template sensor `Wechselrichter_SMA_Bat_Leistung_Sollwert_W`). Pure Python without any I/O:
measurements and time are passed in, so the loop can be unit tested and simulated.

Sign conventions (as in the Home Assistant configuration and the SMA registers):
    grid power      export (+), import (-)
    battery power   discharge (+), charge (-), measured at the DC side of the inverter

Control loop:
    The controlled variable is the grid power, the manipulated variable is the battery power.
    Discharging raises the grid power, so the process gain is positive and the control error
    is "setpoint - measurement" with positive gains (standard negative feedback). All three
    terms use the standard signs, including the derivative:

        P:  kp * error                  error = (setpoint - grid) / efficiency
        I:  ki * error_integral         error of the grid power averaged over avg_window_s
        D:  kd * (error - last_error)   zero in the first step after a reset

    The output is based on the measured battery power, not on the last commanded one.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Optional

# Charge power limit [C-rate] over battery temperature [degC] (battery datasheet, as used in the
# Home Assistant configuration). Linear interpolation between the points.
# Note: the Home Assistant template computes the 5..7.5 degC segment as
# `0.1 + 0.28 * (T - 7.5) / 2.5`, which is negative below 7.5 degC. The ramp 0.1 -> 0.38 is
# assumed to be the intent.
_CHARGE_C_RATE_SOC_BELOW_80 = (
    (0.0, 0.0),
    (2.5, 0.1),
    (5.0, 0.1),
    (7.5, 0.38),
    (10.0, 0.38),
    (12.5, 0.5),
    (15.0, 0.5),
    (20.0, 1.0),
    (35.0, 1.0),
    (48.0, 0.8),
    (55.0, 0.27),
    (60.0, 0.0),
)
_CHARGE_C_RATE_SOC_FROM_80 = (
    (0.0, 0.0),
    (2.5, 0.1),
    (5.0, 0.1),
    (7.5, 0.38),
    (10.0, 0.38),
    (12.5, 0.5),
    (15.0, 0.5),
    (20.0, 0.75),
    (25.0, 0.8),
    (48.0, 0.8),
    (55.0, 0.27),
    (60.0, 0.0),
)


def _interpolate(x: float, points: tuple[tuple[float, float], ...]) -> float:
    """Piecewise linear interpolation, constant outside of the given points."""
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def charge_power_limit_w(
    temp_c: float, soc_percent: float, capacity_wh: float, hw_max_charge_w: float
) -> float:
    """Charge power the battery accepts at this temperature and state of charge.

    The charge rate of the battery data sheet (C-rate over temperature, derated from 80 % SoC)
    times the capacity, limited by the maximum charge power the inverter reports.
    """
    table = _CHARGE_C_RATE_SOC_FROM_80 if soc_percent >= 80 else _CHARGE_C_RATE_SOC_BELOW_80
    return min(hw_max_charge_w, _interpolate(temp_c, table) * capacity_wh)


def _efficiency(ac_w: float, dc_pv_w: float, bat_dc_w: float) -> float:
    """Estimate the DC/AC conversion efficiency of the inverter (limited to 0.6 .. 1.0).

    The efficiency is output power over input power: AC/DC when the inverter feeds AC
    (DC power positive), DC/AC when it draws AC (DC power negative, e.g. charging from the grid).
    """
    dc_w = dc_pv_w + bat_dc_w
    if dc_w == 0.0:
        eff = 0.98
    elif (dc_w < 0 < ac_w) or (ac_w < 0 < dc_w):
        eff = 0.6
    elif dc_w > 0:
        eff = ac_w / dc_w
    else:
        eff = dc_w / ac_w if ac_w != 0.0 else 0.6
    return max(min(eff, 1.0), 0.6)


@dataclass(frozen=True)
class GridControllerSettings:
    """Tuning of the controller. Defaults are the values of the Home Assistant configuration."""

    kp: float = 0.7
    ki: float = 1.0
    kd: float = 0.2
    offset_kp: float = 0.9  # gain of the correction by (measured - last commanded) battery power
    offset_error_limit_w: float = 50.0
    avg_window_s: float = 15.0  # averaging window of the grid power used by the "integral" part
    excess_tau_s: float = 30.0  # time constant of the long-time PV excess filter
    charge_headroom_w: float = 50.0  # charge only if the long-time excess is above
    charge_trim_w: float = 50.0  # reduce charge power to avoid grid import
    charge_min_w: float = 50.0
    charge_hold_s: float = 30.0  # no charging this long after discharging
    discharge_hold_s: float = 30.0  # no discharging this long after charging
    discharge_min_w: float = 50.0
    discharge_bias_w: float = 120.0  # constant bias added to discharge power (empirical)
    min_charge_s: float = 180.0  # do not start charging that would be over faster than this
    critical_delta_w: float = 1000.0  # zero crossing of the error with a change above: damp
    eco_c_rate: float = 0.2  # preferred (gentle) charge/discharge power in C
    discharge_c_rate: float = 1.0


@dataclass(frozen=True)
class ControlInputs:
    """All inputs of one controller step."""

    now: float  # monotonic time [s]
    grid_w: float  # grid power, export (+), import (-)
    ac_w: float  # AC power of the inverter
    dc_pv_w: float  # DC power of the PV strings
    bat_dc_w: float  # measured battery power, discharge (+), charge (-)
    soc_percent: float
    temp_c: float  # battery temperature
    capacity_wh: float
    hw_max_charge_w: float  # limits reported by the inverter
    hw_max_discharge_w: float
    soc_min_percent: float  # no discharge at or below
    soc_max_percent: float  # no charge at or above (also the charge target)
    allow_charge: bool = True
    allow_discharge: bool = True
    grid_setpoint_w: float = 0.0  # grid power to hold, export (+), import (-)
    # Charge and discharge power the battery allows at this operating point, e.g. from the
    # battery profile. None: the built-in charge table and discharge_c_rate are used.
    max_charge_w: Optional[float] = None
    max_discharge_w: Optional[float] = None


class GridPowerController:
    """Holds the grid power at a setpoint by commanding the battery power."""

    def __init__(self, settings: Optional[GridControllerSettings] = None) -> None:
        self.s = settings or GridControllerSettings()
        self.reset()

    def reset(self) -> None:
        """Forget all state (call when control (re-)starts or the inputs were missing)."""
        self._grid_history: deque[tuple[float, float]] = deque()
        self._excess_lp_w: Optional[float] = None
        self._last_t: Optional[float] = None
        self._last_output_w = 0.0
        self._last_error_w: Optional[float] = None  # None: no derivative in the first step
        self._last_charge_t = -math.inf
        self._last_discharge_t = -math.inf

    def step(self, i: ControlInputs) -> int:
        """Return the battery power setpoint in W (discharge +, charge -)."""
        s = self.s
        eff = _efficiency(i.ac_w, i.dc_pv_w, i.bat_dc_w)

        # Grid power averaged over the window (used by the "integral" part).
        self._grid_history.append((i.now, i.grid_w))
        while self._grid_history[0][0] < i.now - s.avg_window_s:
            self._grid_history.popleft()
        grid_avg_w = sum(v for _, v in self._grid_history) / len(self._grid_history)

        # PV excess = what would be exported without the battery, low-pass filtered.
        # (Equals ac_pv - ac_load of the Home Assistant configuration, without its 100 W load floor.)
        excess_w = i.grid_w - eff * i.bat_dc_w
        if self._excess_lp_w is None or self._last_t is None:
            excess_lp_w = excess_w
        else:
            alpha = 1.0 - math.exp(-(i.now - self._last_t) / s.excess_tau_s)
            excess_lp_w = self._excess_lp_w + alpha * (excess_w - self._excess_lp_w)
        self._excess_lp_w = excess_lp_w
        self._last_t = i.now

        # Control errors (setpoint - measurement) on the DC side (hence the division by the
        # DC/AC efficiency). The first step after a reset has no derivative.
        error_w = (i.grid_setpoint_w - i.grid_w) / eff
        error_integral_w = (i.grid_setpoint_w - grid_avg_w) / eff
        last_error_w = error_w if self._last_error_w is None else self._last_error_w
        error_delta_w = error_w - last_error_w
        critical = ((error_w < 0 <= last_error_w) or (last_error_w < 0 <= error_w)) and abs(
            error_delta_w
        ) > s.critical_delta_w

        # Correction by the deviation between measured and last commanded battery power.
        offset_error_w = max(
            -s.offset_error_limit_w,
            min(s.offset_error_limit_w, i.bat_dc_w - self._last_output_w),
        )
        offset_w = s.offset_kp * offset_error_w

        # Power limits of the battery.
        if i.max_charge_w is not None:
            max_charge_w = min(i.hw_max_charge_w, i.max_charge_w)
        else:
            max_charge_w = charge_power_limit_w(
                i.temp_c, i.soc_percent, i.capacity_wh, i.hw_max_charge_w
            )
        if i.max_discharge_w is not None:
            max_discharge_w = min(i.hw_max_discharge_w, i.max_discharge_w)
        else:
            max_discharge_w = min(i.hw_max_discharge_w, s.discharge_c_rate * i.capacity_wh)
        eco_w = s.eco_c_rate * i.capacity_wh
        # Power needed to reach the charge target within one hour.
        req_w = max(0.0, (i.soc_max_percent - i.soc_percent) / 100.0 * i.capacity_wh)

        # The measured battery power is the base of the new output (not the last commanded one).
        output_w = i.bat_dc_w + s.kp * error_w + s.ki * error_integral_w + s.kd * error_delta_w

        if output_w < 0:  # charge
            if (
                not i.allow_charge
                or i.soc_percent >= i.soc_max_percent
                or excess_lp_w < s.charge_headroom_w
                or i.now - self._last_discharge_t < s.charge_hold_s
            ):
                output_w = 0.0
            else:
                output_w = min(output_w + s.charge_trim_w, 0.0)
                limit_w = min(max_charge_w, -output_w)  # never more than the missing power
                eco = min(eco_w, limit_w)
                req = min(req_w, limit_w)
                floor = min(s.charge_min_w, limit_w)
                charge_w = eco if critical else max(req, eco, floor)
                too_short = (
                    charge_w > 0
                    and 3600.0 * req / charge_w < s.min_charge_s
                    and i.soc_max_percent < 98
                )
                if charge_w < s.charge_min_w or too_short:
                    output_w = 0.0
                else:
                    output_w = -(charge_w + max(-offset_w, 0.0))
            output_w = max(output_w, -max_charge_w)
        else:  # discharge
            if (
                not i.allow_discharge
                or i.soc_percent <= i.soc_min_percent
                or i.now - self._last_charge_t < s.discharge_hold_s
            ):
                output_w = 0.0
            else:
                output_w = min(output_w, max_discharge_w)
                if critical:
                    output_w = min(output_w, eco_w)
                if output_w < s.discharge_min_w:
                    output_w = 0.0
                else:
                    output_w += max(offset_w, 0.0) + s.discharge_bias_w
            output_w = min(output_w, max_discharge_w)

        if output_w < 0:
            self._last_charge_t = i.now
        elif output_w > 0:
            self._last_discharge_t = i.now
        self._last_output_w = output_w
        self._last_error_w = error_w
        return int(output_w)


@dataclass(frozen=True)
class PvLimiterSettings:
    """Tuning of the PV export limiter."""

    rated_power_w: float  # the AC power that 100 % of the inverter power limit refers to
    headroom_w: float = 100.0  # export left on purpose, see PvExportLimiter
    deadband_w: float = 50.0  # deviations below this are tolerated without action
    hold_s: float = 60.0  # a condition must last this long before the limit is changed
    min_write_interval_s: float = 60.0  # minimum time between two changes of the limit
    step_percent: int = 2  # smallest change of the limit worth a write (except release to 100 %)
    keepalive_s: Optional[float] = None  # write an unchanged limit this often while limiting


class PvExportLimiter:
    """Keeps the grid export small by limiting the AC output of the inverter (PV curtailment).

    Pure logic without I/O, like the grid power controller. The AC output of the inverter is
    `load + export`, so limiting it to `ac - export + headroom_w` leaves an export of about
    `headroom_w`: the surplus that neither the loads nor the battery take is curtailed.

    The headroom is deliberate. The grid power controller must keep seeing a small export,
    otherwise it takes it for "no surplus" and reduces the battery charging, which in turn lets
    the surplus reappear. With the headroom it keeps charging at the maximum power of the
    battery for as long as the PV delivers more than load and charging can take.

    Two states:

    - Not limiting (100 %). The limiter waits for export that lasts `hold_s` while the battery
      is saturated (it cannot take more), then it sets the limit.
    - Limiting (< 100 %). The limit follows the load: export above the headroom, or import,
      that lasts `hold_s` moves it. If the battery is no longer saturated and there is no import
      for `hold_s`, the PV production is not held back any more (the surplus fits into the
      battery again, or the PV delivers less): the limit is released. While the PV is curtailed
      the grid power controller sees the headroom as export and keeps the battery saturated,
      so this does not happen by itself.

    The limit is a parameter of the inverter that SMA does not intend for cyclic writes, so
    the limiter is slow on purpose: it waits `hold_s`, writes at most every
    `min_write_interval_s` and ignores changes below `step_percent`.

    Usage: `propose()` returns the percentage to write or None, the caller writes it and
    reports it with `committed()`. A failed write is therefore not remembered as done.
    """

    def __init__(self, settings: PvLimiterSettings) -> None:
        self.s = settings
        self.reset()

    def reset(self, percent: int = 100) -> None:
        """Start from the limit the inverter reports (call after (re)connecting)."""
        self._percent = max(0, min(100, percent))
        self._deviation_since: Optional[float] = None
        self._nonbinding_since: Optional[float] = None
        self._last_write_t = -math.inf

    @property
    def percent(self) -> int:
        """The limit in force, in percent of the rated AC power."""
        return self._percent

    @property
    def limiting(self) -> bool:
        """The PV production is limited."""
        return self._percent < 100

    @property
    def _one_percent_w(self) -> float:
        return self.s.rated_power_w / 100.0

    def _track_percent(self, ac_w: float, export_w: float) -> int:
        """The limit that leaves the headroom as export (rounded up: rather export than import)."""
        target_w = ac_w - export_w + self.s.headroom_w
        return max(0, min(100, math.ceil(target_w / self.s.rated_power_w * 100.0)))

    def propose(self, now: float, export_w: float, ac_w: float, saturated: bool) -> Optional[int]:
        """Return the new limit in percent if it should be written now, else None.

        Args:
            now: Monotonic time [s].
            export_w: Grid export, positive when exporting.
            ac_w: AC power of the inverter.
            saturated: The battery cannot take more surplus (full, charging not allowed or at
                its charge power limit).
        """
        s = self.s
        desired: Optional[int] = None
        # export that belongs to a limit that does its job: the headroom plus the rounding
        tolerated_export_w = s.headroom_w + self._one_percent_w + s.deadband_w
        if not self.limiting:
            self._nonbinding_since = None
            if saturated and export_w > s.deadband_w:
                if self._deviation_since is None:
                    self._deviation_since = now
                if now - self._deviation_since >= s.hold_s:
                    desired = self._track_percent(ac_w, export_w)
            else:
                self._deviation_since = None
        else:
            importing = export_w < -s.deadband_w
            if not saturated and not importing:
                # the surplus fits into the battery or the PV delivers less: nothing to hold back
                self._deviation_since = None
                if self._nonbinding_since is None:
                    self._nonbinding_since = now
                if now - self._nonbinding_since >= s.hold_s:
                    desired = 100
            else:
                self._nonbinding_since = None
                if export_w > tolerated_export_w or importing:
                    if self._deviation_since is None:
                        self._deviation_since = now
                    if now - self._deviation_since >= s.hold_s:
                        desired = self._track_percent(ac_w, export_w)
                else:
                    self._deviation_since = None

        keepalive_due = (
            s.keepalive_s is not None
            and self.limiting
            and now - self._last_write_t >= s.keepalive_s
        )
        if desired is None or desired == self._percent:
            return self._percent if keepalive_due else None
        if desired != 100 and abs(desired - self._percent) < s.step_percent:
            return self._percent if keepalive_due else None
        if now - self._last_write_t < s.min_write_interval_s:
            return None
        return desired

    def committed(self, now: float, percent: int) -> None:
        """Report a limit that was written to the inverter."""
        self._percent = max(0, min(100, percent))
        self._last_write_t = now
        self._deviation_since = None
        self._nonbinding_since = None
