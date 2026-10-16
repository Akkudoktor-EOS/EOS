import unittest

import numpy as np

from akkudoktoreos.devices.genetic.battery import (
    Battery,
    ElectricVehicleParameters,
    SolarPanelBatteryParameters,
)

RATES = [0.0] + [amps / 16 for amps in range(6, 17)]


def battery(kind=ElectricVehicleParameters, soc=99, maximum=100):
    parameters = kind(
        device_id="ev-test", capacity_wh=60000,
        initial_soc_percentage=int(soc), charging_efficiency=0.95,
        discharging_efficiency=1.0, max_charge_power_w=11040,
        max_soc_percentage=maximum, charge_rates=RATES,
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
                value = battery(soc=soc)
                missing = 60000 - value.soc_wh
                stored, losses = value.charge_energy(None, 0, charge_factor=0.375)
                self.assertAlmostEqual(stored, missing)
                self.assertAlmostEqual(value.soc_wh, 60000)
                self.assertAlmostEqual(losses, missing / 0.95 - missing)
                self.assertEqual(value.charge_array[0], 0.375)
                self.assertLessEqual(stored + losses, 4140 * 0.25)

    def test_full_ev_has_no_charge_command_or_energy(self):
        value = battery(soc=100)
        self.assertEqual(value.charge_energy(None, 0, charge_factor=0.375), (0.0, 0.0))
        self.assertEqual(value.charge_array[0], 0)

    def test_complete_minimum_current_slot_preserves_watt_hours(self):
        value = battery(soc=80)
        stored, losses = value.charge_energy(None, 0, charge_factor=0.375)
        self.assertAlmostEqual(stored + losses, 4140 * 0.25)
        self.assertAlmostEqual(stored, 4140 * 0.25 * 0.95)

    def test_ev_respects_a_lower_configured_maximum(self):
        value = battery(soc=95.9, maximum=96)
        value.charge_energy(None, 0, charge_factor=0.375)
        self.assertAlmostEqual(value.soc_wh, 60000 * 0.96)

    def test_stationary_battery_keeps_discrete_rate_behavior(self):
        value = battery(SolarPanelBatteryParameters, soc=99)
        self.assertEqual(value.charge_energy(None, 0, charge_factor=0.375), (0.0, 0.0))
        self.assertEqual(value.soc_wh, 59400)
        self.assertEqual(value.charge_array[0], 0)

    def test_final_slot_preserves_each_configured_current(self):
        for amps in range(6, 17):
            with self.subTest(amps=amps):
                value = battery(soc=99.9)
                factor = amps / 16
                value.charge_array[0] = factor
                missing = value.max_soc_wh - value.soc_wh
                stored, losses = value.charge_energy(None, 0, charge_factor=factor)
                self.assertAlmostEqual(stored, missing)
                self.assertAlmostEqual(value.soc_wh, value.max_soc_wh)
                self.assertAlmostEqual(losses, missing / 0.95 - missing)
                self.assertEqual(value.charge_array[0], factor)


if __name__ == "__main__":
    unittest.main()
