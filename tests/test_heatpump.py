import pytest
from modules.class_heatpump import Heatpump


@pytest.fixture(scope='function')
def heatpump() -> Heatpump:
    """ Heatpump with 5 kw heating power and 24 h prediction
    """
    return Heatpump(5000, 24)

class TestHeatpump:
    def test_cop(self, heatpump):
        """Testing calculate COP for variouse outside temperatures"""
        assert heatpump.cop_berechnen(-10) == 2.0, "COP for -10 degree isn't correct"
        assert heatpump.cop_berechnen(0) == 3.0, "COP for 0 degree isn't correct"
        assert heatpump.cop_berechnen(10) == 4.0, "COP for 10 degree isn't correct"
