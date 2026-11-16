"""Tests for the native_fleet_reference fallback (presolve-off retry, idle-device fallback)."""
from datetime import datetime, timezone

import numpy as np
import pytest

import akkudoktoreos.thermal.thermaloptimization as T
from akkudoktoreos.optimization.fleet import (
    FleetDevice,
    FleetRequest,
    FleetSolution,
    optimize_fleet,
)

START = datetime(2026, 11, 12, tzinfo=timezone.utc)


def request(n=8, target=0.0, **kw):
    battery = FleetDevice(device_id="bat", kind="battery", capacity_wh=10000, initial_energy_wh=5000,
                          reserve_wh=1000, max_charge_power_w=3000, min_charge_power_w=300,
                          max_discharge_power_w=3000, charge_efficiency=0.95, discharge_efficiency=0.95,
                          terminal_value_eur_per_kwh=0.2, target_energy_wh=target)
    ev = FleetDevice(device_id="ev", kind="ev", capacity_wh=50000, initial_energy_wh=30000,
                     max_charge_power_w=11000, min_charge_power_w=1400,
                     charge_levels_w=[0, 1400, 3700, 7400, 11000])
    return FleetRequest(start_datetime=START, load_w=[800.0 + 100 * k for k in range(n)],
                        pv_w=[0, 0, 1500, 4000, 4000, 1500, 0, 0][:n] + [0] * max(0, n - 8),
                        import_price_eur_per_kwh=[0.35, 0.2, 0.25, 0.3, 0.3, 0.4, 0.45, 0.3][:n] + [0.3] * max(0, n - 8),
                        export_price_eur_per_kwh=[0.07] * n, import_limit_w=30000, export_limit_w=10000,
                        devices=[battery, ev], solver_time_limit_seconds=10, **kw)


def test_idle_devices_is_verified_feasible_and_unoptimized():
    req = request()
    s = optimize_fleet(req, idle_devices=True)
    assert s.status == "feasible" and s.relative_gap is None and s.objective_eur is not None
    for dev in req.devices:
        plan = s.devices[dev.device_id]
        assert max(plan.charge_w + plan.discharge_w) == 0
        assert plan.energy_wh[-1] == pytest.approx(dev.initial_energy_wh)
    net = np.array(req.load_w) - np.array(req.pv_w)
    assert np.allclose(np.array(s.import_w) - np.array(s.export_w) - np.array(s.curtailed_pv_w), net, atol=1e-5)
    optimal = optimize_fleet(req)
    assert optimal.status == "optimal" and optimal.objective_eur <= s.objective_eur + 1e-9


def test_idle_devices_does_not_invent_unreachable_targets():
    s = optimize_fleet(request(target=8000.0), idle_devices=True)
    assert s.status == "infeasible" and not s.devices


def test_presolve_off_solves_the_identical_model():
    req = request()
    a, b = optimize_fleet(req), optimize_fleet(req, highs_options={"presolve": False})
    assert a.status == b.status == "optimal"
    assert a.objective_eur == pytest.approx(b.objective_eur, abs=1e-6)


def no_incumbent(req):
    return FleetSolution(status="limit", message="no incumbent", start_datetime=req.start_datetime,
                         interval_seconds=req.interval_seconds)


class Fake:
    def __init__(self, fail):
        self.fail, self.calls = fail, []

    def __call__(self, req, **kw):
        self.calls.append((req.solver_time_limit_seconds, kw))
        return no_incumbent(req) if len(self.calls) <= self.fail else optimize_fleet(req, **kw)


@pytest.mark.parametrize("fail,expect", [(0, "retry"), (1, "idle"), (2, "none")])
def test_fallback_chain(monkeypatch, fail, expect):
    fake = Fake(fail)
    monkeypatch.setattr(T, "optimize_fleet", fake)
    budget = T._PlanningBudget(seconds=60)
    req = request()
    out = T._native_fleet_reference_fallback(req, no_incumbent(req), budget)
    names = [d.name for d in budget.diagnostics]
    assert fake.calls[0][1] == {"highs_options": {"presolve": False}}
    assert fake.calls[0][0] <= T._FLEET_RETRY_SECONDS
    if expect == "retry":
        assert out.status == "optimal" and budget.native_fleet_reference_retry_used
        assert not budget.native_fleet_reference_idle_fallback and names == ["native_fleet_reference_retry"]
    elif expect == "idle":
        assert fake.calls[1][1] == {"idle_devices": True}
        assert out.status == "feasible" and out.relative_gap is None
        assert budget.native_fleet_reference_idle_fallback and not budget.native_fleet_reference_retry_used
        assert names == ["native_fleet_reference_retry", "native_fleet_reference_idle_fallback"]
    else:
        assert out.status == "limit" and out.objective_eur is None
        assert not budget.native_fleet_reference_idle_fallback and not budget.native_fleet_reference_retry_used


@pytest.mark.parametrize("status", ["optimal", "feasible", "infeasible", "error"])
def test_no_fallback_for_solutions_or_proven_failures(monkeypatch, status):
    fake = Fake(0)
    monkeypatch.setattr(T, "optimize_fleet", fake)
    req = request()
    given = optimize_fleet(req) if status in ("optimal", "feasible") else FleetSolution(
        status=status, message="x", start_datetime=START, interval_seconds=900)
    if status == "feasible":
        given = given.model_copy(update={"status": "feasible"})
    budget = T._PlanningBudget(seconds=60)
    assert T._native_fleet_reference_fallback(req, given, budget) is given
    assert fake.calls == [] and budget.diagnostics == []


def test_fallback_flags_are_reported():
    budget = T._PlanningBudget(seconds=60)
    budget.native_fleet_reference_idle_fallback = True
    plan_like = T.ThermalShadowSolution(status="optimal", reason="Conditional model comparison",
                                        comparison_qualified=True, savings_objective_eur=1.0)
    out = T._finish_planning(plan_like, budget)
    assert out.checks["native_fleet_reference_idle_fallback"] is True
    assert out.checks["native_fleet_reference_retry_used"] is False
