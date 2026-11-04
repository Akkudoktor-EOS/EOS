"""Configuration, forecasts, EMS publication and API contracts for FLEET."""

from asyncio import Lock
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import numpy as np
import pytest

from akkudoktoreos.core.ems import EnergyManagement, EnergyManagementStage
from akkudoktoreos.core.emsettings import EnergyManagementMode
from akkudoktoreos.optimization import fleetrequest
from akkudoktoreos.optimization.fleet import FleetSolution
from akkudoktoreos.optimization.fleetrequest import FleetConfigRequest
from akkudoktoreos.optimization.optimization import OptimizationAlgorithm
from akkudoktoreos.utils.datetimeutil import to_datetime


@pytest.fixture
def configured_fleet(config_eos, monkeypatch):
    config_eos.merge_settings_from_dict(
        {
            "optimization": {
                "algorithm": "FLEET",
                "fleet": {
                    "interval_sec": 900,
                    "horizon_hours": 1,
                    "import_limit_w": 4000,
                    "export_limit_w": 1000,
                    "solver_time_limit_seconds": 5,
                },
            },
            "devices": {
                "max_batteries": 2,
                "max_electric_vehicles": 2,
                "max_inverters": 2,
                "batteries": {
                    key: {
                        "capacity_wh": 2000,
                        "max_charge_power_w": 1000,
                        "charging_efficiency": 1,
                        "discharging_efficiency": 1,
                    }
                    for key in ("bat1", "bat2")
                },
                "electric_vehicles": {
                    key: {
                        "capacity_wh": 4000,
                        "max_charge_power_w": 1000,
                        "charging_efficiency": 1,
                        "discharging_efficiency": 1,
                        "min_soc_percentage": 25,
                        "charge_rates": [0, 1],
                    }
                    for key in ("car1", "car2")
                },
                "inverters": {},
                "home_appliances": {},
            },
        }
    )
    ems = EnergyManagement()
    monkeypatch.setattr(EnergyManagement, "_run_lock", Lock())
    for name in ("_fleet_solution", "_optimization_solution", "_plan"):
        monkeypatch.setattr(EnergyManagement, name, None)
    monkeypatch.setattr(EnergyManagement, "adapter", Mock(update_data=AsyncMock()))
    monkeypatch.setattr(EnergyManagement, "prediction", Mock(update_data=AsyncMock()))
    monkeypatch.setattr(fleetrequest, "get_ems", lambda: ems)
    start = to_datetime("2026-11-04T10:15:00+01:00", in_timezone="Europe/Berlin")
    ems.set_start_datetime(start)
    measurement = Mock(key_to_lists=AsyncMock(return_value=([start], [0.0])))
    monkeypatch.setattr(FleetConfigRequest, "measurement", measurement)
    forecasts = {
        "total_load": [250.0] * 48,
        "pv_forecast_wh": [0.0] * 48,
        "electricity_price_per_wh": [0.0003] * 48,
        "feed_in_tariff_per_wh": [0.0001] * 48,
    }
    data = {"soc": {key: 0.0 for key in ("bat1", "bat2", "car1", "car2")}, "forecasts": forecasts}
    return config_eos, ems, start, measurement, data


@pytest.mark.asyncio
async def test_resolver_preserves_devices_units_and_config(configured_fleet):
    config, _, start, _, data = configured_fleet
    before = config.model_dump_json()
    data["soc"]["bat1"] = 12.5
    request = await FleetConfigRequest.model_validate(data).resolve()
    assert request.start_datetime == start
    assert request.load_w == [1000.0] * 4
    assert request.import_price_eur_per_kwh == [0.3] * 4
    assert [d.device_id for d in request.devices] == ["bat1", "bat2", "car1", "car2"]
    assert request.devices[0].initial_energy_wh == 250
    assert request.devices[2].target_energy_wh == 1000
    assert config.model_dump_json() == before


