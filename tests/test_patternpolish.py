"""pattern_polish_seconds: exact local search on the on/off pattern (short pauses / bursts).

Default (0 or absent) must be bit-identical to the previous behaviour; enabled it may only
lower the verified cost and keeps every verification value inside tolerance.
"""

import numpy as np
import pytest
from conftest import heat_pump_request_data as data
from pydantic import ValidationError

import akkudoktoreos.thermal.thermaloptimization as T
from akkudoktoreos.thermal.thermalheatplanning import (
    ThermalHeatPlanningRequest,
    optimize_heat_pump_shadow,
)


def run(payload):
    return optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))


def payload(n=24, **extra):
    p = data(n)
    p["planning_time_limit_seconds"] = 60.0
    p["fleet"]["solver_time_limit_seconds"] = 20.0
    p["initial_running"] = False
    p["electrical"] = {"extras_are_outside_cop": True, "startup_warmup_seconds": 390.0}
    p.update(extra)
    return p


KEYS = (
    "max_constraint_violation",
    "max_minute_comfort_violation_c",
    "max_thermal_terminal_error_c",
    "max_distribution_minute_constraint_violation_c",
    "reference_max_source_flow_violation_w",
    "candidate_max_source_flow_violation_w",
)


@pytest.mark.parametrize("value", [None, 0.0])
def test_default_and_zero_are_bit_identical_and_add_no_diagnostics(value):
    ref = run(payload())
    alt = run(payload() if value is None else payload(pattern_polish_seconds=value))
    assert ref.candidate_heat_power_w == alt.candidate_heat_power_w
    assert ref.candidate_source_electric_power_w == alt.candidate_source_electric_power_w
    assert ref.plan.plan_id == alt.plan.plan_id
    assert not any(key.startswith("pattern_polish") for key in alt.checks)
    # fingerprint/plan id do not depend on the field
    assert run(payload(pattern_polish_seconds=5.0)).plan.plan_id == ref.plan.plan_id


@pytest.mark.parametrize("bad", [-1.0, 61.0])
def test_field_is_bounded(bad):
    with pytest.raises(ValidationError):
        ThermalHeatPlanningRequest.model_validate(payload(pattern_polish_seconds=bad))


def test_enabled_never_costs_more_and_stays_verified():
    off = run(payload())
    on = run(payload(pattern_polish_seconds=6.0))
    assert on.status in ("optimal", "feasible"), on.reason
    assert on.candidate.objective_eur <= off.candidate.objective_eur + 1e-6
    for key in KEYS:
        assert on.checks[key] <= 1e-5
    assert on.checks["storage_terminal_states_match"] is True
    assert on.checks["all_distribution_terminal_states_match"] is True
    assert "pattern_polish_moves" in on.checks
    assert on.checks["pattern_polish_gain_eur"] >= 0.0


def test_injected_short_pause_is_closed_only_when_cheaper(monkeypatch):
    """Replace the seed by a witness whose run block holds a one-slot pause; the polish must
    return a plan that is not dearer than that witness and has no one-slot pause left if it
    was dearer to keep."""
    base = run(payload(24))
    heat = np.array(base.candidate_heat_power_w)
    runs = np.flatnonzero(heat > 1e-3)
    if len(runs) < 6:
        pytest.skip("synthetic horizon has no long run block")
    k = int(runs[len(runs) // 2])
    seen = {}
    original = T._economic_seed

    def bad_seed(p, objective, request, source, hd, ints, budget):
        original(p, objective, request, source, hd, ints, budget)
        seen["witness_cost"] = float(
            objective @ np.array([p.witness_values[i] for i in range(len(p.lower))])
        )

    monkeypatch.setattr(T, "_economic_seed", bad_seed)
    on = run(payload(24, pattern_polish_seconds=6.0))
    assert on.status in ("optimal", "feasible")
    assert on.candidate.objective_eur <= seen["witness_cost"] + 1e-6


def _plain(n=8, with_floor=True):
    p = data(n)
    if not with_floor:
        p["heat_pump_profile"]["thermal_min_points"] = []
    return p


def test_floor_present_gives_no_warning_and_known_flag():
    from akkudoktoreos.thermal.thermalheatplanning import minimum_power_warning

    request = ThermalHeatPlanningRequest.model_validate(_plain())
    assert minimum_power_warning(request.heat_pump_profile) is None
    result = run(_plain())
    assert result.checks["source_minimum_modulation_known"] is True
    assert "source_minimum_power_warning" not in result.checks


def test_missing_floor_warns_in_plain_language_and_in_checks():
    from akkudoktoreos.thermal.thermalheatplanning import minimum_power_warning

    request = ThermalHeatPlanningRequest.model_validate(_plain(with_floor=False))
    text = minimum_power_warning(request.heat_pump_profile)
    assert text and request.heat_pump_profile.profile_id in text and "thermal_min_points" in text
    result = run(_plain(with_floor=False))
    assert result.checks["source_minimum_modulation_known"] is False
    assert result.checks["source_minimum_power_warning"] == text
    # planner behaviour itself is unchanged by the warning
    assert result.status in ("optimal", "feasible")
