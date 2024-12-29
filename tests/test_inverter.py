from unittest.mock import Mock

import pytest

from akkudoktoreos.devices.inverter import Inverter, InverterParameters


@pytest.fixture
def mock_battery():
    mock_battery = Mock()
    mock_battery.charge_energy = Mock(return_value=(0.0, 0.0))
    mock_battery.discharge_energy = Mock(return_value=(0.0, 0.0))
    return mock_battery


@pytest.fixture
def inverter(mock_battery):
    mock_self_consumption_predictor = Mock()
    mock_self_consumption_predictor.calculate_self_consumption.return_value = 1.0
    return Inverter(
        mock_self_consumption_predictor,
        InverterParameters(max_power_wh=500.0),
        battery=mock_battery,
    )


def test_process_energy_excess_generation(inverter, mock_battery):
    # Battery charges 100 Wh with 10 Wh loss
    mock_battery.charge_energy.return_value = (100.0, 10.0)
    generation = 600.0
    consumption = 200.0
    hour = 12

    grid_export, grid_import, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_export == pytest.approx(290.0, rel=1e-2)  # 290 Wh feed-in after battery charges
    assert grid_import == 0.0  # No grid draw
    assert losses == 10.0  # Battery charging losses
    assert self_consumption == 200.0  # All consumption is met
    mock_battery.charge_energy.assert_called_once_with(400.0, hour)
    mock_battery.discharge_energy.assert_not_called()
    inverter.self_consumption_predictor.calculate_self_consumption.assert_called_once_with(
        consumption, generation
    )


def test_process_energy_excess_generation_interpolator(inverter, mock_battery):
    # Battery charges 100 Wh with 10 Wh loss
    mock_battery.charge_energy.return_value = (100.0, 10.0)
    mock_battery.discharge_energy.return_value = (20.0, 2.0)
    inverter.self_consumption_predictor.calculate_self_consumption.return_value = 0.95

    generation = 600.0
    consumption = 200.0
    hour = 12

    grid_export, grid_import, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_export == pytest.approx(
        270.0, rel=1e-2
    )  # 290 Wh feed-in - 5% of generation-consumption self consumption after battery charges
    assert grid_import == pytest.approx(0.0, rel=1e-2)  # No grid draw
    assert losses == 12.0  # Battery charging losses
    assert self_consumption == 220.0  # All consumption is met
    mock_battery.charge_energy.assert_called_once_with(pytest.approx(380.0, rel=1e-2), hour)
    mock_battery.discharge_energy.assert_called_once_with(pytest.approx(20.0, rel=1e-2), hour)
    inverter.self_consumption_predictor.calculate_self_consumption.assert_called_once_with(
        consumption, generation
    )


def test_process_energy_generation_equals_consumption(inverter, mock_battery):
    generation = 300.0
    consumption = 300.0
    hour = 12

    grid_export, grid_import, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_export == 0.0  # No feed-in as generation equals consumption
    assert grid_import == 0.0  # No grid draw
    assert losses == 0.0  # No losses
    assert self_consumption == 300.0  # All consumption is met with generation

    mock_battery.charge_energy.assert_not_called()
    mock_battery.discharge_energy.assert_not_called()
    inverter.self_consumption_predictor.calculate_self_consumption.assert_called_once_with(
        consumption, generation
    )


def test_process_energy_battery_discharges(inverter, mock_battery):
    # Battery discharges 100 Wh with 10 Wh loss already accounted for in the discharge
    mock_battery.discharge_energy.return_value = (100.0, 10.0)
    generation = 100.0
    consumption = 250.0
    hour = 12

    grid_export, grid_import, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_export == 0.0  # No feed-in as generation is insufficient
    assert grid_import == pytest.approx(
        50.0, rel=1e-2
    )  # Grid supplies remaining shortfall after battery discharge
    assert losses == 10.0  # Discharge losses
    assert self_consumption == 200.0  # Generation + battery discharge
    mock_battery.charge_energy.assert_not_called()
    mock_battery.discharge_energy.assert_called_once_with(150.0, hour)
    inverter.self_consumption_predictor.calculate_self_consumption.assert_not_called()


