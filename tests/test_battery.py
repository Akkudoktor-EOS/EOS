import numpy as np
import pytest

from akkudoktoreos.devices.battery import Battery, SolarPanelBatteryParameters


@pytest.fixture
def setup_pv_battery():
    params = SolarPanelBatteryParameters(
        device_id="battery1",
        capacity_wh=10000,
        initial_soc_percentage=50,
        min_soc_percentage=20,
        max_soc_percentage=80,
        max_charge_power_w=8000,
        hours=24,
    )
    battery = Battery(params)
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
    print(discharged_wh, loss_wh, battery.current_soc_percentage(), battery.min_soc_percentage)
    assert battery.current_soc_percentage() >= 20  # Ensure it's above min_soc_percentage
    assert loss_wh >= 0  # Losses should not be negative
    assert discharged_wh == 2640.0, "The energy discharged should be limited by min_soc"


def test_battery_charge_above_max_soc(setup_pv_battery):
    battery = setup_pv_battery
    charged_wh, loss_wh = battery.charge_energy(5000, 0)

    # Ensure it charges energy and stops at the max SOC
    assert charged_wh > 0
    assert battery.current_soc_percentage() <= 80  # Ensure it's below max_soc_percentage
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
    assert battery.current_soc_percentage() == battery.initial_soc_percentage


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


def test_max_charge_power_w(setup_pv_battery):
    battery = setup_pv_battery
    assert battery.parameters.max_charge_power_w == 8000, (
        "Default max charge power should be 5000W, We ask for 8000W here"
    )


def test_charge_energy_within_limits(setup_pv_battery):
    battery = setup_pv_battery
    initial_soc_wh = battery.soc_wh

    charged_wh, losses_wh = battery.charge_energy(wh=4000, hour=1)

    assert charged_wh > 0, "Charging should add energy"
    assert losses_wh >= 0, "Losses should not be negative"
    assert battery.soc_wh > initial_soc_wh, "State of charge should increase after charging"
    assert battery.soc_wh <= battery.max_soc_wh, "SOC should not exceed max SOC"


def test_charge_energy_exceeds_capacity(setup_pv_battery):
    battery = setup_pv_battery
    initial_soc_wh = battery.soc_wh

    # Try to overcharge beyond max capacity
    charged_wh, losses_wh = battery.charge_energy(wh=20000, hour=2)

    assert charged_wh + initial_soc_wh <= battery.max_soc_wh, (
        "Charging should not exceed max capacity"
    )
    assert losses_wh >= 0, "Losses should not be negative"
    assert battery.soc_wh == battery.max_soc_wh, "SOC should be at max after overcharge attempt"


def test_charge_energy_not_allowed_hour(setup_pv_battery):
    battery = setup_pv_battery

    # Disable charging for all hours
    battery.set_charge_per_hour(np.zeros(battery.hours))

    charged_wh, losses_wh = battery.charge_energy(wh=4000, hour=3)

    assert charged_wh == 0, "No energy should be charged in disallowed hours"
    assert losses_wh == 0, "No losses should occur if charging is not allowed"
    assert (
        battery.soc_wh == (battery.parameters.initial_soc_percentage / 100) * battery.capacity_wh
    ), "SOC should remain unchanged"


def test_charge_energy_relative_power(setup_pv_battery):
    battery = setup_pv_battery

    relative_power = 0.5  # 50% of max charge power
    charged_wh, losses_wh = battery.charge_energy(wh=None, hour=4, relative_power=relative_power)

    assert charged_wh > 0, "Charging should occur with relative power"
    assert losses_wh >= 0, "Losses should not be negative"
    assert charged_wh <= battery.max_charge_power_w * relative_power, (
        "Charging should respect relative power limit"
    )
    assert battery.soc_wh > 0, "SOC should increase after charging"


@pytest.fixture
def setup_car_battery():
    from akkudoktoreos.devices.battery import ElectricVehicleParameters

    params = ElectricVehicleParameters(
        device_id="ev1",
        capacity_wh=40000,
        initial_soc_percentage=60,
        min_soc_percentage=10,
        max_soc_percentage=90,
        max_charge_power_w=7000,
        hours=24,
    )
    battery = Battery(params)
    battery.reset()
    return battery


def test_car_and_pv_battery_discharge_and_max_charge_power(setup_pv_battery, setup_car_battery):
    pv_battery = setup_pv_battery
    car_battery = setup_car_battery

    # Test discharge for PV battery
    pv_discharged_wh, pv_loss_wh = pv_battery.discharge_energy(3000, 5)
    assert pv_discharged_wh > 0, "PV battery should discharge energy"
    assert pv_battery.current_soc_percentage() >= pv_battery.parameters.min_soc_percentage, (
        "PV battery SOC should stay above min SOC"
    )
    assert pv_battery.parameters.max_charge_power_w == 8000, (
        "PV battery max charge power should remain as defined"
    )

    # Test discharge for car battery
    car_discharged_wh, car_loss_wh = car_battery.discharge_energy(5000, 10)
    assert car_discharged_wh > 0, "Car battery should discharge energy"
    assert car_battery.current_soc_percentage() >= car_battery.parameters.min_soc_percentage, (
        "Car battery SOC should stay above min SOC"
    )
    assert car_battery.parameters.max_charge_power_w == 7000, (
        "Car battery max charge power should remain as defined"
    )
