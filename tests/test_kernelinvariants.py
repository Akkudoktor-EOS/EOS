"""Invariants of the optimizer kernel with several batteries and vehicles.

The kernel must close the energy balance of every slot, treat a battery of capacity 0 as
absent, keep to the discharge ceiling of the plan, not charge from PV where the plan forbids
it, report exceeded grid limits exactly, and give the same results for a batch and for a
partial run as for the single run.
"""

from typing import Optional

import numpy as np
import pytest

from akkudoktoreos.devices.models.appliance import ApplianceParams
from akkudoktoreos.devices.models.inverter import InverterParams
from akkudoktoreos.devices.models.storage import StorageParams
from akkudoktoreos.optimization.kernel import (
    GridConnection,
    Plan,
    PlanningProblem,
    initial_state,
    simulate,
    simulate_batch,
)

SLOTS = 96
SLOT_H = 0.25


def forecasts(seed: int = 3, slots: int = SLOTS) -> dict[str, np.ndarray]:
    """Return PV, load and price series of one synthetic day."""
    rng = np.random.default_rng(seed)
    hours = np.arange(slots) * SLOT_H
    pv = np.clip(np.sin((hours - 6.0) / 12.0 * np.pi), 0.0, None) * 4500.0 * SLOT_H
    load = (350.0 + 250.0 * rng.random(slots) + 500.0 * (np.abs(hours - 19.0) < 2)) * SLOT_H
    price = (0.0002 + 0.0002 * rng.random(slots) + 0.0002 * (np.abs(hours - 19.0) < 2)) * 1.0
    tariff = np.full(slots, 8e-5)
    tariff[(hours > 11) & (hours < 14)] = -4e-5
    return {"pv_wh": pv, "load_wh": load, "price": price, "tariff": tariff}


def battery(capacity_wh: float = 8000.0, soc: float = 50.0, **update: object) -> StorageParams:
    base = {
        "capacity_wh": capacity_wh,
        "initial_soc_pct": soc,
        "min_soc_pct": 10.0,
        "max_soc_pct": 95.0,
        "charging_efficiency": 0.95,
        "discharging_efficiency": 0.95,
        "max_charge_power_w": 4000.0,
        "max_discharge_power_w": 4000.0,
        "charge_rates": (0.0, 0.25, 0.5, 0.75, 1.0),
    }
    base.update(update)
    return StorageParams(**base)  # type: ignore[arg-type]


def vehicle(soc: float = 20.0, **update: object) -> StorageParams:
    base = {
        "capacity_wh": 50000.0,
        "initial_soc_pct": soc,
        "charging_efficiency": 0.9,
        "discharging_efficiency": 1.0,
        "max_charge_power_w": 7000.0,
        "max_discharge_power_w": 0.0,
        "charge_rates": (0.0, 0.5, 1.0),
        "is_vehicle": True,
    }
    base.update(update)
    return StorageParams(**base)  # type: ignore[arg-type]


def make_problem(
    batteries: tuple[StorageParams, ...],
    vehicles: tuple[StorageParams, ...] = (),
    inverters: Optional[tuple[InverterParams, ...]] = None,
    appliances: tuple[ApplianceParams, ...] = (),
    direct_marketing: bool = True,
    grid: Optional[GridConnection] = None,
    first_slot_fraction: float = 1.0,
    seed: int = 3,
) -> PlanningProblem:
    series = forecasts(seed)
    if inverters is None:
        inverters = tuple(
            InverterParams(
                max_power_w=5000.0,
                battery_index=index,
                dc_to_ac_efficiency=0.96,
                ac_to_dc_efficiency=0.95,
                max_ac_charge_power_w=3500.0,
            )
            for index in range(len(batteries))
        )
    return PlanningProblem(
        slot_duration_h=SLOT_H,
        pv_wh=series["pv_wh"],
        load_wh=series["load_wh"],
        import_price_per_wh=series["price"],
        feed_in_tariff_per_wh=series["tariff"],
        batteries=batteries,
        vehicles=vehicles,
        inverters=inverters,
        appliances=appliances,
        appliance_cycles=tuple(2 for _ in appliances),
        grid=grid or GridConnection(),
        first_slot_fraction=first_slot_fraction,
        direct_marketing=direct_marketing,
    )


