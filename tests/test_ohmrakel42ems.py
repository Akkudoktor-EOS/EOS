"""Run OHMRAKEL42 through EMS, its REST entry points and its adapters."""

import time
from collections.abc import Mapping
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi.testclient import TestClient
from test_requestfixtures import (  # noqa: F401  (fake_measurement_fixture registers the fixture)
    BATTERY,
    DISHWASHER,
    INVERTER,
    VEHICLE,
    fake_measurement_fixture,
    forecast_series,
)
from test_planningrequestdevices import MANY_DEVICES
from test_planningrequestthermal import DEVICES as THERMAL_DEVICES
from test_planningrequestthermal import NOW as THERMAL_NOW
from test_planningrequestthermal import (
    THERMAL,
    stale_power,
    thermal_world_fixture,  # noqa: F401
)

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core import ems as ems_module
from akkudoktoreos.core.emplan import EnergyManagementPlan
from akkudoktoreos.core.ems import EnergyManagement, EnergyManagementStage
from akkudoktoreos.core.emsettings import EnergyManagementMode
from akkudoktoreos.optimization.request import PlanningRequest, RuntimeForecasts
from akkudoktoreos.optimization.solutionmodel import Ohmrakel42Solution
from akkudoktoreos.utils.datetimeutil import to_datetime

START = to_datetime("2026-09-16T10:00:00+02:00", in_timezone="Europe/Berlin")
SIMPLE_DEVICES: dict[str, Any] = {
    "batteries": {"bat1": BATTERY},
    "electric_vehicles": {"ev1": VEHICLE},
    "inverters": {"inv1": INVERTER},
    "home_appliances": {"dishwasher": DISHWASHER},
}
SIMPLE_SOC = {"bat1": 55, "ev1": 10}
NO_PV_SWITCH = {
    key: {**value, "pv_charge_switchable": False}
    for key, value in MANY_DEVICES["batteries"].items()
}


@pytest.fixture
def ems_world(config_eos: ConfigEOS, monkeypatch: pytest.MonkeyPatch, fake_measurement: Any) -> Any:
    """A clean energy management without prediction or adapter input/output."""
    cls = EnergyManagement
    for name in (
        "_start_datetime",
        "_observation_datetime",
        "_last_run_datetime",
        "_plan",
        "_optimization_solution",
        "_planning_outcome",
        "_ohmrakel42_solution",
        "_ohmrakel42_warm_start",
    ):
        monkeypatch.setattr(cls, name, None)
    monkeypatch.setattr(cls, "_thermal_plans", [])
    monkeypatch.setattr(cls, "_stage", EnergyManagementStage.IDLE)
    monkeypatch.setattr(cls, "prediction", Mock(update_data=AsyncMock()))
    monkeypatch.setattr(cls, "adapter", Mock(update_data=AsyncMock()))
    monkeypatch.setattr(ems_module, "CacheEnergyManagementStore", Mock())
    return cls()


def configure(
    config: ConfigEOS,
    devices: dict[str, Any],
    *,
    interval: int = 3600,
    horizon: int = 24,
    extra: Any = None,
) -> None:
    settings: dict[str, Any] = {
        "prediction": {"hours": 48},
        "optimization": {
            "horizon_hours": horizon,
            "tail_horizon_hours": 0,
            "terminal_value_mode": "FIXED",
            "interval_sec": interval,
        },
        "devices": devices,
    }
    settings.update(extra or {})
    config.merge_settings_from_dict(settings)


def request_for(interval: int, soc: Mapping[str, float], **kwargs: Any) -> PlanningRequest:
    series = forecast_series("optimize_input_1.json", interval)
    return PlanningRequest(soc=dict(soc), forecasts=RuntimeForecasts(**series), **kwargs)


