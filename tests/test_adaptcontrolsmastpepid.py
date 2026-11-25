"""Tests for the grid power controller of the SMA STP-SE (smastpepid.py).

The controller is pure: measurements and time are passed in, so the tests need no mocks.

- The unit tests pin single decisions of ``GridPowerController.step()``.
- ``TestClosedLoop`` runs the controller against a small plant model.

Sign conventions (as in the module): grid power export (+), import (-); battery power
discharge (+), charge (-). The tests use the default settings unless a test says otherwise.
Where a test pins heuristic behavior (offset correction, bias) it says so, so that a deliberate
change of that behavior is easy to recognize.
"""

import dataclasses
import math
from typing import Any, Callable

import pytest

from akkudoktoreos.adapter.controllers.sma.smastpepid import (
    _CHARGE_C_RATE_SOC_BELOW_80,
    _CHARGE_C_RATE_SOC_FROM_80,
    ControlInputs,
    GridControllerSettings,
    GridPowerController,
    PvExportLimiter,
    PvLimiterSettings,
    _efficiency,
    _interpolate,
    charge_power_limit_w,
)

# Efficiency the controller assumes when there is no DC power at all (nothing to estimate from).
EFF_NO_DC = 0.98


def make_inputs(**overrides: Any) -> ControlInputs:
    """Inputs of an idle system: 50 % SoC, 25 degC, 10 kWh battery, no power flows."""
    values: dict[str, Any] = {
        "now": 0.0,
        "grid_w": 0.0,
        "ac_w": 0.0,
        "dc_pv_w": 0.0,
        "bat_dc_w": 0.0,
        "soc_percent": 50.0,
        "temp_c": 25.0,
        "capacity_wh": 10000.0,
        "hw_max_charge_w": 5000.0,
        "hw_max_discharge_w": 5000.0,
        "soc_min_percent": 5.0,
        "soc_max_percent": 100.0,
    }
    values.update(overrides)
    return ControlInputs(**values)


def make_controller(**settings: float) -> GridPowerController:
    return GridPowerController(GridControllerSettings(**settings))


def single_step(controller_settings: dict[str, float] | None = None, **inputs: Any) -> int:
    """Output of the first step of a fresh controller."""
    return make_controller(**(controller_settings or {})).step(make_inputs(**inputs))


class TestInterpolate:
    POINTS = ((0.0, 0.0), (10.0, 100.0), (20.0, 0.0))

    def test_constant_below_the_first_point(self) -> None:
        assert _interpolate(-5.0, self.POINTS) == 0.0

    def test_constant_above_the_last_point(self) -> None:
        assert _interpolate(50.0, ((0.0, 3.0), (10.0, 7.0))) == 7.0

    @pytest.mark.parametrize("x, y", [(0.0, 0.0), (10.0, 100.0), (20.0, 0.0)])
    def test_returns_the_given_points(self, x: float, y: float) -> None:
        assert _interpolate(x, self.POINTS) == pytest.approx(y)

    @pytest.mark.parametrize("x, y", [(2.5, 25.0), (5.0, 50.0), (15.0, 50.0), (19.0, 10.0)])
    def test_interpolates_linearly_between_points(self, x: float, y: float) -> None:
        assert _interpolate(x, self.POINTS) == pytest.approx(y)


class TestChargeRateTables:
    TABLES = {"below_80": _CHARGE_C_RATE_SOC_BELOW_80, "from_80": _CHARGE_C_RATE_SOC_FROM_80}

    @pytest.fixture(params=sorted(TABLES))
    def table(self, request: pytest.FixtureRequest) -> tuple[tuple[float, float], ...]:
        return self.TABLES[request.param]

    def test_temperatures_are_strictly_increasing(
        self, table: tuple[tuple[float, float], ...]
    ) -> None:
        temperatures = [t for t, _ in table]
        assert temperatures == sorted(set(temperatures))

    def test_c_rates_are_in_the_unit_range(self, table: tuple[tuple[float, float], ...]) -> None:
        assert all(0.0 <= c_rate <= 1.0 for _, c_rate in table)

    def test_no_charging_at_the_temperature_limits(
        self, table: tuple[tuple[float, float], ...]
    ) -> None:
        assert _interpolate(0.0, table) == 0.0
        assert _interpolate(60.0, table) == 0.0
        assert _interpolate(-20.0, table) == 0.0
        assert _interpolate(80.0, table) == 0.0

    def test_cold_battery_charges_gently(self, table: tuple[tuple[float, float], ...]) -> None:
        assert _interpolate(5.0, table) == pytest.approx(0.1)

    def test_charge_rate_is_never_higher_from_80_percent_soc(self) -> None:
        for temperature in (t / 2 for t in range(0, 121)):
            assert _interpolate(temperature, _CHARGE_C_RATE_SOC_FROM_80) <= _interpolate(
                temperature, _CHARGE_C_RATE_SOC_BELOW_80
            ) + 1e-9


class TestChargePowerLimit:
    def test_limited_by_the_hardware(self) -> None:
        assert charge_power_limit_w(25.0, 50.0, 10000.0, 3000.0) == 3000.0

    def test_limited_by_the_charge_rate_of_the_temperature(self) -> None:
        # 0.1 C of 10 kWh at 5 degC
        assert charge_power_limit_w(5.0, 50.0, 10000.0, 5000.0) == pytest.approx(1000.0)

    @pytest.mark.parametrize("temp_c", [-10.0, 0.0, 60.0, 70.0])
    def test_no_charging_outside_the_temperature_range(self, temp_c: float) -> None:
        assert charge_power_limit_w(temp_c, 50.0, 10000.0, 5000.0) == 0.0

    def test_derated_from_80_percent_soc(self) -> None:
        # 20 degC: 1.0 C below 80 %, 0.75 C from 80 %
        assert charge_power_limit_w(20.0, 79.0, 10000.0, 20000.0) == pytest.approx(10000.0)
        assert charge_power_limit_w(20.0, 80.0, 10000.0, 20000.0) == pytest.approx(7500.0)

    def test_scales_with_the_capacity(self) -> None:
        assert charge_power_limit_w(5.0, 50.0, 20000.0, 50000.0) == pytest.approx(2000.0)


