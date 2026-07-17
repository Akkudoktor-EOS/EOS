import traceback
from asyncio import Lock, get_running_loop
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from typing import ClassVar, Optional, cast

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
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.optimization.optimization import OptimizationSolution
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


class EnergyManagement(
    SingletonMixin, ConfigMixin, PredictionMixin, AdapterMixin, PydanticBaseModel
):
    """Energy management."""

    # Start datetime.
    _start_datetime: ClassVar[Optional[DateTime]] = None

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

    # energy management lock (for energy management run)
    _run_lock: ClassVar[Lock] = Lock()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def start_datetime(self) -> DateTime:
        """The starting datetime of the current or latest energy management."""
        if EnergyManagement._start_datetime is None:
            EnergyManagement.set_start_datetime()
        return EnergyManagement._start_datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def last_run_datetime(self) -> Optional[DateTime]:
        """The datetime the last energy management was run."""
        return EnergyManagement._last_run_datetime

    @classmethod
    def set_start_datetime(cls, start_datetime: Optional[DateTime] = None) -> DateTime:
        """Set the start datetime for the next energy management run.

        If no datetime is provided, the current datetime is used.

        The start datetime is always rounded down to the nearest hour
        (i.e., setting minutes, seconds, and microseconds to zero).

        Args:
            start_datetime (Optional[DateTime]): The datetime to set as the start.
                If None, the current datetime is used.

        Returns:
            DateTime: The adjusted start datetime.
        """
        if start_datetime is None:
            start_datetime = to_datetime()
        cls._start_datetime = start_datetime.set(minute=0, second=0, microsecond=0)
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

    async def run(
        self,
        start_datetime: Optional[DateTime] = None,
        mode: Optional[EnergyManagementMode] = None,
        algorithm: Optional[str] = None,
        genetic_parameters: Optional[GeneticOptimizationParameters] = None,
        genetic_individuals: Optional[int] = None,
        genetic_seed: Optional[int] = None,
        force_enable: Optional[bool] = False,
        force_update: Optional[bool] = False,
    ) -> None:
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
            algorithm (str, optional):
                The algorithm to use. Must be one of:
                - "GENETIC": Optimization uses the `GENETIC` optimization algorithm.

                Defaults to the algorithm defined in the current configuration.
            genetic_parameters (GeneticOptimizationParameters, optional): The
                parameter set for the `GENETIC` algorithm. If not provided, it will
                be constructed based on the current configuration and predictions.
            genetic_individuals (int, optional): The number of individuals for the
                `GENETIC` algorithm. Defaults to the algorithm's internal default (400)
                if not specified.
            genetic_seed (int, optional): The seed for the `GENETIC` algorithm. Defaults
                to the algorithm's internal random seed if not specified.
            force_enable (bool, optional): If True, bypasses any disabled state
                to force the update process. This is mostly applicable to
                prediction providers.
            force_update (bool, optional): If True, forces data to be refreshed
                even if a cached version is still valid.

        Returns:
            None
        """
        async with EnergyManagement._run_lock:
            if mode is None:
                mode = self.config.ems.mode

            if mode not in EnergyManagementMode._value2member_map_:
                raise ValueError(f"Unknown energy management mode {mode}.")
            if mode == EnergyManagementMode.DISABLED:
                logger.info("Energy management run disabled.")
                return

            logger.info("Starting energy management run.")

            # --- Data Aquisition ---
            EnergyManagement._stage = EnergyManagementStage.DATA_ACQUISITION

            # Remember/ set the start datetime of this energy management run.
            # None leads to current time as start datetime
            self.set_start_datetime(start_datetime)

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
            await self.prediction.update_data(force_enable=force_enable, force_update=force_update)

            if mode == EnergyManagementMode.PREDICTION:
                logger.info("Energy management run done (predictions updated)")
                EnergyManagement._stage = EnergyManagementStage.IDLE
                return

            # --- Optimization ---
            EnergyManagement._stage = EnergyManagementStage.OPTIMIZATION
            optimization_start = to_datetime()
            logger.info("Starting energy management optimization.")

            if algorithm is None:
                algorithm = self.config.optimization.algorithm

            if algorithm == "GENETIC":
                # Prepare optimization parameters
                # This also creates default configurations for missing values and updates the predictions
                logger.info("Starting optimzation parameter preparation.")
                if genetic_parameters is None:
                    genetic_parameters = await GeneticOptimizationParameters.prepare()
                    if genetic_parameters is None:
                        logger.error(
                            "Energy management run canceled. Could not prepare optimisation parameters."
                        )
                        EnergyManagement._stage = EnergyManagementStage.IDLE
                        return

                # Take values from config if not given
                if genetic_individuals is None:
                    genetic_individuals = self.config.optimization.genetic.individuals
                if genetic_seed is None:
                    genetic_seed = self.config.optimization.genetic.seed

                if EnergyManagement._start_datetime is None:  # Make mypy happy - already set by us
                    raise RuntimeError("Start datetime not set.")

                # --- Optimization (CPU-bound → MUST offload) ---
                try:
                    optimization = GeneticOptimization(
                        verbose=bool(self.config.server.verbose),
                        fixed_seed=genetic_seed,
                    )

                    loop = get_running_loop()
                    start_hour = EnergyManagement._start_datetime.hour
                    solution = await loop.run_in_executor(
                        None,
                        lambda: optimization.optimize_ems(
                            start_hour=start_hour,
                            parameters=cast(
                                GeneticOptimizationParameters, genetic_parameters
                            ),  # cast for mypy
                            ngen=genetic_individuals,
                        ),
                    )

                except Exception:
                    logger.exception("Energy management optimization failed.")
                    EnergyManagement._stage = EnergyManagementStage.IDLE
                    return

            else:
                logger.error(f"Unknown optimization algorithm: '{algorithm}'. Skipping.")
                EnergyManagement._stage = EnergyManagementStage.IDLE
                return

            optimization_duration = to_datetime() - optimization_start
            logger.info(
                "Energy management optimization ({}) completed in {:.1f} seconds.",
                algorithm,
                optimization_duration.total_seconds(),
            )

            logger.debug(
                "Energy management optimization solution:\n{}",
                EnergyManagement._optimization_solution,
            )
            logger.debug("Energy management plan:\n{}", EnergyManagement._plan)

            # --- Control dispatch by adapters ---
            EnergyManagement._stage = EnergyManagementStage.CONTROL_DISPATCH

            # Make genetic solution public
            EnergyManagement._genetic_solution = solution

            # Make optimization solution public
            EnergyManagement._optimization_solution = await solution.optimization_solution()

            # Make plan public
            EnergyManagement._plan = solution.energy_management_plan()

            logger.debug(
                "Energy management genetic solution:\n{}", EnergyManagement._genetic_solution
            )

            if genetic_parameters is None:
                genetic_parameters = await GeneticOptimizationParameters.prepare()

            if not genetic_parameters:
                logger.error("Energy management run canceled. Could not prepare parameters.")
                EnergyManagement._stage = EnergyManagementStage.IDLE
                return

            EnergyManagement._stage = EnergyManagementStage.OPTIMIZATION

            if genetic_individuals is None:
                genetic_individuals = self.config.optimization.genetic.individuals
            if genetic_seed is None:
                genetic_seed = self.config.optimization.genetic.seed

            if EnergyManagement._start_datetime is None:
                raise RuntimeError("Start datetime not set.")

            # --- Optimization (CPU-bound → MUST offload) ---
            try:
                optimization = GeneticOptimization(
                    verbose=bool(self.config.server.verbose),
                    fixed_seed=genetic_seed,
                )

                loop = get_running_loop()
                start_hour = EnergyManagement._start_datetime.hour
                solution = await loop.run_in_executor(
                    None,
                    lambda: optimization.optimize_ems(
                        start_hour=start_hour,
                        parameters=genetic_parameters,
                        ngen=genetic_individuals,
                    ),
                )

            except Exception:
                logger.exception("Energy management optimization failed.")
                EnergyManagement._stage = EnergyManagementStage.IDLE
                return

            EnergyManagement._genetic_solution = solution
            EnergyManagement._optimization_solution = await solution.optimization_solution()
            EnergyManagement._plan = solution.energy_management_plan()

            logger.debug("Genetic solution:\n{}", EnergyManagement._genetic_solution)
            logger.debug("Optimization solution:\n{}", EnergyManagement._optimization_solution)
            logger.debug("Plan:\n{}", EnergyManagement._plan)
            logger.info("Energy management run done (optimization updated)")

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
