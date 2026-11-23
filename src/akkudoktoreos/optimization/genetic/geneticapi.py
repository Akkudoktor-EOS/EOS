"""REST API of GENETIC (ADR 0005, 0011): its router.

The router fetches nothing itself: the generic server fills the placeholder dependencies
``last_run()`` and ``run()`` through ``app.dependency_overrides``.

Transitional (migration step 6a): the responses are still the Pydantic ``GeneticSolution`` the
algorithm returns; step 6b makes the result a dataclass and builds the response models here.
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, Body, Depends, Request, Response

from akkudoktoreos.optimization.genetic.geneticparameters import GeneticParameters
from akkudoktoreos.optimization.genetic.geneticrequest import GeneticRequest
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.optimization.optimizer import (
    OptimizerApi,
    OptimizerProblem,
    OptimizerRun,
    RunTrigger,
)

router = APIRouter()


def last_run() -> Optional[OptimizerRun[GeneticParameters, GeneticSolution]]:
    """Placeholder dependency: the latest GENETIC run, filled by the server.

    Raises:
        NotImplementedError: The server did not fill the placeholder.
    """
    raise NotImplementedError("Filled by the server through app.dependency_overrides.")


def run() -> RunTrigger:
    """Placeholder dependency: starts a GENETIC run, filled by the server.

    Raises:
        NotImplementedError: The server did not fill the placeholder.
    """
    raise NotImplementedError("Filled by the server through app.dependency_overrides.")


def api() -> OptimizerApi:
    """The REST API of GENETIC for the server.

    Returns:
        The router and its placeholder dependencies.
    """
    return OptimizerApi(router=router, last_run=last_run, run=run)


@router.get("/v1/energy-management/optimization/solution/GENETIC", tags=["energy-management"])
async def fastapi_energy_management_optimization_solution_genetic_get(
    latest: Optional[OptimizerRun[GeneticParameters, GeneticSolution]] = Depends(last_run),
) -> GeneticSolution:
    """Get the latest GENETIC specific solution of the optimization."""
    if latest is None:
        raise OptimizerProblem(
            status=404,
            title="Algorithm specific optimization solution retrieval failed",
            detail=(
                "Can not get the 'GENETIC' optimization solution.\n"
                "Did you configure automatic 'GENETIC' optimization?\n"
                "Did you run the optimization/ energy management?"
            ),
        )
    return latest.result


@router.get(
    "/v1/energy-management/optimization/solution/GENETIC/pdf",
    tags=["energy-management"],
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def fastapi_energy_management_optimization_solution_genetic_pdf_get(
    latest: Optional[OptimizerRun[GeneticParameters, GeneticSolution]] = Depends(last_run),
) -> Response:
    """Render the retained GENETIC result without rerunning optimization.

    Rendering runs outside the event loop. Copy the result before offloading;
    its recorded timestamp, interval and inputs own the report's time grid.
    The legacy /visualization_results.pdf route continues to serve GENETIC0.
    """
    if latest is None:
        raise OptimizerProblem(
            status=404,
            title="Optimization solution report retrieval failed",
            detail="Can not get the 'GENETIC' optimization solution.",
        )
    snapshot = latest.result.model_copy(deep=True)
    # matplotlib is only needed for the PDF report; import it on first use.
    from akkudoktoreos.optimization.genetic import geneticvisualize

    pdf = await asyncio.to_thread(
        geneticvisualize.genetic_prepare_visualize, parameters=latest.parameters, result=snapshot
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="optimization-genetic.pdf"'},
    )


@router.post("/v1/optimize", tags=["optimize"])
async def fastapi_optimize_config(
    request: Request,
    parameters: GeneticRequest = Body(default_factory=GeneticRequest),
    trigger: RunTrigger = Depends(run),
) -> GeneticSolution:
    """Optimize GENETIC using configured devices and optional fresh runtime inputs.

    Static settings belong in configuration; query overrides are rejected.
    Forecast arrays start at local
    midnight and contain Wh per configured GENETIC slot; prices are currency/Wh.
    An empty body uses configured providers. Explicit SoC values take precedence over
    fresh measured states of charge; missing usable measurements default to an assumed
    initial SoC of 0% without blocking a plan.
    The deprecated /optimize endpoint continues to run hourly GENETIC0.
    """
    if request.query_params:
        raise OptimizerProblem(
            status=422,
            title="HTTP Error",
            detail="Configure optimization settings; query overrides are not supported.",
        )
    solution = await trigger(request=parameters)
    if solution is None:
        raise OptimizerProblem(
            status=503,
            title="GENETIC optimization failed",
            detail="No new solution was produced. Check configured devices and forecast coverage.",
        )
    if not isinstance(solution, GeneticSolution):
        raise OptimizerProblem(
            status=500,
            title="Unexpected optimization algorithm result",
            detail="GENETIC did not return its native solution type.",
        )
    return solution
