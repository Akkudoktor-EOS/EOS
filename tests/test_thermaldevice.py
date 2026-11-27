"""Thermal device of the kernel: physics parity, electricity, balance, objective parts, partials."""

from dataclasses import replace
from typing import Any

import numpy as np
import pytest
import test_searchscenarios as sc

from akkudoktoreos.devices.heatpump import (
    HeatPumpProfile,
    evaluate_heat_pump,
    evaluate_heat_pump_electricity,
    get_heat_pump_operating_points,
)
from akkudoktoreos.devices.models import thermal as thm
from akkudoktoreos.devices.models.inverter import InverterParams
from akkudoktoreos.optimization.kernel import Plan, PlanningProblem, simulate, simulate_batch
from akkudoktoreos.optimization.objective import Objective, ObjectiveSettings
from akkudoktoreos.optimization.search.checks import check_plan, repair_plan
from akkudoktoreos.prediction.weatherabc import WeatherDataRecord
from akkudoktoreos.thermal.planninginput import build_thermal_device, steady_reference
from akkudoktoreos.thermal.thermaldistribution import (
    DirectDistributionSettings,
    DistributionTemperatureBounds,
    WaterDistributionSettings,
    distribution_electric_power_w,
    step_distribution,
)
from akkudoktoreos.thermal.thermalmodels import ThermalComfortBand

SLOTS = 48
BAND = ThermalComfortBand(min_temperature_c=20.0, target_temperature_c=21.0, max_temperature_c=23.0)
ELECTRICAL = sc.HEAT_ELECTRICAL.model_copy(update={"startup_warmup_seconds": 120.0})

# A profile with paired operating points that interpolate linearly between outdoor slices.
OPERATING_PROFILE = HeatPumpProfile.model_validate(
    {
        "profile_id": "operating",
        "heat_pump_type": "air_to_air",
        "cop_points": [
            {"outdoor_temperature_c": -10.0, "delivery_temperature_c": 20.0, "cop": 2.5},
            {"outdoor_temperature_c": 10.0, "delivery_temperature_c": 20.0, "cop": 4.0},
        ],
        "thermal_max_points": [
            {"outdoor_temperature_c": t, "delivery_temperature_c": 20.0, "thermal_power_w": 4500.0}
            for t in (-10.0, 10.0)
        ],
        "operating_points": [
            {
                "outdoor_temperature_c": t,
                "delivery_temperature_c": 20.0,
                "thermal_power_w": q,
                "electric_power_w": e,
                "point_id": f"p{t}-{q}",
                "source": "test",
            }
            for t, rows in {
                -10.0: [(1500, 700), (3000, 1500), (4500, 2500)],
                0.0: [(1500, 500), (3000, 1000), (4500, 1700)],
                10.0: [(1500, 350), (3000, 700), (4500, 1200)],
            }.items()
            for q, e in rows
        ],
        "operating_point_interpolation": "linear_common_heat",
        "provenance": {
            "source": "test",
            "quality": "user_supplied",
            "electric_power_basis": "total_system",
        },
    }
)


def device(
    kind: str,
    profile: HeatPumpProfile = sc.HEAT_PROFILE,
    outdoor: Any = None,
    **kwargs: Any,
) -> thm.ThermalParams:
    """Return a thermal device: ``kind`` is ``"direct"`` or ``"air"``."""
    direct = kind == "direct"
    outdoor_c = np.linspace(-6.0, 4.0, SLOTS) if outdoor is None else outdoor
    defaults: dict[str, Any] = {
        "outdoor_c": outdoor_c,
        "comfort_bands": [BAND] * len(outdoor_c),
        "initial_temperature_c": [21.0, 21.0] if direct else [21.0, 21.0, 21.0],
        "delivery_temperature_c": 20.0,
        "electrical": ELECTRICAL,
    }
    defaults.update(kwargs)
    return build_thermal_device(
        sc.HEAT_BUILDING,
        DirectDistributionSettings() if direct else sc.HEAT_AIR,
        profile,
        **defaults,
    )


