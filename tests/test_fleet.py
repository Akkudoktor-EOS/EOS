"""Behavioral tests for shared-grid multi-device optimization."""

# ruff: noqa: S101 -- pytest assertions are the test contract.

import datetime
import sys
import time
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


def test_export_prohibition_is_hard_even_with_positive_export_revenue():
    sol = optimize_fleet(request(pv_w=[2000, 2000], export_allowed=[False, True]))
    assert sol.status == "optimal"
    assert sol.export_w == pytest.approx([0, 2000])
    assert sol.curtailed_pv_w == pytest.approx([2000, 0])
    assert sol.grid_cost_eur == pytest.approx(-0.2)


def test_store_pv_during_export_prohibition():
    sol = optimize_fleet(
        request(
            pv_w=[1000, 1000],
            export_allowed=[False, True],
            devices=[battery(target_energy_wh=1000)],
        )
    )
    assert sol.status == "optimal"
    assert sol.devices["bat"].charge_w == pytest.approx([1000, 0])
    assert sol.export_w == pytest.approx([0, 1000])


def test_export_prohibition_requires_complete_horizon():
    with pytest.raises(ValidationError, match="Export availability"):
        request(export_allowed=[False])


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
    sol = optimize_fleet(
        request(
            load_w=[1000, 0],
            pv_w=[1000, 0],
            export_price_eur_per_kwh=[2, 2],
            devices=[battery(initial_energy_wh=1000)],
            allow_battery_export=enabled,
        )
    )
    assert sol.status == "optimal"
    if enabled:
        assert sum(sol.export_w) >= 1000 - 1e-5
    else:
        assert sol.export_w == pytest.approx([0, 0])


def test_max_energy_and_minimum_power_constraints():
    sol = optimize_fleet(
        request(
            pv_w=[1000, 1000],
            export_limit_w=0,
            devices=[battery(max_energy_wh=500, min_charge_power_w=600)],
        )
    )
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


def _rolling_request(
    shift=0, *, ev=True, second_battery=True, group=False, initial=None, **updates
):
    """Hourly 12-slot request whose forecasts are a window of one longer curve."""
    t = np.arange(shift, shift + 12) + 6
    first = datetime.datetime.fromisoformat("2026-11-04T00:00:00+01:00")
    initial = initial or {}
    devices = [
        battery(
            "house1",
            capacity_wh=8000,
            initial_energy_wh=initial.get("house1", 3000),
            reserve_wh=500,
            max_charge_power_w=3000,
            max_discharge_power_w=3000,
            charge_efficiency=0.95,
            discharge_efficiency=0.95,
            power_group="g" if group else None,
        )
    ]
    if second_battery:
        devices.append(
            battery(
                "house2",
                capacity_wh=5000,
                initial_energy_wh=initial.get("house2", 1000),
                max_charge_power_w=2000,
                max_discharge_power_w=2000,
                power_group="g" if group else None,
            )
        )
    if ev:
        devices.append(
            battery(
                "car",
                kind="ev",
                capacity_wh=40000,
                initial_energy_wh=initial.get("car", 10000),
                target_energy_wh=20000,
                target_slot=10 - shift,
                max_charge_power_w=4000,
                max_discharge_power_w=0,
                min_charge_power_w=1500,
                charge_levels_w=[0, 2000, 4000],
                available=[True] * (10 - shift) + [False] * (2 + shift),
            )
        )
    data = dict(
        start_datetime=first + datetime.timedelta(hours=shift),
        load_w=[600.0] * 12,
        pv_w=(np.maximum(0, 5000 * np.sin((t % 24 - 6) * np.pi / 12)) * (t % 24 > 6)).tolist(),
        import_price_eur_per_kwh=(0.25 + 0.1 * np.cos(t * np.pi / 12)).tolist(),
        export_price_eur_per_kwh=[0.07] * 12,
        import_limit_w=7000,
        export_limit_w=3000,
        devices=devices,
        solver_time_limit_seconds=10,
    )
    if group:
        data["power_groups"] = [
            dict(group_id="g", max_charge_power_w=4000, max_discharge_power_w=4000)
        ]
    data.update(updates)
    return request(**data)


