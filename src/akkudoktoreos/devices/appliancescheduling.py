"""Start slots of household appliances: what the planning may choose between.

A household appliance runs complete cycles of a fixed load profile. The plan chooses the start
slot of every cycle; this module computes which start slots are allowed from the configuration
(time windows, earliest start, deadline, cycles per day, minimum gap) and the state of the
appliance (cycles already completed today, a run in progress).

``plan_appliance`` is the entry point of the request layer: it returns the run profile, the
fixed load of a run in progress and, per cycle that still has to run, the allowed start slots.
``estimate_daily_load`` gives the expected load of the daily appliances beyond the horizon,
which the value of stored energy needs.

The rules follow the former optimizer's appliance device and are tested against it.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
from loguru import logger

from akkudoktoreos.config.configabc import TimeWindow
from akkudoktoreos.devices.devicesabc import ConsumerDeadlinePolicy, ConsumerScheduleMode
from akkudoktoreos.devices.settings.homeappliancesettings import HomeApplianceCommonSettings
from akkudoktoreos.utils.datetimeutil import DateTime, Duration, to_duration

# A run in progress whose start is more than this many run durations ago is taken as a stale
# report (the executor stopped updating it).
STALE_RUN_FACTOR = 2.0


def resample_power_to_slot_energy(
    power_w: Sequence[float],
    input_interval_seconds: float,
    slot_interval_seconds: float,
) -> np.ndarray:
    """Resample a piecewise-constant power profile to per-slot energy.

    Each input value ``power_w[i]`` is a constant power [W] over the interval
    ``[i * input_interval_seconds, (i + 1) * input_interval_seconds)``. The energy of every
    output slot is the time weighted integral of the input power over that slot, so the energy
    is conserved for any ratio of the two intervals (10 -> 15 or 20 -> 15 minutes included).

    Args:
        power_w: Power values [W] of a single run.
        input_interval_seconds: Duration of one input step [s] (> 0).
        slot_interval_seconds: Duration of one output slot [s] (> 0).

    Returns:
        Energy per slot [Wh]; the length is the number of slots the run occupies.
    """
    if not math.isfinite(input_interval_seconds) or input_interval_seconds <= 0:
        raise ValueError("Input interval must be finite and positive.")
    if not math.isfinite(slot_interval_seconds) or slot_interval_seconds <= 0:
        raise ValueError("Slot interval must be finite and positive.")
    if not len(power_w) or any(not math.isfinite(p) or p < 0 for p in power_w):
        raise ValueError("Power profile must contain finite non-negative values.")
    total_seconds = len(power_w) * input_interval_seconds
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


class ApplianceTimes:
    """Run profile, run in progress and allowed start slots of one household appliance."""

    def __init__(
        self,
        settings: HomeApplianceCommonSettings,
        slot_duration_h: float,
        total_slots: int,
        *,
        completed_cycles: int = 0,
        run_started: Optional[DateTime] = None,
        ignore_deadline: bool = False,
    ) -> None:
        """Prepare one appliance.

        Args:
            settings: Configuration of the appliance.
            slot_duration_h: Duration of a slot [h].
            total_slots: Length of the horizon in slots; a run must end within it.
            completed_cycles: Cycles already completed on the first planning day.
            run_started: Start of the run in progress, None when the appliance is not running.
            ignore_deadline: Plan without the configured deadline.
        """
        if not math.isfinite(slot_duration_h) or slot_duration_h <= 0:
            raise ValueError("Slot duration must be finite and positive.")
        self.settings = settings
        self.device_id = settings.device_id
        self.slot_duration_h = slot_duration_h
        self.slot_seconds = slot_duration_h * 3600
        self.total_slots = total_slots
        self.schedule_mode = settings.schedule_mode
        self.earliest_start_datetime = settings.earliest_start_datetime
        self.deadline_datetime = None if ignore_deadline else settings.deadline_datetime
        self.deadline_policy = settings.deadline_policy
        self.deadline_relaxed = False
        self.num_cycles = settings.effective_num_cycles
        self.min_cycle_gap_h = settings.min_cycle_gap_h
        self.completed_cycles = completed_cycles
        if completed_cycles > self.num_cycles:
            raise ValueError("completed_cycles must not exceed num_cycles.")
        self.run_started = run_started
        self._build_run_profile()
        self.pinned_load = np.zeros(total_slots)
        self.pinned_end_slot = 0
        self.pinned_start_datetime: Optional[DateTime] = None
        self.pinned_end_datetime: Optional[DateTime] = None
        self.pinned_run_date: Any = None

    def _build_run_profile(self) -> None:
        """Build the per-slot energy [Wh] of a single complete run."""
        settings = self.settings
        if settings.load_profile_power_w is not None:
            power = [float(value) for value in settings.load_profile_power_w]
            input_interval = settings.load_profile_interval_seconds or self.slot_seconds
        else:
            duration_h = settings.duration_h
            consumption_wh = settings.consumption_wh
            if duration_h is None or consumption_wh is None:
                raise ValueError("Flat appliance load requires duration and consumption.")
            power = [consumption_wh / duration_h]
            input_interval = duration_h * 3600
        self.run_energy_wh = resample_power_to_slot_energy(
            power, float(input_interval), float(self.slot_seconds)
        )
        self.run_slots = int(len(self.run_energy_wh))
        self.run_power_w = power
        self.run_power_interval_seconds = float(input_interval)
        self.run_duration_seconds = len(power) * float(input_interval)

    @property
    def run_in_progress(self) -> bool:
        """Whether a run in progress is pinned."""
        return self.pinned_start_datetime is not None

    @property
    def separation_slots(self) -> int:
        """Slots between the starts of two consecutive cycles: the run plus the minimum gap."""
        return self.run_slots + math.ceil(self.min_cycle_gap_h / self.slot_duration_h)

    def pin_run_in_progress(self, slot0: DateTime, observation: DateTime) -> None:
        """Fix the remainder of the run in progress as load from now on.

        A run in progress is kept until the device reports it as finished. Its remaining energy
        is placed from the observation time on, independent of windows and deadline. A run past
        its configured duration still runs: slot 0 stays occupied without extra energy. A start
        older than ``STALE_RUN_FACTOR`` run durations is taken as a stale report and ignored.

        Args:
            slot0: Local, timezone aware start of slot 0.
            observation: Moment of the planning (within slot 0).
        """
        self.pinned_load = np.zeros(self.total_slots)
        self.pinned_end_slot = 0
        self.pinned_start_datetime = None
        self.pinned_end_datetime = None
        self.pinned_run_date = None
        started = self.run_started
        if started is None or self.total_slots <= 0:
            return
        timezone = slot0.timezone
        if timezone is None:
            raise ValueError("The slot origin must have a timezone.")
        started = started.in_timezone(timezone)
        now = observation.in_timezone(timezone)
        elapsed = (now - started).total_seconds()
        if elapsed < 0 or elapsed > STALE_RUN_FACTOR * self.run_duration_seconds:
            logger.error(
                "Home appliance '{}': run start {} is not plausible at {} "
                "(run duration {} s); planned as not running.",
                self.device_id,
                started,
                now,
                self.run_duration_seconds,
            )
            return
        slot_seconds = self.slot_seconds
        now_offset = (now - slot0).total_seconds()
        start_offset = (started - slot0).total_seconds()
        end_offset = start_offset + self.run_duration_seconds
        for index, power in enumerate(self.run_power_w):
            segment_start = max(start_offset + index * self.run_power_interval_seconds, now_offset)
            segment_end = start_offset + (index + 1) * self.run_power_interval_seconds
            while power > 0 and segment_start < segment_end:
                slot = int(segment_start // slot_seconds)
                if slot >= self.total_slots:
                    break
                part_end = min(segment_end, (slot + 1) * slot_seconds)
                self.pinned_load[slot] += power * (part_end - segment_start) / 3600.0
                segment_start = part_end
        self.pinned_end_slot = min(
            self.total_slots, max(1, math.ceil(end_offset / slot_seconds - 1e-9))
        )
        self.pinned_start_datetime = started
        self.pinned_end_datetime = max(started.add(seconds=self.run_duration_seconds), now)
        self.pinned_run_date = started.in_timezone(timezone).date()
        if elapsed >= self.run_duration_seconds:
            logger.info(
                "Home appliance '{}': run started {} is past its duration of {} s "
                "and still running; kept until the device reports it finished.",
                self.device_id,
                started,
                self.run_duration_seconds,
            )

    def next_cycle_earliest_slot(self) -> int:
        """Earliest slot for the next cycle: after a pinned run and its gap."""
        if not self.run_in_progress:
            return 0
        return self.pinned_end_slot + math.ceil(self.min_cycle_gap_h / self.slot_duration_h)

    def completed_cycles_on(self, first_date: Any) -> int:
        """Cycles counted as done on the first planning day, including a run in progress."""
        pinned = 0
        if self.run_in_progress and (
            self.schedule_mode == ConsumerScheduleMode.ONCE or self.pinned_run_date == first_date
        ):
            pinned = 1
        return min(self.num_cycles, self.completed_cycles + pinned)

    def _slot_offset(self, moment: DateTime, slot0: DateTime, *, round_up: bool) -> int:
        """Convert an absolute moment into a slot index relative to slot 0."""
        timezone = slot0.timezone
        if timezone is None:
            raise ValueError("The slot origin must have a timezone.")
        seconds = (moment.in_timezone(timezone) - slot0).total_seconds()
        exact = seconds / self.slot_seconds
        # The tolerance keeps a moment exactly on a slot boundary from moving to a neighbour.
        return math.ceil(exact - 1e-9) if round_up else math.floor(exact + 1e-9)

    def allowed_start_slots(
        self,
        *,
        slot0: DateTime,
        earliest_slot: int,
        horizon_end_slot: int,
        cycle_index: Optional[int] = None,
    ) -> list[int]:
        """Return the sorted start slots at which a full run is allowed.

        A start slot ``s`` is allowed when the complete run fits the horizon, the earliest start
        and the deadline (the run must have ended by then), and lies in one allowed time window
        (respecting weekday, date and month). When a deadline leaves no start at all and the
        policy is BEST_EFFORT, the deadline is dropped and only the earliest still possible
        start is offered (a warning is logged and ``deadline_relaxed`` is set); with several
        cycles all choices stay so that the earliest joint schedule can be found.

        Args:
            slot0: Local, timezone aware start of slot 0.
            earliest_slot: First slot the plan may use ("now").
            horizon_end_slot: Exclusive upper bound; a run must end at or before.
            cycle_index: Configured cycle index, independent of completed cycles.

        Returns:
            The allowed start slots (may be empty).
        """
        self.deadline_relaxed = False
        allowed = self._allowed_start_slots(
            slot0=slot0,
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
            slot0=slot0,
            earliest_slot=earliest_slot,
            horizon_end_slot=horizon_end_slot,
            apply_deadline=False,
            cycle_index=cycle_index,
        )
        if not relaxed:
            return relaxed
        self.deadline_relaxed = True
        earliest = relaxed[:1] if self.num_cycles == 1 else relaxed
        logger.warning(
            "Home appliance '{}': deadline {} can not be met - running as early as "
            "possible instead (BEST_EFFORT). Run ends {}.",
            self.device_id,
            self.deadline_datetime,
            self.run_end_datetime(earliest[0], slot0),
        )
        return earliest

    def _allowed_start_slots(
        self,
        *,
        slot0: DateTime,
        earliest_slot: int,
        horizon_end_slot: int,
        cycle_index: Optional[int] = None,
        apply_deadline: bool,
    ) -> list[int]:
        """Compute the allowed start slots for one set of constraints."""
        run_slots = self.run_slots
        if run_slots <= 0:
            return []
        first_start = max(earliest_slot, 0)
        if self.earliest_start_datetime is not None:
            first_start = max(
                first_start,
                self._slot_offset(self.earliest_start_datetime, slot0, round_up=True),
            )
        end_bound = min(horizon_end_slot, self.total_slots)
        if apply_deadline and self.deadline_datetime is not None:
            end_bound = min(
                end_bound, self._slot_offset(self.deadline_datetime, slot0, round_up=False)
            )
        last_start = end_bound - run_slots
        if last_start < first_start:
            return []
        if cycle_index is not None and not 0 <= cycle_index < self.num_cycles:
            raise ValueError("Cycle index is outside the configured cycle range.")
        cycle_windows = [
            window
            for window in (
                self.settings.cycle_time_windows.windows if self.settings.cycle_time_windows else []
            )
            if window.value is not None and int(window.value) == cycle_index
        ]
        shared = self.settings.time_windows
        run_duration = to_duration(f"{run_slots * self.slot_seconds} seconds")
        allowed: list[int] = []
        for slot in range(first_start, last_start + 1):
            start_dt = slot0.add(seconds=slot * self.slot_seconds)
            if (
                shared is None or self._windows_allow_run(shared.windows, start_dt, run_duration)
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

        Date, weekday and month restrictions belong to the window's opening day. Per-cycle
        windows keep their union semantics; shared windows require one complete containing
        occurrence. Offsets from the run start are compared, so the repeated hour of a DST
        transition cannot be mixed up.
        """
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
                opening, closing = window._window_start_end(anchor)
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

    def run_end_datetime(self, start_slot: int, slot0: DateTime) -> DateTime:
        """Return the moment at which a run started at ``start_slot`` finishes."""
        return slot0.add(seconds=(start_slot + self.run_slots) * self.slot_seconds)

    def deadline_missed(self, starts: Sequence[int], slot0: DateTime) -> bool:
        """Whether the scheduled runs violate the configured deadline.

        A consumer that was not scheduled at all, or whose run ends after the deadline (a
        relaxed BEST_EFFORT deadline), counts as missed; a run in progress counts with its
        expected end.
        """
        if self.deadline_datetime is None:
            return False
        timezone = slot0.timezone
        if timezone is None:
            raise ValueError("The slot origin must have a timezone.")
        ends = [self.run_end_datetime(start, slot0) for start in starts]
        if self.pinned_end_datetime is not None:
            ends.append(self.pinned_end_datetime)
        if not ends:
            return True
        deadline = self.deadline_datetime.in_timezone(timezone)
        return any(end > deadline for end in ends)


