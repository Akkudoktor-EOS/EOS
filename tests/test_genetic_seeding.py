from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from deap import creator, tools

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.core.emsettings import EnergyManagementMode
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.utils.datetimeutil import to_datetime


def _configure_hourly_grid(config_eos: ConfigEOS, *, start_hour: int = 0) -> None:
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {"horizon_hours": 48, "interval": 3600},
        }
    )
    get_ems(init=True).set_start_datetime(to_datetime().set(hour=start_hour, minute=0))


def test_energy_management_forwards_individuals_and_generations_separately(
    config_eos: ConfigEOS,
):
    _configure_hourly_grid(config_eos)
    config_eos.optimization.genetic.individuals = 100
    config_eos.optimization.genetic.generations = 80
    ems = get_ems(init=True)
    parameters = MagicMock()
    solution = MagicMock()
    optimizer = MagicMock()
    optimizer.optimierung_ems.return_value = solution

    with (
        patch("akkudoktoreos.adapter.adapterabc.AdapterContainer.update_data"),
        patch("akkudoktoreos.core.ems.GeneticOptimization", return_value=optimizer),
    ):
        ems._run(
            start_datetime=to_datetime().set(hour=0, minute=0),
            mode=EnergyManagementMode.OPTIMIZATION,
            genetic_parameters=parameters,
        )

    optimizer.optimierung_ems.assert_called_once_with(
        start_hour=0,
        parameters=parameters,
        ngen=80,
        individuals=100,
    )


def test_ev_repair_is_resimulated_before_fitness_assignment(config_eos: ConfigEOS):
    _configure_hourly_grid(config_eos)
    opt = GeneticOptimization(fixed_seed=42)
    opt.optimize_ev = True
    opt.ev_possible_charge_values = [0.0, 1.0]
    opt.setup_deap_environment({"home_appliance": 0}, start_hour=0)
    individual = creator.Individual([0] * opt.total_slots + [1] * opt.total_slots)

    first_result = {
        "Gesamtbilanz_Euro": 10.0,
        "Gesamt_Verluste": 0.0,
        "EAuto_SoC_pro_Stunde": np.full(opt.total_slots, 100.0),
    }
    repaired_result = {
        "Gesamtbilanz_Euro": 1.0,
        "Gesamt_Verluste": 0.0,
        "EAuto_SoC_pro_Stunde": np.full(opt.total_slots, 100.0),
    }
    parameters = SimpleNamespace(
        ems=SimpleNamespace(preis_euro_pro_wh_akku=0.0),
        eauto=None,
    )

    with patch.object(
        opt, "evaluate_inner", side_effect=[first_result, repaired_result]
    ) as evaluate:
        fitness = opt.evaluate(individual, parameters, start_hour=0, worst_case=False)  # type: ignore[arg-type]

    assert evaluate.call_count == 2
    assert fitness == pytest.approx((1.0,))
    assert individual[opt.total_slots :] == [0] * opt.total_slots


def test_fitness_cache_restores_canonical_ev_genome(config_eos: ConfigEOS):
    _configure_hourly_grid(config_eos)
    opt = GeneticOptimization(fixed_seed=42)
    opt.optimize_ev = True
    opt.ev_possible_charge_values = [0.0, 1.0]
    opt.setup_deap_environment({"home_appliance": 0}, start_hour=0)
    parameters = SimpleNamespace(
        ems=SimpleNamespace(preis_euro_pro_wh_akku=0.0),
        eauto=None,
    )
    result = {
        "Gesamtbilanz_Euro": 1.0,
        "Gesamt_Verluste": 0.0,
        "EAuto_SoC_pro_Stunde": np.full(opt.total_slots, 100.0),
    }
    first = creator.Individual([0] * opt.total_slots + [1] * opt.total_slots)
    duplicate = creator.Individual(first)
    opt._fitness_cache_enabled = True

    with patch.object(opt, "evaluate_inner", return_value=result) as evaluate:
        first_fitness = opt.evaluate(first, parameters, 0, False)  # type: ignore[arg-type]
        duplicate_fitness = opt.evaluate(duplicate, parameters, 0, False)  # type: ignore[arg-type]

    # The miss evaluates and then re-evaluates the repaired EV plan. The duplicate
    # is served directly from the original-key alias and receives the canonical genome.
    assert evaluate.call_count == 2
    assert first_fitness == duplicate_fitness
    assert duplicate == first
    assert duplicate[opt.total_slots :] == [0] * opt.total_slots
    assert duplicate.extra_data == first.extra_data
    assert opt._fitness_cache_hits == 1
    assert opt._fitness_cache_misses == 1