def house(unit: thm.ThermalParams, **kwargs: Any) -> PlanningProblem:
    """Return a small house (no battery, no PV) with the thermal device."""
    slots = unit.slots
    return PlanningProblem(
        slot_duration_h=0.25,
        pv_wh=np.zeros(slots),
        load_wh=np.full(slots, 80.0),
        import_price_per_wh=np.linspace(2e-4, 5e-4, slots),
        feed_in_tariff_per_wh=np.zeros(slots),
        inverters=(InverterParams(max_power_w=8000.0, battery_index=None),),
        thermal=(unit,),
        **kwargs,
    )


def random_heat(unit: thm.ThermalParams, seed: int) -> np.ndarray:
    """Return a random heat plan: off, any share of the highest heat, or the highest heat."""
    rng = np.random.RandomState(seed)
    top = unit.heat_limit_w
    share = np.where(rng.random_sample(unit.slots) < 0.3, 0.0, rng.random_sample(unit.slots))
    share = np.where(rng.random_sample(unit.slots) < 0.2, 1.0, share)
    return share * top


def on_off_heat(unit: thm.ThermalParams, seed: int) -> np.ndarray:
    """Return a random plan of off and the highest heat (full run share)."""
    rng = np.random.RandomState(seed)
    return np.where(rng.random_sample(unit.slots) < 0.5, 0.0, unit.heat_limit_w)


@pytest.mark.parametrize("kind", ["direct", "air"])
@pytest.mark.parametrize("seed", range(5))
def test_temperatures_equal_the_distribution_step(kind: str, seed: int) -> None:
    """Random heat sequences give the temperatures of ``step_distribution`` (<= 1e-9 K)."""
    unit = device(kind)
    problem = house(unit)
    plan = Plan.idle(problem)
    plan.thermal_heat_w[0] = random_heat(unit, seed)
    result = simulate(problem, plan)
    assert result.thermal is not None
    distribution = DirectDistributionSettings() if kind == "direct" else sc.HEAT_AIR
    temperature = np.array(unit.initial_temperature_c)
    worst = 0.0
    for slot in range(SLOTS):
        step = step_distribution(
            sc.HEAT_BUILDING,
            distribution,
            temperature.tolist(),
            thermal_power_w=float(plan.thermal_heat_w[0, slot]),
            outdoor_temperature_c=float(unit.outdoor_c[slot]),
            seconds=900.0,
        )
        temperature = np.array(step.temperatures_c)
        worst = max(
            worst, float(np.abs(temperature - result.thermal.temperature_c[0][slot + 1]).max())
        )
    assert worst <= 1e-9


@pytest.mark.parametrize("kind", ["direct", "air"])
@pytest.mark.parametrize("profile", ["fixed", "operating"])
def test_electricity_equals_the_heat_pump_accounting(kind: str, profile: str) -> None:
    """At run share 0 or 1 the energy equals ``evaluate_heat_pump_electricity`` (<= 1e-9 Wh)."""
    spec = sc.HEAT_PROFILE if profile == "fixed" else OPERATING_PROFILE
    unit = device(kind, spec, outdoor=np.linspace(-8.0, 8.0, SLOTS))
    problem = house(unit)
    worst = 0.0
    for seed in range(4):
        plan = Plan.idle(problem)
        plan.thermal_heat_w[0] = on_off_heat(unit, seed)
        result = simulate(problem, plan)
        assert result.thermal is not None
        was_running = False
        for slot in range(SLOTS):
            heat = float(plan.thermal_heat_w[0, slot])
            record = WeatherDataRecord(weather_temp_air=float(unit.outdoor_c[slot]))
            if profile == "fixed":
                rating = evaluate_heat_pump(sc.HEAT_PROFILE, record, 20.0)
                performance = evaluate_heat_pump(
                    sc.HEAT_PROFILE, record, 20.0, thermal_load_w=rating.max_thermal_power_w
                )
            else:
                points = get_heat_pump_operating_points(OPERATING_PROFILE, record, 20.0)
                base = float(
                    np.interp(
                        heat,
                        [p.thermal_power_w for p in points],
                        [p.electric_power_w for p in points],
                    )
                )
                rating = evaluate_heat_pump(OPERATING_PROFILE, record, 20.0)
                performance = rating.model_copy(
                    update={
                        "cop": heat / base if heat > 0 else 1.0,
                        "min_thermal_power_w": None,
                        "supported": True,
                        "max_thermal_power_w": points[-1].thermal_power_w,
                    }
                )
            running = heat > thm.RUN_TOLERANCE_W
            breakdown = evaluate_heat_pump_electricity(
                performance, heat if running else 0.0, 900.0, running, was_running, ELECTRICAL
            )
            fan, _ = distribution_electric_power_w(
                DirectDistributionSettings() if kind == "direct" else sc.HEAT_AIR
            )
            expected = breakdown.total_energy_wh + (fan or 0.0) * 0.25
            if (
                breakdown.started
            ):  # the warm-up runs at the first point, not at the load of the slot
                warmup_h = ELECTRICAL.startup_warmup_seconds / 3600.0
                first_w = float(unit.operating_electric_w[slot, 0])
                expected += warmup_h * (first_w - breakdown.base_energy_wh / 0.25)
            worst = max(worst, abs(expected - result.thermal.electric_wh[0, slot]))
            share = float(result.thermal.share[0, slot])
            assert share == (1.0 if running else 0.0)
            assert breakdown.started == (running and not was_running)
            was_running = running
    assert worst <= 1e-9


