"""Behavioral tests for shared-grid multi-device optimization."""

# ruff: noqa: S101 -- pytest assertions are the test contract.

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from scipy.optimize import milp

from akkudoktoreos.optimization.fleet import FleetRequest, optimize_fleet
from akkudoktoreos.server.rest.fleet import router


def request(**updates):
    data = dict(
        start_datetime="2026-11-04T00:00:00+01:00",
        interval_seconds=3600,
        load_w=[0, 0],
        pv_w=[0, 0],
        import_price_eur_per_kwh=[0.3, 0.3],
        export_price_eur_per_kwh=[0.1, 0.1],
        import_limit_w=10000,
        export_limit_w=10000,
    )
    data.update(updates)
    return FleetRequest.model_validate(data)


def battery(device_id="bat", **updates):
    data = dict(
        device_id=device_id,
        kind="battery",
        capacity_wh=1000,
        initial_energy_wh=0,
        max_charge_power_w=1000,
        max_discharge_power_w=1000,
    )
    data.update(updates)
    return data


def test_store_clipped_pv_instead_of_earlier_exportable_pv():
    req = request(pv_w=[1000, 2000], export_limit_w=1000, devices=[battery(target_energy_wh=1000)])
    sol = optimize_fleet(req)
    assert sol.status == "optimal"
    assert sol.devices["bat"].charge_w == pytest.approx([0, 1000])
    assert sol.export_w == pytest.approx([1000, 1000])
    assert sol.curtailed_pv_w == pytest.approx([0, 0])
    assert sol.grid_cost_eur == pytest.approx(-0.2)


def test_export_only_pays_for_energy_within_limit():
    sol = optimize_fleet(request(pv_w=[2000, 3000], export_limit_w=1000))
    assert sol.curtailed_pv_w == pytest.approx([1000, 2000])
    assert sol.grid_cost_eur == pytest.approx(-0.2)


def test_two_evs_share_grid_and_keep_individual_targets():
    evs = [
        battery(
            str(i),
            kind="ev",
            max_discharge_power_w=0,
            target_energy_wh=1000,
            charge_levels_w=[0, 1000],
        )
        for i in range(2)
    ]
    sol = optimize_fleet(request(import_limit_w=1000, devices=evs))
    assert sol.status == "optimal"
    assert sol.import_w == pytest.approx([1000, 1000])
    for plan in sol.devices.values():
        assert plan.energy_wh[-1] == pytest.approx(1000)
        assert sorted(plan.charge_w) == pytest.approx([0, 1000])


def test_ev_availability_and_departure():
    ev = battery(
        kind="ev",
        max_discharge_power_w=0,
        available=[True, False],
        target_energy_wh=1000,
        target_slot=1,
    )
    sol = optimize_fleet(request(devices=[ev], import_price_eur_per_kwh=[1, 0]))
    assert sol.devices["bat"].charge_w == pytest.approx([1000, 0])


@pytest.mark.parametrize("interval", [900, 3600])
def test_multiple_storage_efficiencies_and_balance(interval):
    req = request(
        interval_seconds=interval,
        load_w=[1000, 1000],
        devices=[
            battery("a", initial_energy_wh=1000, discharge_efficiency=0.8),
            battery("b", initial_energy_wh=1000, discharge_efficiency=0.9),
        ],
    )
    sol = optimize_fleet(req)
    assert sol.status == "optimal"
    for t in range(2):
        net = sol.import_w[t] - sol.export_w[t] + req.pv_w[t] - sol.curtailed_pv_w[t]
        for d in req.devices:
            p = sol.devices[d.device_id]
            net += p.discharge_w[t] - p.charge_w[t]
            assert p.energy_wh[t + 1] - p.energy_wh[t] == pytest.approx(
                interval
                / 3600
                * (p.charge_w[t] * d.charge_efficiency - p.discharge_w[t] / d.discharge_efficiency)
            )
        assert net == pytest.approx(req.load_w[t])


def test_initial_energy_below_reserve_is_not_created_or_discharged():
    sol = optimize_fleet(
        request(load_w=[1000, 1000], devices=[battery(initial_energy_wh=50, reserve_wh=200)])
    )
    assert sol.devices["bat"].energy_wh == pytest.approx([50, 50, 50])
    assert sol.import_w == pytest.approx([1000, 1000])


