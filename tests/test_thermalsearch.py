"""Search with a thermal device: exactness, guard, repeatability, warm start, plausibility."""

import itertools
from dataclasses import replace
from typing import Any, Optional

import numpy as np
import pytest
import test_searchscenarios as sc

from akkudoktoreos.devices.models import thermal as thm
from akkudoktoreos.devices.models.inverter import InverterParams
from akkudoktoreos.optimization.kernel import Plan, PlanningProblem, simulate
from akkudoktoreos.optimization.objective import Objective, ObjectiveSettings
from akkudoktoreos.optimization.search import (
    DeviceRef,
    DeviceSearch,
    SearchSettings,
    check_plan,
    shift_plan,
)
from akkudoktoreos.thermal.planninginput import build_thermal_device
from akkudoktoreos.thermal.thermaldistribution import DirectDistributionSettings
from akkudoktoreos.thermal.thermalmodels import ThermalComfortBand


def band(low: float, high: float) -> ThermalComfortBand:
    """Return a comfort band whose target lies one Kelvin above its minimum."""
    return ThermalComfortBand(
        min_temperature_c=low, target_temperature_c=min(low + 1.0, high), max_temperature_c=high
    )


def small_house(
    slots: int,
    outdoor: Any,
    bands: list[ThermalComfortBand],
    price: Any,
    *,
    kind: str = "direct",
    temperature: Optional[list[float]] = None,
    blocked: Optional[np.ndarray] = None,
    running: bool = False,
    pv: Any = None,
    max_electric_power_w: Optional[float] = None,
    under_weight: Any = None,
) -> tuple[PlanningProblem, Objective]:
    """Return a house with only the heat pump (a base load, no storage)."""
    direct = kind == "direct"
    start = temperature or ([21.0, 21.0] if direct else [21.0, 21.0, 21.0])
    unit = build_thermal_device(
        sc.HEAT_BUILDING,
        DirectDistributionSettings() if direct else sc.HEAT_AIR,
        sc.HEAT_PROFILE,
        outdoor_c=np.broadcast_to(outdoor, (slots,)).copy(),
        comfort_bands=bands,
        initial_temperature_c=start,
        delivery_temperature_c=20.0,
        electrical=sc.HEAT_ELECTRICAL,
        initial_running=running,
        blocked=blocked,
        reference_room_c=bands[-1].target_temperature_c,
        max_electric_power_w=max_electric_power_w,
        under_weight=under_weight,
    )
    problem = PlanningProblem(
        slot_duration_h=0.25,
        pv_wh=np.zeros(slots) if pv is None else np.asarray(pv, dtype=float),
        load_wh=np.full(slots, 60.0),
        import_price_per_wh=np.broadcast_to(np.asarray(price, dtype=float), (slots,)).copy(),
        feed_in_tariff_per_wh=np.zeros(slots),
        inverters=(InverterParams(max_power_w=8000.0, battery_index=None),),
        thermal=(unit,),
    )
    return problem, Objective(problem, ObjectiveSettings())


def solve_heat(problem: PlanningProblem, objective: Objective, **kwargs: Any) -> Any:
    """Search the heat plan of a house with only a heat pump."""
    settings = kwargs.pop("settings", SearchSettings(thermal_order="first", max_passes=4))
    return DeviceSearch(problem, objective, settings=settings).solve(**kwargs)


def heat_energy_wh(problem: PlanningProblem, plan: Plan, window: slice) -> float:
    """Return the heat pump's electric energy in a window of slots [Wh]."""
    result = simulate(problem, plan)
    assert result.thermal is not None
    return float(result.thermal.electric_wh[0, window].sum())


# --------------------------------------------------------------------------- exactness


@pytest.mark.parametrize("kind", ["direct", "air"])
def test_the_heat_plan_is_as_good_as_every_plan_of_a_grid_of_run_shares(kind: str) -> None:
    """The heat pump alone on 3 slots: no plan of a grid of run shares is cheaper than the result.

    The shares run from 0 to 1 in steps of 0.1 (11^3 plans); the linear program is exact for
    the heat pump alone up to the hull of the operating table, so the result is at most the
    best plan of the grid plus the small gap of the grid itself, and the guard never lets it
    end above the start.
    """
    slots = 3
    margins = []
    for seed in range(3):
        rng = np.random.RandomState(seed)
        low = np.full(slots, 20.0)
        low[rng.randint(2, slots) :] = rng.choice([19.0, 20.5, 21.5])
        bands = [band(float(a), float(a) + 2.5) for a in low]
        direct = kind == "direct"
        start = float(rng.uniform(20.3, 22.5))
        temperature = [start, start + float(rng.uniform(-0.5, 0.5))] + ([] if direct else [start])
        problem, objective = small_house(
            slots,
            rng.uniform(-8.0, 2.0, slots),
            bands,
            rng.uniform(1e-4, 8e-4, slots),
            kind=kind,
            temperature=temperature,
            running=bool(rng.randint(2)),
        )
        unit = problem.thermal[0]
        best = np.inf
        plan = Plan.idle(problem)
        for combo in itertools.product(np.linspace(0.0, 1.0, 11), repeat=slots):
            plan.thermal_heat_w[0] = np.array(combo) * unit.heat_limit_w
            best = min(best, objective.evaluate(plan, simulate(problem, plan)).total_eur)
        found = solve_heat(problem, objective)
        assert found.violations == []
        assert found.objective.total_eur <= found.start_objective.total_eur
        margins.append(found.objective.total_eur - best)
    assert max(margins) <= 1e-4


