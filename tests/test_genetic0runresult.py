"""Offline regression tests for per-run optimization results and atomic publication."""

from asyncio import Lock
from types import SimpleNamespace
from typing import Any
from unittest.mock import ANY, AsyncMock, Mock

import pytest

from akkudoktoreos.core import ems as ems_module
from akkudoktoreos.core.emsettings import EnergyManagementMode
from akkudoktoreos.optimization.genetic import geneticalgorithm as genetic_module
from akkudoktoreos.optimization.genetic import geneticprepare
from akkudoktoreos.optimization.genetic0 import genetic0algorithm as genetic0_module
from akkudoktoreos.optimization.genetic0 import genetic0convert, genetic0prepare
from akkudoktoreos.optimization.genetic0.genetic0request import (
    Genetic0Request,
)
from akkudoktoreos.optimization.genetic.geneticrequest import GeneticRequest
from akkudoktoreos.optimization.optimization import OptimizationAlgorithm
from akkudoktoreos.optimization.optimizer import OptimizerRun
from akkudoktoreos.utils.datetimeutil import to_datetime
from akkudoktoreos.utils.gridinterpolator import GridInterpolatorBackend


def _patch_optimizer(monkeypatch, prefix: str, constructor: Any) -> None:
    """Replace the optimizer class that the optimizer's ``run()`` instantiates.

    Both are imported on first use inside their optimizer, so they are patched at their source.
    """
    module = genetic0_module if prefix == "Genetic0" else genetic_module
    monkeypatch.setattr(module, prefix + "Optimization", constructor)


def _patch_prepare(monkeypatch, prefix: str, prepare: Any) -> None:
    """Replace the forecast and device preparation of GENETIC or GENETIC0.

    GENETIC resolves every request, the configured default one included, through
    ``resolve_request()``; GENETIC0's legacy preparation serves runs without a request only.
    """
    if prefix == "Genetic0":
        monkeypatch.setattr(genetic0prepare, "prepare_request", prepare)
    else:
        monkeypatch.setattr(geneticprepare, "resolve_request", prepare)


def _warm_start_of(call: Any) -> Any:
    """The start solution a call of GENETIC0's legacy preparation got."""
    start, observation, start_solution = call.args
    return start_solution


def _assert_prepared_without_request(prefix: str, prepare: Any) -> None:
    """Both pass the warm start (none here) into their preparation; GENETIC a default request."""
    prepare.assert_awaited_once()
    if prefix == "Genetic0":
        assert _warm_start_of(prepare.await_args) is None
    else:
        request, start, observation, warm_start = prepare.await_args.args
        assert request.model_dump() == GeneticRequest().model_dump()
        assert warm_start is None


def _conversion_arguments(prefix: str) -> dict[str, Any]:
    """Arguments of the stand-in results' conversion methods (none for both optimizers)."""
    return {}


def _native_solution(prefix: str) -> Any:
    """The native solution the latest run published for GENETIC or GENETIC0."""
    return ems_module.EnergyManagement.optimizer_result(prefix.upper())


def _set_native_solution(monkeypatch, prefix: str, solution: Any) -> None:
    """Publish ``solution`` as the result of a previous run of GENETIC or GENETIC0."""
    runs = {prefix.upper(): OptimizerRun(parameters=object(), result=solution)}
    monkeypatch.setattr(ems_module.EnergyManagement, "_optimizer_runs", runs)


def _stand_in_request() -> SimpleNamespace:
    """A request both preparations accept; GENETIC0's preparation looks at its home appliance."""
    return SimpleNamespace(dishwasher=None)


