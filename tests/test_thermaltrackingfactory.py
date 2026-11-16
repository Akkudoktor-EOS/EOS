"""Isolated ASGI construction for target-only pilot admission, without live services."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from akkudoktoreos.server.thermalshadow import (
    create_native_thermal_shadow_app,
    create_thermal_shadow_app,
)
from akkudoktoreos.thermal.thermalsettings import ThermalCommonSettings
from akkudoktoreos.thermal.thermaltracking import ThermalTrackingSettings


class ReadOnlyContext:
    async def ingest(self, bundle):
        raise AssertionError("No data acquisition in factory checks")

    async def plan(self, request):
        raise AssertionError("No optimizer in factory checks")

    def status(self):
        return {"has_bundle": False}


def request_payload():
    now = datetime.now(timezone.utc)
    return {
        "zone_id": "example",
        "operator": {
            "master_power": "On",
            "control_mode": "mpc",
            "target_temperature_c": 21,
            "control_revision": 7,
            "observed_at": now.isoformat(),
        },
        "room_temperature_c": 21,
        "room_observed_at": now.isoformat(),
        "source": {"status": "infeasible", "issued_at": now.isoformat()},
        "pilot_expires_at": (now + timedelta(minutes=90)).isoformat(),
    }


def test_native_factory_defaults_pilot_to_disabled_and_keeps_shadow_health():
    app = create_native_thermal_shadow_app(ReadOnlyContext())
    with TestClient(app) as client:
        health = client.get("/health").json()
        response = client.post("/v1/thermal/tracking/proposal", json=request_payload())
    assert health["shadow_only"] is True
    assert health["execution_allowed"] is False
    assert response.status_code == 200
    assert response.json()["pilot_tracking_allowed"] is False
    assert response.json()["reason"] == "pilot_disabled"


def test_native_factory_uses_explicit_settings_without_enabling_full_plan():
    settings = ThermalTrackingSettings(enabled=True, zone_id="example")
    app = create_native_thermal_shadow_app(ReadOnlyContext(), tracking_settings=settings)
    with TestClient(app) as client:
        response = client.post("/v1/thermal/tracking/proposal", json=request_payload())
    assert response.status_code == 200
    assert response.json()["reason"] == "source_has_no_current_success"
    assert response.json()["execution_allowed"] is False
    assert response.json()["battery_execution_allowed"] is False
    assert app.state.thermal_tracking_settings is not settings


def test_each_app_has_an_independent_persistent_policy_and_lock():
    first = create_native_thermal_shadow_app(ReadOnlyContext())
    second = create_native_thermal_shadow_app(ReadOnlyContext())
    assert first.state.thermal_tracking_policy is not second.state.thermal_tracking_policy
    assert first.state.thermal_tracking_lock is not second.state.thermal_tracking_lock
    policy = first.state.thermal_tracking_policy
    with TestClient(first) as client:
        for _ in range(2):
            assert (
                client.post("/v1/thermal/tracking/proposal", json=request_payload()).status_code
                == 200
            )
            assert first.state.thermal_tracking_policy is policy


def test_standalone_off_mode_does_not_enable_a_configured_tracking_flag():
    settings = ThermalCommonSettings(
        mode="off", tracking=ThermalTrackingSettings(enabled=True, zone_id="example")
    )
    app = create_thermal_shadow_app(settings)
    assert app.state.thermal_tracking_settings.enabled is False


def test_standalone_shadow_mode_passes_explicit_tracking_settings():
    settings = ThermalCommonSettings(
        mode="shadow", tracking=ThermalTrackingSettings(enabled=True, zone_id="example")
    )
    app = create_thermal_shadow_app(settings)
    assert app.state.thermal_tracking_settings.enabled is True