def test_process_energy_battery_empty(inverter, mock_battery):
    # Battery is empty, so no energy can be discharged
    mock_battery.discharge_energy.return_value = (0.0, 0.0)
    generation = 100.0
    consumption = 300.0
    hour = 12

    grid_export, grid_import, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_export == 0.0  # No feed-in as generation is insufficient
    assert grid_import == pytest.approx(200.0, rel=1e-2)  # Grid has to cover the full shortfall
    assert losses == 0.0  # No losses as the battery didn't discharge
    assert self_consumption == 100.0  # Only generation is consumed
    mock_battery.charge_energy.assert_not_called()
    mock_battery.discharge_energy.assert_called_once_with(200.0, hour)
    inverter.self_consumption_predictor.calculate_self_consumption.assert_not_called()


def test_process_energy_battery_full_at_start(inverter, mock_battery):
    # Battery is full, so no charging happens
    mock_battery.charge_energy.return_value = (0.0, 0.0)
    generation = 500.0
    consumption = 200.0
    hour = 12

    grid_export, grid_import, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_export == pytest.approx(
        300.0, rel=1e-2
    )  # All excess energy should be fed into the grid
    assert grid_import == 0.0  # No grid draw
    assert losses == 0.0  # No losses
    assert self_consumption == 200.0  # Only consumption is met
    mock_battery.charge_energy.assert_called_once_with(300.0, hour)
    mock_battery.discharge_energy.assert_not_called()
    inverter.self_consumption_predictor.calculate_self_consumption.assert_called_once_with(
        consumption, generation
    )


def test_process_energy_insufficient_generation_no_battery(inverter, mock_battery):
    # Insufficient generation and no battery discharge
    mock_battery.discharge_energy.return_value = (0.0, 0.0)
    generation = 100.0
    consumption = 500.0
    hour = 12

    grid_export, grid_import, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_export == 0.0  # No feed-in as generation is insufficient
    assert grid_import == pytest.approx(400.0, rel=1e-2)  # Grid supplies the shortfall
    assert losses == 0.0  # No losses
    assert self_consumption == 100.0  # Only generation is consumed
    mock_battery.charge_energy.assert_not_called()
    mock_battery.discharge_energy.assert_called_once_with(400.0, hour)
    inverter.self_consumption_predictor.calculate_self_consumption.assert_not_called()


def test_process_energy_insufficient_generation_battery_assists(inverter, mock_battery):
    # Battery assists with some discharge to cover the shortfall
    mock_battery.discharge_energy.return_value = (
        50.0,
        5.0,
    )  # Battery discharges 50 Wh with 5 Wh loss
    generation = 200.0
    consumption = 400.0
    hour = 12

    grid_export, grid_import, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_export == 0.0  # No feed-in as generation is insufficient
    assert grid_import == pytest.approx(
        150.0, rel=1e-2
    )  # Grid supplies the remaining shortfall after battery discharge
    assert losses == 5.0  # Discharge losses
    assert self_consumption == 250.0  # Generation + battery discharge
    mock_battery.charge_energy.assert_not_called()
    mock_battery.discharge_energy.assert_called_once_with(200.0, hour)
    inverter.self_consumption_predictor.calculate_self_consumption.assert_not_called()


def test_process_energy_zero_generation(inverter, mock_battery):
    # Zero generation, full reliance on battery and grid
    mock_battery.discharge_energy.return_value = (
        100.0,
        5.0,
    )  # Battery discharges 100 Wh with 5 Wh loss
    generation = 0.0
    consumption = 300.0
    hour = 12

    grid_export, grid_import, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_export == 0.0  # No feed-in as there is zero generation
    assert grid_import == pytest.approx(200.0, rel=1e-2)  # Grid supplies the remaining shortfall
    assert losses == 5.0  # Discharge losses
    assert self_consumption == 100.0  # Only battery discharge is consumed
    mock_battery.charge_energy.assert_not_called()
    mock_battery.discharge_energy.assert_called_once_with(300.0, hour)
    inverter.self_consumption_predictor.calculate_self_consumption.assert_not_called()


