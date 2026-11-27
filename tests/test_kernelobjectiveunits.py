"""Properties of the objective that do not need the former optimizer.

Several batteries are valued as one combined battery, the import limit is priced by its
excess energy, and batches and partial runs give the same objective as the single run.
"""

from dataclasses import replace
from typing import Any

import numpy as np
import pytest
from test_kernelinvariants import (
    APPLIANCES,
    SLOT_H,
    SLOTS,
    battery,
    forecasts,
    make_problem,
    random_plan,
    vehicle,
)

from akkudoktoreos.devices.models.inverter import InverterParams, reference_dc_to_ac_efficiency
from akkudoktoreos.optimization.kernel import GridConnection, Plan, PlanningProblem, simulate
from akkudoktoreos.optimization.objective import (
    DEFAULT_IMPORT_LIMIT_PENALTY_EUR_PER_KWH,
    FullChargeTarget,
    Objective,
    ObjectiveSettings,
    TerminalValueSettings,
    VehicleTarget,
)
from akkudoktoreos.optimization.terminalvalue import combine_batteries

PARTS = (
    "balance_eur",
    "terminal_value_eur",
    "vehicle_target_penalty_eur",
    "full_charge_penalty_eur",
    "ac_break_even_penalty_eur",
    "import_limit_penalty_eur",
    "total_eur",
)


def with_tail(problem: PlanningProblem, slots: int = 24) -> PlanningProblem:
    """Return the problem with a lookahead tail of ``slots`` slots."""
    tail = forecasts(seed=9, slots=slots)
    return replace(
        problem,
        tail_pv_wh=tail["pv_wh"],
        tail_load_wh=tail["load_wh"],
        tail_import_price_per_wh=tail["price"],
        tail_feed_in_tariff_per_wh=tail["tariff"],
    )


def halves(direct_marketing: bool = True) -> tuple[PlanningProblem, PlanningProblem]:
    """A problem with one battery and the same one split into two identical halves."""
    whole = make_problem(
        (battery(10000.0, 60.0, max_charge_power_w=4000.0, max_discharge_power_w=4000.0),),
        direct_marketing=direct_marketing,
    )
    half = battery(5000.0, 60.0, max_charge_power_w=2000.0, max_discharge_power_w=2000.0)
    twin = make_problem(
        (half, half),
        inverters=tuple(
            InverterParams(2500.0, index, 0.96, 0.95, max_ac_charge_power_w=1750.0)
            for index in range(2)
        ),
        direct_marketing=direct_marketing,
    )
    object.__setattr__(
        whole,
        "inverters",
        (InverterParams(5000.0, 0, 0.96, 0.95, max_ac_charge_power_w=3500.0),),
    )
    return whole, twin


@pytest.mark.parametrize("lookahead", [False, True])
@pytest.mark.parametrize("direct_marketing", [False, True])
def test_two_half_batteries_have_the_value_of_one_whole(
    lookahead: bool, direct_marketing: bool
) -> None:
    whole, twin = halves(direct_marketing)
    if lookahead:
        whole, twin = with_tail(whole), with_tail(twin)
    settings = ObjectiveSettings(
        terminal_value=TerminalValueSettings(
            mode="AUTO", window_slots=48, lookahead=lookahead, tail_export_rates=(0.5, 1.0)
        )
    )
    one = Objective(whole, settings)
    two = Objective(twin, settings)
    assert not one.curve.empty
    np.testing.assert_allclose(two.curve.energy_wh, one.curve.energy_wh, rtol=1e-12, atol=1e-9)
    np.testing.assert_allclose(two.curve.value_eur, one.curve.value_eur, rtol=1e-12, atol=1e-9)
    # Without flows the final energy is the initial energy: the credits agree.
    for problem in (whole, twin):
        object.__setattr__(problem, "pv_wh", np.zeros(SLOTS))
        object.__setattr__(problem, "load_wh", np.zeros(SLOTS))
        object.__setattr__(problem, "_kernel_cache", None)
    credit = []
    for problem, objective in ((whole, one), (twin, two)):
        plan = Plan.idle(problem)
        credit.append(objective.evaluate(plan, simulate(problem, plan)).terminal_value_eur)
    assert credit[0] == pytest.approx(credit[1], abs=1e-9)
    assert credit[0] != 0.0


