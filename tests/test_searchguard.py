"""Guard, allowed values, repeatability, warm start and event re-planning of the search."""

from dataclasses import replace
from typing import Any

import numpy as np
import pytest
import test_searchscenarios as sc

from akkudoktoreos.devices.models.appliance import ApplianceParams
from akkudoktoreos.devices.models.inverter import InverterParams
from akkudoktoreos.devices.models.storage import StorageParams
from akkudoktoreos.optimization.kernel import Plan, PlanningProblem, simulate
from akkudoktoreos.optimization.objective import FullChargeTarget, Objective
from akkudoktoreos.optimization.search import (
    ApplianceSchedule,
    DeviceRef,
    DeviceSearch,
    SearchResult,
    SearchSettings,
    SearchSpace,
    check_plan,
    empty_plan,
    repair_plan,
    shift_plan,
)
from akkudoktoreos.optimization.search.actions import battery_actions, normalize_discharge
from akkudoktoreos.optimization.search.warmstart import vehicle_rule_plan

PLANTS = [
    ("A", "winter_clear"),
    ("A", "negative_prices"),
    ("A", "price_spike"),
    ("B", "price_spike"),
]


def solve(
    plant: str, scenario: str, slots: int = 96, **kwargs: Any
) -> tuple[PlanningProblem, Objective, DeviceSearch, SearchResult]:
    """Search a plant on the first ``slots`` slots of a scenario."""
    problem, objective = sc.build(sc.make_series(scenario), plant, slots=slots)
    settings = kwargs.pop("settings", SearchSettings())
    search = DeviceSearch(problem, objective, settings=settings)
    return problem, objective, search, search.solve(**kwargs)


@pytest.mark.parametrize(("plant", "scenario"), PLANTS)
def test_objective_falls_monotonically_and_plan_is_valid(plant: str, scenario: str) -> None:
    """The objective falls with every accepted move, never ends above the start, no violations."""
    problem, objective, _, result = solve(plant, scenario)
    assert result.violations == []
    assert result.trace[0] == pytest.approx(result.start_objective.total_eur)
    assert all(b < a for a, b in zip(result.trace, result.trace[1:]))
    assert result.objective.total_eur <= result.start_objective.total_eur
    assert result.objective.total_eur == pytest.approx(result.trace[-1], abs=1e-12)
    # The reported objective is the one of an independent simulation of the plan.
    check = objective.evaluate(result.plan, simulate(problem, result.plan))
    assert check.total_eur == pytest.approx(result.objective.total_eur, abs=1e-12)
    assert check_plan(problem, result.plan) == []


def test_two_runs_give_bit_identical_plans() -> None:
    """Same input, same plan, to the last bit."""
    _, _, _, first = solve("B", "winter_clear", slots=48)
    _, _, _, second = solve("B", "winter_clear", slots=48)
    for name in (
        "battery_grid_charge",
        "battery_discharge_limit",
        "battery_pv_charge",
        "battery_export",
        "vehicle_charge",
        "appliance_starts",
    ):
        assert np.array_equal(getattr(first.plan, name), getattr(second.plan, name))
    assert first.objective.total_eur == second.objective.total_eur


def test_a_given_plan_is_never_made_worse() -> None:
    """Start from a poor, valid plan and from the result: the objective does not rise."""
    problem, _, search, first = solve("A", "winter_clear", slots=96)
    again = search.solve(first.plan)
    assert again.objective.total_eur <= first.objective.total_eur
    start = empty_plan(problem)
    start.vehicle_charge[0, :] = 1.0
    worse = search.solve(repair_plan(problem, start))
    assert worse.objective.total_eur <= worse.start_objective.total_eur


def test_pair_moves_do_not_hurt_and_are_counted() -> None:
    """Pair moves (off by default) are tried and counted; the result is not worse than without."""
    pairs = SearchSettings(pair_moves=("ee", "eb", "bb"))
    _, _, _, with_pairs = solve("B", "price_spike", slots=96, settings=pairs)
    _, _, _, without = solve("B", "price_spike", slots=96, settings=SearchSettings(pair_moves=()))
    tried = sum(
        v
        for k, v in {**with_pairs.stats.accepted, **with_pairs.stats.rejected}.items()
        if k.startswith("pair")
    )
    assert tried > 0
    assert with_pairs.objective.total_eur <= without.objective.total_eur + 1e-9