class TestEfficiency:
    @pytest.mark.parametrize(
        "ac_w, dc_pv_w, bat_dc_w, expected",
        [
            pytest.param(0.0, 0.0, 0.0, 0.98, id="no-dc-power"),
            pytest.param(950.0, 1000.0, 0.0, 0.95, id="pv-to-ac"),
            pytest.param(950.0, 500.0, 500.0, 0.95, id="pv-and-battery-to-ac"),
            pytest.param(-1050.0, 0.0, -1000.0, 1000.0 / 1050.0, id="grid-charges-battery"),
            pytest.param(500.0, -100.0, 0.0, 0.6, id="ac-and-dc-opposite-1"),
            pytest.param(-500.0, 100.0, 0.0, 0.6, id="ac-and-dc-opposite-2"),
            pytest.param(0.0, 0.0, -200.0, 0.6, id="dc-drawn-without-ac"),
            pytest.param(2000.0, 1000.0, 0.0, 1.0, id="clamped-to-1"),
            pytest.param(100.0, 1000.0, 0.0, 0.6, id="clamped-to-0.6"),
        ],
    )
    def test_efficiency(
        self, ac_w: float, dc_pv_w: float, bat_dc_w: float, expected: float
    ) -> None:
        assert _efficiency(ac_w, dc_pv_w, bat_dc_w) == pytest.approx(expected)

    def test_efficiency_is_always_in_range(self) -> None:
        for ac_w in range(-3000, 3001, 500):
            for dc_pv_w in range(0, 3001, 500):
                for bat_dc_w in range(-3000, 3001, 500):
                    assert 0.6 <= _efficiency(ac_w, dc_pv_w, bat_dc_w) <= 1.0


class TestDataclasses:
    def test_default_settings_are_the_home_assistant_values(self) -> None:
        s = GridControllerSettings()
        assert (s.kp, s.ki, s.kd) == (0.7, 1.0, 0.2)
        assert s.discharge_bias_w == 120.0
        assert s.avg_window_s == 15.0
        assert s.charge_hold_s == s.discharge_hold_s == 30.0

    def test_settings_are_immutable(self) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            GridControllerSettings().kp = 1.0  # type: ignore[misc]

    def test_inputs_are_immutable(self) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            make_inputs().grid_w = 5.0  # type: ignore[misc]

    def test_inputs_allow_charge_and_discharge_with_setpoint_zero_by_default(self) -> None:
        inputs = make_inputs()
        assert inputs.allow_charge is True
        assert inputs.allow_discharge is True
        assert inputs.grid_setpoint_w == 0.0

    def test_controller_uses_default_settings_without_arguments(self) -> None:
        assert GridPowerController().s == GridControllerSettings()

    def test_controller_uses_given_settings(self) -> None:
        settings = GridControllerSettings(kp=0.1)
        assert GridPowerController(settings).s is settings


class TestReset:
    def test_new_controller_has_no_state(self) -> None:
        controller = make_controller()
        assert len(controller._grid_history) == 0
        assert controller._excess_lp_w is None
        assert controller._last_error_w is None

    def test_reset_forgets_all_state(self) -> None:
        controller = make_controller()
        controller.step(make_inputs(now=0.0, grid_w=-1000.0))
        controller.step(make_inputs(now=5.0, grid_w=-500.0))
        controller.reset()
        assert len(controller._grid_history) == 0
        assert controller._excess_lp_w is None
        assert controller._last_error_w is None

    def test_step_after_reset_equals_step_of_a_new_controller(self) -> None:
        used = make_controller()
        for k in range(5):
            used.step(make_inputs(now=5.0 * k, grid_w=-800.0 + 300.0 * k, bat_dc_w=400.0))
        used.reset()
        fresh = make_controller()
        inputs = make_inputs(now=100.0, grid_w=-600.0)
        assert used.step(inputs) == fresh.step(inputs)


class TestDirectionOfControl:
    def test_grid_import_discharges_the_battery(self) -> None:
        assert single_step(grid_w=-800.0) > 0

    def test_grid_export_charges_the_battery(self) -> None:
        assert single_step(grid_w=800.0) < 0

    def test_balanced_grid_does_nothing(self) -> None:
        assert single_step(grid_w=0.0) == 0

    def test_output_is_an_integer(self) -> None:
        assert isinstance(single_step(grid_w=-333.3), int)

    def test_larger_import_discharges_more(self) -> None:
        outputs = [single_step(grid_w=-w) for w in (200.0, 500.0, 1000.0, 2000.0)]
        assert outputs == sorted(outputs)
        assert len(set(outputs)) == len(outputs)

    def test_larger_export_charges_more(self) -> None:
        outputs = [single_step(grid_w=w) for w in (300.0, 800.0, 1500.0)]
        assert outputs == sorted(outputs, reverse=True)

    def test_holding_the_setpoint_does_nothing(self) -> None:
        assert single_step(grid_w=-200.0, grid_setpoint_w=-200.0) == 0
        assert single_step(grid_w=300.0, grid_setpoint_w=300.0) == 0

    def test_setpoint_is_in_the_grid_power_convention(self) -> None:
        # Wanting 500 W of export while exporting nothing means: discharge.
        assert single_step(grid_w=0.0, grid_setpoint_w=500.0) > 0
        # Wanting 500 W of import while importing nothing means: charge (if there is surplus).
        assert single_step({"charge_headroom_w": -1e9}, grid_w=0.0, grid_setpoint_w=-500.0) < 0


