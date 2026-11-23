"""The interface every optimizer implements (ADR 0006).

The energy management drives an optimizer only through this interface and the optimizer
registry (ADR 0005): it prepares the optimization parameters once, runs the optimizer on them and
converts the result into the optimization solution and the energy management plan.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Generic, Optional, Protocol, TypeVar

from akkudoktoreos.core.emplan import EnergyManagementPlan
from akkudoktoreos.optimization.optimization import OptimizationSolution
from akkudoktoreos.utils.datetimeutil import DateTime

ParametersT = TypeVar("ParametersT")
RequestT_contra = TypeVar("RequestT_contra", contravariant=True)
ResultT = TypeVar("ResultT")


class Optimizer(Protocol[ParametersT, RequestT_contra, ResultT]):
    """An optimizer, driven by the energy management.

    ``ParametersT`` are the optimizer's optimization parameters, ``RequestT_contra`` its optimization
    request from clients and ``ResultT`` its optimization result.
    """

    name: str

    async def prepare(
        self,
        start_datetime: DateTime,
        observation_datetime: DateTime,
        warm_start: Optional[ResultT],
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> ParametersT:
        """Prepare the optimization parameters from configuration, predictions and measurements.

        Args:
            start_datetime: Start of the optimization run.
            observation_datetime: Moment of the run that measurements refer to (the start is
                this moment rounded down to the optimizer's slot).
            warm_start: Result of the optimizer's previous run, if any.
            overrides: Run-time overrides of settings (e.g. ``{"generations": 10}``). Keys the
                optimizer does not know are logged as errors and ignored.

        Returns:
            Complete optimization parameters for one run.
        """
        ...

    async def prepare_from_request(
        self,
        request: RequestT_contra,
        start_datetime: DateTime,
        observation_datetime: DateTime,
        warm_start: Optional[ResultT],
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> ParametersT:
        """Complete a client's optimization request into optimization parameters.

        Args:
            request: The optimization request of a client.
            start_datetime: Start of the optimization run.
            observation_datetime: Moment of the run that measurements refer to.
            warm_start: Result of the optimizer's previous run, if any.
            overrides: Run-time overrides of settings, as for ``prepare()``.

        Returns:
            Complete optimization parameters for one run.
        """
        ...

    def run(self, parameters: ParametersT) -> ResultT:
        """Run the optimization; CPU-bound and deterministic for the same parameters.

        Args:
            parameters: The optimization parameters of this run.

        Returns:
            The optimization result.
        """
        ...

    async def convert(
        self, parameters: ParametersT, result: ResultT
    ) -> tuple[OptimizationSolution, EnergyManagementPlan]:
        """Convert the result of a run into the optimization solution and the plan.

        Args:
            parameters: The optimization parameters of the run.
            result: The optimization result of the run.

        Returns:
            The optimization solution and the energy management plan.
        """
        ...


@dataclass(frozen=True, slots=True)
class OptimizerRun(Generic[ParametersT, ResultT]):
    """The optimization parameters and the result of an optimizer's latest run."""

    parameters: ParametersT
    result: ResultT