def random_plan(problem: PlanningProblem, seed: int) -> Plan:
    """Return a plan with random controls from the usual level sets."""
    rng = np.random.default_rng(seed)
    plan = Plan.idle(problem)
    shape_b = (len(problem.batteries), problem.slots)
    plan.battery_grid_charge = rng.choice([0.0, 0.0, 0.25, 0.5, 1.0], size=shape_b)
    plan.battery_discharge_limit = rng.choice([0.0, 0.5, 1.0, 1.0], size=shape_b)
    plan.battery_pv_charge = rng.choice([0.0, 1.0, 1.0], size=shape_b)
    plan.battery_export = rng.choice([0.0, 0.0, 0.5, 1.0], size=shape_b)
    plan.vehicle_charge = rng.choice(
        [0.0, 0.0, 0.5, 1.0], size=(len(problem.vehicles), problem.slots)
    )
    for index in range(len(problem.appliances)):
        plan.appliance_starts[index] = rng.integers(0, problem.slots - 8, size=2)
    return plan


APPLIANCES = (
    ApplianceParams(run_energy_wh=(500.0, 500.0, 300.0, 300.0)),
    ApplianceParams(run_energy_wh=(900.0, 400.0), pinned_load_wh=(250.0, 250.0, 100.0)),
)


@pytest.fixture
def problem() -> PlanningProblem:
    """Two batteries, two vehicles, two appliances, direct marketing."""
    return make_problem(
        (battery(8000.0, 50.0), battery(12000.0, 70.0, max_discharge_power_w=3000.0)),
        (vehicle(20.0), vehicle(60.0, available=tuple(i < 60 for i in range(SLOTS)))),
        appliances=APPLIANCES,
        first_slot_fraction=0.6,
    )


@pytest.mark.parametrize("seed", range(8))
def test_energy_balance_is_closed_every_slot(problem: PlanningProblem, seed: int) -> None:
    result = simulate(problem, random_plan(problem, seed))
    assert np.max(np.abs(result.energy_balance_residual_wh)) <= 1e-9
    assert np.all(result.grid_import_wh >= 0.0)
    assert np.all(result.grid_export_wh >= 0.0)
    assert np.all(result.losses_wh >= -1e-12)
    # The stored energy of the vehicles is not lost: SoC rises with the stored charge.
    batteries = len(problem.batteries)
    for index, vehicle_params in enumerate(problem.vehicles):
        stored = result.charge_stored_wh[batteries + index].sum()
        gained = (
            result.final_soc_wh[batteries + index]
            - vehicle_params.initial_soc_pct / 100 * vehicle_params.capacity_wh
        )
        assert gained == pytest.approx(stored, abs=1e-9)


def test_battery_without_capacity_is_absent(problem: PlanningProblem) -> None:
    single = make_problem((battery(8000.0, 50.0),), (vehicle(),), appliances=APPLIANCES)
    with_empty = make_problem(
        (battery(8000.0, 50.0), battery(0.0, 0.0)),
        (vehicle(),),
        appliances=APPLIANCES,
        inverters=(
            InverterParams(5000.0, 0, 0.96, 0.95, max_ac_charge_power_w=3500.0),
            # An empty battery with a live inverter would add AC capacity, so its inverter
            # has none either.
            InverterParams(0.0, 1, 0.96, 0.95, max_ac_charge_power_w=3500.0),
        ),
    )
    plan_single = random_plan(single, 5)
    plan_two = random_plan(with_empty, 6)
    for name in (
        "battery_grid_charge",
        "battery_discharge_limit",
        "battery_pv_charge",
        "battery_export",
    ):
        getattr(plan_two, name)[0] = getattr(plan_single, name)[0]
    plan_two.vehicle_charge = plan_single.vehicle_charge
    plan_two.appliance_starts = plan_single.appliance_starts
    one = simulate(single, plan_single)
    two = simulate(with_empty, plan_two)
    for name in (
        "load_wh",
        "grid_import_wh",
        "grid_export_wh",
        "cost_eur",
        "revenue_eur",
        "losses_wh",
    ):
        assert np.max(np.abs(getattr(one, name) - getattr(two, name))) <= 1e-9, name
    assert two.total_balance_eur == pytest.approx(one.total_balance_eur, abs=1e-9)
    assert np.max(np.abs(two.soc_pct[0] - one.soc_pct[0])) <= 1e-9
    assert np.all(two.soc_pct[1] == 0.0)
    assert np.all(two.charge_raw_wh[1] == 0.0)


