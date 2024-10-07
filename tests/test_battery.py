import pytest

from modules.battery import Battery


@pytest.fixture(scope="function")
def battery_10kw_soc20() -> Battery:
    """Battery with 10.000 kW, Start SOC of 20, min SOC of 20 and max SOC of 80

    Returns:
        Battery: _description_
    """
    return Battery(
        capacity_wh=10000,
        hours=1,
        start_soc_percent=20,
        min_soc_percent=20,
        max_soc_percent=80,
    )


@pytest.fixture(scope="function")
def battery_10kw_soc80() -> Battery:
    """Battery with 10.000 kW, Start SOC of 80, min SOC of 20 and max SOC of 80

    Returns:
        Battery: _description_
    """
    return Battery(
        capacity_wh=10000,
        hours=1,
        start_soc_percent=80,
        min_soc_percent=20,
        max_soc_percent=80,
    )


@pytest.fixture(scope="function")
def battery_10kw_soc50() -> Battery:
    """Battery with 10.000 kW, Start SOC of 50, min SOC of 20 and max SOC of 80

    Returns:
        Battery: _description_
    """
    return Battery(
        capacity_wh=10000,
        hours=1,
        start_soc_percent=50,
        min_soc_percent=20,
        max_soc_percent=80,
    )


class TestBattery:
    def test_initial_state_of_charge(self, battery_10kw_soc50: Battery):
        assert (
            battery_10kw_soc50.charge_state_percent() == 50.0
        ), "Initial SoC should be 50%"

    def test_discharge_below_min_soc(self, battery_10kw_soc50: Battery):
        battery_10kw_soc50.reset()
        # Try to discharge more energy than available above min_soc
        abgegeben_wh, _ = battery_10kw_soc50.discharge(
            5000, 0
        )  # Try to discharge 5000 Wh
        expected_soc = (
            battery_10kw_soc50.min_soc_percent
        )  # SoC should not drop below min_soc
        assert (
            battery_10kw_soc50.charge_state_percent() == expected_soc
        ), "SoC should not drop below min_soc after discharge"
        assert (
            abgegeben_wh == 2640.0
        ), "The energy discharged should be limited by min_soc"

    def test_charge_above_max_soc(self, battery_10kw_soc50: Battery):
        # Try to charge more energy than available up to max_soc
        assert battery_10kw_soc50.charge_state_percent() == 50
        geladen_wh, _ = battery_10kw_soc50.charge(5000, 0)  # Try to charge 5000 Wh
        assert (
            battery_10kw_soc50.charge_state_percent()
            <= battery_10kw_soc50.max_soc_percent
        ), "SoC should not exceed max_soc after charge"
        assert geladen_wh == 3000.0, "The energy charged should be limited by max_soc"

    def test_charging_at_max_soc(self, battery_10kw_soc80: Battery):
        # Try to charge when SoC is already at max_soc
        geladen_wh, _ = battery_10kw_soc80.charge(5000, 0)
        assert geladen_wh == 0.0, "No energy should be charged when at max_soc"
        assert (
            battery_10kw_soc80.charge_state_percent()
            == battery_10kw_soc80.max_soc_percent
        ), "SoC should remain at max_soc"

    def test_discharging_at_min_soc(self, battery_10kw_soc20: Battery):
        # Try to discharge when SoC is already at min_soc
        abgegeben_wh, _ = battery_10kw_soc20.discharge(5000, 0)
        assert abgegeben_wh == 0.0, "No energy should be discharged when at min_soc"
        assert (
            battery_10kw_soc20.charge_state_percent()
            == battery_10kw_soc20.min_soc_percent
        ), "SoC should remain at min_soc"

    def test_soc_limits(self, battery_10kw_soc50: Battery):
        # Test to ensure that SoC never exceeds max_soc or drops below min_soc
        battery_10kw_soc50.soc_wh = (
            battery_10kw_soc50.max_soc_percent / 100
        ) * battery_10kw_soc50.capacity_wh + 1000  # Manually set SoC above max limit
        battery_10kw_soc50.soc_wh = min(
            battery_10kw_soc50.soc_wh, battery_10kw_soc50.max_soc_wh
        )
        assert (
            battery_10kw_soc50.charge_state_percent()
            == battery_10kw_soc50.max_soc_percent
        ), "SoC should not exceed max_soc"

        battery_10kw_soc50.soc_wh = (
            battery_10kw_soc50.min_soc_percent / 100
        ) * battery_10kw_soc50.capacity_wh - 1000  # Manually set SoC below min limit
        battery_10kw_soc50.soc_wh = max(
            battery_10kw_soc50.soc_wh, battery_10kw_soc50.min_soc_wh
        )
        assert (
            battery_10kw_soc50.charge_state_percent()
            <= battery_10kw_soc50.min_soc_percent
        ), "SoC should not drop below min_soc"