class TestChargeLimits:
    """Grid export of 1 kW at 50 % SoC is a charge request, unless something forbids charging."""

    def test_charges_without_restrictions(self) -> None:
        assert single_step(grid_w=1000.0) < 0

    def test_does_not_charge_if_not_allowed(self) -> None:
        assert single_step(grid_w=1000.0, allow_charge=False) == 0

    @pytest.mark.parametrize("soc_percent", [100.0, 100.5])
    def test_does_not_charge_at_full_battery(self, soc_percent: float) -> None:
        assert single_step(grid_w=1000.0, soc_percent=soc_percent) == 0

    def test_charge_target_is_the_soc_maximum(self) -> None:
        assert single_step(grid_w=1000.0, soc_percent=60.0, soc_max_percent=60.0) == 0
        assert single_step(grid_w=1000.0, soc_percent=59.0, soc_max_percent=60.0) <= 0

    @pytest.mark.parametrize("temp_c", [-10.0, 0.0, 60.0, 70.0])
    def test_does_not_charge_outside_the_temperature_range(self, temp_c: float) -> None:
        assert single_step(grid_w=1000.0, temp_c=temp_c) == 0

    def test_cold_battery_is_charged_with_the_derated_power(self) -> None:
        # 0.1 C of a 10 kWh battery at 5 degC
        assert single_step(grid_w=1000.0, temp_c=5.0, soc_percent=20.0) == -1000

    def test_charge_power_is_limited_by_the_hardware(self) -> None:
        assert single_step(grid_w=4000.0, hw_max_charge_w=300.0) == -300

    def test_charge_power_is_limited_by_the_missing_power(self) -> None:
        # never more than what is needed to bring the grid to the setpoint
        assert abs(single_step(grid_w=500.0, soc_percent=20.0)) < 1500

    def test_does_not_charge_below_the_minimum_power(self) -> None:
        settings = {"charge_headroom_w": 0.0}
        assert single_step(settings, grid_w=40.0) == 0
        assert single_step(settings, grid_w=1000.0) < 0

    def test_does_not_charge_without_long_time_excess(self) -> None:
        # exporting 30 W is below charge_headroom_w (50 W)
        assert single_step(grid_w=30.0) == 0

    def test_does_not_start_a_charge_that_is_over_too_fast(self) -> None:
        # 0.5 % of 10 kWh = 50 Wh to the target, at 2 kW that is 90 s < min_charge_s
        assert single_step(grid_w=2000.0, soc_percent=50.5, soc_max_percent=51.0) == 0
        # 1100 Wh to the target is long enough
        assert single_step(grid_w=2000.0, soc_percent=40.0, soc_max_percent=51.0) < 0

    def test_tops_up_the_battery_if_the_target_is_close_to_full(self) -> None:
        # the minimum duration only applies to targets below 98 %
        assert single_step(grid_w=2000.0, soc_percent=99.95, soc_max_percent=100.0) < 0


class TestDischargeLimits:
    """Grid import of 1 kW at 50 % SoC is a discharge request, unless something forbids it."""

    def test_discharges_without_restrictions(self) -> None:
        assert single_step(grid_w=-1000.0) > 0

    def test_does_not_discharge_if_not_allowed(self) -> None:
        assert single_step(grid_w=-1000.0, allow_discharge=False) == 0

    @pytest.mark.parametrize("soc_percent", [0.0, 4.9, 5.0])
    def test_does_not_discharge_at_empty_battery(self, soc_percent: float) -> None:
        assert single_step(grid_w=-1000.0, soc_percent=soc_percent) == 0

    def test_discharges_just_above_the_soc_minimum(self) -> None:
        assert single_step(grid_w=-1000.0, soc_percent=5.1) > 0

    def test_discharge_minimum_is_the_soc_input(self) -> None:
        assert single_step(grid_w=-1000.0, soc_percent=20.0, soc_min_percent=20.0) == 0
        assert single_step(grid_w=-1000.0, soc_percent=21.0, soc_min_percent=20.0) > 0

    def test_discharge_power_is_limited_by_the_hardware(self) -> None:
        assert single_step(grid_w=-4000.0, hw_max_discharge_w=300.0) == 300

    def test_discharge_power_is_limited_by_the_capacity(self) -> None:
        # discharge_c_rate (1 C) of a 200 Wh battery
        assert single_step(grid_w=-4000.0, capacity_wh=200.0) == 200

    def test_does_not_discharge_below_the_minimum_power(self) -> None:
        assert single_step(grid_w=-20.0) == 0
        assert single_step(grid_w=-200.0) > 0

    def test_bias_is_added_to_the_discharge_power(self) -> None:
        with_bias = single_step(grid_w=-1000.0)
        without_bias = single_step({"discharge_bias_w": 0.0}, grid_w=-1000.0)
        assert with_bias - without_bias == pytest.approx(120.0, abs=1.0)

    def test_bias_is_not_added_to_the_charge_power(self) -> None:
        assert single_step(grid_w=1000.0) == single_step({"discharge_bias_w": 0.0}, grid_w=1000.0)

    def test_bias_does_not_turn_idle_into_discharge(self) -> None:
        assert single_step({"discharge_bias_w": 500.0}, grid_w=0.0) == 0


class TestHoldTimes:
    """No charging right after discharging and the other way round (switching losses)."""

    # follow the instantaneous excess: the long-time filter is not what these tests are about
    FAST_FILTER = {"excess_tau_s": 0.001}

    def test_no_charge_right_after_discharge(self) -> None:
        controller = make_controller(**self.FAST_FILTER)
        assert controller.step(make_inputs(now=0.0, grid_w=-1000.0)) > 0
        assert controller.step(make_inputs(now=5.0, grid_w=1000.0)) == 0
        assert controller.step(make_inputs(now=29.0, grid_w=1000.0)) == 0
        assert controller.step(make_inputs(now=40.0, grid_w=1000.0)) < 0

    def test_no_discharge_right_after_charge(self) -> None:
        controller = make_controller(**self.FAST_FILTER)
        assert controller.step(make_inputs(now=0.0, grid_w=1000.0)) < 0
        assert controller.step(make_inputs(now=5.0, grid_w=-1000.0)) == 0
        assert controller.step(make_inputs(now=29.0, grid_w=-1000.0)) == 0
        assert controller.step(make_inputs(now=40.0, grid_w=-1000.0)) > 0

    def test_hold_time_is_configurable(self) -> None:
        controller = make_controller(charge_hold_s=0.0, **self.FAST_FILTER)
        assert controller.step(make_inputs(now=0.0, grid_w=-1000.0)) > 0
        assert controller.step(make_inputs(now=5.0, grid_w=1000.0)) < 0

    def test_no_hold_after_standing_still(self) -> None:
        controller = make_controller(**self.FAST_FILTER)
        assert controller.step(make_inputs(now=0.0, grid_w=0.0)) == 0
        assert controller.step(make_inputs(now=1.0, grid_w=1000.0)) < 0

    def test_continued_charging_is_not_held(self) -> None:
        controller = make_controller(**self.FAST_FILTER)
        assert controller.step(make_inputs(now=0.0, grid_w=1000.0)) < 0
        assert controller.step(make_inputs(now=5.0, grid_w=1000.0)) < 0

    def test_hold_expires_after_the_hold_time(self) -> None:
        controller = make_controller(charge_hold_s=30.0, **self.FAST_FILTER)
        controller.step(make_inputs(now=0.0, grid_w=-1000.0))
        assert controller.step(make_inputs(now=29.9, grid_w=1000.0)) == 0
        assert controller.step(make_inputs(now=30.0, grid_w=1000.0)) < 0