@pytest.mark.parametrize(("full", "efficiency"), [(0, 0.6), (1, 0.9)])
def test_the_end_value_of_each_battery_uses_the_efficiency_of_its_own_inverter(
    full: int, efficiency: float
) -> None:
    """1 kWh usable behind a 60 % or a 90 % inverter is worth 0.6 or 0.9 kWh at the fixed price."""
    slots = 4
    cells = [
        battery(
            1000.0,
            100.0 if index == full else 0.0,
            min_soc_pct=0.0,
            max_soc_pct=100.0,
            discharging_efficiency=1.0,
        )
        for index in range(2)
    ]
    inverters = tuple(
        InverterParams(max_power_w=5000.0, battery_index=index, dc_to_ac_efficiency=value)
        for index, value in enumerate((0.6, 0.9))
    )
    problem = PlanningProblem(
        slot_duration_h=SLOT_H,
        pv_wh=np.zeros(slots),
        load_wh=np.zeros(slots),
        import_price_per_wh=np.full(slots, 3e-4),
        feed_in_tariff_per_wh=np.full(slots, 8e-5),
        batteries=tuple(cells),
        inverters=inverters,
    )
    settings = ObjectiveSettings(terminal_value=TerminalValueSettings(fixed_price_per_wh=3e-4))
    objective = Objective(problem, settings)
    final = np.array([c.initial_soc_pct / 100.0 * c.capacity_wh for c in cells])
    expected = 1000.0 * efficiency * 3e-4
    assert objective.terminal_energy_wh(final) == pytest.approx(1000.0 * efficiency)
    assert objective.terminal_credit(final) == pytest.approx(expected)
    assert objective.terminal_credit_many(final[None, :])[0] == pytest.approx(expected)
    plan = Plan.idle(problem)
    assert objective.evaluate(plan, simulate(problem, plan)).terminal_value_eur == pytest.approx(
        expected
    )
    assert objective.evaluate_batch([plan]).terminal_value_eur[0] == pytest.approx(expected)


@pytest.mark.parametrize("curve", [None, ((0.0, 0.7), (0.5, 0.93), (1.0, 0.95))])
def test_one_battery_keeps_the_end_energy_of_the_combined_battery_to_the_last_bit(
    curve: Any,
) -> None:
    """With one battery the efficiency of its inverter is the one of the combined battery."""
    problem = make_problem(
        (battery(10000.0, 60.0),),
        inverters=(
            InverterParams(
                max_power_w=5000.0,
                battery_index=0,
                dc_to_ac_efficiency=0.96,
                dc_to_ac_efficiency_curve=curve,
            ),
        ),
    )
    price = 3e-4
    settings = ObjectiveSettings(terminal_value=TerminalValueSettings(fixed_price_per_wh=price))
    objective = Objective(problem, settings)
    combined, unit = combine_batteries(problem.batteries, problem.inverters)
    reference = reference_dc_to_ac_efficiency(unit, SLOT_H)
    for soc_wh in (1000.0, 4321.5, 9500.0):
        old = float((soc_wh - 1000.0) * combined.discharging_efficiency * reference)
        final = np.array([soc_wh])
        assert objective.terminal_energy_wh(final) == old
        assert objective.terminal_credit_many(final[None, :])[0] == old * price


def test_import_limit_penalty_is_the_excess_energy_times_the_price() -> None:
    free = make_problem((battery(), battery()), (vehicle(),), appliances=APPLIANCES)
    limited = make_problem(
        (battery(), battery()),
        (vehicle(),),
        appliances=APPLIANCES,
        grid=GridConnection(import_limit_w=2000.0),
    )
    plan = random_plan(free, 4)
    plan.vehicle_charge = plan.vehicle_charge[:1]
    for price in (DEFAULT_IMPORT_LIMIT_PENALTY_EUR_PER_KWH, 0.75):
        settings = ObjectiveSettings(import_limit_eur_per_kwh=price, ac_break_even_factor=0.0)
        result = simulate(limited, plan)
        parts = Objective(limited, settings).evaluate(plan, result)
        excess = np.maximum(result.grid_import_wh - 2000.0 * SLOT_H, 0.0)
        assert excess.sum() > 0.0
        assert parts.import_limit_penalty_eur == pytest.approx(excess.sum() / 1000.0 * price)
        assert parts.total_eur == pytest.approx(
            parts.balance_eur - parts.terminal_value_eur + parts.import_limit_penalty_eur
        )
    unlimited = Objective(free, ObjectiveSettings()).evaluate(plan, simulate(free, plan))
    assert unlimited.import_limit_penalty_eur == 0.0


def test_default_import_limit_price_exceeds_any_price_arbitrage() -> None:
    assert DEFAULT_IMPORT_LIMIT_PENALTY_EUR_PER_KWH >= 9.99


def rich_problem() -> tuple[PlanningProblem, ObjectiveSettings]:
    """Two batteries, two vehicles, appliances, grid limit, tail; every penalty can occur."""
    problem = with_tail(
        make_problem(
            (battery(8000.0, 40.0), battery(12000.0, 55.0, max_discharge_power_w=3000.0)),
            (vehicle(20.0), vehicle(30.0)),
            appliances=APPLIANCES,
            grid=GridConnection(import_limit_w=3000.0, export_limit_w=2500.0),
            first_slot_fraction=0.7,
        )
    )
    settings = ObjectiveSettings(
        terminal_value=TerminalValueSettings(
            mode="AUTO", window_slots=48, lookahead=True, tail_export_rates=(0.5, 1.0)
        ),
        vehicle_targets=(VehicleTarget(80.0, 60), VehicleTarget(70.0, None)),
        full_charge_targets=(FullChargeTarget(95.0, 70), None),
    )
    return problem, settings


