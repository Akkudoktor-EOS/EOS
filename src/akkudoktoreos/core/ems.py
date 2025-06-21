import traceback
from typing import ClassVar, Optional

from loguru import logger
from pendulum import DateTime
from pydantic import ConfigDict, computed_field

from akkudoktoreos.core.cache import CacheUntilUpdateStore
from akkudoktoreos.core.coreabc import ConfigMixin, PredictionMixin, SingletonMixin
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.optimization.genetic import GeneticOptimization
from akkudoktoreos.optimization.geneticparams import (
    OptimizationParameters,
)
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime


class EnergyManagement(SingletonMixin, ConfigMixin, PredictionMixin, PydanticBaseModel):
    # Disable validation on assignment to speed up simulation runs.
    model_config = ConfigDict(
        validate_assignment=False,
    )

    # Start datetime.
    _start_datetime: ClassVar[Optional[DateTime]] = None

    # last run datetime. Used by energy management task
    _last_datetime: ClassVar[Optional[DateTime]] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def start_datetime(self) -> DateTime:
        """The starting datetime of the current or latest energy management."""
        if EnergyManagement._start_datetime is None:
            EnergyManagement.set_start_datetime()
        return EnergyManagement._start_datetime

    @classmethod
    def set_start_datetime(cls, start_datetime: Optional[DateTime] = None) -> DateTime:
        """Set the start datetime for the next energy management cycle.

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

    def run(
        self,
        force_enable: Optional[bool] = False,
        force_update: Optional[bool] = False,
    ) -> None:
        """Run energy management.

        Sets `start_datetime` to current hour, updates the configuration and the prediction, and
        starts simulation at current hour.

        Args:
            force_enable (bool, optional): If True, forces to update even if disabled. This
            is mostly relevant to prediction providers.
            force_update (bool, optional): If True, forces to update the data even if still cached.
        """
        # Throw away any cached results of the last run.
        logger.info("Starting energy management run.")
        CacheUntilUpdateStore().clear()
        self.set_start_datetime()

        if self.config.ems.mode is None or self.config.ems.mode == "PREDICTION":
            # Update the predictions
            self.prediction.update_data(force_enable=force_enable, force_update=force_update)
            logger.info("Energy management run done (predictions updated)")
            return

        # Prepare optimization parameters
        # This also creates default configurations for missing values and updates the predictions
        oparams = OptimizationParameters.prepare()

        if not oparams:
            logger.error(
                "Energy managment run canceled. Could not prepare optimisation parameters."
            )
            return

        # Create optimisation problem that calls into devices.update_data() for simulations.
        # Still not doing devices update.
        logger.info("Starting energy management optimisation.")
        try:
            opt_class = GeneticOptimization(verbose=bool(self.config.server.verbose))
            result = opt_class.optimierung_ems(
                parameters=oparams, start_hour=self.start_datetime.hour
            )
        except:
            logger.exception("Energy management optimisation failed.")
            return

        logger.info("Energy management plan: {}", result)
        logger.info("Energy management run done (optimization updated)")

    def manage_energy(self) -> None:
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

        if EnergyManagement._last_datetime is None:
            # Never run before
            try:
                # Remember energy run datetime.
                EnergyManagement._last_datetime = current_datetime
                # Try to run a first energy management. May fail due to config incomplete.
                self.run()
            except Exception as e:
                trace = "".join(traceback.TracebackException.from_exception(e).format())
                message = f"EOS init: {e}\n{trace}"
                logger.error(message)
            return

        if interval is None or interval == float("nan"):
            # No Repetition
            return

        if (
            compare_datetimes(current_datetime, EnergyManagement._last_datetime).time_diff
            < interval
        ):
            # Wait for next run
            return

        try:
            self.run()
        except Exception as e:
            trace = "".join(traceback.TracebackException.from_exception(e).format())
            message = f"EOS run: {e}\n{trace}"
            logger.error(message)

        # Remember the energy management run - keep on interval even if we missed some intervals
        while (
            compare_datetimes(current_datetime, EnergyManagement._last_datetime).time_diff
            >= interval
        ):
            EnergyManagement._last_datetime = EnergyManagement._last_datetime.add(seconds=interval)


# Initialize the Energy Management System, it is a singleton.
ems = EnergyManagement()


def get_ems() -> EnergyManagement:
    """Gets the EOS Energy Management System."""
    return ems