def test_house_balance_closes_with_the_heat_pump() -> None:
    """The energy balance of every slot holds (<= 1e-9) and the load contains the heat pump."""
    series = sc.make_series("winter_clear")
    problem, _ = sc.build(series, "C", slots=96)
    plan = Plan.idle(problem)
    plan.thermal_heat_w[0] = random_heat(problem.thermal[0], 7)
    result = simulate(problem, plan)
    assert result.thermal is not None
    assert np.abs(result.energy_balance_residual_wh).max() <= 1e-9
    base = problem.load_wh + result.appliance_wh
    heat_load = result.thermal.electric_wh[0]
    assert result.load_wh == pytest.approx(base + heat_load, abs=1e-9)
    assert result.grid_import_wh.sum() > 0.0


def test_heat_above_the_table_is_rejected_below_it_is_time_sharing() -> None:
    """A heat above the maximum is an error; below the first point the chord from 0 applies."""
    unit = device("direct")
    problem = house(unit)
    plan = Plan.idle(problem)
    plan.thermal_heat_w[0, 3] = unit.heat_range_w(3)[1] + 100.0  # type: ignore[index]
    with pytest.raises(ValueError, match="operating table"):
        simulate(problem, plan)
    low, _ = unit.heat_range_w(3)  # type: ignore[misc]
    half = 0.5 * low
    plan.thermal_heat_w[0, 3] = half
    result = simulate(problem, plan)
    assert result.thermal is not None
    first_w = float(unit.operating_electric_w[3, 0])
    compressor = thm.base_electric_w(
        unit.operating_heat_w, unit.operating_electric_w, unit.operating_count, 3, half
    )
    assert compressor == pytest.approx(0.5 * first_w)
    assert float(result.thermal.share[0, 3]) == pytest.approx(0.5)  # the run time, not heat / top


def test_the_start_energy_is_the_ramp_of_the_run_share() -> None:
    """A rising run time pays the start energy for its rise only; a falling one pays none."""
    unit = device("direct")
    problem = house(unit)
    first = unit.operating_heat_w[:, 0]
    plan = Plan.idle(problem)
    plan.thermal_heat_w[0, 3] = 0.5 * first[3]
    plan.thermal_heat_w[0, 4] = 0.75 * first[4]
    plan.thermal_heat_w[0, 5] = 0.25 * first[5]
    result = simulate(problem, plan)
    assert result.thermal is not None
    fixed = unit.fixed_power_w
    assert fixed is not None
    for slot, now, before in ((3, 0.5, 0.0), (4, 0.75, 0.5), (5, 0.25, 0.75)):
        heat = now * first[slot]
        base = thm.base_electric_w(
            unit.operating_heat_w, unit.operating_electric_w, unit.operating_count, slot, heat
        )
        power = base + fixed[slot] + unit.running_auxiliary_power_w * now
        start = (
            unit.startup_energy_wh
            + unit.startup_warmup_s / 3600.0 * unit.operating_electric_w[slot, 0]
        )
        expected = power * unit.slot_hours[slot] + max(now - before, 0.0) * start
        assert result.thermal.electric_wh[0, slot] == pytest.approx(expected, abs=1e-9)
    # A heat pump that ran before slot 0 starts from the share 1: no start energy at slot 0.
    running = device("direct", initial_running=True)
    plan = Plan.idle(house(running))
    plan.thermal_heat_w[0, 0] = running.heat_limit_w[0]
    again = simulate(house(running), plan)
    assert again.thermal is not None
    full = (
        thm.base_electric_w(
            running.operating_heat_w,
            running.operating_electric_w,
            running.operating_count,
            0,
            float(running.heat_limit_w[0]),
        )
        + float(running.fixed_power_w[0])  # type: ignore[index]
        + running.running_auxiliary_power_w
    ) * float(running.slot_hours[0])
    assert again.thermal.electric_wh[0, 0] == pytest.approx(full, abs=1e-9)


