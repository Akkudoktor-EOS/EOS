"""Tests for the configurable size limit of the GENETIC fitness cache."""

import json
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import patch

import numpy as np
import pytest
from deap import creator
from pydantic import ValidationError

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.cache import CacheEnergyManagementStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticsettings import GeneticCommonSettings
from akkudoktoreos.utils.datetimeutil import to_datetime

DIR_TESTDATA = Path(__file__).parent / "testdata" / "genetic"


def _configure_hourly_grid(config_eos: ConfigEOS) -> None:
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {
                "genetic": {"tail_horizon_hours": 0, "horizon_hours": 48, "interval_sec": 3600}
            },
        }
    )
    get_ems(init=True).set_start_datetime(to_datetime().set(hour=0, minute=0))


def test_fitness_cache_max_entries_setting_defaults_to_unbounded():
    assert GeneticCommonSettings().fitness_cache_max_entries is None
    assert GeneticCommonSettings(fitness_cache_max_entries=0).fitness_cache_max_entries == 0
    assert GeneticCommonSettings(fitness_cache_max_entries=5000).fitness_cache_max_entries == 5000
    with pytest.raises(ValidationError):
        GeneticCommonSettings(fitness_cache_max_entries=-1)


@pytest.mark.parametrize("max_entries", [1, 2, 3, 10])
def test_bounded_fitness_cache_evicts_oldest_keys(config_eos: ConfigEOS, max_entries: int):
    _configure_hourly_grid(config_eos)
    opt = GeneticOptimization(fixed_seed=42)
    opt.optimize_ev = False
    opt.setup_deap_environment({"home_appliance": 0}, start_hour=0)
    parameters = SimpleNamespace(ems=SimpleNamespace(price_per_wh_battery=0.0), ev=None)
    result = {
        "Gesamtbilanz_Euro": 1.0,
        "Gesamt_Verluste": 0.0,
        "EAuto_SoC_pro_Stunde": np.zeros(opt.control_slots),
    }
    opt._fitness_cache = OrderedDict()
    opt._fitness_cache_max_entries = max_entries
    opt._fitness_cache_enabled = True
    individuals = [
        creator.Individual([value % 2] + [value // 2] + [0] * (opt.control_slots - 2))
        for value in range(20)
    ]

    with patch.object(opt, "evaluate_inner", return_value=result):
        for individual in individuals:
            opt.evaluate(individual, parameters, 0, False)  # type: ignore[arg-type]
            assert len(opt._fitness_cache) <= max_entries

    # The most recent evaluation is still cached; the first one has been evicted.
    newest_key = opt._fitness_key(individuals[-1])
    oldest_key = opt._fitness_key(individuals[0])
    assert newest_key in opt._fitness_cache
    assert oldest_key not in opt._fitness_cache
    assert opt._fitness_cache_misses == len(individuals)
    assert opt._fitness_cache_hits == 0


def test_evict_fitness_cache_also_bounds_a_plain_dict(config_eos: ConfigEOS):
    opt = GeneticOptimization(fixed_seed=42)
    opt._fitness_cache = {(bytes([value]),): None for value in range(10)}  # type: ignore[misc]
    opt._evict_fitness_cache(3)
    assert list(opt._fitness_cache) == [(bytes([value]),) for value in (7, 8, 9)]


def _run_optimization(
    config_eos: ConfigEOS, max_entries: Optional[int]
) -> tuple[dict[str, Any], dict[str, Any], int, int]:
    """Run a short, seeded GENETIC optimization with EV and return its result."""
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {
                "algorithm": "GENETIC",
                "genetic": {
                    "horizon_hours": 38,
                    "tail_horizon_hours": 0,
                    "terminal_value_mode": "FIXED",
                    "individuals": 40,
                    "generations": 10,
                    "fitness_cache_max_entries": max_entries,
                    "penalties": {"ev_soc_miss": 10, "ac_charge_break_even": 1},
                },
            },
            "devices": {
                "max_electric_vehicles": 1,
                "electric_vehicles": {
                    "ev1": {"charge_rates": [0.0, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0]}
                },
            },
        }
    )
    assert config_eos.optimization.genetic.fitness_cache_max_entries == max_entries
    with (DIR_TESTDATA / "optimize_input_2.json").open("r") as f_in:
        parameters = GeneticOptimizationParameters(**json.load(f_in))
    get_ems(init=True).set_start_datetime(
        to_datetime("2026-09-16T10:00:00+02:00", in_timezone="Europe/Berlin")
    )
    CacheEnergyManagementStore().clear()

    opt = GeneticOptimization(fixed_seed=42)
    max_cache_size = 0
    uncached = opt._evaluate_uncached

    def spy(*args: Any, **kwargs: Any) -> tuple[float]:
        nonlocal max_cache_size
        max_cache_size = max(max_cache_size, len(opt._fitness_cache))
        return uncached(*args, **kwargs)

    with patch.object(opt, "_evaluate_uncached", side_effect=spy):
        solution = opt.optimize_ems(parameters=parameters, start_hour=10, ngen=12)

    dump = solution.model_dump(mode="json")
    history = dump.pop("fitness_history")
    cache_stats = history.pop("fitness_cache")
    max_cache_size = max(max_cache_size, cache_stats["keys"])
    return dump, history, cache_stats["hits"], max_cache_size


def test_fitness_cache_limit_does_not_change_the_result(config_eos: ConfigEOS):
    reference, reference_history, reference_hits, unbounded_size = _run_optimization(
        config_eos, None
    )
    assert reference_hits > 0
    assert unbounded_size > 20

    for max_entries in (0, 1, 7, 20):
        result, history, hits, max_size = _run_optimization(config_eos, max_entries)
        assert result == reference, f"fitness_cache_max_entries={max_entries}"
        assert history == reference_history, f"fitness_cache_max_entries={max_entries}"
        assert max_size <= max_entries
        if max_entries == 0:
            assert hits == 0
