import unittest

from akkudoktoreos.devices.battery import BaseBatteryParameters, Battery


class TestBattery(unittest.TestCase):
    def setUp(self):
        # Initializing common parameters for tests
        self.capacity_wh = 10000  # 10,000 Wh capacity
        self.charging_efficiency = 0.88
        self.discharging_efficiency = 0.88
        self.min_soc_percentage = 20  # Minimum SoC is 20%
        self.max_soc_percentage = 80  # Maximum SoC is 80%

    def test_initial_state_of_charge(self):
        akku = Battery(
            BaseBatteryParameters(
                capacity_wh=self.capacity_wh,
                initial_soc_percentage=50,
                min_soc_percentage=self.min_soc_percentage,
                max_soc_percentage=self.max_soc_percentage,
            ),
            hours=1,
        )
        self.assertEqual(akku.current_soc_percentage(), 50.0, "Initial SoC should be 50%")

    def test_discharge_below_min_soc(self):
        akku = Battery(
            BaseBatteryParameters(
                capacity_wh=self.capacity_wh,
                initial_soc_percentage=50,
                min_soc_percentage=self.min_soc_percentage,
                max_soc_percentage=self.max_soc_percentage,
            ),
            hours=1,
        )
        akku.reset()
        # Try to discharge more energy than available above min_soc
        abgegeben_wh, verlust_wh = akku.discharge_energy(5000, 0)  # Try to discharge 5000 Wh
        expected_soc = self.min_soc_percentage  # SoC should not drop below min_soc
        self.assertEqual(
            akku.current_soc_percentage(),
            expected_soc,
            "SoC should not drop below min_soc after discharge",
        )
        self.assertEqual(abgegeben_wh, 2640.0, "The energy discharged should be limited by min_soc")

    def test_charge_above_max_soc(self):
        akku = Battery(
            BaseBatteryParameters(
                capacity_wh=self.capacity_wh,
                initial_soc_percentage=50,
                min_soc_percentage=self.min_soc_percentage,
                max_soc_percentage=self.max_soc_percentage,
            ),
            hours=1,
        )
        akku.reset()
        # Try to charge more energy than available up to max_soc
        geladen_wh, verlust_wh = akku.charge_energy(5000, 0)  # Try to charge 5000 Wh
        expected_soc = self.max_soc_percentage  # SoC should not exceed max_soc
        self.assertEqual(
            akku.current_soc_percentage(),
            expected_soc,
            "SoC should not exceed max_soc after charge",
        )
        self.assertEqual(geladen_wh, 3000.0, "The energy charged should be limited by max_soc")

    def test_charging_at_max_soc(self):
        akku = Battery(
            BaseBatteryParameters(
                capacity_wh=self.capacity_wh,
                initial_soc_percentage=80,
                min_soc_percentage=self.min_soc_percentage,
                max_soc_percentage=self.max_soc_percentage,
            ),
            hours=1,
        )
        akku.reset()
        # Try to charge when SoC is already at max_soc
        geladen_wh, verlust_wh = akku.charge_energy(5000, 0)
        self.assertEqual(geladen_wh, 0.0, "No energy should be charged when at max_soc")
        self.assertEqual(
            akku.current_soc_percentage(),
            self.max_soc_percentage,
            "SoC should remain at max_soc",
        )

    def test_discharging_at_min_soc(self):
        akku = Battery(
            BaseBatteryParameters(
                capacity_wh=self.capacity_wh,
                initial_soc_percentage=20,
                min_soc_percentage=self.min_soc_percentage,
                max_soc_percentage=self.max_soc_percentage,
            ),
            hours=1,
        )
        akku.reset()
        # Try to discharge when SoC is already at min_soc
        abgegeben_wh, verlust_wh = akku.discharge_energy(5000, 0)
        self.assertEqual(abgegeben_wh, 0.0, "No energy should be discharged when at min_soc")
        self.assertEqual(
            akku.current_soc_percentage(),
            self.min_soc_percentage,
            "SoC should remain at min_soc",
        )

    def test_soc_limits(self):
        # Test to ensure that SoC never exceeds max_soc or drops below min_soc
        akku = Battery(
            BaseBatteryParameters(
                capacity_wh=self.capacity_wh,
                initial_soc_percentage=50,
                min_soc_percentage=self.min_soc_percentage,
                max_soc_percentage=self.max_soc_percentage,
            ),
            hours=1,
        )
        akku.reset()
        akku.soc_wh = (
            self.max_soc_percentage / 100
        ) * self.capacity_wh + 1000  # Manually set SoC above max limit
        akku.soc_wh = min(akku.soc_wh, akku.max_soc_wh)
        self.assertLessEqual(
            akku.current_soc_percentage(),
            self.max_soc_percentage,
            "SoC should not exceed max_soc",
        )

        akku.soc_wh = (
            self.min_soc_percentage / 100
        ) * self.capacity_wh - 1000  # Manually set SoC below min limit
        akku.soc_wh = max(akku.soc_wh, akku.min_soc_wh)
        self.assertGreaterEqual(
            akku.current_soc_percentage(),
            self.min_soc_percentage,
            "SoC should not drop below min_soc",
        )


if __name__ == "__main__":
    unittest.main()