@pytest.mark.asyncio
async def test_real_ems_publishes_all_devices_and_terminal_idle(configured_fleet):
    _, ems, start, _, data = configured_fleet
    result = await ems.run(
        start_datetime=start.add(minutes=2),
        mode=EnergyManagementMode.OPTIMIZATION,
        fleet_parameters=FleetConfigRequest.model_validate(data),
    )
    assert isinstance(result, FleetSolution) and result.status == "optimal"
    assert result is ems.fleet_solution()
    plan = ems.plan()
    table = ems.optimization_solution().solution.to_dataframe()
    assert len(table) == 4
    for key in data["soc"]:
        assert f"{key}_soc_factor" in table.columns
        actions = [i for i in plan.instructions if i.resource_id == key]
        assert actions[-1].operation_mode_id == "IDLE"
        assert actions[-1].execution_time == start.add(hours=1)
    assert plan.valid_until == start.add(hours=1)
    assert ems.stage() == EnergyManagementStage.IDLE


@pytest.mark.asyncio
async def test_infeasible_run_keeps_last_published_plan(configured_fleet):
    config, ems, start, _, data = configured_fleet
    await ems.run(
        start_datetime=start,
        mode=EnergyManagementMode.OPTIMIZATION,
        fleet_parameters=FleetConfigRequest.model_validate(data),
    )
    before = (ems.plan(), ems.optimization_solution(), ems.fleet_solution())
    config.optimization.fleet.import_limit_w = 0
    result = await ems.run(
        start_datetime=start,
        mode=EnergyManagementMode.OPTIMIZATION,
        fleet_parameters=FleetConfigRequest.model_validate(data),
    )
    assert result.status == "infeasible"
    assert (ems.plan(), ems.optimization_solution(), ems.fleet_solution()) == before
    assert ems.stage() == EnergyManagementStage.IDLE


@pytest.mark.asyncio
async def test_automatic_path_uses_providers_and_fresh_measurements(configured_fleet, monkeypatch):
    _, ems, start, measurement, _ = configured_fleet
    values = {
        "loadforecast_power_w": 1000,
        "pvforecast_ac_power": 0,
        "elecprice_marketprice_wh": 0.0003,
        "feed_in_tariff_wh": 0.0001,
    }

    async def read(*args, key, **kwargs):
        assert kwargs["start_datetime"] == start
        return np.full(4, values[key], dtype=float)

    monkeypatch.setattr(fleetrequest, "bounded_forecast_array", read)
    monkeypatch.setattr(FleetConfigRequest, "prediction", Mock(update_data=AsyncMock()))
    result = await ems.run(start_datetime=start, mode=EnergyManagementMode.OPTIMIZATION)
    assert result.status == "optimal"
    assert result.parameters.load_w == [1000] * 4
    assert result.parameters.import_price_eur_per_kwh == [0.3] * 4
    assert measurement.key_to_lists.await_count == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "date,value",
    [
        ("2026-11-04T10:00:00+01:00", 0.2),
        ("2026-11-04T10:16:00+01:00", 0.2),
        ("2026-11-04T10:15:00+01:00", float("nan")),
    ],
)
async def test_missing_stale_future_invalid_soc_rejected(configured_fleet, date, value):
    _, _, _, measurement, data = configured_fleet
    data["soc"] = {}
    measurement.key_to_lists.return_value = ([to_datetime(date)], [value])
    with pytest.raises(ValueError, match="SoC"):
        await FleetConfigRequest.model_validate(data).resolve()


@pytest.mark.asyncio
async def test_provider_short_coverage_is_not_filled(configured_fleet, monkeypatch):
    _, _, _, _, data = configured_fleet
    data["forecasts"] = {}
    monkeypatch.setattr(FleetConfigRequest, "prediction", Mock(update_data=AsyncMock()))
    monkeypatch.setattr(
        fleetrequest, "bounded_forecast_array", AsyncMock(return_value=np.array([0.0]))
    )
    with pytest.raises(ValueError, match="horizon"):
        await FleetConfigRequest.model_validate(data).resolve()


