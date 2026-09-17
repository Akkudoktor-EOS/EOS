"""A warm start from an earlier run is aligned with the slot this run starts in.

Genomes are run-relative: gene 0 controls the slot the run starts in. Reusing
the previous solution unchanged after a slot boundary describes every decision
one slot too late, and a search that keeps the seed postpones a planned action
by one slot per run.
"""

from types import SimpleNamespace

import pytest

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticEnergyManagementParameters,
    GeneticOptimizationParameters,
)
from akkudoktoreos.utils.datetimeutil import DateTime, compare_datetimes, to_datetime


def _optimizer(
    config_eos: ConfigEOS,
    *,
    interval: int,
    optimize_ev: bool = False,
    n_appliance_genes: int = 0,
) -> tuple[GeneticOptimization, DateTime]:
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {
                "genetic": {"tail_horizon_hours": 0, "horizon_hours": 24, "interval_sec": interval}
            },
        }
    )
    slot0 = get_ems(init=True).set_start_datetime(to_datetime().set(hour=8, minute=0, second=0))
    opt = GeneticOptimization(fixed_seed=42)
    opt.optimize_ev = optimize_ev
    opt.appliance_layout = SimpleNamespace(n_genes=n_appliance_genes, genes=[])  # type: ignore[assignment]
    opt._slot0_datetime = slot0
    return opt, slot0


def _genes(start: int, stop: int) -> list[float]:
    return [float(value) for value in range(start, stop)]


def test_quarter_hour_warm_start_moves_one_slot_forward(config_eos: ConfigEOS):
    # 07:45 run: export genes (32) at 08:00 and 08:15. Unshifted, the 08:00 run
    # would read them as 08:15 and 08:30.
    opt, slot0 = _optimizer(config_eos, interval=900)
    previous = [19.0, 32.0, 32.0, 14.0] + [36.0] * (opt.control_slots - 4)

    aligned = opt._start_solution_for_run_start(previous, slot0.subtract(minutes=15))

    assert aligned == [32, 32, 14] + [36] * (opt.control_slots - 3)


def test_elapsed_slots_are_dropped_per_block_and_appliance_genes_kept(config_eos: ConfigEOS):
    opt, slot0 = _optimizer(config_eos, interval=3600, optimize_ev=True, n_appliance_genes=1)
    slots = opt.control_slots
    battery = _genes(0, slots)
    ev = _genes(100, 100 + slots)
    appliance = [3.0]

    aligned = opt._start_solution_for_run_start(battery + ev + appliance, slot0.subtract(hours=3))

    assert aligned == (
        _genes(3, slots)
        + [slots - 1.0] * 3
        + _genes(103, 100 + slots)
        + [100 + slots - 1.0] * 3
        + appliance
    )


def test_same_slot_or_unknown_start_keeps_warm_start(config_eos: ConfigEOS):
    opt, slot0 = _optimizer(config_eos, interval=900)
    previous = _genes(0, opt.control_slots)

    assert opt._start_solution_for_run_start(previous, slot0) == previous
    assert opt._start_solution_for_run_start(previous, None) == previous
    assert opt._start_solution_for_run_start(None, slot0) is None


@pytest.mark.parametrize(
    "offset_minutes",
    [
        pytest.param(-24 * 60, id="all-control-slots-elapsed"),
        pytest.param(15, id="starts-after-this-run"),
    ],
)
def test_unusable_warm_start_is_dropped(config_eos: ConfigEOS, offset_minutes: int):
    opt, slot0 = _optimizer(config_eos, interval=900)
    previous = _genes(0, opt.control_slots)

    assert opt._start_solution_for_run_start(previous, slot0.add(minutes=offset_minutes)) is None


def test_start_datetime_falls_back_to_last_solution_of_this_server(
    config_eos: ConfigEOS, monkeypatch: pytest.MonkeyPatch
):
    opt, slot0 = _optimizer(config_eos, interval=900)
    last_start = slot0.subtract(minutes=15)
    monkeypatch.setattr(
        type(get_ems()),
        "_genetic_solution",
        SimpleNamespace(start_solution=[19.0, 32.0, 14.0], start_solution_datetime=last_start),
    )

    same = SimpleNamespace(start_solution=[19, 32, 14], start_solution_datetime=None)
    other = SimpleNamespace(start_solution=[19, 32, 15], start_solution_datetime=None)
    explicit_start = slot0.subtract(minutes=30)
    explicit = SimpleNamespace(start_solution=[19, 32, 14], start_solution_datetime=explicit_start)

    assert opt._resolve_start_solution_datetime(same) == last_start  # type: ignore[arg-type]
    assert opt._resolve_start_solution_datetime(other) is None  # type: ignore[arg-type]
    assert opt._resolve_start_solution_datetime(explicit) == explicit_start  # type: ignore[arg-type]


def test_parameters_accept_iso_start_solution_datetime(config_eos: ConfigEOS):
    parameters = GeneticOptimizationParameters.model_validate(
        dict(
            ems=GeneticEnergyManagementParameters.model_validate(
                dict(
                    pv_prognose_wh=[0.0, 0.0],
                    strompreis_euro_pro_wh=[0.0, 0.0],
                    einspeiseverguetung_euro_pro_wh=0.0,
                    preis_euro_pro_wh_akku=0.0,
                    gesamtlast=[0.0, 0.0],
                )
            ),
            pv_akku=None,
            inverter=None,
            eauto=None,
            start_solution=[1.0, 2.0],
            start_solution_datetime="2026-09-14T07:45:00+02:00",
        )
    )

    assert parameters.start_solution_datetime is not None
    assert compare_datetimes(
        parameters.start_solution_datetime, to_datetime("2026-09-14T07:45:00+02:00")
    ).equal
