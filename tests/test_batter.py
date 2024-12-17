import pytest

from akkudoktoreos.devices.battery import Battery, BaseBatteryParameters


@pytest.fixture
def setup_pv_battery():
    params = BaseBatteryParameters(
        capacity_wh=10000, initial_soc_percent=50, min_soc_percent=20, max_soc_percent=80
    )
    battery = Battery(params, hours=24)
    battery.reset()
    return battery


def test_initial_state_of_charge(setup_pv_battery):
    battery = setup_pv_battery
    assert battery.current_soc_percentage() == 50.0, "Initial SoC should be 50%"


def test_battery_discharge_below_min_soc(setup_pv_battery):
    battery = setup_pv_battery
    discharged_wh, loss_wh = battery.discharge_energy(5000, 0)

    # Ensure it discharges energy and stops at the min SOC
    assert discharged_wh > 0
    assert battery.current_soc_percentage() >= 20  # Ensure it's above min_soc_percent
    assert loss_wh >= 0  # Losses should not be negative
    assert discharged_wh == 2640.0, "The energy discharged should be limited by min_soc"


def test_battery_charge_above_max_soc(setup_pv_battery):
    battery = setup_pv_battery
    charged_wh, loss_wh = battery.charge_energy(5000, 0)

    # Ensure it charges energy and stops at the max SOC
    assert charged_wh > 0
    assert battery.current_soc_percentage() <= 80  # Ensure it's below max_soc_percent
    assert loss_wh >= 0  # Losses should not be negative
    assert charged_wh == 3000.0, "The energy charged should be limited by max_soc"


def test_battery_charge_when_full(setup_pv_battery):
    battery = setup_pv_battery
    battery.soc_wh = battery.max_soc_wh  # Set battery to full
    charged_wh, loss_wh = battery.charge_energy(5000, 0)

    # No charging should happen if battery is full
    assert charged_wh == 0
    assert loss_wh == 0
    assert battery.current_soc_percentage() == 80, "SoC should remain at max_soc"


def test_battery_discharge_when_empty(setup_pv_battery):
    battery = setup_pv_battery
    battery.soc_wh = battery.min_soc_wh  # Set battery to minimum SOC
    discharged_wh, loss_wh = battery.discharge_energy(5000, 0)

    # No discharge should happen if battery is at min SOC
    assert discharged_wh == 0
    assert loss_wh == 0
    assert battery.current_soc_percentage() == 20, "SoC should remain at min_soc"


def test_battery_discharge_exactly_min_soc(setup_pv_battery):
    battery = setup_pv_battery
    battery.soc_wh = battery.min_soc_wh  # Set battery to exactly min SOC
    discharged_wh, loss_wh = battery.discharge_energy(1000, 0)

    # Battery should not go below the min SOC
    assert discharged_wh == 0
    assert battery.current_soc_percentage() == 20  # SOC should remain at min_SOC


def test_battery_charge_exactly_max_soc(setup_pv_battery):
    battery = setup_pv_battery
    battery.soc_wh = battery.max_soc_wh  # Set battery to exactly max SOC
    charged_wh, loss_wh = battery.charge_energy(1000, 0)

    # Battery should not exceed the max SOC
    assert charged_wh == 0
    assert battery.current_soc_percentage() == 80  # SOC should remain at max_SOC


def test_battery_reset_function(setup_pv_battery):
    battery = setup_pv_battery
    battery.soc_wh = 8000  # Change the SOC to some value
    battery.reset()

    # After reset, SOC should be equal to the initial value
    assert battery.soc_wh == battery._calculate_soc_wh(battery.initial_soc_percent)
    assert battery.current_soc_percentage() == battery.initial_soc_percent


def test_soc_limits(setup_pv_battery):
    battery = setup_pv_battery

    # Manually set SoC above max limit
    battery.soc_wh = battery.max_soc_wh + 1000
    battery.soc_wh = min(battery.soc_wh, battery.max_soc_wh)
    assert battery.current_soc_percentage() <= 80, "SoC should not exceed max_soc"

    # Manually set SoC below min limit
    battery.soc_wh = battery.min_soc_wh - 1000
    battery.soc_wh = max(battery.soc_wh, battery.min_soc_wh)
    assert battery.current_soc_percentage() >= 20, "SoC should not drop below min_soc"