def test_fitness_cache_never_stores_failed_evaluations(config_eos: ConfigEOS):
    _configure_hourly_grid(config_eos)
    opt = GeneticOptimization(fixed_seed=42)
    opt.optimize_ev = False
    opt.setup_deap_environment({"home_appliance": 0}, start_hour=0)
    parameters = SimpleNamespace(
        ems=SimpleNamespace(preis_euro_pro_wh_akku=0.0),
        eauto=None,
    )
    first = creator.Individual([0] * opt.total_slots)
    duplicate = creator.Individual(first)
    opt._fitness_cache_enabled = True

    with patch.object(opt, "evaluate_inner", side_effect=RuntimeError("transient")) as evaluate:
        assert opt.evaluate(first, parameters, 0, False) == (100000.0,)  # type: ignore[arg-type]
        assert opt.evaluate(duplicate, parameters, 0, False) == (100000.0,)  # type: ignore[arg-type]

    assert evaluate.call_count == 2
    assert opt._fitness_cache_hits == 0
    assert opt._fitness_cache_misses == 2
    assert opt._fitness_cache == {}


def test_fitness_cache_ignores_elapsed_control_slots(config_eos: ConfigEOS):
    _configure_hourly_grid(config_eos, start_hour=10)
    opt = GeneticOptimization(fixed_seed=42)
    opt.optimize_ev = False
    opt.setup_deap_environment({"home_appliance": 0}, start_hour=10)
    parameters = SimpleNamespace(
        ems=SimpleNamespace(preis_euro_pro_wh_akku=0.0),
        eauto=None,
    )
    result = {
        "Gesamtbilanz_Euro": 1.0,
        "Gesamt_Verluste": 0.0,
        "EAuto_SoC_pro_Stunde": np.zeros(opt.total_slots),
    }
    first = creator.Individual([0] * opt.total_slots)
    elapsed_variant = creator.Individual(first)
    elapsed_variant[0] = 1
    opt._fitness_cache_enabled = True

    with patch.object(opt, "evaluate_inner", return_value=result) as evaluate:
        first_fitness = opt.evaluate(first, parameters, 10, False)  # type: ignore[arg-type]
        variant_fitness = opt.evaluate(elapsed_variant, parameters, 10, False)  # type: ignore[arg-type]

    assert evaluate.call_count == 1
    assert first_fitness == variant_fitness
    assert opt._fitness_cache_hits == 1


def test_mutated_warm_start_neighbors_keep_elapsed_slots(config_eos: ConfigEOS):
    _configure_hourly_grid(config_eos, start_hour=10)
    opt = GeneticOptimization(fixed_seed=42)
    opt.optimize_ev = False
    opt.setup_deap_environment({"home_appliance": 0}, start_hour=10)
    start_solution = [0] * opt.total_slots

    neighbors = opt._mutated_warm_start_neighbors(start_solution, count=5)

    assert len(neighbors) == 5
    assert len({tuple(neighbor) for neighbor in neighbors}) == 5
    assert all(neighbor[:10] == start_solution[:10] for neighbor in neighbors)
    assert all(neighbor != start_solution for neighbor in neighbors)


