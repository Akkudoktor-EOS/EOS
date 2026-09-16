"""Simulation of a home appliance that runs one or more fixed-duration cycles.

This module models household devices such as dishwashers or washing
machines that must run for a fixed duration, possibly multiple times
(cycles), within one or more allowed time windows. Given a set of
requested start times, `HomeAppliance` repairs them into a
feasible, chronologically ordered schedule and produces the resulting
hourly load curve.

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

from typing import Optional

import numpy as np
from pydantic import Field

from akkudoktoreos.config.configabc import CycleTimeWindowSequence, ValueTimeWindow
from akkudoktoreos.optimization.genetic.geneticdevices import DeviceParameters
from akkudoktoreos.utils.datetimeutil import (
    DateTime,
    Duration,
    to_datetime,
    to_duration,
    to_time,
)


class HomeApplianceParameters(DeviceParameters):
    """Configuration for a simulated home appliance device."""

    device_id: str = Field(
        json_schema_extra={
            "description": "ID of home appliance",
            "examples": ["dishwasher"],
        }
    )
    consumption_wh: int = Field(
        gt=0,
        json_schema_extra={
            "description": (
                "An integer representing the energy consumption "
                "of a household device in watt-hours."
            ),
            "examples": [2000],
        },
    )
    duration_h: int = Field(
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
    ) -> None:
        """Initializes the appliance and builds its allowed-start masks.

        Args:
            parameters: The appliance's configuration.
            optimization_hours: Number of hours under active
                optimization.
            prediction_hours: Length of the simulation horizon, in
                hours.
        """
        self.parameters = parameters
        self.optimization_hours = optimization_hours
        self.prediction_hours = prediction_hours

        self.duration_h = parameters.duration_h
        self.consumption_wh = parameters.consumption_wh
        self.num_cycles = parameters.num_cycles
        self.min_cycle_gap_h = parameters.min_cycle_gap_h

        self.completed_cycles = 0

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
            hours=self.prediction_hours,
        )
        interval = to_duration("1 hour")

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
            self.prediction_hours - self.duration_h,
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
            horizon - self.duration_h,
        )

        allowed = np.zeros(
            horizon,
            dtype=bool,
        )

        if self.duration_h > horizon:
            return allowed

        # Rolling sum of `duration_h` consecutive steps, aligned so
        # that window_sums[s] == sum(window_steps[s : s + duration_h]).
        cumulative = np.concatenate(([0.0], np.cumsum(window_steps)))
        window_sums = cumulative[self.duration_h :] - cumulative[: -self.duration_h]

        allowed[: max_start + 1] = window_sums[: max_start + 1] == float(self.duration_h)

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
            self.prediction_hours - self.duration_h,
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
        min_next_start = self.duration_h + self.min_cycle_gap_h

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

        power_per_hour = self.consumption_wh / self.duration_h

        for start_hour in self.start_hours:
            if start_hour >= self.prediction_hours:
                continue

            end_hour = min(
                start_hour + self.duration_h,
                self.prediction_hours,
            )

            self.load_curve[start_hour:end_hour] += power_per_hour

    def reset_load_curve(self) -> None:
        """Resets the load curve to all zeros."""
        self.load_curve = np.zeros(self.prediction_hours)

    def get_load_curve(self) -> np.ndarray:
        """Returns the current hourly load curve, in watts."""
        return self.load_curve

    def get_load_for_hour(self, hour: int) -> float:
        """Returns the load for a specific hour.

        Args:
            hour: Hour of the prediction horizon to look up.

        Returns:
            The load, in watts, at ``hour``.

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