@dataclass(frozen=True)
class ApplianceRun:
    """One run an appliance still has to make within the horizon.

    Attributes:
        cycle: Configured cycle index.
        date: Local date of the run (daily appliances), None for a once appliance.
        allowed_starts: Allowed start slots, ascending, never empty.
        deadline_relaxed: The deadline could not be met; the run goes as early as possible.
    """

    cycle: int
    date: Any
    allowed_starts: tuple[int, ...]
    deadline_relaxed: bool = False


@dataclass(frozen=True)
class AppliancePlanInput:
    """What the planning needs to know about one appliance.

    Attributes:
        model: The appliance (run profile, run in progress).
        runs: The runs to plan, in chronological order of their allowed slots.
        min_gap_slots: Minimum distance between the starts of consecutive runs.
    """

    model: ApplianceTimes
    runs: tuple[ApplianceRun, ...]
    min_gap_slots: int
    run_dates: frozenset[Any] = field(default_factory=frozenset)


def plan_appliance(
    model: ApplianceTimes, slot0: DateTime, start_slot: int = 0
) -> AppliancePlanInput:
    """Compute the runs an appliance still has to make and the start slots of each.

    Daily appliances get one run per configured cycle and local day with a feasible full run;
    once appliances one run per cycle that is not completed. A run in progress counts as done
    for its day and delays the next cycle until after its end and the minimum gap.

    Args:
        model: The appliance, with its run in progress already pinned.
        slot0: Local, timezone aware start of slot 0.
        start_slot: First slot the plan may use.

    Raises:
        ValueError: If a run has no valid start within its windows, deadline and horizon.
    """
    first_date = slot0.date()
    cycles = range(model.num_cycles)
    completed = model.completed_cycles_on(first_date)
    earliest_slot = max(start_slot, model.next_cycle_earliest_slot())
    allowed_by_cycle: dict[int, list[int]] = {}
    relaxed_by_cycle: dict[int, bool] = {}
    for cycle in cycles:
        allowed_by_cycle[cycle] = model.allowed_start_slots(
            slot0=slot0,
            earliest_slot=earliest_slot,
            horizon_end_slot=model.total_slots,
            cycle_index=cycle,
        )
        relaxed_by_cycle[cycle] = model.deadline_relaxed
    groups: list[tuple[Any, int, list[int]]]
    if model.schedule_mode == ConsumerScheduleMode.ONCE:
        groups = [(None, c, allowed_by_cycle[c]) for c in cycles if c >= completed]
    else:
        by_date: dict[Any, dict[int, list[int]]] = {}
        for cycle, allowed in allowed_by_cycle.items():
            for slot in allowed:
                date = slot0.add(seconds=slot * model.slot_seconds).date()
                if date == first_date and cycle < completed:
                    continue
                by_date.setdefault(date, {}).setdefault(cycle, []).append(slot)
        groups = [
            (date, cycle, allowed)
            for date, cycle_slots in sorted(by_date.items())
            for cycle, allowed in sorted(cycle_slots.items())
        ]
        # A partially elapsed first day may have no feasible remaining run; later complete
        # days must keep all configured cycles.
        for date, cycle_slots in by_date.items():
            expected = {c for c in cycles if date != first_date or c >= completed}
            if set(cycle_slots) != expected:
                raise ValueError(
                    f"Home appliance '{model.device_id}' has no complete cycle schedule on {date}."
                )
    runs: list[ApplianceRun] = []
    for date, cycle, allowed in groups:
        if not allowed:
            raise ValueError(
                f"Home appliance '{model.device_id}' cycle {cycle} has no valid start within its "
                "windows, deadline and control horizon."
            )
        runs.append(ApplianceRun(cycle, date, tuple(allowed), relaxed_by_cycle[cycle]))
    # The plan places the runs in this order; cycle numbers identify windows, they do not
    # impose a temporal order.
    runs.sort(key=lambda run: (run.allowed_starts[0], run.allowed_starts[-1], run.cycle))
    runs = _pin_relaxed_runs(runs, model.separation_slots)
    return AppliancePlanInput(
        model=model,
        runs=tuple(runs),
        min_gap_slots=model.separation_slots,
        run_dates=frozenset(run.date for run in runs if run.date is not None),
    )


