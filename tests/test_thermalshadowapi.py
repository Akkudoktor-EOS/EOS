"""In-process shadow API isolation; never starts Uvicorn or contacts devices."""

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Event
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core import coreabc
from akkudoktoreos.core import ems as emsmodule
from akkudoktoreos.core.ems import EnergyManagement, EnergyManagementStage
from akkudoktoreos.core.emsettings import EnergyManagementMode
from akkudoktoreos.optimization.fleet import FleetSolution
from akkudoktoreos.optimization.fleetrequest import FleetConfigRequest
from akkudoktoreos.optimization.optimization import OptimizationAlgorithm
from akkudoktoreos.prediction.weatherabc import WeatherDataRecord
from akkudoktoreos.server import thermalshadow
from akkudoktoreos.server.rest import thermal as api
from akkudoktoreos.thermal.thermalheatplanning import (
    ThermalHeatPlanningRequest,
    ThermalHeatPlanningSolution,
)
from akkudoktoreos.thermal.thermaloptimization import (
    ThermalShadowRequest,
    ThermalShadowSolution,
)
from akkudoktoreos.thermal.thermalsettings import ThermalCommonSettings
from akkudoktoreos.utils.datetimeutil import to_datetime

NOW = datetime(2026, 1, 10, 8, tzinfo=timezone.utc)
START = NOW + timedelta(minutes=15)


def payload() -> dict[str, Any]:
    """Provide a small explicit residual-load scenario with no private inputs."""
    return {
        "fleet": {
            "start_datetime": START.isoformat(),
            "interval_seconds": 900,
            "load_w": [500.0] * 4,
            "pv_w": [0.0, 500.0, 500.0, 0.0],
            "import_price_eur_per_kwh": [0.1, 0.1, 0.8, 0.8],
            "export_price_eur_per_kwh": [0.05] * 4,
            "import_limit_w": 5000,
            "export_limit_w": 5000,
            "solver_time_limit_seconds": 2,
            "devices": [
                {
                    "device_id": "battery1",
                    "kind": "battery",
                    "capacity_wh": 2000,
                    "initial_energy_wh": 1000,
                    "reserve_wh": 200,
                    "max_charge_power_w": 1000,
                    "max_discharge_power_w": 1000,
                    "charge_efficiency": 0.95,
                    "discharge_efficiency": 0.95,
                }
            ],
        },
        "load_basis": "residual_excluding_heat",
        "zone_id": "room1",
        "heat_source_id": "heater1",
        "model": {
            "room_loss_per_hour": 0.08,
            "room_mass_exchange_per_hour": 0.3,
            "mass_capacity_ratio": 3.0,
            "electric_gain_kelvin_per_kwh": 3.0,
        },
        "state": {
            "estimated_at": START.isoformat(),
            "based_on_observed_at": NOW.isoformat(),
            "room_temperature_c": 20.5,
            "mass_temperature_c": 20.5,
        },
        "outdoor_temperature_c": [5.0] * 4,
        "comfort_bands": [
            {"min_temperature_c": 20.0, "target_temperature_c": 20.5, "max_temperature_c": 21.0}
            for _ in range(4)
        ],
        "max_electric_power_w": 2000,
        "issued_at": NOW.isoformat(),
        "revision": 7,
        "basis_control_revision": 3,
        "plan_ttl_seconds": 1800,
    }


def settings(mode: str = "shadow", enabled: bool = True) -> ThermalCommonSettings:
    """Declare one isolated zone without enabling an operational device."""
    return ThermalCommonSettings.model_validate(
        {
            "mode": mode,
            "zones": {
                "room1": {
                    "enabled": enabled,
                    "room_temperature_measurement_key": "room1-temperature",
                    "model_reference": "model1",
                    "comfort": {
                        "min_temperature_c": 20.0,
                        "target_temperature_c": 20.5,
                        "max_temperature_c": 21.0,
                    },
                }
            },
        }
    )