def test_initial_population_uses_fixed_seed_budget_and_configured_population(
    config_eos: ConfigEOS,
):
    _configure_hourly_grid(config_eos)
    config_eos.optimization.genetic.individuals = 300
    opt = GeneticOptimization(fixed_seed=42)
    opt.optimize_ev = False
    opt.setup_deap_environment({"home_appliance": 0}, start_hour=0)
    start_solution = [5] * opt.total_slots
    warm_neighbors = [[6] * opt.total_slots for _ in range(50)]
    educated = [[7] * opt.total_slots for _ in range(100)]
    captured: dict[str, object] = {}

    def fake_evolution(population, **kwargs):
        captured["population"] = list(population)
        captured["mu"] = kwargs["mu"]
        captured["lambda"] = kwargs["lambda_"]
        for individual in population:
            individual.fitness.values = (float(sum(individual)),)
            individual.extra_data = (0.0, 0.0, 0.0)
        kwargs["halloffame"].update(population)
        return population, SimpleNamespace(select=lambda _name: [])

    with (
        patch.object(opt, "_mutated_warm_start_neighbors", return_value=warm_neighbors),
        patch.object(opt, "_educated_guess_individuals", return_value=educated),
        patch.object(
            opt.toolbox,
            "population",
            side_effect=lambda n: [creator.Individual([9] * opt.total_slots) for _ in range(n)],
        ),
        patch.object(opt, "_evolve_population_adaptive", side_effect=fake_evolution),
    ):
        opt.optimize(start_solution=start_solution, ngen=1)

    population = captured["population"]
    first_genes = [individual[0] for individual in population]  # type: ignore[union-attr]
    assert len(population) == 300  # type: ignore[arg-type]
    assert first_genes.count(5) == 10
    assert first_genes.count(6) == 50
    assert first_genes.count(7) == 100
    assert first_genes.count(9) == 140
    assert captured["mu"] == 300
    assert captured["lambda"] == 300


def test_small_population_scales_warm_and_educated_seed_families(config_eos: ConfigEOS):
    _configure_hourly_grid(config_eos)
    config_eos.optimization.genetic.individuals = 100
    opt = GeneticOptimization(fixed_seed=42)
    opt.optimize_ev = False
    opt.setup_deap_environment({"home_appliance": 0}, start_hour=0)
    start_solution = [5] * opt.total_slots
    captured: dict[str, object] = {}

    def warm_neighbors(_solution, count):
        captured["warm_count"] = count
        return [[6] * opt.total_slots for _ in range(count)]

    def educated(count):
        captured["educated_count"] = count
        return [[7] * opt.total_slots for _ in range(count)]

    def fake_evolution(population, **kwargs):
        captured["population"] = list(population)
        captured["mu"] = kwargs["mu"]
        captured["lambda"] = kwargs["lambda_"]
        for individual in population:
            individual.fitness.values = (float(sum(individual)),)
            individual.extra_data = (0.0, 0.0, 0.0)
        kwargs["halloffame"].update(population)
        return population, SimpleNamespace(select=lambda _name: [])

    with (
        patch.object(opt, "_mutated_warm_start_neighbors", side_effect=warm_neighbors),
        patch.object(opt, "_educated_guess_individuals", side_effect=educated),
        patch.object(
            opt.toolbox,
            "population",
            side_effect=lambda n: [creator.Individual([9] * opt.total_slots) for _ in range(n)],
        ),
        patch.object(opt, "_evolve_population_adaptive", side_effect=fake_evolution),
    ):
        opt.optimize(start_solution=start_solution, ngen=1)

    population = captured["population"]
    first_genes = [individual[0] for individual in population]  # type: ignore[union-attr]
    assert len(population) == 100  # type: ignore[arg-type]
    assert first_genes.count(5) == 10
    assert first_genes.count(6) == 20
    assert first_genes.count(7) == 40
    assert first_genes.count(9) == 30
    assert captured["warm_count"] == 20
    assert captured["educated_count"] == 40
    assert captured["mu"] == 100
    assert captured["lambda"] == 100


def test_adaptive_evolution_soft_restarts_collapsed_population(config_eos: ConfigEOS):
    _configure_hourly_grid(config_eos)
    opt = GeneticOptimization(fixed_seed=42)
    opt.optimize_ev = False
    opt.setup_deap_environment({"home_appliance": 0}, start_hour=0)
    opt.toolbox.register("evaluate", lambda individual: (float(sum(individual)),))
    population = [creator.Individual([0] * opt.total_slots) for _ in range(20)]
    stats = tools.Statistics(lambda individual: individual.fitness.values)
    stats.register("min", np.min)
    stats.register("avg", np.mean)
    stats.register("max", np.max)
    halloffame = tools.HallOfFame(1)

    fresh = [creator.Individual([value] + [0] * (opt.total_slots - 1)) for value in range(1, 20)]
    with patch.object(opt, "_fresh_population", return_value=fresh) as create_fresh:
        evolved, log = opt._evolve_population_adaptive(
            population,
            mu=20,
            lambda_=20,
            ngen=1,
            stats=stats,
            halloffame=halloffame,
        )

    create_fresh.assert_called_once_with(19, educated_fraction=0.40)
    assert log.select("restart") == [0, 1]
    assert log.select("immigrants") == [0, 19]
    assert opt._adaptive_evolution_metrics["soft_restarts"] == 1
    assert opt._population_diversity(evolved) == pytest.approx(1.0)
    assert halloffame[0].fitness.values == (0.0,)


