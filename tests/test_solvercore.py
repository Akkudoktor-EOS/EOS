"""Joint shadow economics, native FLEET parity and strict model feasibility."""

import warnings
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from akkudoktoreos.optimization.fleet import FleetRequest, optimize_fleet
from akkudoktoreos.thermal import thermaloptimization as thermal
from akkudoktoreos.thermal.thermalmodels import ThermalRC2Parameters
from akkudoktoreos.thermal.thermaloptimization import (
    ThermalShadowRequest,
    ThermalShadowSolution,
    optimize_thermal_shadow,
)


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class TestThermalPlanningDeadline:
    @staticmethod
    def pattern_program():
        p = thermal._Program(thermal._PlanningBudget(60.0), "reference_tracking")
        power = p.var(1, high=2000.0)[0]
        on = p.var(1, high=1.0, binary=True)[0]
        p.eq({power: 1.0}, 1000.0)
        p.row({power: 1.0, on: -2000.0}, high=0.0)
        p.row({power: 1.0, on: -500.0}, low=0.0)
        p.adaptive_stage_cap_seconds = 60.0
        p.integer_hint_factory = lambda: np.ones(1)
        p.reference_witness_validator = lambda values: True
        return p, np.array([0.001, 0.0])

    def test_fixed_pattern_lp_is_bounded_verified_and_uses_real_tracking_objective(
        self, monkeypatch
    ) -> None:
        from scipy.optimize import linprog

        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)
        seen = []

        def mixed(*args, **kwargs):
            clock.value += 5.0
            return SimpleNamespace(status=1, x=None, fun=None, message="no primal")

        def linear(*args, **kwargs):
            seen.append(kwargs)
            result = linprog(*args, **kwargs)
            clock.value += 0.5
            return result

        monkeypatch.setattr("scipy.optimize.milp", mixed)
        monkeypatch.setattr("scipy.optimize.linprog", linear)
        p, objective = self.pattern_program()
        result = p.solve(objective, 10.0)
        assert result.x == pytest.approx([1000.0, 1.0])
        assert result.status == "feasible" and result.objective == 1.0
        assert result.lower_bound is None and result.gap is None
        assert p.budget.reference_tracking_fixed_pattern_rescue
        assert not p.budget.reference_tracking_feasibility_rescue
        assert p.budget.diagnostics[-1].adaptive_extensions == 0
        assert p.budget.diagnostics[-1].fixed_pattern_rescues == 1
        assert [a.kind for a in p.budget.diagnostics[-1].attempts] == ["milp", "fixed_pattern_lp"]
        assert len(seen) == 1 and seen[0]["options"]["time_limit"] == 2.0
        assert seen[0]["bounds"][1] == (1.0, 1.0)

    @pytest.mark.parametrize(
        "pattern", [None, np.array([]), np.array([1.5]), np.array([2.0]), np.array([np.nan])]
    )
    def test_incomplete_or_invalid_pattern_does_not_block_original_adaptive_path(
        self, monkeypatch, pattern
    ) -> None:
        calls = 0

        def mixed(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return SimpleNamespace(status=1, x=None, fun=None, message="no primal")
            return SimpleNamespace(status=0, x=np.ones(2), fun=1.0, mip_dual_bound=1.0, mip_gap=0.0)

        monkeypatch.setattr("scipy.optimize.milp", mixed)
        p, objective = self.pattern_program()
        p.integer_hint_factory = lambda: pattern
        result = p.solve(objective, 10.0)
        assert result.status == "optimal" and result.lower_bound == 1.0
        assert not p.budget.reference_tracking_fixed_pattern_rescue
        assert p.budget.diagnostics[-1].fixed_pattern_rescues == 0
        assert p.budget.diagnostics[-1].adaptive_extensions == 1 and calls == 2

    @pytest.mark.parametrize("invalid_physics", [False, True])
    def test_invalid_pattern_lp_vector_or_physics_is_rejected_without_acceptance_flag(
        self, monkeypatch, invalid_physics
    ) -> None:
        from scipy.optimize import linprog

        calls, lp_calls = 0, 0

        def mixed(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return SimpleNamespace(status=1, x=None, fun=None, message="no primal")
            return SimpleNamespace(status=0, x=np.ones(2), fun=1.0, mip_dual_bound=1.0, mip_gap=0.0)

        def linear(*args, **kwargs):
            nonlocal lp_calls
            lp_calls += 1
            result = linprog(*args, **kwargs)
            if lp_calls == 1 and not invalid_physics:
                result.x[0] += 1.0  # W, in original coordinates.
            return result

        monkeypatch.setattr("scipy.optimize.milp", mixed)
        monkeypatch.setattr("scipy.optimize.linprog", linear)
        p, objective = self.pattern_program()
        p.reference_witness_validator = lambda values: not invalid_physics
        result = p.solve(objective, 10.0)
        assert result.status == "optimal" and result.lower_bound == 1.0
        assert not p.budget.reference_tracking_fixed_pattern_rescue
        assert p.budget.diagnostics[-1].fixed_pattern_rescues == 1
        assert calls == 2 and lp_calls == 2

    def test_pattern_preparation_expiry_never_starts_a_late_lp(self, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)
        monkeypatch.setattr(
            "scipy.optimize.milp",
            lambda *a, **kw: SimpleNamespace(status=1, x=None, fun=None, message="no primal"),
        )
        monkeypatch.setattr(
            "scipy.optimize.linprog",
            lambda *a, **kw: pytest.fail("Expired hint preparation started LP"),
        )
        p, objective = self.pattern_program()

        def hints():
            clock.value = 11.0
            return np.ones(1)

        p.integer_hint_factory = hints
        with pytest.raises(thermal._NoSolution, match="deadline"):
            p.solve(objective, 10.0)
        assert not p.budget.reference_tracking_fixed_pattern_rescue

    def test_unsupported_hint_pattern_leaves_original_mip_path_available(self, monkeypatch) -> None:
        calls = 0

        def mixed(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return SimpleNamespace(status=1, x=None, fun=None, message="no primal")
            return SimpleNamespace(status=0, x=np.ones(2), fun=1.0, mip_dual_bound=1.0, mip_gap=0.0)

        def hints():
            raise thermal._NoSolution("unsupported", "No supported source segment for a hint")

        monkeypatch.setattr("scipy.optimize.milp", mixed)
        p, objective = self.pattern_program()
        p.integer_hint_factory = hints
        result = p.solve(objective, 10.0)
        assert result.status == "optimal" and result.lower_bound == 1.0 and calls == 2
        assert not p.budget.reference_tracking_fixed_pattern_rescue
        assert p.budget.diagnostics[-1].fixed_pattern_rescues == 0
        assert (
            "No supported source segment" in p.budget.diagnostics[-1].fixed_pattern_hint_rejection
        )

    def test_late_pattern_lp_cannot_publish_even_a_valid_vector(self, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)
        monkeypatch.setattr(
            "scipy.optimize.milp",
            lambda *a, **kw: SimpleNamespace(status=1, x=None, fun=None, message="no primal"),
        )

        def linear(*args, **kwargs):
            assert kwargs["options"]["time_limit"] <= 2.0
            clock.value = 11.0
            return SimpleNamespace(status=0, x=np.array([1000.0, 1.0]), fun=1.0)

        monkeypatch.setattr("scipy.optimize.linprog", linear)
        p, objective = self.pattern_program()
        with pytest.raises(thermal._NoSolution, match="deadline"):
            p.solve(objective, 10.0)
        assert not p.budget.reference_tracking_fixed_pattern_rescue
        assert p.budget.diagnostics[-1].fixed_pattern_rescues == 1

    @pytest.mark.parametrize("cold_recovery", [False, True])
    def test_accepted_pattern_reference_never_publishes_economic_certificate(
        self, monkeypatch, cold_recovery
    ) -> None:
        from scipy.optimize import milp

        calls = 0

        def mixed(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return SimpleNamespace(
                    status=1, x=None, fun=None, message="forced initial no primal"
                )
            return milp(*args, **kwargs)

        monkeypatch.setattr("scipy.optimize.milp", mixed)
        data = request_data(4)
        data["min_electric_power_w"] = 100.0
        if cold_recovery:
            data["state"]["room_temperature_c"] = 18.5
            data["recovery"] = {"max_duration_seconds": 3600}
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.plan is not None and result.status == "feasible", result.reason
        assert result.checks["reference_tracking_fixed_pattern_rescue"] is True
        assert result.checks["reference_tracking_feasibility_rescue"] is False
        assert not result.comparison_qualified
        assert result.savings_cash_eur is None and result.savings_objective_eur is None
        assert result.objective_lower_bound_eur is None and result.absolute_gap_eur is None
        assert result.reference_absolute_gap_eur is None
        if cold_recovery:
            assert result.recovery is not None and result.recovery.completed
            assert result.checks["recovery_prefix_identical"] is True

    @pytest.mark.parametrize(
        "total,initial,cap,first_elapsed,expected_deadline,expected_reserve",
        [
            (60.0, 15.0, 120.0, 10.5, 45.0, 15.0),
            (60.0, 15.0, 30.0, 10.5, 30.0, 15.0),
            (60.0, 15.0, 20.0, 10.5, 20.0, 15.0),
            (2.0, 0.5, 120.0, 0.35, 1.5, 0.5),
        ],
    )
    def test_unwitnessed_tracking_borrows_once_with_absolute_cap_and_downstream_reserve(
        self, monkeypatch, total, initial, cap, first_elapsed, expected_deadline, expected_reserve
    ) -> None:
        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)
        objectives, limits = [], []

        def mixed(objective, **kwargs):
            objectives.append(objective.copy())
            limits.append(kwargs["options"]["time_limit"])
            if len(limits) == 1:
                clock.value += first_elapsed
                return SimpleNamespace(status=1, x=None, fun=None, mip_dual_bound=6.0)
            assert kwargs["options"]["presolve"] is False
            clock.value += limits[-1] * 0.5
            return SimpleNamespace(status=0, x=np.ones(1), fun=7.0, mip_dual_bound=7.0, mip_gap=0.0)

        def linear(*args, **kwargs):
            limits.append(kwargs["options"]["time_limit"])
            return SimpleNamespace(status=0, x=np.ones(1), fun=7.0)

        monkeypatch.setattr("scipy.optimize.milp", mixed)
        monkeypatch.setattr("scipy.optimize.linprog", linear)
        budget = thermal._PlanningBudget(total)
        p = thermal._Program(budget, "reference_tracking")
        p.adaptive_stage_cap_seconds = cap
        index = p.var(1, high=1, binary=True)[0]
        p.eq({index: 1.0}, 1.0)
        result = p.solve(np.array([7.0]), initial)
        diagnostics = budget.diagnostics[-1]
        assert result.status == "optimal" and result.objective == result.lower_bound == 7.0
        assert diagnostics.initial_allocation_seconds == initial
        assert diagnostics.final_allocation_seconds == pytest.approx(expected_deadline)
        assert diagnostics.final_allocation_seconds <= cap
        assert diagnostics.adaptive_extensions == 1
        assert diagnostics.downstream_reserve_seconds == pytest.approx(expected_reserve)
        assert diagnostics.feasibility_rescues == 0
        assert all(np.array_equal(objective, np.array([7.0])) for objective in objectives)
        assert clock.value < expected_deadline < total
        assert [a.kind for a in diagnostics.attempts] == ["milp", "milp", "lp"]

    @pytest.mark.parametrize("witness", [False, True])
    def test_tracking_does_not_borrow_after_stage_cap_or_with_feasible_incumbent(
        self, monkeypatch, witness
    ) -> None:
        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)
        calls = 0

        def mixed(*args, **kwargs):
            nonlocal calls
            calls += 1
            clock.value += kwargs["options"]["time_limit"]
            return SimpleNamespace(
                status=1,
                x=np.ones(1) if witness else None,
                fun=7.0 if witness else None,
                mip_dual_bound=6.0,
                mip_gap=0.2,
                message="time limit",
            )

        monkeypatch.setattr("scipy.optimize.milp", mixed)
        monkeypatch.setattr(
            "scipy.optimize.linprog",
            lambda *a, **kw: SimpleNamespace(status=0, x=np.ones(1), fun=7.0),
        )
        budget = thermal._PlanningBudget(60.0)
        p = thermal._Program(budget, "reference_tracking")
        p.adaptive_stage_cap_seconds = 120.0 if witness else 10.0
        p.var(1, high=1, binary=True)
        if witness:
            assert p.solve(np.array([7.0]), 10.0).status == "feasible"
            assert calls == 1
        else:
            with pytest.raises(thermal._NoSolution, match="time limit"):
                p.solve(np.array([7.0]), 10.0)
            assert calls == 2  # Initial search plus rescue, both inside original cap.
        assert budget.diagnostics[-1].adaptive_extensions == 0
        assert budget.diagnostics[-1].final_allocation_seconds == 10.0

    def test_no_witness_after_adaptive_search_remains_limit_with_full_attempt_trace(
        self, monkeypatch
    ) -> None:
        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)

        def mixed(*args, **kwargs):
            clock.value += kwargs["options"]["time_limit"]
            return SimpleNamespace(status=1, x=None, fun=None, message="no primal")

        monkeypatch.setattr("scipy.optimize.milp", mixed)
        monkeypatch.setattr(
            "scipy.optimize.linprog", lambda *a, **kw: pytest.fail("No primal to clean")
        )
        budget = thermal._PlanningBudget(60.0)
        p = thermal._Program(budget, "reference_tracking")
        p.adaptive_stage_cap_seconds = 120.0
        p.var(1, high=1, binary=True)
        with pytest.raises(thermal._NoSolution, match="no primal"):
            p.solve(np.ones(1), 15.0)
        diagnostics = budget.diagnostics[-1]
        assert diagnostics.status == "limit" and diagnostics.adaptive_extensions == 1
        assert diagnostics.feasibility_rescues == 1
        assert [a.kind for a in diagnostics.attempts] == ["milp", "milp", "feasibility_milp"]
        assert all(a.status == 1 for a in diagnostics.attempts)
        assert clock.value < diagnostics.final_allocation_seconds < 60.0
        assert not budget.reference_tracking_feasibility_rescue

    def test_verified_recovery_clears_failed_stage_but_keeps_failure_diagnostics(self) -> None:
        budget = thermal._PlanningBudget(60.0)
        budget.failed_stage = "joint_candidate"
        budget.diagnostics.append(
            thermal.ThermalSolveDiagnostics(
                name="joint_candidate",
                status="infeasible",
                elapsed_seconds=0.1,
                solver_calls=2,
                presolve_retries=1,
            )
        )
        p = thermal._Program(budget, "joint_candidate")
        index = p.var(1, high=1)[0]
        p.eq({index: 1.0}, 0.5)
        assert p.verified_fallback(np.ones(1), np.array([0.5])).status == "feasible"
        assert budget.failed_stage is None
        assert [a.status for a in budget.diagnostics] == ["infeasible", "feasible_witness"]

    @pytest.mark.parametrize("failure", ["infeasible", "error"])
    def test_joint_numerical_failure_requires_complete_verified_reference_without_savings(
        self, monkeypatch, failure
    ) -> None:
        original = thermal._Program.solve

        def solve(program, objective, seconds):
            if program.stage == "joint_candidate":
                raise thermal._NoSolution(failure, "forced numerical rejection")
            return original(program, objective, seconds)

        monkeypatch.setattr(thermal._Program, "solve", solve)
        data = request_data(4, constant=True)
        data["fleet"].update(storage_devices(4))
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.plan is not None and result.status == "feasible", result.reason
        assert result.checks["candidate_numerical_reference_fallback"] is True
        assert result.checks["candidate_plan_origin"] == "verified_reference_fallback"
        assert result.checks["storage_terminal_states_match"] is True
        assert float(result.checks["max_constraint_violation"]) <= 1e-5
        assert not result.comparison_qualified
        assert result.savings_cash_eur is None and result.savings_objective_eur is None

    @pytest.mark.parametrize("failure", ["limit", "infeasible", "error"])
    def test_numerical_joint_fallback_cannot_hide_invalid_original_matrix_witness(
        self, monkeypatch, failure
    ) -> None:
        original = thermal._Program.solve

        def solve(program, objective, seconds):
            if program.stage == "joint_candidate":
                # This row is deliberately inconsistent with the reference.
                program.eq({0: 1.0}, program.witness_values[0] + 1000.0)
                raise thermal._NoSolution(failure, "forced solver rejection")
            return original(program, objective, seconds)

        monkeypatch.setattr(thermal._Program, "solve", solve)
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(request_data(2)))
        assert result.plan is None and result.status == "error"
        assert "witness failed" in result.reason
        assert result.checks["candidate_numerical_reference_fallback"] is False

    @pytest.mark.parametrize(
        "flag",
        [
            "reference_tracking_feasibility_rescue",
            "cleanup_original_refinement_used",
            "cleanup_physical_refinement_used",
        ],
    )
    def test_verified_rescue_or_refinement_plan_is_not_an_economic_certificate(
        self, monkeypatch, flag
    ) -> None:
        original = thermal._Program.solve

        def solve(program, objective, seconds):
            result = original(program, objective, seconds)
            if program.stage == "reference_tracking":
                setattr(program.budget, flag, True)
            return result

        monkeypatch.setattr(thermal._Program, "solve", solve)
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(request_data(2)))
        assert result.plan is not None and result.status == "feasible", result.reason
        assert result.checks[flag] is True
        assert result.checks["candidate_plan_origin"] == "joint_optimization"
        assert not result.comparison_qualified
        assert result.savings_cash_eur is None and result.savings_objective_eur is None
        if flag == "cleanup_physical_refinement_used":
            assert result.objective_lower_bound_eur is None and result.absolute_gap_eur is None
            assert result.reference_absolute_gap_eur is None

    def test_tracking_rescue_recomputes_original_objective_and_shares_stage_time(
        self, monkeypatch
    ) -> None:
        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)
        caps, objectives = [], []

        def mixed(objective, **kwargs):
            objectives.append(objective.copy())
            caps.append(kwargs["options"]["time_limit"])
            clock.value += 4 if len(caps) == 1 else 3
            if len(caps) == 1:
                return SimpleNamespace(status=1, x=None, fun=None, mip_dual_bound=2.0)
            assert kwargs["options"]["presolve"] is False
            return SimpleNamespace(status=0, x=np.ones(1), fun=0.0, mip_dual_bound=0.0, mip_gap=0.0)

        def linear(*args, **kwargs):
            caps.append(kwargs["options"]["time_limit"])
            clock.value += 1
            return SimpleNamespace(status=0, x=np.ones(1), fun=7.0)

        monkeypatch.setattr("scipy.optimize.milp", mixed)
        monkeypatch.setattr("scipy.optimize.linprog", linear)
        budget = thermal._PlanningBudget(10.0)
        p = thermal._Program(budget, "reference_tracking")
        index = p.var(1, high=1, binary=True)[0]
        p.eq({index: 1.0}, 1.0)
        result = p.solve(np.array([7.0]), 10.0)
        assert caps == pytest.approx([9.0, 5.1, 3.0])
        assert objectives[0] == pytest.approx([7.0])
        assert objectives[1] == pytest.approx([0.0])
        assert result.objective == 7.0 and result.status == "feasible"
        assert result.lower_bound is None and result.gap is None
        assert budget.reference_tracking_feasibility_rescue is True
        assert budget.diagnostics[-1].lower_bound == 2.0
        assert budget.diagnostics[-1].feasibility_rescues == 1
        assert budget.diagnostics[-1].solver_calls == 3
        assert [attempt.kind for attempt in budget.diagnostics[-1].attempts] == [
            "milp",
            "feasibility_milp",
            "lp",
        ]
        assert [attempt.status for attempt in budget.diagnostics[-1].attempts] == [1, 0, 0]

    def test_tracking_rescue_does_not_override_hard_physical_infeasibility(
        self, monkeypatch
    ) -> None:
        calls = 0

        def mixed(*args, **kwargs):
            nonlocal calls
            calls += 1
            return SimpleNamespace(status=2, x=None, fun=None, message="hard infeasible")

        monkeypatch.setattr("scipy.optimize.milp", mixed)
        budget = thermal._PlanningBudget(10.0)
        p = thermal._Program(budget, "reference_tracking")
        p.var(1, high=1, binary=True)
        with pytest.raises(thermal._NoSolution, match="hard infeasible"):
            p.solve(np.ones(1), 2.0)
        assert calls == 2 and budget.reference_tracking_feasibility_rescue is False
        assert budget.diagnostics[-1].feasibility_rescues == 0

    @pytest.mark.parametrize("integer", [False, True])
    def test_original_matrix_refinement_preserves_watts_and_fixed_integers(
        self, monkeypatch, integer
    ) -> None:
        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)
        seen = []
        wrong = np.array([1.000002, 1.0]) if integer else np.array([1.000002])
        correct = np.array([1000.0, 1.0]) if integer else np.array([1000.0])

        def mixed(*args, **kwargs):
            clock.value += 2
            return SimpleNamespace(
                status=0, x=wrong.copy(), fun=1.0, mip_dual_bound=0.9, mip_gap=0.1
            )

        def linear(*args, **kwargs):
            seen.append(kwargs)
            clock.value += 1
            return SimpleNamespace(
                status=0, x=wrong.copy() if len(seen) == 1 else correct.copy(), fun=1.0
            )

        monkeypatch.setattr("scipy.optimize.milp", mixed)
        monkeypatch.setattr("scipy.optimize.linprog", linear)
        budget = thermal._PlanningBudget(10.0)
        p = thermal._Program(budget, "refine")
        power = p.var(1, high=2000)[0]
        if integer:
            p.var(1, high=1, binary=True)
        p.eq({power: 1.0}, 1000.0)
        result = p.solve(np.array([0.001, 0.0]) if integer else np.array([0.001]), 6.0)
        assert result.x == pytest.approx(correct) and result.violation == 0.0
        assert result.objective == 1.0 and result.status == "feasible"
        assert result.lower_bound is None and result.gap is None
        assert budget.cleanup_original_refinement_used is True
        assert budget.diagnostics[-1].original_refinements == 1
        assert budget.diagnostics[-1].attempts[-1].kind == "original_lp"
        refined = seen[-1]
        assert refined["options"]["presolve"] is False
        assert refined["options"]["primal_feasibility_tolerance"] == 1e-10
        assert refined["options"]["small_matrix_value"] == 1e-12
        assert refined["b_eq"] == pytest.approx([1000.0])
        assert refined["A_eq"].toarray()[0, power] == 1.0
        assert refined["bounds"][power] == (0.0, 2000.0)
        if integer:
            assert refined["bounds"][1] == (1.0, 1.0)
            assert [item["options"]["time_limit"] for item in seen] == [4.0, 3.0]
        else:
            assert [item["options"]["time_limit"] for item in seen] == [6.0, 5.0]

    def test_original_refinement_cannot_start_after_stage_expiry(self, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)
        calls = 0

        def linear(*args, **kwargs):
            nonlocal calls
            calls += 1
            clock.value = 2.0
            return SimpleNamespace(status=0, x=np.array([1.000002]), fun=1.0)

        monkeypatch.setattr("scipy.optimize.linprog", linear)
        p = thermal._Program(thermal._PlanningBudget(10.0), "refine")
        power = p.var(1, high=2000)[0]
        p.eq({power: 1.0}, 1000.0)
        with pytest.raises(thermal._NoSolution, match="deadline"):
            p.solve(np.array([0.001]), 2.0)
        assert calls == 1

    def test_highs_forwarding_notice_filter_keeps_other_backend_warnings(self, monkeypatch) -> None:
        from scipy.optimize import OptimizeWarning, linprog

        def linear(*args, **kwargs):
            warnings.warn("Different backend warning", OptimizeWarning)
            return linprog(*args, **kwargs)

        monkeypatch.setattr("scipy.optimize.linprog", linear)
        p = thermal._Program()
        index = p.var(1, high=1)[0]
        p.eq({index: 1.0}, 0.5)
        with pytest.warns(OptimizeWarning, match="Different backend warning") as seen:
            p.solve(np.ones(1), 2.0)
        assert len(seen) == 1

    def test_joint_stage_leaves_paid_final_verification_reserve(self, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)
        original_solve, original_verify = thermal._Program.solve, thermal._minute_temperatures
        calls = 0
        joint_cap = []

        def solve(program, objective, seconds):
            result = original_solve(program, objective, seconds)
            if program.stage == "joint_candidate":
                joint_cap.append(seconds)
                clock.value += seconds
            return result

        def verify(*args):
            nonlocal calls
            result = original_verify(*args)
            calls += 1
            if calls == 2:
                clock.value += 0.5
            return result

        monkeypatch.setattr(thermal._Program, "solve", solve)
        monkeypatch.setattr(thermal, "_minute_temperatures", verify)
        data = request_data(1, constant=True)
        data["fleet"]["solver_time_limit_seconds"] = 120.0
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert joint_cap == pytest.approx([59.0])
        assert result.plan is not None, result.reason
        assert result.checks["planning_elapsed_seconds"] == 59.5
        assert result.checks["planning_verification_reserve_seconds"] == 1.0
        assert result.checks["planning_deadline_exceeded"] is False

    def test_remaining_stage_shares_reserve_time_and_carry_unused_time(self, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)
        seen = {}
        original_solve, original_fleet = thermal._Program.solve, thermal.optimize_fleet

        def solve(program, objective, seconds):
            seen[program.stage] = seconds
            result = original_solve(program, objective, seconds)
            if program.stage in ("reference_tracking", "reference_heat_energy"):
                clock.value += 3
            return result

        def fleet(request):
            seen["native_fleet_reference"] = request.solver_time_limit_seconds
            result = original_fleet(request)
            clock.value += 2
            return result

        monkeypatch.setattr(thermal._Program, "solve", solve)
        monkeypatch.setattr(thermal, "optimize_fleet", fleet)
        data = request_data(1, constant=True)
        data["fleet"]["solver_time_limit_seconds"] = 120.0
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.plan is not None, result.reason
        assert seen == pytest.approx(
            {
                "reference_tracking": 15.0,
                "reference_heat_energy": 19.0,
                "native_fleet_reference": 27.0,
                "joint_candidate": 51.0,
            }
        )

    def test_reference_energy_timeout_reuses_verified_tracking_witness_without_savings(
        self, monkeypatch
    ) -> None:
        original = thermal._Program.solve

        def solve(program, objective, seconds):
            if program.stage == "reference_heat_energy":
                raise thermal._NoSolution("limit", "forced reference energy timeout")
            return original(program, objective, seconds)

        monkeypatch.setattr(thermal._Program, "solve", solve)
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(request_data(4)))
        assert result.plan is not None and result.status == "feasible", result.reason
        assert result.checks["reference_energy_witness_reused"] is True
        assert result.checks["reference_heat_optimization_complete"] is False
        assert result.checks["candidate_plan_origin"] == "joint_optimization"
        assert not result.comparison_qualified
        assert result.savings_cash_eur is None and result.savings_objective_eur is None

    @pytest.mark.parametrize("below_reserve", [False, True])
    def test_joint_timeout_verifies_full_reference_matrix_with_native_devices(
        self, monkeypatch, below_reserve
    ) -> None:
        original = thermal._Program.solve

        def solve(program, objective, seconds):
            if program.stage == "joint_candidate":
                raise thermal._NoSolution("limit", "forced joint timeout")
            return original(program, objective, seconds)

        monkeypatch.setattr(thermal._Program, "solve", solve)
        data = request_data(4, constant=True)
        data["fleet"].update(storage_devices(4))
        if below_reserve:
            battery = data["fleet"]["devices"][0]
            battery.update(
                initial_energy_wh=100.0,
                max_charge_power_w=0.0,
                min_charge_power_w=0.0,
                terminal_value_eur_per_kwh=0.0,
            )
            data["fleet"]["devices"] = [battery]
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert (
            result.plan is not None
            and result.candidate is not None
            and result.reference is not None
        ), result.reason
        assert result.status == "feasible" and not result.comparison_qualified
        assert result.checks["candidate_plan_origin"] == "verified_reference_fallback"
        assert result.savings_cash_eur is None and result.savings_objective_eur is None
        assert result.objective_lower_bound_eur is None
        assert float(result.checks["max_constraint_violation"]) <= 1e-5
        assert result.checks["storage_terminal_states_match"] is True
        for key, plan in result.candidate.devices.items():
            assert plan.energy_wh == pytest.approx(result.reference.devices[key].energy_wh)

    def test_joint_fallback_rejects_corrupted_reference_witness(self, monkeypatch) -> None:
        solve, verify = thermal._Program.solve, thermal._Program.verified_fallback

        def timeout(program, objective, seconds):
            if program.stage == "joint_candidate":
                raise thermal._NoSolution("limit", "forced joint timeout")
            return solve(program, objective, seconds)

        def corrupt(program, objective, values):
            values = values.copy()
            values[0] += 1000.0
            return verify(program, objective, values)

        monkeypatch.setattr(thermal._Program, "solve", timeout)
        monkeypatch.setattr(thermal._Program, "verified_fallback", corrupt)
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(request_data(4)))
        assert result.plan is None and result.status == "error"
        assert "witness failed" in result.reason

    def test_global_expiry_never_reuses_reference_witness(self, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)
        original = thermal._Program.solve

        def timeout(program, objective, seconds):
            if program.stage == "joint_candidate":
                clock.value = 61.0
                raise thermal._NoSolution("limit", "forced overdue joint")
            return original(program, objective, seconds)

        monkeypatch.setattr(thermal._Program, "solve", timeout)
        result = optimize_thermal_shadow(
            ThermalShadowRequest.model_validate(request_data(1, constant=True))
        )
        assert result.status == "limit" and result.plan is None and result.candidate is None
        assert result.checks["planning_deadline_exceeded"] is True

    def test_cleanup_retry_has_no_fresh_budget(self, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)
        limits = []

        def mixed(*args, **kwargs):
            limits.append(kwargs["options"]["time_limit"])
            clock.value += 4
            return SimpleNamespace(
                status=0, x=np.array([1.0]), fun=1.0, mip_dual_bound=1.0, mip_gap=0.0
            )

        def linear(*args, **kwargs):
            limits.append(kwargs["options"]["time_limit"])
            clock.value += 2 if len(limits) == 2 else 1
            return SimpleNamespace(
                status=2 if len(limits) == 2 else 0,
                x=None if len(limits) == 2 else np.array([1.0]),
                fun=1.0,
            )

        monkeypatch.setattr("scipy.optimize.milp", mixed)
        monkeypatch.setattr("scipy.optimize.linprog", linear)
        budget = thermal._PlanningBudget(10.0)
        p = thermal._Program(budget, "cleanup_retry")
        p.var(1, high=1, binary=True)
        p.solve(np.ones(1), 10.0)
        assert limits == pytest.approx([9.0, 6.0, 4.0])
        assert budget.diagnostics[-1].solver_calls == 3
        assert budget.diagnostics[-1].presolve_retries == 1

    def test_overlong_mip_does_not_start_cleanup_or_publish_incumbent(self, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)

        def mixed(*args, **kwargs):
            clock.value = 2
            return SimpleNamespace(
                status=0, x=np.array([1.0]), fun=1.0, mip_dual_bound=1.0, mip_gap=0.0
            )

        monkeypatch.setattr("scipy.optimize.milp", mixed)
        monkeypatch.setattr(
            "scipy.optimize.linprog",
            lambda *a, **kw: pytest.fail("Overdue incumbent started cleanup"),
        )
        budget = thermal._PlanningBudget(1.0)
        p = thermal._Program(budget, "late_mip")
        p.var(1, high=1, binary=True)
        with pytest.raises(thermal._NoSolution, match="deadline"):
            p.solve(np.ones(1), 10.0)
        assert budget.diagnostics[-1].solver_calls == 1
        assert budget.diagnostics[-1].status == "limit"

    def test_scaled_mip_cleanup_still_rejects_original_unit_constraint_violation(
        self, monkeypatch
    ) -> None:
        # The backend uses kW, but the final physical validation still uses W.
        wrong = np.array([1.000002, 1.0])  # 1000.002W violates the exact1000W row.
        monkeypatch.setattr(
            "scipy.optimize.milp",
            lambda *a, **kw: SimpleNamespace(
                status=0, x=wrong.copy(), fun=1.0, mip_dual_bound=1.0, mip_gap=0.0
            ),
        )
        monkeypatch.setattr(
            "scipy.optimize.linprog",
            lambda *a, **kw: SimpleNamespace(status=0, x=wrong.copy(), fun=1.0),
        )
        p = thermal._Program()
        power = p.var(1, high=2000)[0]
        p.var(1, high=1, binary=True)
        p.eq({power: 1}, 1000.0)
        with pytest.raises(thermal._NoSolution, match="Cleaned integer witness failed"):
            p.solve(np.array([0.001, 0.0]), 2.0)

    def test_numerical_scaling_preserves_original_watts_and_euro_objective(self) -> None:
        p = thermal._Program()
        power = p.var(1, high=5000)[0]
        p.eq({power: 1}, 4000)
        result = p.solve(np.array([0.25 / 1000]), 2.0)
        assert result.x[power] == pytest.approx(4000)
        assert result.objective == pytest.approx(1.0)
        assert result.violation < 1e-9

    @pytest.mark.parametrize("value", [0, -1, True, "60", float("nan"), float("inf"), 3601])
    def test_total_budget_is_explicit_positive_finite_and_bounded(self, value: Any) -> None:
        with pytest.raises(ValidationError):
            ThermalShadowRequest.model_validate(
                {**request_data(), "planning_time_limit_seconds": value}
            )

    def test_touching_hard_bands_have_feasible_reference_and_joint(self) -> None:
        data = request_data(4)
        data["state"].update(room_temperature_c=19.6, mass_temperature_c=20.0)
        data["comfort_bands"] = [
            {"min_temperature_c": 19.0, "target_temperature_c": 19.5, "max_temperature_c": 20.5}
        ] * 2 + [
            {"min_temperature_c": 20.5, "target_temperature_c": 21.0, "max_temperature_c": 21.5}
        ] * 2
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.plan is not None, result.reason
        assert result.reference_room_temperature_c[2] == pytest.approx(20.5, abs=1e-8)
        assert float(result.checks["max_minute_comfort_violation_c"]) <= 1e-5
        assert result.checks["storage_terminal_states_match"] is True

    @pytest.mark.parametrize("upward", [True, False])
    def test_disjoint_bands_report_conflicting_slots_and_time_without_solving(
        self, monkeypatch, upward
    ) -> None:
        data = request_data(2)
        cold = {"min_temperature_c": 19.0, "target_temperature_c": 19.5, "max_temperature_c": 20.0}
        warm = {"min_temperature_c": 20.5, "target_temperature_c": 21.0, "max_temperature_c": 21.5}
        data["comfort_bands"] = [cold, warm] if upward else [warm, cold]
        monkeypatch.setattr(
            thermal._Program, "solve", lambda *a: pytest.fail("Conflicting bands entered solver")
        )
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.status == "infeasible" and result.plan is None
        assert "slots 0 and 1" in result.reason and "12:15:00" in result.reason
        assert "cannot jump" in result.reason
        assert result.stage_diagnostics == ()

    def test_false_presolve_infeasible_retries_same_lp_and_verifies_witness(
        self, monkeypatch
    ) -> None:
        from scipy.optimize import linprog

        calls = []

        def fake(*args, **kwargs):
            calls.append(kwargs["options"])
            if len(calls) == 1:
                return SimpleNamespace(
                    status=2, x=None, fun=None, message="false presolve rejection"
                )
            return linprog(*args, **kwargs)

        monkeypatch.setattr("scipy.optimize.linprog", fake)
        budget = thermal._PlanningBudget(10.0)
        p = thermal._Program(budget, "witness_lp")
        index = p.var(1, high=1)[0]
        p.eq({index: 1}, 0.5)
        p.feasible_witness = np.array([0.5])
        result = p.solve(np.ones(1), 5.0)
        assert result.x == pytest.approx([0.5]) and result.violation < 1e-12
        assert [item["presolve"] for item in calls] == [True, False]
        assert budget.diagnostics[-1].presolve_retries == 1

    def test_retry_and_cleanup_use_remaining_stage_and_global_time(self, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)
        limits = []

        def mixed(*args, **kwargs):
            limits.append(kwargs["options"]["time_limit"])
            clock.value += 4
            return SimpleNamespace(
                status=0, x=np.array([1.0]), fun=1.0, mip_dual_bound=1.0, mip_gap=0.0
            )

        def linear(*args, **kwargs):
            limits.append(kwargs["options"]["time_limit"])
            clock.value += 2
            return SimpleNamespace(status=0, x=np.array([1.0]), fun=1.0)

        monkeypatch.setattr("scipy.optimize.milp", mixed)
        monkeypatch.setattr("scipy.optimize.linprog", linear)
        budget = thermal._PlanningBudget(20.0)
        p = thermal._Program(budget, "cleanup")
        p.var(1, high=1, binary=True)
        p.solve(np.ones(1), 10.0)
        assert limits == pytest.approx([9.0, 6.0])
        assert budget.diagnostics[-1].solver_calls == 2
        assert budget.diagnostics[-1].elapsed_seconds == 6

    def test_lp_retry_shares_stage_cap(self, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)
        limits = []

        def linear(*args, **kwargs):
            limits.append(kwargs["options"]["time_limit"])
            clock.value += 4 if len(limits) == 1 else 1
            return SimpleNamespace(
                status=2 if len(limits) == 1 else 0,
                x=None if len(limits) == 1 else np.array([0.5]),
                fun=None if len(limits) == 1 else 0.5,
            )

        monkeypatch.setattr("scipy.optimize.linprog", linear)
        p = thermal._Program(thermal._PlanningBudget(10.0), "retry")
        p.var(1, high=1)
        p.solve(np.ones(1), 6.0)
        assert limits == pytest.approx([6.0, 2.0])

    def test_preparation_expiry_never_starts_solver(self, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)
        original = thermal._dynamics

        def prepare(*args):
            clock.value = 2
            return original(*args)

        monkeypatch.setattr(thermal, "_dynamics", prepare)
        monkeypatch.setattr(
            thermal._Program, "solve", lambda *a: pytest.fail("Late preparation started solver")
        )
        result = optimize_thermal_shadow(
            ThermalShadowRequest.model_validate(
                {**request_data(1, constant=True), "planning_time_limit_seconds": 1.0}
            )
        )
        assert result.status == "limit" and result.plan is None
        assert result.checks["planning_deadline_exceeded"] is True

    def test_expiry_between_stages_never_starts_native_fleet(self, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)

        def reference(request, source, **kwargs):
            clock.value = 2
            return np.zeros(1), np.full((2, 2), 20.5), np.full(15, 20.5), True

        monkeypatch.setattr(thermal, "_reference", reference)
        monkeypatch.setattr(
            thermal, "optimize_fleet", lambda *a: pytest.fail("Expired budget started native FLEET")
        )
        result = optimize_thermal_shadow(
            ThermalShadowRequest.model_validate(
                {**request_data(1, constant=True), "planning_time_limit_seconds": 1.0}
            )
        )
        assert result.status == "limit" and result.plan is None

    def test_verification_overrun_discards_complete_solution(self, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)
        original = thermal._minute_temperatures
        calls = 0

        def verify(*args):
            nonlocal calls
            calls += 1
            result = original(*args)
            if calls == 2:
                clock.value = 2
            return result

        monkeypatch.setattr(thermal, "_minute_temperatures", verify)
        result = optimize_thermal_shadow(
            ThermalShadowRequest.model_validate(
                {**request_data(1, constant=True), "planning_time_limit_seconds": 1.0}
            )
        )
        assert result.status == "limit" and result.plan is None and result.candidate is None
        assert result.savings_cash_eur is None
        assert result.checks["planning_deadline_exceeded"] is True


