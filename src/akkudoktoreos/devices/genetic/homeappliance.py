"""Simulation of a home appliance that runs one or more fixed-duration cycles.

This module models household devices such as dishwashers or washing
machines that must run for a fixed duration, possibly multiple times
(cycles), within one or more allowed time windows. Given a set of
requested start times, `HomeAppliance` repairs them into a
feasible, chronologically ordered schedule and produces the resulting
per-slot energy curve.

Time windows are always expressed as a `CycleTimeWindowSequence`
(see ``akkudoktoreos.config.configabc``): each contained window's
``value`` encodes the 0-based cycle index it applies to, so different
cycles of the same appliance can be constrained to different windows.
When no windows are configured, every cycle defaults to a single
window spanning the full prediction horizon (i.e. unconstrained).

Cycle start times are repaired according to the following rules:

1. Round and clip the requested start to the simulation horizon.
2. Snap each cycle's start to the nearest start allowed by that
   cycle's own time window.
3. Sort all cycle starts chronologically, keeping each start paired
   with the cycle (and therefore the allowed-start mask) it belongs
   to.
4. Walk the sorted starts and push any cycle that starts too soon
   after its predecessor to the next start allowed by its own
   window, enforcing the appliance duration plus the configured
   minimum idle gap.
5. Generate the combined hourly load curve from the final starts.
"""

import math
from collections.abc import Sequence
from typing import Any, Optional, Self

import numpy as np
from loguru import logger
from pydantic import Field, field_validator, model_validator

from akkudoktoreos.config.configabc import (
    CycleTimeWindowSequence,
    TimeWindow,
    TimeWindowSequence,
    ValueTimeWindow,
)
from akkudoktoreos.devices.devicesabc import (
    ConsumerDeadlinePolicy,
    ConsumerScheduleMode,
    validate_home_appliance_load_definition,
)
from akkudoktoreos.optimization.genetic.geneticdevices import DeviceParameters
from akkudoktoreos.utils.datetimeutil import (
    DateTime,
    Duration,
    compare_datetimes,
    to_datetime,
    to_duration,
    to_time,
)


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