# --------------------------------------------------------------------------- guard and repeat

PLANTS = [("C", "winter_clear"), ("C", "price_spike"), ("D", "shoulder_mixed")]


@pytest.mark.parametrize(("plant", "scenario"), PLANTS)
def test_search_is_valid_monotone_and_repeatable(plant: str, scenario: str) -> None:
    """No violations, a falling trace, equal to an independent simulation, bit-identical runs."""
    problem, objective = sc.build(sc.make_series(scenario), plant, slots=96)
    settings = SearchSettings(max_passes=3)
    first = DeviceSearch(problem, objective, settings=settings).solve()
    assert first.violations == []
    assert check_plan(problem, first.plan) == []
    assert all(b < a for a, b in zip(first.trace, first.trace[1:]))
    assert first.objective.total_eur <= first.start_objective.total_eur
    check = objective.evaluate(first.plan, simulate(problem, first.plan))
    assert check.total_eur == pytest.approx(first.objective.total_eur, abs=1e-12)
    second = DeviceSearch(problem, objective, settings=settings).solve()
    for name in (
        "battery_grid_charge",
        "battery_discharge_limit",
        "battery_pv_charge",
        "vehicle_charge",
        "thermal_heat_w",
    ):
        assert np.array_equal(getattr(first.plan, name), getattr(second.plan, name))


def test_warm_start_and_event_replanning_with_a_heat_pump() -> None:
    """A warm start never ends above its start; an event re-plans the heat pump first."""
    series = sc.make_series("winter_clear")
    problem, objective = sc.build(series, "C", slots=96)
    settings = SearchSettings(max_passes=3)
    base = DeviceSearch(problem, objective, settings=settings).solve()
    # Warm start: the same plan moved forward by 4 slots on the later window.
    later, later_objective = sc.build(series, "C", offset=4, slots=96)
    result = simulate(problem, base.plan)
    assert result.thermal is not None
    unit = later.thermal[0]
    from dataclasses import replace

    moved = replace(
        unit,
        initial_temperature_c=result.thermal.temperature_c[0][4].copy(),
        initial_running=bool(result.thermal.heat_w[0, 3] > thm.RUN_TOLERANCE_W),
    )
    later = replace(later, thermal=(moved,))
    later_objective = Objective(later, later_objective.settings)
    start = shift_plan(later, base.plan, 4, None, settings)
    warm = DeviceSearch(later, later_objective, settings=settings).solve(start)
    assert warm.violations == []
    assert warm.objective.total_eur <= warm.start_objective.total_eur
    # Event: the heat pump is blocked for four hours; the affected device is re-planned first.
    blocked = np.zeros(96, dtype=bool)
    blocked[30:46] = True
    hit = replace(problem.thermal[0], blocked=blocked)
    event_problem = replace(problem, thermal=(hit,))
    event_objective = Objective(event_problem, objective.settings)
    event = DeviceSearch(event_problem, event_objective, settings=settings).solve(
        base.plan, affected=(DeviceRef("thermal", 0),)
    )
    assert event.violations == []
    assert np.all(event.plan.thermal_heat_w[0, 30:46] == 0.0)
    assert event.stats.seconds.get("event", 0.0) > 0.0


# --------------------------------------------------------------------------- plausibility


