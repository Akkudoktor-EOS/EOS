"""Offline regression tests for per-run optimization results and atomic publication."""

from asyncio import Lock
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from akkudoktoreos.core import ems as ems_module
from akkudoktoreos.core.emsettings import EnergyManagementMode
from akkudoktoreos.optimization.genetic0.genetic0params import (
    Genetic0OptimizationParameters,
)
from akkudoktoreos.optimization.optimization import OptimizationAlgorithm
from akkudoktoreos.utils.datetimeutil import to_datetime


@pytest.fixture
def offline_ems(monkeypatch):
    """Run the actual EMS orchestration with no real adapters or prediction IO."""
    cls = ems_module.EnergyManagement
    for name in (
        "_start_datetime",
        "_last_run_datetime",
        "_plan",
        "_optimization_solution",
        "_genetic_solution",
        "_genetic0_solution",
    ):
        monkeypatch.setattr(cls, name, None)
    monkeypatch.setattr(cls, "_stage", ems_module.EnergyManagementStage.IDLE)
    monkeypatch.setattr(cls, "_run_lock", Lock())
    monkeypatch.setattr(ems_module, "CacheEnergyManagementStore", Mock())
    return SimpleNamespace(
        config=SimpleNamespace(
            ems=SimpleNamespace(mode=EnergyManagementMode.OPTIMIZATION),
            optimization=SimpleNamespace(
                algorithm=OptimizationAlgorithm.GENETIC,
                genetic=SimpleNamespace(generations=3, seed=17),
                genetic0=SimpleNamespace(generations=5, seed=29),
            ),
            server=SimpleNamespace(verbose=False),
        ),
        prediction=SimpleNamespace(update_data=AsyncMock()),
        adapter=SimpleNamespace(update_data=AsyncMock()),
        set_start_datetime=cls.set_start_datetime,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("algorithm", list(OptimizationAlgorithm))
@pytest.mark.parametrize("selection", ["configured", "explicit"])
@pytest.mark.parametrize("supplied", [False, True])
async def test_optimization_routes_only_selected_algorithm(
    monkeypatch, offline_ems, algorithm, selection, supplied
):
    """Configuration selection and explicit overrides retain isolated async paths."""
    selected_name = "Genetic" if algorithm == OptimizationAlgorithm.GENETIC else "Genetic0"
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
        monkeypatch.setattr(ems_module, prefix + "Optimization", constructor)
        prepare = AsyncMock(return_value=sentinel_parameters)
        preparers[prefix] = prepare
        monkeypatch.setattr(
            getattr(ems_module, prefix + "OptimizationParameters"), "prepare", prepare
        )
    kwargs = {"start_datetime": to_datetime("2026-09-16T10:00:00+02:00")}
    if selection == "configured":
        offline_ems.config.optimization.algorithm = algorithm
    else:
        offline_ems.config.optimization.algorithm = OptimizationAlgorithm(other_suffix.upper())
        kwargs["algorithm"] = algorithm
    if supplied:
        kwargs[suffix + "_parameters"] = sentinel_parameters
        kwargs[suffix + "_generations"] = 7
        kwargs[suffix + "_seed"] = 43
    run_result = await ems_module.EnergyManagement.run(offline_ems, **kwargs)
    assert run_result is solution
    selected = constructors[selected_name]
    expected_config = getattr(offline_ems.config.optimization, suffix)
    selected.assert_called_once_with(
        verbose=False, fixed_seed=43 if supplied else expected_config.seed
    )
    selected.return_value.optimize_ems.assert_called_once_with(
        start_hour=10,
        parameters=sentinel_parameters,
        ngen=7 if supplied else expected_config.generations,
    )
    other_name = "Genetic0" if selected_name == "Genetic" else "Genetic"
    constructors[other_name].assert_not_called()
    preparers[other_name].assert_not_awaited()
    if supplied:
        preparers[selected_name].assert_not_awaited()
    else:
        preparers[selected_name].assert_awaited_once_with()
    solution.optimization_solution.assert_awaited_once_with()
    solution.energy_management_plan.assert_called_once_with()
    cls = ems_module.EnergyManagement
    assert getattr(cls, "_" + suffix + "_solution") is solution
    assert getattr(cls, "_" + other_suffix + "_solution") is None
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
    monkeypatch.setattr(ems_module, "GeneticOptimization", constructors[0])
    monkeypatch.setattr(ems_module, "Genetic0Optimization", constructors[1])
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
    monkeypatch.setattr(ems_module, prefix + "Optimization", constructor)
    prepare = AsyncMock(return_value=None)
    monkeypatch.setattr(getattr(ems_module, prefix + "OptimizationParameters"), "prepare", prepare)
    await ems_module.EnergyManagement.run(
        offline_ems, algorithm=OptimizationAlgorithm(prefix.upper())
    )
    prepare.assert_awaited_once_with()
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
        run=AsyncMock(return_value=None), genetic0_solution=Mock(return_value=None)
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
    assert kwargs["genetic0_parameters"] is parameters
    assert kwargs["genetic0_generations"] == 2
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
    monkeypatch.setattr(cls, "_genetic0_solution", previous)
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
    monkeypatch.setattr(ems_module, "Genetic0Optimization", constructor)
    offline_ems.run = MethodType(cls.run, offline_ems)
    offline_ems.genetic0_solution = cls.genetic0_solution
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
    suffix = prefix.lower()
    monkeypatch.setattr(cls, "_" + suffix + "_solution", previous_specific)
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
    monkeypatch.setattr(ems_module, prefix + "Optimization", constructor)
    result = await cls.run(
        offline_ems,
        algorithm=OptimizationAlgorithm(prefix.upper()),
        **{suffix + "_parameters": object()},
    )
    assert result is None
    conversion.assert_awaited_once_with()
    assert solution.energy_management_plan.call_count == (phase == "plan")
    assert offline_ems.adapter.update_data.await_count == 1
    assert (
        getattr(cls, "_" + suffix + "_solution"),
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
        genetic0_solution=Mock(side_effect=AssertionError("must use this run's result")),
    )
    monkeypatch.setattr(eos, "get_ems", lambda: fake)
    result = await eos.fastapi_optimize(parameters=produced.parameters, ngen=1)
    assert result.start_solution == produced.start_solution
    assert result.result.total_balance == produced.result.total_balance
    fake.genetic0_solution.assert_not_called()