def _slot_energy_wh(unit: thm.ThermalParams, slot: int, heat_w: float, previous: float) -> float:
    """Return the electric energy of a slot [Wh] as the simulation counts it."""
    return float(thm.thermal_electric_wh(unit.arrays(), slot, heat_w, previous)[0])


def test_below_the_first_point_the_run_time_carries_the_auxiliaries_and_the_start() -> None:
    """500 W of 1 kW at the first point (5 kW highest): the compressor runs half of the slot.

    The auxiliaries run with it (50 %, not 10 % of the highest heat) and the start is the rise
    of the run time: half of the start energy and of the warm-up for the half slot from off, the
    whole of both when the compressor runs the whole slot at its first point.
    """
    unit = device("direct")
    slot = 3
    first_w = float(unit.operating_heat_w[slot, 0])
    first_electric_w = float(unit.operating_electric_w[slot, 0])
    assert float(unit.heat_limit_w[slot]) > 2.0 * first_w
    assert unit.running_auxiliary_power_w > 0.0 and unit.startup_energy_wh > 0.0
    fixed = float(unit.fixed_power_w[slot])  # type: ignore[index]
    hours = float(unit.slot_hours[slot])
    start_wh = unit.startup_energy_wh + unit.startup_warmup_s / 3600.0 * first_electric_w
    assert unit.startup_warmup_s > 0.0
    for run_time in (0.5, 1.0):
        energy = _slot_energy_wh(unit, slot, run_time * first_w, 0.0)
        power = run_time * first_electric_w + fixed + unit.running_auxiliary_power_w * run_time
        assert energy == pytest.approx(power * hours + run_time * start_wh, abs=1e-9)
    # A start that is the whole slot costs the whole start energy, however high the table goes.
    started = _slot_energy_wh(unit, slot, first_w, 0.0) - _slot_energy_wh(unit, slot, first_w, 1.0)
    assert started == pytest.approx(start_wh, abs=1e-9)


def test_modulating_a_running_compressor_up_is_no_start() -> None:
    """From the first point to the highest heat the compressor keeps running: no start energy."""
    unit = device("direct")
    problem = house(unit)
    slot = 3
    first_w = float(unit.operating_heat_w[slot, 0])
    top_w = float(unit.heat_limit_w[slot])
    assert top_w > first_w
    plan = Plan.idle(problem)
    plan.thermal_heat_w[0, slot] = first_w
    plan.thermal_heat_w[0, slot + 1] = top_w
    result = simulate(problem, plan)
    assert result.thermal is not None
    assert result.thermal.share[0, slot] == pytest.approx(1.0)
    assert result.thermal.share[0, slot + 1] == pytest.approx(1.0)
    running = _slot_energy_wh(unit, slot + 1, top_w, 1.0)
    assert result.thermal.electric_wh[0, slot + 1] == pytest.approx(running, abs=1e-9)
    assert np.flatnonzero(result.thermal.started[0]).tolist() == [slot]


def test_water_distribution_is_rejected_with_a_clear_message() -> None:
    """Water and buffer distributions (4-5 states) are not available yet."""
    water = WaterDistributionSettings(
        emitter_type="radiator",
        water_capacity_j_per_k=1e5,
        emitter_capacity_j_per_k=1e6,
        water_to_emitter_conductance_w_per_k=300.0,
        emitter_to_room_conductance_w_per_k=300.0,
        water_temperature_bounds=DistributionTemperatureBounds(
            min_temperature_c=5.0, max_temperature_c=60.0
        ),
        emitter_temperature_bounds=DistributionTemperatureBounds(
            min_temperature_c=5.0, max_temperature_c=60.0
        ),
    )
    with pytest.raises(ValueError, match="water and buffer"):
        build_thermal_device(
            sc.HEAT_BUILDING,
            water,
            sc.HEAT_PROFILE,
            outdoor_c=np.zeros(4),
            comfort_bands=[BAND] * 4,
            initial_temperature_c=[21.0] * 5,
            delivery_temperature_c=35.0,
        )