def test_preheats_in_cheap_hours_at_equal_comfort() -> None:
    """With the same band the plan heats more in cheap hours than with a flat price.

    The heat is stored in the building and used to skip the dear hours.
    """
    slots = 64
    bands = [band(20.0, 24.0)] * slots
    price = np.where(np.arange(slots) < 28, 1.5e-4, 7e-4)
    problem, objective = small_house(slots, -3.0, bands, price, temperature=[20.2, 20.2])
    cheap = solve_heat(problem, objective).plan
    flat_problem, flat_objective = small_house(slots, -3.0, bands, 4e-4, temperature=[20.2, 20.2])
    flat = solve_heat(flat_problem, flat_objective).plan
    window = slice(0, 28)
    assert heat_energy_wh(problem, cheap, window) > 1.25 * heat_energy_wh(problem, flat, window)
    result = simulate(problem, cheap)
    assert result.thermal is not None
    assert result.thermal.temperature_c[0][28, 0] >= 22.0  # stored heat when the price rises
    # The solver holds the band up to its tolerance (about 1e-9 K).
    assert result.thermal.under_kh[0] == pytest.approx(0.0, abs=1e-6)
    assert result.thermal.over_kh[0] == pytest.approx(0.0, abs=1e-6)
    # The dear hours are bridged with less heat: a third less than in the flat price case.
    dear = slice(28, slots)
    assert heat_energy_wh(problem, cheap, dear) < 0.8 * heat_energy_wh(problem, flat, dear)


def test_long_absence_pauses_the_heating_and_reheats_in_time() -> None:
    """In a long absence the heating pauses; it resumes early enough for the return."""
    slots = 96
    away = (16, 64)
    bands = [band(20.5, 23.0)] * slots
    for slot in range(*away):
        bands[slot] = band(15.0, 23.0)
    problem, objective = small_house(slots, -6.0, bands, 4e-4, temperature=[21.0, 21.0])
    result = solve_heat(problem, objective)
    assert result.objective.comfort_penalty_eur == pytest.approx(0.0, abs=1e-6)
    sim = simulate(problem, result.plan)
    assert sim.thermal is not None
    heat = sim.thermal.heat_w[0]
    # The heating pauses while the building cools down to the absence minimum ...
    assert np.all(heat[16:36] == 0.0)
    assert sim.thermal.temperature_c[0][36, 0] < 16.0
    # ... it resumes before the return and the room is back in its band in time.
    assert np.any(heat[52:64] > 0.0)
    assert sim.thermal.temperature_c[0][64, 0] >= 20.5 - 1e-6
    # Compared with holding the comfort band all the time, energy is saved.
    full_bands = [band(20.5, 23.0)] * slots
    full_problem, full_objective = small_house(
        slots, -6.0, full_bands, 4e-4, temperature=[21.0, 21.0]
    )
    full = solve_heat(full_problem, full_objective).plan
    assert heat_energy_wh(problem, result.plan, slice(0, slots)) < 0.9 * heat_energy_wh(
        full_problem, full, slice(0, slots)
    )


def test_a_blocked_period_is_kept_and_prepared_for() -> None:
    """A four hour block gets no heat; before it the plan heats as much as the band allows."""
    slots = 80
    blocked = np.zeros(slots, dtype=bool)
    blocked[40:56] = True
    bands = [band(19.5, 24.0)] * slots
    problem, objective = small_house(
        slots, 8.0, bands, 4e-4, temperature=[21.0, 21.0], blocked=blocked
    )
    plain, plain_objective = small_house(slots, 8.0, bands, 4e-4, temperature=[21.0, 21.0])
    with_block = solve_heat(problem, objective)
    without = solve_heat(plain, plain_objective)
    assert np.all(with_block.plan.thermal_heat_w[0, 40:56] == 0.0)
    assert with_block.violations == []
    assert with_block.objective.comfort_penalty_eur == pytest.approx(0.0, abs=1e-6)
    sim = simulate(problem, with_block.plan)
    assert sim.thermal is not None
    reference = simulate(plain, without.plan)
    assert reference.thermal is not None
    # Preheating: warmer at the start of the block than without the block, close to the upper
    # bound (24) as far as the band lets it; and more heat in the hours before.
    assert sim.thermal.temperature_c[0][40, 0] > reference.thermal.temperature_c[0][40, 0] + 1.0
    assert sim.thermal.temperature_c[0][40, 0] >= 23.0
    assert heat_energy_wh(problem, with_block.plan, slice(24, 40)) > heat_energy_wh(
        plain, without.plan, slice(24, 40)
    )


def test_flat_price_holds_the_band_with_little_energy() -> None:
    """Without price differences the plan holds the band with less energy than a thermostat.

    The thermostat keeps the room on the target, one Kelvin above the minimum.
    """
    slots = 96
    bands = [band(20.0, 23.0)] * slots
    problem, objective = small_house(slots, -2.0, bands, 4e-4, temperature=[21.0, 21.0])
    result = solve_heat(problem, objective)
    assert result.objective.comfort_penalty_eur == pytest.approx(0.0, abs=1e-6)
    sim = simulate(problem, result.plan)
    assert sim.thermal is not None
    assert float(sim.thermal.temperature_c[0][1:, 0].min()) >= 20.0 - 1e-9
    thermostat = Plan.idle(problem)
    thermostat.thermal_heat_w[0] = sc.thermostat_heat(problem.thermal[0], 21.0)
    thermostat_sim = simulate(problem, thermostat)
    assert thermostat_sim.thermal is not None
    assert sim.thermal.electric_wh[0].sum() < thermostat_sim.thermal.electric_wh[0].sum()
    # And the plan costs less than the thermostat at every price.
    assert result.objective.total_eur < objective.evaluate(thermostat, thermostat_sim).total_eur


