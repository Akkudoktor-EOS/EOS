import pytest
from modules.heatpump import Heatpump


@pytest.fixture(scope="function")
def hp_5kw_24h() -> Heatpump:
    """Heat pump with 5 kw heating output and 24 h prediction"""
    return Heatpump(5000, 24)