class HomeApplianceParameters(DeviceParameters):
    """Configuration for a simulated home appliance device."""

    device_id: str = Field(
        json_schema_extra={
            "description": "ID of home appliance",
            "examples": ["dishwasher"],
        }
    )
    consumption_wh: Optional[int] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": (
                "An integer representing the energy consumption "
                "of a household device in watt-hours."
            ),
            "examples": [2000],
        },
    )
    duration_h: Optional[int] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": (
                "An integer representing the usage duration of a household device in hours."
            ),
            "examples": [3],
        },
    )
    num_cycles: int = Field(
        default=1,
        gt=0,
        json_schema_extra={
            "description": "Number of cycles the appliance must run.",
            "examples": [2],
        },
    )
    completed_cycles: int = Field(
        default=0, ge=0, description="Cycles already completed on the first planning day."
    )

    min_cycle_gap_h: int = Field(
        default=0,
        ge=0,
        json_schema_extra={
            "description": (
                "Minimum idle time between the end of one cycle and the start of the next cycle."
            ),
            "examples": [1],
        },
    )
    time_windows: Optional[CycleTimeWindowSequence] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Allowed per-cycle time windows. Each window's `value` "
                "encodes the 0-based cycle index it applies to; multiple "
                "windows may share a cycle index. When omitted, every "
                "cycle is unconstrained across the full prediction "
                "horizon."
            ),
            "examples": [
                [
                    {
                        "start_time": "10:00",
                        "duration": "3 hours",
                        "value": 0,
                    },
                ],
            ],
        },
    )

    shared_time_windows: Optional[TimeWindowSequence] = Field(
        default=None,
        description="Allowed recurring windows shared by every cycle; intersected with per-cycle windows.",
    )

    load_profile_power_w: Optional[list[float]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Explicit load profile describing a single complete run as a "
                "sequence of non-negative power values in watts. Each value "
                "covers 'load_profile_interval_seconds'. Mutually exclusive with "
                "consumption_wh/duration_h."
            ),
            "examples": [[200.0, 2000.0, 1800.0, 100.0]],
        },
    )
    load_profile_interval_seconds: Optional[int] = Field(
        default=None,
        gt=0,
        json_schema_extra={
            "description": (
                "Duration of one 'load_profile_power_w' step in seconds. Defaults "
                "to the configured optimization interval when a profile is given."
            ),
            "examples": [900, 3600],
        },
    )
    schedule_mode: ConsumerScheduleMode = Field(
        default=ConsumerScheduleMode.ONCE,
        json_schema_extra={
            "description": (
                "Scheduling mode: ONCE (a single run within the horizon) or DAILY "
                "(one run per local calendar day with a feasible full run)."
            ),
            "examples": ["ONCE", "DAILY"],
        },
    )
    earliest_start_datetime: Optional[DateTime] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Absolute earliest moment the run may start. Starts before it are "
                "dropped, in addition to 'time_windows' and the horizon. A date "
                "time without timezone is read as local time. This bound is never "
                "relaxed."
            ),
            "examples": [None, "2026-07-15T20:00:00+02:00"],
        },
    )
    deadline_datetime: Optional[DateTime] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Absolute deadline: the complete run must have *finished* at or "
                "before this moment (e.g. end of the day, or 03:00 tonight). A "
                "date time without timezone is read as local time. See "
                "'deadline_policy' for what happens when no start can meet it."
            ),
            "examples": [None, "2026-07-16T03:00:00+02:00"],
        },
    )
    deadline_policy: ConsumerDeadlinePolicy = Field(
        default=ConsumerDeadlinePolicy.BEST_EFFORT,
        json_schema_extra={
            "description": (
                "What to do when 'deadline_datetime' cannot be met: BEST_EFFORT "
                "runs as early as possible instead (warning logged), STRICT keeps "
                "the deadline (a ONCE consumer then fails the optimization)."
            ),
            "examples": ["BEST_EFFORT", "STRICT"],
        },
    )

    @field_validator("earliest_start_datetime", "deadline_datetime", mode="before")
    @classmethod
    def transform_to_datetime(cls, value: Any) -> Optional[DateTime]:
        """Accept the usual date time representations, naive input is local time."""
        if value is None:
            return None
        return to_datetime(value)

    @model_validator(mode="after")
    def validate_load_definition(self) -> Self:
        """Ensure a complete load definition and valid completed-cycle count."""
        if self.completed_cycles > self.num_cycles:
            raise ValueError("completed_cycles must not exceed num_cycles.")
        validate_home_appliance_load_definition(
            load_profile_power_w=self.load_profile_power_w,
            load_profile_interval_seconds=self.load_profile_interval_seconds,
            consumption_wh=self.consumption_wh,
            duration_h=self.duration_h,
        )
        return self

    @model_validator(mode="after")
    def validate_schedule_bounds(self) -> Self:
        """Reject an empty scheduling interval."""
        if self.earliest_start_datetime is not None and self.deadline_datetime is not None:
            if compare_datetimes(self.deadline_datetime, self.earliest_start_datetime).le:
                raise ValueError(
                    f"deadline_datetime {self.deadline_datetime} must be after "
                    f"earliest_start_datetime {self.earliest_start_datetime}."
                )
        return self


