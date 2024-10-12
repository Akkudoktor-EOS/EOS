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
    # Battery charges 100 Wh with 10 Wh loss
    mock_battery.energie_laden.return_value = (100.0, 10.0)
    generation = 600.0  # 600 Wh of generation
    consumption = 200.0  # 200 Wh of consumption
    hour = 12

    grid_feed_in, grid_draw, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_feed_in == pytest.approx(290.0, rel=1e-2)  # 290 Wh feed-in after battery charges
    assert grid_draw == 0.0  # No grid draw
    assert losses == 10.0  # Battery charging losses
    assert self_consumption == 200.0  # All consumption is met
    mock_battery.energie_laden.assert_called_once_with(400.0, hour)


def test_process_energy_generation_equals_consumption(inverter, mock_battery):
    generation = 300.0
    consumption = 300.0
    hour = 12

    grid_feed_in, grid_draw, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_feed_in == 0.0  # No feed-in as generation equals consumption
    assert grid_draw == 0.0  # No grid draw
    assert losses == 0.0  # No losses
    assert self_consumption == 300.0  # All consumption is met with generation

    mock_battery.energie_laden.assert_called_once_with(0.0, hour)


def test_process_energy_battery_discharges(inverter, mock_battery):
    # Battery discharges 100 Wh with 10 Wh loss already accounted for in the discharge
    mock_battery.energie_abgeben.return_value = (100.0, 10.0)
    generation = 100.0
    consumption = 250.0
    hour = 12

    grid_feed_in, grid_draw, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_feed_in == 0.0  # No feed-in as generation is insufficient
    assert grid_draw == pytest.approx(
        50.0, rel=1e-2
    )  # Grid supplies remaining shortfall after battery discharge
    assert losses == 10.0  # Discharge losses
    assert self_consumption == 200.0  # Generation + battery discharge
    mock_battery.energie_abgeben.assert_called_once_with(150.0, hour)


def test_process_energy_battery_empty(inverter, mock_battery):
    # Battery is empty, so no energy can be discharged
    mock_battery.energie_abgeben.return_value = (0.0, 0.0)
    generation = 100.0
    consumption = 300.0
    hour = 12

    grid_feed_in, grid_draw, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_feed_in == 0.0  # No feed-in as generation is insufficient
    assert grid_draw == pytest.approx(200.0, rel=1e-2)  # Grid has to cover the full shortfall
    assert losses == 0.0  # No losses as the battery didn't discharge
    assert self_consumption == 100.0  # Only generation is consumed
    mock_battery.energie_abgeben.assert_called_once_with(200.0, hour)


def test_process_energy_battery_full_at_start(inverter, mock_battery):
    # Battery is full, so no charging happens
    mock_battery.energie_laden.return_value = (0.0, 0.0)
    generation = 500.0
    consumption = 200.0
    hour = 12

    grid_feed_in, grid_draw, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_feed_in == pytest.approx(
        300.0, rel=1e-2
    )  # All excess energy should be fed into the grid
    assert grid_draw == 0.0  # No grid draw
    assert losses == 0.0  # No losses
    assert self_consumption == 200.0  # Only consumption is met
    mock_battery.energie_laden.assert_called_once_with(300.0, hour)


def test_process_energy_insufficient_generation_no_battery(inverter, mock_battery):
    # Insufficient generation and no battery discharge
    mock_battery.energie_abgeben.return_value = (0.0, 0.0)
    generation = 100.0
    consumption = 500.0
    hour = 12

    grid_feed_in, grid_draw, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_feed_in == 0.0  # No feed-in as generation is insufficient
    assert grid_draw == pytest.approx(400.0, rel=1e-2)  # Grid supplies the shortfall
    assert losses == 0.0  # No losses
    assert self_consumption == 100.0  # Only generation is consumed
    mock_battery.energie_abgeben.assert_called_once_with(400.0, hour)


def test_process_energy_insufficient_generation_battery_assists(inverter, mock_battery):
    # Battery assists with some discharge to cover the shortfall
    mock_battery.energie_abgeben.return_value = (
        50.0,
        5.0,
    )  # Battery discharges 50 Wh with 5 Wh loss
    generation = 200.0
    consumption = 400.0
    hour = 12

    grid_feed_in, grid_draw, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_feed_in == 0.0  # No feed-in as generation is insufficient
    assert grid_draw == pytest.approx(
        150.0, rel=1e-2
    )  # Grid supplies the remaining shortfall after battery discharge
    assert losses == 5.0  # Discharge losses
    assert self_consumption == 250.0  # Generation + battery discharge
    mock_battery.energie_abgeben.assert_called_once_with(200.0, hour)


def test_process_energy_zero_generation(inverter, mock_battery):
    # Zero generation, full reliance on battery and grid
    mock_battery.energie_abgeben.return_value = (
        100.0,
        5.0,
    )  # Battery discharges 100 Wh with 5 Wh loss
    generation = 0.0
    consumption = 300.0
    hour = 12

    grid_feed_in, grid_draw, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_feed_in == 0.0  # No feed-in as there is zero generation
    assert grid_draw == pytest.approx(200.0, rel=1e-2)  # Grid supplies the remaining shortfall
    assert losses == 5.0  # Discharge losses
    assert self_consumption == 100.0  # Only battery discharge is consumed
    mock_battery.energie_abgeben.assert_called_once_with(300.0, hour)
