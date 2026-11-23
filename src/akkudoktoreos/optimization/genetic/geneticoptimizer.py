"""GENETIC behind the optimizer interface (ADR 0006).

Transitional: the result is still the Pydantic ``GeneticSolution``; step 6 replaces it without
changing results (ADR 0008).
"""

from collections.abc import Mapping
from typing import Any, Optional

from akkudoktoreos.core.emplan import EnergyManagementPlan
from akkudoktoreos.optimization.genetic import geneticconvert, geneticprepare
from akkudoktoreos.optimization.genetic.geneticparameters import GeneticParameters
from akkudoktoreos.optimization.genetic.geneticrequest import GeneticRequest
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
        return await geneticprepare.prepare(
            start_datetime, observation_datetime, warm_start, overrides
        )

    async def prepare_from_request(
        self,
        request: GeneticRequest,
        start_datetime: DateTime,
        observation_datetime: DateTime,
        warm_start: Optional[GeneticSolution],
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> GeneticParameters:
        """See ``Optimizer.prepare_from_request()``."""
        return await geneticprepare.prepare_from_request(
            request, start_datetime, observation_datetime, warm_start, overrides
        )

    def run(self, parameters: GeneticParameters) -> GeneticSolution:
        """See ``Optimizer.run()``."""
        # Imported on first use: the simulation is only needed for a run.
        from akkudoktoreos.optimization.genetic.geneticalgorithm import (
            GeneticOptimization,
        )

        optimization = GeneticOptimization(parameters.settings)
        return optimization.optimize_ems(
            start_hour=parameters.start_hour,
            parameters=parameters.request,
            ngen=parameters.settings.generations,
            individuals=parameters.settings.individuals,
        )

    def api(self) -> Optional[OptimizerApi]:
        """See ``Optimizer.api()``: none yet, the server serves GENETIC until step 6."""
        return None

    async def convert(
        self, parameters: GeneticParameters, result: GeneticSolution
    ) -> tuple[OptimizationSolution, EnergyManagementPlan]:
        """See ``Optimizer.convert()``."""
        return await geneticconvert.convert(parameters, result)