def test_unsupported_rating_is_rejected_unless_clamped() -> None:
    """A slot outside of the support of the performance map is rejected."""
    cold = np.full(8, -40.0)
    with pytest.raises(ValueError, match="outside of the support"):
        device("direct", outdoor=cold)
    assert device("direct", outdoor=cold, allow_clamped_rating=True).slots == 8


def test_blocked_slots_and_checks() -> None:
    """A blocked slot has no heat; the check finds heat there and the repair removes it."""
    blocked = np.zeros(SLOTS, dtype=bool)
    blocked[10:16] = True
    unit = device("direct", blocked=blocked)
    assert unit.heat_range_w(12) is None and unit.heat_limit_w[12] == 0.0
    problem = house(unit)
    plan = Plan.idle(problem)
    plan.thermal_heat_w[0, 5] = 1234.5
    plan.thermal_heat_w[0, 12] = 3000.0
    plan.thermal_heat_w[0, 20] = -50.0
    reasons = {(v.slot, v.reason) for v in check_plan(problem, plan)}
    assert reasons == {(12, "heat while blocked"), (20, "heat below zero")}
    repaired = repair_plan(problem, plan)
    assert check_plan(problem, repaired) == []
    assert repaired.thermal_heat_w[0, 12] == 0.0
    assert repaired.thermal_heat_w[0, 5] == plan.thermal_heat_w[0, 5]


def test_comfort_penalty_is_the_price_times_the_kelvin_hours() -> None:
    """Penalties = price x Kelvin hours (under, over, protection), partial runs = full - prefix."""
    series = sc.make_series("winter_clear")
    problem, objective = sc.build(series, "C", slots=96)
    plan = Plan.idle(problem)
    plan.thermal_heat_w[0] = random_heat(problem.thermal[0], 3)
    maxima = [problem.thermal[0].heat_range_w(s) for s in range(40, 70)]
    assert all(m is not None for m in maxima)
    plan.thermal_heat_w[0, 40:70] = np.array([m[1] for m in maxima if m is not None])
    result = simulate(problem, plan)
    assert result.thermal is not None
    breakdown = objective.evaluate(plan, result)
    comfort, protection = objective.thermal_prices()
    under = float(result.thermal.under_kh[0] + result.thermal.over_kh[0])
    assert under > 0.0
    assert breakdown.comfort_penalty_eur == pytest.approx(comfort * under, rel=1e-12)
    assert breakdown.protection_penalty_eur == pytest.approx(
        protection * float(result.thermal.protection_kh[0]), rel=1e-12
    )
    # The compiled per-slot cost of the search is the same number.
    from akkudoktoreos.optimization.objective import thermal_comfort_cost

    unit = problem.thermal[0]
    cost = thermal_comfort_cost(
        result.thermal.temperature_c[0],
        0,
        unit.comfort_min_c,
        unit.comfort_max_c,
        unit.protection_min_c,  # type: ignore[arg-type]
        unit.slot_hours,
        comfort,
        protection,
    )
    assert cost == pytest.approx(breakdown.comfort_penalty_eur + breakdown.protection_penalty_eur)
    # Partial run from slot k: the parts of the slots from k on.
    for start in (1, 17, 50):
        part = simulate(
            problem, plan, start, result.soc_wh[:, start], result.thermal.state_at(start)
        )
        assert part.thermal is not None
        assert np.allclose(part.thermal.temperature_c[0], result.thermal.temperature_c[0][start:])
        assert part.thermal.electric_wh[0] == pytest.approx(result.thermal.electric_wh[0][start:])
        assert part.total_balance_eur == pytest.approx(
            float(result.cost_eur[start:].sum() - result.revenue_eur[start:].sum()), abs=1e-12
        )