class HomeAppliance:
    """Non-vectorized simulation of a multi-cycle home appliance.

    A home appliance may execute multiple fixed-duration cycles during
    the simulation horizon. See the module docstring for the start-time
    repair algorithm.
    """

    def __init__(
        self,
        parameters: HomeApplianceParameters,
        optimization_hours: int,
        prediction_hours: int,
        slot_duration_h: float = 1.0,
    ) -> None:
        """Initializes the appliance and builds its allowed-start masks.

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

        # Start times for remaining cycles, in chronological order.
        self.start_hours: list[int] = []

        # Absolute cycle index corresponding to each remaining cycle.
        #
        # Example:
        #   num_cycles = 4
        #   completed_cycles = 2
        #
        #   remaining_cycle_indices = [2, 3]
        self.remaining_cycle_indices: list[int] = []

        # start_allowed[k][hour]
        #
        # k is the index into remaining_cycle_indices (NOT into the
        # chronologically-sorted self.start_hours).
        self.start_allowed: list[np.ndarray] = []

        self.start_earliest: list[int] = []
        self.start_latest: list[int] = []

        self._setup()

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
            or self.deadline_policy == ConsumerDeadlinePolicy.STRICT
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
            for window in (
                self.parameters.time_windows.windows if self.parameters.time_windows else []
            )
            if window.value is not None and int(window.value) == cycle_index
        ]

        run_duration = to_duration(f"{run_slots * self.slot_interval_seconds} seconds")
        allowed: list[int] = []
        for slot in range(first_start, last_start + 1):
            start_dt = slot0_datetime.add(seconds=slot * self.slot_interval_seconds)
            if (
                self.time_windows is None
                or self._windows_allow_run(self.time_windows.windows, start_dt, run_duration)
            ) and (
                not cycle_windows
                or self._windows_allow_run(cycle_windows, start_dt, run_duration, merge=True)
            ):
                allowed.append(slot)
        return allowed

    @staticmethod
    def _windows_allow_run(
        windows: Sequence[TimeWindow], start: DateTime, duration: Duration, *, merge: bool = False
    ) -> bool:
        """Check full coverage including windows anchored on previous local dates.

        Date and weekday restrictions belong to the window's opening day.
        Per-cycle windows retain their existing union semantics; shared windows
        require one complete containing occurrence.
        """
        intervals: list[tuple[DateTime, DateTime]] = []
        run_end = start + duration
        for window in windows:
            days_back = math.ceil(window.duration.total_seconds() / 86400) + 1
            for offset in range(days_back + 1):
                anchor = start.subtract(days=offset)
                if window.date is not None and anchor.date() != window.date:
                    continue
                if window.day_of_week is not None and anchor.day_of_week != window.day_of_week:
                    continue
                opening, closing = window._window_start_end(anchor)
                if opening <= start and run_end <= closing:
                    return True
                if merge and opening < run_end and closing > start:
                    intervals.append((opening, closing))
        covered_until = start
        for opening, closing in sorted(intervals):
            if opening > covered_until:
                break
            covered_until = max(covered_until, closing)
            if covered_until >= run_end:
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
        BEST_EFFORT deadline), counts as missed.

        Args:
            starts: Absolute start slots of the scheduled runs.
            slot0_datetime: Local, timezone-aware datetime of slot index 0.

        Returns:
            True if the deadline is set and not met.
        """
        if self.deadline_datetime is None:
            return False
        if not starts:
            return True
        timezone = slot0_datetime.timezone
        if timezone is None:
            raise ValueError("The optimization slot origin must have a timezone.")
        deadline = self.deadline_datetime.in_timezone(timezone)
        return any(self.run_end_datetime(start, slot0_datetime) > deadline for start in starts)

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

    def _setup(self) -> None:
        """Sets up appliance parameters and default time windows.

        When no ``time_windows`` are configured, builds a single
        placeholder window spanning the full prediction horizon with
        ``value`` left unset. ``CycleTimeWindowSequence.cycles_to_matrix``
        ignores windows whose ``value`` is ``None``, so this window never
        matches any cycle; every remaining cycle then falls through to
        the "no window for this cycle" branch in
        ``_build_cycle_start_allowed``, which treats it as unconstrained.
        The net effect is the same as having no windows at all, without
        assigning cycles an explicit (and misleadingly meaningful)
        ``value``.
        """
        if self.parameters.time_windows is None:
            self.parameters.time_windows = CycleTimeWindowSequence(
                windows=[
                    ValueTimeWindow(
                        start_time=to_time("00:00"),
                        duration=to_duration(f"{self.prediction_hours} hours"),
                    ),
                ]
            )

        self._build_start_allowed()

    @property
    def num_remaining_cycles(self) -> int:
        """int: Number of cycles which still have to be scheduled."""
        return max(0, self.num_cycles - self.completed_cycles)

    def set_completed_cycles(self, completed_cycles: int) -> None:
        """Sets the number of cycles already completed.

        Clears any previously scheduled start times and load curve,
        and rebuilds the allowed-start masks for the cycles that
        remain.

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
        self._build_start_allowed()

    # ------------------------------------------------------------------
    # Time-window handling
    # ------------------------------------------------------------------

    def _build_start_allowed(self) -> None:
        """Builds allowed start positions for all remaining cycles."""
        if self.parameters.time_windows is None:
            raise ValueError("Expected time windows in parameters, got {self.parameters}.")
        self.start_allowed = []
        self.start_earliest = []
        self.start_latest = []

        self.remaining_cycle_indices = list(
            range(
                self.completed_cycles,
                self.num_cycles,
            )
        )

        if not self.remaining_cycle_indices:
            return

        start_datetime = to_datetime().set(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        end_datetime = start_datetime.add(
            seconds=self.prediction_hours * self.slot_interval_seconds,
        )
        interval = to_duration(self.slot_interval_seconds)

        self._build_cycle_start_allowed(
            self.parameters.time_windows,
            start_datetime,
            end_datetime,
            interval,
        )

    def _build_cycle_start_allowed(
        self,
        time_windows: CycleTimeWindowSequence,
        start_datetime: DateTime,
        end_datetime: DateTime,
        interval: Duration,
    ) -> None:
        """Builds allowed starts from per-cycle windows.

        Args:
            time_windows: Windows associated with individual cycles.
            start_datetime: Start of the simulation horizon.
            end_datetime: End of the simulation horizon.
            interval: Step size used to sample the cycle windows.
        """
        cycle_indices, matrix = time_windows.cycles_to_matrix(
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            interval=interval,
        )

        cycle_to_row = {cycle: row for row, cycle in enumerate(cycle_indices)}

        max_start = max(
            0,
            self.prediction_hours - self.run_slots,
        )

        # The matrix tells us which individual *steps* are inside
        # the cycle window. We still have to check that the complete
        # appliance duration fits inside the window.
        for cycle in self.remaining_cycle_indices:
            row_index = cycle_to_row.get(cycle)

            if row_index is None:
                # No window for this cycle -> unconstrained.
                allowed = np.zeros(
                    self.prediction_hours,
                    dtype=bool,
                )
                allowed[: max_start + 1] = True
            else:
                allowed = self._build_duration_feasibility(
                    matrix[row_index],
                )

            self.start_allowed.append(allowed)

            allowed_indices = np.flatnonzero(allowed)

            if len(allowed_indices):
                self.start_earliest.append(int(allowed_indices[0]))
                self.start_latest.append(int(allowed_indices[-1]))
            else:
                self.start_earliest.append(0)
                self.start_latest.append(max_start)

    def _build_duration_feasibility(
        self,
        window_steps: np.ndarray,
    ) -> np.ndarray:
        """Returns starts where the complete appliance fits in a window.

        A start ``s`` is feasible when every step in
        ``window_steps[s : s + duration_h]`` lies inside the cycle's
        window (i.e. sums to ``duration_h``).

        Args:
            window_steps: Per-hour membership of the cycle's window
                (1.0 inside the window, 0.0 outside), one value per
                hour of the prediction horizon.

        Returns:
            Boolean mask, one entry per hour of the prediction
            horizon, ``True`` where a cycle of length ``duration_h``
            can start without leaving the window.
        """
        horizon = len(window_steps)
        max_start = max(
            0,
            horizon - self.run_slots,
        )

        allowed = np.zeros(
            horizon,
            dtype=bool,
        )

        if self.run_slots > horizon:
            return allowed

        # Rolling sum of `duration_h` consecutive steps, aligned so
        # that window_sums[s] == sum(window_steps[s : s + duration_h]).
        cumulative = np.concatenate(([0.0], np.cumsum(window_steps)))
        window_sums = cumulative[self.run_slots :] - cumulative[: -self.run_slots]

        allowed[: max_start + 1] = window_sums[: max_start + 1] == float(self.run_slots)

        return allowed

    # ------------------------------------------------------------------
    # Scheduling / repair
    # ------------------------------------------------------------------

    def set_starting_times(
        self,
        start_hours: list[int],
    ) -> list[int]:
        """Sets and repairs the start times of all remaining cycles.

        See the module docstring for the repair algorithm.

        Args:
            start_hours: Requested start hour for each remaining
                cycle, in the same order as ``remaining_cycle_indices``
                (i.e. matching ``self.start_allowed``).

        Returns:
            The repaired, chronologically ordered start hours.

        Raises:
            ValueError: If ``start_hours`` does not have exactly
                ``num_remaining_cycles`` entries.
        """
        if len(start_hours) != self.num_remaining_cycles:
            raise ValueError(
                f"Expected {self.num_remaining_cycles} start times, got {len(start_hours)}."
            )

        if self.num_remaining_cycles == 0:
            self.start_hours = []
            self.reset_load_curve()
            return []

        max_start = max(
            0,
            self.prediction_hours - self.run_slots,
        )

        # 1. Round and clip.
        starts = [
            max(
                0,
                min(
                    int(round(start)),
                    max_start,
                ),
            )
            for start in start_hours
        ]

        # 2. Snap each cycle to its nearest allowed start, keeping the
        #    start paired with the cycle (start_allowed index) it
        #    belongs to.
        repaired = [
            (
                self._repair_start(
                    start,
                    cycle_index,
                    max_start,
                ),
                cycle_index,
            )
            for cycle_index, start in enumerate(starts)
        ]

        # 3. Sort by start time, keeping each cycle's own index
        #    attached so its allowed-start mask is still used
        #    correctly in step 4.
        repaired.sort(key=lambda pair: pair[0])

        # 4. Enforce duration + minimum idle gap. Each cycle is
        #    pushed forward, if needed, to the next start allowed by
        #    its *own* window.
        min_next_start = self.run_slots + math.ceil(self.min_cycle_gap_h / self.slot_duration_h)

        final_starts = [repaired[0][0]]

        for index in range(1, len(repaired)):
            _, cycle_index = repaired[index]
            earliest = final_starts[index - 1] + min_next_start

            candidate = self._first_allowed_start_at_or_after(
                cycle_index=cycle_index,
                earliest=earliest,
            )

            if candidate is None:
                # No valid start remains for this cycle.
                final_starts.append(max_start)
            else:
                final_starts.append(candidate)

        # 5. Reconstruct load curve from the final schedule.
        self.start_hours = final_starts
        self._build_load_curve()

        return list(self.start_hours)

    def _repair_start(
        self,
        start: int,
        cycle_index: int,
        max_start: int,
    ) -> int:
        """Snaps a start to the nearest allowed start.

        Args:
            start: Requested (already rounded and clipped) start
                hour.
            cycle_index: Index into ``self.start_allowed`` for the
                cycle being repaired.
            max_start: Latest hour at which any cycle may start
                without exceeding the prediction horizon, used as a
                fallback when the cycle has no allowed start at all.

        Returns:
            The nearest hour allowed for this cycle, or ``max_start``
            if the cycle has no allowed start.
        """
        allowed = self.start_allowed[cycle_index]

        if not np.any(allowed):
            # Same fallback as the vectorized implementation.
            return max_start

        if allowed[start]:
            return start

        allowed_indices = np.flatnonzero(allowed)

        distances = np.abs(allowed_indices - start)

        return int(allowed_indices[np.argmin(distances)])

    def _first_allowed_start_at_or_after(
        self,
        cycle_index: int,
        earliest: int,
    ) -> int | None:
        """Returns the first allowed start at or after ``earliest``.

        Args:
            cycle_index: Index into ``self.start_allowed`` for the
                cycle being scheduled.
            earliest: Earliest acceptable start hour.

        Returns:
            The first allowed hour ``>= earliest``, or ``None`` if no
            such hour exists.
        """
        allowed = self.start_allowed[cycle_index]

        allowed_indices = np.flatnonzero(allowed)

        if len(allowed_indices) == 0:
            return None

        position = np.searchsorted(
            allowed_indices,
            max(0, earliest),
            side="left",
        )

        if position >= len(allowed_indices):
            return None

        return int(allowed_indices[position])

    # ------------------------------------------------------------------
    # Backwards-compatible single-cycle interface
    # ------------------------------------------------------------------

    def set_starting_time(
        self,
        start_hour: int,
        global_start_hour: int = 0,
    ) -> int:
        """Sets the start time of the first remaining cycle.

        Args:
            start_hour: Requested start hour for the first remaining
                cycle.
            global_start_hour: Retained for API compatibility with
                the old, single-cycle implementation. Unused.

        Returns:
            The repaired start hour of the first remaining cycle, or
            ``start_hour`` unchanged if there are no remaining
            cycles.
        """
        if self.num_remaining_cycles == 0:
            self.reset_load_curve()
            return start_hour

        if self.start_hours:
            starts = list(self.start_hours)
        else:
            starts = [self.start_earliest[index] for index in range(self.num_remaining_cycles)]

        starts[0] = start_hour

        repaired = self.set_starting_times(starts)

        return repaired[0]

    # ------------------------------------------------------------------
    # Load curve
    # ------------------------------------------------------------------

    def _build_load_curve(self) -> None:
        """Builds the load curve from all scheduled cycles."""
        self.reset_load_curve()

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