class TestDerivativeTerm:
    """The derivative acts on the control error (setpoint - grid) with the standard sign."""

    @staticmethod
    def second_step(kd: float, first_grid_w: float, second_grid_w: float) -> int:
        controller = make_controller(kd=kd)
        controller.step(make_inputs(now=0.0, grid_w=first_grid_w))
        return controller.step(make_inputs(now=5.0, grid_w=second_grid_w))

    def test_first_step_after_start_has_no_derivative(self) -> None:
        assert single_step({"kd": 0.0}, grid_w=-800.0) == single_step({"kd": 0.2}, grid_w=-800.0)

    def test_first_step_after_reset_has_no_derivative(self) -> None:
        controllers = [make_controller(kd=kd) for kd in (0.0, 0.2)]
        for controller in controllers:
            controller.step(make_inputs(now=0.0, grid_w=-100.0))
            controller.step(make_inputs(now=5.0, grid_w=-3000.0))
            controller.reset()
        outputs = [c.step(make_inputs(now=10.0, grid_w=-800.0)) for c in controllers]
        assert outputs[0] == outputs[1]

    def test_growing_import_raises_the_discharge_power(self) -> None:
        # error grows by (800 - 300) / efficiency: derivative term = kd * that
        delta = self.second_step(0.2, -300.0, -800.0) - self.second_step(0.0, -300.0, -800.0)
        assert delta == pytest.approx(0.2 * 500.0 / EFF_NO_DC, abs=1.5)

    def test_shrinking_import_lowers_the_discharge_power(self) -> None:
        delta = self.second_step(0.2, -800.0, -300.0) - self.second_step(0.0, -800.0, -300.0)
        assert delta == pytest.approx(-0.2 * 500.0 / EFF_NO_DC, abs=1.5)

    def test_growing_export_raises_the_charge_power(self) -> None:
        settings = {"excess_tau_s": 0.001}
        outputs = []
        for kd in (0.0, 0.2):
            controller = make_controller(kd=kd, **settings)
            controller.step(make_inputs(now=0.0, grid_w=300.0))
            outputs.append(controller.step(make_inputs(now=5.0, grid_w=800.0)))
        # the derivative of a growing export is negative: more charging
        assert outputs[1] < outputs[0]

    def test_constant_error_has_no_derivative(self) -> None:
        assert self.second_step(0.2, -500.0, -500.0) == self.second_step(0.0, -500.0, -500.0)


class TestIntegralTerm:
    def test_grid_power_is_averaged_over_the_window(self) -> None:
        # only the integral term is active: output = ki * averaged error / efficiency
        controller = make_controller(kp=0.0, kd=0.0, discharge_bias_w=0.0, offset_kp=0.0)
        for k in range(3):
            controller.step(make_inputs(now=5.0 * k, grid_w=-1000.0))
        output = controller.step(make_inputs(now=15.0, grid_w=0.0))
        assert output == int(750.0 / EFF_NO_DC)  # (3 x -1000 + 0) / 4 = -750

    def test_old_samples_leave_the_window(self) -> None:
        controller = make_controller(avg_window_s=15.0)
        for k in range(10):
            controller.step(make_inputs(now=5.0 * k, grid_w=-100.0))
        # samples at t, t-5, t-10 and t-15
        assert len(controller._grid_history) == 4

    def test_window_is_configurable(self) -> None:
        controller = make_controller(avg_window_s=1.0)
        for k in range(10):
            controller.step(make_inputs(now=5.0 * k, grid_w=-100.0))
        assert len(controller._grid_history) == 1

    def test_integral_term_reacts_slower_than_the_proportional_term(self) -> None:
        proportional = make_controller(ki=0.0, kd=0.0, discharge_bias_w=0.0)
        integral = make_controller(kp=0.0, kd=0.0, discharge_bias_w=0.0)
        for controller in (proportional, integral):
            for k in range(3):
                controller.step(make_inputs(now=5.0 * k, grid_w=0.0))
        p = proportional.step(make_inputs(now=15.0, grid_w=-1000.0))
        i = integral.step(make_inputs(now=15.0, grid_w=-1000.0))
        assert p == int(0.7 * 1000.0 / EFF_NO_DC)  # kp x the full error
        assert i == int(1.0 * 250.0 / EFF_NO_DC)  # ki x the error averaged with three zeros


class TestExcessFilter:
    def test_filter_starts_at_the_first_excess(self) -> None:
        controller = make_controller()
        controller.step(make_inputs(now=0.0, grid_w=400.0))
        assert controller._excess_lp_w == pytest.approx(400.0)

    def test_excess_is_the_grid_power_without_the_battery(self) -> None:
        controller = make_controller()
        # the battery discharges 1000 W DC at 95 % efficiency: the house would export 950 W less
        controller.step(
            make_inputs(now=0.0, grid_w=100.0, bat_dc_w=1000.0, ac_w=950.0, dc_pv_w=0.0)
        )
        assert controller._excess_lp_w == pytest.approx(100.0 - 0.95 * 1000.0)

    def test_filter_follows_with_the_time_constant(self) -> None:
        controller = make_controller(excess_tau_s=30.0)
        controller.step(make_inputs(now=0.0, grid_w=0.0))
        controller.step(make_inputs(now=1.0, grid_w=400.0))
        assert controller._excess_lp_w == pytest.approx(400.0 * (1.0 - math.exp(-1.0 / 30.0)))

    def test_short_export_peak_does_not_start_charging(self) -> None:
        controller = make_controller()
        assert controller.step(make_inputs(now=0.0, grid_w=0.0)) == 0
        assert controller.step(make_inputs(now=1.0, grid_w=400.0)) == 0

    def test_lasting_export_starts_charging(self) -> None:
        controller = make_controller()
        controller.step(make_inputs(now=0.0, grid_w=0.0))
        controller.step(make_inputs(now=1.0, grid_w=400.0))
        assert controller.step(make_inputs(now=200.0, grid_w=400.0)) < 0

    def test_headroom_is_configurable(self) -> None:
        assert single_step({"charge_headroom_w": 500.0}, grid_w=400.0) == 0
        assert single_step({"charge_headroom_w": 100.0}, grid_w=400.0) < 0