@pytest.mark.parametrize("seed", range(6))
def test_swapping_identical_batteries_keeps_the_sums(seed: int) -> None:
    """Identical batteries are interchangeable where no priority between them matters.

    Batteries are served in their configured order, so the order matters whenever a limit
    binds for one of them, and PV charging takes charge power before grid charging in the same
    slot. With large batteries, the same discharge ceiling and PV permission for both, no
    export and grid charging only at night, only the assignment of the grid charge rows
    differs and the sums must not change.
    """
    twin = make_problem(
        (battery(60000.0, 50.0), battery(60000.0, 50.0)),
        (vehicle(),),
        appliances=APPLIANCES,
        direct_marketing=False,
    )
    plan = random_plan(twin, seed)
    plan.battery_discharge_limit[1] = plan.battery_discharge_limit[0]
    plan.battery_pv_charge[1] = plan.battery_pv_charge[0]
    plan.battery_export[:] = 0.0
    plan.battery_grid_charge[:, twin.pv_wh > 0.0] = 0.0
    swapped = Plan(
        battery_grid_charge=plan.battery_grid_charge[::-1].copy(),
        battery_discharge_limit=plan.battery_discharge_limit[::-1].copy(),
        battery_pv_charge=plan.battery_pv_charge[::-1].copy(),
        battery_export=plan.battery_export[::-1].copy(),
        vehicle_charge=plan.vehicle_charge,
        appliance_starts=plan.appliance_starts,
    )
    first = simulate(twin, plan)
    second = simulate(twin, swapped)
    assert second.total_balance_eur == pytest.approx(first.total_balance_eur, abs=1e-9)
    assert second.total_losses_wh == pytest.approx(first.total_losses_wh, abs=1e-9)
    assert np.max(np.abs(first.grid_import_wh - second.grid_import_wh)) <= 1e-9
    assert np.max(np.abs(first.grid_export_wh - second.grid_export_wh)) <= 1e-9
    assert second.soc_wh[:2].sum(axis=0) == pytest.approx(first.soc_wh[:2].sum(axis=0), abs=1e-9)


def test_battery_order_sets_the_priority() -> None:
    """With different ceilings the first battery serves first, so the order is not neutral."""
    twin = make_problem((battery(60000.0, 50.0), battery(60000.0, 50.0)), direct_marketing=False)
    object.__setattr__(twin, "load_wh", np.full(SLOTS, 5000.0 * SLOT_H))
    object.__setattr__(twin, "pv_wh", np.zeros(SLOTS))
    object.__setattr__(twin, "_kernel_cache", None)
    plan = Plan.idle(twin)
    plan.battery_discharge_limit[0] = 0.5
    plan.battery_discharge_limit[1] = 1.0
    result = simulate(twin, plan)
    assert result.discharge_raw_wh[0].sum() > 0.0
    reverse = Plan(
        battery_grid_charge=plan.battery_grid_charge,
        battery_discharge_limit=plan.battery_discharge_limit[::-1].copy(),
        battery_pv_charge=plan.battery_pv_charge,
        battery_export=plan.battery_export,
        vehicle_charge=plan.vehicle_charge,
        appliance_starts=plan.appliance_starts,
    )
    other = simulate(twin, reverse)
    # The first battery serves first, up to its ceiling; the second takes the rest.
    assert result.discharge_raw_wh[0, 0] == pytest.approx(0.5 * 4000.0 * SLOT_H)
    assert other.discharge_raw_wh[0, 0] > 0.5 * 4000.0 * SLOT_H + 100.0
    assert result.discharge_raw_wh[1, 0] > other.discharge_raw_wh[1, 0]
    # Either way the load of the first slots is covered completely.
    assert result.grid_import_wh[0] == other.grid_import_wh[0] == 0.0


