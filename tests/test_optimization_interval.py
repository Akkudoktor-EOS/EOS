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
from akkudoktoreos.optimization.genetic.geneticdevices import HomeApplianceParameters
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.utils.datetimeutil import to_datetime
from akkudoktoreos.utils.visualize import prepare_visualize

ems_eos = get_ems(init=True)  # init once

DIR_TESTDATA = Path(__file__).parent / "testdata"


def load_hourly_parameters() -> GeneticOptimizationParameters:
    """Load the legacy 48-value API example used by hourly clients."""
    with (DIR_TESTDATA / "optimize_input_1.json").open("r") as f_in:
        return GeneticOptimizationParameters(**json.load(f_in))


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


def test_ems_start_is_floored_to_quarter_hour(config_eos: ConfigEOS):
    """Rolling optimization starts at the current slot, not the previous full hour."""
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {"horizon_hours": 48, "interval": 900},
        }
    )

    aligned = ems_eos.set_start_datetime(to_datetime().set(hour=10, minute=38, second=42))

    assert aligned.hour == 10
    assert aligned.minute == 30
    assert aligned.second == 0


def test_unsupported_interval_falls_back_to_hourly(config_eos: ConfigEOS):
    """The genetic optimizer falls back without restricting interval-aware providers."""
    config_eos.merge_settings_from_dict({"optimization": {"interval": 1800}})

    assert config_eos.optimization.interval == 1800
    GeneticOptimization(fixed_seed=42)
    assert config_eos.optimization.interval == 3600


def test_hourly_api_input_is_normalized_to_quarter_hour_slots(config_eos: ConfigEOS):
    """Legacy API energy is split while prices are held over four slots."""
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {"horizon_hours": 48, "interval": 900},
        }
    )
    parameters = load_hourly_parameters()
    opt = GeneticOptimization(fixed_seed=42)

    normalized = opt._parameters_for_slot_grid(parameters)

    assert len(normalized.ems.pv_prognose_wh) == 192
    assert len(normalized.ems.gesamtlast) == 192
    assert len(normalized.ems.strompreis_euro_pro_wh) == 192
    assert len(normalized.ems.einspeiseverguetung_euro_pro_wh) == 192
    assert sum(normalized.ems.pv_prognose_wh[:4]) == pytest.approx(parameters.ems.pv_prognose_wh[0])
    assert sum(normalized.ems.gesamtlast[:4]) == pytest.approx(parameters.ems.gesamtlast[0])
    assert (
        normalized.ems.strompreis_euro_pro_wh[:4] == [parameters.ems.strompreis_euro_pro_wh[0]] * 4
    )
    assert (
        normalized.ems.einspeiseverguetung_euro_pro_wh[:4]
        == [parameters.ems.einspeiseverguetung_euro_pro_wh[0]] * 4
    )


def test_native_quarter_hour_input_is_not_resampled(config_eos: ConfigEOS):
    """Native 192-value input survives normalization without repetition or scaling."""
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {"horizon_hours": 48, "interval": 900},
        }
    )
    parameters = load_hourly_parameters()
    native_values = [float(i) for i in range(192)]
    native_ems = parameters.ems.model_copy(
        update={
            "pv_prognose_wh": native_values,
            "gesamtlast": native_values,
            "strompreis_euro_pro_wh": native_values,
            "einspeiseverguetung_euro_pro_wh": native_values,
        },
        deep=True,
    )
    native_parameters = parameters.model_copy(update={"ems": native_ems}, deep=True)

    normalized = GeneticOptimization(fixed_seed=42)._parameters_for_slot_grid(native_parameters)

    assert normalized.ems.pv_prognose_wh == native_values
    assert normalized.ems.gesamtlast == native_values
    assert normalized.ems.strompreis_euro_pro_wh == native_values
    assert normalized.ems.einspeiseverguetung_euro_pro_wh == native_values


