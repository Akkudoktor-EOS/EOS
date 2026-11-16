"""pattern_polish_seconds: exact local search on the on/off pattern (short pauses / bursts).

Default (0 or absent) must be bit-identical to the previous behaviour; enabled it may only
lower the verified cost and keeps every verification value inside tolerance.
"""

import time
from types import SimpleNamespace

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
    assert not any(key.startswith("pattern_") for key in alt.checks)
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
    assert 0 <= on.checks["pattern_smooth_moves"] <= on.checks["pattern_smooth_attempts"]


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


# --- bundled smoothing moves, tested on a stub program with a scripted cost ----------------

_N = 30
# runs 0-3, 5-9, 21-25, 27-29; pauses 4 (short), 10-20, 26 (short); one-slot run at 15.
_BASE = [True] * 4 + [False] + [True] * 5 + [False] * 5 + [True] + [False] * 5 + [True] * 5
_BASE += [False] + [True] * 3
_COST_INDEX = 2 * _N


class _StubProgram:
    """Minimal stand-in for the MILP program: pinned on/off slots give a scripted cost."""

    def __init__(self, cost_of, delay=0.0):
        self.cost_of = cost_of
        self.delay = delay
        self.on_slots = list(range(_N))
        self.integer = [1] * _N + [0] * (_N + 1)
        self.lower = [0.0] * (2 * _N + 1)
        self.upper = [1.0] * _N + [2000.0] * _N + [100.0]
        self.witness_values = {}
        self.solves = 0

    def solve(self, objective, seconds):
        self.solves += 1
        time.sleep(self.delay)
        on = [self.lower[k] > 0.5 for k in range(_N)]
        x = np.zeros(2 * _N + 1)
        x[:_N] = on
        x[_N : 2 * _N] = np.array(on) * 1000.0
        x[_COST_INDEX] = cost = self.cost_of(on)
        return T._Result(
            x=x, objective=cost, lower_bound=None, gap=None, status="feasible", violation=0.0
        )

    def witness_violation(self, values):
        return 0.0

    def physical_violation(self, values, name):
        return 0.0


def _polish(monkeypatch, cost_of, *, delay=0.0, seconds=30.0, base=_BASE):
    def thermal_program(request, source, witness_heat=None, **kwargs):
        values = {k: float(witness_heat[k] > 0) for k in range(_N)}
        return (SimpleNamespace(witness_values=values),)

    monkeypatch.setattr(T, "_thermal_program", thermal_program)
    program = _StubProgram(cost_of, delay)
    x0 = np.zeros(2 * _N + 1)
    x0[:_N] = base
    x0[_COST_INDEX] = cost_of(base)
    objective = np.zeros(2 * _N + 1)
    objective[_COST_INDEX] = 1.0
    budget = T._PlanningBudget(120.0)
    source = SimpleNamespace(
        fixed_heat_prefix_w=(), max_power_w=(2000.0,) * _N, min_power_w=(500.0,) * _N
    )
    request = SimpleNamespace(pattern_polish_seconds=seconds)
    result = T._pattern_polish(
        program,
        objective,
        request,
        source,
        list(range(_N, 2 * _N)),
        list(range(_N)),
        budget,
        seconds=seconds,
        x0=x0,
        reserve=0.0,
    )
    return result, budget, program


def _short_blocks(on):
    found, i = 0, 0
    while i < len(on):
        j = i
        while j < len(on) and on[j] == on[i]:
            j += 1
        found += int(i > 0 and j < len(on) and j - i <= 2)
        i = j
    return found


def _joint_gain_cost(on):
    """Cheaper only once every short block is gone: no single or paired move can profit."""
    return 8.0 if _short_blocks(on) == 0 else 10.0


def test_smoothing_bundle_is_accepted_after_the_pause_and_run_moves_failed(monkeypatch):
    result, budget, _ = _polish(monkeypatch, _joint_gain_cost)
    assert result is not None
    assert _short_blocks([bool(v > 0.5) for v in result[:_N]]) == 0
    assert budget.pattern_smooth_attempts == 1
    assert budget.pattern_smooth_moves == 1
    assert budget.pattern_polish_moves == 1
    # pause-only and run-only moves first, then one bundle instead of the single moves
    assert budget.pattern_polish_evaluations == 3
    assert budget.pattern_polish_gain_eur == pytest.approx(2.0)


def _single_gain_cost(on):
    """Only closing the first pause pays; any bundle also closes the dearer second pause."""
    return 10.0 - 1.0 * on[4] + 3.0 * on[26]


def test_rejected_bundle_falls_back_to_the_single_moves(monkeypatch):
    result, budget, _ = _polish(monkeypatch, _single_gain_cost)
    assert result is not None
    final = [bool(v > 0.5) for v in result[:_N]]
    assert final[4] and not final[26]  # first pause closed, the dear one left alone
    assert budget.pattern_smooth_attempts == 1 and budget.pattern_smooth_moves == 0
    assert budget.pattern_polish_moves == 1
    assert result[_COST_INDEX] < _single_gain_cost(_BASE)


def test_cheap_pause_and_run_moves_leave_the_bundles_untouched(monkeypatch):
    def pauses_pay(on):
        return 10.0 - 1.0 * (on[4] and on[26])

    result, budget, _ = _polish(monkeypatch, pauses_pay)
    assert result is not None
    assert budget.pattern_smooth_attempts == 0 and budget.pattern_smooth_moves == 0


_BASE_FOUR = [True] * 2 + [False] + [True] * 4 + [False] * 6 + [True] + [False] * 6 + [True] * 4
_BASE_FOUR += [False] + [True] * 2 + [False] * 3  # short blocks at 2, 13, 24 and 25-26


def test_rejected_bundle_is_halved_and_the_front_half_wins(monkeypatch):
    def front_half_pays(on):
        return 10.0 - 1.0 * (on[2] and not on[13]) + 3.0 * (on[24] or not on[25])

    result, budget, _ = _polish(monkeypatch, front_half_pays, base=_BASE_FOUR)
    assert result is not None
    final = [bool(v > 0.5) for v in result[:_N]]
    assert final[2] and not final[13] and not final[24] and final[25]
    # first round: all blocks (rejected), front half (accepted); then the two left-over blocks
    assert budget.pattern_smooth_attempts == 3 and budget.pattern_smooth_moves == 1
    assert budget.pattern_polish_evaluations == 7


def test_bundles_never_exceed_their_attempt_cap(monkeypatch):
    def nothing_pays(on):
        return 10.0

    result, budget, _ = _polish(monkeypatch, nothing_pays)
    assert result is None
    assert 1 <= budget.pattern_smooth_attempts <= T._SMOOTH_MAX_ATTEMPTS
    assert budget.pattern_smooth_moves == 0


def test_polish_with_smoothing_respects_its_time_budget(monkeypatch):
    start = time.monotonic()
    result, budget, _ = _polish(monkeypatch, _joint_gain_cost, delay=0.6, seconds=2.0)
    # left() must stay above 1 s to begin an evaluation: 2 s budget, 0.6 s per solve.
    assert time.monotonic() - start < 2.0 + 2 * 0.6 + 0.5
    assert budget.pattern_polish_evaluations <= 2
    assert budget.pattern_polish_seconds_used <= 2.0 + 2 * 0.6 + 0.5
    assert result is None or budget.pattern_polish_moves >= 1


def test_zero_seconds_does_nothing(monkeypatch):
    result, budget, shared = _polish(monkeypatch, _joint_gain_cost, seconds=0.0)
    assert result is None
    assert budget.pattern_smooth_attempts == 0 and budget.pattern_polish_evaluations == 0
    assert shared.solves == 0
