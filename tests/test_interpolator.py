import pytest

from akkudoktoreos.prediction.interpolator import get_eos_load_interpolator


def test_quarter_hour_energy_is_converted_back_to_same_mean_power():
    """Splitting hourly energy must not change the minute-load probability lookup."""
    interpolator = get_eos_load_interpolator()
    hourly_load_wh = 800.0
    hourly_pv_wh = 1200.0
    slot_duration_h = 0.25

    hourly = interpolator.calculate_self_consumption(hourly_load_wh, hourly_pv_wh)
    quarter_hour = interpolator.calculate_self_consumption(
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
