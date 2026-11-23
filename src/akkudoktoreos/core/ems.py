import json
import threading
import traceback
import urllib.request
from asyncio import Lock, get_running_loop
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from typing import Any, ClassVar, Optional, cast

from loguru import logger
from pydantic import computed_field

from akkudoktoreos.core.cache import CacheEnergyManagementStore
from akkudoktoreos.core.coreabc import (
    AdapterMixin,
    ConfigMixin,
    PredictionMixin,
    SingletonMixin,
)
from akkudoktoreos.core.emplan import EnergyManagementPlan
from akkudoktoreos.core.emsettings import EnergyManagementMode
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.optimization.genetic.configrequest import ConfigOptimizationRequest
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.optimization.optimization import (
    OptimizationAlgorithm,
    OptimizationSolution,
)
from akkudoktoreos.optimization.optimizer import OptimizerRun
from akkudoktoreos.optimization.optimizerregistry import get_optimizer
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime

# The executor to execute the CPU heavy energy management run
executor = ThreadPoolExecutor(max_workers=1)


class EnergyManagementStage(StrEnum):
    """Enumeration of the main stages in the energy management lifecycle."""

    IDLE = "IDLE"
    DATA_ACQUISITION = "DATA_AQUISITION"
    FORECAST_RETRIEVAL = "FORECAST_RETRIEVAL"
    OPTIMIZATION = "OPTIMIZATION"
    CONTROL_DISPATCH = "CONTROL_DISPATCH"


async def ems_manage_energy() -> None:
    """Repeating task for managing energy.

    This task should be executed by the server regularly
    to ensure proper energy management.
    """
    await EnergyManagement().run()


NOTIFY_TIMEOUT_SECONDS = 5.0
_notify_failing = False


def _post_notification(url: str, event: dict) -> None:
    """POST one event; log a failure once per failure streak, not every run."""
    global _notify_failing
    # The scheme is restricted to http(s) by the notify_url setting.
    request = urllib.request.Request(  # noqa: S310
        url,
        data=json.dumps(event).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=NOTIFY_TIMEOUT_SECONDS) as response:  # noqa: S310
            response.read()
    except Exception as exc:
        if not _notify_failing:
            logger.warning("Optimization notification to {} failed: {}", url, exc)
        _notify_failing = True
        return
    if _notify_failing:
        logger.info("Optimization notification to {} works again.", url)
    _notify_failing = False


def notify_optimization_completed(url: Optional[str], event: dict) -> Optional[threading.Thread]:
    """Tell ``ems.notify_url`` that a new solution is available.

    Runs in a background thread so a slow or unreachable receiver never delays
    the energy management run.
    """
    if not url:
        return None
    thread = threading.Thread(
        target=_post_notification, args=(url, event), name="eos-notify", daemon=True
    )
    thread.start()
    return thread