def test_terminal_value_of_the_heat_is_priced_with_the_cost_of_making_it_up() -> None:
    """The deficit against the reference state is valued mean price / mean COP; more earns nothing."""
    series = sc.make_series("winter_clear")
    problem, objective = sc.build(series, "C", slots=96)
    unit = problem.thermal[0]
    assert unit.reference_heat_w > 0.0
    value = objective.heat_value_per_wh(0)
    assert value > 0.0
    # Value = mean price / mean COP of the last 24 hours, where the COP is the one at the middle
    # heat of each slot.
    cops = []
    for slot in range(0, 96):
        low, high = unit.heat_range_w(slot)  # type: ignore[misc]
        middle = 0.5 * (low + high)
        cops.append(
            middle
            / thm.base_electric_w(
                unit.operating_heat_w, unit.operating_electric_w, unit.operating_count, slot, middle
            )
        )
    expected = float(np.mean(problem.import_price_per_wh) / np.mean(cops))
    assert value == pytest.approx(expected, rel=1e-12)
    cold = unit.reference_temperature_c - np.array([1.0, 0.5])
    warm = unit.reference_temperature_c + np.array([3.0, 1.0])
    deficit_wh = float(unit.capacity_wh_per_k @ np.array([1.0, 0.5]))
    assert objective.thermal_terminal_eur(0, cold) == pytest.approx(deficit_wh * value, rel=1e-12)
    assert objective.thermal_terminal_eur(0, warm) == 0.0
    doubled = Objective(
        problem,
        replace(
            objective.settings, thermal=replace(objective.settings.thermal, terminal_scale=2.0)
        ),
    )
    assert doubled.thermal_terminal_eur(0, cold) == pytest.approx(
        2.0 * objective.thermal_terminal_eur(0, cold), rel=1e-12
    )
    off = Objective(
        problem,
        replace(
            objective.settings, thermal=replace(objective.settings.thermal, terminal_scale=0.0)
        ),
    )
    assert off.thermal_terminal_eur(0, cold) == 0.0


def test_no_end_value_without_heating_load() -> None:
    """Where the building needs no heat at the reference state the end value is 0."""
    unit = device("direct", outdoor=np.full(SLOTS, 30.0), allow_clamped_rating=True)
    assert unit.reference_heat_w < 0.0
    problem = house(unit)
    assert Objective(problem).heat_value_per_wh(0) == 0.0


def test_steady_reference_holds_the_room_temperature() -> None:
    """The reference state is a fixed point of the dynamics with the reference heat."""
    unit = device("air", outdoor=np.full(SLOTS, -4.0))
    state, heat = steady_reference(
        unit.dynamics_a[0], unit.dynamics_b[0], unit.dynamics_e[0], -4.0, 21.0
    )
    nxt = unit.dynamics_a[0] @ state + unit.dynamics_b[0] * heat + unit.dynamics_e[0] * -4.0
    assert nxt == pytest.approx(state, abs=1e-9)
    assert state[0] == pytest.approx(21.0)
    # Heat that holds 21 K at -4 degrees: about the loss of the room (0.12/h of 556 Wh/K).
    assert heat == pytest.approx(0.12 * 555.56 * 25.0, rel=0.05)


def test_batch_equals_single_and_plans_with_other_heat_fall_back() -> None:
    """Batch totals equal the single ones, also for plans whose heat plans differ."""
    series = sc.make_series("shoulder_mixed")
    problem, objective = sc.build(series, "C", slots=96)
    unit = problem.thermal[0]
    base = Plan.idle(problem)
    base.thermal_heat_w[0] = random_heat(unit, 1)
    other = base.copy()
    other.thermal_heat_w[0] = random_heat(unit, 2)
    for plans in ([base, base.copy()], [base, other]):
        batch = simulate_batch(problem, plans)
        objective_batch = objective.evaluate_batch(plans)
        for position, plan in enumerate(plans):
            single = simulate(problem, plan)
            assert batch.total_balance_eur[position] == pytest.approx(
                single.total_balance_eur, abs=1e-12
            )
            assert objective_batch.total_eur[position] == pytest.approx(
                objective.evaluate(plan, single).total_eur, abs=1e-12
            )


def test_without_thermal_devices_nothing_changes() -> None:
    """A problem without thermal devices simulates exactly as before (no thermal result)."""
    problem, objective = sc.build(sc.make_series("price_spike"), "A", slots=96)
    plan = Plan.idle(problem)
    result = simulate(problem, plan)
    assert result.thermal is None
    assert plan.thermal_heat_w.size == 0
    breakdown = objective.evaluate(plan, result)
    assert breakdown.comfort_penalty_eur == breakdown.protection_penalty_eur == 0.0