def test_scalar_feed_in_tariff_fills_quarter_hour_grid(config_eos: ConfigEOS):
    """A fixed feed-in tariff becomes one value per optimization slot."""
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {"horizon_hours": 48, "interval": 900},
        }
    )
    parameters = load_hourly_parameters()
    fixed_tariff = 0.00008
    scalar_ems = parameters.ems.model_copy(
        update={"einspeiseverguetung_euro_pro_wh": fixed_tariff}, deep=True
    )
    scalar_parameters = parameters.model_copy(update={"ems": scalar_ems}, deep=True)

    normalized = GeneticOptimization(fixed_seed=42)._parameters_for_slot_grid(scalar_parameters)

    assert normalized.ems.einspeiseverguetung_euro_pro_wh == [fixed_tariff] * 192


def test_ambiguous_input_length_is_rejected(config_eos: ConfigEOS):
    """Unexpected input lengths fail instead of silently shortening the simulation."""
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {"horizon_hours": 48, "interval": 900},
        }
    )
    parameters = load_hourly_parameters()
    invalid_ems = parameters.ems.model_copy(
        update={
            "pv_prognose_wh": [0.0] * 96,
            "gesamtlast": [0.0] * 96,
            "strompreis_euro_pro_wh": [0.0] * 96,
            "einspeiseverguetung_euro_pro_wh": [0.0] * 96,
        },
        deep=True,
    )
    invalid_parameters = parameters.model_copy(update={"ems": invalid_ems}, deep=True)

    with pytest.raises(ValueError, match="expected either 48 hourly values or 192"):
        GeneticOptimization(fixed_seed=42)._parameters_for_slot_grid(invalid_parameters)


def test_hourly_start_solution_is_expanded_to_slots(config_eos: ConfigEOS):
    """A cached hourly genome becomes a valid quarter-hour warm start."""
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {"horizon_hours": 48, "interval": 900},
        }
    )
    opt = GeneticOptimization(fixed_seed=42)
    opt.optimize_ev = False
    hourly = list(range(48))

    migrated = opt._start_solution_for_slot_grid(hourly, has_appliance=False)

    assert len(migrated) == 192
    assert migrated[:8] == [0, 0, 0, 0, 1, 1, 1, 1]


def test_quarter_hour_mutation_probability_preserves_hourly_rate(config_eos: ConfigEOS):
    """A finer genome does not mutate four times as many controls per hour."""
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {"horizon_hours": 48, "interval": 900},
        }
    )
    opt = GeneticOptimization(fixed_seed=42)
    opt.optimize_ev = False
    opt.setup_deap_environment({"home_appliance": 0}, start_hour=0)

    assert opt.toolbox.mutate_charge_discharge.keywords["indpb"] == pytest.approx(0.05)


def test_sub_hourly_home_appliance_is_rejected(config_eos: ConfigEOS):
    """An hourly appliance model must not silently run on slot indices."""
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {"horizon_hours": 48, "interval": 900},
        }
    )
    parameters = load_hourly_parameters().model_copy(
        update={
            "dishwasher": HomeApplianceParameters(
                device_id="dishwasher", consumption_wh=1200, duration_h=2
            )
        },
        deep=True,
    )
    ems_eos.set_start_datetime(to_datetime().set(hour=10, minute=0))

    with pytest.raises(ValueError, match="Home-appliance scheduling"):
        GeneticOptimization(fixed_seed=42).optimierung_ems(
            parameters=parameters, start_hour=10, ngen=1
        )


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

    input_data = load_hourly_parameters()

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
        genetic_solution = opt.optimierung_ems(parameters=input_data, start_hour=10, ngen=3)

    # The genetic core emitted a full-day grid at 15-min resolution.
    assert len(genetic_solution.ac_charge) == 192
    assert len(genetic_solution.dc_charge) == 192
    assert len(genetic_solution.discharge_allowed) == 192
    expected_result_slots = 192 - opt._start_day_slot()
    assert len(genetic_solution.result.Last_Wh_pro_Stunde) == expected_result_slots
    assert len(genetic_solution.result.Electricity_price) == expected_result_slots

    # The serializers consume the 15-min grid without error and emit a 900 s
    # spaced solution index.
    solution = genetic_solution.optimization_solution()
    df = solution.solution.to_dataframe()
    assert len(df.index) >= 2
    delta_seconds = (df.index[1] - df.index[0]).total_seconds()
    assert delta_seconds == 900

    plan = genetic_solution.energy_management_plan()
    assert plan is not None
