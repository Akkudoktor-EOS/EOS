import pytest

from modules.heatpump import Heatpump


@pytest.fixture(scope="function")
def hp_5kw_24h() -> Heatpump:
    """Heatpump with 5 kw heating power and 24 h prediction"""
    return Heatpump(5000, 24)


class TestHeatpump:
    def test_cop(self, hp_5kw_24h: Heatpump):
        """Testing calculate COP for variouse outside temperatures"""
        assert hp_5kw_24h.calculate_cop(-10) == 2.0, "COP for -10 degree isn't correct"
        assert hp_5kw_24h.calculate_cop(0) == 3.0, "COP for 0 degree isn't correct"
        assert hp_5kw_24h.calculate_cop(10) == 4.0, "COP for 10 degree isn't correct"
        # Check edge case for outside temperature
        out_temp_min = -100.1
        out_temp_max = 100.1
        with pytest.raises(ValueError) as err:
            hp_5kw_24h.calculate_cop(out_temp_min)
        assert str(out_temp_min) in str(err.value)
        with pytest.raises(ValueError) as err:
            hp_5kw_24h.calculate_cop(out_temp_max)
        assert str(out_temp_max) in str(err.value)

    def test_heating_output(self, hp_5kw_24h: Heatpump):
        """Testing calculate of heating output"""
        assert (
            hp_5kw_24h.calculate_heating_output(-10.0) == 5000
        ), "Wrong output at -10.0 Celsius"
        assert (
            hp_5kw_24h.calculate_heating_output(0.0) == 5000
        ), "Wrong output at 0.0 Celsius"
        assert hp_5kw_24h.calculate_heating_output(10.0) == pytest.approx(
            4939.583
        ), "Wrong output at 10.0 Celsius"

    def test_heating_power(self, hp_5kw_24h: Heatpump):
        """Testing calculation of heating power"""
        assert hp_5kw_24h.calculate_heat_power(-10.0) == 2104, "Wrong power at -10.0 Celsius"
        assert hp_5kw_24h.calculate_heat_power(0.0) == 1164, "Wrong power at 0.0 Celsius"
        assert hp_5kw_24h.calculate_heat_power(10.0) == 548, "Wrong power at 10.0 Celsius"
