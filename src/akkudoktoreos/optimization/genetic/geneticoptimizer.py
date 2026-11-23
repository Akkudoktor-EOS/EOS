"""GENETIC behind the optimizer interface (ADR 0006).

Transitional (migration step 1): runs and converts with today's ``GeneticOptimization`` and
``GeneticSolution``; later steps replace them without changing results (ADR 0008).
"""

from collections.abc import Mapping
from typing import Any, Optional

from akkudoktoreos.core.emplan import EnergyManagementPlan
from akkudoktoreos.optimization.genetic import geneticprepare
from akkudoktoreos.optimization.genetic.configrequest import ConfigOptimizationRequest
from akkudoktoreos.optimization.genetic.geneticparameters import GeneticParameters
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.optimization.optimization import OptimizationSolution
from akkudoktoreos.optimization.optimizer import OptimizerApi, RunClock
from akkudoktoreos.utils.datetimeutil import DateTime


class GeneticOptimizer:
    """The GENETIC optimizer."""

    name = "GENETIC"

    def run_clock(self) -> RunClock:
        """See ``Optimizer.run_clock()``."""
        return geneticprepare.run_clock()

    async def prepare(
        self,
        start_datetime: DateTime,
        observation_datetime: DateTime,
        warm_start: Optional[GeneticSolution],
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> GeneticParameters:
        """See ``Optimizer.prepare()``."""
        return await geneticprepare.prepare(start_datetime, warm_start, overrides)

    async def prepare_from_request(
        self,
        request: ConfigOptimizationRequest,
        start_datetime: DateTime,
        observation_datetime: DateTime,
        warm_start: Optional[GeneticSolution],
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> GeneticParameters:
        """See ``Optimizer.prepare_from_request()``."""
        return await geneticprepare.prepare_from_request(
            request, start_datetime, warm_start, overrides
        )

    def run(self, parameters: GeneticParameters) -> GeneticSolution:
        """See ``Optimizer.run()``."""
        # Imported on first use: the simulation is only needed for a run.
        from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization

        optimization = GeneticOptimization(
            verbose=parameters.verbose,
            fixed_seed=parameters.seed,
        )
        return optimization.optimize_ems(
            start_hour=parameters.start_hour,
            parameters=parameters.request,
            ngen=parameters.generations,
            individuals=parameters.individuals,
        )

    def api(self) -> Optional[OptimizerApi]:
        """See ``Optimizer.api()``: none yet, the server serves GENETIC until step 6."""
        return None

    async def convert(
        self, parameters: GeneticParameters, result: GeneticSolution
    ) -> tuple[OptimizationSolution, EnergyManagementPlan]:
        """See ``Optimizer.convert()``."""
        optimization_solution = await result.optimization_solution()
        plan = result.energy_management_plan()
        return optimization_solution, plan