def request_data(n: int = 8, *, constant: bool = False) -> dict[str, Any]:
    """Make an explicit generic scenario, not installation or calendar data."""
    start = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    return {
        "fleet": {
            "start_datetime": start,
            "load_w": [300.0] * n,
            "pv_w": [0.0] * n,
            "import_price_eur_per_kwh": [0.1] * (n // 2) + [0.8] * (n - n // 2),
            "export_price_eur_per_kwh": [0.05] * n,
            "import_limit_w": 10000.0,
            "export_limit_w": 10000.0,
            "solver_time_limit_seconds": 10.0,
        },
        "load_basis": "residual_excluding_heat",
        "zone_id": "zone1",
        "heat_source_id": "heater1",
        "model": {
            "room_loss_per_hour": 0.1,
            "room_mass_exchange_per_hour": 0.2,
            "mass_capacity_ratio": 5.0,
            "electric_gain_kelvin_per_kwh": 4.0,
        },
        "state": {
            "estimated_at": start,
            "based_on_observed_at": start - timedelta(seconds=60),
            "room_temperature_c": 20.5,
            "mass_temperature_c": 20.5,
        },
        "outdoor_temperature_c": [20.5 if constant else 0.0] * n,
        "comfort_bands": [
            {
                "min_temperature_c": 20.5 if constant else 20.0,
                "target_temperature_c": 20.5,
                "max_temperature_c": 20.5 if constant else 21.0,
            }
        ]
        * n,
        "max_electric_power_w": 2000.0,
        "issued_at": start - timedelta(seconds=30),
        "revision": 1,
        "basis_control_revision": 1,
        "smoothing_seconds": None,
    }


def storage_devices(n: int = 4) -> dict[str, Any]:
    """Native battery/EV features whose constraints must survive embedding."""
    return {
        "devices": [
            {
                "device_id": "battery1",
                "kind": "battery",
                "capacity_wh": 4000.0,
                "max_energy_wh": 3500.0,
                "initial_energy_wh": 2000.0,
                "reserve_wh": 500.0,
                "max_charge_power_w": 2000.0,
                "min_charge_power_w": 400.0,
                "max_discharge_power_w": 2000.0,
                "charge_efficiency": 0.9,
                "discharge_efficiency": 0.9,
                "terminal_value_eur_per_kwh": 0.2,
                "discharge_cost_eur_per_kwh": 0.01,
                "power_group": "shared",
            },
            {
                "device_id": "ev1",
                "kind": "ev",
                "capacity_wh": 4000.0,
                "initial_energy_wh": 0.0,
                "max_charge_power_w": 2000.0,
                "min_charge_power_w": 1000.0,
                "charge_levels_w": [0.0, 1000.0, 2000.0],
                "target_energy_wh": 500.0,
                "target_slot": 3,
                "available": [True, False, True, False][:n],
                "power_group": "shared",
            },
        ],
        "power_groups": [
            {"group_id": "shared", "max_charge_power_w": 2500.0, "max_discharge_power_w": 2000.0}
        ],
    }


class TestThermalStateCondensation:
    @pytest.mark.parametrize("error", [float("nan"), float("inf"), -1.0])
    def test_invalid_physical_metrics_are_not_recorded_or_published(self, error) -> None:
        p, _objective = self.retry_program()
        p.physical_validator = lambda values: {"source_flow_violation_w": error}
        with pytest.raises(thermal._NoSolution, match="invalid error metrics"):
            p.physical_violation(np.zeros(len(p.lower)))
        assert p.physical_check_history == [] and p.budget.physical_metrics == {}

    @staticmethod
    def physical_program():
        p, objective = TestThermalStateCondensation.retry_program()
        p.physical_validator = lambda values: {
            "source_flow_violation_w": max(0.0, float(values[3] - 23.0) * 1e6),
            "thermal_terminal_error_c": abs(float(values[4] - 20.0)),
        }
        return p, objective

    def test_physical_watt_failure_repairs_same_integer_branch_inside_stage(
        self, monkeypatch
    ) -> None:
        from scipy.optimize import linprog, milp

        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)
        mip_calls, lp_calls = [], []

        def mixed(*args, **kwargs):
            mip_calls.append(kwargs)
            result = milp(*args, **kwargs)
            clock.value += 2.0
            return result

        def linear(*args, **kwargs):
            lp_calls.append(kwargs)
            if len(lp_calls) == 1:
                result = SimpleNamespace(
                    status=0,
                    x=np.array([3.2000000008, 1.3999999996, 20.0, 23.000000002, 20.0, 1.0, 1.0]),
                    fun=0.96,
                )
            else:
                result = linprog(*args, **kwargs)
            clock.value += 1.0
            return result

        monkeypatch.setattr("scipy.optimize.milp", mixed)
        monkeypatch.setattr("scipy.optimize.linprog", linear)
        p, objective = self.physical_program()
        result = p.solve(objective, 10.0)
        assert result.status == "feasible" and result.objective == pytest.approx(0.96)
        assert result.lower_bound is None and result.gap is None
        assert len(mip_calls) == 1 and len(lp_calls) == 2  # No second MILP.
        assert lp_calls[-1]["bounds"][-2:] == [(1.0, 1.0), (1.0, 1.0)]
        assert lp_calls[-1]["bounds"][:2] == [(0.0, 4.0), (0.0, 4.0)]
        assert [call["options"]["time_limit"] for call in lp_calls] == [8.0, 7.0]
        assert p.budget.cleanup_physical_refinement_used
        stage = p.budget.diagnostics[-1]
        assert stage.physical_refinements == 1 and stage.representation_switches == 1
        assert [a.kind for a in stage.attempts] == ["milp", "lp", "physical_cleanup_lp"]
        assert stage.physical_checks[0].metrics["source_flow_violation_w"] > 1e-5
        assert stage.physical_checks[-1].metrics["source_flow_violation_w"] <= 1e-5
        assert p.budget.physical_metrics["failed_candidate_source_flow_violation_w"] > 1e-5
        assert p.budget.physical_metrics["candidate_source_flow_violation_w"] <= 1e-5

    def test_failed_physical_cleanup_can_only_reuse_a_physically_valid_reference(
        self, monkeypatch
    ) -> None:
        def linear(*args, **kwargs):
            wrong = (
                [3.2000000008, 1.3999999996, 20.0, 23.000000002, 20.0, 1.0, 1.0]
                if len(kwargs["bounds"]) == 7
                else [3.2000000008, 1.3999999996, 1.0, 1.0]
            )
            return SimpleNamespace(status=0, x=np.array(wrong), fun=0.96)

        monkeypatch.setattr("scipy.optimize.linprog", linear)
        p, objective = self.physical_program()
        with pytest.raises(thermal._NoSolution, match="physical verification"):
            p.solve(objective, 5.0)
        assert not p.budget.cleanup_physical_refinement_used
        witness = np.array([p.witness_values[i] for i in range(len(p.lower))])
        assert p.verified_fallback(objective, witness).status == "feasible"
        assert (
            p.budget.diagnostics[-1].physical_checks[-1].metrics["source_flow_violation_w"] <= 1e-5
        )
        p.physical_validator = lambda values: {"source_flow_violation_w": 0.001}
        with pytest.raises(thermal._NoSolution, match="physical verification"):
            p.verified_fallback(objective, witness)

    def test_physical_cleanup_expiry_discards_valid_lp_without_acceptance(
        self, monkeypatch
    ) -> None:
        from scipy.optimize import linprog

        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)
        calls = 0

        def linear(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return SimpleNamespace(
                    status=0,
                    x=np.array([3.2000000008, 1.3999999996, 20.0, 23.000000002, 20.0, 1.0, 1.0]),
                    fun=0.96,
                )
            result = linprog(*args, **kwargs)
            clock.value = 11.0
            return result

        monkeypatch.setattr("scipy.optimize.linprog", linear)
        p, objective = self.physical_program()
        with pytest.raises(thermal._NoSolution, match="deadline"):
            p.solve(objective, 10.0)
        assert calls == 2 and not p.budget.cleanup_physical_refinement_used
        assert p.budget.diagnostics[-1].physical_refinements == 1

    def test_invalid_condensed_cleanup_and_original_refinement_are_still_rejected(
        self, monkeypatch
    ) -> None:
        from scipy.optimize import milp

        calls = 0

        def mixed(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return SimpleNamespace(status=2, x=None, fun=None, message="false sparse rejection")
            return milp(*args, **kwargs)

        def linear(*args, **kwargs):
            wrong = (
                [3.201, 1.4, 1.0, 1.0]
                if len(kwargs["bounds"]) == 4
                else [3201.0, 1400.0, 20.0, 23.0025, 20.00125, 1.0, 1.0]
            )
            return SimpleNamespace(status=0, x=np.array(wrong), fun=0.96)

        monkeypatch.setattr("scipy.optimize.milp", mixed)
        monkeypatch.setattr("scipy.optimize.linprog", linear)
        p, objective = self.retry_program()
        with pytest.raises(thermal._NoSolution, match="Cleaned integer witness failed"):
            p.solve(objective, 5.0)
        assert p.budget.diagnostics[-1].representation_switches == 1
        assert p.budget.diagnostics[-1].original_refinements == 1

    @staticmethod
    def retry_program():
        p, heat, states, on, objective = TestThermalStateCondensation.program()
        p.initial_representation = "sparse"
        p.stage = "joint_candidate"
        witness = np.zeros(len(p.lower))
        witness[heat], witness[states], witness[on] = (
            [3200.0, 1400.0],
            [20.0, 23.0, 20.0],
            [1.0, 1.0],
        )
        p.set_witness(range(len(p.lower)), witness)
        return p, objective

    def test_verified_sparse_numerical_rejection_switches_once_without_resetting_clock(
        self, monkeypatch
    ) -> None:
        from scipy.optimize import linprog, milp

        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)
        mip_caps, lp_caps, columns = [], [], []

        def mixed(*args, **kwargs):
            mip_caps.append(kwargs["options"]["time_limit"])
            columns.append(len(args[0]))
            if len(mip_caps) == 1:
                clock.value += 3.0
                return SimpleNamespace(status=2, x=None, fun=None, message="false sparse rejection")
            result = milp(*args, **kwargs)
            clock.value += 1.0
            return result

        def linear(*args, **kwargs):
            lp_caps.append(kwargs["options"]["time_limit"])
            result = linprog(*args, **kwargs)
            clock.value += 1.0
            return result

        monkeypatch.setattr("scipy.optimize.milp", mixed)
        monkeypatch.setattr("scipy.optimize.linprog", linear)
        p, objective = self.retry_program()
        result = p.solve(objective, 10.0)
        assert result.status == "optimal" and result.objective == pytest.approx(0.96)
        assert result.lower_bound == pytest.approx(0.96)
        assert p.witness_violation(result.x) < 1e-9
        assert mip_caps == pytest.approx([9.0, 6.3]) and lp_caps == pytest.approx([6.0])
        assert columns == [7, 4]
        diagnostic = p.budget.diagnostics[-1]
        assert diagnostic.representation_switches == 1 and diagnostic.presolve_retries == 0
        assert diagnostic.final_allocation_seconds == 10.0
        assert [a.representation for a in diagnostic.attempts] == [
            "sparse",
            "condensed",
            "condensed",
        ]
        assert [a.status for a in diagnostic.attempts] == [2, 0, 0]

    def test_successful_or_feasible_sparse_incumbent_never_constructs_condensation(
        self, monkeypatch
    ) -> None:
        from scipy.optimize import milp

        def mixed(*args, **kwargs):
            result = milp(*args, **kwargs)
            result.status = 1  # Valid incumbent with an unproven search status.
            return result

        monkeypatch.setattr("scipy.optimize.milp", mixed)
        p, objective = self.retry_program()
        monkeypatch.setattr(
            p,
            "_thermal_reduction",
            lambda: pytest.fail("Valid sparse incumbent changed representation"),
        )
        result = p.solve(objective, 5.0)
        assert result.status == "feasible" and p.witness_violation(result.x) < 1e-9
        assert p.budget.diagnostics[-1].representation_switches == 0
        assert all(a.representation == "sparse" for a in p.budget.diagnostics[-1].attempts)

    def test_no_valid_reference_cannot_relabel_true_infeasibility_as_numerical_retry(
        self, monkeypatch
    ) -> None:
        calls = 0

        def mixed(*args, **kwargs):
            nonlocal calls
            calls += 1
            return SimpleNamespace(status=2, x=None, fun=None, message="infeasible")

        monkeypatch.setattr("scipy.optimize.milp", mixed)
        p, objective = self.retry_program()
        p.witness_values[0] += 1000.0
        with pytest.raises(thermal._NoSolution, match="infeasible"):
            p.solve(objective, 5.0)
        assert calls == 2 and p.budget.diagnostics[-1].representation_switches == 0

    def test_sparse_cleanup_failure_uses_same_deadline_condensed_retry(self, monkeypatch) -> None:
        from scipy.optimize import linprog

        calls = 0

        def linear(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls <= 2:
                return SimpleNamespace(status=2, x=None, fun=None, message="fragile sparse cleanup")
            return linprog(*args, **kwargs)

        monkeypatch.setattr("scipy.optimize.linprog", linear)
        p, objective = self.retry_program()
        result = p.solve(objective, 5.0)
        assert result.objective == pytest.approx(0.96)
        assert p.witness_violation(result.x) < 1e-9
        assert p.budget.diagnostics[-1].representation_switches == 1
        assert [a.representation for a in p.budget.diagnostics[-1].attempts] == [
            "sparse",
            "sparse",
            "sparse",
            "condensed",
        ]
        assert p.budget.diagnostics[-1].attempts[-1].kind == "physical_cleanup_lp"
        assert result.status == "feasible" and result.lower_bound is None

    def test_expired_sparse_rejection_cannot_start_condensed_solver(self, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(thermal.time, "monotonic", clock)
        calls = 0

        def mixed(*args, **kwargs):
            nonlocal calls
            calls += 1
            clock.value = 11.0
            return SimpleNamespace(status=2, x=None, fun=None, message="late numerical rejection")

        monkeypatch.setattr("scipy.optimize.milp", mixed)
        p, objective = self.retry_program()
        with pytest.raises(thermal._NoSolution, match="deadline"):
            p.solve(objective, 10.0)
        assert calls == 1 and p.budget.diagnostics[-1].representation_switches == 0

    def test_kelvin_residual_does_not_substitute_for_absolute_flow_watts(self) -> None:
        step = (np.eye(3), np.zeros(3), np.zeros(3))
        source = thermal._SourceHorizon(
            outdoor_c=(0.0,),
            power_gain_scales=(1.0,),
            min_power_w=(0.0,),
            max_power_w=(2000.0,),
            steps=(step,) * 16,
            fingerprint="synthetic",
            model_kind="thermal_building",
            source_temperature_state_index=2,
            delivery_temperature_c=(20.0,),
            source_flow_conductance_w_per_k=(1e6,),
            additional_temperature_bounds_c=((0.0, 30.0),),
        )
        heat = np.array([1000.001])
        trajectory = np.array([[20.5, 20.5, 19.999], [20.5, 20.5, 19.999]])
        request = ThermalShadowRequest.model_validate(request_data(1))
        assert thermal._distribution_violation(request, source, heat, trajectory) < 1e-5
        assert thermal._source_flow_violation_w(source, heat, trajectory) == pytest.approx(
            0.001, abs=1e-7
        )
        assert thermal._source_flow_violation_w(source, np.zeros(1), trajectory) == 0.0

    @pytest.mark.parametrize("bad_call", [1, 3])
    def test_reference_and_candidate_require_the_separate_watt_guard(
        self, monkeypatch, bad_call
    ) -> None:
        calls = 0

        def flow(*args):
            nonlocal calls
            calls += 1
            # A persistent failure leaves no physically valid reference fallback.
            return 0.001 if calls >= bad_call else 0.0

        monkeypatch.setattr(thermal, "_source_flow_violation_w", flow)
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(request_data(2)))
        assert result.status == "error" and result.plan is None and result.candidate is None
        assert "verification" in result.reason

    @staticmethod
    def program(condense: bool = True):
        """An exact synthetic RC recurrence, with genuinely free ON decisions."""
        p = thermal._Program(thermal._PlanningBudget(10.0), "condensation_unit")
        p.initial_representation = "condensed" if condense else "sparse"
        heat = p.var(2, high=4000.0)
        states = p.var(3, low=20.0, high=23.0)
        p.lower[states[0]] = p.upper[states[0]] = 20.0
        on = p.var(2, high=1.0, binary=True)
        recurrences = []
        for k in range(2):
            recurrences.append((states[k + 1], len(p.rows)))
            p.eq({states[k + 1]: 1.0, states[k]: -0.5, heat[k]: -0.0025}, 5.0)
            p.row({heat[k]: 1.0, on[k]: -4000.0}, high=0.0)
            p.row({heat[k]: 1.0, on[k]: -1000.0}, low=0.0)
        p.eq({states[-1]: 1.0}, 20.0)
        if condense:
            p.thermal_state_substitution = thermal._ThermalStateSubstitution(
                initial=(states[0],), inputs=tuple(heat), recurrences=tuple(recurrences)
            )
        objective = np.zeros(len(p.lower))
        objective[heat] = [0.000025, 0.0002]
        objective[states[0]] = 0.03  # Nonzero eliminated objective constant 0.6.
        return p, heat, states, on, objective

    def test_same_original_optimum_and_bounds_with_correct_global_objective_constant(self) -> None:
        original, *_rest, objective = self.program(False)
        expected = original.solve(objective, 5.0)
        p, heat, states, on, objective = self.program()
        result = p.solve(objective, 5.0)
        assert result.status == "optimal"
        assert result.objective == pytest.approx(expected.objective, abs=1e-10)
        assert result.objective == pytest.approx(0.96, abs=1e-10)
        assert result.lower_bound == pytest.approx(result.objective, abs=1e-10)
        assert result.x[heat] == pytest.approx([3200.0, 1400.0])
        assert result.x[states] == pytest.approx([20.0, 23.0, 20.0])
        assert result.x[on] == pytest.approx([1.0, 1.0])
        assert result.x.shape == (len(p.lower),) and p.witness_violation(result.x) < 1e-9
        assert p.budget.diagnostics[-1].condensed_state_variables == 3
        assert p.budget.diagnostics[-1].condensed_dynamics_rows == 2

    def test_all_original_integer_choices_remain_free_in_reduced_mip(self, monkeypatch) -> None:
        from scipy.optimize import milp

        seen = []

        def mixed(*args, **kwargs):
            seen.append(kwargs)
            return milp(*args, **kwargs)

        monkeypatch.setattr("scipy.optimize.milp", mixed)
        p, *_rest, objective = self.program()
        p.solve(objective, 5.0)
        assert len(seen) == 1
        assert seen[0]["integrality"].tolist() == [0, 0, 1, 1]
        assert seen[0]["bounds"].lb[-2:].tolist() == [0.0, 0.0]
        assert seen[0]["bounds"].ub[-2:].tolist() == [1.0, 1.0]

    def test_eliminated_future_state_objective_and_bound_use_the_same_affine_map(self) -> None:
        p, heat, states, _on, objective = self.program()
        objective[states[1]] = 0.0001
        result = p.solve(objective, 5.0)
        assert result.x[heat] == pytest.approx([3200.0, 1400.0])
        assert result.objective == pytest.approx(0.9623, abs=1e-10)
        assert result.lower_bound == pytest.approx(result.objective, abs=1e-10)

    def test_slot_specific_gain_and_fixed_recovery_input_remain_exact(self) -> None:
        p, heat, states, _on, objective = self.program()
        metadata = p.thermal_state_substitution
        assert metadata is not None
        p.rows[metadata.recurrences[1][1]][heat[1]] = -0.003
        p.lower[heat[0]] = p.upper[heat[0]] = 2000.0
        result = p.solve(objective, 5.0)
        assert result.x[heat] == pytest.approx([2000.0, 5000.0 / 3])
        assert result.x[states] == pytest.approx([20.0, 20.0, 20.0])
        assert p.witness_violation(result.x) < 1e-9

    def test_projection_uses_registered_rows_and_adds_colliding_heat_terms(self) -> None:
        p, heat, states, _on, _objective = self.program()
        reduction = p._thermal_reduction()
        assert reduction is not None
        reduced = np.zeros(len(reduction.free_columns))
        reduced[:2] = [2400.0, 1800.0]
        full = reduction.projection @ reduced + reduction.offset
        assert full[states] == pytest.approx([20.0, 21.0, 20.0])
        # A row with both a source and its dependent state must ADD contributions.
        row = {heat[0]: 0.0025, states[1]: 1.0}
        projected = sum(value * reduction.projection.getrow(index) for index, value in row.items())
        assert float((projected @ reduced).item()) + sum(
            value * reduction.offset[index] for index, value in row.items()
        ) == pytest.approx(full[states[1]] + 0.0025 * full[heat[0]])

    def test_five_node_projection_preserves_slot_specific_transition_outside_and_gain(self) -> None:
        p = thermal._Program()
        heat = p.var(2, high=5000.0)
        states = [p.var(3, low=-100.0, high=100.0) for _ in range(5)]
        initial = np.array([20.0, 21.0, 25.0, 24.0, 28.0])
        for j in range(5):
            p.lower[states[j][0]] = p.upper[states[j][0]] = initial[j]
        powers, outside, scales = np.array([500.0, 1000.0]), [-5.0, 5.0], [2.0, 3.0]
        recurrences, expected = [], [initial]
        for k in range(2):
            a = np.eye(5) * (0.8 if k == 0 else 0.7)
            for j in range(5):
                a[j, (j + 1) % 5] = 0.1 if k == 0 else 0.2
            b = np.arange(1, 6) * 1e-4 if k == 0 else np.arange(5, 0, -1) * 2e-4
            e = np.ones(5) * 0.1
            for j in range(5):
                recurrences.append((states[j][k + 1], len(p.rows)))
                p.eq(
                    {
                        states[j][k + 1]: 1.0,
                        **{states[state_index][k]: -a[j, state_index] for state_index in range(5)},
                        heat[k]: -b[j] * scales[k],
                    },
                    float(e[j] * outside[k]),
                )
            expected.append(a @ expected[-1] + b * scales[k] * powers[k] + e * outside[k])
        p.thermal_state_substitution = thermal._ThermalStateSubstitution(
            initial=tuple(state[0] for state in states),
            inputs=tuple(heat),
            recurrences=tuple(recurrences),
        )
        reduction = p._thermal_reduction()
        assert reduction is not None
        restored = reduction.projection @ powers + reduction.offset
        assert np.array([restored[state] for state in states]).T == pytest.approx(
            np.array(expected), abs=1e-12
        )
        assert p.witness_violation(restored) < 1e-12

    def test_second_solve_rebuilds_reduction_and_retains_new_objective_bound(self) -> None:
        p, heat, states, _on, objective = self.program()
        first = p.solve(objective, 5.0)
        p.feasible_witness = first.x.copy()
        p.row({heat[0]: 1.0}, high=2400.0)
        second = p.solve(objective, 5.0)
        assert second.x[heat] == pytest.approx([2400.0, 1800.0])
        assert second.x[states] == pytest.approx([20.0, 21.0, 20.0])
        assert second.objective == pytest.approx(1.02, abs=1e-10)
        assert p.witness_violation(second.x) < 1e-9

    def test_production_stage_scope_preserves_sparse_reference_and_reduces_only_joint(self) -> None:
        p, _heat, _states, _on, objective = self.program()
        metadata = p.thermal_state_substitution
        assert metadata is not None
        p.thermal_state_substitution = replace(metadata, enabled_stages=("joint_candidate",))
        for stage in ("reference_tracking", "reference_heat_energy", "joint_candidate"):
            p.stage = stage
            result = p.solve(objective, 5.0)
            assert result.objective == pytest.approx(0.96, abs=1e-10)
            assert p.budget.diagnostics[-1].condensed_state_variables == (
                3 if stage == "joint_candidate" else 0
            )
            assert p.budget.diagnostics[-1].condensed_dynamics_rows == (
                2 if stage == "joint_candidate" else 0
            )

    def test_constant_state_contradiction_is_not_dropped(self, monkeypatch) -> None:
        p, _heat, states, _on, objective = self.program()
        p.row({states[0]: 1.0}, low=21.0)
        monkeypatch.setattr(
            "scipy.optimize.milp",
            lambda *a, **kw: pytest.fail("Contradictory state entered backend"),
        )
        with pytest.raises(thermal._NoSolution, match="constant bound"):
            p.solve(objective, 5.0)

    def test_invalid_registered_dynamics_are_rejected_instead_of_guessing_other_rows(self) -> None:
        p, _heat, _states, _on, objective = self.program()
        metadata = p.thermal_state_substitution
        assert metadata is not None
        pivot, row_id = metadata.recurrences[0]
        p.rows[row_id][pivot] = 0.5
        with pytest.raises(thermal._NoSolution, match="registered thermal recurrence"):
            p.solve(objective, 5.0)


class TestThermalShadowValidation:
    @pytest.mark.parametrize(
        "changes",
        [
            {"load_basis": "whole_house"},
            {"outdoor_temperature_c": [0.0]},
            {"max_electric_power_w": True},
            {"min_electric_power_w": 3000.0},
            {"smoothing_seconds": 901},
            {"max_absolute_gap_eur": float("nan")},
        ],
    )
    def test_invalid_explicit_inputs_are_rejected(self, changes):
        with pytest.raises(ValidationError):
            ThermalShadowRequest.model_validate({**request_data(), **changes})

    def test_non_quarter_hour_start_is_rejected(self):
        data = request_data()
        data["fleet"]["start_datetime"] += timedelta(minutes=1)
        with pytest.raises(ValidationError, match="UTC 900-second grid"):
            ThermalShadowRequest.model_validate(data)

    @pytest.mark.parametrize("channel", ["stale", "future", "state_time", "past_origin", "lease"])
    def test_unsupported_timestamps_do_not_enter_a_solver(self, monkeypatch, channel):
        data = request_data()
        if channel == "stale":
            data["state"]["based_on_observed_at"] -= timedelta(hours=1)
        elif channel == "future":
            data["state"]["based_on_observed_at"] = data["issued_at"] + timedelta(seconds=1)
        elif channel == "state_time":
            data["state"]["estimated_at"] += timedelta(minutes=1)
        elif channel == "past_origin":
            data["issued_at"] = data["fleet"]["start_datetime"] + timedelta(seconds=1)
        else:
            data["plan_ttl_seconds"] = 10

        def forbidden(*args, **kwargs):
            pytest.fail("Unsupported data must not call a solver")

        monkeypatch.setattr(thermal, "optimize_fleet", forbidden)
        monkeypatch.setattr(thermal._Program, "solve", forbidden)
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.status == "unsupported"
        assert result.plan is None and not result.execution_allowed

    def test_initial_cold_room_is_not_relabelled_comfortable(self):
        data = request_data()
        data["state"]["room_temperature_c"] = 19.0
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.status == "infeasible"
        assert "recovery" in result.reason
        assert not result.comparison_qualified

    def test_shadow_result_cannot_be_an_execution_permission(self):
        with pytest.raises(ValidationError):
            ThermalShadowSolution.model_validate(
                {
                    "status": "optimal",
                    "reason": "test",
                    "execution_allowed": True,
                }
            )


class TestJointThermalOptimization:
    def test_known_more_expensive_incumbent_is_replaced_by_fully_verified_reference(
        self, monkeypatch
    ) -> None:
        original = thermal._Program.solve

        def solve(program, objective, seconds):
            if program.stage != "joint_candidate":
                return original(program, objective, seconds)
            values = np.array([program.witness_values[i] for i in range(len(program.lower))])
            # Complete a strictly worse, still-feasible grid/curtailment cycle.
            for index, coefficient in enumerate(objective):
                if coefficient > 0 and program.upper[index] > 1000.0 and values[index] == 0.0:
                    for row in program.rows:
                        if row.get(index) == 1.0:
                            candidates = [
                                j
                                for j, value in row.items()
                                if value == -1.0
                                and program.upper[j] == 1000.0
                                and values[j] >= 100.0
                            ]
                            if candidates:
                                values[index] += 100.0
                                values[candidates[0]] += 100.0
                                assert program.witness_violation(values) <= 1e-5
                                return thermal._Result(
                                    values,
                                    float(objective @ values),
                                    None,
                                    None,
                                    "feasible",
                                    program.witness_violation(values),
                                )
            pytest.fail("Synthetic worse grid cycle was not located")

        monkeypatch.setattr(thermal._Program, "solve", solve)
        data = request_data(2, constant=True)
        data["fleet"].update(pv_w=[1000.0] * 2, export_allowed=[False] * 2)
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.plan is not None and result.status == "feasible", result.reason
        assert result.checks["candidate_worse_incumbent_replaced"] is True
        assert result.checks["candidate_plan_origin"] == "verified_reference_fallback"
        assert result.reference is not None and result.candidate is not None
        assert result.candidate.objective_eur == pytest.approx(result.reference.objective_eur)
        assert not result.comparison_qualified
        assert result.savings_cash_eur is None and result.savings_objective_eur is None

    def test_real_improved_candidate_keeps_joint_origin(self) -> None:
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(request_data(8)))
        assert (
            result.plan is not None
            and result.reference is not None
            and result.candidate is not None
        ), result.reason
        assert (
            result.reference.objective_eur is not None
            and result.candidate.objective_eur is not None
        )
        assert result.candidate.objective_eur < result.reference.objective_eur - 1e-5
        assert result.checks["candidate_worse_incumbent_replaced"] is False
        assert result.checks["candidate_plan_origin"] == "joint_optimization"

    def test_quarter_hour_power_is_valued_as_wh_not_watt_hours_squared(self):
        data = request_data(1, constant=True)
        data["fleet"].update(load_w=[1000.0], import_price_eur_per_kwh=[1.0])
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.status == "optimal"
        assert result.reference is not None and result.candidate is not None
        assert result.reference.grid_cost_eur == pytest.approx(0.25)
        assert result.candidate.grid_cost_eur == pytest.approx(0.25)
        assert result.candidate_heat_power_w == pytest.approx([0.0], abs=1e-5)

    def test_rc_electrical_input_units_and_internal_exchange_conservation(self):
        model = ThermalRC2Parameters(
            room_loss_per_hour=1e-10,
            room_mass_exchange_per_hour=2.0,
            mass_capacity_ratio=5.0,
            electric_gain_kelvin_per_kwh=4.0,
        )
        _, power, _ = thermal._dynamics(model, 900)
        response = power * 1000.0  # 1kW for0.25h, normalized gain4K/kWh.
        assert response[0] + 5 * response[1] == pytest.approx(1.0, abs=1e-9)

    @pytest.mark.parametrize("allow_export", [False, True])
    def test_zero_heat_has_native_fleet_parity_for_all_storage_constraints(self, allow_export):
        data = request_data(4, constant=True)
        data["fleet"].update(storage_devices())
        data["fleet"].update(
            pv_w=[0.0, 2000.0, 0.0, 0.0],
            export_allowed=[True, False, True, True],
            allow_battery_export=allow_export,
        )
        original = FleetRequest.model_validate(data["fleet"])
        native = optimize_fleet(original)
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert native.status == "optimal"
        assert result.status == "optimal"
        assert result.reference is not None and result.candidate is not None
        assert result.reference.objective_eur == pytest.approx(native.objective_eur, abs=1e-5)
        assert result.candidate.objective_eur == pytest.approx(native.objective_eur, abs=1e-5)
        ev = result.candidate.devices["ev1"]
        assert ev.energy_wh[3] >= 500 - 1e-5
        assert ev.charge_w[1] == pytest.approx(0.0, abs=1e-5)
        assert ev.charge_w[3] == pytest.approx(0.0, abs=1e-5)
        for power in ev.charge_w:
            assert min(abs(power - level) for level in [0, 1000, 2000]) < 1e-5
        assert result.candidate.export_w[1] == pytest.approx(0.0, abs=1e-5)

    def test_native_reference_not_initial_energy_sets_all_storage_end_budgets(self):
        data = request_data(4, constant=True)
        data["fleet"].update(storage_devices())
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.reference is not None and result.candidate is not None
        assert result.reference.devices["battery1"].energy_wh[-1] != pytest.approx(2000.0)
        for device in data["fleet"]["devices"]:
            name = device["device_id"]
            assert result.candidate.devices[name].energy_wh[-1] == pytest.approx(
                result.reference.devices[name].energy_wh[-1], abs=1e-5
            )
        assert result.checks["storage_terminal_states_match"] is True
        assert result.savings_cash_eur is not None

    def test_initial_energy_below_reserve_is_never_raised_for_free(self):
        data = request_data(4, constant=True)
        device = storage_devices()["devices"][0]
        device.update(
            initial_energy_wh=100.0,
            max_charge_power_w=0.0,
            min_charge_power_w=0.0,
            terminal_value_eur_per_kwh=0.0,
        )
        data["fleet"]["devices"] = [device]
        device.pop("power_group")
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.candidate is not None
        battery = result.candidate.devices["battery1"]
        assert battery.energy_wh == pytest.approx([100.0] * 5, abs=1e-5)
        assert battery.discharge_w == pytest.approx([0.0] * 4, abs=1e-5)

    def test_price_shift_is_joint_and_does_not_borrow_warmth_or_change_input(self):
        request = ThermalShadowRequest.model_validate(request_data(16))
        before = request.model_dump_json()
        result = optimize_thermal_shadow(request)
        assert result.status in ("optimal", "feasible"), result.reason
        assert result.comparison_qualified
        assert result.savings_objective_eur is not None and result.savings_objective_eur > 0
        assert result.reference is not None and result.candidate is not None
        assert request.model_dump_json() == before
        assert result.candidate_room_temperature_c[-1] == pytest.approx(
            result.reference_room_temperature_c[-1], abs=1e-5
        )
        assert result.candidate_mass_temperature_c[-1] == pytest.approx(
            result.reference_mass_temperature_c[-1], abs=1e-5
        )
        comfort_violation = result.checks["max_minute_comfort_violation_c"]
        assert isinstance(comfort_violation, float) and comfort_violation <= 1e-5
        assert result.plan is not None and len(result.plan.intervals) == 16
        assert result.plan.intervals[0].start_datetime.utcoffset() == timedelta(0)
        assert not result.execution_allowed
        assert result.checks["energy_reduction_required"] is False

    def test_smoothing_is_a_constraint_not_posthoc_temperature_filtering(self):
        data = request_data(8)
        data["smoothing_seconds"] = 1800
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.candidate is not None, result.reason
        for k in range(0, 8, 2):
            assert result.candidate_heat_power_w[k] == pytest.approx(
                result.candidate_heat_power_w[k + 1], abs=1e-5
            )

    def test_minimum_heating_power_is_respected_or_off(self):
        data = request_data(8)
        data["min_electric_power_w"] = 700.0
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.candidate is not None, result.reason
        assert all(q <= 1e-5 or q >= 700 - 1e-5 for q in result.candidate_heat_power_w)

    def test_interior_comfort_failure_cannot_hide_between_valid_endpoints(self):
        data = request_data(1)
        data["state"].update(room_temperature_c=20.0, mass_temperature_c=30.0)
        data["model"].update(
            room_loss_per_hour=2.0, room_mass_exchange_per_hour=20.0, mass_capacity_ratio=1.0
        )
        data.update(
            outdoor_temperature_c=[8.0],
            max_electric_power_w=1.0,
            comfort_bands=[
                {"min_temperature_c": 19.5, "target_temperature_c": 20.5, "max_temperature_c": 21.5}
            ],
        )
        request = ThermalShadowRequest.model_validate(data)
        a, _, e = thermal._dynamics(request.model, 900)
        endpoint = a @ np.array([20.0, 30.0]) + e * 8.0
        assert 19.5 <= endpoint[0] <= 21.5  # Endpoint-only checking would miss the hot excursion.
        result = optimize_thermal_shadow(request)
        assert result.status == "infeasible"
        assert result.plan is None

    def test_big_m_bounds_are_derived_not_physical_grid_assumptions(self):
        data = request_data(4, constant=True)
        data["fleet"].update(import_limit_w=1e6, export_limit_w=1e6)
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.candidate is not None
        assert result.checks["derived_import_bound_w"] == 2300.0
        assert result.checks["derived_export_bound_w"] == 0.0
        assert result.checks["original_import_limit_w"] == 1e6
        assert result.checks["grid_bounds_are_mathematical_not_physical"] is True

    def test_wide_candidate_solver_gap_blocks_savings_claim(self, monkeypatch):
        original = thermal._Program.solve

        def solve(self, objective, seconds):
            result = original(self, objective, seconds)
            if any(self.integer):
                return replace(result, lower_bound=result.objective - 1.0, status="feasible")
            return result

        monkeypatch.setattr(thermal._Program, "solve", solve)
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(request_data(4)))
        assert result.candidate is not None
        assert not result.comparison_qualified
        assert result.savings_cash_eur is None and result.savings_objective_eur is None

    def test_wide_reference_solver_gap_also_blocks_savings_claim(self, monkeypatch):
        def reference(request):
            return optimize_fleet(request).model_copy(
                update={"status": "feasible", "relative_gap": 0.5}
            )

        monkeypatch.setattr(thermal, "optimize_fleet", reference)
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(request_data(4)))
        assert result.candidate is not None
        assert not result.comparison_qualified and result.savings_objective_eur is None

    def test_invalid_native_reference_does_not_publish_a_candidate(self):
        data = request_data(4, constant=True)
        data["fleet"].update(import_limit_w=0.0, pv_w=[0.0] * 4)
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.status == "infeasible"
        assert result.candidate is None and result.plan is None

    @pytest.mark.parametrize("output", [float("nan"), 2.0])
    def test_nonfinite_or_infeasible_solver_output_is_rejected(self, monkeypatch, output):
        import scipy.optimize

        monkeypatch.setattr(
            scipy.optimize,
            "milp",
            lambda *a, **kw: SimpleNamespace(status=0, x=np.array([output]), fun=0.0),
        )
        program = thermal._Program()
        program.var(1, high=1, binary=True)
        with pytest.raises(thermal._NoSolution):
            program.solve(np.zeros(1), 1.0)


