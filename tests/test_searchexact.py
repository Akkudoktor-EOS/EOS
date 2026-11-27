"""The search finds the optimum of tiny problems that can be solved by trying every plan.

A single battery, a single vehicle and a single appliance without other devices: the search must
reach the objective of the best plan of the complete enumeration (up to 1e-9 EUR). For two
batteries the search is a heuristic; the test requires that it is not worse than the start and
prints the gap to the optimum.
"""

import itertools
from dataclasses import replace
from typing import Any

import numpy as np
import pytest
import test_searchscenarios as sc

from akkudoktoreos.devices.models.appliance import ApplianceParams
from akkudoktoreos.optimization.kernel import Plan, PlanningProblem
from akkudoktoreos.optimization.objective import Objective
from akkudoktoreos.optimization.search import (
    ApplianceSchedule,
    DeviceSearch,
    SearchSettings,
    SearchSpace,
)

TOLERANCE = 1e-9


def tiny_series(seed: int, slots: int) -> sc.Series:
    """Return a short synthetic day with PV, a price peak and a varying load."""
    rng = np.random.default_rng(seed)
    pv = np.array([0, 0, 0, 300, 900, 1200, 700, 0, 0, 0], dtype=float)[:slots]
    price = np.array([0.1, 0.1, 0.15, 0.25, 0.3, 0.4, 0.4, 0.2, 0.15, 0.1])[:slots] * 1e-3
    return sc.Series(
        "tiny",
        pv * rng.uniform(0.5, 1.5),
        rng.uniform(250.0, 750.0, slots),
        price * rng.uniform(0.7, 1.3),
        np.full(slots, 5e-5),
    )


def best_of(objective: Any, plans: Any) -> float:
    """Return the lowest objective over an iterable of plans (evaluated in batches)."""
    best = np.inf
    chunk: list[Plan] = []
    for plan in plans:
        chunk.append(plan)
        if len(chunk) >= 4096:
            best = min(best, float(objective.evaluate_batch(chunk).total_eur.min()))
            chunk = []
    if chunk:
        best = min(best, float(objective.evaluate_batch(chunk).total_eur.min()))
    return best


