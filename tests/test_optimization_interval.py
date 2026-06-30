"""Tests for the 15-minute optimization interval.

The genetic optimizer runs on a fixed slot grid whose length is
``prediction.hours * (3600 / interval)``. At the default interval of 3600 s this
is the established hourly behaviour (covered by ``test_geneticoptimize.py``);
here we cover the 900 s (15 min) slot grid.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.cache import CacheEnergyManagementStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.utils.datetimeutil import to_datetime
from akkudoktoreos.utils.visualize import prepare_visualize

ems_eos = get_ems(init=True)  # init once

DIR_TESTDATA = Path(__file__).parent / "testdata"


@pytest.mark.parametrize(
    "interval, exp_slots_per_hour, exp_slot_duration_h",
    [
        (3600, 1, 1.0),
        (900, 4, 0.25),
    ],
)
def test_slot_helpers(
    config_eos: ConfigEOS,
    interval: int,
    exp_slots_per_hour: int,
    exp_slot_duration_h: float,
):
    """slot_duration_h / slots_per_hour / total_slots track the configured interval."""
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {"horizon_hours": 48, "interval": interval},
        }
    )
    ems_eos.set_start_datetime(to_datetime().set(hour=10, minute=0))

    opt = GeneticOptimization(fixed_seed=42)

    assert opt.slots_per_hour == exp_slots_per_hour
    assert opt.slot_duration_h == exp_slot_duration_h
    assert opt.total_slots == 48 * exp_slots_per_hour
    # At minute 0 the start slot is the hour scaled by the slot count.
    assert opt._start_day_slot() == 10 * exp_slots_per_hour


def test_start_day_slot_includes_minute_offset(config_eos: ConfigEOS):
    """At 15-min resolution the start slot includes the minute offset."""
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {"horizon_hours": 48, "interval": 900},
        }
    )
    ems_eos.set_start_datetime(to_datetime().set(hour=10, minute=30))

    opt = GeneticOptimization(fixed_seed=42)

    # Slot index is derived from the actual EMS start datetime (which may be
    # floored to the hour by the energy management system): hour*4 + minute//15.
    sd = opt.ems.start_datetime
    assert opt._start_day_slot() == sd.hour * 4 + sd.minute // 15


def test_optimize_15min_slot_grid(config_eos: ConfigEOS):
    """An end-to-end optimization at interval=900 runs on a 192-slot day grid.

    This exercises the full path (parameter preparation, GA core, device
    simulation, solution/plan serialization) at 15-min resolution and asserts the
    structural properties; the optimization result itself is not pinned because
    the 15-min grid is a different problem than the hourly one.
    """
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {
                "horizon_hours": 48,
                "interval": 900,
                "genetic": {
                    "individuals": 300,
                    "generations": 10,
                    "penalties": {
                        "ev_soc_miss": 10,
                        "ac_charge_break_even": 0,
                    },
                },
            },
            "devices": {
                "max_electric_vehicles": 1,
                "electric_vehicles": [
                    {
                        "charge_rates": [0.0, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0],
                    }
                ],
            },
        }
    )

    with (DIR_TESTDATA / "optimize_input_1.json").open("r") as f_in:
        input_data = GeneticOptimizationParameters(**json.load(f_in))

    ems_eos.set_start_datetime(to_datetime().set(hour=10, minute=0))
    CacheEnergyManagementStore().clear()

    opt = GeneticOptimization(fixed_seed=42)
    assert opt.total_slots == 192
    assert opt.slot_duration_h == 0.25

    visualize_filename = str((DIR_TESTDATA / "new_optimize_15min.json").with_suffix(".pdf"))
    with patch(
        "akkudoktoreos.utils.visualize.prepare_visualize",
        side_effect=lambda parameters, results, *args, **kwargs: prepare_visualize(
            parameters, results, filename=visualize_filename, **kwargs
        ),
    ):
        genetic_solution = opt.optimierung_ems(
            parameters=input_data, start_hour=10, ngen=3
        )

    # The genetic core emitted a full-day grid at 15-min resolution.
    assert len(genetic_solution.ac_charge) == 192
    assert len(genetic_solution.dc_charge) == 192
    assert len(genetic_solution.discharge_allowed) == 192

    # The serializers consume the 15-min grid without error and emit a 900 s
    # spaced solution index.
    solution = genetic_solution.optimization_solution()
    df = solution.solution.to_dataframe()
    assert len(df.index) >= 2
    delta_seconds = (df.index[1] - df.index[0]).total_seconds()
    assert delta_seconds == 900

    plan = genetic_solution.energy_management_plan()
    assert plan is not None