def test_check_plan_finds_every_kind_of_violation() -> None:
    """Values outside the allowed set are reported."""
    away = sc.Vehicle(40000.0, 30.0, 11000.0, 0, 40, 80.0)
    problem, _ = sc.build(sc.make_series("winter_clear"), "A", slots=48, vehicles=(away,))
    plan = empty_plan(problem)
    assert check_plan(problem, plan) == []
    plan.battery_grid_charge[0, 3] = 0.37  # not a level
    plan.battery_grid_charge[0, 5] = 0.5  # grid charging together with discharging (ceiling 1.0)
    plan.battery_discharge_limit[0, 6] = 0.33
    plan.battery_export[0, 7] = 0.5  # no direct marketing
    plan.vehicle_charge[0, 45] = 0.5  # vehicle away (departs at slot 40)
    reasons = {v.reason for v in check_plan(problem, plan)}
    assert "grid charge level not allowed" in reasons
    assert "grid charging together with discharging" in reasons
    assert "discharge ceiling not allowed" in reasons
    assert "export level not allowed" in reasons
    assert "charging while away" in reasons
    repaired = repair_plan(problem, plan)
    assert check_plan(problem, repaired) == []


def test_vehicle_is_not_charged_while_away_or_full() -> None:
    """Charging outside the plug-in window or at full state of charge is a violation."""
    vehicle = sc.Vehicle(10000.0, 95.0, 11000.0, 4, 20, 100.0)
    problem, _ = sc.build(sc.make_series("winter_clear"), "A", slots=32, vehicles=(vehicle,))
    plan = empty_plan(problem)
    plan.vehicle_charge[0, 0] = 1.0  # away
    plan.vehicle_charge[0, 8:12] = 1.0  # fills up, the later slots start full
    reasons = {v.reason for v in check_plan(problem, plan)}
    assert "charging while away" in reasons
    assert "charging at full state of charge" in reasons
    assert check_plan(problem, repair_plan(problem, plan)) == []


def test_levels_of_the_variants_are_respected() -> None:
    """Plain on/off discharging and a fine raster give plans that pass their own check."""
    series = sc.make_series("price_spike")
    for settings, rates in (
        (SearchSettings(discharge_levels=(1.0,), pv_charge_switchable=False), sc.STD_RATES),
        (
            SearchSettings(discharge_levels=tuple(j / 20 for j in range(1, 21))),
            tuple(j / 20 for j in range(21)),
        ),
    ):
        problem, objective = sc.build(series, "A", slots=96, battery_rates=rates)
        result = DeviceSearch(problem, objective, settings=settings).solve()
        assert result.violations == []
        if len(settings.discharge_levels) == 1:
            assert set(np.unique(result.plan.battery_discharge_limit)) <= {0.0, 1.0}
            assert set(np.unique(result.plan.battery_pv_charge)) == {1.0}


def test_fill_the_grid_limit_is_an_action_when_the_raster_lacks_the_level() -> None:
    """With a thinned raster and an import limit the action fills the headroom with a listed level."""
    problem, objective = sc.build(sc.make_series("winter_clear"), "B", slots=96)
    plan = empty_plan(problem)
    table = battery_actions(problem, objective, SearchSettings(), plan, 4, levels_count=2)
    fill = table.values.shape[1] - 1
    levels = table.values[table.valid[:, fill] == 1, fill, 0]
    assert levels.size > 0
    allowed = set(problem.batteries[4].charge_rates)
    assert set(levels) <= allowed
    listed = set(table.values[:, 2:4, 0].ravel())  # the thinned grid levels
    assert not set(levels) & listed
    full = battery_actions(problem, objective, SearchSettings(), plan, 4)
    assert not full.valid[:, full.values.shape[1] - 1].any()  # all levels listed: nothing to add


def test_warm_start_and_event() -> None:
    """A shifted plan is valid; an event is answered by re-planning the affected vehicle first."""
    series = sc.make_series("winter_clear")
    problem, objective = sc.build(series, "A", slots=96)
    first = DeviceSearch(problem, objective).solve()
    shifted_problem, shifted_objective = sc.build(series, "A", offset=4, slots=96)
    start = shift_plan(shifted_problem, first.plan, 4)
    assert check_plan(shifted_problem, start) == []
    assert np.array_equal(start.battery_grid_charge[:, :92], first.plan.battery_grid_charge[:, 4:])
    warm = DeviceSearch(shifted_problem, shifted_objective).solve(start)
    assert warm.violations == []

    # The vehicle leaves two hours earlier than planned.
    early = sc.Vehicle(40000.0, 30.0, 11000.0, 0, 68, 80.0)
    event_problem, event_objective = sc.build(series, "A", slots=96, vehicles=(early,))
    event_search = DeviceSearch(event_problem, event_objective)
    response = event_search.solve(first.plan, affected=[DeviceRef("vehicle", 0)])
    scratch = event_search.solve()
    assert response.violations == []
    assert response.objective.total_eur <= response.start_objective.total_eur
    assert "event" in response.stats.seconds
    assert response.objective.total_eur <= scratch.objective.total_eur + 1.0