class TestCriticalDamping:
    """A fast change of the error across zero must not make the battery swing at full power."""

    ECO_W = 2000.0  # eco_c_rate (0.2 C) of the 10 kWh battery

    def test_charge_after_a_large_swing_is_limited_to_the_eco_power(self) -> None:
        controller = make_controller(excess_tau_s=0.001)
        controller.step(make_inputs(now=0.0, grid_w=-2000.0))  # large import
        output = controller.step(make_inputs(now=60.0, grid_w=2000.0))  # large export
        assert -2100.0 < output < -self.ECO_W

    def test_charge_without_a_swing_is_not_limited(self) -> None:
        controller = make_controller(excess_tau_s=0.001)
        controller.step(make_inputs(now=0.0, grid_w=500.0))
        output = controller.step(make_inputs(now=60.0, grid_w=2000.0))
        assert output < -3000.0

    def test_discharge_after_a_large_swing_is_limited_to_the_eco_power(self) -> None:
        controller = make_controller(excess_tau_s=0.001)
        controller.step(make_inputs(now=0.0, grid_w=2000.0))  # large export
        output = controller.step(make_inputs(now=60.0, grid_w=-2000.0))  # large import
        # eco power + offset correction (45 W) + bias (120 W)
        assert output == pytest.approx(self.ECO_W + 45.0 + 120.0, abs=3.0)

    def test_discharge_without_a_swing_is_not_limited(self) -> None:
        controller = make_controller(excess_tau_s=0.001)
        controller.step(make_inputs(now=0.0, grid_w=-500.0))
        output = controller.step(make_inputs(now=60.0, grid_w=-2000.0))
        assert output > 3000.0

    def test_small_swing_is_not_critical(self) -> None:
        def run(critical_delta_w: float) -> int:
            controller = make_controller(excess_tau_s=0.001, critical_delta_w=critical_delta_w)
            controller.step(make_inputs(now=0.0, grid_w=-300.0))
            return controller.step(make_inputs(now=60.0, grid_w=300.0))

        # the error changes by about 600 W / efficiency, below critical_delta_w (1000 W):
        # the result is the same as if the damping did not exist
        assert run(1000.0) == run(1e9)

    def test_threshold_is_configurable(self) -> None:
        def run(critical_delta_w: float) -> int:
            controller = make_controller(excess_tau_s=0.001, critical_delta_w=critical_delta_w)
            controller.step(make_inputs(now=0.0, grid_w=-1000.0))
            return controller.step(make_inputs(now=60.0, grid_w=1000.0))

        # critical with the low threshold: limited to eco power, so less charging
        assert abs(run(500.0)) < abs(run(1e9))


class TestOffsetCorrection:
    """Correction by the deviation of the measured from the last commanded battery power.

    This pins the heuristic as it is: the correction is added in the direction of the deviation
    and only on the discharge side, clamped to offset_error_limit_w (50 W) with offset_kp 0.9.
    """

    GAIN = 0.9

    @staticmethod
    def second_step_output(
        bat_offset_w: float, controller_settings: dict[str, float] | None = None
    ) -> tuple[int, int]:
        """Return (first output, second output) with the measured power off by bat_offset_w."""
        controller = make_controller(**(controller_settings or {}))
        first = controller.step(make_inputs(now=0.0, grid_w=-1000.0))
        measured_w = first + bat_offset_w
        # efficiency of 0.95 for every variant
        second = controller.step(
            make_inputs(now=5.0, grid_w=-1000.0, bat_dc_w=measured_w, ac_w=0.95 * measured_w)
        )
        return first, second

    def test_matching_battery_power_needs_no_correction(self) -> None:
        _, with_correction = self.second_step_output(0.0)
        _, without_correction = self.second_step_output(0.0, {"offset_kp": 0.0})
        assert with_correction == without_correction

    def test_the_base_of_the_output_is_the_measured_battery_power(self) -> None:
        _, same = self.second_step_output(0.0)
        _, higher = self.second_step_output(10.0)
        # 10 W more measured battery power: 10 W more output, plus the offset correction
        assert higher - same == pytest.approx(10.0 + self.GAIN * 10.0, abs=2.0)

    def test_overshoot_is_corrected_in_the_direction_of_the_deviation(self) -> None:
        _, same = self.second_step_output(0.0)
        _, higher = self.second_step_output(40.0)
        assert higher - same == pytest.approx(40.0 + self.GAIN * 40.0, abs=2.0)

    def test_undershoot_is_not_corrected(self) -> None:
        _, same = self.second_step_output(0.0)
        _, lower = self.second_step_output(-40.0)
        assert lower - same == pytest.approx(-40.0, abs=2.0)

    def test_correction_is_clamped(self) -> None:
        _, same = self.second_step_output(0.0)
        _, much_higher = self.second_step_output(500.0)
        assert much_higher - same == pytest.approx(500.0 + self.GAIN * 50.0, abs=2.0)


class TestOutputBounds:
    @staticmethod
    def sweep() -> Any:
        for grid_w in range(-8000, 8001, 700):
            for bat_dc_w in (-3000.0, 0.0, 3000.0):
                for soc_percent in (0.0, 5.0, 50.0, 99.0, 100.0):
                    for temp_c in (-10.0, 0.0, 5.0, 25.0, 60.0, 70.0):
                        yield make_inputs(
                            grid_w=float(grid_w),
                            bat_dc_w=bat_dc_w,
                            soc_percent=soc_percent,
                            temp_c=temp_c,
                            hw_max_charge_w=2500.0,
                            hw_max_discharge_w=3500.0,
                        )

    def test_output_stays_within_the_hardware_limits(self) -> None:
        for inputs in self.sweep():
            output = make_controller().step(inputs)
            assert -2500 <= output <= 3500, inputs

    def test_nothing_happens_if_charging_and_discharging_are_forbidden(self) -> None:
        for inputs in self.sweep():
            forbidden = dataclasses.replace(inputs, allow_charge=False, allow_discharge=False)
            assert make_controller().step(forbidden) == 0, inputs

    def test_never_charges_if_charging_is_forbidden(self) -> None:
        for inputs in self.sweep():
            output = make_controller().step(dataclasses.replace(inputs, allow_charge=False))
            assert output >= 0, inputs

    def test_never_discharges_if_discharging_is_forbidden(self) -> None:
        for inputs in self.sweep():
            output = make_controller().step(dataclasses.replace(inputs, allow_discharge=False))
            assert output <= 0, inputs

    def test_output_is_always_an_integer(self) -> None:
        for inputs in self.sweep():
            assert isinstance(make_controller().step(inputs), int)


