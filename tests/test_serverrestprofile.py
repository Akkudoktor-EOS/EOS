"""Tests for the REST API of the device profile store (/v1/profile)."""

import json
from pathlib import Path
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from akkudoktoreos.devices.profile.batteryprofile import BatteryProfileSettings
from akkudoktoreos.devices.profile.profilestore import ProfileStore, get_profile_store
from akkudoktoreos.server.rest.profile import router


@pytest.fixture
def client(config_eos) -> Iterator[TestClient]:
    """Client of an app with only the profile router, on a fresh store."""
    ProfileStore.reset_instance()
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client
    ProfileStore.reset_instance()


@pytest.fixture
def generic_lfp() -> dict:
    """The document of the packaged generic LFP battery profile."""
    store = get_profile_store()
    path = store.package_dir(BatteryProfileSettings) / "generic-lfp.json"
    return json.loads(path.read_text(encoding="utf-8"))


def user_path(name: str) -> Path:
    return get_profile_store().user_dir(BatteryProfileSettings) / f"{name}.json"


class TestRead:
    def test_kinds(self, client):
        response = client.get("/v1/profile")
        assert response.status_code == 200
        assert "battery" in response.json()

    def test_list_contains_the_packaged_profile(self, client):
        response = client.get("/v1/profile/battery")
        assert response.status_code == 200
        assert {"name": "generic-lfp", "source": "package", "packaged": True} in response.json()

    def test_schema(self, client):
        response = client.get("/v1/profile/battery/schema")
        assert response.status_code == 200
        assert "charge_limit_crate" in response.json()["properties"]

    def test_get_document(self, client):
        response = client.get("/v1/profile/battery/generic-lfp")
        assert response.status_code == 200
        assert response.json()["chemistry"] == "LFP"
        BatteryProfileSettings.model_validate(response.json())

    def test_unknown_kind_is_404(self, client):
        assert client.get("/v1/profile/nope").status_code == 404
        assert client.get("/v1/profile/nope/x").status_code == 404
        assert client.put("/v1/profile/nope/x", json={}).status_code == 404

    def test_missing_profile_is_404(self, client):
        response = client.get("/v1/profile/battery/does-not-exist")
        assert response.status_code == 404
        assert "does-not-exist" in response.json()["detail"]


class TestWrite:
    def test_put_saves_a_user_profile_and_returns_it(self, client, generic_lfp):
        document = {**generic_lfp, "battery_model": "My battery", "nominal_capacity_wh": 10240.0}
        response = client.put("/v1/profile/battery/my-battery", json=document)
        assert response.status_code == 200
        assert response.json()["nominal_capacity_wh"] == 10240.0
        assert user_path("my-battery").is_file()
        assert client.get("/v1/profile/battery/my-battery").json()["battery_model"] == "My battery"
        assert {"name": "my-battery", "source": "user", "packaged": False} in client.get(
            "/v1/profile/battery"
        ).json()

    def test_put_takes_effect_for_compiled_profile_users(self, client, generic_lfp):
        store = get_profile_store()
        before = store.get(BatteryProfileSettings, "generic-lfp")
        assert before.nominal_capacity_wh is None
        response = client.put(
            "/v1/profile/battery/generic-lfp", json={**generic_lfp, "nominal_capacity_wh": 5000.0}
        )
        assert response.status_code == 200
        after = store.get(BatteryProfileSettings, "generic-lfp")
        assert after is not before
        assert after.nominal_capacity_wh == 5000.0

    def test_put_of_an_invalid_document_is_422_and_saves_nothing(self, client, generic_lfp):
        response = client.put(
            "/v1/profile/battery/broken", json={**generic_lfp, "nominal_capacity_wh": -1}
        )
        assert response.status_code == 422
        assert "nominal_capacity_wh" in response.json()["detail"]
        assert not user_path("broken").exists()

    def test_put_with_an_unsafe_name_is_422(self, client, generic_lfp):
        response = client.put("/v1/profile/battery/..%2F..%2Fescape", json=generic_lfp)
        assert response.status_code in (404, 422)  # rejected by the router or the store
        data_dir = get_profile_store().user_dir(BatteryProfileSettings).parent.parent
        assert not (data_dir / "escape.json").exists()

    def test_names_with_subdirectories(self, client, generic_lfp):
        assert client.put("/v1/profile/battery/vendor/model-a", json=generic_lfp).status_code == 200
        assert client.get("/v1/profile/battery/vendor/model-a").status_code == 200
        assert user_path("vendor/model-a").is_file()


class TestDelete:
    def test_delete_user_profile(self, client, generic_lfp):
        client.put("/v1/profile/battery/my-battery", json=generic_lfp)
        response = client.delete("/v1/profile/battery/my-battery")
        assert response.status_code == 200
        assert response.json() is None  # no packaged profile of that name
        assert client.get("/v1/profile/battery/my-battery").status_code == 404

    def test_delete_of_an_override_restores_the_packaged_profile(self, client, generic_lfp):
        client.put(
            "/v1/profile/battery/generic-lfp", json={**generic_lfp, "nominal_capacity_wh": 5000.0}
        )
        response = client.delete("/v1/profile/battery/generic-lfp")
        assert response.status_code == 200
        assert response.json() == {"name": "generic-lfp", "source": "package", "packaged": True}
        assert client.get("/v1/profile/battery/generic-lfp").json()["nominal_capacity_wh"] is None

    def test_delete_of_a_packaged_profile_is_409(self, client):
        response = client.delete("/v1/profile/battery/generic-lfp")
        assert response.status_code == 409
        assert client.get("/v1/profile/battery/generic-lfp").status_code == 200

    def test_delete_of_a_missing_profile_is_404(self, client):
        assert client.delete("/v1/profile/battery/does-not-exist").status_code == 404


def test_eos_app_serves_the_profile_api(config_eos):
    from akkudoktoreos.server.eos import app

    paths = app.openapi()["paths"]
    assert "/v1/profile/{kind}/{name}" in paths
    assert set(paths["/v1/profile/{kind}/{name}"]) == {"get", "put", "delete"}