@pytest.mark.parametrize("limit", [0.25, 0.5, 0.75])
def test_discharge_ceiling_bounds_power_and_load(limit: float) -> None:
    big_load = make_problem((battery(30000.0, 90.0),), direct_marketing=False)
    object.__setattr__(big_load, "load_wh", np.full(SLOTS, 6000.0 * SLOT_H))
    object.__setattr__(big_load, "pv_wh", np.zeros(SLOTS))
    object.__setattr__(big_load, "_kernel_cache", None)
    plan = Plan.idle(big_load)
    plan.battery_discharge_limit[:] = limit
    result = simulate(big_load, plan)
    max_raw_wh = limit * 4000.0 * SLOT_H
    assert np.max(result.discharge_raw_wh[0]) <= max_raw_wh + 1e-9
    assert np.max(result.discharge_raw_wh[0]) == pytest.approx(max_raw_wh)
    assert np.all(result.battery_discharge_ac_wh[0] <= result.load_wh)

    # A small load is covered, never exceeded.
    small = make_problem((battery(30000.0, 90.0),), direct_marketing=False)
    object.__setattr__(small, "load_wh", np.full(SLOTS, 300.0 * SLOT_H))
    object.__setattr__(small, "pv_wh", np.zeros(SLOTS))
    object.__setattr__(small, "_kernel_cache", None)
    plan = Plan.idle(small)
    plan.battery_discharge_limit[:] = limit
    result = simulate(small, plan)
    assert result.battery_discharge_ac_wh[0] == pytest.approx(result.load_wh, abs=1e-9)
    assert np.all(result.grid_import_wh <= 1e-9)

    # Ceiling 0 forbids discharging: everything is imported.
    plan.battery_discharge_limit[:] = 0.0
    result = simulate(small, plan)
    assert np.all(result.battery_discharge_ac_wh == 0.0)
    assert result.grid_import_wh == pytest.approx(result.load_wh)


def test_no_pv_charging_where_the_plan_forbids_it() -> None:
    sunny = make_problem((battery(10000.0, 20.0), battery(10000.0, 20.0)), direct_marketing=False)
    plan = Plan.idle(sunny)
    plan.battery_pv_charge[0] = 0.0
    plan.battery_pv_charge[1] = 1.0
    result = simulate(sunny, plan)
    assert np.all(result.battery_pv_charge_wh[0] == 0.0)
    assert result.battery_pv_charge_wh[1].sum() > 1000.0
    assert np.all(np.diff(result.soc_wh[0]) <= 1e-9)

    plan.battery_pv_charge[:] = 0.0
    result = simulate(sunny, plan)
    assert result.battery_pv_charge_wh.sum() == 0.0
    # The surplus is fed in or curtailed instead.
    assert result.grid_export_wh.sum() > 0.0


def test_import_limit_only_reports_the_excess() -> None:
    free = make_problem((battery(), battery()), (vehicle(),), appliances=APPLIANCES)
    limited = make_problem(
        (battery(), battery()),
        (vehicle(),),
        appliances=APPLIANCES,
        grid=GridConnection(import_limit_w=2500.0),
    )
    plan = random_plan(free, 11)
    plan.vehicle_charge = plan.vehicle_charge[:1]
    base = simulate(free, plan)
    result = simulate(limited, plan)
    # The import limit does not change the flows, only what is reported.
    assert np.max(np.abs(result.grid_import_wh - base.grid_import_wh)) == 0.0
    assert np.max(np.abs(result.grid_export_wh - base.grid_export_wh)) == 0.0
    assert result.total_balance_eur == base.total_balance_eur
    assert np.all(base.import_excess_wh == 0.0)
    expected = np.maximum(result.grid_import_wh - 2500.0 * SLOT_H, 0.0)
    np.testing.assert_allclose(result.import_excess_wh, expected, atol=1e-12, rtol=0)
    assert result.import_excess_wh.sum() > 0.0
    assert result.total_import_excess_wh == pytest.approx(expected.sum())


