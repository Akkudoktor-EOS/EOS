import unittest

import numpy as np
import pytest
from pydantic import ValidationError

from akkudoktoreos.devices.devices import BatteriesCommonSettings
from akkudoktoreos.devices.genetic.battery import (
    Battery,
    ElectricVehicleParameters,
    SolarPanelBatteryParameters,
)


@pytest.fixture
def setup_pv_battery():
    device_id="battery1"
    capacity_wh=10000
    initial_soc_percentage=50
    charging_efficiency=0.88
    discharging_efficiency=0.88
    min_soc_percentage=20
    max_soc_percentage=80
    max_charge_power_w=8000
    hours=24

    params = SolarPanelBatteryParameters(
        device_id=device_id,
        capacity_wh=capacity_wh,
        initial_soc_percentage=initial_soc_percentage,
        charging_efficiency=charging_efficiency,
        discharging_efficiency=discharging_efficiency,
        min_soc_percentage=min_soc_percentage,
        max_soc_percentage=max_soc_percentage,
        max_charge_power_w=max_charge_power_w,
        hours=hours,
    )
    battery = Battery(
        params,
        prediction_hours=48,
    )
    battery.reset()

    assert battery.parameters.device_id==device_id
    assert battery.capacity_wh==capacity_wh
    assert battery.initial_soc_percentage==initial_soc_percentage
    assert battery.charging_efficiency==charging_efficiency
    assert battery.initial_soc_percentage==initial_soc_percentage
    assert battery.discharging_efficiency==discharging_efficiency
    assert battery.max_soc_percentage==max_soc_percentage
    assert battery.max_charge_power_w==max_charge_power_w
    assert battery.soc_wh==float((initial_soc_percentage / 100) * capacity_wh)
    assert battery.min_soc_wh==float((min_soc_percentage / 100) * capacity_wh)
    assert battery.max_soc_wh==float((max_soc_percentage / 100) * capacity_wh)
    assert np.all(battery.charge_array == 0)
    assert np.all(battery.discharge_array == 0)

    # Init for test
    battery.charge_array = np.full(battery.prediction_hours, 1)
    battery.discharge_array = np.full(battery.prediction_hours, 1)
    assert np.all(battery.charge_array == 1)
    assert np.all(battery.discharge_array == 1)

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
    battery.set_charge_per_hour(np.zeros(battery.prediction_hours))

    charged_wh, losses_wh = battery.charge_energy(wh=4000, hour=3)

    assert charged_wh == 0, "No energy should be charged in disallowed hours"
    assert losses_wh == 0, "No losses should occur if charging is not allowed"
    assert (
        battery.soc_wh == (battery.parameters.initial_soc_percentage / 100) * battery.capacity_wh
    ), "SOC should remain unchanged"


@pytest.mark.parametrize(
    "wh, charge_factor, expected_raises",
    [
        (None, 0.5, False),  # Expected to work normally (if capacity allows)
        (None, 1.0, False),  # Often still OK, depending on fixture capacity
        (None, 2.0, False),   # Exceeds max charge → always ValueError
        (1000, 0, False),
        (1000, 1.0, True),
    ],
)
def test_charge_energy_with_charge_factor(setup_pv_battery, wh, charge_factor, expected_raises):
    battery = setup_pv_battery
    hour = 4

    if wh is not None and charge_factor == 0.0:  # mode 1
        raw_request_wh = wh
    else:
        raw_request_wh = battery.max_charge_power_w * charge_factor
    raw_capacity_wh = max(battery.max_soc_wh - battery.soc_wh, 0.0)

    if expected_raises:
        # Should raise
        with pytest.raises(ValueError):
            battery.charge_energy(
                wh=wh,
                hour=hour,
                charge_factor=charge_factor,
            )
        return

    # Should NOT raise
    charged_wh, losses_wh = battery.charge_energy(
        wh=wh,
        hour=hour,
        charge_factor=charge_factor,
    )

    # Expectations
    assert charged_wh > 0, "Charging should occur with charge factor"
    assert losses_wh >= 0, "Losses must not be negative"
    assert charged_wh <= raw_request_wh, "Charging must not exceed request"
    assert battery.soc_wh > 0, "SOC should increase after charging"


@pytest.fixture
def setup_car_battery():
    from akkudoktoreos.optimization.genetic.geneticparams import (
        ElectricVehicleParameters,
    )

    params = ElectricVehicleParameters(
        device_id="ev1",
        capacity_wh=40000,
        initial_soc_percentage=60,
        min_soc_percentage=10,
        max_soc_percentage=90,
        max_charge_power_w=7000,
        hours=24,
    )
    battery = Battery(
        params,
        prediction_hours=48,
    )
    battery.reset()

    # Init for test
    battery.charge_array = np.full(battery.prediction_hours, 1)
    battery.discharge_array = np.full(battery.prediction_hours, 1)
    assert np.all(battery.charge_array == 1)
    assert np.all(battery.discharge_array == 1)

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


