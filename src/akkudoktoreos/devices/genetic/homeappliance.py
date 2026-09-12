"""Flexible consumer (home appliance) device model for genetic optimization.

A consumer is described by the energy of a **single complete run** resampled onto
the optimization slot grid. The optimizer decides, per run, at which slot the run
starts; :meth:`HomeAppliance.build_load_curve` then places the resampled run
energy at the chosen start(s). Several runs (DAILY mode) and several devices may
overlap; their energies simply add up.
"""

import math
from typing import Optional

import numpy as np
from loguru import logger

from akkudoktoreos.config.configabc import TimeWindowSequence
from akkudoktoreos.devices.devicesabc import ConsumerDeadlinePolicy, ConsumerScheduleMode
from akkudoktoreos.optimization.genetic.geneticdevices import HomeApplianceParameters
from akkudoktoreos.utils.datetimeutil import DateTime, to_duration


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


class HomeAppliance:
    """A flexible consumer scheduled onto the optimization slot grid."""

    def __init__(
        self,
        parameters: HomeApplianceParameters,
        optimization_hours: int,
        prediction_hours: int,
        slot_duration_h: float = 1.0,
    ):
        """Initialize the appliance and precompute its per-slot run energy.

        Args:
            parameters: Appliance configuration (load definition, schedule mode,
                allowed time windows).
            optimization_hours: Optimization horizon in hours (informational).
            prediction_hours: Total number of optimization slots of the run grid.
            slot_duration_h: Length of one optimization slot in hours (1.0 hourly,
                0.25 at 15 min).
        """
        self.parameters: HomeApplianceParameters = parameters
        self.optimization_hours = optimization_hours
        self.total_slots = int(prediction_hours)
        self.slot_duration_h = slot_duration_h
        self.slot_interval_seconds = int(round(slot_duration_h * 3600))
        self.device_id: str = parameters.device_id
        self.schedule_mode: ConsumerScheduleMode = parameters.schedule_mode
        self.time_windows: Optional[TimeWindowSequence] = parameters.time_windows
        self.earliest_start_datetime: Optional[DateTime] = parameters.earliest_start_datetime
        self.deadline_datetime: Optional[DateTime] = parameters.deadline_datetime
        self.deadline_policy: ConsumerDeadlinePolicy = parameters.deadline_policy
        # Set when a BEST_EFFORT deadline had to be dropped in the last
        # allowed_start_slots() call, so callers can report the miss.
        self.deadline_relaxed: bool = False
        self._build_run_profile()
        self.reset_load_curve()

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
        seconds = (moment.in_timezone(slot0_datetime.timezone) - slot0_datetime).total_seconds()
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
        is set): the run happens too late anyway, so it is scheduled with the
        smallest possible delay instead of at the cheapest slot.

        No snapping is performed: every returned slot is a genuinely valid start.

        Args:
            slot0_datetime: Local, timezone-aware datetime of slot index 0.
            earliest_slot: First slot the optimizer may schedule at ("now").
            horizon_end_slot: Exclusive upper bound; a run must end at or before.

        Returns:
            Sorted list of allowed absolute start slots (may be empty).
        """
        self.deadline_relaxed = False
        allowed = self._allowed_start_slots(
            slot0_datetime=slot0_datetime,
            earliest_slot=earliest_slot,
            horizon_end_slot=horizon_end_slot,
            apply_deadline=True,
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
        )
        if not relaxed:
            return relaxed
        self.deadline_relaxed = True
        # Keep only the earliest possible start: the deadline is already missed,
        # so the run is scheduled as soon as possible rather than as cheap as
        # possible.
        earliest = relaxed[:1]
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
        apply_deadline: bool,
    ) -> list[int]:
        """Compute the allowed start slots for one set of constraints.

        Args:
            slot0_datetime: Local, timezone-aware datetime of slot index 0.
            earliest_slot: First slot the optimizer may schedule at ("now").
            horizon_end_slot: Exclusive upper bound; a run must end at or before.
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

        if self.time_windows is None:
            return list(range(first_start, last_start + 1))

        run_duration = to_duration(f"{run_slots * self.slot_interval_seconds} seconds")
        allowed: list[int] = []
        for slot in range(first_start, last_start + 1):
            start_dt = slot0_datetime.add(seconds=slot * self.slot_interval_seconds)
            if self.time_windows.contains(start_dt, duration=run_duration):
                allowed.append(slot)
        return allowed

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
        deadline = self.deadline_datetime.in_timezone(slot0_datetime.timezone)
        return any(self.run_end_datetime(start, slot0_datetime) > deadline for start in starts)

    def build_load_curve(self, starts: list[int]) -> None:
        """Place the resampled run energy at each decoded start slot.

        Multiple runs may overlap; their per-slot energies are summed.

        Args:
            starts: Absolute start slots of the scheduled runs.
        """
        self.reset_load_curve()
        for start in starts:
            if start is None or start < 0:
                continue
            end = min(start + self.run_slots, self.total_slots)
            length = end - start
            if length > 0:
                self.load_curve[start:end] += self.run_energy_wh[:length]

    def reset_load_curve(self) -> None:
        """Reset the load curve to all zeros."""
        self.load_curve = np.zeros(self.total_slots)

    def get_load_curve(self) -> np.ndarray:
        """Return the current per-slot load curve [Wh]."""
        return self.load_curve

    def get_load_for_hour(self, hour: int) -> float:
        """Return the load [Wh] for a specific slot.

        Args:
            hour: The slot index for which the load is queried.

        Returns:
            The energy in watt-hours for the specified slot.
        """
        if hour < 0 or hour >= self.total_slots:
            raise ValueError(
                f"The specified slot {hour} is outside the available time frame {self.total_slots}."
            )
        return self.load_curve[hour]
