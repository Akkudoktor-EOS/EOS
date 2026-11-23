"""Offline regression tests for per-run optimization results and atomic publication."""

from asyncio import Lock
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from akkudoktoreos.core import ems as ems_module
from akkudoktoreos.core.emsettings import EnergyManagementMode
from akkudoktoreos.optimization.genetic0 import genetic0 as genetic0_module
from akkudoktoreos.optimization.genetic0 import genetic0prepare
from akkudoktoreos.optimization.genetic0.genetic0params import (
    Genetic0OptimizationParameters,
)
from akkudoktoreos.optimization.optimization import OptimizationAlgorithm
from akkudoktoreos.optimization.optimizer import OptimizerRun
from akkudoktoreos.utils.datetimeutil import to_datetime


def _patch_optimizer(monkeypatch, prefix: str, constructor: Any) -> None:
    """Replace the optimizer class that EnergyManagement.run() instantiates.

    GENETIC0 is imported on first use inside its optimizer, so it is patched at its source.
    """
    module = genetic0_module if prefix == "Genetic0" else ems_module
    monkeypatch.setattr(module, prefix + "Optimization", constructor)


def _patch_prepare(monkeypatch, prefix: str, prepare: Any) -> None:
    """Replace the legacy parameter preparation of GENETIC or GENETIC0."""
    parameters_class = (
        Genetic0OptimizationParameters
        if prefix == "Genetic0"
        else ems_module.GeneticOptimizationParameters
    )
    monkeypatch.setattr(parameters_class, "prepare", prepare)


def _assert_prepared_without_request(prefix: str, prepare: Any) -> None:
    """GENETIC0 passes the warm start (none here) into its legacy preparation."""
    if prefix == "Genetic0":
        prepare.assert_awaited_once_with(start_solution=None)
    else:
        prepare.assert_awaited_once_with()


def _native_solution(prefix: str) -> Any:
    """The native solution the latest run published for GENETIC or GENETIC0."""
    cls = ems_module.EnergyManagement
    if prefix == "Genetic0":
        return cls.optimizer_result("GENETIC0")
    return cls._genetic_solution


def _set_native_solution(monkeypatch, prefix: str, solution: Any) -> None:
    """Publish ``solution`` as the result of a previous run of GENETIC or GENETIC0."""
    cls = ems_module.EnergyManagement
    if prefix == "Genetic0":
        runs = {"GENETIC0": OptimizerRun(parameters=object(), result=solution)}
        monkeypatch.setattr(cls, "_optimizer_runs", runs)
    else:
        monkeypatch.setattr(cls, "_genetic_solution", solution)


def _supplied(prefix: str, parameters: Any, generations: int, seed: int) -> dict[str, Any]:
    """Run arguments that supply the parameters, generations and seed of a run."""
    if prefix == "Genetic0":
        return {"request": parameters, "overrides": {"generations": generations, "seed": seed}}
    return {
        "genetic_parameters": parameters,
        "genetic_generations": generations,
        "genetic_seed": seed,
    }


@pytest.fixture
def offline_ems(monkeypatch):
    """Run the actual EMS orchestration with no real adapters or prediction IO."""
    cls = ems_module.EnergyManagement
    for name in (
        "_start_datetime",
        "_observation_datetime",
        "_last_run_datetime",
        "_plan",
        "_optimization_solution",
        "_genetic_solution",
    ):
        monkeypatch.setattr(cls, name, None)
    monkeypatch.setattr(cls, "_optimizer_runs", {})
    monkeypatch.setattr(cls, "_stage", ems_module.EnergyManagementStage.IDLE)
    monkeypatch.setattr(cls, "_run_lock", Lock())
    monkeypatch.setattr(ems_module, "CacheEnergyManagementStore", Mock())
    ems = SimpleNamespace(
        config=SimpleNamespace(
            general=SimpleNamespace(timezone="Europe/Berlin"),
            ems=SimpleNamespace(mode=EnergyManagementMode.OPTIMIZATION),
            optimization=SimpleNamespace(
                algorithm=OptimizationAlgorithm.GENETIC,
                genetic=SimpleNamespace(generations=3, seed=17, interval_sec=3600, individuals=31),
                genetic0=SimpleNamespace(generations=5, seed=29),
            ),
            server=SimpleNamespace(verbose=False),
        ),
        prediction=SimpleNamespace(update_data=AsyncMock()),
        adapter=SimpleNamespace(update_data=AsyncMock()),
        set_start_datetime=cls.set_start_datetime,
    )
    # GENETIC0's parameter preparation reads the settings itself.
    monkeypatch.setattr(genetic0prepare, "get_config", lambda: ems.config)
    return ems