@pytest.mark.asyncio
async def test_two_batteries_two_vehicles_and_a_heat_pump(
    ems_world: EnergyManagement, config_eos: ConfigEOS, thermal_world: dict[str, Any]
) -> None:
    devices = {
        **MANY_DEVICES,
        "batteries": NO_PV_SWITCH,
        "heat_sources": THERMAL_DEVICES["heat_sources"],
    }
    configure(config_eos, devices, extra=THERMAL)
    soc = {"bat1": 40, "bat2": 70, "ev1": 20, "ev2": 30}
    begun = time.perf_counter()
    solution = await ems_world.run(
        start_datetime=THERMAL_NOW,
        mode=EnergyManagementMode.OPTIMIZATION,
        ohmrakel42_request=request_for(3600, soc),
    )
    seconds = time.perf_counter() - begun
    print(f"E2E 2 batteries, 2 vehicles, heat pump, 2 appliances: {seconds:.1f} s")
    assert isinstance(solution, Ohmrakel42Solution)
    assert solution.battery_ids == ["bat1", "bat2"] and solution.thermal_ids == ["hp1"]
    plan = ems_world.plan()
    assert plan is not None
    assert {i.resource_id for i in plan.instructions} == {
        "bat1",
        "bat2",
        "ev1",
        "ev2",
        "dishwasher",
        "washer",
    }
    thermal = ems_world.thermal_plans()
    assert len(thermal) == 1 and thermal[0].zone_id == "living"
    # The search checks its plan against the allowed values; the solution carries no violation
    # and no penalty for the comfort band.
    assert solution.objective["comfort_penalty_eur"] == pytest.approx(0.0, abs=1e-6)
    assert solution.objective["total_eur"] <= solution.start_objective_eur + 1e-9
    frame = ems_world.optimization_solution().solution.to_dataframe()  # type: ignore[union-attr]
    assert "bat2_soc_factor" in frame.columns and "ev2_soc_factor" in frame.columns


@pytest.mark.asyncio
async def test_a_stale_heat_pump_measurement_does_not_stop_the_electrical_planning(
    ems_world: EnergyManagement, config_eos: ConfigEOS, thermal_world: dict[str, Any]
) -> None:
    devices = {
        **MANY_DEVICES,
        "batteries": NO_PV_SWITCH,
        "heat_sources": THERMAL_DEVICES["heat_sources"],
    }
    configure(config_eos, devices, extra=stale_power(thermal_world))
    soc = {"bat1": 40, "bat2": 70, "ev1": 20, "ev2": 30}
    solution = await ems_world.run(
        start_datetime=THERMAL_NOW,
        mode=EnergyManagementMode.OPTIMIZATION,
        ohmrakel42_request=request_for(3600, soc),
    )
    assert isinstance(solution, Ohmrakel42Solution)
    assert solution.battery_ids == ["bat1", "bat2"] and solution.thermal_ids == []
    assert [(w.code, w.devices) for w in solution.warnings] == [("thermal_not_planned", ["hp1"])]
    assert "electric power of hp1" in solution.warnings[0].message
    plan = ems_world.plan()
    assert plan is not None
    assert {"bat1", "bat2", "ev1", "ev2"} <= {i.resource_id for i in plan.instructions}
    assert ems_world.thermal_plans() == []


@pytest.mark.asyncio
async def test_a_second_run_starts_from_the_first_plan(
    ems_world: EnergyManagement, config_eos: ConfigEOS
) -> None:
    configure(config_eos, SIMPLE_DEVICES, interval=900)

    async def run(start: Any) -> Ohmrakel42Solution:
        solution = await ems_world.run(
            start_datetime=start,
            mode=EnergyManagementMode.OPTIMIZATION,
            ohmrakel42_request=request_for(900, SIMPLE_SOC),
        )
        assert isinstance(solution, Ohmrakel42Solution)
        return solution

    first = await run(START)
    assert not first.warm_started
    later = START.add(minutes=15)
    warm = await run(later)
    assert warm.warm_started
    assert warm.objective["total_eur"] <= warm.start_objective_eur + 1e-9
    # The same run from scratch: the warm start is checked against the cold search, so it is
    # not worse (beyond the margin that keeps the plan calm) and costs about as much time.
    assert warm.statistics["winner"] in ("warm", "cold")
    EnergyManagement._ohmrakel42_warm_start = None
    cold = await run(later)
    assert not cold.warm_started and cold.statistics["winner"] == "cold"
    assert warm.statistics["seconds"] <= cold.statistics["seconds"] * 2.0 + 0.5
    assert warm.objective["total_eur"] <= cold.objective["total_eur"] + 0.01 + 1e-9
    # The plan stays consistent: the vehicle charges the same amount over the horizon.
    assert sum(warm.controls["ev1_charge"]) == pytest.approx(
        sum(cold.controls["ev1_charge"]), rel=0.5, abs=8.0
    )