class Plant:
    """House with a battery behind the inverter: first-order battery response, no noise.

    The grid power is export (+). The battery follows the commanded power with the time
    constant `tau_s`; charging draws more and discharging delivers less than the DC power.
    """

    def __init__(
        self,
        scenario: Callable[[float], tuple[float, float]],
        *,
        tau_s: float = 4.0,
        efficiency: float = 0.95,
        soc_percent: float = 60.0,
        capacity_wh: float = 10000.0,
    ) -> None:
        self.scenario = scenario  # time -> (pv_w, load_w)
        self.tau_s = tau_s
        self.efficiency = efficiency
        self.soc_percent = soc_percent
        self.capacity_wh = capacity_wh
        self.bat_dc_w = 0.0

    def run(
        self, controller: GridPowerController, *, dt: float, duration: float
    ) -> list[tuple[float, float, float]]:
        """Return a list of (time, grid power, SoC)."""
        trace = []
        command_w = 0
        t = 0.0
        for _ in range(int(duration / dt)):
            pv_w, load_w = self.scenario(t)
            self.bat_dc_w += (command_w - self.bat_dc_w) * (1.0 - math.exp(-dt / self.tau_s))
            bat_ac_w = (
                self.bat_dc_w * self.efficiency
                if self.bat_dc_w > 0
                else self.bat_dc_w / self.efficiency
            )
            grid_w = pv_w - load_w + bat_ac_w
            self.soc_percent -= self.bat_dc_w * dt / 3600.0 / self.capacity_wh * 100.0
            command_w = controller.step(
                make_inputs(
                    now=t,
                    grid_w=grid_w,
                    ac_w=pv_w + bat_ac_w,
                    dc_pv_w=pv_w / 0.97,
                    bat_dc_w=self.bat_dc_w,
                    soc_percent=self.soc_percent,
                    capacity_wh=self.capacity_wh,
                )
            )
            trace.append((t, grid_w, self.soc_percent))
            t += dt
        return trace


def night(t: float) -> tuple[float, float]:
    """No PV, load steps 1000 W -> 3000 W (t = 400 s) -> 1500 W (t = 800 s)."""
    return 0.0, 1000.0 if t < 400.0 else (3000.0 if t < 800.0 else 1500.0)


def day(t: float) -> tuple[float, float]:
    """3 kW PV, load steps 500 W -> 2000 W (t = 400 s) -> 300 W (t = 800 s)."""
    return 3000.0, 500.0 if t < 400.0 else (2000.0 if t < 800.0 else 300.0)


def settled_grid_w(trace: list[tuple[float, float, float]], start: float, end: float) -> list[float]:
    return [grid_w for t, grid_w, _ in trace if start <= t < end]


# phases of the scenarios: the end of each phase, where the loop has long settled
SETTLED_WINDOWS = [(300.0, 400.0), (700.0, 800.0), (1100.0, 1200.0)]


class TestClosedLoop:
    """The controller against a plant model. Thresholds leave a large margin to the exact values."""

    @pytest.mark.parametrize("dt", [5.0, 2.0])
    @pytest.mark.parametrize("scenario", [night, day], ids=["night", "day"])
    def test_grid_power_settles_after_load_steps(
        self, scenario: Callable[[float], tuple[float, float]], dt: float
    ) -> None:
        trace = Plant(scenario).run(make_controller(), dt=dt, duration=1200.0)
        for start, end in SETTLED_WINDOWS:
            grid = settled_grid_w(trace, start, end)
            assert max(abs(g) for g in grid) < 100.0, (start, end)
            # settled: no oscillation
            assert max(grid) - min(grid) < 5.0, (start, end)

    @pytest.mark.parametrize("scenario", [night, day], ids=["night", "day"])
    def test_control_interval_does_not_change_the_settled_grid_power(
        self, scenario: Callable[[float], tuple[float, float]]
    ) -> None:
        means = {}
        for dt in (5.0, 2.0):
            trace = Plant(scenario).run(make_controller(), dt=dt, duration=1200.0)
            means[dt] = [
                sum(g := settled_grid_w(trace, a, b)) / len(g) for a, b in SETTLED_WINDOWS
            ]
        for slow, fast in zip(means[5.0], means[2.0]):
            assert slow == pytest.approx(fast, abs=10.0)

    def test_without_bias_the_grid_power_settles_at_zero_while_discharging(self) -> None:
        trace = Plant(night).run(
            make_controller(discharge_bias_w=0.0), dt=5.0, duration=1200.0
        )
        for start, end in SETTLED_WINDOWS:
            grid = settled_grid_w(trace, start, end)
            assert abs(sum(grid) / len(grid)) < 5.0

    @pytest.mark.parametrize("bias_w", [60.0, 120.0, 240.0])
    def test_bias_settles_as_standing_export(self, bias_w: float) -> None:
        # The bias is added in every step and balanced by the control error: it appears as an
        # export of about bias * efficiency / (kp + ki), not as battery power.
        settings = GridControllerSettings(discharge_bias_w=bias_w)
        expected_w = bias_w * 0.95 / (settings.kp + settings.ki)
        trace = Plant(night).run(GridPowerController(settings), dt=5.0, duration=1200.0)
        for start, end in SETTLED_WINDOWS:
            grid = settled_grid_w(trace, start, end)
            assert sum(grid) / len(grid) == pytest.approx(expected_w, abs=0.15 * expected_w + 3.0)

    def test_bias_does_not_depend_on_the_control_interval(self) -> None:
        # the standing export only depends on the gains of one step, not on the step length
        means = []
        for dt in (5.0, 2.0):
            trace = Plant(night).run(make_controller(), dt=dt, duration=1200.0)
            grid = settled_grid_w(trace, 700.0, 800.0)
            means.append(sum(grid) / len(grid))
        assert means[0] == pytest.approx(means[1], abs=5.0)

    def test_battery_stays_within_the_soc_limits(self) -> None:
        trace = Plant(night, soc_percent=7.0).run(make_controller(), dt=5.0, duration=1200.0)
        assert min(soc for _, _, soc in trace) >= 5.0 - 0.1
        trace = Plant(day, soc_percent=99.0).run(make_controller(), dt=5.0, duration=1200.0)
        assert max(soc for _, _, soc in trace) <= 100.0 + 0.1

    def test_empty_battery_leaves_the_grid_to_cover_the_load(self) -> None:
        trace = Plant(night, soc_percent=5.0).run(make_controller(), dt=5.0, duration=300.0)
        # the battery does not discharge: all the load is imported
        assert settled_grid_w(trace, 200.0, 300.0)[-1] == pytest.approx(-1000.0, abs=1.0)

    def test_grid_setpoint_is_held(self) -> None:
        # hold 300 W of import: the setpoint is part of the inputs of every step
        controller = make_controller(discharge_bias_w=0.0)
        plant = Plant(night)
        command_w, grid_trace = 0, []
        t = 0.0
        for _ in range(240):
            pv_w, load_w = plant.scenario(t)
            plant.bat_dc_w += (command_w - plant.bat_dc_w) * (1.0 - math.exp(-5.0 / 4.0))
            bat_ac_w = plant.bat_dc_w * 0.95 if plant.bat_dc_w > 0 else plant.bat_dc_w / 0.95
            grid_w = pv_w - load_w + bat_ac_w
            command_w = controller.step(
                make_inputs(
                    now=t,
                    grid_w=grid_w,
                    ac_w=bat_ac_w,
                    bat_dc_w=plant.bat_dc_w,
                    grid_setpoint_w=-300.0,
                )
            )
            grid_trace.append((t, grid_w))
            t += 5.0
        settled = [g for tt, g in grid_trace if 300.0 <= tt < 400.0]
        assert sum(settled) / len(settled) == pytest.approx(-300.0, abs=10.0)


