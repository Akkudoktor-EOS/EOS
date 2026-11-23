"""Simulation of a home appliance that runs one or more fixed-duration cycles.

This module models household devices such as dishwashers or washing
machines that must run for a fixed duration, possibly multiple times
(cycles), within one or more allowed time windows.

Time windows are expressed as cycle windows (``GeneticTimeWindow``, see
``genetictimewindow``): each window's ``value`` encodes the 0-based cycle
index it applies to, so different cycles of the same appliance can be
constrained to different windows. A cycle without a window of its own is
unconstrained.

The optimizer asks ``allowed_start_slots()`` for the start slots at which a
complete run is allowed (windows, earliest start, deadline and horizon, from
the run start as slot 0), decodes the starts of its genome from them, and the
appliance builds the per-slot energy curve of those starts
(``build_load_curve()``).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Optional

import numpy as np
from loguru import logger

from akkudoktoreos.optimization.genetic.devices.genetictimewindow import (
    GeneticTimeWindow,
)
from akkudoktoreos.utils.datetimeutil import (
    DateTime,
    Duration,
    to_duration,
)


class GeneticScheduleMode(StrEnum):
    """How often an appliance's load profile is scheduled within the horizon."""

    ONCE = "ONCE"
    DAILY = "DAILY"


class GeneticDeadlinePolicy(StrEnum):
    """Whether a deadline that cannot be kept may be dropped."""

    BEST_EFFORT = "BEST_EFFORT"
    STRICT = "STRICT"


@dataclass(frozen=True, slots=True)
class GeneticHomeApplianceParameters:
    """A home appliance of the simulation, derived from its request.

    Attributes:
        device_id: ID of the appliance.
        consumption_wh: Energy of one run of the flat load [Wh]; with ``duration_h``.
        duration_h: Duration of one run of the flat load [h]; with ``consumption_wh``.
        num_cycles: Runs per schedule period.
        completed_cycles: Runs already completed.
        run_started_datetime: Start of a run in progress, if any.
        min_cycle_gap_h: Minimum idle time between two runs [h].
        time_windows: Cycle windows (``value`` is the cycle index); ``None`` unconstrained.
        shared_time_windows: Windows every run must lie within; ``None`` unconstrained.
        load_profile_power_w: Explicit power per profile step [W], instead of the flat load.
        load_profile_interval_seconds: Duration of one profile step [s].
        schedule_mode: How often the profile is scheduled.
        earliest_start_datetime: No run starts before this moment.
        deadline_datetime: Every run ends by this moment.
        deadline_policy: Whether a deadline that cannot be kept may be dropped.
    """

    device_id: str
    consumption_wh: Optional[int]
    duration_h: Optional[int]
    num_cycles: int
    completed_cycles: int
    run_started_datetime: Optional[DateTime]
    min_cycle_gap_h: int
    time_windows: Optional[tuple[GeneticTimeWindow, ...]]
    shared_time_windows: Optional[tuple[GeneticTimeWindow, ...]]
    load_profile_power_w: Optional[tuple[float, ...]]
    load_profile_interval_seconds: Optional[int]
    schedule_mode: GeneticScheduleMode
    earliest_start_datetime: Optional[DateTime]
    deadline_datetime: Optional[DateTime]
    deadline_policy: GeneticDeadlinePolicy


