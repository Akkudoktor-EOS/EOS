import json
import threading
import traceback
import urllib.request
from asyncio import Lock, get_running_loop
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Optional, Sequence

from loguru import logger
from pydantic import computed_field

from akkudoktoreos.config.configabc import OHMRAKEL42_ALGORITHM_NAME
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
from akkudoktoreos.optimization.optimization import OptimizationSolution
from akkudoktoreos.optimization.solutionmodel import Ohmrakel42Solution
from akkudoktoreos.thermal.thermalmodels import ThermalPlan
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime

if TYPE_CHECKING:
    # The device by device optimizer is imported on first use (it needs numba).
    from akkudoktoreos.optimization.planner import WarmStart
    from akkudoktoreos.optimization.request import PlanningRequest
    from akkudoktoreos.optimization.solution import PlanningOutcome

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

    # Solution of the device by device algorithm of latest energy management run with optimization
    _ohmrakel42_solution: ClassVar[Optional[Ohmrakel42Solution]] = None
    _planning_outcome: ClassVar[Optional["PlanningOutcome"]] = None

    # Thermal plans (one per heat source) of the latest device by device run
    _thermal_plans: ClassVar[list[ThermalPlan]] = []

    # Plan of the latest device by device run, the start of the next one
    _ohmrakel42_warm_start: ClassVar[Optional["WarmStart"]] = None

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
            interval_seconds: Explicit slot duration; otherwise use the configured interval.

        Returns:
            DateTime: The adjusted start datetime.
        """
        if start_datetime is None:
            start_datetime = to_datetime()
        cls._observation_datetime = start_datetime
        if interval_seconds is None:
            interval_seconds = cls.config.optimization.interval_sec
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
    def planning_outcome(cls) -> Optional["PlanningOutcome"]:
        """Get the latest successful configuration-based planning outcome."""
        return cls._planning_outcome

    @classmethod
    def ohmrakel42_solution(cls) -> Optional[Ohmrakel42Solution]:
        """Get the latest OHMRAKEL42 solution."""
        return cls._ohmrakel42_solution

    @classmethod
    def thermal_plans(cls) -> list[ThermalPlan]:
        """Get the thermal plans (one per heat source) of the latest device by device run."""
        return list(cls._thermal_plans)

    async def run(
        self,
        start_datetime: Optional[DateTime] = None,
        mode: Optional[EnergyManagementMode] = None,
        ohmrakel42_request: Optional["PlanningRequest"] = None,
        changed_devices: Optional[Sequence[str]] = None,
        binary_discharge: bool = False,
        include_outcome: bool = False,
        force_enable: Optional[bool] = False,
        force_update: Optional[bool] = False,
    ) -> "Ohmrakel42Solution | PlanningOutcome | None":
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
            ohmrakel42_request (PlanningRequest, optional): Runtime values (state of charge,
                forecasts, blocks, absences) for the `OHMRAKEL42` algorithm. Everything else
                is resolved from the current configuration and predictions.
            changed_devices (Sequence[str], optional): Ids of devices an event concerns
                (for example a vehicle that leaves earlier). The `OHMRAKEL42` algorithm plans
                them first, starting from the last plan. An unknown id is an error.
            include_outcome: Include the simulation and plan for callers translating the result.
            binary_discharge: Limit discharge to on/off for compatibility clients.
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
            if start_datetime is None:
                start_datetime = to_datetime(in_timezone=self.config.general.timezone)
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

            algorithm = OHMRAKEL42_ALGORITHM_NAME
            from akkudoktoreos.optimization.planner import run_planning, warm_start_of
            from akkudoktoreos.optimization.request import PlanningRequest

            logger.info(f"{algorithm}: Starting planning task preparation.")
            try:
                request = (
                    ohmrakel42_request if ohmrakel42_request is not None else PlanningRequest()
                )
                task = await request.resolve()
                if binary_discharge:
                    task = replace(
                        task,
                        search=replace(
                            task.search,
                            battery_discharge_levels=((1.0,),) * len(task.battery_ids),
                        ),
                    )
                previous = EnergyManagement._ohmrakel42_warm_start
                ttl_seconds = self.config.thermal.plan_ttl_seconds
                changed = list(changed_devices or [])

                # --- Planning (CPU-bound → MUST offload) ---
                loop = get_running_loop()
                outcome = await loop.run_in_executor(
                    None, lambda: run_planning(task, previous, changed, ttl_seconds)
                )
            except Exception:
                # A failed planning is loud: the cause goes to the caller, no plan is made up.
                logger.exception(f"{algorithm}: Energy management optimization failed.")
                EnergyManagement._stage = EnergyManagementStage.IDLE
                raise

            # Publish a consistent set without yielding to another coroutine.
            EnergyManagement._planning_outcome = outcome
            EnergyManagement._ohmrakel42_solution = outcome.solution
            EnergyManagement._optimization_solution = outcome.optimization_solution
            EnergyManagement._plan = outcome.energy_management_plan
            EnergyManagement._thermal_plans = list(outcome.thermal_plans)
            # The outcome's task may be the one without heat pumps (see ``run_planning``).
            EnergyManagement._ohmrakel42_warm_start = warm_start_of(outcome.task, outcome.plan)

            logger.debug("{}: Energy management solution:\n{}", algorithm, outcome.solution)

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
            return outcome if include_outcome else outcome.solution