class EnergyManagement(
    SingletonMixin, ConfigMixin, PredictionMixin, AdapterMixin, PydanticBaseModel
):
    """Energy management."""

    # Start datetime.
    _start_datetime: ClassVar[Optional[DateTime]] = None
    _observation_datetime: ClassVar[Optional[DateTime]] = None

    # last run datetime. Used by energy management task
    _last_run_datetime: ClassVar[Optional[DateTime]] = None

    # Current energy management stage
    _stage: ClassVar[EnergyManagementStage] = EnergyManagementStage.IDLE

    # energy management plan of latest energy management run with optimization
    _plan: ClassVar[Optional[EnergyManagementPlan]] = None

    # opimization solution of the latest energy management run
    _optimization_solution: ClassVar[Optional[OptimizationSolution]] = None

    # Solution of the genetic algorithm of latest energy management run with optimization
    # For classic API
    _genetic_solution: ClassVar[Optional[GeneticSolution]] = None

    # Parameters and result of the latest run per optimizer of the registry, by optimizer name.
    # The result is the warm start of the optimizer's next run.
    _optimizer_runs: ClassVar[dict[str, OptimizerRun[Any, Any]]] = {}

    # energy management lock (for energy management run)
    _run_lock: ClassVar[Lock] = Lock()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def start_datetime(self) -> DateTime:
        """The starting datetime of the current or latest energy management."""
        if EnergyManagement._start_datetime is None:
            return EnergyManagement.set_start_datetime()
        return EnergyManagement._start_datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def last_run_datetime(self) -> Optional[DateTime]:
        """The datetime the last energy management was run."""
        return EnergyManagement._last_run_datetime

    @property
    def observation_datetime(self) -> DateTime:
        """Unrounded run time used to assess the freshness of runtime measurements."""
        return EnergyManagement._observation_datetime or self.start_datetime

    @classmethod
    def set_start_datetime(
        cls, start_datetime: Optional[DateTime] = None, interval_seconds: Optional[int] = None
    ) -> DateTime:
        """Set the start datetime for the next energy management run.

        If no datetime is provided, the current datetime is used.

        The start is rounded down to the selected algorithm slot using elapsed
        time, retaining the correct offset during repeated DST hours.

        Args:
            start_datetime (Optional[DateTime]): The datetime to set as the start.
                If None, the current datetime is used.
            interval_seconds: Explicit slot duration; otherwise use the configured algorithm.

        Returns:
            DateTime: The adjusted start datetime.
        """
        if start_datetime is None:
            start_datetime = to_datetime()
        cls._observation_datetime = start_datetime
        if interval_seconds is None:
            interval_seconds = (
                cls.config.optimization.genetic.interval_sec
                if cls.config.optimization.algorithm == OptimizationAlgorithm.GENETIC
                else 3600
            )
        if interval_seconds not in (900, 3600):
            raise ValueError("Optimization slots must be 900 or 3600 seconds.")
        midnight = start_datetime.start_of("day").int_timestamp
        elapsed = start_datetime.int_timestamp - midnight
        cls._start_datetime = to_datetime(
            midnight + (elapsed // interval_seconds) * interval_seconds,
            in_timezone=start_datetime.timezone_name,
        )
        return cls._start_datetime

    @classmethod
    def stage(cls) -> EnergyManagementStage:
        """Get the the stage of the energy management.

        Returns:
            EnergyManagementStage: The current stage of energy management.
        """
        return cls._stage

    @classmethod
    def plan(cls) -> Optional[EnergyManagementPlan]:
        """Get the latest energy management plan.

        Returns:
            Optional[EnergyManagementPlan]: The latest energy management plan or None.
        """
        return cls._plan

    @classmethod
    def optimization_solution(cls) -> Optional[OptimizationSolution]:
        """Get the latest optimization solution.

        Returns:
            Optional[OptimizationSolution]: The latest optimization solution.
        """
        return cls._optimization_solution

    @classmethod
    def genetic_solution(cls) -> Optional[GeneticSolution]:
        """Get the latest solution of the genetic algorithm.

        Returns:
            Optional[GeneticSolution]: The latest solution of the genetic algorithm.
        """
        return cls._genetic_solution

    @classmethod
    def optimizer_run(cls, name: str) -> Optional[OptimizerRun[Any, Any]]:
        """Get the parameters and result of the latest successful run of an optimizer.

        Args:
            name: The optimizer's name, e.g. ``"GENETIC0"``.

        Returns:
            The optimizer's latest run, or None if it has not run yet.
        """
        return cls._optimizer_runs.get(name)

    @classmethod
    def optimizer_result(cls, name: str) -> Optional[Any]:
        """Get the result of the latest successful run of an optimizer of the registry.

        Args:
            name: The optimizer's name, e.g. ``"GENETIC0"``.

        Returns:
            The optimizer's native optimization result, or None if it has not run yet.
        """
        run = cls._optimizer_runs.get(name)
        return None if run is None else run.result

    async def run(
        self,
        start_datetime: Optional[DateTime] = None,
        mode: Optional[EnergyManagementMode] = None,
        algorithm: Optional[OptimizationAlgorithm] = None,
        genetic_parameters: Optional[
            GeneticOptimizationParameters | ConfigOptimizationRequest
        ] = None,
        genetic_generations: Optional[int] = None,
        genetic_individuals: Optional[int] = None,
        genetic_seed: Optional[int] = None,
        request: Optional[Any] = None,
        overrides: Optional[Mapping[str, Any]] = None,
        force_enable: Optional[bool] = False,
        force_update: Optional[bool] = False,
    ) -> Optional[Any]:
        """Run the energy management.

        This method initializes the energy management run by setting its
        start datetime, updating predictions, and optionally starting
        optimization depending on the selected mode or configuration.

        Args:
            start_datetime (DateTime): The starting timestamp of the energy management run.
            mode (EnergyManagementMode): The management mode to use. Must be one of:
                - "OPTIMIZATION": Runs the optimization process.
                - "PREDICTION": Updates the forecast without optimization.
                - "DISABLED": Does not run.

                Defaults to the mode defined in the current configuration.
            algorithm (OptimizationAlgorithm, optional):
                The algorithm to use. Must be one of:
                - "GENETIC": Optimization uses the `GENETIC` optimization algorithm.
                - "GENETIC0": Optimization uses the `GENETIC0` optimization algorithm.

                Defaults to the algorithm defined in the current configuration.
            genetic_parameters (GeneticOptimizationParameters, optional): The
                parameter set for the `GENETIC` algorithm. If not provided, it will
                be constructed based on the current configuration and predictions.
            genetic_generations (int, optional): The number of generations for the
                `GENETIC` algorithm. Defaults to the algorithm's internal default (400)
                if not specified.
            genetic_individuals (int, optional): Population size override for this run.
            genetic_seed (int, optional): The seed for the `GENETIC` algorithm. Defaults
                to the algorithm's internal random seed if not specified.
            request (optional): An optimization request for an optimizer of the registry
                (e.g. `GENETIC0`), completed by its `prepare_from_request()`. If not provided,
                the optimizer prepares its parameters from configuration and predictions.
            overrides (Mapping, optional): Run-time overrides of the settings of an optimizer
                of the registry, e.g. ``{"generations": 10, "seed": 42}``.
            force_enable (bool, optional): If True, bypasses any disabled state
                to force the update process. This is mostly applicable to
                prediction providers.
            force_update (bool, optional): If True, forces data to be refreshed
                even if a cached version is still valid.

        Returns:
            The native optimization solution produced by this run, or None when
            no optimization completed. Previous successful results remain available
            through the solution getters after an aborted run.
        """
        async with EnergyManagement._run_lock:
            if mode is None:
                mode = self.config.ems.mode

            if mode not in EnergyManagementMode._value2member_map_:
                raise ValueError(f"Unknown energy management mode {mode}.")
            if mode == EnergyManagementMode.DISABLED:
                logger.info("Energy management run disabled.")
                return None

            logger.info("Starting energy management run.")

            # --- Data Aquisition ---
            EnergyManagement._stage = EnergyManagementStage.DATA_ACQUISITION

            # Remember/ set the start datetime of this energy management run.
            # None leads to current time as start datetime
            if algorithm is None:
                algorithm = self.config.optimization.algorithm
            if start_datetime is None and algorithm == OptimizationAlgorithm.GENETIC:
                # Consumer windows and midnight-based forecasts use the site clock,
                # which can differ from the server's local timezone.
                start_datetime = to_datetime(in_timezone=self.config.general.timezone)
            self.set_start_datetime(
                start_datetime,
                interval_seconds=(
                    self.config.optimization.genetic.interval_sec
                    if algorithm == OptimizationAlgorithm.GENETIC
                    else 3600
                ),
            )

            # Throw away any memory cached results of the last energy management run.
            CacheEnergyManagementStore().clear()

            # --- Adapter update     ---
            try:
                await self.adapter.update_data(force_enable)
            except Exception as e:
                trace = "".join(traceback.TracebackException.from_exception(e).format())
                error_msg = (
                    f"Adapter update failed - phase {EnergyManagement._stage}:\n{e}\n{trace}"
                )
                logger.error(error_msg)

            # --- Prediction ---
            EnergyManagement._stage = EnergyManagementStage.FORECAST_RETRIEVAL

            # Update the predictions
            logger.info("Starting energy management prediction update.")
            try:
                await self.prediction.update_data(
                    force_enable=force_enable, force_update=force_update
                )
            except Exception as e:
                trace = "".join(traceback.TracebackException.from_exception(e).format())
                error_msg = (
                    f"Prediction update failed - phase {EnergyManagement._stage}:\n{e}\n{trace}"
                )
                logger.error(error_msg)

            if mode == EnergyManagementMode.PREDICTION:
                logger.info("Energy management run done (predictions updated)")
                EnergyManagement._stage = EnergyManagementStage.IDLE
                return None

            # --- Optimization ---
            EnergyManagement._stage = EnergyManagementStage.OPTIMIZATION
            optimization_start = to_datetime()
            logger.info("Starting energy management optimization.")

            if algorithm is None:
                algorithm = self.config.optimization.algorithm

            run_solution: Any
            optimizer = (
                None if algorithm == OptimizationAlgorithm.GENETIC else get_optimizer(algorithm)
            )

            # --- GENETIC algorithm ---
            if algorithm == OptimizationAlgorithm.GENETIC:
                # Prepare optimization parameters
                # This also creates default configurations for missing values and updates the predictions
                logger.info(f"{algorithm}: Starting optimzation parameter preparation.")
                try:
                    if isinstance(genetic_parameters, ConfigOptimizationRequest):
                        genetic_parameters = await genetic_parameters.resolve()
                    elif genetic_parameters is None:
                        genetic_parameters = await GeneticOptimizationParameters.prepare()
                    if genetic_parameters is None:
                        logger.error("GENETIC: Parameter preparation failed; canceling run.")
                        EnergyManagement._stage = EnergyManagementStage.IDLE
                        return None
                except Exception:
                    logger.exception("GENETIC: Parameter preparation failed.")
                    EnergyManagement._stage = EnergyManagementStage.IDLE
                    return None

                # Take values from config if not given
                if genetic_generations is None:
                    genetic_generations = self.config.optimization.genetic.generations
                if genetic_seed is None:
                    genetic_seed = self.config.optimization.genetic.seed

                if EnergyManagement._start_datetime is None:  # Make mypy happy - already set by us
                    raise RuntimeError(f"{algorithm}: Start datetime not set.")

                # --- Optimization (CPU-bound → MUST offload) ---
                try:
                    genetic_optimization = GeneticOptimization(
                        verbose=bool(self.config.server.verbose),
                        fixed_seed=genetic_seed,
                    )

                    loop = get_running_loop()
                    start_hour = EnergyManagement._start_datetime.hour
                    genetic_solution = await loop.run_in_executor(
                        None,
                        lambda: genetic_optimization.optimize_ems(
                            start_hour=start_hour,
                            parameters=cast(
                                GeneticOptimizationParameters, genetic_parameters
                            ),  # cast for mypy
                            ngen=genetic_generations,
                            individuals=genetic_individuals,
                        ),
                    )

                    # Build all representations before publishing any of them.
                    optimization_solution = await genetic_solution.optimization_solution()
                    plan = genetic_solution.energy_management_plan()

                except Exception:
                    logger.exception(f"{algorithm}: Energy management optimization failed.")
                    EnergyManagement._stage = EnergyManagementStage.IDLE
                    return None

                # Publish a consistent set without yielding to another coroutine.
                EnergyManagement._genetic_solution = genetic_solution
                EnergyManagement._optimization_solution = optimization_solution
                EnergyManagement._plan = plan
                run_solution = genetic_solution

                logger.debug(
                    "{}: Energy management genetic solution:\n{}",
                    algorithm,
                    EnergyManagement._genetic_solution,
                )

            # --- Optimizers of the registry ---
            elif optimizer is not None:
                # Prepare optimization parameters
                logger.info(f"{algorithm}: Starting optimzation parameter preparation.")
                if EnergyManagement._start_datetime is None:  # Make mypy happy - already set by us
                    raise RuntimeError(f"{algorithm}: Start datetime not set.")
                start = EnergyManagement._start_datetime
                observation = EnergyManagement._observation_datetime or start
                previous = EnergyManagement._optimizer_runs.get(optimizer.name)
                warm_start = None if previous is None else previous.result
                try:
                    if request is None:
                        parameters = await optimizer.prepare(
                            start, observation, warm_start, overrides
                        )
                    else:
                        parameters = await optimizer.prepare_from_request(
                            request, start, observation, warm_start, overrides
                        )
                except Exception:
                    # Until every optimizer's preparation never fails (ADR 0006).
                    logger.exception(
                        f"{algorithm}: Energy management run canceled. "
                        "Could not prepare optimisation parameters."
                    )
                    EnergyManagement._stage = EnergyManagementStage.IDLE
                    return None

                # --- Optimization (CPU-bound → MUST offload) ---
                try:
                    loop = get_running_loop()
                    result = await loop.run_in_executor(None, optimizer.run, parameters)

                    # Build all representations before publishing any of them.
                    optimization_solution, plan = await optimizer.convert(parameters, result)

                except Exception:
                    logger.exception(f"{algorithm}: Energy management optimization failed.")
                    EnergyManagement._stage = EnergyManagementStage.IDLE
                    return None

                # Publish a consistent set without yielding to another coroutine.
                EnergyManagement._optimizer_runs[optimizer.name] = OptimizerRun(
                    parameters=parameters, result=result
                )
                EnergyManagement._optimization_solution = optimization_solution
                EnergyManagement._plan = plan
                run_solution = result

                logger.debug("{}: Energy management optimizer result:\n{}", algorithm, result)

            else:
                logger.error(f"Unknown optimization algorithm: '{algorithm}'. Skipping.")
                EnergyManagement._stage = EnergyManagementStage.IDLE
                return None

            optimization_duration = to_datetime() - optimization_start
            logger.info(
                "{}: Energy management optimization completed in {:.1f} seconds.",
                algorithm,
                optimization_duration.total_seconds(),
            )

            logger.debug(
                "{}: Energy management optimization solution:\n{}",
                algorithm,
                EnergyManagement._optimization_solution,
            )
            logger.debug("{}: Energy management plan:\n{}", algorithm, EnergyManagement._plan)

            logger.info("{}: Energy management run done (optimization updated)", algorithm)
            notify_optimization_completed(
                getattr(self.config.ems, "notify_url", None),
                {
                    "event": "optimization_completed",
                    "algorithm": str(algorithm),
                    "completed_at": to_datetime().to_iso8601_string(),
                    "duration_s": round(optimization_duration.total_seconds(), 1),
                },
            )

            # --- Dispatch control by adapters ---
            EnergyManagement._stage = EnergyManagementStage.CONTROL_DISPATCH

            # Dispatch (sync → optionally offload)
            try:
                await self.adapter.update_data(force_enable)
            except Exception as e:
                trace = "".join(traceback.TracebackException.from_exception(e).format())
                error_msg = (
                    f"Adapter update failed - phase {EnergyManagement._stage}:\n{e}\n{trace}"
                )
                logger.error(error_msg)

            # --- Idle ---
            # Remember energy run datetime.
            EnergyManagement._last_run_datetime = to_datetime()

            # energy management run finished
            EnergyManagement._stage = EnergyManagementStage.IDLE
            return run_solution
