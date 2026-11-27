"""The objective helpers the planners use agree with the objective itself."""

import numpy as np
import pytest
import test_searchscenarios as sc

from akkudoktoreos.optimization.kernel import simulate
from akkudoktoreos.optimization.search import empty_plan


def test_terminal_credit_equals_the_part_of_the_objective() -> None:
    """The credit for the final states matches the terminal part of a simulated plan."""
    problem, objective = sc.build(sc.make_series("winter_clear"), "B", slots=96)
    plan = empty_plan(problem)
    plan.vehicle_charge[0, :20] = 0.5
    result = simulate(problem, plan)
    breakdown = objective.evaluate(plan, result)
    assert objective.terminal_credit(result.final_soc_wh) == pytest.approx(
        breakdown.terminal_value_eur, abs=1e-12
    )


def test_terminal_credit_many_equals_the_scalar_credit() -> None:
    """The credit of many final states is the credit of each of them."""
    problem, objective = sc.build(sc.make_series("winter_clear"), "B", slots=96)
    result = simulate(problem, empty_plan(problem))
    rng = np.random.default_rng(3)
    finals = result.final_soc_wh * rng.uniform(0.0, 1.6, size=(4, 5, result.final_soc_wh.shape[0]))
    many = objective.terminal_credit_many(finals)
    assert many.shape == (4, 5)
    for index in np.ndindex(4, 5):
        assert many[index] == pytest.approx(objective.terminal_credit(finals[index]), abs=1e-9)


def test_break_even_cost_per_level_sums_to_the_penalty() -> None:
    """The penalty of grid charging is the sum of level times the cost per level."""
    problem, objective = sc.build(sc.make_series("winter_clear"), "A", slots=96)
    plan = empty_plan(problem)
    levels = np.zeros(96)
    levels[30:60] = 0.7
    levels[0] = 0.3
    plan.battery_grid_charge[0] = levels
    breakdown = objective.evaluate(plan, simulate(problem, plan))
    expected = float(np.sum(levels * objective.break_even_cost_per_level(0)))
    assert breakdown.ac_break_even_penalty_eur == pytest.approx(expected, abs=1e-12)
    assert breakdown.ac_break_even_penalty_eur > 0.0


def test_vehicle_target_accessor() -> None:
    """The active target and deadline of a vehicle are reported as given."""
    _, objective = sc.build(sc.make_series("winter_clear"), "B", slots=192)
    assert objective.vehicle_target(0) == (80.0, 76)
    assert objective.vehicle_target(2) == (75.0, 140)
    _, short = sc.build(sc.make_series("winter_clear"), "B", slots=96)
    assert short.vehicle_target(2) == (75.0, None)