def test_ratings_and_validation() -> None:
    """Parameter validation: states, shapes, ascending operating points."""
    unit = device("direct")
    with pytest.raises(ValueError, match="2 .direct. or 3"):
        replace(unit, initial_temperature_c=np.array([21.0]))
    with pytest.raises(ValueError, match="ascend"):
        bad = unit.operating_heat_w.copy()
        bad[0, 0], bad[0, 1] = 3000.0, 1000.0
        replace(unit, operating_heat_w=bad)
    problem = house(unit)
    with pytest.raises(ValueError, match="thermal_heat_w"):
        Plan.idle(replace(problem, thermal=())).validate(problem)


def test_air_distribution_is_a_third_state() -> None:
    """With the air node the heat pump heats the air; the room follows with a delay."""
    unit = device("air", outdoor=np.full(SLOTS, 0.0))
    problem = house(unit)
    plan = Plan.idle(problem)
    plan.thermal_heat_w[0, :4] = unit.heat_range_w(0)[1]  # type: ignore[index]
    result = simulate(problem, plan)
    assert result.thermal is not None
    trajectory = result.thermal.temperature_c[0]
    assert trajectory.shape == (SLOTS + 1, 3)
    assert trajectory[1, 2] > trajectory[1, 0]  # the air node is the hottest one
    assert ObjectiveSettings().thermal.comfort_eur_per_kh > 0.0


def sustained_electric_w(unit: thm.ThermalParams, slot: int, heat_w: float) -> float:
    """Electric power while running at a heat: compressor, standby, circulation, auxiliaries."""
    base = thm.base_electric_w(
        unit.operating_heat_w, unit.operating_electric_w, unit.operating_count, slot, heat_w
    )
    assert base >= 0.0
    assert unit.fixed_power_w is not None
    return base + float(unit.fixed_power_w[slot]) + unit.running_auxiliary_power_w


@pytest.mark.parametrize("kind", ["direct", "air"])
def test_the_electric_limit_cuts_the_highest_heat(kind: str) -> None:
    """No slot draws more than the limit at its highest heat; the cut slots sit on it."""
    free = device(kind)
    limit_w = 700.0
    capped = device(kind, max_electric_power_w=limit_w)
    cut = 0
    for slot in range(SLOTS):
        top = float(capped.heat_limit_w[slot])
        assert top > 0.0
        assert sustained_electric_w(capped, slot, top) <= limit_w + 1e-6
        if sustained_electric_w(free, slot, float(free.heat_limit_w[slot])) > limit_w:
            cut += 1
            assert top < free.heat_limit_w[slot]
            assert sustained_electric_w(capped, slot, top) == pytest.approx(limit_w)
        else:
            assert top == pytest.approx(free.heat_limit_w[slot])
    assert cut > 0  # the limit is binding in this scenario


def test_a_slot_that_cannot_run_within_the_limit_is_blocked() -> None:
    """A limit below the lowest operating point (plus the fixed power) bars the slot."""
    unit = device("direct", max_electric_power_w=50.0)
    assert unit.blocked is not None and bool(np.all(unit.blocked))
    assert all(unit.heat_range_w(slot) is None for slot in range(SLOTS))


def slot_power_w(unit: thm.ThermalParams, heat: np.ndarray) -> np.ndarray:
    """Return the electric power of every slot of a heat plan (the energy of a start counted)."""
    problem = house(unit)
    plan = Plan.idle(problem)
    plan.thermal_heat_w[0] = heat
    result = simulate(problem, plan)
    assert result.thermal is not None
    return result.thermal.electric_wh[0] / unit.slot_hours


def _limit_between_run_and_start(kind: str) -> float:
    """Return a limit that the top heat of slot 3 meets while running but not as a start."""
    free = device(kind)
    top = float(free.heat_limit_w[3])
    running = sustained_electric_w(free, 3, top)
    started = thm.thermal_electric_wh(free.arrays(), 3, top, 0.0)[0] / float(free.slot_hours[3])
    assert started > running + 100.0
    return running + 0.5 * (started - running)