def _after_one_slot(previous):
    """Initial energies of the next run: what the previous plan reached after its first slot."""
    return {key: plan.energy_wh[1] for key, plan in previous.devices.items()}


class TestFleetWarmStart:
    VARIANTS = [
        pytest.param({}, id="ev-two-batteries"),
        pytest.param({"ev": False, "second_battery": False}, id="single-battery"),
        pytest.param({"ev": False}, id="two-batteries"),
        pytest.param({"group": True, "ev": False}, id="power-group"),
    ]

    def test_without_start_milp_is_called_as_before(self):
        import scipy.optimize

        calls = []
        original = scipy.optimize.milp

        def spy(*args, **kwargs):
            calls.append(kwargs["options"])
            return original(*args, **kwargs)

        req = _rolling_request(ev=False)
        with patch.object(scipy.optimize, "milp", spy):
            plain = optimize_fleet(req)
            explicit = optimize_fleet(req, start=None)
        assert calls == [{"time_limit": 10, "mip_rel_gap": 0.0}] * 2
        assert plain.start_used is False and plain.start_objective_eur is None
        assert explicit.objective_eur == plain.objective_eur
        assert explicit.devices == plain.devices

    @pytest.mark.parametrize("variant", VARIANTS)
    def test_start_from_previous_run_is_used_and_not_worse(self, variant):
        previous = optimize_fleet(_rolling_request(0, **variant))
        assert previous.status in ("optimal", "feasible")
        later = _rolling_request(1, initial=_after_one_slot(previous), **variant)
        plain = optimize_fleet(later)
        seeded = optimize_fleet(later, start=previous)
        assert seeded.status in ("optimal", "feasible")
        assert seeded.start_used is True
        assert seeded.start_objective_eur is not None
        assert seeded.objective_eur <= plain.objective_eur + 1e-6
        assert seeded.objective_eur <= seeded.start_objective_eur + 1e-9

    def test_total_time_stays_close_to_the_limit(self):
        previous = optimize_fleet(_rolling_request(0))
        later = _rolling_request(
            1, initial=_after_one_slot(previous), solver_time_limit_seconds=3
        )
        started = time.monotonic()
        seeded = optimize_fleet(later, start=previous)
        assert time.monotonic() - started < 6
        assert seeded.status in ("optimal", "feasible")

    def test_unusable_start_is_ignored_never_an_error(self):
        previous = optimize_fleet(_rolling_request(0))
        hourly = _rolling_request(1, initial=_after_one_slot(previous))
        no_car = optimize_fleet(_rolling_request(0, ev=False))
        cases = {
            "other device set": (no_car, hourly),
            "other raster": (previous, _rolling_request(1, interval_seconds=900)),
            "start in the future": (previous, _rolling_request(-1)),
            "shift beyond horizon": (no_car, _rolling_request(12, ev=False)),
            "no devices in start": (previous.model_copy(update={"devices": {}}), hourly),
        }
        for name, (start, req) in cases.items():
            plain = optimize_fleet(req)
            seeded = optimize_fleet(req, start=start)
            assert seeded.start_used is False, name
            assert seeded.status == plain.status, name
            assert seeded.objective_eur == plain.objective_eur, name

    def test_start_made_infeasible_by_the_new_request_is_ignored(self):
        previous = optimize_fleet(_rolling_request(0))
        later = _rolling_request(1, initial=_after_one_slot(previous))
        devices = [d.model_dump() for d in later.devices]
        for d in devices:
            if d["device_id"] == "car":
                d["initial_energy_wh"] = 10000
                d["available"] = [True] + [False] * 11  # target no longer reachable
        req = _rolling_request(1, devices=devices)
        plain = optimize_fleet(req)
        seeded = optimize_fleet(req, start=previous)
        assert plain.status == "infeasible"
        assert seeded.status == plain.status
        assert seeded.start_used is False

    @staticmethod
    def _search_result_replaced(monkeypatch, replace):
        """Let the repair LP run for real, but alter the result of the MILP search."""
        import scipy.optimize

        original = scipy.optimize.milp
        calls = []

        def spy(*args, **kwargs):
            result = original(*args, **kwargs)
            is_lp = kwargs["integrality"].sum() == 0
            calls.append((is_lp, kwargs["options"]["time_limit"]))
            return result if is_lp else replace(result)

        monkeypatch.setattr(scipy.optimize, "milp", spy)
        return calls

    def test_no_search_result_returns_the_repaired_start(self, monkeypatch):
        from scipy.optimize import OptimizeResult

        previous = optimize_fleet(_rolling_request(0, group=True, ev=False))
        later = _rolling_request(
            1, group=True, ev=False, initial=_after_one_slot(previous)
        )
        self._search_result_replaced(
            monkeypatch, lambda r: OptimizeResult(status=1, x=None, fun=None, message="limit")
        )
        seeded = optimize_fleet(later, start=previous)
        assert seeded.status == "feasible"
        assert seeded.start_used is True
        assert seeded.relative_gap is None
        assert seeded.objective_eur == pytest.approx(seeded.start_objective_eur)
        net = np.array(later.load_w) - np.array(later.pv_w)
        flow = np.array(seeded.import_w) - np.array(seeded.export_w)
        flow = flow - np.array(seeded.curtailed_pv_w)
        for plan in seeded.devices.values():
            flow = flow + np.array(plan.discharge_w) - np.array(plan.charge_w)
        assert flow == pytest.approx(net, abs=1e-3)

    def test_worse_search_result_is_replaced_by_the_repaired_start(self, monkeypatch):
        previous = optimize_fleet(_rolling_request(0))
        later = _rolling_request(1, initial=_after_one_slot(previous))
        plain = optimize_fleet(later)

        def worse(result):
            from scipy.optimize import OptimizeResult

            changed = OptimizeResult(dict(result))
            changed["fun"] = result.fun + 100.0
            return changed

        self._search_result_replaced(monkeypatch, worse)
        seeded = optimize_fleet(later, start=previous)
        assert seeded.status == "feasible"
        assert seeded.start_used is True
        assert plain.objective_eur - 1e-9 <= seeded.objective_eur <= seeded.start_objective_eur

    def test_better_search_result_is_kept_and_search_keeps_the_full_time(self, monkeypatch):
        previous = optimize_fleet(_rolling_request(0))
        later = _rolling_request(1, initial=_after_one_slot(previous))
        plain = optimize_fleet(later)
        calls = self._search_result_replaced(monkeypatch, lambda r: r)
        seeded = optimize_fleet(later, start=previous)
        assert seeded.status == "optimal"
        assert seeded.start_used is True
        assert seeded.objective_eur == plain.objective_eur
        assert [lp for lp, _ in calls] == [False, True]  # search first, then the repair
        assert calls[0][1] == later.solver_time_limit_seconds
        assert calls[1][1] <= 0.5 * later.solver_time_limit_seconds

    def test_tiny_time_limit_never_errors(self):
        previous = optimize_fleet(_rolling_request(0))
        seeded = optimize_fleet(
            _rolling_request(
                1, initial=_after_one_slot(previous), solver_time_limit_seconds=0.001
            ),
            start=previous,
        )
        assert seeded.status in ("feasible", "limit", "optimal")
        assert seeded.status == "limit" or seeded.objective_eur is not None

    def test_idle_devices_ignores_start(self):
        previous = optimize_fleet(_rolling_request(0, ev=False))
        idle = optimize_fleet(_rolling_request(1, ev=False), idle_devices=True, start=previous)
        assert idle.status == "feasible" and idle.start_used is False