def test_export_limit_curtails_the_feed_in() -> None:
    free = make_problem((battery(), battery()), (vehicle(),), appliances=APPLIANCES)
    limited = make_problem(
        (battery(), battery()),
        (vehicle(),),
        appliances=APPLIANCES,
        grid=GridConnection(export_limit_w=1800.0),
    )
    plan = random_plan(free, 11)
    plan.vehicle_charge = plan.vehicle_charge[:1]
    base = simulate(free, plan)
    result = simulate(limited, plan)
    cap = 1800.0 * SLOT_H
    assert np.all(base.export_curtailed_wh == 0.0)
    assert np.all(result.grid_export_wh <= cap + 1e-12)
    first = int(np.argmax(base.grid_export_wh > cap))
    # The battery does not discharge for energy the limit would curtail: only PV is curtailed.
    assert result.discharge_raw_wh[:, first].sum() < base.discharge_raw_wh[:, first].sum()
    assert np.all(result.export_curtailed_wh <= limited.pv_wh + 1e-9)
    assert result.export_curtailed_wh.sum() > 0.0
    assert np.max(np.abs(result.energy_balance_residual_wh)) <= 1e-9
    assert result.total_export_curtailed_wh == pytest.approx(result.export_curtailed_wh.sum())
    # A limit that is never reached changes nothing.
    loose = make_problem(
        (battery(), battery()),
        (vehicle(),),
        appliances=APPLIANCES,
        grid=GridConnection(export_limit_w=1e9),
    )
    same = simulate(loose, plan)
    assert same.total_balance_eur == base.total_balance_eur
    assert np.all(same.export_curtailed_wh == 0.0)


def test_the_export_limit_caps_the_battery_discharge_before_it_happens() -> None:
    """A battery asked for 1 kW against a 400 W limit delivers 100 Wh, not 250 Wh with 150 Wh lost."""
    slots = 4
    lossless = battery(
        10000.0,
        50.0,
        charging_efficiency=1.0,
        discharging_efficiency=1.0,
        max_discharge_power_w=1000.0,
    )
    inverter = InverterParams(
        max_power_w=5000.0, battery_index=0, dc_to_ac_efficiency=1.0, ac_to_dc_efficiency=1.0
    )
    problem = PlanningProblem(
        slot_duration_h=SLOT_H,
        pv_wh=np.zeros(slots),
        load_wh=np.zeros(slots),
        import_price_per_wh=np.full(slots, 3e-4),
        feed_in_tariff_per_wh=np.full(slots, 8e-5),
        batteries=(lossless,),
        inverters=(inverter,),
        grid=GridConnection(export_limit_w=400.0),
        direct_marketing=True,
    )
    plan = Plan.idle(problem)
    plan.battery_export[:] = 1.0
    result = simulate(problem, plan)
    assert result.grid_export_wh[0] == pytest.approx(100.0)
    assert result.discharge_raw_wh[0, 0] == pytest.approx(100.0)
    assert result.losses_wh[0] == pytest.approx(0.0, abs=1e-9)
    assert result.export_curtailed_wh[0] == pytest.approx(0.0, abs=1e-9)
    assert np.max(np.abs(result.energy_balance_residual_wh)) <= 1e-9


def test_first_slot_fraction_scales_the_power_limits_of_slot_zero_only() -> None:
    full = make_problem((battery(),), direct_marketing=False)
    partial = make_problem((battery(),), direct_marketing=False, first_slot_fraction=0.5)
    for item in (full, partial):
        # The forecast of slot 0 is already reduced to the part of the slot still ahead.
        load = np.full(SLOTS, 3000.0 * SLOT_H)
        load[0] *= item.first_slot_fraction
        object.__setattr__(item, "load_wh", load)
        object.__setattr__(item, "pv_wh", np.zeros(SLOTS))
        object.__setattr__(item, "_kernel_cache", None)
    one = simulate(full, Plan.idle(full))
    two = simulate(partial, Plan.idle(partial))
    assert two.discharge_raw_wh[0, 0] == pytest.approx(one.discharge_raw_wh[0, 0] * 0.5)
    assert two.discharge_raw_wh[0, 1] == pytest.approx(one.discharge_raw_wh[0, 1])