def test_can_recharge_from_below_reserve():
    sol = optimize_fleet(
        request(pv_w=[1000, 0], load_w=[0, 1000], devices=[battery(reserve_wh=200)])
    )
    assert sol.devices["bat"].energy_wh == pytest.approx([0, 1000, 200])
    assert sol.import_w == pytest.approx([0, 200])


def test_infeasible_base_load_is_not_clipped():
    sol = optimize_fleet(request(load_w=[2000, 0], import_limit_w=1000))
    assert sol.status == "infeasible"
    assert sol.import_w == []
    assert sol.devices == {}


def test_impossible_discrete_ev_target_is_reported():
    ev = battery(
        kind="ev", max_discharge_power_w=0, target_energy_wh=1000, charge_levels_w=[0, 1000]
    )
    assert optimize_fleet(request(import_limit_w=500, devices=[ev])).status == "infeasible"


def test_shared_converter_cap():
    req = request(
        devices=[
            battery("a", target_energy_wh=1000, power_group="g"),
            battery("b", target_energy_wh=1000, power_group="g"),
        ],
        power_groups=[dict(group_id="g", max_charge_power_w=1000, max_discharge_power_w=1000)],
    )
    assert optimize_fleet(req).import_w == pytest.approx([1000, 1000])


@pytest.mark.parametrize("enabled", [False, True])
def test_storage_export_policy_with_concurrent_pv(enabled):
    sol = optimize_fleet(request(
        load_w=[1000, 0], pv_w=[1000, 0], export_price_eur_per_kwh=[2, 2],
        devices=[battery(initial_energy_wh=1000)], allow_battery_export=enabled,
    ))
    assert sol.status == "optimal"
    if enabled:
        assert sum(sol.export_w) >= 1000 - 1e-5
    else:
        assert sol.export_w == pytest.approx([0, 0])


def test_max_energy_and_minimum_power_constraints():
    sol = optimize_fleet(request(
        pv_w=[1000, 1000], export_limit_w=0,
        devices=[battery(max_energy_wh=500, min_charge_power_w=600)],
    ))
    assert sol.status == "optimal"
    assert sol.devices["bat"].charge_w == pytest.approx([0, 0])
    assert sol.curtailed_pv_w == pytest.approx([1000, 1000])


def test_negative_prices_do_not_allow_simultaneous_grid_import_export():
    sol = optimize_fleet(
        request(import_price_eur_per_kwh=[-1, -1], export_price_eur_per_kwh=[1, 1])
    )
    assert sol.import_w == pytest.approx([0, 0])
    assert sol.export_w == pytest.approx([0, 0])


def test_negative_prices_do_not_allow_same_battery_charge_discharge():
    sol = optimize_fleet(
        request(
            import_price_eur_per_kwh=[-1, -1],
            devices=[battery(charge_efficiency=0.8, discharge_efficiency=0.8)],
        )
    )
    for c, d in zip(sol.devices["bat"].charge_w, sol.devices["bat"].discharge_w):
        assert min(c, d) == pytest.approx(0)


@pytest.mark.parametrize(
    "updates",
    [
        dict(pv_w=[1]),
        dict(load_w=[-1, 0]),
        dict(import_limit_w=-1),
        dict(pv_w=[float("nan"), 0]),
        dict(import_price_eur_per_kwh=[float("inf"), 0]),
        dict(devices=[battery(), battery()]),
        dict(devices=[battery(initial_energy_wh=2000)]),
        dict(devices=[battery(target_slot=3)]),
        dict(devices=[battery(available=[True])]),
        dict(devices=[battery(power_group="unknown")]),
        dict(devices=[battery(charge_levels_w=[-1, 0])]),
        dict(devices=[battery(kind="ev")]),
        dict(start_datetime="2026-11-04T00:00:00"),
    ],
)
def test_invalid_inputs(updates):
    with pytest.raises(ValidationError):
        request(**updates)