def test_quarter_hour_charge_calls_share_one_power_budget():
    params = SolarPanelBatteryParameters(
        device_id="battery1",
        capacity_wh=10_000,
        initial_soc_percentage=0,
        min_soc_percentage=0,
        max_soc_percentage=100,
        max_charge_power_w=1_000,
        charging_efficiency=1.0,
        discharging_efficiency=1.0,
    )
    battery = Battery(params, prediction_hours=4, slot_duration_h=0.25)
    battery.set_charge_per_hour(np.ones(4))

    first_stored, _ = battery.charge_energy(200.0, 0)
    second_stored, _ = battery.charge_energy(200.0, 0)

    assert first_stored == pytest.approx(200.0)
    assert second_stored == pytest.approx(50.0)
    assert battery.soc_wh == pytest.approx(250.0)


def test_quarter_hour_discharge_calls_share_one_power_budget():
    params = SolarPanelBatteryParameters(
        device_id="battery1",
        capacity_wh=10_000,
        initial_soc_percentage=100,
        min_soc_percentage=0,
        max_soc_percentage=100,
        max_charge_power_w=1_000,
        charging_efficiency=1.0,
        discharging_efficiency=1.0,
    )
    battery = Battery(params, prediction_hours=4, slot_duration_h=0.25)
    battery.set_discharge_per_hour(np.ones(4))

    first_delivered, _ = battery.discharge_energy(200.0, 0)
    second_delivered, _ = battery.discharge_energy(200.0, 0)

    assert first_delivered == pytest.approx(200.0)
    assert second_delivered == pytest.approx(50.0)
    assert battery.discharged_energy_wh(0) == pytest.approx(250.0)
    assert battery.soc_wh == pytest.approx(9_750.0)

    battery.reset()

    assert battery.discharged_energy_wh(0) == 0.0


def test_grid_export_rates_are_sorted_and_deduplicated():
    """Export rates are normalized like the charge rates."""
    settings = BatteriesCommonSettings(
        device_id="battery1", grid_export_rates=[1.0, 0.5, 0.5, 0.25]
    )
    assert settings.grid_export_rates == [0.25, 0.5, 1.0]


def test_grid_export_rates_default_and_override():
    """None falls back to the defaults; [1.0] restores all-or-nothing export."""
    assert BatteriesCommonSettings(device_id="battery1").grid_export_rates == [
        0.25,
        0.5,
        0.75,
        1.0,
    ]
    fallback = BatteriesCommonSettings(device_id="battery1", grid_export_rates=None)
    assert fallback.grid_export_rates == [0.25, 0.5, 0.75, 1.0]
    full_export = BatteriesCommonSettings(device_id="battery1", grid_export_rates=[1.0])
    assert full_export.grid_export_rates == [1.0]


@pytest.mark.parametrize("rates", [[0.0, 0.5], [1.5], [-0.25], [], [np.nan], [np.inf], [[0.5]], 0.5])
def test_grid_export_rates_reject_invalid_values(rates):
    """0.0 is not an export level, and rates above the rated power are rejected."""
    with pytest.raises(ValidationError):
        BatteriesCommonSettings(device_id="battery1", grid_export_rates=rates)


def test_rated_discharge_energy_scales_with_slot_duration(setup_pv_battery):
    """The rate reference is the rated discharge energy of one slot."""
    battery = setup_pv_battery
    expected = battery.max_charge_power_w * battery.slot_duration_h * battery.discharging_efficiency
    assert battery.rated_discharge_energy_wh() == pytest.approx(expected)


def test_pv_converter_preserves_id_lcos_charge_and_export_rates():
    settings = BatteriesCommonSettings(
        device_id="house",
        charge_rates=[0.0, 0.5, 1.0],
        grid_export_rates=[1.0, 0.25],
        levelized_cost_of_storage_amt_kwh=0.123,
    )
    assert isinstance(BatteriesCommonSettings.validate_and_sort_charge_rates(None), np.ndarray)
    parameters = settings.to_genetic_pv_bat_param()
    assert parameters.device_id == "house"
    assert parameters.charge_rates == [0.0, 0.5, 1.0]
    assert parameters.grid_export_rates == [0.25, 1.0]
    assert parameters.levelized_cost_of_storage_kwh == pytest.approx(0.123)
    battery = Battery(parameters, prediction_hours=4, slot_duration_h=0.25)
    assert battery.levelized_cost_of_storage_kwh == pytest.approx(0.123)