def test_battery_failure_keeps_plan_valid() -> None:
    """A battery that fails (capacity 0) is skipped by the search."""
    problem, _ = sc.build(sc.make_series("winter_clear"), "B", slots=48)
    broken = replace(problem.batteries[4], capacity_wh=0.0)
    batteries = problem.batteries[:4] + (broken,)
    inverters = problem.inverters[:4] + (replace(problem.inverters[4], max_power_w=0.0),)
    new = replace(problem, batteries=batteries, inverters=inverters)
    objective = Objective(new)
    result = DeviceSearch(new, objective).solve()
    assert result.violations == []
    assert result.objective.total_eur <= result.start_objective.total_eur
    assert isinstance(result.plan, Plan)


def test_limits_stop_the_search() -> None:
    """A zero time budget returns the start plan; one pass stops after one pass."""
    _, _, _, immediate = solve(
        "A", "winter_clear", slots=48, settings=SearchSettings(time_budget_s=0.0)
    )
    assert immediate.stats.stop == "time"
    assert immediate.objective.total_eur == immediate.start_objective.total_eur
    assert immediate.violations == []
    _, _, _, once = solve("A", "winter_clear", slots=48, settings=SearchSettings(max_passes=1))
    assert once.stats.passes == once.stats.starts  # one pass per start
    assert once.stats.stop in ("max_passes", "converged")
    assert once.objective.total_eur <= once.start_objective.total_eur


def test_periodic_full_charge_is_planned() -> None:
    """A battery with a full charge target before a deadline reaches it where it can."""
    problem, objective = sc.build(sc.make_series("winter_dark"), "A", slots=64)
    settings = replace(
        objective.settings,
        full_charge_targets=(FullChargeTarget(target_soc_pct=95.0, deadline_slot=30),),
    )
    objective = Objective(problem, settings)
    start = objective.evaluate(empty_plan(problem), simulate(problem, empty_plan(problem)))
    assert start.full_charge_penalty_eur > 0.0
    result = DeviceSearch(problem, objective).solve()
    assert result.objective.full_charge_penalty_eur == 0.0
    assert result.violations == []


def test_appliances_with_batteries_and_vehicles() -> None:
    """Appliance starts are chosen from their windows together with the storages."""
    problem, objective = sc.build(sc.make_series("winter_clear"), "A", slots=96)
    problem = replace(
        problem,
        appliances=(
            ApplianceParams(run_energy_wh=(1200.0, 900.0, 600.0, 300.0)),
            ApplianceParams(run_energy_wh=(800.0, 800.0), pinned_load_wh=(400.0, 400.0)),
        ),
        appliance_cycles=(2, 1),
    )
    objective = Objective(problem, objective.settings)
    space = SearchSpace(
        (
            ApplianceSchedule((np.arange(8, 40), np.arange(50, 90)), min_gap_slots=4),
            ApplianceSchedule((np.arange(0, 96, 2),), min_gap_slots=0),
        )
    )
    result = DeviceSearch(problem, objective, space=space).solve()
    assert result.violations == []
    starts = result.plan.appliance_starts
    assert 8 <= starts[0, 0] < 40
    assert 50 <= starts[0, 1] < 90
    assert starts[1, 0] % 2 == 0
    assert result.objective.total_eur <= result.start_objective.total_eur
    assert result.stats.accepted.get("appliance", 0) + result.stats.rejected.get("appliance", 0) > 0


def test_full_charge_counts_the_highest_state_before_the_end() -> None:
    """With a deadline beyond the horizon the battery must have been full once, not at the end."""
    problem, objective = sc.build(sc.make_series("winter_dark"), "A", slots=64)
    settings = replace(
        objective.settings,
        full_charge_targets=(FullChargeTarget(target_soc_pct=95.0, deadline_slot=10_000),),
    )
    objective = Objective(problem, settings)
    result = DeviceSearch(problem, objective).solve()
    soc = simulate(problem, result.plan).soc_pct[0]
    assert result.objective.full_charge_penalty_eur == 0.0
    assert soc.max() >= 95.0 - 1e-6
    assert soc[-1] < 95.0 - 1.0  # it does not have to stay full to the end
    assert result.violations == []