@pytest.mark.asyncio
@pytest.mark.parametrize("algorithm", list(OptimizationAlgorithm))
@pytest.mark.parametrize("explicit_start", [False, True])
async def test_implicit_genetic_start_uses_site_timezone(
    offline_ems, monkeypatch, set_other_timezone, algorithm, explicit_start
):
    set_other_timezone("UTC")
    now = to_datetime("2026-09-16T22:47:23Z", in_timezone="UTC")

    def frozen_datetime(value=None, **kwargs):
        return to_datetime(now if value is None else value, **kwargs)

    monkeypatch.setattr(ems_module, "to_datetime", frozen_datetime)
    offline_ems.config.optimization.genetic.interval_sec = 900
    await ems_module.EnergyManagement.run(
        offline_ems,
        mode=EnergyManagementMode.PREDICTION,
        algorithm=algorithm,
        start_datetime=now if explicit_start else None,
    )
    start = ems_module.EnergyManagement._start_datetime
    observed = ems_module.EnergyManagement._observation_datetime
    assert start is not None and observed is not None
    assert observed.timestamp() == now.timestamp()
    if algorithm == OptimizationAlgorithm.GENETIC:
        assert start.minute == 45
        assert start.hour == (22 if explicit_start else 0)
        assert start.day == (16 if explicit_start else 17)
        assert start.timezone_name == ("UTC" if explicit_start else "Europe/Berlin")
    else:
        assert start.minute == 0
        assert start.hour == 22
        assert start.day == 16
        assert start.timezone_name == "UTC"


@pytest.mark.asyncio
@pytest.mark.parametrize("algorithm", list(OptimizationAlgorithm))
@pytest.mark.parametrize("selection", ["configured", "explicit"])
@pytest.mark.parametrize("supplied", [False, True])
@pytest.mark.parametrize("timezone, expected_hour", [("UTC", 8), ("Europe/Berlin", 10)])
async def test_optimization_routes_only_selected_algorithm(
    monkeypatch, offline_ems, algorithm, selection, supplied, timezone, expected_hour
):
    """Configuration selection and explicit overrides retain isolated async paths."""
    selected_name = "Genetic" if algorithm == OptimizationAlgorithm.GENETIC else "Genetic0"
    other_name = "Genetic0" if selected_name == "Genetic" else "Genetic"
    suffix = "genetic" if algorithm == OptimizationAlgorithm.GENETIC else "genetic0"
    other_suffix = "genetic0" if suffix == "genetic" else "genetic"
    sentinel_parameters = object()
    sentinel_result, sentinel_plan = object(), object()
    solution = SimpleNamespace(
        optimization_solution=AsyncMock(return_value=sentinel_result),
        energy_management_plan=Mock(return_value=sentinel_plan),
    )
    constructors = {}
    preparers = {}
    for prefix in ("Genetic", "Genetic0"):
        constructor = Mock()
        constructor.return_value.optimize_ems.return_value = solution
        constructors[prefix] = constructor
        _patch_optimizer(monkeypatch, prefix, constructor)
        prepare = AsyncMock(return_value=sentinel_parameters)
        preparers[prefix] = prepare
        _patch_prepare(monkeypatch, prefix, prepare)
    kwargs: dict[str, Any] = {
        "start_datetime": to_datetime("2026-09-16T10:00:00+02:00", in_timezone=timezone)
    }
    if selection == "configured":
        offline_ems.config.optimization.algorithm = algorithm
    else:
        offline_ems.config.optimization.algorithm = OptimizationAlgorithm(other_suffix.upper())
        kwargs["algorithm"] = algorithm
    if supplied:
        kwargs.update(_supplied(selected_name, sentinel_parameters, 7, 43))
        if algorithm == OptimizationAlgorithm.GENETIC:
            kwargs["genetic_individuals"] = 11
    run_result = await ems_module.EnergyManagement.run(offline_ems, **kwargs)
    assert run_result is solution
    selected = constructors[selected_name]
    expected_config = getattr(offline_ems.config.optimization, suffix)
    selected.assert_called_once_with(
        verbose=False, fixed_seed=43 if supplied else expected_config.seed
    )
    expected_arguments: dict[str, Any] = {
        "start_hour": expected_hour,
        "parameters": sentinel_parameters,
        "ngen": 7 if supplied else expected_config.generations,
    }
    if algorithm == OptimizationAlgorithm.GENETIC:
        expected_arguments["individuals"] = 11 if supplied else None
    selected.return_value.optimize_ems.assert_called_once_with(**expected_arguments)
    constructors[other_name].assert_not_called()
    preparers[other_name].assert_not_awaited()
    if supplied:
        preparers[selected_name].assert_not_awaited()
    else:
        _assert_prepared_without_request(selected_name, preparers[selected_name])
    solution.optimization_solution.assert_awaited_once_with()
    solution.energy_management_plan.assert_called_once_with()
    cls = ems_module.EnergyManagement
    assert _native_solution(selected_name) is solution
    assert _native_solution(other_name) is None
    assert cls.optimization_solution() is sentinel_result
    assert cls.plan() is sentinel_plan
    assert cls.stage() == ems_module.EnergyManagementStage.IDLE
    offline_ems.prediction.update_data.assert_awaited_once_with(
        force_enable=False, force_update=False
    )
    assert offline_ems.adapter.update_data.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [EnergyManagementMode.DISABLED, EnergyManagementMode.PREDICTION])
