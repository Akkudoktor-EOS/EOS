from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from deap import creator

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.coreabc import get_ems
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

    with patch.object(opt, "evaluate_inner", side_effect=[first_result, repaired_result]) as evaluate:
        fitness = opt.evaluate(individual, parameters, start_hour=0, worst_case=False)  # type: ignore[arg-type]

    assert evaluate.call_count == 2
    assert fitness == pytest.approx((1.0,))
    assert individual[opt.total_slots :] == [0] * opt.total_slots


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
    assert len(guesses) >= 4
    assert all(len(guess) == slots for guess in guesses)
    assert any(guess[0] == dc_allowed_state for guess in guesses)
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
