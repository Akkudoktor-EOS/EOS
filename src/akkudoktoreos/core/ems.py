import traceback
from asyncio import Lock, get_running_loop
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from functools import partial
from typing import ClassVar, Optional

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
from akkudoktoreos.utils.datetimeutil import DateTime, compare_datetimes, to_datetime

# The executor to execute the CPU heavy energy management run
executor = ThreadPoolExecutor(max_workers=1)


class EnergyManagementStage(Enum):
    """Enumeration of the main stages in the energy management lifecycle."""

    IDLE = "IDLE"
    DATA_ACQUISITION = "DATA_AQUISITION"
    FORECAST_RETRIEVAL = "FORECAST_RETRIEVAL"
    OPTIMIZATION = "OPTIMIZATION"
    CONTROL_DISPATCH = "CONTROL_DISPATCH"

    def __str__(self) -> str:
        """Return the string representation of the stage."""
        return self.value


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

    @classmethod
    def _run(
        cls,
        start_datetime: Optional[DateTime] = None,
        mode: Optional[EnergyManagementMode] = None,
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
            start_datetime (DateTime, optional): The starting timestamp
                of the energy management run. Defaults to the current datetime
                if not provided.
            mode (EnergyManagementMode, optional): The management mode to use. Must be one of:
                - "OPTIMIZATION": Runs the optimization process.
                - "PREDICTION": Updates the forecast without optimization.

                Defaults to the mode defined in the current configuration.
            genetic_parameters (GeneticOptimizationParameters, optional): The
                parameter set for the genetic algorithm. If not provided, it will
                be constructed based on the current configuration and predictions.
            genetic_individuals (int, optional): The number of individuals for the
                genetic algorithm. Defaults to the algorithm's internal default (400)
                if not specified.
            genetic_seed (int, optional): The seed for the genetic algorithm. Defaults
                to the algorithm's internal random seed if not specified.
            force_enable (bool, optional): If True, bypasses any disabled state
                to force the update process. This is mostly applicable to
                prediction providers.
            force_update (bool, optional): If True, forces data to be refreshed
                even if a cached version is still valid.

        Returns:
            None
        """
        # Ensure there is only one optimization/ energy management run at a time
        if mode not in (None, "PREDICTION", "OPTIMIZATION"):
            raise ValueError(f"Unknown energy management mode {mode}.")

        logger.info("Starting energy management run.")

        cls._stage = EnergyManagementStage.DATA_ACQUISITION

        # Remember/ set the start datetime of this energy management run.
        # None leads
        cls.set_start_datetime(start_datetime)

        # Throw away any memory cached results of the last energy management run.
        CacheEnergyManagementStore().clear()

        # Do data aquisition by adapters
        try:
            cls.adapter.update_data(force_enable)
        except Exception as e:
            trace = "".join(traceback.TracebackException.from_exception(e).format())
            error_msg = f"Adapter update failed - phase {cls._stage}: {e}\n{trace}"
            logger.error(error_msg)

        cls._stage = EnergyManagementStage.FORECAST_RETRIEVAL

        if mode is None:
            mode = cls.config.ems.mode
        if mode is None or mode == "PREDICTION":
            # Update the predictions
            cls.prediction.update_data(force_enable=force_enable, force_update=force_update)
            logger.info("Energy management run done (predictions updated)")
            cls._stage = EnergyManagementStage.IDLE
            return

        # Prepare optimization parameters
        # This also creates default configurations for missing values and updates the predictions
        logger.info(
            "Starting energy management prediction update and optimzation parameter preparation."
        )
        if genetic_parameters is None:
            genetic_parameters = GeneticOptimizationParameters.prepare()

        if not genetic_parameters:
            logger.error(
                "Energy management run canceled. Could not prepare optimisation parameters."
            )
            cls._stage = EnergyManagementStage.IDLE
            return

        cls._stage = EnergyManagementStage.OPTIMIZATION
        logger.info("Starting energy management optimization.")

        # Take values from config if not given
        if genetic_individuals is None:
            genetic_individuals = cls.config.optimization.genetic.individuals
        if genetic_seed is None:
            genetic_seed = cls.config.optimization.genetic.seed

        if cls._start_datetime is None:  # Make mypy happy - already set by us
            raise RuntimeError("Start datetime not set.")

        try:
            optimization = GeneticOptimization(
                verbose=bool(cls.config.server.verbose),
                fixed_seed=genetic_seed,
            )
            solution = optimization.optimierung_ems(
                start_hour=cls._start_datetime.hour,
                parameters=genetic_parameters,
                ngen=genetic_individuals,
            )
        except:
            logger.exception("Energy management optimization failed.")
            cls._stage = EnergyManagementStage.IDLE
            return

        cls._stage = EnergyManagementStage.CONTROL_DISPATCH

        # Make genetic solution public
        cls._genetic_solution = solution

        # Make optimization solution public
        cls._optimization_solution = solution.optimization_solution()

        # Make plan public
        cls._plan = solution.energy_management_plan()

        logger.debug("Energy management genetic solution:\n{}", cls._genetic_solution)
        logger.debug("Energy management optimization solution:\n{}", cls._optimization_solution)
        logger.debug("Energy management plan:\n{}", cls._plan)
        logger.info("Energy management run done (optimization updated)")

        # Do control dispatch by adapters
        try:
            cls.adapter.update_data(force_enable)
        except Exception as e:
            trace = "".join(traceback.TracebackException.from_exception(e).format())
            error_msg = f"Adapter update failed - phase {cls._stage}: {e}\n{trace}"
            logger.error(error_msg)

        # energy management run finished
        cls._stage = EnergyManagementStage.IDLE

    async def run(
        self,
        start_datetime: Optional[DateTime] = None,
        mode: Optional[EnergyManagementMode] = None,
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
            start_datetime (DateTime, optional): The starting timestamp
                of the energy management run. Defaults to the current datetime
                if not provided.
            mode (EnergyManagementMode, optional): The management mode to use. Must be one of:
                - "OPTIMIZATION": Runs the optimization process.
                - "PREDICTION": Updates the forecast without optimization.

                Defaults to the mode defined in the current configuration.
            genetic_parameters (GeneticOptimizationParameters, optional): The
                parameter set for the genetic algorithm. If not provided, it will
                be constructed based on the current configuration and predictions.
            genetic_individuals (int, optional): The number of individuals for the
                genetic algorithm. Defaults to the algorithm's internal default (400)
                if not specified.
            genetic_seed (int, optional): The seed for the genetic algorithm. Defaults
                to the algorithm's internal random seed if not specified.
            force_enable (bool, optional): If True, bypasses any disabled state
                to force the update process. This is mostly applicable to
                prediction providers.
            force_update (bool, optional): If True, forces data to be refreshed
                even if a cached version is still valid.

        Returns:
            None
        """
        async with self._run_lock:
            loop = get_running_loop()
            # Create a partial function with parameters "baked in"
            func = partial(
                EnergyManagement._run,
                start_datetime=start_datetime,
                mode=mode,
                genetic_parameters=genetic_parameters,
                genetic_individuals=genetic_individuals,
                genetic_seed=genetic_seed,
                force_enable=force_enable,
                force_update=force_update,
            )
            # Run optimization in background thread to avoid blocking event loop
            await loop.run_in_executor(executor, func)

    async def manage_energy(self) -> None:
        """Repeating task for managing energy.

        This task should be executed by the server regularly (e.g., every 10 seconds)
        to ensure proper energy management. Configuration changes to the energy management interval
        will only take effect if this task is executed.

        - Initializes and runs the energy management for the first time if it has never been run
          before.
        - If the energy management interval is not configured or invalid (NaN), the task will not
          trigger any repeated energy management runs.
        - Compares the current time with the last run time and runs the energy management if the
          interval has elapsed.
        - Logs any exceptions that occur during the initialization or execution of the energy
          management.

        Note: The task maintains the interval even if some intervals are missed.
        """
        current_datetime = to_datetime()
        interval = self.config.ems.interval  # interval maybe changed in between

        if EnergyManagement._last_run_datetime is None:
            # Never run before
            try:
                # Remember energy run datetime.
                EnergyManagement._last_run_datetime = current_datetime
                # Try to run a first energy management. May fail due to config incomplete.
                await self.run()
            except Exception as e:
                trace = "".join(traceback.TracebackException.from_exception(e).format())
                message = f"EOS init: {e}\n{trace}"
                logger.error(message)
            return

        if interval is None or interval == float("nan"):
            # No Repetition
            return

        if (
            compare_datetimes(current_datetime, EnergyManagement._last_run_datetime).time_diff
            < interval
        ):
            # Wait for next run
            return

        try:
            await self.run()
        except Exception as e:
            trace = "".join(traceback.TracebackException.from_exception(e).format())
            message = f"EOS run: {e}\n{trace}"
            logger.error(message)

        # Remember the energy management run - keep on interval even if we missed some intervals
        while (
            compare_datetimes(current_datetime, EnergyManagement._last_run_datetime).time_diff
            >= interval
        ):
            EnergyManagement._last_run_datetime = EnergyManagement._last_run_datetime.add(
                seconds=interval
            )



# Initialize the Energy Management System, it is a singleton.
ems = EnergyManagement()


def get_ems() -> EnergyManagement:
    """Gets the EOS Energy Management System."""
    return ems