def resample_power_to_slot_energy(
    power_w: list[float],
    input_interval_seconds: float,
    slot_interval_seconds: float,
) -> np.ndarray:
    """Resample a piecewise-constant power profile to per-slot energy.

    Each input value ``power_w[i]`` is interpreted as a constant power [W] over
    the interval ``[i * input_interval_seconds, (i + 1) * input_interval_seconds)``.
    The energy of every output slot is the time-weighted integral of the input
    power over that slot::

        E_j = sum_i  P_i * overlap(i, j) / 3600   [Wh]

    where ``overlap(i, j)`` is the temporal overlap (in seconds) between input
    interval ``i`` and output slot ``j``. This is exact for arbitrary (including
    non-integer) ratios such as 10 -> 15 or 20 -> 15 minutes and conserves
    energy within numerical tolerance::

        sum_j E_j == sum_i P_i * input_interval_seconds / 3600

    Args:
        power_w: Piecewise-constant power values [W] of a single run.
        input_interval_seconds: Duration of one input step [s] (> 0).
        slot_interval_seconds: Duration of one output slot [s] (> 0).

    Returns:
        1-D array of per-slot energy [Wh]; length is the number of slots the run
        occupies (ceil of the total run duration divided by the slot duration).
    """
    if not math.isfinite(input_interval_seconds) or input_interval_seconds <= 0:
        raise ValueError("Input interval must be finite and positive.")
    if not math.isfinite(slot_interval_seconds) or slot_interval_seconds <= 0:
        raise ValueError("Slot interval must be finite and positive.")
    if not power_w or any(not math.isfinite(p) or p < 0 for p in power_w):
        raise ValueError("Power profile must contain finite non-negative values.")
    n_in = len(power_w)
    total_seconds = n_in * input_interval_seconds
    n_slots = int(np.ceil(total_seconds / slot_interval_seconds - 1e-9))
    out = np.zeros(max(n_slots, 0), dtype=float)
    for i, power in enumerate(power_w):
        if power == 0.0:
            continue
        seg_start = i * input_interval_seconds
        seg_end = seg_start + input_interval_seconds
        first = int(seg_start // slot_interval_seconds)
        last = int((seg_end - 1e-9) // slot_interval_seconds)
        for j in range(first, last + 1):
            slot_start = j * slot_interval_seconds
            slot_end = slot_start + slot_interval_seconds
            overlap = min(seg_end, slot_end) - max(seg_start, slot_start)
            if overlap > 0:
                out[j] += power * overlap / 3600.0
    return out


class GeneticHomeAppliance:
    """Non-vectorized simulation of a multi-cycle home appliance.

    A home appliance may execute multiple fixed-duration cycles during
    the simulation horizon. See the module docstring for how the optimizer
    schedules them.
    """

    # A run in progress whose start is more than this many run durations ago
    # is taken as a stale report (the executor stopped updating it).
    STALE_RUN_FACTOR: float = 2.0

    def __init__(
        self,
        parameters: GeneticHomeApplianceParameters,
        optimization_hours: int,
        prediction_hours: int,
        slot_duration_h: float = 1.0,
    ) -> None:
        """Initializes the appliance.

        Args:
            parameters: The appliance's configuration.
            optimization_hours: Number of hours under active
                optimization.
            prediction_hours: Length of the simulation horizon, in
                slots.
            slot_duration_h: Physical duration of each slot, in hours.
        """
        self.parameters = parameters
        self.optimization_hours = optimization_hours
        self.prediction_hours = prediction_hours

        if not math.isfinite(slot_duration_h) or slot_duration_h <= 0:
            raise ValueError("Slot duration must be finite and positive.")
        self.total_slots = prediction_hours
        self.slot_duration_h = slot_duration_h
        self.slot_interval_seconds = slot_duration_h * 3600
        self.device_id = parameters.device_id
        self.schedule_mode = parameters.schedule_mode
        self.time_windows = parameters.shared_time_windows
        # Cycle windows; a cycle without a window of its own is unconstrained.
        self.cycle_windows: tuple[GeneticTimeWindow, ...] = parameters.time_windows or ()
        self.earliest_start_datetime = parameters.earliest_start_datetime
        self.deadline_datetime = parameters.deadline_datetime
        self.deadline_policy = parameters.deadline_policy
        self.deadline_relaxed = False
        self._build_run_profile()
        self.duration_h = self.run_slots * slot_duration_h
        self.consumption_wh = float(self.run_energy_wh.sum())
        self.num_cycles = parameters.num_cycles
        self.min_cycle_gap_h = parameters.min_cycle_gap_h

        self.completed_cycles = parameters.completed_cycles

        self.load_curve = np.zeros(prediction_hours)

        # Run in progress, set by pin_run_in_progress(): its remaining energy
        # per slot from now, the first slot after it, and its start/end.
        self.pinned_load: np.ndarray = np.zeros(prediction_hours)
        self.pinned_end_slot: int = 0
        self.pinned_start_datetime: Optional[DateTime] = None
        self.pinned_end_datetime: Optional[DateTime] = None
        self.pinned_run_date: Any = None

        # Start times for remaining cycles, in chronological order.
        self.start_hours: list[int] = []

    def _build_run_profile(self) -> None:
        """Build the per-slot energy [Wh] of a single complete run."""
        if self.parameters.load_profile_power_w is not None:
            power = [float(value) for value in self.parameters.load_profile_power_w]
            input_interval = (
                self.parameters.load_profile_interval_seconds or self.slot_interval_seconds
            )
        else:
            # Flat fallback: constant power over duration_h hours. Route it through
            # the same resampling path so hourly and sub-hourly grids behave
            # identically. Power [W] = energy per hour = consumption_wh / duration_h.
            duration_h = self.parameters.duration_h
            consumption_wh = self.parameters.consumption_wh
            if duration_h is None or consumption_wh is None:
                raise ValueError("Flat appliance load requires duration and consumption.")
            power = [consumption_wh / duration_h]
            input_interval = duration_h * 3600

        self.run_energy_wh: np.ndarray = resample_power_to_slot_energy(
            power, float(input_interval), float(self.slot_interval_seconds)
        )
        self.run_slots: int = int(len(self.run_energy_wh))
        # The profile itself, for the remainder of a run in progress.
        self.run_power_w: list[float] = power
        self.run_power_interval_seconds: float = float(input_interval)
        self.run_duration_seconds: float = len(power) * float(input_interval)

    def pin_run_in_progress(self, slot0_datetime: DateTime, observation_datetime: DateTime) -> None:
        """Fix the remainder of the run in progress as load from now on.

        A run in progress (``run_started_datetime``) is kept until the device
        reports it as finished. Its remaining energy is placed from the
        observation time on, independent of windows, deadline and genes. A run
        past its configured duration still runs: slot 0 stays occupied without
        extra energy. A start older than ``STALE_RUN_FACTOR`` run durations is
        taken as a stale report and ignored.

        Args:
            slot0_datetime: Local, timezone-aware datetime of slot index 0.
            observation_datetime: Moment of the optimization run (within slot 0).
        """
        self.pinned_load = np.zeros(self.prediction_hours)
        self.pinned_end_slot = 0
        self.pinned_start_datetime = None
        self.pinned_end_datetime = None
        self.pinned_run_date = None
        started = self.parameters.run_started_datetime
        if started is None or self.prediction_hours <= 0:
            return
        timezone = slot0_datetime.timezone
        if timezone is None:
            raise ValueError("The optimization slot origin must have a timezone.")
        started = started.in_timezone(timezone)
        now = observation_datetime.in_timezone(timezone)
        elapsed = (now - started).total_seconds()
        if elapsed < 0 or elapsed > self.STALE_RUN_FACTOR * self.run_duration_seconds:
            logger.error(
                "Home appliance '{}': run start {} is not plausible at {} "
                "(run duration {} s); planned as not running.",
                self.device_id,
                started,
                now,
                self.run_duration_seconds,
            )
            return

        slot_seconds = self.slot_interval_seconds
        now_offset = (now - slot0_datetime).total_seconds()
        start_offset = (started - slot0_datetime).total_seconds()
        end_offset = start_offset + self.run_duration_seconds
        for index, power in enumerate(self.run_power_w):
            segment_start = max(start_offset + index * self.run_power_interval_seconds, now_offset)
            segment_end = start_offset + (index + 1) * self.run_power_interval_seconds
            while power > 0 and segment_start < segment_end:
                slot = int(segment_start // slot_seconds)
                if slot >= self.prediction_hours:
                    break
                part_end = min(segment_end, (slot + 1) * slot_seconds)
                self.pinned_load[slot] += power * (part_end - segment_start) / 3600.0
                segment_start = part_end
        # Slot 0 stays occupied while the device runs, even past its duration.
        self.pinned_end_slot = min(
            self.prediction_hours,
            max(1, math.ceil(end_offset / slot_seconds - 1e-9)),
        )
        self.pinned_start_datetime = started
        self.pinned_end_datetime = max(started.add(seconds=self.run_duration_seconds), now)
        # The start day in the time zone of the horizon, which also dates the cycles.
        horizon_timezone = slot0_datetime.timezone
        self.pinned_run_date = (
            started.in_timezone(horizon_timezone) if horizon_timezone is not None else started
        ).date()
        if elapsed >= self.run_duration_seconds:
            logger.info(
                "Home appliance '{}': run started {} is past its duration of {} s "
                "and still running; kept until the device reports it finished.",
                self.device_id,
                started,
                self.run_duration_seconds,
            )

    @property
    def run_in_progress(self) -> bool:
        """bool: Whether a run in progress is pinned."""
        return self.pinned_start_datetime is not None

    def next_cycle_earliest_slot(self) -> int:
        """Earliest slot for the next cycle: after a pinned run and its gap."""
        if not self.run_in_progress:
            return 0
        return self.pinned_end_slot + math.ceil(self.min_cycle_gap_h / self.slot_duration_h)

    def completed_cycles_on(self, first_date: Any) -> int:
        """Cycles counted as done on the first planning day, incl. a pinned run.

        Args:
            first_date: Local date of slot 0.

        Returns:
            ``completed_cycles`` plus one when a run in progress belongs to that
            day (DAILY) or to the horizon (ONCE), capped to ``num_cycles``.
        """
        pinned = 0
        if self.run_in_progress and (
            self.schedule_mode == GeneticScheduleMode.ONCE or self.pinned_run_date == first_date
        ):
            pinned = 1
        return min(self.num_cycles, self.completed_cycles + pinned)

    def _slot_offset(self, moment: DateTime, slot0_datetime: DateTime, *, round_up: bool) -> int:
        """Convert an absolute moment into a slot index relative to slot 0.

        Args:
            moment: Absolute moment; converted into ``slot0_datetime``'s timezone.
            slot0_datetime: Local, timezone-aware datetime of slot index 0.
            round_up: ``True`` returns the first slot boundary at or after
                ``moment`` (lower bounds), ``False`` the last one at or before
                it (upper bounds).

        Returns:
            Slot index (may be negative or beyond the grid; callers clamp).
        """
        timezone = slot0_datetime.timezone
        if timezone is None:
            raise ValueError("The optimization slot origin must have a timezone.")
        seconds = (moment.in_timezone(timezone) - slot0_datetime).total_seconds()
        exact = seconds / self.slot_interval_seconds
        # Tolerance absorbs float noise so a moment that sits exactly on a slot
        # boundary is not pushed to the neighbouring slot.
        return math.ceil(exact - 1e-9) if round_up else math.floor(exact + 1e-9)

    def allowed_start_slots(
        self,
        *,
        slot0_datetime: DateTime,
        earliest_slot: int,
        horizon_end_slot: int,
        cycle_index: Optional[int] = None,
    ) -> list[int]:
        """Return the sorted absolute start slots at which a full run is allowed.

        A start slot ``s`` is allowed when the complete run fits the optimization
        horizon, both absolute bounds and (if configured) a single allowed time
        window:

        - ``earliest_slot <= s`` and ``s + run_slots <= horizon_end_slot``
        - with ``earliest_start_datetime`` set, the run starts at or after it
        - with ``deadline_datetime`` set, the run *ends* at or before it
        - with ``time_windows`` set, the run's whole occupied span starting at
          ``s`` is contained in one window (respecting weekday/date constraints)

        When a deadline leaves no start at all and the policy is
        ``BEST_EFFORT``, the deadline is dropped and only the earliest still
        possible start is offered (a warning is logged and ``deadline_relaxed``
        is set). Multiple cycles retain all relaxed choices for joint
        earliest-start repair by the optimizer, respecting per-cycle windows
        and minimum gaps.

        No snapping is performed: every returned slot is a genuinely valid start.

        Args:
            slot0_datetime: Local, timezone-aware datetime of slot index 0.
            earliest_slot: First slot the optimizer may schedule at ("now").
            horizon_end_slot: Exclusive upper bound; a run must end at or before.
            cycle_index: Global configured cycle index, independent of completed cycles.

        Returns:
            Sorted list of allowed absolute start slots (may be empty).
        """
        self.deadline_relaxed = False
        allowed = self._allowed_start_slots(
            slot0_datetime=slot0_datetime,
            earliest_slot=earliest_slot,
            horizon_end_slot=horizon_end_slot,
            apply_deadline=True,
            cycle_index=cycle_index,
        )
        if (
            allowed
            or self.deadline_datetime is None
            or self.deadline_policy == GeneticDeadlinePolicy.STRICT
        ):
            return allowed

        relaxed = self._allowed_start_slots(
            slot0_datetime=slot0_datetime,
            earliest_slot=earliest_slot,
            horizon_end_slot=horizon_end_slot,
            apply_deadline=False,
            cycle_index=cycle_index,
        )
        if not relaxed:
            return relaxed
        self.deadline_relaxed = True
        # Keep only the earliest possible start: the deadline is already missed,
        # so the run is scheduled as soon as possible rather than as cheap as
        # possible. Multiple cycles retain choices so the optimizer can find
        # the earliest joint schedule that respects idle gaps.
        earliest = relaxed[:1] if self.num_cycles == 1 else relaxed
        logger.warning(
            "Home appliance '{}': deadline {} can not be met - running as early as "
            "possible instead (BEST_EFFORT). Run ends {}.",
            self.device_id,
            self.deadline_datetime,
            self.run_end_datetime(earliest[0], slot0_datetime),
        )
        return earliest

    def _allowed_start_slots(
        self,
        *,
        slot0_datetime: DateTime,
        earliest_slot: int,
        horizon_end_slot: int,
        cycle_index: Optional[int] = None,
        apply_deadline: bool,
    ) -> list[int]:
        """Compute the allowed start slots for one set of constraints.

        Args:
            slot0_datetime: Local, timezone-aware datetime of slot index 0.
            earliest_slot: First slot the optimizer may schedule at ("now").
            horizon_end_slot: Exclusive upper bound; a run must end at or before.
            cycle_index: Global configured cycle index, independent of completed cycles.
            apply_deadline: Whether ``deadline_datetime`` restricts the run end.

        Returns:
            Sorted list of allowed absolute start slots (may be empty).
        """
        run_slots = self.run_slots
        if run_slots <= 0:
            return []

        first_start = max(earliest_slot, 0)
        if self.earliest_start_datetime is not None:
            first_start = max(
                first_start,
                self._slot_offset(self.earliest_start_datetime, slot0_datetime, round_up=True),
            )

        end_bound = min(horizon_end_slot, self.total_slots)
        if apply_deadline and self.deadline_datetime is not None:
            end_bound = min(
                end_bound,
                self._slot_offset(self.deadline_datetime, slot0_datetime, round_up=False),
            )

        last_start = end_bound - run_slots
        if last_start < first_start:
            return []

        if cycle_index is not None and not 0 <= cycle_index < self.num_cycles:
            raise ValueError("Cycle index is outside the configured cycle range.")
        cycle_windows = [
            window
            for window in self.cycle_windows
            if window.value is not None and int(window.value) == cycle_index
        ]

        run_duration = to_duration(f"{run_slots * self.slot_interval_seconds} seconds")
        allowed: list[int] = []
        for slot in range(first_start, last_start + 1):
            start_dt = slot0_datetime.add(seconds=slot * self.slot_interval_seconds)
            if (
                self.time_windows is None
                or self._windows_allow_run(self.time_windows, start_dt, run_duration)
            ) and (
                not cycle_windows
                or self._windows_allow_run(cycle_windows, start_dt, run_duration, merge=True)
            ):
                allowed.append(slot)
        return allowed

    @staticmethod
    def _windows_allow_run(
        windows: Sequence[GeneticTimeWindow],
        start: DateTime,
        duration: Duration,
        *,
        merge: bool = False,
    ) -> bool:
        """Check full coverage including windows anchored on previous local dates.

        Date, weekday, and month restrictions belong to the window's opening day.
        Per-cycle windows retain their existing union semantics; shared windows
        require one complete containing occurrence.
        """
        # Compare elapsed offsets from the run start, including when sorting
        # and merging intervals. Wall-clock ordering cannot distinguish the
        # two occurrences of an hour repeated during a DST transition.
        intervals: list[tuple[float, float]] = []
        run_end = start + duration
        run_seconds = (run_end - start).total_seconds()
        for window in windows:
            days_back = math.ceil(window.duration.total_seconds() / 86400) + 1
            for offset in range(days_back + 1):
                anchor = start.subtract(days=offset)
                if window.date is not None and anchor.date() != window.date:
                    continue
                if window.day_of_week is not None and anchor.day_of_week != window.day_of_week:
                    continue
                if window.month is not None and anchor.month != window.month:
                    continue
                opening, closing = window.start_end(anchor)
                opening_offset = (opening - start).total_seconds()
                closing_offset = (closing - start).total_seconds()
                if opening_offset <= 0 and run_seconds <= closing_offset:
                    return True
                if merge and opening_offset < run_seconds and closing_offset > 0:
                    intervals.append((opening_offset, closing_offset))
        covered_until = 0.0
        for opening_offset, closing_offset in sorted(intervals):
            if opening_offset > covered_until:
                break
            covered_until = max(covered_until, closing_offset)
            if covered_until >= run_seconds:
                return True
        return False

    def run_end_datetime(self, start_slot: int, slot0_datetime: DateTime) -> DateTime:
        """Absolute local moment at which a run started at ``start_slot`` finishes.

        Args:
            start_slot: Absolute start slot of the run.
            slot0_datetime: Local, timezone-aware datetime of slot index 0.

        Returns:
            End datetime of the run (exclusive, i.e. the first free moment).
        """
        return slot0_datetime.add(
            seconds=(start_slot + self.run_slots) * self.slot_interval_seconds
        )

    def deadline_missed(self, starts: list[int], slot0_datetime: DateTime) -> bool:
        """Whether the scheduled runs violate the configured deadline.

        Without a deadline nothing can be missed. With one, a consumer that was
        not scheduled at all, or whose run ends after the deadline (a relaxed
        BEST_EFFORT deadline), counts as missed. A run in progress counts with
        its expected end.

        Args:
            starts: Absolute start slots of the scheduled runs.
            slot0_datetime: Local, timezone-aware datetime of slot index 0.

        Returns:
            True if the deadline is set and not met.
        """
        if self.deadline_datetime is None:
            return False
        timezone = slot0_datetime.timezone
        if timezone is None:
            raise ValueError("The optimization slot origin must have a timezone.")
        ends = [self.run_end_datetime(start, slot0_datetime) for start in starts]
        if self.pinned_end_datetime is not None:
            ends.append(self.pinned_end_datetime)
        if not ends:
            return True
        deadline = self.deadline_datetime.in_timezone(timezone)
        return any(end > deadline for end in ends)

    def build_load_curve(self, starts: list[int]) -> None:
        """Place the resampled run energy at each decoded start slot.

        Multiple runs may overlap; their per-slot energies are summed.

        Args:
            starts: Absolute start slots of the scheduled runs.
        """
        if any(start < 0 or start + self.run_slots > self.total_slots for start in starts):
            raise ValueError("A complete appliance run must fit inside the slot horizon.")
        self.start_hours = list(starts)
        self._build_load_curve()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    @property
    def num_remaining_cycles(self) -> int:
        """int: Number of cycles which still have to be scheduled."""
        return max(0, self.num_cycles - self.completed_cycles)

    def set_completed_cycles(self, completed_cycles: int) -> None:
        """Sets the number of cycles already completed.

        Clears any previously scheduled start times and load curve.

        Args:
            completed_cycles: Number of cycles completed so far.
                Clamped to ``[0, num_cycles]``.
        """
        self.completed_cycles = max(
            0,
            min(completed_cycles, self.num_cycles),
        )

        self.start_hours = []
        self.reset_load_curve()

    # ------------------------------------------------------------------
    # Load curve
    # ------------------------------------------------------------------

    def _build_load_curve(self) -> None:
        """Builds the load curve from all scheduled cycles and a pinned run."""
        self.reset_load_curve()
        if self.run_in_progress:
            self.load_curve += self.pinned_load

        for start in self.start_hours:
            length = min(self.run_slots, self.total_slots - start)
            if 0 <= start and length > 0:
                self.load_curve[start : start + length] += self.run_energy_wh[:length]

    def reset_load_curve(self) -> None:
        """Resets the load curve to all zeros."""
        self.load_curve = np.zeros(self.prediction_hours)

    def get_load_curve(self) -> np.ndarray:
        """Returns the current per-slot load curve, in watt-hours."""
        return self.load_curve

    def get_load_for_hour(self, hour: int) -> float:
        """Returns the load for a specific hour.

        Args:
            hour: Hour of the prediction horizon to look up.

        Returns:
            The energy, in watt-hours, at slot ``hour``.

        Raises:
            ValueError: If ``hour`` is outside
                ``[0, prediction_hours)``.
        """
        if hour < 0 or hour >= self.prediction_hours:
            raise ValueError(
                f"The specified hour {hour} is outside the available "
                f"time frame {self.prediction_hours}."
            )

        return float(self.load_curve[hour])