def test_timeout_never_exposes_unverified_plan():
    with patch(
        "scipy.optimize.milp",
        return_value=SimpleNamespace(status=1, message="Time limit", x=None),
    ):
        sol = optimize_fleet(request())
    assert sol.status == "limit"
    assert sol.import_w == []


@pytest.mark.parametrize("corruption", [None, "bound", "nonfinite"])
def test_timeout_incumbent_is_verified(corruption):
    def limited_solver(*args, **kwargs):
        result = milp(*args, **kwargs)
        result.status = 1
        assert result.x is not None
        if corruption == "bound":
            result.x[0] = 10001
        elif corruption == "nonfinite":
            result.x[0] = np.nan
        return result

    with patch("scipy.optimize.milp", side_effect=limited_solver):
        sol = optimize_fleet(request(load_w=[1000, 1000]))
    if corruption:
        assert sol.status == "error"
        assert sol.import_w == []
    else:
        assert sol.status == "feasible"
        assert sol.import_w == pytest.approx([1000, 1000])


def test_api_without_running_eos_server():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post(
            "/v1/optimize/fleet",
            json=request(pv_w=[2000, 0], export_limit_w=1000).model_dump(mode="json"),
        )
        assert response.status_code == 200
        assert response.json()["curtailed_pv_w"] == pytest.approx([1000, 0])
        assert client.post("/v1/optimize/fleet", json={}).status_code == 422


def test_day_plan_two_cars_two_batteries():
    n = 96
    req = request(
        interval_seconds=900,
        load_w=[700] * n,
        pv_w=[max(0, 6000 * np.sin((t - 24) * np.pi / 48)) if t < 72 else 0 for t in range(n)],
        import_price_eur_per_kwh=[0.3] * n,
        export_price_eur_per_kwh=[0.08] * n,
        import_limit_w=7000,
        export_limit_w=3000,
        devices=[
            battery(
                "house1",
                capacity_wh=8000,
                initial_energy_wh=1000,
                reserve_wh=500,
                max_charge_power_w=3000,
                max_discharge_power_w=3000,
                charge_efficiency=0.95,
                discharge_efficiency=0.95,
            ),
            battery(
                "house2",
                capacity_wh=5000,
                initial_energy_wh=500,
                reserve_wh=500,
                max_charge_power_w=2000,
                max_discharge_power_w=2000,
                charge_efficiency=0.9,
                discharge_efficiency=0.9,
            ),
            battery(
                "car1",
                kind="ev",
                capacity_wh=50000,
                initial_energy_wh=20000,
                target_energy_wh=30000,
                target_slot=80,
                max_charge_power_w=4000,
                max_discharge_power_w=0,
                charge_levels_w=[0, 2000, 4000],
                available=[True] * 80 + [False] * 16,
            ),
            battery(
                "car2",
                kind="ev",
                capacity_wh=40000,
                initial_energy_wh=15000,
                target_energy_wh=20000,
                target_slot=96,
                max_charge_power_w=3000,
                max_discharge_power_w=0,
                charge_levels_w=[0, 1500, 3000],
            ),
        ],
    )
    sol = optimize_fleet(req)
    assert sol.status in ("optimal", "feasible"), sol.message
    for t in range(n):
        assert -1e-5 <= sol.import_w[t] <= req.import_limit_w + 1e-5
        assert -1e-5 <= sol.export_w[t] <= req.export_limit_w + 1e-5
        net = sol.import_w[t] - sol.export_w[t] + req.pv_w[t] - sol.curtailed_pv_w[t]
        for d in req.devices:
            plan = sol.devices[d.device_id]
            net += plan.discharge_w[t] - plan.charge_w[t]
            assert plan.energy_wh[t + 1] - plan.energy_wh[t] == pytest.approx(
                0.25
                * (
                    plan.charge_w[t] * d.charge_efficiency
                    - plan.discharge_w[t] / d.discharge_efficiency
                ),
                abs=1e-5,
            )
        assert net == pytest.approx(req.load_w[t], abs=1e-5)
    assert sol.devices["car1"].energy_wh[80] >= 30000 - 1e-5
    assert sol.devices["car2"].energy_wh[96] >= 20000 - 1e-5