def _pin_relaxed_runs(runs: list[ApplianceRun], separation: int) -> list[ApplianceRun]:
    """Reduce the runs of a missed deadline to the earliest joint placement.

    The deadline is already missed, so these runs go as soon as possible rather than as cheap
    as possible: each takes the earliest allowed slot that keeps the gap to the run before.
    """
    result: list[ApplianceRun] = []
    previous: Optional[int] = None
    for run in runs:
        starts = run.allowed_starts
        usable = [s for s in starts if previous is None or s >= previous + separation]
        if run.deadline_relaxed and usable:
            starts = (usable[0],)
        result.append(ApplianceRun(run.cycle, run.date, starts, run.deadline_relaxed))
        previous = usable[0] if usable else starts[0]
    return result


def estimate_daily_load(
    inputs: Sequence[AppliancePlanInput],
    slot0: DateTime,
    start_slot: int,
    end_slot: int,
    *,
    skip_planned_days: bool = False,
) -> np.ndarray:
    """Return the expected load of the daily appliances over a slot range [Wh per slot].

    Daily appliances repeat once per local day, so their load is part of the demand beyond the
    horizon even though the plan never schedules it there. Each (day, cycle) run is placed at
    its earliest allowed start inside its time window; cycles of one appliance on one day keep
    the minimum gap. A deadline only limits the run it was set for and does not cap the
    following days. Once appliances have no recurring schedule and are left out.

    Args:
        inputs: The appliances as planned.
        slot0: Local, timezone aware start of slot 0.
        start_slot: First slot of the range.
        end_slot: Exclusive end slot of the range.
        skip_planned_days: Leave out local days whose run is already planned within the
            horizon, so a day that straddles the horizon end is not counted twice.

    Returns:
        Load per slot over ``[start_slot, end_slot)``.
    """
    estimate = np.zeros(max(end_slot - start_slot, 0), dtype=float)
    if start_slot >= end_slot:
        return estimate
    first_date = slot0.date()
    for item in inputs:
        appliance = item.model
        if appliance.schedule_mode != ConsumerScheduleMode.DAILY:
            continue
        completed = appliance.completed_cycles_on(first_date)
        # The configured appliance only spans the horizon, so its window logic cannot see the
        # tail: a probe sized to the estimated range shares the parameters (windows, earliest
        # start) but has no deadline.
        probe = ApplianceTimes(
            appliance.settings,
            appliance.slot_duration_h,
            end_slot,
            completed_cycles=appliance.completed_cycles,
            ignore_deadline=True,
        )
        by_date: dict[Any, dict[int, list[int]]] = {}
        for cycle in range(appliance.num_cycles):
            allowed = probe.allowed_start_slots(
                slot0=slot0, earliest_slot=start_slot, horizon_end_slot=end_slot, cycle_index=cycle
            )
            for slot in allowed:
                date = slot0.add(seconds=slot * appliance.slot_seconds).date()
                if date == first_date and cycle < completed:
                    continue
                by_date.setdefault(date, {}).setdefault(cycle, []).append(slot)
        if skip_planned_days:
            by_date = {d: c for d, c in by_date.items() if d not in item.run_dates}
        gap_slots = appliance.run_slots + math.ceil(
            appliance.min_cycle_gap_h / appliance.slot_duration_h
        )
        first_start = max(start_slot, appliance.next_cycle_earliest_slot())
        for cycle_slots in by_date.values():
            earliest = first_start
            for cycle in sorted(cycle_slots, key=lambda c: cycle_slots[c][0]):
                starts = [slot for slot in cycle_slots[cycle] if slot >= earliest]
                if not starts:
                    continue
                start = starts[0]
                run_end = min(start + appliance.run_slots, end_slot)
                estimate[start - start_slot : run_end - start_slot] += appliance.run_energy_wh[
                    : run_end - start
                ]
                earliest = start + gap_slots
    return estimate