def battery_plans(
    problem: PlanningProblem, actions: list[tuple[float, float, float]], batteries: int = 1
) -> Any:
    """Yield every plan in which each battery takes one of the (grid, discharge, PV) actions per slot."""
    slots = problem.slots
    table = np.asarray(actions)
    base = Plan.idle(problem)
    width = slots * batteries
    for number in range(len(actions) ** width):
        digits = np.array(
            [(number // len(actions) ** position) % len(actions) for position in range(width)]
        ).reshape(batteries, slots)
        yield Plan(
            battery_grid_charge=table[digits, 0],
            battery_discharge_limit=table[digits, 1],
            battery_pv_charge=table[digits, 2],
            battery_export=base.battery_export,
            vehicle_charge=base.vehicle_charge,
            appliance_starts=base.appliance_starts,
        )


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_battery_alone_finds_the_optimum(seed: int) -> None:
    """Three levels (grid 0.5, grid 1.0, discharge on), 8 slots: 65,536 plans."""
    problem, objective = sc.build(
        tiny_series(seed, 8),
        "A",
        slots=8,
        battery_rates=(0.0, 0.5, 1.0),
        vehicles=(),
        batteries=((6000.0, 20.0, 10.0, 3000.0),),
    )
    settings = SearchSettings(discharge_levels=(1.0,), pv_charge_switchable=False)
    result = DeviceSearch(problem, objective, settings=settings).solve()
    actions = [(0.0, 0.0, 1.0), (0.5, 0.0, 1.0), (1.0, 0.0, 1.0), (0.0, 1.0, 1.0)]
    optimum = best_of(objective, battery_plans(problem, actions))
    assert result.objective.total_eur == pytest.approx(optimum, abs=TOLERANCE)
    assert result.violations == []


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_battery_with_pv_switch_and_ceilings(seed: int) -> None:
    """Ceilings 0.5 and 1.0 with PV charging on and off, grid levels 0.5 and 1.0, 5 slots."""
    problem, objective = sc.build(
        tiny_series(seed, 5),
        "A",
        slots=5,
        battery_rates=(0.0, 0.5, 1.0),
        vehicles=(),
        batteries=((6000.0, 20.0, 10.0, 3000.0),),
    )
    settings = SearchSettings(discharge_levels=(0.5, 1.0))
    result = DeviceSearch(problem, objective, settings=settings).solve()
    actions = [(0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.5, 0.0, 1.0), (1.0, 0.0, 1.0)]
    actions += [(0.0, d, pv) for d in (0.5, 1.0) for pv in (0.0, 1.0)]
    optimum = best_of(objective, battery_plans(problem, actions))
    assert result.objective.total_eur == pytest.approx(optimum, abs=TOLERANCE)
    assert result.violations == []


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_vehicle_alone_finds_the_optimum(seed: int) -> None:
    """A vehicle with levels 0, 0.5 and 1.0 and a target at the end: 3^8 plans."""
    slots = 8
    vehicle = sc.Vehicle(30000.0, 40.0, 11000.0, 0, 100, 70.0)
    problem, objective = sc.build(
        tiny_series(seed, slots), "A", slots=slots, batteries=(), vehicles=(vehicle,)
    )
    unit = replace(problem.vehicles[0], charge_rates=(0.0, 0.5, 1.0))
    problem = replace(problem, vehicles=(unit,))
    objective = Objective(problem, objective.settings)
    result = DeviceSearch(problem, objective).solve()
    plans = []
    for combo in itertools.product((0.0, 0.5, 1.0), repeat=slots):
        plan = Plan.idle(problem)
        plan.vehicle_charge[0] = combo
        plans.append(plan)
    optimum = best_of(objective, plans)
    assert result.objective.total_eur == pytest.approx(optimum, abs=TOLERANCE)
    assert result.violations == []


@pytest.mark.parametrize(("seed", "cycles"), [(1, 1), (2, 1), (3, 2), (4, 2)])
def test_appliance_alone_finds_the_optimum(seed: int, cycles: int) -> None:
    """Start slots of one or two cycles from a list of allowed slots with a minimum gap."""
    slots = 10
    problem, objective = sc.build(
        tiny_series(seed, slots), "A", slots=slots, batteries=(), vehicles=()
    )
    problem = replace(
        problem,
        appliances=(ApplianceParams(run_energy_wh=(900.0, 600.0, 300.0)),),
        appliance_cycles=(cycles,),
    )
    objective = Objective(problem, objective.settings)
    allowed = (np.array([0, 1, 2, 3, 5, 6, 7], dtype=np.int64),) * cycles
    space = SearchSpace((ApplianceSchedule(allowed, min_gap_slots=3),))
    result = DeviceSearch(problem, objective, space=space).solve()
    best = np.inf
    for combo in itertools.product(*[range(len(a)) for a in allowed]):
        starts = [int(allowed[c][i]) for c, i in enumerate(combo)]
        if any(b - a < 3 for a, b in zip(starts, starts[1:])):
            continue
        plan = Plan.idle(problem)
        plan.appliance_starts[0, :cycles] = starts
        best = min(best, float(objective.evaluate_batch([plan]).total_eur[0]))
    assert result.objective.total_eur == pytest.approx(best, abs=TOLERANCE)
    assert result.violations == []


@pytest.mark.parametrize("seed", [1, 3])
def test_two_batteries_are_close_to_the_optimum(seed: int) -> None:
    """Two batteries, 5 slots, actions PV only / grid 1.0 / discharge on: 3^10 plans."""
    problem, objective = sc.build(
        tiny_series(seed, 5),
        "A",
        slots=5,
        battery_rates=(0.0, 1.0),
        vehicles=(),
        batteries=((6000.0, 20.0, 10.0, 3000.0), (4000.0, 30.0, 10.0, 2000.0)),
    )
    settings = SearchSettings(discharge_levels=(1.0,), pv_charge_switchable=False)
    result = DeviceSearch(problem, objective, settings=settings).solve()
    actions = [(0.0, 0.0, 1.0), (1.0, 0.0, 1.0), (0.0, 1.0, 1.0)]
    optimum = best_of(objective, battery_plans(problem, actions, batteries=2))
    gap = result.objective.total_eur - optimum
    assert gap >= -TOLERANCE
    assert result.objective.total_eur <= result.start_objective.total_eur
    print(f"two batteries seed {seed}: gap to the optimum {gap:.3e} EUR")
