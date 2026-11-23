"""GENETIC0 behind the optimizer interface (ADR 0006).

Transitional (migration step 1): runs and converts with today's ``Genetic0Optimization`` and
``Genetic0Solution``; later steps replace them without changing results (ADR 0008).
"""

from collections.abc import Mapping
from typing import Any, Optional

from akkudoktoreos.core.emplan import EnergyManagementPlan
from akkudoktoreos.optimization.genetic0 import genetic0prepare
from akkudoktoreos.optimization.genetic0.genetic0parameters import Genetic0Parameters
from akkudoktoreos.optimization.genetic0.genetic0params import (
    Genetic0OptimizationParameters,
)
from akkudoktoreos.optimization.genetic0.genetic0solution import Genetic0Solution
from akkudoktoreos.optimization.optimization import OptimizationSolution
from akkudoktoreos.utils.datetimeutil import DateTime


class Genetic0Optimizer:
    """The GENETIC0 optimizer."""

    name = "GENETIC0"

    async def prepare(
        self,
        start_datetime: DateTime,
        warm_start: Optional[Genetic0Solution],
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> Genetic0Parameters:
        """See ``Optimizer.prepare()``."""
        return await genetic0prepare.prepare(start_datetime, warm_start, overrides)

    async def prepare_from_request(
        self,
        request: Genetic0OptimizationParameters,
        start_datetime: DateTime,
        warm_start: Optional[Genetic0Solution],
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> Genetic0Parameters:
        """See ``Optimizer.prepare_from_request()``."""
        return await genetic0prepare.prepare_from_request(
            request, start_datetime, warm_start, overrides
        )

    def run(self, parameters: Genetic0Parameters) -> Genetic0Solution:
        """See ``Optimizer.run()``."""
        # Imported on first use: the simulation is only needed for a run.
        from akkudoktoreos.optimization.genetic0.genetic0 import Genetic0Optimization

        optimization = Genetic0Optimization(
            verbose=parameters.verbose,
            fixed_seed=parameters.seed,
        )
        return optimization.optimize_ems(
            start_hour=parameters.start_hour,
            parameters=parameters.request,
            ngen=parameters.generations,
        )

    async def convert(
        self, parameters: Genetic0Parameters, result: Genetic0Solution
    ) -> tuple[OptimizationSolution, EnergyManagementPlan]:
        """See ``Optimizer.convert()``."""
        optimization_solution = await result.optimization_solution()
        plan = result.energy_management_plan()
        return optimization_solution, plan