def rich_objective(settings: ObjectiveSettings, problem: PlanningProblem) -> Objective:
    return Objective(problem, settings)


@pytest.mark.parametrize("break_even_problem", [False, True])
def test_batch_matches_single_runs(break_even_problem: bool) -> None:
    problem, settings = rich_problem()
    if break_even_problem:
        # A fixed value keeps the break-even penalty active (it is off with the lookahead).
        settings = replace(settings, terminal_value=TerminalValueSettings(fixed_price_per_wh=1e-4))
    objective = Objective(problem, settings)
    plans = [random_plan(problem, seed) for seed in range(12)]
    batch = objective.evaluate_batch(plans)
    for index, plan in enumerate(plans):
        single = objective.evaluate(plan, simulate(problem, plan))
        for name in PARTS:
            assert getattr(batch, name)[index] == pytest.approx(
                getattr(single, name), abs=1e-12
            ), name
    totals = batch.total_eur
    assert np.all(np.isfinite(totals)) and np.ptp(totals) > 0.0
    assert batch.vehicle_target_penalty_eur.max() > 0.0
    assert batch.import_limit_penalty_eur.max() > 0.0
    assert len(objective.evaluate_batch([]).total_eur) == 0


@pytest.mark.parametrize("start_slot", [1, 20, 50])
def test_partial_run_has_the_objective_of_the_remaining_slots(start_slot: int) -> None:
    problem, settings = rich_problem()
    settings = replace(settings, terminal_value=TerminalValueSettings(fixed_price_per_wh=1e-4))
    objective = Objective(problem, settings)
    plan = random_plan(problem, 3)
    full_result = simulate(problem, plan)
    full = objective.evaluate(plan, full_result)
    partial = objective.evaluate(
        plan, simulate(problem, plan, start_slot, full_result.soc_wh[:, start_slot])
    )
    # The deadlines lie after the start slot: these parts are the same as for the full run.
    assert partial.terminal_value_eur == pytest.approx(full.terminal_value_eur, abs=1e-12)
    assert partial.vehicle_target_penalty_eur == pytest.approx(
        full.vehicle_target_penalty_eur, abs=1e-12
    )
    assert partial.full_charge_penalty_eur == pytest.approx(full.full_charge_penalty_eur, abs=1e-12)
    # Balance, import limit and break-even cover only the remaining slots.
    prefix_cost = float(full_result.cost_eur[:start_slot].sum())
    prefix_revenue = float(full_result.revenue_eur[:start_slot].sum())
    assert partial.balance_eur == pytest.approx(
        full.balance_eur - (prefix_cost - prefix_revenue), abs=1e-12
    )
    prefix_excess = float(full_result.import_excess_wh[:start_slot].sum())
    price = settings.import_limit_eur_per_kwh
    assert partial.import_limit_penalty_eur == pytest.approx(
        full.import_limit_penalty_eur - prefix_excess / 1000.0 * price, abs=1e-12
    )
    later_free = replace_plan(plan, start_slot)
    prefix_only = objective.evaluate(later_free, simulate(problem, later_free))
    assert partial.ac_break_even_penalty_eur == pytest.approx(
        full.ac_break_even_penalty_eur - prefix_only.ac_break_even_penalty_eur, abs=1e-12
    )
    assert full.ac_break_even_penalty_eur > 0.0


def replace_plan(plan: Plan, start_slot: int) -> Plan:
    """Return the plan without grid charging from ``start_slot`` on."""
    grid = plan.battery_grid_charge.copy()
    grid[:, start_slot:] = 0.0
    other: Any = Plan(
        battery_grid_charge=grid,
        battery_discharge_limit=plan.battery_discharge_limit,
        battery_pv_charge=plan.battery_pv_charge,
        battery_export=plan.battery_export,
        vehicle_charge=plan.vehicle_charge,
        appliance_starts=plan.appliance_starts,
    )
    return other


def test_parts_add_up_and_penalties_are_not_negative() -> None:
    problem, settings = rich_problem()
    objective = Objective(problem, settings)
    for seed in range(6):
        plan = random_plan(problem, seed)
        parts = objective.evaluate(plan, simulate(problem, plan))
        assert parts.total_eur == pytest.approx(
            parts.balance_eur
            - parts.terminal_value_eur
            + parts.vehicle_target_penalty_eur
            + parts.full_charge_penalty_eur
            + parts.ac_break_even_penalty_eur
            + parts.import_limit_penalty_eur
        )
        assert (
            min(
                parts.vehicle_target_penalty_eur,
                parts.full_charge_penalty_eur,
                parts.ac_break_even_penalty_eur,
                parts.import_limit_penalty_eur,
            )
            >= 0.0
        )