async def test_non_optimization_modes_never_optimize(monkeypatch, offline_ems, mode):
    constructors = [Mock(), Mock()]
    _patch_optimizer(monkeypatch, "Genetic", constructors[0])
    _patch_optimizer(monkeypatch, "Genetic0", constructors[1])
    offline_ems.config.ems.mode = mode
    await ems_module.EnergyManagement.run(offline_ems)
    for constructor in constructors:
        constructor.assert_not_called()
    assert offline_ems.prediction.update_data.await_count == (
        mode == EnergyManagementMode.PREDICTION
    )
    assert offline_ems.adapter.update_data.await_count == (mode == EnergyManagementMode.PREDICTION)
    assert ems_module.EnergyManagement.plan() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["Genetic", "Genetic0"])
async def test_missing_preparation_does_not_dispatch_controls(monkeypatch, offline_ems, prefix):
    constructor = Mock()
    _patch_optimizer(monkeypatch, prefix, constructor)
    prepare = AsyncMock(return_value=None)
    _patch_prepare(monkeypatch, prefix, prepare)
    await ems_module.EnergyManagement.run(
        offline_ems, algorithm=OptimizationAlgorithm(prefix.upper())
    )
    _assert_prepared_without_request(prefix, prepare)
    constructor.assert_not_called()
    assert offline_ems.adapter.update_data.await_count == 1  # acquisition only
    assert ems_module.EnergyManagement.plan() is None
    assert ems_module.EnergyManagement.stage() == ems_module.EnergyManagementStage.IDLE


