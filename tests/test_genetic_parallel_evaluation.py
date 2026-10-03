"""Parallel fitness evaluation gives the same result as evaluating in one process."""

import pytest

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.cache import CacheEnergyManagementStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.optimization.genetic import genetic as genetic_module
from akkudoktoreos.optimization.genetic.genetic import (
    GeneticOptimization,
    auto_evaluation_workers,
)
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.utils.datetimeutil import to_datetime

ems_eos = get_ems(init=True)  # init once

HOURS = 24


@pytest.mark.parametrize(
    "cores,cpu_limit,expected",
    [
        (1, None, 1),
        (2, None, 1),  # one core stays free
        (3, None, 2),
        (4, None, 2),
        (16, None, 2),  # at most two
        (4, 1, 1),  # container CPU limit
        (4, 2, 2),
        (2, 4, 1),
    ],
)
def test_auto_workers_keep_one_core_free(cores, cpu_limit, expected):
    assert auto_evaluation_workers(cores=cores, cpu_limit=cpu_limit) == expected


def _parameters(
    config_eos: ConfigEOS, workers: int, cache_entries
) -> GeneticOptimizationParameters:
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": HOURS},
            "optimization": {
                "genetic": {
                    "individuals": 30,
                    "generations": 10,
                    "tail_horizon_hours": 0,
                    "horizon_hours": HOURS,
                    "interval_sec": 3600,
                    "workers": workers,
                    "fitness_cache_max_entries": cache_entries,
                }
            },
            "feedintariff": {"direct_marketing_enabled": True},
            "devices": {
                "max_batteries": 1,
                "batteries": {
                    "battery1": {"device_id": "battery1", "grid_export_rates": [0.5, 1.0]}
                },
            },
        }
    )
    ems_eos.set_start_datetime(to_datetime().set(hour=0, minute=0))
    CacheEnergyManagementStore().clear()
    return GeneticOptimizationParameters.model_validate(
        dict(
            ems={
                "pv_prognose_wh": [0.0] * 8 + [3000.0] * 8 + [0.0] * 8,
                "strompreis_euro_pro_wh": [0.0003] * 6 + [0.0001] * 6 + [0.0004] * 12,
                "einspeiseverguetung_euro_pro_wh": [0.0001] * 12 + [0.0009] * 12,
                "preis_euro_pro_wh_akku": 0.0,
                "gesamtlast": [400.0] * HOURS,
            },
            pv_battery={
                "device_id": "battery1",
                "capacity_wh": 10000,
                "initial_soc_percentage": 40,
                "min_soc_percentage": 5,
                "max_charge_power_w": 5000,
            },
            inverter={
                "device_id": "inverter1",
                "max_power_wh": 10000,
                "battery_id": "battery1",
            },
            ev=None,
        )
    )


def _run(config_eos: ConfigEOS, workers: int, cache_entries):
    parameters = _parameters(config_eos, workers, cache_entries)
    optimization = GeneticOptimization(fixed_seed=7)
    used: list[int] = []
    original_start = optimization._start_evaluation_pool

    def start_and_record():
        original_start()
        used.append(
            0 if optimization._evaluation_pool is None else optimization._evaluation_workers
        )

    optimization._start_evaluation_pool = start_and_record  # type: ignore[method-assign]
    solution = optimization.optimize_ems(parameters=parameters, start_hour=0, ngen=6)
    assert optimization._evaluation_pool is None  # stopped after the run
    return solution, used


@pytest.mark.parametrize("cache_entries", [None, 0])
def test_parallel_evaluation_matches_serial(config_eos: ConfigEOS, cache_entries):
    serial, serial_used = _run(config_eos, 1, cache_entries)
    parallel, parallel_used = _run(config_eos, 2, cache_entries)

    assert serial_used == [0]
    assert parallel_used == [2]
    assert parallel.start_solution == serial.start_solution
    assert parallel.result.total_balance == serial.result.total_balance
    assert parallel.fitness_history == serial.fitness_history
    assert parallel.ac_charge == serial.ac_charge
    assert parallel.dc_charge == serial.dc_charge
    assert parallel.discharge_allowed == serial.discharge_allowed
    assert parallel.battery_grid_export_factor == serial.battery_grid_export_factor


def test_failed_batch_falls_back_to_serial(config_eos: ConfigEOS, monkeypatch):
    serial, _ = _run(config_eos, 1, None)

    def broken_worker(genome):
        raise RuntimeError("worker lost")

    monkeypatch.setattr(genetic_module, "_evaluation_worker", broken_worker)
    fallback, used = _run(config_eos, 2, None)

    assert used == [2]
    assert fallback.start_solution == serial.start_solution
    assert fallback.ac_charge == serial.ac_charge


def test_cgroup_cpu_limit_reads_own_and_parent_cgroups(tmp_path, monkeypatch):
    from akkudoktoreos.optimization.genetic.genetic import _cgroup_cpu_limit

    service = tmp_path / "system.slice" / "eos.service"
    service.mkdir(parents=True)
    (tmp_path / "cpu.max").write_text("max 100000\n")
    (tmp_path / "system.slice" / "cpu.max").write_text("max 100000\n")
    (service / "cpu.max").write_text("150000 100000\n")

    real_open = open

    def fake_open(path, *args, **kwargs):
        if path == "/proc/self/cgroup":
            import io

            return io.StringIO("0::/system.slice/eos.service\n")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    assert _cgroup_cpu_limit(str(tmp_path)) == 2  # 1.5 cores -> 2
    (service / "cpu.max").write_text("max 100000\n")
    assert _cgroup_cpu_limit(str(tmp_path)) is None
    (tmp_path / "cpu.max").write_text("100000 100000\n")
    assert _cgroup_cpu_limit(str(tmp_path)) == 1  # a parent limit counts too