@pytest.mark.asyncio
async def test_a_changed_device_is_planned_first_and_unknown_ones_are_errors(
    ems_world: EnergyManagement, config_eos: ConfigEOS
) -> None:
    configure(config_eos, SIMPLE_DEVICES)

    async def run(**kwargs: Any) -> Any:
        return await ems_world.run(
            start_datetime=START,
            mode=EnergyManagementMode.OPTIMIZATION,
            ohmrakel42_request=request_for(3600, SIMPLE_SOC),
            **kwargs,
        )

    first = await run()
    event = await run(changed_devices=["ev1"])
    assert event.warm_started
    assert event.objective["total_eur"] <= first.objective["total_eur"] + 1e-6
    with pytest.raises(ValueError, match="Unknown devices in changed_devices: \\['ghost'\\]"):
        await run(changed_devices=["ghost"])
    # The failed run left the energy management usable and the last solution in place.
    assert EnergyManagement.stage() == EnergyManagementStage.IDLE
    assert ems_world.ohmrakel42_solution() is event
    assert await run() is not None


@pytest.mark.asyncio
async def test_a_failed_planning_raises_and_publishes_nothing(
    ems_world: EnergyManagement, config_eos: ConfigEOS
) -> None:
    configure(config_eos, {**SIMPLE_DEVICES, "inverters": {}})
    with pytest.raises(ValueError, match="Configure an inverter"):
        await ems_world.run(
            start_datetime=START,
            mode=EnergyManagementMode.OPTIMIZATION,
            ohmrakel42_request=request_for(3600, SIMPLE_SOC),
        )
    assert ems_world.ohmrakel42_solution() is None and ems_world.plan() is None
    assert EnergyManagement.stage() == EnergyManagementStage.IDLE


@pytest.mark.asyncio
async def test_the_endpoints_deliver_the_types(
    ems_world: EnergyManagement, config_eos: ConfigEOS, monkeypatch: pytest.MonkeyPatch
) -> None:
    from akkudoktoreos.server import eos

    configure(config_eos, SIMPLE_DEVICES)
    client = TestClient(eos.app)
    notify = Mock()
    monkeypatch.setattr(ems_module, "notify_optimization_completed", notify)
    # Before any run there is nothing to deliver.
    assert client.get("/v1/energy-management/optimization/solution/OHMRAKEL42").status_code == 404
    assert client.get("/v1/energy-management/thermal-plan").status_code == 404

    series = forecast_series("optimize_input_1.json", 3600)
    body = {"soc": SIMPLE_SOC, "forecasts": series}
    response = client.post("/v1/energy-management/run", json=body)
    assert response.status_code == 200, response.text
    plan = EnergyManagementPlan.model_validate(response.json())
    assert plan.id.startswith("plan-ohmrakel42@")
    assert {i.resource_id for i in plan.instructions} == {"bat1", "ev1", "dishwasher"}

    solution = client.get("/v1/energy-management/optimization/solution/OHMRAKEL42")
    assert solution.status_code == 200
    native = Ohmrakel42Solution.model_validate(solution.json())
    assert native.battery_ids == ["bat1"] and not native.warm_started
    assert native.id.startswith("ohmrakel42@")
    assert native.energy_management_plan_id == plan.id
    assert notify.call_args.args[1]["algorithm"] == "OHMRAKEL42"

    results = client.get("/v1/energy-management/optimization/solution")
    assert results.status_code == 200
    assert results.json()["id"].startswith("optimization-ohmrakel42@")
    assert native.optimization_solution_id == results.json()["id"]

    former_path = "/v1/energy-management/optimization/solution/DEVICEWISE"
    assert client.get(former_path).status_code == 404
    assert former_path not in eos.app.openapi()["paths"]
    assert client.get("/v1/energy-management/plan").status_code == 200
    assert client.get("/v1/energy-management/thermal-plan").json() == []

    # A second run with an event starts from the first plan.
    again = client.post("/v1/energy-management/run", json={**body, "changed_devices": ["ev1"]})
    assert again.status_code == 200, again.text
    assert client.get("/v1/energy-management/optimization/solution/OHMRAKEL42").json()[
        "warm_started"
    ]

    # Errors answer with the cause: an unknown device, a query override.
    unknown = client.post("/v1/energy-management/run", json={**body, "changed_devices": ["ghost"]})
    assert unknown.status_code == 422 and "Unknown devices" in unknown.text
    assert client.post("/v1/energy-management/run?algorithm=DEVICEWISE", json=body).status_code == 422


