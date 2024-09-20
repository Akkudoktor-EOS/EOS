import json
import pytest
from flask_server import app 

@pytest.fixture
def client():
    with app.test_client() as client:
        yield client

def test_flask_soc(client):
    response = client.get('/soc')
    assert response.status_code == 200
    assert response.json == "Done"

def test_flask_strompreis(client):
    response = client.get('/strompreis')
    assert response.status_code == 200
    assert isinstance(response.json, list)  # Assuming the response is a list

def test_flask_gesamtlast(client):
    test_data = {
        "year_energy": 1000,
        "hours": 48,
        "measured_data": [{"time": "2023-09-01T00:00:00", "value": 10}]  # Example data
    }
    response = client.post('/gesamtlast', data=json.dumps(test_data), content_type='application/json')
    assert response.status_code == 200
    assert isinstance(response.json, list)

def test_flask_gesamtlast_simple(client):
    response = client.get('/gesamtlast_simple', query_string={"year_energy": 1000})
    assert response.status_code == 200
    assert isinstance(response.json, list)

def test_flask_pvprognose(client):
    query_string = {
        "url": "http://example.com/pv",
        "ac_power_measurement": "100"
    }
    response = client.get('/pvforecast', query_string=query_string)
    assert response.status_code == 200
    assert "temperature" in response.json
    assert "pvpower" in response.json

def test_flask_optimize(client):
    test_data = {
        "preis_euro_pro_wh_akku": 0.1,
        "strompreis_euro_pro_wh": 0.15,
        "gesamtlast": [1, 2, 3],  # Example data
        "pv_akku_cap": 10,
        "einspeiseverguetung_euro_pro_wh": 0.05,
        "pv_forecast": [0, 0, 0],  # Example data
        "temperature_forecast": [20, 21, 19],  # Example data
        "eauto_min_soc": 20,
        "eauto_cap": 40,
        "eauto_charge_efficiency": 0.9,
        "eauto_charge_power": 10,
        "eauto_soc": 50,
        "pv_soc": 60,
        "start_solution": {},
        "haushaltsgeraet_dauer": 2,
        "haushaltsgeraet_wh": 5
    }
    response = client.post('/optimize', data=json.dumps(test_data), content_type='application/json')
    assert response.status_code == 200
    assert isinstance(response.json, dict)  # Check the expected response structure

def test_get_pdf(client):
    response = client.get('/visualisierungsergebnisse.pdf')
    assert response.status_code == 200
    assert response.headers['Content-Type'] == 'application/pdf'

def test_site_map(client):
    response = client.get('/site-map')
    assert response.status_code == 200
    assert "<h1>Valid routes</h1>" in response.data.decode()

def test_root_redirect(client):
    response = client.get('/')
    assert response.status_code == 302
    assert response.location == "/site-map"  # Just compare the relative path

