import pytest

from akkudoktoreos.devices.heatpump import Heatpump


@pytest.fixture(scope="function")
def hp_5kw_24h() -> Heatpump:
    """Heatpump with 5 kw heating power and 24 h prediction."""
    return Heatpump(5000, 24)


class TestHeatpump:
    def test_cop(self, hp_5kw_24h: Heatpump):
        """Testing calculate COP for variouse outside temperatures."""
        assert hp_5kw_24h.calculate_cop(-10) == 2.0
        assert hp_5kw_24h.calculate_cop(0) == 3.0
        assert hp_5kw_24h.calculate_cop(10) == 4.0
        # Check edge case for outside temperature
        out_temp_min = -100.1
        out_temp_max = 100.1
        with pytest.raises(ValueError, match=f"'{out_temp_min}' not in range"):
            hp_5kw_24h.calculate_cop(out_temp_min)
        with pytest.raises(ValueError, match=f"'{out_temp_max}' not in range"):
            hp_5kw_24h.calculate_cop(out_temp_max)

    def test_heating_output(self, hp_5kw_24h: Heatpump):
        """Testing calculate of heating output."""
        assert hp_5kw_24h.calculate_heating_output(-10.0) == 5000
        assert hp_5kw_24h.calculate_heating_output(0.0) == 5000
        assert hp_5kw_24h.calculate_heating_output(10.0) == pytest.approx(4939.583)

    def test_heating_power(self, hp_5kw_24h: Heatpump):
        """Testing calculation of heating power."""
        assert hp_5kw_24h.calculate_heat_power(-10.0) == 2104
        assert hp_5kw_24h.calculate_heat_power(0.0) == 1164
        assert hp_5kw_24h.calculate_heat_power(10.0) == 548