@pytest.mark.asyncio
@pytest.mark.parametrize("start_hour", [None, 11])
async def test_legacy_optimize_explicitly_uses_genetic0(monkeypatch, start_hour):
    """Even with GENETIC configured, /optimize must never become the new optimizer."""
    from akkudoktoreos.server import eos
    from akkudoktoreos.server.rest.error import EOSProblem

    fake = SimpleNamespace(
        run=AsyncMock(return_value=None), optimizer_result=Mock(return_value=None)
    )
    monkeypatch.setattr(eos, "get_ems", lambda: fake)
    parameters = Genetic0OptimizationParameters.model_validate(
        {
            "ems": {
                "pv_prognose_wh": [0.0, 100.0],
                "gesamtlast": [100.0, 100.0],
                "strompreis_euro_pro_wh": [0.0003, 0.0003],
                "einspeiseverguetung_euro_pro_wh": 0.00008,
                "preis_euro_pro_wh_akku": 0.0,
            },
            "pv_akku": None,
            "eauto": None,
            "inverter": None,
        }
    )
    with pytest.raises(EOSProblem):
        await eos.fastapi_optimize(parameters=parameters, start_hour=start_hour, ngen=2)
    kwargs = fake.run.await_args.kwargs
    assert kwargs["mode"] == EnergyManagementMode.OPTIMIZATION
    assert kwargs["algorithm"] == OptimizationAlgorithm.GENETIC0
    assert kwargs["request"] is parameters
    assert kwargs["overrides"] == {"generations": 2}
    assert "genetic_parameters" not in kwargs
    assert (
        kwargs["start_datetime"] is None
        if start_hour is None
        else kwargs["start_datetime"].hour == 11
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["optimizer", "conversion", "plan"])
async def test_failed_legacy_http_run_never_reports_previous_solution(
    monkeypatch, offline_ems, phase
):
    import json
    from pathlib import Path
    from types import MethodType

    from httpx import ASGITransport, AsyncClient

    from akkudoktoreos.optimization.genetic0.genetic0solution import Genetic0Solution
    from akkudoktoreos.server import eos

    cls = ems_module.EnergyManagement
    data = json.loads(
        (Path(__file__).parent / "testdata/genetic0/optimize_result_1.json").read_text()
    )
    previous = Genetic0Solution.model_validate(data)
    _set_native_solution(monkeypatch, "Genetic0", previous)
    constructor = Mock()
    error = RuntimeError("synthetic " + phase + " failure")
    native = SimpleNamespace(
        optimization_solution=AsyncMock(return_value=object()),
        energy_management_plan=Mock(return_value=object()),
    )
    constructor.return_value.optimize_ems.return_value = native
    if phase == "optimizer":
        constructor.return_value.optimize_ems.side_effect = error
    elif phase == "conversion":
        native.optimization_solution.side_effect = error
    else:
        native.energy_management_plan.side_effect = error
    _patch_optimizer(monkeypatch, "Genetic0", constructor)
    offline_ems.run = MethodType(cls.run, offline_ems)
    offline_ems.optimizer_result = cls.optimizer_result
    monkeypatch.setattr(eos, "get_ems", lambda: offline_ems)
    async with AsyncClient(
        transport=ASGITransport(app=eos.app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.post("/optimize?ngen=1", json=data["parameters"])
    constructor.return_value.optimize_ems.assert_called_once()
    # This diagnostic proves a failure is the stale-success bug, not invalid input.
    if response.status_code == 200:
        assert response.json()["start_solution"] == previous.start_solution
        assert response.json()["result"]["total_balance"] == previous.result.total_balance
    assert response.status_code >= 400, (
        "The failing optimizer returned HTTP 200 with the previous solution"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["Genetic", "Genetic0"])
@pytest.mark.parametrize("phase", ["conversion", "plan"])
async def test_conversion_failure_preserves_consistent_previous_results(
    monkeypatch, offline_ems, prefix, phase
):
    cls = ems_module.EnergyManagement
    previous_specific, previous_generic, previous_plan = object(), object(), object()
    _set_native_solution(monkeypatch, prefix, previous_specific)
    monkeypatch.setattr(cls, "_optimization_solution", previous_generic)
    monkeypatch.setattr(cls, "_plan", previous_plan)
    conversion = AsyncMock(return_value=object())
    solution = SimpleNamespace(optimization_solution=conversion, energy_management_plan=Mock())
    if phase == "conversion":
        conversion.side_effect = RuntimeError("synthetic conversion failure")
    else:
        solution.energy_management_plan.side_effect = RuntimeError("synthetic plan failure")
    constructor = Mock()
    constructor.return_value.optimize_ems.return_value = solution
    _patch_optimizer(monkeypatch, prefix, constructor)
    supplied_parameters = _supplied(prefix, object(), 3, 17)
    result = await cls.run(
        offline_ems,
        algorithm=OptimizationAlgorithm(prefix.upper()),
        **supplied_parameters,
    )
    assert result is None
    conversion.assert_awaited_once_with()
    assert solution.energy_management_plan.call_count == (phase == "plan")
    assert offline_ems.adapter.update_data.await_count == 1
    assert (
        _native_solution(prefix),
        cls.optimization_solution(),
        cls.plan(),
        cls.stage(),
    ) == (
        previous_specific,
        previous_generic,
        previous_plan,
        ems_module.EnergyManagementStage.IDLE,
    ), (
        "Failed conversion published a new algorithm result beside the old plan and left EMS in OPTIMIZATION"
    )


@pytest.mark.asyncio
async def test_legacy_endpoint_uses_run_return_value_not_last_cached_solution(monkeypatch):
    import json
    from pathlib import Path

    from akkudoktoreos.optimization.genetic0.genetic0solution import Genetic0Solution
    from akkudoktoreos.server import eos

    data = json.loads(
        (Path(__file__).parent / "testdata/genetic0/optimize_result_1.json").read_text()
    )
    produced = Genetic0Solution.model_validate(data)
    fake = SimpleNamespace(
        run=AsyncMock(return_value=produced),
        optimizer_result=Mock(side_effect=AssertionError("must use this run's result")),
    )
    monkeypatch.setattr(eos, "get_ems", lambda: fake)
    result = await eos.fastapi_optimize(parameters=produced.parameters, ngen=1)
    assert result.start_solution == produced.start_solution
    assert result.result.total_balance == produced.result.total_balance
    fake.optimizer_result.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["Genetic", "Genetic0"])
async def test_raising_preparation_cancels_the_run(monkeypatch, offline_ems, prefix):
    """A preparation that raises cancels the run like one that returns None."""
    cls = ems_module.EnergyManagement
    previous_specific, previous_generic, previous_plan = object(), object(), object()
    _set_native_solution(monkeypatch, prefix, previous_specific)
    monkeypatch.setattr(cls, "_optimization_solution", previous_generic)
    monkeypatch.setattr(cls, "_plan", previous_plan)
    constructor = Mock()
    _patch_optimizer(monkeypatch, prefix, constructor)
    _patch_prepare(monkeypatch, prefix, AsyncMock(side_effect=RuntimeError("synthetic")))
    result = await cls.run(offline_ems, algorithm=OptimizationAlgorithm(prefix.upper()))
    assert result is None
    constructor.assert_not_called()
    assert offline_ems.adapter.update_data.await_count == 1  # acquisition only
    assert (
        _native_solution(prefix),
        cls.optimization_solution(),
        cls.plan(),
        cls.stage(),
    ) == (
        previous_specific,
        previous_generic,
        previous_plan,
        ems_module.EnergyManagementStage.IDLE,
    )


@pytest.mark.asyncio
async def test_previous_genetic0_result_is_the_warm_start_of_the_next_run(
    monkeypatch, offline_ems
):
    """The EMS passes the result of the last successful GENETIC0 run into the next preparation.

    The optimizer holds no state: the warm start comes from the EMS slot, and a failed run keeps
    the previous one.
    """
    cls = ems_module.EnergyManagement
    first = SimpleNamespace(
        start_solution=[1.0, 2.0],
        optimization_solution=AsyncMock(return_value=object()),
        energy_management_plan=Mock(return_value=object()),
    )
    second = SimpleNamespace(
        start_solution=[3.0, 4.0],
        optimization_solution=AsyncMock(return_value=object()),
        energy_management_plan=Mock(return_value=object()),
    )
    constructor = Mock()
    constructor.return_value.optimize_ems.side_effect = [
        first,
        RuntimeError("synthetic optimizer failure"),
        second,
        second,
    ]
    _patch_optimizer(monkeypatch, "Genetic0", constructor)
    prepare = AsyncMock(return_value=object())
    _patch_prepare(monkeypatch, "Genetic0", prepare)
    for _ in range(3):
        await cls.run(offline_ems, algorithm=OptimizationAlgorithm.GENETIC0)
    assert cls.optimizer_result("GENETIC0") is second
    # Without a stored run there is no warm start.
    monkeypatch.setattr(cls, "_optimizer_runs", {})
    await cls.run(offline_ems, algorithm=OptimizationAlgorithm.GENETIC0)
    assert [call.kwargs for call in prepare.await_args_list] == [
        {"start_solution": None},
        {"start_solution": [1.0, 2.0]},
        {"start_solution": [1.0, 2.0]},  # the failed second run kept the first as warm start
        {"start_solution": None},
    ]