def test_a_heat_move_is_taken_only_when_the_exact_objective_falls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A candidate heat plan that is worse for the exact objective is rejected by the guard.

    The search therefore never raises the objective, whatever the heat plan proposes: the
    trace of the accepted plans falls strictly, which excludes a cycle between the heat plan
    and the other devices.
    """
    from akkudoktoreos.optimization.search.moves import MoveContext

    problem, objective = sc.build(sc.make_series("winter_clear"), "C", slots=96)
    settings = SearchSettings(max_passes=3)
    base = DeviceSearch(problem, objective, settings=settings).solve()
    top = problem.thermal[0].heat_limit_w

    def worse(
        self: MoveContext, plan: Plan, sim: Any, device: int, time_limit_s: Any = None
    ) -> Plan:
        candidate = plan.copy()
        candidate.thermal_heat_w[device] = top  # full power all day: far above the optimum
        return candidate

    monkeypatch.setattr(MoveContext, "plan_thermal", worse)
    held = replace(settings, warm_start_compare=False)  # the given plan is the only start
    again = DeviceSearch(problem, objective, settings=held).solve(base.plan)
    assert again.stats.accepted.get("thermal", 0) == 0
    assert again.stats.rejected.get("thermal", 0) > 0
    assert np.array_equal(again.plan.thermal_heat_w, base.plan.thermal_heat_w)
    assert all(b < a for a, b in zip(again.trace, again.trace[1:]))


def test_the_end_value_of_the_heat_holds_the_building_at_the_end() -> None:
    """With the end value the building ends warmer than without it (no free cool-down)."""
    slots = 48
    bands = [
        ThermalComfortBand(
            min_temperature_c=14.0, target_temperature_c=21.0, max_temperature_c=24.0
        )
    ] * slots  # no heat asked by the band
    price = np.where(np.arange(slots) < 36, 1e-4, 1.5e-3)  # dear at the end, cheap before
    problem, objective = small_house(slots, -4.0, bands, price, temperature=[21.0, 21.0])
    off = Objective(
        problem,
        replace(
            objective.settings, thermal=replace(objective.settings.thermal, terminal_scale=0.0)
        ),
    )
    final = []
    for target in (objective, off):
        plan = solve_heat(problem, target).plan
        sim = simulate(problem, plan)
        assert sim.thermal is not None
        final.append(float(sim.thermal.temperature_c[0][-1, 1]))
    assert final[0] > final[1] + 0.1


def test_the_heat_plan_follows_the_dynamics_and_the_cost_curve() -> None:
    """The plan of the program is feasible: valid, inside the band, no start-energy surplus."""
    slots = 48
    bands = [band(20.0, 23.0)] * slots
    price = 3e-4 + 2e-4 * np.sin(np.arange(slots) * 0.9)
    problem, objective = small_house(slots, 0.0, bands, price)
    result = solve_heat(problem, objective)
    assert result.violations == []
    assert result.objective.comfort_penalty_eur == pytest.approx(0.0, abs=1e-6)
    unit = problem.thermal[0]
    assert np.all(result.plan.thermal_heat_w[0] <= unit.heat_limit_w + 1e-9)
    assert np.all(result.plan.thermal_heat_w[0] >= 0.0)


def test_the_shortfall_below_the_comfort_minimum_is_measured_by_tier_and_slot() -> None:
    """Kelvin hours below the minimum count with the weight of their tier (0.5 K, 1 K, the rest)."""
    temperatures = np.array([[21.0, 21.0], [19.7, 0.0], [18.8, 0.0], [17.5, 0.0]])
    weight = np.tile([1.0, 2.0, 4.0], (3, 1))
    out = np.zeros(3)
    thm.comfort_hours(
        temperatures,
        0,
        np.full(3, 20.0),
        np.full(3, 24.0),
        np.full(3, -np.inf),
        np.full(3, 0.25),
        weight,
        out,
    )
    # 0.3 K; 0.5 K + 0.7 K in the second tier; 0.5 K + 1 K + 1 K in the third
    assert out[0] == pytest.approx(0.25 * (0.3 + (0.5 + 1.4) + (0.5 + 2.0 + 4.0)))
    assert out[1] == 0.0 and out[2] == 0.0


def test_the_expected_cost_with_certain_presence_and_one_price_is_the_old_cost() -> None:
    slots = 48
    bands = [band(20.7, 24.0)] * slots
    price = 3e-4 + 2e-4 * np.sin(np.arange(slots) / 6.0)
    plain, plain_objective = small_house(slots, -2.0, bands, price, temperature=[20.0, 20.0])
    tiered, tiered_objective = small_house(
        slots, -2.0, bands, price, temperature=[20.0, 20.0], under_weight=np.ones((slots, 3))
    )
    assert not tiered.thermal[0].tiered
    a, b = solve_heat(plain, plain_objective), solve_heat(tiered, tiered_objective)
    assert b.objective.total_eur == pytest.approx(a.objective.total_eur, rel=1e-9)


def test_unlikely_presence_lets_the_room_fall_and_saves_energy() -> None:
    """With a low probability and cheap first tiers the cold room costs less than the heat."""
    slots = 48
    bands = [band(20.7, 24.0)] * slots
    price = np.full(slots, 4e-4)
    energy = {}
    for name, probability in (("sure", 1.0), ("unlikely", 0.02)):
        weight = probability * np.array([1e-3, 4e-3, 1.0])[None, :].repeat(slots, 0)
        problem, objective = small_house(
            slots, -2.0, bands, price, temperature=[21.0, 21.0], under_weight=weight
        )
        assert problem.thermal[0].tiered
        result = solve_heat(problem, objective)
        sim = simulate(problem, result.plan)
        assert sim.thermal is not None
        energy[name] = float(sim.thermal.electric_wh[0].sum())
        energy[name + "_min"] = float(sim.thermal.temperature_c[0][1:, 0].min())
        # the LP and the exact objective agree on the price of what the plan leaves cold
        assert result.objective.comfort_penalty_eur >= 0.0
    assert energy["unlikely_min"] < energy["sure_min"] - 0.3
    assert energy["unlikely"] < energy["sure"]


# --------------------------------------------------------------------------- electric limit


def _cold_house(limit_w: Optional[float], **kwargs: Any) -> tuple[PlanningProblem, Objective]:
    """Return a cold house that needs the heat pump at (nearly) full power for hours."""
    slots = 24
    return small_house(
        slots,
        -8.0,
        [band(21.0, 23.0)] * slots,
        np.linspace(2e-4, 6e-4, slots),
        temperature=[19.5, 19.5],
        max_electric_power_w=limit_w,
        **kwargs,
    )


def _slot_power_w(problem: PlanningProblem, plan: Plan) -> np.ndarray:
    result = simulate(problem, plan)
    assert result.thermal is not None
    return result.thermal.electric_wh[0] / problem.thermal[0].slot_hours


@pytest.mark.parametrize("running", [False, True])
def test_the_search_keeps_every_slot_within_the_electric_limit_with_the_start_counted(
    running: bool,
) -> None:
    """The slot that starts the heat pump carries the energy of the start against the limit."""
    free, _ = _cold_house(None, running=running)
    unit = free.thermal[0]
    top = float(unit.heat_limit_w[5])
    running_w = thm.thermal_electric_wh(unit.arrays(), 5, top, 1.0)[0] / float(unit.slot_hours[5])
    start_w = thm.thermal_electric_wh(unit.arrays(), 5, top, 0.0)[0] / float(unit.slot_hours[5])
    assert start_w > running_w + 100.0
    limit_w = running_w + 0.5 * (start_w - running_w)  # the top level fits only while running
    problem, objective = _cold_house(limit_w, running=running)
    found = solve_heat(problem, objective, settings=SearchSettings(max_passes=6))
    assert found.violations == []
    power = _slot_power_w(problem, found.plan)
    assert float(found.plan.thermal_heat_w.max()) > 0.0  # it heats
    assert float(power.max()) <= limit_w + 1e-6
    assert float(power[found.plan.thermal_heat_w[0] > 0.0].max()) > 0.9 * limit_w  # the limit binds


def test_the_program_prices_the_start_with_the_power_of_the_simulation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The start power of the limit rows is the start energy of one unit of ramp per hour.

    The simulation starts at the first operating point; the program must not take the (higher)
    power of the highest heat for it, or the two disagree about which starts fit the limit.
    """
    from akkudoktoreos.optimization.search import heatplan
    from akkudoktoreos.optimization.search.moves import MoveContext

    problem, objective = _cold_house(1e5)
    unit = replace(problem.thermal[0], startup_warmup_s=600.0)
    problem = replace(problem, thermal=(unit,))
    objective = Objective(problem, objective.settings)
    captured: dict[str, Any] = {}
    real = heatplan.linprog

    def spy(*args: Any, **kwargs: Any) -> Any:
        captured["A_ub"] = kwargs["A_ub"]
        return real(*args, **kwargs)

    monkeypatch.setattr(heatplan, "linprog", spy)
    plan = Plan.idle(problem)
    candidate = MoveContext(problem, objective, SearchSettings()).plan_thermal(
        plan, simulate(problem, plan), 0
    )
    assert candidate is not None
    matrix = captured["A_ub"].tocsc()
    data = unit.arrays()
    checked = 0
    for slot in range(unit.slots):
        if unit.heat_limit_w[slot] <= thm.RUN_TOLERANCE_W:
            continue
        column = matrix[:, 2 * unit.slots + slot].toarray().ravel()
        expected = (
            thm.thermal_electric_wh(data, slot, float(unit.operating_heat_w[slot, 0]), 0.0)[0]
            - thm.thermal_electric_wh(data, slot, float(unit.operating_heat_w[slot, 0]), 1.0)[0]
        ) / float(unit.slot_hours[slot])
        assert column[column > 0.0] == pytest.approx([expected] * int(np.sum(column > 0.0)))
        assert np.sum(column > 0.0) > 0  # the limit rows carry it
        checked += 1
    assert checked > 0