def _supplied(parameters: Any, generations: int, seed: int) -> dict[str, Any]:
    """Run arguments that supply the parameters, generations and seed of a run."""
    return {"request": parameters, "overrides": {"generations": generations, "seed": seed}}


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
                genetic=SimpleNamespace(
                    generations=3,
                    seed=17,
                    interval_sec=3600,
                    individuals=31,
                    horizon_hours=24,
                    tail_horizon_hours=0,
                    penalties={},
                    workers=None,
                    pin_workers=True,
                    fitness_cache_max_entries=None,
                    terminal_value_mode="AUTO",
                    terminal_value_window_hours=24,
                ),
                genetic0=SimpleNamespace(
                    generations=5, seed=29, horizon_hours=24, individuals=13, penalties={}
                ),
            ),
            prediction=SimpleNamespace(hours=48),
            feedintariff=SimpleNamespace(provider=None, direct_marketing_enabled=False),
            devices=SimpleNamespace(
                batteries=None, electric_vehicles=None, max_home_appliances=0
            ),
            server=SimpleNamespace(verbose=False),
        ),
        prediction=SimpleNamespace(update_data=AsyncMock()),
        adapter=SimpleNamespace(update_data=AsyncMock()),
        set_start_datetime=cls.set_start_datetime,
    )
    # The parameter preparations read the settings (and GENETIC0's the predictions) themselves.
    monkeypatch.setattr(geneticprepare, "get_config", lambda: ems.config)
    # Stand-in requests are already resolved.
    monkeypatch.setattr(
        geneticprepare, "resolve_request", AsyncMock(side_effect=lambda request, *args: request)
    )
    monkeypatch.setattr(genetic0prepare, "get_config", lambda: ems.config)
    monkeypatch.setattr(
        genetic0prepare, "get_prediction", lambda: SimpleNamespace(record_keys=[])
    )
    monkeypatch.setattr(
        genetic0prepare,
        "configured_grid_interpolator_backend",
        lambda: GridInterpolatorBackend.SCIPY,
    )
    # Stand-in results carry their own conversion methods.
    async def optimization_solution(parameters: Any, result: Any) -> Any:
        return await result.optimization_solution()

    monkeypatch.setattr(genetic0convert, "optimization_solution", optimization_solution)
    monkeypatch.setattr(
        genetic0convert,
        "energy_management_plan",
        lambda parameters, result: result.energy_management_plan(),
    )
    # Stand-in requests have no forecasts to derive the simulation's dataclasses from.
    monkeypatch.setattr(
        genetic0prepare.Genetic0Parameters,
        "from_request",
        classmethod(lambda cls, **fields: SimpleNamespace(**fields)),
    )
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
    sentinel_parameters = _stand_in_request()
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
        kwargs.update(_supplied(sentinel_parameters, 7, 43))
        if algorithm == OptimizationAlgorithm.GENETIC:
            kwargs["overrides"]["individuals"] = 11
    run_result = await ems_module.EnergyManagement.run(offline_ems, **kwargs)
    assert run_result is solution
    selected = constructors[selected_name]
    expected_config = getattr(offline_ems.config.optimization, suffix)
    expected_seed = 43 if supplied else expected_config.seed
    expected_generations = 7 if supplied else expected_config.generations
    expected_arguments: dict[str, Any] = {
        "start_hour": expected_hour,
        "parameters": sentinel_parameters,
    }
    # Both get their algorithm settings from the parameters (step 3).
    (settings,) = selected.call_args.args
    assert (settings.seed, settings.generations, settings.verbose) == (
        expected_seed,
        expected_generations,
        False,
    )
    if algorithm == OptimizationAlgorithm.GENETIC:
        expected_arguments["ngen"] = expected_generations
        expected_arguments["individuals"] = 11 if supplied else expected_config.individuals
        selected.return_value.optimize_ems.assert_called_once_with(**expected_arguments)
    else:
        # GENETIC0 runs from its parameters, which carry the request and the start hour.
        (parameters,) = selected.return_value.optimize_ems.call_args.args
        assert parameters.request is sentinel_parameters
        assert parameters.start_hour == expected_hour
    constructors[other_name].assert_not_called()
    preparers[other_name].assert_not_awaited()
    if supplied and algorithm == OptimizationAlgorithm.GENETIC:
        # GENETIC completes the supplied request.
        preparers[selected_name].assert_awaited_once_with(
            sentinel_parameters, ANY, ANY, None
        )
    elif supplied:
        preparers[selected_name].assert_not_awaited()
    else:
        _assert_prepared_without_request(selected_name, preparers[selected_name])
    conversion_arguments = _conversion_arguments(selected_name)
    solution.optimization_solution.assert_awaited_once_with(**conversion_arguments)
    solution.energy_management_plan.assert_called_once_with(**conversion_arguments)
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
@pytest.mark.parametrize("start_hour", [None, 11])
async def test_legacy_optimize_explicitly_uses_genetic0(monkeypatch, start_hour):
    """Even with GENETIC configured, /optimize must never become the new optimizer.

    The GENETIC0 router gets its run trigger from the server's placeholder override.
    """
    from httpx import ASGITransport, AsyncClient

    from akkudoktoreos.server import eos

    fake = SimpleNamespace(
        run=AsyncMock(return_value=None), optimizer_run=Mock(return_value=None)
    )
    monkeypatch.setattr(eos, "get_ems", lambda: fake)
    body = {
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
    query = "ngen=2" + ("" if start_hour is None else f"&start_hour={start_hour}")
    async with AsyncClient(
        transport=ASGITransport(app=eos.app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.post(f"/optimize?{query}", json=body)
    assert response.status_code == 404
    assert response.json()["title"] == "Optimization solution retrieval failed"
    kwargs = fake.run.await_args.kwargs
    assert kwargs["mode"] == EnergyManagementMode.OPTIMIZATION
    assert kwargs["algorithm"] == OptimizationAlgorithm.GENETIC0
    assert kwargs["request"].model_dump() == Genetic0Request.model_validate(body).model_dump()
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

    from akkudoktoreos.optimization.genetic0.genetic0api import Genetic0Solution
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
    supplied_parameters = _supplied(_stand_in_request(), 3, 17)
    result = await cls.run(
        offline_ems,
        algorithm=OptimizationAlgorithm(prefix.upper()),
        **supplied_parameters,
    )
    assert result is None
    conversion.assert_awaited_once_with(**_conversion_arguments(prefix))
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

    from httpx import ASGITransport, AsyncClient
    from test_genetic0optimize import result_of

    from akkudoktoreos.optimization.genetic0.genetic0api import Genetic0Solution
    from akkudoktoreos.server import eos

    data = json.loads(
        (Path(__file__).parent / "testdata/genetic0/optimize_result_1.json").read_text()
    )
    produced = Genetic0Solution.model_validate(data)
    fake = SimpleNamespace(
        run=AsyncMock(return_value=result_of(produced)),
        optimizer_run=Mock(side_effect=AssertionError("must use this run's result")),
    )
    monkeypatch.setattr(eos, "get_ems", lambda: fake)
    async with AsyncClient(
        transport=ASGITransport(app=eos.app), base_url="http://test"
    ) as client:
        response = await client.post("/optimize?ngen=1", json=data["parameters"])
    assert response.status_code == 200
    assert response.json()["start_solution"] == produced.start_solution
    assert response.json()["result"]["total_balance"] == produced.result.total_balance
    fake.optimizer_run.assert_not_called()


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
    prepare = AsyncMock(return_value=SimpleNamespace(dishwasher=None))
    _patch_prepare(monkeypatch, "Genetic0", prepare)
    for _ in range(3):
        await cls.run(offline_ems, algorithm=OptimizationAlgorithm.GENETIC0)
    assert cls.optimizer_result("GENETIC0") is second
    # Without a stored run there is no warm start.
    monkeypatch.setattr(cls, "_optimizer_runs", {})
    await cls.run(offline_ems, algorithm=OptimizationAlgorithm.GENETIC0)
    assert [_warm_start_of(call) for call in prepare.await_args_list] == [
        None,
        [1.0, 2.0],
        [1.0, 2.0],  # the failed second run kept the first as warm start
        None,
    ]