@pytest.mark.asyncio
async def test_the_thermal_plan_endpoint(
    ems_world: EnergyManagement, config_eos: ConfigEOS, thermal_world: dict[str, Any]
) -> None:
    from akkudoktoreos.server import eos

    devices = {k: THERMAL_DEVICES[k] for k in THERMAL_DEVICES}
    configure(config_eos, devices, extra=THERMAL)
    await ems_world.run(
        start_datetime=THERMAL_NOW,
        mode=EnergyManagementMode.OPTIMIZATION,
        ohmrakel42_request=request_for(3600, {"bat1": 50}),
    )
    response = TestClient(eos.app).get("/v1/energy-management/thermal-plan")
    assert response.status_code == 200
    plans = response.json()
    assert len(plans) == 1 and plans[0]["zone_id"] == "living"
    assert len(plans[0]["intervals"]) == 24


@pytest.mark.asyncio
@pytest.mark.parametrize("supports_limit, expected", [(False, 1.0), (True, 0.4)])
async def test_the_adapters_read_the_new_solution_without_a_change(
    ems_world: EnergyManagement,
    config_eos: ConfigEOS,
    monkeypatch: pytest.MonkeyPatch,
    supports_limit: bool,
    expected: float,
) -> None:
    from akkudoktoreos.adapter.homeassistant import HomeAssistantAdapter
    from akkudoktoreos.adapter.nodered import NodeREDAdapter

    battery = {
        **BATTERY,
        "adapter_supports_discharge_limit": supports_limit,
        "discharge_rates": [0.4],
    }
    configure(config_eos, {**SIMPLE_DEVICES, "batteries": {"bat1": battery}})
    await ems_world.run(
        start_datetime=START,
        mode=EnergyManagementMode.OPTIMIZATION,
        ohmrakel42_request=request_for(3600, SIMPLE_SOC),
    )
    plan = ems_world.plan()
    solution = ems_world.optimization_solution()
    assert plan is not None and solution is not None
    # What is active at the start of the plan.
    latest = ems_world.ohmrakel42_solution()
    assert latest is not None
    limits = latest.controls["bat1_discharge_limit"]
    assert set(limits) <= {0.0, expected}
    assert expected in limits
    slot = limits.index(expected)
    moment = START.add(hours=slot, minutes=5)
    active = {i.resource_id: i for i in plan.get_active_instructions(moment)}
    assert set(active) == {"bat1", "ev1", "dishwasher"}

    ems = MagicMock()
    ems.stage.return_value = EnergyManagementStage.CONTROL_DISPATCH
    active_plan = MagicMock()
    active_plan.get_active_instructions.return_value = list(active.values())
    ems.plan.return_value = active_plan
    ems.optimization_solution.return_value = solution

    # Node-RED: operation mode and factor per resource.
    config_eos.set_nested_value("adapter/provider", ["NodeRED"])
    node = NodeREDAdapter()
    monkeypatch.setitem(node.__dict__, "ems", ems)
    with (
        patch("requests.post") as post,
    ):
        post.return_value.status_code = 200
        await node.update_data(force_enable=True)
    sent = post.call_args.kwargs["json"]
    for device in ("bat1", "ev1", "dishwasher"):
        assert f"{device}_op_mode" in sent and f"{device}_op_factor" in sent
    assert sent["bat1_op_mode"] == "SELF_CONSUMPTION"
    assert sent["bat1_op_factor"] == expected
    assert sent["dishwasher_op_mode"] in ("RUN", "OFF", "run", "off")

    # Home Assistant: a solution column and the active instruction of a device.
    config_eos.set_nested_value("adapter/provider", ["HomeAssistant"])
    config_eos.merge_settings_from_dict(
        {
            "adapter": {
                "homeassistant": {
                    "solution_entity_ids": ["sensor.eos_bat1_soc_factor"],
                    "device_instruction_entity_ids": ["sensor.eos_bat1", "sensor.eos_ev1"],
                }
            }
        }
    )
    assistant = HomeAssistantAdapter()
    monkeypatch.setitem(assistant.__dict__, "ems", ems)
    states: dict[str, Any] = {}
    with (
        patch.object(
            HomeAssistantAdapter,
            "set_entity_state",
            lambda self, entity, state, attributes=None: states.update(
                {entity: (state, attributes)}
            ),
        ),
        patch("pandas.Timestamp.now", return_value=solution.solution.to_dataframe().index[3]),
    ):
        await assistant.update_data(force_enable=True)
    assert "sensor.eos_bat1_soc_factor" in states
    assert 0.0 <= float(states["sensor.eos_bat1_soc_factor"][0]) <= 1.0
    assert states["sensor.eos_bat1"] == ("self_consumption", {"operation_mode_factor": expected})
    assert states["sensor.eos_ev1"][0] in {"idle", "grid_support_import"}