@pytest.mark.parametrize("target", [0, 50, 100])
def test_equal_soc_limits_are_valid(target):
    settings = BatteriesCommonSettings(device_id="ev", min_soc_percentage=target,
                                      max_soc_percentage=target)
    assert settings.to_genetic_ev_bat_param().min_soc_percentage == target
    assert settings.to_genetic0_ev_bat_param().min_soc_percentage == target


@pytest.mark.parametrize("minimum,maximum", [(100, 99), (81, 80), (101, 100), (-1, 100)])
def test_inverted_or_out_of_range_limits_still_fail(minimum, maximum):
    with pytest.raises(ValidationError):
        BatteriesCommonSettings(device_id="ev", min_soc_percentage=minimum,
                                max_soc_percentage=maximum)


def test_full_target_charges_to_capacity_without_overcharge():
    settings = BatteriesCommonSettings(device_id="ev", capacity_wh=1000,
        min_soc_percentage=100, max_soc_percentage=100, max_charge_power_w=1000,
        charging_efficiency=1)
    params = settings.to_genetic_ev_bat_param()
    params.initial_soc_percentage = 70
    battery = Battery(params, prediction_hours=4, slot_duration_h=0.25)
    battery.charge_array = np.ones(4)
    for slot in range(4):
        battery.charge_energy(wh=1000, hour=slot)
        assert battery.current_soc_percentage() <= 100
    assert battery.current_soc_percentage() == pytest.approx(100)
    assert battery.charge_energy(wh=1000, hour=3) == (0, 0)


_EV_CHARGE_RATES = [0.0] + [amps / 16 for amps in range(6, 17)]


def _minimum_current_battery(kind=ElectricVehicleParameters, soc=99, maximum=100):
    parameters = kind(
        device_id="ev-test", capacity_wh=60000,
        initial_soc_percentage=int(soc), charging_efficiency=0.95,
        discharging_efficiency=1.0, max_charge_power_w=11040,
        max_soc_percentage=maximum, charge_rates=_EV_CHARGE_RATES,
    )
    value = Battery(parameters, prediction_hours=4, slot_duration_h=0.25)
    value.reset()
    value.soc_wh = soc / 100 * value.capacity_wh
    value.charge_array = np.full(4, 0.375)
    return value


class TestEVMinimumCurrent(unittest.TestCase):
    def test_ev_finishes_within_final_slot_at_unchanged_minimum_current(self):
        for soc in [98.5, 99.0, 99.9]:
            with self.subTest(soc=soc):
                value = _minimum_current_battery(soc=soc)
                missing = 60000 - value.soc_wh
                stored, losses = value.charge_energy(None, 0, charge_factor=0.375)
                self.assertAlmostEqual(stored, missing)
                self.assertAlmostEqual(value.soc_wh, 60000)
                self.assertAlmostEqual(losses, missing / 0.95 - missing)
                self.assertEqual(value.charge_array[0], 0.375)
                self.assertLessEqual(stored + losses, 4140 * 0.25)

    def test_full_ev_has_no_charge_command_or_energy(self):
        value = _minimum_current_battery(soc=100)
        self.assertEqual(value.charge_energy(None, 0, charge_factor=0.375), (0.0, 0.0))
        self.assertEqual(value.charge_array[0], 0)

    def test_complete_minimum_current_slot_preserves_watt_hours(self):
        value = _minimum_current_battery(soc=80)
        stored, losses = value.charge_energy(None, 0, charge_factor=0.375)
        self.assertAlmostEqual(stored + losses, 4140 * 0.25)
        self.assertAlmostEqual(stored, 4140 * 0.25 * 0.95)

    def test_ev_respects_a_lower_configured_maximum(self):
        value = _minimum_current_battery(soc=95.9, maximum=96)
        value.charge_energy(None, 0, charge_factor=0.375)
        self.assertAlmostEqual(value.soc_wh, 60000 * 0.96)

    def test_stationary_battery_keeps_discrete_rate_behavior(self):
        value = _minimum_current_battery(SolarPanelBatteryParameters, soc=99)
        self.assertEqual(value.charge_energy(None, 0, charge_factor=0.375), (0.0, 0.0))
        self.assertEqual(value.soc_wh, 59400)
        self.assertEqual(value.charge_array[0], 0)

    def test_final_slot_preserves_each_configured_current(self):
        for amps in range(6, 17):
            with self.subTest(amps=amps):
                value = _minimum_current_battery(soc=99.9)
                factor = amps / 16
                value.charge_array[0] = factor
                missing = value.max_soc_wh - value.soc_wh
                stored, losses = value.charge_energy(None, 0, charge_factor=factor)
                self.assertAlmostEqual(stored, missing)
                self.assertAlmostEqual(value.soc_wh, value.max_soc_wh)
                self.assertAlmostEqual(losses, missing / 0.95 - missing)
                self.assertEqual(value.charge_array[0], factor)