@pytest.mark.parametrize("kind", ["direct", "air"])
def test_a_start_counts_its_energy_against_the_electric_limit(kind: str) -> None:
    """The slot that ramps the run share up carries the start energy against the limit."""
    limit_w = _limit_between_run_and_start(kind)
    unit = device(kind, max_electric_power_w=limit_w)
    top = float(unit.heat_limit_w[3])
    assert sustained_electric_w(unit, 3, top) <= limit_w  # the table allows it while running
    heat = np.zeros(unit.slots)
    heat[3] = top  # off before slot 3: a start with the ramp 1
    problem = house(unit)
    plan = Plan.idle(problem)
    plan.thermal_heat_w[0] = heat
    reasons = [(v.slot, v.reason) for v in check_plan(problem, plan)]
    assert (3, "electric power above the limit") in reasons
    # The same heat in a run that is already going at the full share is fine.
    heat[2] = float(unit.heat_limit_w[2])
    plan.thermal_heat_w[0] = heat
    assert (3, "electric power above the limit") not in [
        (v.slot, v.reason) for v in check_plan(problem, plan)
    ]
    # Starting from a running heat pump needs no start energy at all.
    running = device(kind, max_electric_power_w=limit_w, initial_running=True)
    heat = np.zeros(running.slots)
    heat[0] = float(running.heat_limit_w[0])
    plan = Plan.idle(house(running))
    plan.thermal_heat_w[0] = heat
    assert not [v for v in check_plan(house(running), plan) if v.slot == 0]


@pytest.mark.parametrize("kind", ["direct", "air"])
def test_repair_cuts_a_start_back_to_what_fits_the_electric_limit(kind: str) -> None:
    """A start above the limit is cut to the largest heat that fits; the check then passes."""
    limit_w = _limit_between_run_and_start(kind)
    unit = device(kind, max_electric_power_w=limit_w)
    problem = house(unit)
    plan = Plan.idle(problem)
    plan.thermal_heat_w[0, 3:7] = unit.heat_limit_w[3:7]
    repaired = repair_plan(problem, plan)
    assert check_plan(problem, repaired) == []
    assert 0.0 < repaired.thermal_heat_w[0, 3] < plan.thermal_heat_w[0, 3]
    assert slot_power_w(unit, repaired.thermal_heat_w[0]).max() <= limit_w + 1e-6
    # The cut is the largest heat that fits: a little more breaks the limit.
    more = repaired.thermal_heat_w[0].copy()
    more[3] = min(more[3] * 1.001, unit.heat_limit_w[3])
    assert slot_power_w(unit, more)[3] > limit_w + 1e-6
    # Heat that is allowed stays as it is.
    again = repair_plan(problem, repaired)
    np.testing.assert_array_equal(again.thermal_heat_w, repaired.thermal_heat_w)


def test_repair_cuts_heat_to_the_range_of_the_device() -> None:
    """Negative heat becomes 0, a blocked slot gets no heat, dust is no run."""
    blocked = np.zeros(SLOTS, dtype=bool)
    blocked[4] = True
    unit = device("direct", blocked=blocked)
    plan = Plan.idle(house(unit))
    plan.thermal_heat_w[0, 2] = -10.0
    plan.thermal_heat_w[0, 4] = 1000.0
    plan.thermal_heat_w[0, 6] = 1e-9
    plan.thermal_heat_w[0, 8] = 0.3 * unit.heat_limit_w[8]
    repaired = thm.clip_heat_plan(unit, plan.thermal_heat_w[0])
    assert repaired[2] == repaired[4] == repaired[6] == 0.0
    assert repaired[8] == pytest.approx(0.3 * unit.heat_limit_w[8])


def test_started_marks_the_switch_on_slots() -> None:
    """``started`` is derived from the run share: 1 where it leaves 0, also across a partial start."""
    unit = device("direct")
    problem = house(unit)
    plan = Plan.idle(problem)
    top = unit.heat_limit_w
    plan.thermal_heat_w[0, 2:4] = 0.5 * top[2:4]
    plan.thermal_heat_w[0, 6] = top[6]
    result = simulate(problem, plan)
    assert result.thermal is not None
    assert np.flatnonzero(result.thermal.started[0]).tolist() == [2, 6]
    running = device("direct", initial_running=True)
    plan = Plan.idle(house(running))
    plan.thermal_heat_w[0, 0] = running.heat_limit_w[0]
    result = simulate(house(running), plan)
    assert result.thermal is not None
    assert not result.thermal.started[0].any()