class TestCommonColdRecovery:
    def test_cold_prefix_is_identical_paid_and_diagnosed_without_relabelling_comfort(self):
        data = request_data(16)
        data["state"].update(room_temperature_c=19.0, mass_temperature_c=19.0)
        data["recovery"] = {"max_duration_seconds": 14400}
        data["smoothing_seconds"] = 1800
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.status == "optimal", result.reason
        assert result.recovery is not None and result.recovery.completed
        slots = len(result.recovery.heat_power_w)
        assert 0 < slots < 16
        assert result.reference_heat_power_w[:slots] == pytest.approx(
            result.recovery.heat_power_w, abs=1e-5
        )
        assert result.candidate_heat_power_w[:slots] == pytest.approx(
            result.recovery.heat_power_w, abs=1e-5
        )
        assert result.reference is not None and result.candidate is not None
        assert result.reference.parameters is not None and result.candidate.parameters is not None
        for fleet, heat in [
            (result.reference, result.reference_heat_power_w),
            (result.candidate, result.candidate_heat_power_w),
        ]:
            assert fleet.parameters is not None
            assert fleet.parameters.load_w == pytest.approx([300 + power for power in heat])
        assert result.recovery.cold_minutes > 0
        assert result.recovery.cold_degree_hours > 0
        assert result.recovery.maximum_cold_deficit_c == pytest.approx(1.0)
        assert result.recovery.native_tracking_validated is False
        assert result.plan is not None and result.plan.intervals[0].min_temperature_c == 20.0
        assert result.plan.intervals[0].target_temperature_c >= 20.0
        assert result.checks["recovery_prefix_identical"] is True
        violation = result.checks["max_minute_comfort_violation_c"]
        assert isinstance(violation, float) and violation <= 1e-5
        assert "identical target-limited cold recovery" in result.reason

    def test_recovery_power_is_limited_by_minute_upper_comfort(self):
        data = request_data(8)
        data["state"].update(room_temperature_c=19.0, mass_temperature_c=19.0)
        data["max_electric_power_w"] = 20000.0
        data["recovery"] = {"max_duration_seconds": 3600}
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.candidate is not None and result.recovery is not None, result.reason
        assert result.recovery.heat_power_w[0] < 20000.0
        request = ThermalShadowRequest.model_validate(data)
        initial = np.array([19.0, 19.0])
        for minute in range(16):
            a, b, e = thermal._dynamics(request.model, minute * 60)
            room = (a @ initial + b * result.recovery.heat_power_w[0] + e * 0.0)[0]
            assert room <= 21.0 + 1e-8

    def test_insufficient_recovery_reports_cold_deficit_without_economic_claim(self):
        data = request_data(8)
        data["state"].update(room_temperature_c=18.0, mass_temperature_c=18.0)
        data["max_electric_power_w"] = 300.0
        data["recovery"] = {"max_duration_seconds": 900}
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.status == "infeasible"
        assert result.recovery is not None and not result.recovery.completed
        assert result.recovery.cold_minutes == 15
        assert result.recovery.heat_power_w == (300.0,)
        assert result.savings_cash_eur is None and result.plan is None

    def test_recovery_does_not_waive_an_initial_upper_comfort_failure(self):
        data = request_data()
        data["state"]["room_temperature_c"] = 22.0
        data["recovery"] = {"max_duration_seconds": 3600}
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.status == "infeasible" and result.recovery is None

    @pytest.mark.parametrize("value", [0, 901, True, 900.5])
    def test_recovery_duration_is_positive_on_the_control_grid(self, value):
        data = request_data()
        data["recovery"] = {"max_duration_seconds": value}
        with pytest.raises(ValidationError):
            ThermalShadowRequest.model_validate(data)


