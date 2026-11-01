"""Secrets in the configuration never leave the REST API (server/rest/secrets.py)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from akkudoktoreos.server.rest.secrets import (
    MASK,
    is_masked,
    mask_value,
    path_is_secret,
    redact,
    strip_masked,
)

TOKEN = "eyJhbGciOiJIUzI1NiJ9.secret-token-value"
TIBBER = "tibber-secret-access-token"


def test_path_is_secret():
    assert path_is_secret("pvforecast/homeassistant/token")
    assert path_is_secret("elecprice/tibber/access_token")
    assert path_is_secret("pvforecast/provider_settings/PVForecastSolcast/api_key")
    assert not path_is_secret("general/latitude")
    assert not path_is_secret("pvforecast/homeassistant/entity_ids")


def test_mask_value():
    assert mask_value("token", TOKEN) == MASK
    assert mask_value("token", None) is None
    assert mask_value("token", "") == ""  # "not set" is no secret
    assert mask_value("dsn", "postgresql://eos:pw@db:5432/eos") == f"postgresql://eos:{MASK}@db:5432/eos"
    assert mask_value("dsn", "host=db password=pw dbname=eos") == f"host=db password={MASK} dbname=eos"
    assert mask_value("dsn", "postgresql://db:5432/eos") == "postgresql://db:5432/eos"


def test_redact_and_strip_masked():
    data: dict = {"a": {"token": TOKEN, "entity": "x", "list": [{"api_key": "k"}]}, "b": 1}
    red: dict = redact(data)
    assert red == {"a": {"token": MASK, "entity": "x", "list": [{"api_key": MASK}]}, "b": 1}
    assert data["a"]["token"] == TOKEN  # input untouched
    assert is_masked(red["a"]["token"]) and not is_masked(TOKEN)
    assert strip_masked(red) == {"a": {"entity": "x", "list": [{}]}, "b": 1}


@pytest.fixture
def client(config_eos):
    from akkudoktoreos.server.eos import app

    config_eos.merge_settings_from_dict(
        {
            "pvforecast": {"homeassistant": {"token": TOKEN}},
            "elecprice": {"tibber": {"access_token": TIBBER}},
        }
    )
    return TestClient(app), config_eos


def test_config_responses_are_redacted(client):
    http, config = client
    for response in (
        http.get("/v1/config"),
        http.get("/v1/config/pvforecast"),
        http.get("/v1/config/elecprice/tibber"),
        http.put("/v1/config/ems/interval", json=120),
    ):
        assert response.status_code == 200, response.text
        assert TOKEN not in response.text
        assert TIBBER not in response.text
    assert http.get("/v1/config/pvforecast/homeassistant/token").json() == MASK
    assert http.get("/v1/config/elecprice/tibber/access_token").json() == MASK
    assert http.get("/v1/config/ems/interval").json() == 120
    # The secrets are unchanged inside EOS
    assert config.pvforecast.homeassistant.token == TOKEN
    assert config.elecprice.tibber.access_token == TIBBER


def test_masked_values_written_back_keep_the_secret(client):
    http, config = client
    response = http.put("/v1/config/pvforecast/homeassistant/token", json=MASK)
    assert response.status_code == 200, response.text
    assert config.pvforecast.homeassistant.token == TOKEN

    # Import of an exported (masked) configuration, as EOSdash does it
    exported = http.get("/v1/config").json()
    exported["ems"]["interval"] = 90
    response = http.put("/v1/config", json=exported)
    assert response.status_code == 200, response.text
    assert config.pvforecast.homeassistant.token == TOKEN
    assert config.elecprice.tibber.access_token == TIBBER
    assert config.ems.interval == 90

    # A real new value is accepted
    response = http.put("/v1/config/elecprice/tibber/access_token", json="new-token")
    assert response.status_code == 200, response.text
    assert config.elecprice.tibber.access_token == "new-token"