def test_process_energy_zero_consumption(inverter, mock_battery):
    # Generation exceeds consumption, but consumption is zero
    mock_battery.charge_energy.return_value = (100.0, 10.0)
    generation = 500.0
    consumption = 0.0
    hour = 12

    grid_export, grid_import, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_export == pytest.approx(390.0, rel=1e-2)  # Excess energy after battery charges
    assert grid_import == 0.0  # No grid draw as no consumption
    assert losses == 10.0  # Charging losses
    assert self_consumption == 0.0  # Zero consumption
    mock_battery.charge_energy.assert_called_once_with(500.0, hour)
    mock_battery.discharge_energy.assert_not_called()
    inverter.self_consumption_predictor.calculate_self_consumption.assert_called_once_with(
        consumption, generation
    )


def test_process_energy_zero_generation_zero_consumption(inverter, mock_battery):
    generation = 0.0
    consumption = 0.0
    hour = 12

    grid_export, grid_import, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_export == 0.0  # No feed-in
    assert grid_import == 0.0  # No grid draw
    assert losses == 0.0  # No losses
    assert self_consumption == 0.0  # No consumption
    mock_battery.charge_energy.assert_not_called()
    mock_battery.discharge_energy.assert_not_called()
    inverter.self_consumption_predictor.calculate_self_consumption.assert_called_once_with(
        consumption, generation
    )


def test_process_energy_partial_battery_discharge(inverter, mock_battery):
    mock_battery.discharge_energy.return_value = (50.0, 5.0)
    generation = 200.0
    consumption = 400.0
    hour = 12

    grid_export, grid_import, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_export == 0.0  # No feed-in due to insufficient generation
    assert grid_import == pytest.approx(
        150.0, rel=1e-2
    )  # Grid supplies the shortfall after battery assist
    assert losses == 5.0  # Discharge losses
    assert self_consumption == 250.0  # Generation + battery discharge
    mock_battery.charge_energy.assert_not_called()
    mock_battery.discharge_energy.assert_called_once_with(200.0, 12)
    inverter.self_consumption_predictor.calculate_self_consumption.assert_not_called()


def test_process_energy_consumption_exceeds_max_no_battery(inverter, mock_battery):
    # Battery is empty, and consumption is much higher than the inverter's max power
    mock_battery.discharge_energy.return_value = (0.0, 0.0)
    generation = 100.0
    consumption = 1000.0  # Exceeds the inverter's max power
    hour = 12

    grid_export, grid_import, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_export == 0.0  # No feed-in
    assert grid_import == pytest.approx(900.0, rel=1e-2)  # Grid covers the remaining shortfall
    assert losses == 0.0  # No losses as the battery didn’t assist
    assert self_consumption == 100.0  # Only the generation is consumed, maxing out the inverter
    mock_battery.charge_energy.assert_not_called()
    mock_battery.discharge_energy.assert_called_once_with(400.0, hour)
    inverter.self_consumption_predictor.calculate_self_consumption.assert_not_called()


def test_process_energy_zero_generation_full_battery_high_consumption(inverter, mock_battery):
    # Full battery, no generation, and high consumption
    mock_battery.discharge_energy.return_value = (500.0, 10.0)
    generation = 0.0
    consumption = 600.0
    hour = 12

    grid_export, grid_import, losses, self_consumption = inverter.process_energy(
        generation, consumption, hour
    )

    assert grid_export == 0.0  # No feed-in due to zero generation
    assert grid_import == pytest.approx(
        100.0, rel=1e-2
    )  # Grid covers remaining shortfall after battery discharge
    assert losses == 10.0  # Battery discharge losses
    assert self_consumption == 500.0  # Battery fully discharges to meet consumption
    mock_battery.charge_energy.assert_not_called()
    mock_battery.discharge_energy.assert_called_once_with(500.0, hour)
    inverter.self_consumption_predictor.calculate_self_consumption.assert_not_called()