class TestElectricalAccountingAndEconomics:
    @pytest.mark.parametrize("initial_running", [False, True])
    def test_negative_electricity_objective_cannot_invent_off_to_on_starts(self, initial_running):
        data = request_data(4)
        data["comfort_bands"] = [
            {"min_temperature_c": 16.0, "target_temperature_c": 20.5, "max_temperature_c": 30.0}
        ] * 4
        request = ThermalShadowRequest.model_validate(data)
        source = thermal._SourceHorizon(
            outdoor_c=request.outdoor_temperature_c,
            power_gain_scales=(1.0,) * 4,
            min_power_w=(250.0,) * 4,
            max_power_w=(2000.0,) * 4,
            steps=tuple(thermal._dynamics(request.model, minute * 60) for minute in range(16)),
            fingerprint="test",
            model_kind="test",
            standby_power_w=5.0,
            running_auxiliary_power_w=50.0,
            startup_energy_wh=30.0,
            initial_running=initial_running,
        )
        program, heat, _, _, _, electric = thermal._thermal_program(request, source)
        forced = np.array([500.0, 0.0, 500.0, 500.0])
        for index, value in zip(heat, forced):
            program.lower[index] = program.upper[index] = value
        objective = np.zeros(len(program.lower))
        objective[electric] = -1.0  # Rewarding power must not create fake startup binaries.
        result = program.solve(objective, 5.0)
        expected = [555.0 + (0.0 if initial_running else 120.0), 5.0, 675.0, 555.0]
        assert result.x[electric] == pytest.approx(expected, abs=1e-5)
        assert thermal._source_electric_power(source, forced) == pytest.approx(expected)

    @pytest.mark.parametrize("pv", [False, True])
    def test_joint_value_is_additional_to_native_battery_with_identical_budgets(self, pv):
        data = request_data(16)
        data["fleet"]["devices"] = [
            {
                "device_id": "battery1",
                "kind": "battery",
                "capacity_wh": 1000.0,
                "initial_energy_wh": 500.0,
                "reserve_wh": 100.0,
                "max_charge_power_w": 1000.0,
                "max_discharge_power_w": 1000.0,
                "charge_efficiency": 0.9,
                "discharge_efficiency": 0.9,
                "terminal_value_eur_per_kwh": 0.2,
                "discharge_cost_eur_per_kwh": 0.02,
            }
        ]
        if pv:
            data["fleet"]["pv_w"] = [1500.0] * 8 + [0.0] * 8
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.comparison_qualified, result.reason
        assert result.reference is not None and result.candidate is not None
        assert (
            result.reference.objective_eur is not None
            and result.candidate.objective_eur is not None
        )
        assert result.candidate.objective_eur <= result.reference.objective_eur + 1e-5
        for fleet in (result.reference, result.candidate):
            cash = sum(
                (imported * buy - exported * sell) * 0.25 / 1000
                for imported, buy, exported, sell in zip(
                    fleet.import_w,
                    data["fleet"]["import_price_eur_per_kwh"],
                    fleet.export_w,
                    data["fleet"]["export_price_eur_per_kwh"],
                )
            )
            device = fleet.devices["battery1"]
            objective = (
                cash
                + sum(device.discharge_w) * 0.25 / 1000 * 0.02
                - device.energy_wh[-1] / 1000 * 0.2
            )
            assert fleet.grid_cost_eur == pytest.approx(cash, abs=1e-5)
            assert fleet.objective_eur == pytest.approx(objective, abs=1e-5)
        assert result.candidate.devices["battery1"].energy_wh[-1] == pytest.approx(
            result.reference.devices["battery1"].energy_wh[-1], abs=1e-5
        )
        assert result.candidate_room_temperature_c[-1] == pytest.approx(
            result.reference_room_temperature_c[-1], abs=1e-5
        )
        assert result.candidate_mass_temperature_c[-1] == pytest.approx(
            result.reference_mass_temperature_c[-1], abs=1e-5
        )


