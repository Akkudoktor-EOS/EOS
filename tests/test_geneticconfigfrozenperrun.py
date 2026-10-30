"""The genetic configuration is frozen for the duration of an optimization run.

A client (e.g. an energy manager that shrinks the control horizon as the known
price horizon moves on during the night) may update the configuration while a
run is in progress. Genomes and device arrays are sized when the run starts, so
a mid-run change of horizon, tail or prediction hours must not reach the final
evaluation - before the snapshot it failed with
``IndexError: index 84 is out of bounds for axis 0 with size 84``.
"""

import pytest

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.cache import CacheEnergyManagementStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.utils.datetimeutil import to_datetime

ems_eos = get_ems(init=True)  # init once

HOURS = 24


def _setup(config_eos: ConfigEOS, horizon_hours: int) -> GeneticOptimizationParameters:
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": HOURS},
            "optimization": {
                "genetic": {
                    "individuals": 20,
                    "generations": 10,
                    "tail_horizon_hours": 0,
                    "horizon_hours": horizon_hours,
                    "interval_sec": 3600,
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
                "pv_prognose_wh": [0.0] * HOURS,
                "strompreis_euro_pro_wh": [0.0003] * HOURS,
                "einspeiseverguetung_euro_pro_wh": [0.0001] * 12 + [0.0009] * 12,
                "preis_euro_pro_wh_akku": 0.0,
                "gesamtlast": [200.0] * HOURS,
            },
            pv_battery={
                "device_id": "battery1",
                "capacity_wh": 10000,
                "initial_soc_percentage": 100,
                "min_soc_percentage": 0,
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


@pytest.mark.parametrize("start_horizon,changed_horizon", [(20, 24), (24, 20)])
def test_horizon_change_during_run_does_not_reach_the_run(
    config_eos: ConfigEOS, start_horizon: int, changed_horizon: int
):
    parameters = _setup(config_eos, start_horizon)
    optimization = GeneticOptimization(fixed_seed=42)

    original_optimize = optimization.optimize

    def optimize_then_change_config(*args, **kwargs):
        result = original_optimize(*args, **kwargs)
        # A client updates the configuration while the run is still going.
        config_eos.merge_settings_from_dict(
            {"optimization": {"genetic": {"horizon_hours": changed_horizon}}}
        )
        return result

    optimization.optimize = optimize_then_change_config  # type: ignore[method-assign]

    solution = optimization.optimize_ems(parameters=parameters, start_hour=0, ngen=2)

    # The run finished on the horizon it started with ...
    assert len(solution.battery_grid_export_factor) == start_horizon
    # ... and the new value is live for the next run.
    assert config_eos.optimization.genetic.horizon_hours == changed_horizon
    assert optimization._run_genetic_cfg is None


def test_snapshot_is_released_when_the_run_fails(config_eos: ConfigEOS):
    parameters = _setup(config_eos, 24)
    optimization = GeneticOptimization(fixed_seed=42)

    def failing_optimize(*args, **kwargs):
        raise RuntimeError("boom")

    optimization.optimize = failing_optimize  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        optimization.optimize_ems(parameters=parameters, start_hour=0, ngen=1)
    assert optimization._run_genetic_cfg is None
    assert optimization._run_prediction_hours is None
