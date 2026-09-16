import numpy as np
import pytest

from akkudoktoreos.prediction.interpolator import get_eos_load_interpolator


def test_quarter_hour_energy_is_converted_back_to_same_mean_power():
    """Splitting hourly energy must not change the minute-load probability lookup."""
    interpolator = get_eos_load_interpolator()
    hourly_load_wh = 800.0
    hourly_pv_wh = 1200.0
    slot_duration_h = 0.25

    hourly = interpolator.calculate_expected_direct_consumption(hourly_load_wh, hourly_pv_wh)
    quarter_hour = interpolator.calculate_expected_direct_consumption(
        (hourly_load_wh / 4) / slot_duration_h,
        (hourly_pv_wh / 4) / slot_duration_h,
    )

    assert quarter_hour == pytest.approx(hourly)


def test_load_above_probability_grid_uses_highest_supported_distribution():
    """Out-of-range household load must not make self-consumption jump to zero."""
    interpolator = get_eos_load_interpolator()

    at_boundary = interpolator.calculate_self_consumption(3450.0, 5000.0)
    above_boundary = interpolator.calculate_self_consumption(4000.0, 5000.0)

    assert above_boundary == pytest.approx(at_boundary)
    assert above_boundary > 0.99


def test_expected_direct_consumption_accounts_for_subhourly_load_variation():
    """Expected overlap must be below the optimistic overlap of interval means."""
    interpolator = get_eos_load_interpolator()

    direct_power_w = interpolator.calculate_expected_direct_consumption(800.0, 1200.0)

    assert direct_power_w == pytest.approx(621.0, abs=2.0)
    assert 0.0 < direct_power_w < 800.0


@pytest.mark.parametrize(
    ("mean_load_power_w", "pv_power_w"),
    [(800.0, 1200.0), (1000.0, 500.0), (1500.0, 1500.0)],
)
def test_expected_direct_consumption_produces_conservative_energy_balance(
    mean_load_power_w, pv_power_w
):
    """Direct use, residual load and surplus must conserve both mean powers."""
    interpolator = get_eos_load_interpolator()

    direct_power_w = interpolator.calculate_expected_direct_consumption(
        mean_load_power_w, pv_power_w
    )
    residual_load_w = mean_load_power_w - direct_power_w
    pv_surplus_w = pv_power_w - direct_power_w

    assert 0.0 <= direct_power_w <= min(mean_load_power_w, pv_power_w)
    assert direct_power_w + residual_load_w == pytest.approx(mean_load_power_w)
    assert direct_power_w + pv_surplus_w == pytest.approx(pv_power_w)


def test_expected_direct_consumption_preserves_forecast_mean_at_high_pv():
    """A PV level above every normalized load bin covers the complete mean load."""
    interpolator = get_eos_load_interpolator()

    direct_power_w = interpolator.calculate_expected_direct_consumption(3000.0, 10000.0)

    assert direct_power_w == pytest.approx(3000.0)


@pytest.mark.parametrize("load,pv", [(4000.0, 5000.0), (10000.0, 20000.0), (0.0, 0.0)])
def test_genetic_interpolator_boundaries_are_finite_and_physical(load, pv):
    interpolator = get_eos_load_interpolator()
    fraction = interpolator.calculate_self_consumption(load, pv)
    direct = interpolator.calculate_expected_direct_consumption(load, pv)
    assert np.isfinite(fraction)
    assert 0.0 <= fraction <= 1.0
    assert np.isfinite(direct)
    assert 0.0 <= direct <= min(load, pv)


def test_genetic0_inverter_keeps_its_independent_interpolator():
    from akkudoktoreos.devices.genetic0.genetic0inverter import (
        Genetic0Inverter,
        Genetic0InverterParameters,
    )
    from akkudoktoreos.optimization.genetic0.genetic0loadinterpolator import (
        get_genetic0_load_interpolator,
    )

    inverter = Genetic0Inverter(Genetic0InverterParameters(device_id="legacy", max_power_wh=10000))
    assert inverter.self_consumption_predictor is get_genetic0_load_interpolator()
    assert inverter.self_consumption_predictor is not get_eos_load_interpolator()


@pytest.mark.parametrize("load,pv", [(4000.0, 5000.0), (10000.0, 20000.0)])
def test_genetic_inverter_boundary_flows_remain_nonnegative(load, pv):
    from akkudoktoreos.devices.genetic.inverter import Inverter, InverterParameters

    inverter = Inverter(InverterParameters(device_id="boundary", max_power_wh=25000))
    flows = inverter.process_energy(generation=pv, consumption=load, hour=0)
    assert all(np.isfinite(value) and value >= 0.0 for value in flows)