class TestLoadDependentSourceSegments:
    def test_near_integer_mip_value_is_polished_before_it_becomes_a_tracking_bound(
        self, monkeypatch
    ):
        import scipy.optimize

        program = thermal._Program()
        on = program.var(1, low=0.5, high=1, binary=True)[0]
        continuous = program.var(1, high=10)[0]
        program.row({continuous: 1, on: -1}, low=0)
        epsilon_value = 1.0 - 5e-7
        monkeypatch.setattr(
            scipy.optimize,
            "milp",
            lambda *args, **kwargs: SimpleNamespace(
                status=0,
                x=np.array([epsilon_value, epsilon_value]),
                fun=epsilon_value,
                mip_dual_bound=epsilon_value,
                mip_gap=0.0,
            ),
        )
        result = program.solve(np.array([0.0, 1.0]), 5.0)
        assert result.x == pytest.approx([1.0, 1.0], abs=1e-10)
        assert result.objective == 1.0
        assert result.lower_bound == epsilon_value  # Keep the global MIP bound.
        assert result.gap is not None and result.gap > 0
        program.row({continuous: 1}, high=result.objective + 1e-8)
        verified_again = program.solve(np.array([0.0, 1.0]), 5.0)
        assert verified_again.objective == 1.0

    def test_electric_floor_inversion_uses_first_point_on_flat_power_plateau(self):
        points = ((1000.0, 200.0), (2000.0, 200.0), (3000.0, 1200.0))
        assert thermal._input_at_electric_floor(points, 200.0) == 1000.0
        assert thermal._input_at_electric_floor(points, 700.0) == 2500.0
        with pytest.raises(ValueError, match="minimum electricity exceeds"):
            thermal._input_at_electric_floor(points, 1300.0)

    @pytest.mark.parametrize("objective_sign", [-1.0, 1.0])
    @pytest.mark.parametrize(
        "thermal_power, expected_electric", [(1500.0, 600.0), (2000.0, 1000.0), (2500.0, 1100.0)]
    )
    def test_only_adjacent_pairs_can_interpolate_even_when_hull_chord_is_cheaper(
        self, objective_sign, thermal_power, expected_electric
    ):
        data = request_data(1)
        data["comfort_bands"] = [
            {"min_temperature_c": 16.0, "target_temperature_c": 20.5, "max_temperature_c": 30.0}
        ]
        request = ThermalShadowRequest.model_validate(data)
        source = thermal._SourceHorizon(
            outdoor_c=(0.0,),
            power_gain_scales=(1.0,),
            min_power_w=(1000.0,),
            max_power_w=(3000.0,),
            steps=tuple(thermal._rc2_dynamics(0.1, 0.2, 5.0, 1.0, m * 60) for m in range(16)),
            fingerprint="test",
            model_kind="piecewise_thermal",
            operating_points=(((1000.0, 200.0), (2000.0, 1000.0), (3000.0, 1200.0)),),
        )
        program, heat, _, _, _, electric = thermal._thermal_program(request, source)
        program.lower[heat[0]] = program.upper[heat[0]] = thermal_power
        objective = np.zeros(len(program.lower))
        objective[electric] = objective_sign
        result = program.solve(objective, 5.0)
        assert result.x[electric] == pytest.approx([expected_electric], abs=1e-5)
        assert thermal._source_electric_power(source, np.array([thermal_power])) == pytest.approx(
            [expected_electric], abs=1e-5
        )
        assert thermal._input_at_electric_cap(source, 0, 900.0) == pytest.approx(1875.0)
        assert thermal._input_at_electric_cap(source, 0, 150.0) == 0.0