# --------------------------------------------------------------------------- invalid winners


def _invalid_and_cheaper(
    problem: PlanningProblem, objective: Objective, outcome: Any, by_eur: float
) -> Any:
    """Return the outcome with a heat the device cannot deliver, priced ``by_eur`` cheaper."""
    unit = problem.thermal[0]
    plan = outcome.plan.copy()
    plan.thermal_heat_w[0, 3] = -100.0  # a negative heat is not allowed
    sim = simulate(problem, plan)
    assert [v.reason for v in check_plan(problem, plan)] == ["heat below zero"]
    breakdown = replace(
        objective.evaluate(plan, sim), total_eur=outcome.breakdown.total_eur - by_eur
    )
    return replace(outcome, plan=plan, sim=sim, breakdown=breakdown)


def _search_with_one_invalid_side(monkeypatch: pytest.MonkeyPatch, *, invalid_side: str) -> Any:
    """Solve a warm-started search whose warm or cold candidates come out invalid but cheaper."""
    problem, objective = _cold_house(None, running=True)
    settings = SearchSettings(thermal_order="first", max_passes=3)
    heat = DeviceRef("thermal", 0)
    start = DeviceSearch(problem, objective, settings=settings).solve().plan
    original = DeviceSearch._search_from

    def fake(self: DeviceSearch, plan: Plan, order: str, affected: Any, deadline: Any) -> Any:
        outcome = original(self, plan, order, affected, deadline)
        warm = bool(affected)  # only the warm start carries the affected devices
        if warm == (invalid_side == "warm"):
            return _invalid_and_cheaper(problem, objective, outcome, 1.0)
        return outcome

    monkeypatch.setattr(DeviceSearch, "_search_from", fake)
    return DeviceSearch(problem, objective, settings=settings).solve(start, affected=[heat])