def heat_pump_payload() -> dict[str, Any]:
    """Use native weather records and explicit synthetic source ratings."""
    data = payload()
    del data["model"]
    del data["outdoor_temperature_c"]
    data["building"] = {
        "room_loss_per_hour": 0.08,
        "room_mass_exchange_per_hour": 0.3,
        "mass_capacity_ratio": 3.0,
        "heat_gain_kelvin_per_kwh": 1.0,
    }
    data["weather"] = [
        {
            "date_time": (START + timedelta(minutes=15 * k)).isoformat(),
            "weather_temp_air": 5.0,
            "weather_relative_humidity": 80.0,
        }
        for k in range(4)
    ]
    data["delivery_temperature_c"] = [35.0] * 4
    data["heat_pump_profile"] = {
        "profile_id": "synthetic",
        "heat_pump_type": "air_to_water",
        "cop_points": [
            {"outdoor_temperature_c": t, "delivery_temperature_c": 35.0, "cop": 3.0}
            for t in (0.0, 10.0)
        ],
        "thermal_max_points": [
            {"outdoor_temperature_c": t, "delivery_temperature_c": 35.0, "thermal_power_w": 6000.0}
            for t in (0.0, 10.0)
        ],
        "cop_includes_defrost": False,
        "provenance": {"source": "Synthetic API test", "quality": "user_supplied"},
    }
    return data


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(api, "utc_now", lambda: NOW)
    return thermalshadow.create_thermal_shadow_app(settings())


@pytest.mark.asyncio
async def test_successful_shadow_is_separate_from_operational_ems(
    app: FastAPI, config_eos: ConfigEOS, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_plan = Mock(name="operational_plan")
    old_solution = Mock(name="operational_solution")
    old_fleet = Mock(name="operational_fleet")
    monkeypatch.setattr(EnergyManagement, "_plan", old_plan)
    monkeypatch.setattr(EnergyManagement, "_optimization_solution", old_solution)
    monkeypatch.setattr(EnergyManagement, "_fleet_solution", old_fleet)
    monkeypatch.setattr(EnergyManagement, "_stage", EnergyManagementStage.IDLE)
    old_start = to_datetime("2026-01-10T06:00:00Z")
    monkeypatch.setattr(EnergyManagement, "_start_datetime", old_start)
    monkeypatch.setattr(EnergyManagement, "_observation_datetime", old_start)
    run = AsyncMock(side_effect=AssertionError("shadow must not run EMS"))
    notify = Mock(side_effect=AssertionError("shadow must not notify control"))
    monkeypatch.setattr(EnergyManagement, "run", run)
    monkeypatch.setattr(emsmodule, "notify_optimization_completed", notify)
    global_config = Mock(side_effect=AssertionError("standalone must not read operational config"))
    global_ems = Mock(side_effect=AssertionError("shadow must not read or move EMS origin"))
    monkeypatch.setattr(coreabc, "get_config", global_config)
    monkeypatch.setattr(coreabc, "get_ems", global_ems)
    before_config = config_eos.model_dump_json(exclude_computed_fields=True)
    data = payload()
    original = deepcopy(data)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/v1/optimize/thermal/shadow", json=data)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] in ("optimal", "feasible"), result
    assert result["shadow_only"] is True
    assert result["execution_allowed"] is False
    assert result["plan"]["zone_id"] == "room1"
    assert result["plan"]["basis_control_revision"] == 3
    for branch, heat_name in (
        ("reference", "reference_heat_power_w"),
        ("candidate", "candidate_heat_power_w"),
    ):
        total = result[branch]["parameters"]["load_w"]
        assert total == pytest.approx([500.0 + power for power in result[heat_name]])
    assert data == original
    assert config_eos.model_dump_json(exclude_computed_fields=True) == before_config
    assert EnergyManagement._plan is old_plan
    assert EnergyManagement._optimization_solution is old_solution
    assert EnergyManagement._fleet_solution is old_fleet
    assert EnergyManagement._start_datetime == old_start
    assert EnergyManagement._observation_datetime == old_start
    assert EnergyManagement._stage == EnergyManagementStage.IDLE
    run.assert_not_awaited()
    notify.assert_not_called()
    global_config.assert_not_called()
    global_ems.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "control"])
