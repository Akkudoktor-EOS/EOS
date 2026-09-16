"""Tests for the concave terminal value of the energy left in the battery."""

import numpy as np
import pytest

from akkudoktoreos.optimization.genetic.terminalvalue import (
    build_terminal_value_curve,
    trailing_window,
)


def _curve(**overrides):
    """Two expensive slots, one cheap one, no PV, 10 kWh of usable battery."""
    params = dict(
        prices_euro_per_wh=np.array([0.0004, 0.0003, 0.0001]),
        load_wh=np.array([1000.0, 1000.0, 1000.0]),
        pv_wh=np.array([0.0, 0.0, 0.0]),
        feed_in_euro_per_wh=np.array([0.00008, 0.00008, 0.00008]),
        max_energy_wh=10000.0,
        lcos_euro_per_kwh=0.0,
        dc_to_ac_efficiency=1.0,
        grid_export_allowed=False,
    )
    params.update(overrides)
    return build_terminal_value_curve(**params)


def test_marginal_value_follows_the_most_expensive_hours_first():
    """The first stored kWh replaces the most expensive slot, then the next."""
    curve = _curve()

    # 0.40, 0.30 and 0.10 EUR/kWh, in that order.
    assert curve.marginal_euro_per_kwh == pytest.approx([0.4, 0.3, 0.1])
    assert curve.energy_wh == pytest.approx([0.0, 1000.0, 2000.0, 3000.0])
    assert curve.value_euro == pytest.approx([0.0, 0.4, 0.7, 0.8])


def test_curve_is_concave_and_saturates():
    """Marginal values only decrease, and beyond the last breakpoint nothing is added."""
    curve = _curve()
    marginals = curve.marginal_euro_per_kwh

    assert all(a >= b for a, b in zip(marginals, marginals[1:]))
    # The residual load of the window is 3 kWh - more energy replaces nothing.
    assert curve.value(3000.0) == pytest.approx(0.8)
    assert curve.value(9000.0) == pytest.approx(0.8)


def test_value_interpolates_within_a_segment():
    """Half of the first slot is worth half of the first segment."""
    curve = _curve()
    assert curve.value(500.0) == pytest.approx(0.2)


def test_pv_reduces_the_residual_load():
    """Only load that PV cannot cover can be replaced by stored energy."""
    curve = _curve(pv_wh=np.array([600.0, 1000.0, 0.0]))

    # Slot 0 keeps 400 Wh, slot 1 is fully covered by PV, slot 2 keeps 1000 Wh.
    assert curve.energy_wh == pytest.approx([0.0, 400.0, 1400.0])
    assert curve.marginal_euro_per_kwh == pytest.approx([0.4, 0.1])


def test_lcos_is_subtracted_from_the_marginal_value():
    """Storage cost is already charged on discharge and must not be credited twice."""
    curve = _curve(lcos_euro_per_kwh=0.05, dc_to_ac_efficiency=1.0)
    assert curve.marginal_euro_per_kwh == pytest.approx([0.35, 0.25, 0.05])


def test_negative_prices_do_not_create_value():
    """Storing energy for an hour that pays nothing is not worth anything."""
    curve = _curve(prices_euro_per_wh=np.array([0.0004, -0.0001, 0.0]))
    assert curve.marginal_euro_per_kwh == pytest.approx([0.4])
    assert curve.value(5000.0) == pytest.approx(0.4)


def test_export_tail_only_with_direct_marketing():
    """Surplus beyond the residual load is worth an export - if export is allowed."""
    without = _curve(grid_export_allowed=False)
    with_export = _curve(grid_export_allowed=True)

    assert without.value(10000.0) == pytest.approx(0.8)
    # 7 kWh beyond the residual load at the median feed-in tariff of 0.08 EUR/kWh.
    assert with_export.value(10000.0) == pytest.approx(0.8 + 7.0 * 0.08)
    assert with_export.marginal_euro_per_kwh[-1] == pytest.approx(0.08)


def test_residual_energy_marks_the_knee():
    """The knee separates load-backed value from the export tail."""
    without = _curve(grid_export_allowed=False)
    with_export = _curve(grid_export_allowed=True)

    # 3 kWh of residual load in the window, whether or not export is allowed.
    assert without.residual_energy_wh == pytest.approx(3000.0)
    assert with_export.residual_energy_wh == pytest.approx(3000.0)
    # Only the export tail reaches beyond it.
    assert without.energy_wh[-1] == pytest.approx(3000.0)
    assert with_export.energy_wh[-1] == pytest.approx(10000.0)


def test_curve_is_capped_by_the_usable_battery_energy():
    """A battery smaller than the residual load ends the curve early."""
    curve = _curve(max_energy_wh=1500.0)
    assert curve.energy_wh[-1] == pytest.approx(1500.0)
    assert curve.value(5000.0) == pytest.approx(0.4 + 0.5 * 0.3)


def test_empty_window_yields_an_empty_curve():
    """Without data there is no curve, and no credit."""
    curve = build_terminal_value_curve(
        prices_euro_per_wh=np.zeros(0),
        load_wh=np.zeros(0),
        pv_wh=np.zeros(0),
        feed_in_euro_per_wh=np.zeros(0),
        max_energy_wh=10000.0,
    )
    assert curve.energy_wh == []
    assert curve.value(5000.0) == 0.0


def test_trailing_window_takes_the_end_of_the_horizon():
    values = np.arange(10, dtype=float)

    assert list(trailing_window(values, end_slot=8, window_slots=3)) == [5.0, 6.0, 7.0]
    # A window longer than the horizon yields what there is.
    assert list(trailing_window(values, end_slot=2, window_slots=5)) == [0.0, 1.0]
    assert list(trailing_window(None, end_slot=8, window_slots=3)) == []
