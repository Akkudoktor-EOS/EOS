from unittest.mock import Mock

import pytest

from akkudoktoreos.class_inverter import Inverter


@pytest.fixture
def mock_battery():
    mock_battery = Mock()
    mock_battery.energie_laden = Mock(return_value=(0.0, 0.0))
    mock_battery.energie_abgeben = Mock(return_value=(0.0, 0.0))
    return mock_battery


@pytest.fixture
def inverter(mock_battery):
    return Inverter(max_power_wh=500.0, battery=mock_battery)


def test_process_energy_excess_generation(inverter, mock_battery):
    mock_battery.energie_laden.return_value = (
        100.0,
        10.0,
    )  # Battery charges 100 Wh with 10 Wh loss
    generation = 600.0  # 600 Wh of generation
    consumption = 200.0  # 200 Wh of consumption
    hour = 12

    grid_feed_in, grid_draw, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    # Use pytest.approx for floating point comparison to allow for minor rounding differences
    assert grid_feed_in == pytest.approx(
        290.0, rel=1e-2
    )  # Approximately 300 Wh feed-in
    assert grid_draw == 0.0
    assert losses == 10.0
    assert self_consumption == 200.0
    mock_battery.energie_laden.assert_called_once_with(400.0, hour)


def test_process_energy_generation_equals_consumption(inverter, mock_battery):
    generation = 300.0
    consumption = 300.0
    hour = 12

    grid_feed_in, grid_draw, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    # Use pytest.approx for approximate comparison
    assert grid_feed_in == 0.0
    assert grid_draw == 0.0
    assert losses == 0.0
    assert self_consumption == 300.0

    # Allow for a possible call with zero energy, since the method is invoked even when no energy is available for charging
    mock_battery.energie_laden.assert_called_once_with(0.0, hour)