def make_limiter(**settings: Any) -> PvExportLimiter:
    """10 kW inverter: one percent of the limit is 100 W."""
    return PvExportLimiter(PvLimiterSettings(rated_power_w=10000.0, **settings))


# A saturated battery, 6 kW PV and a load of 1 kW: about 5.7 kW AC power, 4.7 kW export.
SURPLUS: dict[str, Any] = {"export_w": 4700.0, "ac_w": 5700.0, "saturated": True}


def limit_at(limiter: PvExportLimiter, now: float) -> None:
    """Put the limiter into the limiting state, as the caller does after a write."""
    limiter.committed(now, 11)


class TestPvExportLimiterStart:
    def test_starts_unlimited(self) -> None:
        limiter = make_limiter()
        assert limiter.percent == 100
        assert not limiter.limiting

    def test_reset_takes_over_the_limit_of_the_inverter(self) -> None:
        limiter = make_limiter()
        limiter.reset(40)
        assert limiter.percent == 40
        assert limiter.limiting

    @pytest.mark.parametrize("reported, expected", [(-5, 0), (150, 100)])
    def test_reset_clamps_the_reported_limit(self, reported: int, expected: int) -> None:
        limiter = make_limiter()
        limiter.reset(reported)
        assert limiter.percent == expected

    def test_committed_limit_is_in_force(self) -> None:
        limiter = make_limiter()
        limiter.committed(0.0, 25)
        assert (limiter.percent, limiter.limiting) == (25, True)


class TestPvExportLimiterEnter:
    def test_no_limit_without_a_saturated_battery(self) -> None:
        limiter = make_limiter()
        for now in (0.0, 30.0, 120.0, 600.0):
            assert limiter.propose(now, export_w=4700.0, ac_w=5700.0, saturated=False) is None

    @pytest.mark.parametrize("export_w", [0.0, 30.0, 50.0, -500.0])
    def test_no_limit_without_export(self, export_w: float) -> None:
        limiter = make_limiter()
        for now in (0.0, 120.0, 600.0):
            assert limiter.propose(now, export_w=export_w, ac_w=1000.0, saturated=True) is None

    def test_limit_only_after_the_hold_time(self) -> None:
        limiter = make_limiter(hold_s=60.0)
        assert limiter.propose(0.0, **SURPLUS) is None
        assert limiter.propose(59.0, **SURPLUS) is None
        assert limiter.propose(60.0, **SURPLUS) is not None

    def test_limit_leaves_the_headroom_as_export(self) -> None:
        # load 1000 W + headroom 100 W = 1100 W of 10 kW
        limiter = make_limiter()
        limiter.propose(0.0, **SURPLUS)
        assert limiter.propose(60.0, **SURPLUS) == 11

    def test_limit_is_rounded_up(self) -> None:
        # 1050 W is 10.5 %: 10 % would import, 11 % exports a little
        limiter = make_limiter(headroom_w=50.0)
        surplus: dict[str, Any] = {"export_w": 4700.0, "ac_w": 5700.0, "saturated": True}
        limiter.propose(0.0, **surplus)
        assert limiter.propose(60.0, **surplus) == 11

    def test_headroom_is_configurable(self) -> None:
        limiter = make_limiter(headroom_w=300.0)
        limiter.propose(0.0, **SURPLUS)
        assert limiter.propose(60.0, **SURPLUS) == 13  # 1300 W

    def test_limit_is_clamped_to_the_valid_range(self) -> None:
        limiter = make_limiter(headroom_w=0.0)
        # AC power below the export cannot happen, the limit does not go below 0 %
        limiter.propose(0.0, export_w=500.0, ac_w=100.0, saturated=True)
        assert limiter.propose(60.0, export_w=500.0, ac_w=100.0, saturated=True) == 0

    def test_short_export_peak_does_not_start_the_hold_time(self) -> None:
        limiter = make_limiter()
        limiter.propose(0.0, **SURPLUS)
        limiter.propose(30.0, export_w=0.0, ac_w=1000.0, saturated=True)  # export gone
        assert limiter.propose(70.0, **SURPLUS) is None  # the hold time starts again
        assert limiter.propose(130.0, **SURPLUS) is not None

    def test_battery_that_can_charge_again_resets_the_hold_time(self) -> None:
        limiter = make_limiter()
        limiter.propose(0.0, **SURPLUS)
        limiter.propose(30.0, export_w=4700.0, ac_w=5700.0, saturated=False)
        assert limiter.propose(70.0, **SURPLUS) is None

    def test_proposal_is_repeated_until_it_is_committed(self) -> None:
        limiter = make_limiter()
        limiter.propose(0.0, **SURPLUS)
        assert limiter.propose(60.0, **SURPLUS) == 11
        # the write failed, nothing was committed
        assert limiter.propose(65.0, **SURPLUS) == 11
        assert limiter.percent == 100


