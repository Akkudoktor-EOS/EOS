"""Injected native shadow API tests, without a Windows server or EOS lifespan."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from akkudoktoreos.server.thermalshadow import (
    create_native_app,
    create_native_thermal_shadow_app,
)


def bundle():
    return {
        "fleet": {
            "start_datetime": "2026-11-08T12:00:00Z",
            "interval_seconds": 900,
            "load_w": [300.0],
            "pv_w": [0.0],
            "import_price_eur_per_kwh": [0.3],
            "export_price_eur_per_kwh": [0.1],
            "import_limit_w": 10000.0,
            "export_limit_w": 5000.0,
            "devices": [],
        },
        "load_basis": "residual_excluding_heat",
        "forecast_issued_at": "2026-11-08T11:58:00Z",
        "sampled_at": "2026-11-08T11:59:00Z",
        "weather": [{"date_time": "2026-11-08T12:00:00Z", "weather_temp_air": 5.0}],
        "samples": [{"date_time": "2026-11-08T11:58:50Z", "key": "room", "value": 20.0}],
        "quality_notes": [],
    }


class FakeContext:
    def __init__(self):
        self.ingest = AsyncMock(return_value={"status": "stored"})
        self.plan = AsyncMock(return_value={"status": "infeasible", "reason": "cold initial room"})

    def status(self):
        return {"status": "ready", "sample_count": 1}


def test_native_routes_use_only_the_injected_context():
    context = FakeContext()
    with (
        patch("akkudoktoreos.core.coreabc.get_ems", side_effect=AssertionError("EMS")),
        patch("akkudoktoreos.core.coreabc.get_adapter", side_effect=AssertionError("adapter")),
    ):
        app = create_native_thermal_shadow_app(context)
        with TestClient(app) as client:
            assert client.get("/health").json()["execution_allowed"] is False
            response = client.put("/v1/thermal/data_bundle", json=bundle())
            assert response.status_code == 200, response.text
            assert response.json()["shadow_only"] is True
            request = {"zone_id": "room", "heat_source_id": "heater", "basis_control_revision": 7}
            response = client.post("/v1/thermal/plan", json=request)
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "infeasible"
            assert response.json()["execution_allowed"] is False
            assert client.get("/v1/thermal/status").json()["sample_count"] == 1
        context.ingest.assert_awaited_once()
        context.plan.assert_awaited_once()


def test_invalid_bundle_never_reaches_context():
    context = FakeContext()
    payload = bundle()
    payload["load_basis"] = "full_house_including_heat"
    with TestClient(create_native_thermal_shadow_app(context)) as client:
        assert client.put("/v1/thermal/data_bundle", json=payload).status_code == 422
        assert client.post("/v1/thermal/plan", json={"zone_id": "room"}).status_code == 422
    context.ingest.assert_not_awaited()


def test_native_distribution_schedule_and_load_ack_roundtrip_without_services():
    context = FakeContext()
    payload = bundle()
    payload["distribution_fan_levels"] = {"zone1": ["low"]}
    payload["distribution_load_excluded_zone_ids"] = ["zone1"]
    with TestClient(create_native_thermal_shadow_app(context)) as client:
        response = client.put("/v1/thermal/data_bundle", json=payload)
    assert response.status_code == 200, response.text
    received = context.ingest.call_args.args[0]
    assert received.distribution_fan_levels == {"zone1": ("low",)}
    assert received.distribution_load_excluded_zone_ids == ("zone1",)


@pytest.mark.parametrize(
    "addition",
    [
        {"distribution_fan_levels": {"zone1": []}},
        {"distribution_fan_levels": {"zone1": [" "]}},
        {"distribution_load_excluded_zone_ids": ["zone1", "zone1"]},
    ],
)
def test_invalid_distribution_bundle_is_rejected_before_injected_context(addition):
    context = FakeContext()
    payload = {**bundle(), **addition}
    with TestClient(create_native_thermal_shadow_app(context)) as client:
        response = client.put("/v1/thermal/data_bundle", json=payload)
    assert response.status_code == 422
    context.ingest.assert_not_awaited()
    context.plan.assert_not_awaited()


def test_context_validation_errors_are_explicit_and_not_a_schedule():
    context = FakeContext()
    context.ingest.side_effect = ValueError("invalid native data")
    context.plan.side_effect = ValueError("unsupported source")
    with TestClient(create_native_thermal_shadow_app(context)) as client:
        assert client.put("/v1/thermal/data_bundle", json=bundle()).status_code == 422
        response = client.post(
            "/v1/thermal/plan",
            json={
                "zone_id": "room",
                "heat_source_id": "heater",
                "basis_control_revision": 1,
            },
        )
        assert response.status_code == 422
        assert "result" not in response.json()


def test_narrow_native_app_has_no_mainserver_or_actuator_routes():
    app = create_native_thermal_shadow_app(FakeContext())
    paths = set(app.openapi()["paths"])
    assert "/v1/optimize" not in paths
    assert "/v1/config" not in paths
    assert "/v1/measurement/value" not in paths
    assert "/v1/thermal/data_bundle" in paths
    assert "/v1/thermal/plan" in paths


def test_uninitialized_route_returns_503():
    from akkudoktoreos.server.rest.thermaldata import router

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/v1/thermal/status").status_code == 503


@pytest.mark.parametrize("config,data", [(None, None), ("relative", "data"), ("same", "same")])
def test_native_factory_requires_isolation_before_core_initialization(
    monkeypatch, tmp_path, config, data
):
    for name, value in [("EOS_CONFIG_DIR", config), ("EOS_DATA_DIR", data)]:
        if value is None:
            monkeypatch.delenv(name, raising=False)
        elif value == "same":
            monkeypatch.setenv(name, str(tmp_path / "same"))
        else:
            monkeypatch.setenv(name, value)
    with patch(
        "akkudoktoreos.core.coreabc.get_config", side_effect=AssertionError("config initialized")
    ):
        with pytest.raises(ValueError):
            create_native_app()


def test_native_factory_requires_existing_reviewed_config(monkeypatch, tmp_path):
    monkeypatch.setenv("EOS_CONFIG_DIR", str(tmp_path / "native-only-config-missing"))
    monkeypatch.setenv("EOS_DATA_DIR", str(tmp_path / "native-only-data"))
    with patch(
        "akkudoktoreos.core.coreabc.get_config", side_effect=AssertionError("config initialized")
    ):
        with pytest.raises(ValueError, match="reviewed private"):
            create_native_app()


@pytest.mark.parametrize("escape", ["config", "data", "cache", "output"])
def test_native_factory_rejects_resolved_storage_outside_private_data_before_db(
    monkeypatch, tmp_path, escape
):
    config_dir = tmp_path / "isolated-config"
    data_dir = tmp_path / "isolated-data"
    config_dir.mkdir()
    data_dir.mkdir()
    (config_dir / "EOS.config.json").write_text("{}")
    monkeypatch.setenv("EOS_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("EOS_DATA_DIR", str(data_dir))
    config = SimpleNamespace(
        general=SimpleNamespace(
            config_folder_path=tmp_path / "active-config" if escape == "config" else config_dir,
            data_folder_path=tmp_path / "active-data" if escape == "data" else data_dir,
            data_output_path=tmp_path / "active-output"
            if escape == "output"
            else data_dir / "output",
        ),
        cache=SimpleNamespace(
            path=lambda: tmp_path / "active-cache" if escape == "cache" else data_dir / "cache"
        ),
    )
    with (
        patch("akkudoktoreos.core.coreabc.get_config", return_value=config),
        patch(
            "akkudoktoreos.core.coreabc.get_database",
            side_effect=AssertionError("database initialized"),
        ),
    ):
        with pytest.raises(ValueError, match="isolated"):
            create_native_app()


def test_native_factory_initializes_only_the_four_isolated_data_services(monkeypatch, tmp_path):
    config_dir, data_dir = tmp_path / "native-config", tmp_path / "native-data"
    config_dir.mkdir()
    (config_dir / "EOS.config.json").write_text("{}")
    monkeypatch.setenv("EOS_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("EOS_DATA_DIR", str(data_dir))
    config = SimpleNamespace(
        general=SimpleNamespace(
            config_folder_path=config_dir,
            data_folder_path=data_dir,
            data_output_path=data_dir / "output",
            timezone="UTC",
        ),
        presence=SimpleNamespace(history_days=14),
        cache=SimpleNamespace(path=lambda: data_dir / "cache"),
        database=SimpleNamespace(autosave_interval_sec=None, compaction_interval_sec=None),
        thermal=SimpleNamespace(mode="shadow"),
    )
    with (
        patch("akkudoktoreos.core.coreabc.get_config", return_value=config) as get_config,
        patch("akkudoktoreos.core.coreabc.get_database") as get_database,
        patch("akkudoktoreos.core.coreabc.get_measurement") as get_measurement,
        patch("akkudoktoreos.core.coreabc.get_prediction") as get_prediction,
        patch("akkudoktoreos.core.coreabc.get_ems", side_effect=AssertionError("EMS")),
        patch("akkudoktoreos.core.coreabc.get_adapter", side_effect=AssertionError("adapter")),
        patch(
            "akkudoktoreos.thermal.thermalconfigrequest.ThermalRuntimeContext",
            return_value=FakeContext(),
        ),
    ):
        app = create_native_app()
        assert app.title == "EOS Native Thermal Shadow"
        for accessor in (get_config, get_database, get_measurement, get_prediction):
            accessor.assert_called_once_with(init=True)