def test_vehicle_rule_plan_charges_in_the_cheapest_slots() -> None:
    """The rule start charges a vehicle at its top level in the cheapest slots until the target."""
    problem, objective = sc.build(sc.make_series("price_spike"), "A", slots=96)
    rule = vehicle_rule_plan(problem, objective)
    assert rule is not None
    charge = rule.vehicle_charge[0]
    chosen = np.nonzero(charge > 0.0)[0]
    assert chosen.size > 0
    price = np.asarray(problem.import_price_per_wh)
    available = np.asarray(problem.kernel_arrays()["storage_available"])[len(problem.batteries)]
    others = np.nonzero((charge == 0.0) & (available > 0.0))[0]
    assert price[chosen].max() <= price[others].min() + 1e-12 or others.size == 0
    assert check_plan(problem, rule) == []


def test_the_start_variants_never_beat_the_guard() -> None:
    """The search with all starts is at least as good as the one from the empty plan only."""
    _, _, _, full = solve("A", "winter_clear", slots=96)
    _, _, _, plain = solve(
        "A",
        "winter_clear",
        slots=96,
        settings=SearchSettings(rule_starts=False, vehicle_frozen_view=False),
    )
    assert full.objective.total_eur <= plain.objective.total_eur + 1e-9


def test_the_start_plan_reaches_a_reachable_full_charge_target() -> None:
    """A hard full charge target is met by the start plan itself, not left to the search."""
    from akkudoktoreos.optimization.search.warmstart import secure_full_charge

    problem, objective = sc.build(sc.make_series("winter_dark"), "A", slots=96)
    settings = replace(
        objective.settings,
        full_charge_targets=(FullChargeTarget(target_soc_pct=100.0, deadline_slot=10_000),),
    )
    objective = Objective(problem, settings)
    empty = empty_plan(problem)
    assert simulate(problem, empty).soc_pct[0].max() < 100.0 - 1e-6
    secured = secure_full_charge(problem, objective, empty)
    assert simulate(problem, secured).soc_pct[0].max() >= 100.0 - 1e-6
    assert check_plan(problem, secured) == []
    # Without a target, nothing changes.
    plain = Objective(problem, replace(settings, full_charge_targets=(None,)))
    assert secure_full_charge(problem, plain, empty) is empty


def test_the_guard_prefilter_remembers_a_full_charge_reached_before_the_changed_slot() -> None:
    """Dropping a later grid charge is no loss when the full charge was reached earlier."""
    problem, objective = sc.build(sc.make_series("winter_clear"), "A", slots=96)
    settings = replace(
        objective.settings,
        full_charge_targets=(FullChargeTarget(target_soc_pct=100.0, deadline_slot=10_000),),
    )
    objective = Objective(problem, settings)
    candidate = empty_plan(problem)
    candidate.battery_grid_charge[0, :12] = 1.0  # full at slot 5
    # The current plan also charges from the grid long after the battery was full.
    current = candidate.copy()
    current.battery_grid_charge[0, 20] = 0.5
    result = simulate(problem, current)
    assert objective.evaluate(current, result).full_charge_penalty_eur == 0.0
    assert objective.evaluate(candidate, simulate(problem, candidate)).total_eur < (
        objective.evaluate(current, result).total_eur - 0.01
    )
    search = DeviceSearch(problem, objective)
    search._adopt(current)
    assert search._consider(candidate, "test")
    assert search._stats.pruned == {}
    assert search._breakdown.full_charge_penalty_eur == 0.0


def test_a_warm_start_is_checked_against_the_cold_search() -> None:
    """The plan from a given start never ends worse than the cold search beyond the margin."""
    problem, objective = sc.build(sc.make_series("winter_clear"), "A", slots=48)
    quick = SearchSettings(max_passes=0, refine_factor=1, random_starts=0)
    bad = empty_plan(problem)
    bad.vehicle_charge[0, :] = 1.0  # charges whenever it can, at any price
    only_warm = DeviceSearch(
        problem, objective, settings=replace(quick, warm_start_compare=False)
    ).solve(bad)
    cold = DeviceSearch(problem, objective, settings=quick).solve()
    compared = DeviceSearch(problem, objective, settings=quick).solve(bad)
    assert only_warm.stats.winner == "warm" and only_warm.stats.starts == 1
    assert cold.objective.total_eur < only_warm.objective.total_eur - 0.01  # the premise
    assert compared.stats.winner == "cold" and compared.stats.starts > 1
    assert compared.objective.total_eur == pytest.approx(cold.objective.total_eur)
    assert compared.violations == []
    # A margin larger than the gap keeps the warm start (the calm plan).
    kept = DeviceSearch(
        problem, objective, settings=replace(quick, warm_start_keep_margin_eur=1e6)
    ).solve(bad)
    assert kept.stats.winner == "warm"
    assert np.array_equal(kept.plan.vehicle_charge, only_warm.plan.vehicle_charge)
    with pytest.raises(ValueError, match="must not be negative"):
        SearchSettings(warm_start_keep_margin_eur=-0.1)