class TestPvExportLimiterWhileLimiting:
    def test_export_of_the_headroom_needs_no_action(self) -> None:
        limiter = make_limiter()
        limit_at(limiter, 0.0)
        for now in (100.0, 200.0, 300.0):
            assert limiter.propose(now, export_w=150.0, ac_w=1150.0, saturated=True) is None

    def test_import_raises_the_limit_after_the_hold_time(self) -> None:
        # the load rose to 3 kW, the limit (1.1 kW) holds the inverter back: 1.9 kW import
        limiter = make_limiter()
        limit_at(limiter, 0.0)
        importing: dict[str, Any] = {"export_w": -1900.0, "ac_w": 1100.0, "saturated": True}
        assert limiter.propose(100.0, **importing) is None
        assert limiter.propose(160.0, **importing) == 31  # load 3000 W + headroom 100 W

    def test_too_much_export_lowers_the_limit(self) -> None:
        # the load dropped to 500 W: the export grew to 600 W
        limiter = make_limiter(min_write_interval_s=10.0)
        limit_at(limiter, 0.0)
        exporting: dict[str, Any] = {"export_w": 700.0, "ac_w": 1100.0, "saturated": True}
        assert limiter.propose(100.0, **exporting) is None
        assert limiter.propose(160.0, **exporting) == 5  # load 400 W + headroom 100 W

    def test_small_changes_are_not_written(self) -> None:
        limiter = make_limiter(step_percent=5, min_write_interval_s=10.0)
        limit_at(limiter, 0.0)
        exporting: dict[str, Any] = {"export_w": 700.0, "ac_w": 1100.0, "saturated": True}
        limiter.propose(100.0, **exporting)
        assert limiter.propose(160.0, **exporting) == 5  # 6 percentage points away
        limiter.committed(160.0, 8)
        # 8 % -> 5 % is below the step of 5 percentage points
        slightly_more: dict[str, Any] = {"export_w": 450.0, "ac_w": 800.0, "saturated": True}
        limiter.propose(200.0, **slightly_more)
        assert limiter.propose(400.0, **slightly_more) is None

    def test_changes_are_rate_limited(self) -> None:
        limiter = make_limiter(min_write_interval_s=300.0)
        limiter.committed(0.0, 11)
        importing: dict[str, Any] = {"export_w": -1900.0, "ac_w": 1100.0, "saturated": True}
        limiter.propose(100.0, **importing)
        assert limiter.propose(200.0, **importing) is None  # 200 s after the last write
        assert limiter.propose(301.0, **importing) == 31

    def test_release_after_the_hold_time_if_the_battery_is_not_saturated(self) -> None:
        limiter = make_limiter()
        limit_at(limiter, 0.0)
        free: dict[str, Any] = {"export_w": 20.0, "ac_w": 1000.0, "saturated": False}
        assert limiter.propose(100.0, **free) is None
        assert limiter.propose(159.0, **free) is None
        assert limiter.propose(160.0, **free) == 100

    def test_release_ignores_the_step_size(self) -> None:
        limiter = make_limiter(step_percent=50)
        limiter.committed(0.0, 99)
        free: dict[str, Any] = {"export_w": 0.0, "ac_w": 1000.0, "saturated": False}
        limiter.propose(100.0, **free)
        assert limiter.propose(200.0, **free) == 100

    def test_saturated_battery_keeps_the_limit(self) -> None:
        limiter = make_limiter()
        limit_at(limiter, 0.0)
        for now in range(100, 1000, 100):
            assert limiter.propose(float(now), export_w=150.0, ac_w=1150.0, saturated=True) is None

    def test_saturated_again_resets_the_release_timer(self) -> None:
        limiter = make_limiter()
        limit_at(limiter, 0.0)
        limiter.propose(100.0, export_w=20.0, ac_w=1000.0, saturated=False)
        limiter.propose(140.0, export_w=150.0, ac_w=1150.0, saturated=True)
        assert limiter.propose(170.0, export_w=20.0, ac_w=1000.0, saturated=False) is None
        assert limiter.propose(231.0, export_w=20.0, ac_w=1000.0, saturated=False) == 100

    def test_import_is_followed_not_released_even_if_not_saturated(self) -> None:
        # a load that rose while the battery backed off: the limit follows the load first
        limiter = make_limiter()
        limit_at(limiter, 0.0)
        importing: dict[str, Any] = {"export_w": -1900.0, "ac_w": 1100.0, "saturated": False}
        limiter.propose(100.0, **importing)
        assert limiter.propose(160.0, **importing) == 31

    def test_release_to_100_percent_is_also_rate_limited(self) -> None:
        limiter = make_limiter(min_write_interval_s=300.0)
        limiter.committed(100.0, 11)
        free: dict[str, Any] = {"export_w": 0.0, "ac_w": 1000.0, "saturated": False}
        limiter.propose(110.0, **free)
        assert limiter.propose(200.0, **free) is None
        assert limiter.propose(401.0, **free) == 100


class TestPvExportLimiterKeepAlive:
    def test_unchanged_limit_is_repeated_while_limiting(self) -> None:
        limiter = make_limiter(keepalive_s=240.0)
        limiter.committed(0.0, 11)
        steady: dict[str, Any] = {"export_w": 150.0, "ac_w": 1150.0, "saturated": True}
        assert limiter.propose(200.0, **steady) is None
        assert limiter.propose(240.0, **steady) == 11
        limiter.committed(240.0, 11)
        assert limiter.propose(300.0, **steady) is None
        assert limiter.propose(480.0, **steady) == 11

    def test_no_keep_alive_by_default(self) -> None:
        limiter = make_limiter()
        limiter.committed(0.0, 11)
        steady: dict[str, Any] = {"export_w": 150.0, "ac_w": 1150.0, "saturated": True}
        assert limiter.propose(100000.0, **steady) is None

    def test_no_keep_alive_while_not_limiting(self) -> None:
        limiter = make_limiter(keepalive_s=10.0)
        assert limiter.propose(1000.0, export_w=0.0, ac_w=1000.0, saturated=False) is None