def test_local_search_moves_weak_export_to_later_expensive_import(config_eos: ConfigEOS):
    _configure_hourly_grid(config_eos)
    opt = GeneticOptimization(fixed_seed=42)
    opt.optimize_ev = False
    opt.optimize_dc_charge = True
    opt.optimize_battery_grid_export = True
    opt.bat_possible_charge_values = [1.0]
    opt.setup_deap_environment({"home_appliance": 0}, start_hour=0)

    slots = opt.total_slots
    export_state = 5
    self_consumption_state = 6
    discharge_state = 1
    source = 10
    targets = list(range(20, 32))
    base = [self_consumption_state] * slots
    base[source] = export_state
    for slot in targets:
        base[slot] = 0

    opt.simulation.elect_price_hourly = np.full(slots, 0.10)
    opt.simulation.elect_price_hourly[targets] = 0.30
    opt.simulation.elect_revenue_per_hour_arr = np.full(slots, 0.05)
    opt.simulation.elect_revenue_per_hour_arr[source] = 0.20
    opt.simulation.pv_prediction_wh = np.zeros(slots)
    opt.simulation.load_energy_array = np.full(slots, 100.0)

    def evaluate(individual):
        export_value = -0.20 if individual[source] == export_state else 0.0
        avoided_import = -0.05 * sum(individual[slot] == discharge_state for slot in targets)
        return (export_value + avoided_import,)

    opt.toolbox.register("evaluate", evaluate)
    incumbent = creator.Individual(base)
    incumbent.fitness.values = evaluate(incumbent)

    best, evaluations, improvements, initial, final = opt._locally_improve_grid_export(
        incumbent,
        max_evaluations=96,
    )

    assert evaluations > 0
    assert improvements == 1
    assert final < initial
    assert best[source] == self_consumption_state
    assert sum(best[slot] == discharge_state for slot in targets) >= 6


def test_educated_guesses_encode_high_price_direct_marketing(config_eos: ConfigEOS):
    _configure_hourly_grid(config_eos)
    opt = GeneticOptimization(fixed_seed=42)
    opt.optimize_ev = False
    opt.optimize_dc_charge = True
    opt.optimize_battery_grid_export = True
    opt.bat_possible_charge_values = [1.0]
    opt.setup_deap_environment({"home_appliance": 0}, start_hour=0)

    slots = opt.total_slots
    opt.simulation.elect_price_hourly = np.linspace(0.0001, 0.0004, slots)
    opt.simulation.elect_revenue_per_hour_arr = np.linspace(0.00001, 0.0003, slots)
    opt.simulation.pv_prediction_wh = np.full(slots, 1000.0)
    opt.simulation.load_energy_array = np.full(slots, 500.0)

    guesses = opt._educated_guess_individuals()

    dc_allowed_state = 4
    export_state = 5
    self_consumption_state = 6
    assert len(guesses) == opt.EDUCATED_GUESS_TARGET
    assert all(len(guess) == slots for guess in guesses)
    assert any(dc_allowed_state in guess or self_consumption_state in guess for guess in guesses)
    assert any(self_consumption_state in guess for guess in guesses)
    assert any(guess[-1] == export_state for guess in guesses)


def test_flat_feed_in_tariff_does_not_seed_direct_marketing(config_eos: ConfigEOS):
    _configure_hourly_grid(config_eos)
    opt = GeneticOptimization(fixed_seed=42)
    opt.optimize_ev = False
    opt.optimize_dc_charge = True
    opt.optimize_battery_grid_export = True
    opt.bat_possible_charge_values = [1.0]
    opt.setup_deap_environment({"home_appliance": 0}, start_hour=0)

    slots = opt.total_slots
    opt.simulation.elect_price_hourly = np.linspace(0.0001, 0.0004, slots)
    opt.simulation.elect_revenue_per_hour_arr = np.full(slots, 0.00005)
    opt.simulation.pv_prediction_wh = np.full(slots, 1000.0)
    opt.simulation.load_energy_array = np.full(slots, 500.0)

    guesses = opt._educated_guess_individuals()

    export_state = 5
    assert all(export_state not in guess for guess in guesses)
