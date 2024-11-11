import requests

from akkudoktoreos.config import prediction_hours


def test_server(server):
    """Test the server."""
    result = requests.get(f"{server}/gesamtlast_simple?year_energy=2000&")
    assert result.status_code == 200
    assert len(result.json()) == prediction_hours