def test_a_good_warm_start_is_kept_and_its_full_charge_target_is_secured() -> None:
    problem, objective = sc.build(sc.make_series("winter_clear"), "A", slots=48)
    full = DeviceSearch(problem, objective).solve()
    again = DeviceSearch(problem, objective).solve(full.plan)
    assert again.stats.winner == "warm"  # the best plan stands unless a cold plan beats it
    assert np.array_equal(again.plan.battery_grid_charge, full.plan.battery_grid_charge)
    assert again.objective.total_eur <= full.objective.total_eur + 1e-9
    # A warm start that misses a reachable full charge target is repaired before the search.
    problem, objective = sc.build(sc.make_series("winter_dark"), "A", slots=96)
    target = replace(
        objective.settings,
        full_charge_targets=(FullChargeTarget(target_soc_pct=100.0, deadline_slot=10_000),),
    )
    objective = Objective(problem, target)
    result = DeviceSearch(
        problem,
        objective,
        settings=SearchSettings(max_passes=0, warm_start_compare=False, refine_factor=1),
    ).solve(empty_plan(problem))
    assert simulate(problem, result.plan).soc_pct[0].max() >= 100.0 - 1e-6


@pytest.mark.parametrize("reverse_inverters", [False, True])
def test_discharge_normalization_preserves_each_batterys_delivery(reverse_inverters: bool) -> None:
    """Lift equivalent ceilings independently, keeping deliberate limits even in the last slot."""
    problem, _ = sc.build(sc.make_series("winter_clear"), "A", slots=3)
    battery = StorageParams(
        capacity_wh=5000.0,
        initial_soc_pct=60.0,
        max_discharge_power_w=1000.0,
        discharging_efficiency=0.8,
    )
    inverters = tuple(
        InverterParams(
            max_power_w=1000.0,
            battery_index=index,
            dc_to_ac_efficiency_curve=((0.0, 0.6), (0.5, 0.9), (1.0, 0.9)),
        )
        for index in ((1, 0) if reverse_inverters else (0, 1))
    )
    problem = replace(
        problem,
        batteries=(battery, battery),
        inverters=inverters,
        vehicles=(),
        pv_wh=np.zeros(3),
        load_wh=np.array([5.0, 25.0, 300.0]),
        first_slot_fraction=0.5,
    )
    plan = Plan.idle(problem)
    plan.battery_discharge_limit[:] = [[0.3, 0.0, 0.2], [0.2, 0.3, 0.3]]
    normalized = normalize_discharge(problem, plan, SearchSettings())
    np.testing.assert_array_equal(
        normalized.battery_discharge_limit, [[1.0, 0.0, 0.2], [1.0, 1.0, 0.3]]
    )
    before, after = simulate(problem, plan), simulate(problem, normalized)
    np.testing.assert_array_equal(before.grid_import_wh, after.grid_import_wh)
    np.testing.assert_array_equal(before.battery_discharge_ac_wh, after.battery_discharge_ac_wh)
    np.testing.assert_array_equal(before.soc_wh, after.soc_wh)
    np.testing.assert_array_equal(before.final_soc_wh, after.final_soc_wh)


def test_a_found_plan_holds_back_only_where_it_limits() -> None:
    """A search result already uses the largest equivalent levels."""
    problem, _, _, result = solve("A", "winter_clear", slots=96)
    assert normalize_discharge(problem, result.plan, SearchSettings()) is result.plan


def test_a_plan_taken_over_from_an_earlier_forecast_is_normalized() -> None:
    """Warm start: ceilings that no longer limit under the new forecast become "no limit"."""
    problem, _ = sc.build(sc.make_series("winter_clear"), "A", slots=96)
    previous = Plan.idle(problem)
    previous.battery_discharge_limit[0] = 0.3
    lower = replace(problem, load_wh=np.asarray(problem.load_wh) * 0.1)
    taken = shift_plan(lower, previous, 0)
    active = taken.battery_discharge_limit[0] > 0.0
    assert active.any()
    assert np.all(taken.battery_discharge_limit[0][active] == 1.0)
    # Under the old forecast the same ceilings limit in some slots and stay.
    kept = shift_plan(problem, previous, 0)
    assert np.any(kept.battery_discharge_limit[0] == 0.3)