def test_batteries_charge_from_pv_in_their_order() -> None:
    both = make_problem((battery(3000.0, 10.0), battery(3000.0, 10.0)), direct_marketing=False)
    result = simulate(both, Plan.idle(both))
    first_full = np.argmax(result.soc_pct[0] > 94.0)
    assert result.soc_pct[0, first_full] > 94.0
    # The second battery only receives what the first could not take.
    assert result.battery_pv_charge_wh[1, : first_full - 1].sum() == pytest.approx(0.0, abs=1e-9)


def test_vehicle_charges_only_while_available() -> None:
    present = make_problem(
        (battery(),),
        (vehicle(10.0, available=tuple(20 <= i < 40 for i in range(SLOTS))),),
    )
    plan = Plan.idle(present)
    plan.vehicle_charge[:] = 1.0
    result = simulate(present, plan)
    charging = result.charge_raw_wh[1] > 0.0
    assert charging[20:40].all()
    assert not charging[:20].any()
    assert not charging[40:].any()


@pytest.mark.parametrize("seed", range(5))
def test_batch_matches_single_runs(problem: PlanningProblem, seed: int) -> None:
    plans = [random_plan(problem, seed * 10 + offset) for offset in range(6)]
    batch = simulate_batch(problem, plans)
    for index, plan in enumerate(plans):
        single = simulate(problem, plan)
        assert batch.total_balance_eur[index] == pytest.approx(single.total_balance_eur, abs=1e-12)
        assert batch.total_cost_eur[index] == pytest.approx(single.total_cost_eur, abs=1e-12)
        assert batch.total_revenue_eur[index] == pytest.approx(single.total_revenue_eur, abs=1e-12)
        assert batch.total_losses_wh[index] == pytest.approx(single.total_losses_wh, abs=1e-12)
    empty = simulate_batch(problem, [])
    assert len(empty.total_balance_eur) == 0


@pytest.mark.parametrize("start_slot", [1, 17, 48, 95])
def test_partial_run_matches_the_tail_of_the_full_run(
    problem: PlanningProblem, start_slot: int
) -> None:
    plan = random_plan(problem, 21)
    full = simulate(problem, plan)
    partial = simulate(problem, plan, start_slot, full.soc_wh[:, start_slot])
    assert partial.start_slot == start_slot
    for name in (
        "load_wh",
        "grid_import_wh",
        "grid_export_wh",
        "cost_eur",
        "revenue_eur",
        "losses_wh",
        "appliance_wh",
        "import_excess_wh",
        "soc_pct",
        "vehicle_soc_pct",
        "soc_wh",
        "battery_power_w",
        "vehicle_power_w",
    ):
        whole = getattr(full, name)
        assert np.max(np.abs(whole[..., start_slot:] - getattr(partial, name))) <= 1e-12, name
    assert np.max(np.abs(partial.final_soc_wh - full.final_soc_wh)) <= 1e-12
    assert partial.total_balance_eur == pytest.approx(
        float(full.cost_eur[start_slot:].sum() - full.revenue_eur[start_slot:].sum()), abs=1e-12
    )
    assert partial.total_losses_wh == pytest.approx(
        float(full.losses_wh[start_slot:].sum()), abs=1e-9
    )


def test_partial_run_needs_a_state(problem: PlanningProblem) -> None:
    with pytest.raises(ValueError, match="initial_soc_wh"):
        simulate(problem, Plan.idle(problem), 5)
    with pytest.raises(ValueError, match="outside"):
        simulate(problem, Plan.idle(problem), SLOTS + 1, initial_state(problem))