def test_a_warm_start_that_fails_the_check_does_not_win(monkeypatch: pytest.MonkeyPatch) -> None:
    """A warm plan that breaks the allowed values loses to the valid cold plan, however cheap."""
    found = _search_with_one_invalid_side(monkeypatch, invalid_side="warm")
    assert found.violations == []
    assert found.stats.winner == "cold"


def test_a_cold_plan_that_fails_the_check_does_not_win(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every cold plan invalid and cheaper than the warm plan: the valid warm plan is kept."""
    found = _search_with_one_invalid_side(monkeypatch, invalid_side="cold")
    assert found.violations == []
    assert found.stats.winner == "warm"


def test_a_failing_solver_keeps_the_plan_and_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A solver status other than 0 gives no heat plan: the plan is held and the failure counted."""
    from akkudoktoreos.optimization.search import heatplan

    class Failed:
        status = 4
        message = "numerical difficulties"
        x = None

    monkeypatch.setattr(heatplan, "linprog", lambda *args, **kwargs: Failed())
    problem, objective = small_house(24, -3.0, [band(20.0, 23.0)] * 24, 4e-4)
    found = solve_heat(problem, objective)
    assert found.stats.unchanged.get("thermal_failed", 0) > 0
    assert found.stats.accepted.get("thermal", 0) == 0


def test_a_solver_exception_is_a_failure_like_any_other(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exception of the solver keeps the plan; the search of the other devices goes on."""
    from akkudoktoreos.optimization.search import heatplan

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise ValueError(
            "The algorithm terminated successfully and determined that the problem is infeasible."
        )

    monkeypatch.setattr(heatplan, "linprog", explode)
    problem, objective = sc.build(sc.make_series("winter_clear"), "C", slots=48)
    found = DeviceSearch(problem, objective, settings=SearchSettings(max_passes=2)).solve()
    assert found.stats.unchanged.get("thermal_failed", 0) > 0
    assert found.stats.accepted.get("battery", 0) + found.stats.accepted.get("vehicle", 0) > 0


def test_non_finite_input_gives_no_heat_plan_instead_of_an_error() -> None:
    """A NaN in the outdoor temperature reaches the program as non-finite input: no candidate."""
    from akkudoktoreos.optimization.search.moves import MoveContext

    problem, objective = sc.build(sc.make_series("winter_clear"), "C", slots=48)
    unit = problem.thermal[0]
    outdoor = unit.outdoor_c.copy()
    outdoor[5] = np.nan
    broken = replace(problem, thermal=(replace(unit, outdoor_c=outdoor),))
    context = MoveContext(broken, objective, SearchSettings())
    sim = simulate(problem, Plan.idle(problem))
    assert context.plan_thermal(Plan.idle(broken), sim, 0) is None


@pytest.mark.parametrize("leave_running", [False, True])
def test_the_solver_survives_short_lived_and_concurrent_threads(leave_running: bool) -> None:
    """HiGHS crashes the process when a thread that solved ends; the fixed worker prevents it.

    The stress script runs in its own process (a crash must not take the test run with it): 600
    solutions from threads that start, solve and end, one after the other and many at once; with
    ``leave_running`` the process ends while solves are still queued on the worker.
    """
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parent / "heatplan_stress.py"
    command = [sys.executable, str(script), "--solves", "600"]
    if leave_running:
        command.append("--leave-running")
    done = subprocess.run(command, capture_output=True, text=True, timeout=300)  # noqa: S603
    assert done.returncode == 0, (done.returncode, done.stdout[-500:], done.stderr[-500:])


def _house_with_battery(
    slots: int, price: Any, terminal_price_per_wh: float, high_c: float
) -> tuple[PlanningProblem, Objective]:
    """Return a house with a full battery, a heat pump and a wide comfort band (min 20.7 C)."""
    from akkudoktoreos.optimization.objective import TerminalValueSettings

    problem, _ = small_house(slots, -1.0, [band(20.7, high_c)] * slots, price)
    problem = replace(
        problem,
        batteries=(sc.battery_params(26400.0, 65.0, 6.0, 7000.0, (0.1, 0.25, 0.5, 1.0)),),
        inverters=(InverterParams(max_power_w=8000.0, battery_index=0),),
        load_wh=np.full(slots, 100.0),
    )
    settings = ObjectiveSettings(
        terminal_value=TerminalValueSettings(fixed_price_per_wh=terminal_price_per_wh)
    )
    return problem, Objective(problem, settings)


def test_the_heat_is_not_stored_with_battery_energy_that_will_be_needed_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live case of 24.11.2026 16:17: preheating to the band maximum while the battery discharges.

    The battery covers the load of every slot, so a cost curve that holds the state of charge of
    the plan fixed sees the heat as free and heats to the upper band limit. The energy is missing
    later (import at a similar price, end value). The result must not cost more than holding the
    room at the lower band, whose battery plan the search finds with the heat held fixed.
    """
    from akkudoktoreos.optimization.search import search as search_module

    slots = 48
    price = np.concatenate([np.linspace(6.7e-4, 5.0e-4, 24), np.linspace(4.9e-4, 3.3e-4, 24)])
    problem, objective = _house_with_battery(slots, price, 4.5e-4, 24.0)
    found = DeviceSearch(problem, objective, settings=SearchSettings(max_passes=6)).solve()

    # hold the room at the target with a thermostat, battery re-planned against it
    unit = problem.thermal[0]
    hold = found.plan.copy()
    temperature = unit.initial_temperature_c.copy()
    heat = np.zeros(slots)
    for slot in range(slots):
        free = unit.dynamics_a[slot] @ temperature + unit.dynamics_e[slot] * unit.outdoor_c[slot]
        need = (21.0 - free[0]) / unit.dynamics_b[slot][0]
        heat[slot] = float(np.clip(need, 0.0, unit.heat_limit_w[slot]))
        temperature = free + unit.dynamics_b[slot] * heat[slot]
    hold.thermal_heat_w[0] = heat
    monkeypatch.setattr(search_module.MoveContext, "plan_thermal", lambda *args, **kwargs: None)
    held = DeviceSearch(problem, objective, settings=SearchSettings(max_passes=6)).solve(hold)
    assert found.objective.total_eur <= held.objective.total_eur + 0.02, (
        found.objective.total_eur,
        held.objective.total_eur,
    )


def test_a_simulation_without_thermal_result_gives_no_heat_plan_instead_of_an_error() -> None:
    """A plan whose simulation has no thermal result is no candidate; the run goes on."""
    from akkudoktoreos.optimization.search.moves import MoveContext

    problem, objective = sc.build(sc.make_series("winter_clear"), "C", slots=48)
    context = MoveContext(problem, objective, SearchSettings())
    plan = Plan.idle(problem)
    sim = simulate(replace(problem, thermal=()), Plan.idle(replace(problem, thermal=())))
    assert sim.thermal is None
    assert context.plan_thermal(plan, sim, 0) is None


# --------------------------------------------------------------------------- negative end value


def _plan_thermal_with_warnings(
    problem: PlanningProblem, objective: Objective
) -> tuple[Optional[Plan], list[str]]:
    """Return the heat candidate of the first thermal device and the warnings logged on the way."""
    from loguru import logger

    from akkudoktoreos.optimization.search.moves import MoveContext

    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    try:
        plan = Plan.idle(problem)
        context = MoveContext(problem, objective, SearchSettings())
        candidate = context.plan_thermal(plan, simulate(problem, plan), 0)
    finally:
        logger.remove(sink)
    return candidate, messages


def _hold_band_plan(problem: PlanningProblem, share: float) -> Plan:
    """Return a plan that runs the heat pump at a share of its highest heat in every slot."""
    plan = Plan.idle(problem)
    plan.thermal_heat_w[0] = share * problem.thermal[0].heat_limit_w
    return plan


def test_negative_prices_give_a_finite_heat_plan_and_no_reward_for_a_cold_end() -> None:
    """8 slots at -0.10 EUR/kWh: the end value of the heat must not turn negative.

    A negative mean price made the slack of the end value (deficit times a negative value) pay for
    a cold end: the program was unbounded (no plan, the old one kept) and the exact objective
    rewarded the deficit. The plan is found, the solver does not warn, and the exact objective of
    the result is at least as good as a plan at 45 % that holds the comfort band.
    """
    slots = 8
    problem, objective = small_house(slots, -3.0, [band(20.0, 23.0)] * slots, -1e-4)
    candidate, warnings = _plan_thermal_with_warnings(problem, objective)
    assert warnings == []
    assert candidate is not None
    assert np.all(np.isfinite(candidate.thermal_heat_w))

    found = solve_heat(problem, objective)
    assert found.violations == []
    held = _hold_band_plan(problem, 0.45)
    held_breakdown = objective.evaluate(held, simulate(problem, held))
    assert held_breakdown.comfort_penalty_eur == pytest.approx(0.0, abs=1e-6)
    assert found.objective.heat_terminal_eur >= 0.0
    assert found.objective.total_eur <= held_breakdown.total_eur + 1e-9
    assert objective.heat_value_per_wh(0) >= 0.0


def test_a_negative_price_in_the_tail_does_not_make_the_end_value_negative() -> None:
    """Positive prices in the horizon, a negative mean price in the tail forecast."""
    slots = 8
    problem, _ = small_house(slots, -3.0, [band(20.0, 23.0)] * slots, 3e-4)
    problem = replace(
        problem,
        tail_pv_wh=np.zeros(slots),
        tail_load_wh=np.full(slots, 60.0),
        tail_import_price_per_wh=np.full(slots, -1e-4),
        tail_feed_in_tariff_per_wh=np.zeros(slots),
    )
    objective = Objective(problem, ObjectiveSettings())
    candidate, warnings = _plan_thermal_with_warnings(problem, objective)
    assert warnings == []
    assert candidate is not None
    found = solve_heat(problem, objective)
    assert found.violations == []
    # no reward for the cold end: the end value penalty is not negative
    assert found.objective.heat_terminal_eur >= 0.0
    assert objective.heat_value_per_wh(0) >= 0.0


@pytest.mark.parametrize("value", ["above_table", "nan"])
def test_the_check_reports_a_heat_the_simulation_cannot_take_instead_of_raising(
    value: str,
) -> None:
    """Heat above the table or NaN is a violation of the check, found before any simulation."""
    problem, _ = small_house(8, -3.0, [band(20.0, 23.0)] * 8, 3e-4)
    plan = Plan.idle(problem)
    plan.thermal_heat_w[0, 2] = float(problem.thermal[0].heat_limit_w[2]) + 1.0
    if value == "nan":
        plan.thermal_heat_w[0, 2] = float("nan")
    found = check_plan(problem, plan)
    assert [(v.device, v.index, v.slot) for v in found] == [("thermal", 0, 2)]
    assert found[0].reason == "heat not finite or above the table"
    plan.thermal_heat_w[0, 2] = float(problem.thermal[0].heat_limit_w[2])  # the top is fine
    assert check_plan(problem, plan) == []