@pytest.mark.asyncio
@pytest.mark.parametrize("stamp", ["2026-10-25T02:15:00+02:00", "2026-10-25T02:15:00+01:00"])
async def test_dst_midnight_series_slicing(configured_fleet, stamp):
    _, ems, _, _, data = configured_fleet
    start = to_datetime(stamp, in_timezone="Europe/Berlin")
    ems.set_start_datetime(start)
    data["forecasts"]["total_load"] = list(range(48))
    result = await FleetConfigRequest.model_validate(data).resolve()
    offset = int((start.timestamp() - start.start_of("day").timestamp()) / 900)
    assert result.load_w == [4 * x for x in range(offset, offset + 4)]


@pytest.mark.asyncio
async def test_existing_api_routes_fleet_and_retrieves_native_solution(
    configured_fleet, monkeypatch
):
    from akkudoktoreos.server import eos

    _, ems, start, _, data = configured_fleet
    real_run = ems.run

    async def fixed_run(**kwargs):
        return await real_run(start_datetime=start, **kwargs)

    monkeypatch.setattr(
        eos, "get_ems", lambda: SimpleNamespace(run=fixed_run, fleet_solution=ems.fleet_solution)
    )
    # ASGI only, no server/lifespan or hardware startup.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=eos.app), base_url="http://test"
    ) as client:
        response = await client.post("/v1/optimize", json=data)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "optimal"
        latest = await client.get("/v1/energy-management/optimization/solution/FLEET")
        assert latest.status_code == 200
        assert set(latest.json()["devices"]) == set(data["soc"])
        invalid = await client.post("/v1/optimize", json={**data, "start_solution": [0]})
        assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_inverter_conversion_limits_and_disabled_ac_charge(configured_fleet):
    config, _, _, _, data = configured_fleet
    config.merge_settings_from_dict(
        {
            "devices": {
                "inverters": {
                    "inv": {
                        "battery_id": "bat1",
                        "max_power_w": 500,
                        "ac_to_dc_efficiency": 0.8,
                        "dc_to_ac_efficiency": 0.9,
                        "max_ac_charge_power_w": 600,
                    }
                }
            }
        }
    )
    resolved = await FleetConfigRequest.model_validate(data).resolve()
    bat = resolved.devices[0]
    assert bat.max_charge_power_w == 600
    assert bat.max_discharge_power_w == 500
    assert bat.charge_efficiency == 0.8
    assert bat.discharge_efficiency == 0.9
    config.devices.inverters["inv"].ac_to_dc_efficiency = 0
    resolved = await FleetConfigRequest.model_validate(data).resolve()
    assert resolved.devices[0].max_charge_power_w == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [False, True])
async def test_fleet_dispatch_does_not_invoke_genetic(configured_fleet, monkeypatch, explicit):
    from akkudoktoreos.core import ems as ems_module
    config, ems, start, _, data = configured_fleet
    genetic = Mock(side_effect=AssertionError("GENETIC must not run"))
    monkeypatch.setattr(ems_module, "GeneticOptimization", genetic)
    if explicit:
        config.optimization.algorithm = OptimizationAlgorithm.GENETIC
    result = await ems.run(start_datetime=start, mode=EnergyManagementMode.OPTIMIZATION,
        algorithm=OptimizationAlgorithm.FLEET if explicit else None,
        fleet_parameters=FleetConfigRequest.model_validate(data))
    assert result.status == "optimal"
    genetic.assert_not_called()


@pytest.mark.asyncio
async def test_shared_group_settings_resolve_and_roundtrip(configured_fleet):
    from akkudoktoreos.optimization.fleetsettings import FleetCommonSettings
    config, _, _, _, data = configured_fleet
    config.merge_settings_from_dict({"optimization": {"fleet": {
        "devices": {"car1": {"power_group": "cars"}, "car2": {"power_group": "cars"}},
        "power_groups": {"cars": {"max_charge_power_w": 1500, "max_discharge_power_w": 0}}
    }}})
    settings = config.optimization.fleet
    assert FleetCommonSettings.model_validate_json(settings.model_dump_json()).model_dump() == settings.model_dump()
    resolved = await FleetConfigRequest.model_validate(data).resolve()
    assert resolved.power_groups[0].max_charge_power_w == 1500
    assert resolved.devices[2].power_group == "cars"