async def test_nonshadow_mode_rejects_before_solver(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setattr(api, "utc_now", lambda: NOW)
    solve = Mock(side_effect=AssertionError("solver must not run"))
    monkeypatch.setattr(api, "optimize_thermal_shadow", solve)
    application = thermalshadow.create_thermal_shadow_app(settings(mode))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post("/v1/optimize/thermal/shadow", json=payload())
    assert response.status_code == 403
    solve.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("zone", ["unknown", "room1"])
async def test_missing_or_disabled_zone_rejects_before_solver(
    monkeypatch: pytest.MonkeyPatch, zone: str
) -> None:
    solve = Mock(side_effect=AssertionError("solver must not run"))
    monkeypatch.setattr(api, "optimize_thermal_shadow", solve)
    application = thermalshadow.create_thermal_shadow_app(settings(enabled=False))
    data = payload()
    data["zone_id"] = zone
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post("/v1/optimize/thermal/shadow", json=data)
    assert response.status_code == 403
    solve.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("basis", [None, "total_includes_heat", "unknown"])
async def test_explicit_residual_basis_is_required(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, basis: str | None
) -> None:
    solve = Mock(side_effect=AssertionError("ambiguous load must not reach solver"))
    monkeypatch.setattr(api, "optimize_thermal_shadow", solve)
    data = payload()
    if basis is None:
        del data["load_basis"]
    else:
        data["load_basis"] = basis
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/v1/optimize/thermal/shadow", json=data)
    assert response.status_code == 422
    solve.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["old_issue", "future_issue", "old_observation", "future_observation", "lease"]
)
async def test_stale_or_unpermitted_inputs_fail_before_solver(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    solve = Mock(side_effect=AssertionError("invalid input must not reach solver"))
    monkeypatch.setattr(api, "optimize_thermal_shadow", solve)
    data = payload()
    if kind == "old_issue":
        data["issued_at"] = (NOW - timedelta(seconds=1801)).isoformat()
    elif kind == "future_issue":
        data["issued_at"] = (NOW + timedelta(seconds=31)).isoformat()
    elif kind == "old_observation":
        data["state"]["based_on_observed_at"] = (NOW - timedelta(seconds=181)).isoformat()
    elif kind == "future_observation":
        data["state"]["based_on_observed_at"] = (NOW + timedelta(seconds=31)).isoformat()
    else:
        data["plan_ttl_seconds"] = 1801
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/v1/optimize/thermal/shadow", json=data)
    assert response.status_code == 422
    solve.assert_not_called()


@pytest.mark.asyncio
async def test_solver_errors_release_lock(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    solve = Mock(side_effect=ValueError("unsupported synthetic scenario"))
    monkeypatch.setattr(api, "optimize_thermal_shadow", solve)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post("/v1/optimize/thermal/shadow", json=payload())
        solve.side_effect = None
        solve.return_value = ThermalShadowSolution(status="limit", reason="bounded test")
        second = await client.post("/v1/optimize/thermal/shadow", json=payload())
    assert first.status_code == 422
    assert second.status_code == 200
    assert second.json()["execution_allowed"] is False


@pytest.mark.asyncio
async def test_parallel_shadow_is_rejected_and_settings_are_snapshotted(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, finish = Event(), Event()
    revisions = []

    def blocked(request: ThermalShadowRequest, **kwargs) -> ThermalShadowSolution:
        revisions.append(request.revision)
        started.set()
        if not finish.wait(timeout=5):
            raise AssertionError("test release did not arrive")
        return ThermalShadowSolution(status="limit", reason=str(request.revision))

    monkeypatch.setattr(api, "optimize_thermal_shadow", blocked)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        task = asyncio.create_task(client.post("/v1/optimize/thermal/shadow", json=payload()))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            second_data = payload()
            second_data["revision"] = 8
            second = await client.post("/v1/optimize/thermal/shadow", json=second_data)
            assert second.status_code == 429
            app.state.thermal_settings.mode = "off"
        finally:
            finish.set()
        first = await task
        denied = await client.post("/v1/optimize/thermal/shadow", json=payload())
    assert first.status_code == 200
    assert first.json()["reason"] == "7"
    assert denied.status_code == 403
    assert revisions == [7]


@pytest.mark.asyncio
async def test_factory_copies_settings_and_defaults_to_off(monkeypatch: pytest.MonkeyPatch) -> None:
    declared = settings()
    application = thermalshadow.create_thermal_shadow_app(declared)
    declared.mode = "control"
    declared.zones["room1"].enabled = False
    assert application.state.thermal_settings.mode == "shadow"
    assert application.state.thermal_settings.zones["room1"].enabled is True
    default = thermalshadow.create_thermal_shadow_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=default), base_url="http://test"
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["mode"] == "off"
    assert response.json()["execution_allowed"] is False


def test_environment_factory_reads_only_explicit_shadow_file(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "shadow.json"
    path.write_text(json.dumps({"thermal": settings().model_dump(mode="json")}), encoding="utf-8")
    monkeypatch.setenv("EOS_THERMAL_SHADOW_CONFIG", str(path))
    get_config = Mock(side_effect=AssertionError("operational config must stay untouched"))
    monkeypatch.setattr(coreabc, "get_config", get_config)
    application = thermalshadow.create_app()
    assert application.state.thermal_settings.mode == "shadow"
    get_config.assert_not_called()


@pytest.mark.asyncio
async def test_missing_app_lock_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    application = FastAPI()
    application.state.thermal_settings = settings()
    application.include_router(api.router)
    monkeypatch.setattr(api, "utc_now", lambda: NOW)
    solve = Mock(side_effect=AssertionError("uninitialized app must not solve"))
    monkeypatch.setattr(api, "optimize_thermal_shadow", solve)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post("/v1/optimize/thermal/shadow", json=payload())
    assert response.status_code == 503
    solve.assert_not_called()


@pytest.mark.asyncio
async def test_live_electrical_run_can_finish_during_shadow_without_being_overwritten(
    app: FastAPI, config_eos: ConfigEOS, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_eos.merge_settings_from_dict({"thermal": {"mode": "shadow"}})
    ems = EnergyManagement()
    monkeypatch.setattr(EnergyManagement, "_run_lock", asyncio.Lock())
    monkeypatch.setattr(EnergyManagement, "_plan", Mock(name="old_plan"))
    # A successful real EMS run publishes all these process-wide attributes.
    # Preserve every slot, not just _plan, so the next config fixture never
    # encounters our mock OptimizationSolution via its computed key registry.
    for name in (
        "_optimization_solution",
        "_fleet_solution",
        "_genetic_solution",
        "_genetic0_solution",
    ):
        monkeypatch.setattr(EnergyManagement, name, None)
    for name in ("_start_datetime", "_observation_datetime", "_last_run_datetime"):
        monkeypatch.setattr(EnergyManagement, name, None)
    monkeypatch.setattr(EnergyManagement, "_stage", EnergyManagementStage.IDLE)
    adapter, prediction = Mock(update_data=AsyncMock()), Mock(update_data=AsyncMock())
    monkeypatch.setattr(EnergyManagement, "adapter", adapter)
    monkeypatch.setattr(EnergyManagement, "prediction", prediction)
    electrical_plan, electrical_solution = (
        Mock(name="electrical_plan"),
        Mock(name="electrical_solution"),
    )
    native = FleetSolution(
        status="optimal", message="test", start_datetime=START, interval_seconds=900
    )
    monkeypatch.setattr(emsmodule, "optimize_fleet", Mock(return_value=native))
    monkeypatch.setattr(
        emsmodule,
        "fleet_representations",
        Mock(return_value=(electrical_solution, electrical_plan)),
    )
    monkeypatch.setattr(emsmodule, "notify_optimization_completed", Mock())
    request = ThermalShadowRequest.model_validate(payload()).fleet
    monkeypatch.setattr(FleetConfigRequest, "resolve", AsyncMock(return_value=request))
    started, finish = Event(), Event()

    def blocked(data: ThermalShadowRequest, **kwargs) -> ThermalShadowSolution:
        started.set()
        if not finish.wait(timeout=5):
            raise AssertionError("test release did not arrive")
        return ThermalShadowSolution(status="limit", reason=data.fleet.start_datetime.isoformat())

    monkeypatch.setattr(api, "optimize_thermal_shadow", blocked)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        task = asyncio.create_task(client.post("/v1/optimize/thermal/shadow", json=payload()))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            normal_start = to_datetime("2026-01-10T10:15:00Z")
            completed = await asyncio.wait_for(
                ems.run(
                    mode=EnergyManagementMode.OPTIMIZATION,
                    algorithm=OptimizationAlgorithm.FLEET,
                    start_datetime=normal_start,
                    fleet_parameters=FleetConfigRequest(),
                ),
                timeout=2,
            )
            assert completed is native
            assert ems.plan() is electrical_plan
        finally:
            finish.set()
        response = await task
    assert response.status_code == 200
    assert response.json()["reason"] == START.isoformat()
    assert ems.plan() is electrical_plan
    assert ems.optimization_solution() is electrical_solution
    assert ems.fleet_solution() is native
    assert ems.start_datetime == normal_start
    # The ordinary EMS acquires data and later dispatches its own existing plan.
    assert adapter.update_data.await_count == 2
    prediction.update_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_heat_pump_route_passes_typed_native_weather_and_map_without_ems_access(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[ThermalHeatPlanningRequest] = []

    def solve(data: ThermalHeatPlanningRequest, **kwargs) -> ThermalHeatPlanningSolution:
        received.append(data)
        assert isinstance(data.weather[0], WeatherDataRecord)
        assert data.weather[0].weather_relative_humidity == 80.0
        assert data.heat_pump_profile.cop_points[0].cop == 3.0
        return ThermalHeatPlanningSolution(status="limit", reason="typed generic request")

    monkeypatch.setattr(api, "optimize_heat_pump_shadow", solve)
    monkeypatch.setattr(coreabc, "get_config", Mock(side_effect=AssertionError("no config read")))
    monkeypatch.setattr(coreabc, "get_ems", Mock(side_effect=AssertionError("no EMS access")))
    run = AsyncMock(side_effect=AssertionError("no EMS run"))
    monkeypatch.setattr(EnergyManagement, "run", run)
    original = heat_pump_payload()
    before = deepcopy(original)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/v1/optimize/thermal/heatpump/shadow", json=original)
    assert response.status_code == 200, response.text
    assert response.json()["reason"] == "typed generic request"
    assert response.json()["shadow_only"] is True
    assert response.json()["execution_allowed"] is False
    assert len(received) == 1
    assert original == before
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_heat_pump_route_returns_evaluated_source_horizon(app: FastAPI) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/optimize/thermal/heatpump/shadow", json=heat_pump_payload()
        )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] in ("optimal", "feasible"), result
    assert result["execution_allowed"] is False
    assert len(result["source_horizon"]["performance"]) == 4
    assert result["source_horizon"]["performance"][0]["cop"] == 3.0
    assert result["source_horizon"]["performance"][0]["max_thermal_power_w"] == 6000.0
    assert result["reference_thermal_power_w"] == pytest.approx(
        [3.0 * value for value in result["reference_heat_power_w"]]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,expected",
    [
        ("off", 403),
        ("control", 403),
        ("disabled", 403),
        ("zone", 403),
        ("old_issue", 422),
        ("future_issue", 422),
        ("old_observation", 422),
        ("future_observation", 422),
        ("lease", 422),
        ("missing_lock", 503),
    ],
)
async def test_heat_pump_route_has_same_admission_guards(
    monkeypatch: pytest.MonkeyPatch, kind: str, expected: int
) -> None:
    monkeypatch.setattr(api, "utc_now", lambda: NOW)
    declared = settings(
        mode=kind if kind in ("off", "control") else "shadow", enabled=kind != "disabled"
    )
    application = thermalshadow.create_thermal_shadow_app(declared)
    solve = Mock(side_effect=AssertionError("unadmitted request must not run"))
    monkeypatch.setattr(api, "optimize_heat_pump_shadow", solve)
    data = heat_pump_payload()
    if kind == "zone":
        data["zone_id"] = "unknown"
    elif kind == "old_issue":
        data["issued_at"] = (NOW - timedelta(seconds=1801)).isoformat()
    elif kind == "future_issue":
        data["issued_at"] = (NOW + timedelta(seconds=31)).isoformat()
    elif kind == "old_observation":
        data["state"]["based_on_observed_at"] = (NOW - timedelta(seconds=181)).isoformat()
    elif kind == "future_observation":
        data["state"]["based_on_observed_at"] = (NOW + timedelta(seconds=31)).isoformat()
    elif kind == "lease":
        data["plan_ttl_seconds"] = 1801
    elif kind == "missing_lock":
        del application.state.thermal_solve_lock
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post("/v1/optimize/thermal/heatpump/shadow", json=data)
    assert response.status_code == expected, response.text
    solve.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("first_generic", [False, True])
async def test_heat_pump_and_legacy_routes_share_one_lock_without_blocking_other_apps(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, first_generic: bool
) -> None:
    started, finish = Event(), Event()

    def legacy(data: ThermalShadowRequest, **kwargs) -> ThermalShadowSolution:
        if data.revision == 7:
            started.set()
            if not finish.wait(timeout=5):
                raise AssertionError("test release did not arrive")
        return ThermalShadowSolution(status="limit", reason="legacy")

    def generic(data: ThermalHeatPlanningRequest, **kwargs) -> ThermalHeatPlanningSolution:
        if data.revision == 7:
            started.set()
            if not finish.wait(timeout=5):
                raise AssertionError("test release did not arrive")
        return ThermalHeatPlanningSolution(status="limit", reason="generic")

    monkeypatch.setattr(api, "optimize_thermal_shadow", legacy)
    monkeypatch.setattr(api, "optimize_heat_pump_shadow", generic)
    first_route = (
        "/v1/optimize/thermal/heatpump/shadow" if first_generic else "/v1/optimize/thermal/shadow"
    )
    second_route = (
        "/v1/optimize/thermal/shadow" if first_generic else "/v1/optimize/thermal/heatpump/shadow"
    )
    first_data = heat_pump_payload() if first_generic else payload()
    second_data = payload() if first_generic else heat_pump_payload()
    second_data["revision"] = 8
    separate = thermalshadow.create_thermal_shadow_app(settings())
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=separate), base_url="http://other"
        ) as other,
    ):
        task = asyncio.create_task(client.post(first_route, json=first_data))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            denied = await client.post(second_route, json=second_data)
            assert denied.status_code == 429
            independent = await other.post(second_route, json=second_data)
            assert independent.status_code == 200
        finally:
            finish.set()
        accepted = await task
        released = await client.post(second_route, json=second_data)
    assert accepted.status_code == 200
    assert released.status_code == 200


@pytest.mark.asyncio
async def test_heat_pump_error_releases_shared_lock_for_legacy(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "optimize_heat_pump_shadow", Mock(side_effect=ValueError("bad map")))
    monkeypatch.setattr(
        api,
        "optimize_thermal_shadow",
        Mock(return_value=ThermalShadowSolution(status="limit", reason="bounded test")),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        failed = await client.post("/v1/optimize/thermal/heatpump/shadow", json=heat_pump_payload())
        released = await client.post("/v1/optimize/thermal/shadow", json=payload())
    assert failed.status_code == 422
    assert failed.json()["detail"] == "bad map"
    assert released.status_code == 200


def test_openapi_exposes_generic_native_request_and_separate_response(app: FastAPI) -> None:
    schema = app.openapi()
    route = schema["paths"]["/v1/optimize/thermal/heatpump/shadow"]["post"]
    assert route["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ThermalHeatPlanningRequest"
    )
    assert route["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ThermalHeatPlanningSolution"
    )
    assert "WeatherDataRecord" in schema["components"]["schemas"]
    assert "HeatPumpProfile" in schema["components"]["schemas"]
