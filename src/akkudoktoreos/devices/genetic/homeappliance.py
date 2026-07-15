"""Flexible consumer (home appliance) device model for genetic optimization.

A consumer is described by the energy of a **single complete run** resampled onto
the optimization slot grid. The optimizer decides, per run, at which slot the run
starts; :meth:`HomeAppliance.build_load_curve` then places the resampled run
energy at the chosen start(s). Several runs (DAILY mode) and several devices may
overlap; their energies simply add up.
"""

from typing import Optional

import numpy as np

from akkudoktoreos.config.configabc import TimeWindowSequence
from akkudoktoreos.devices.devicesabc import ConsumerScheduleMode
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

    def allowed_start_slots(
        self,
        *,
        slot0_datetime: DateTime,
        earliest_slot: int,
        horizon_end_slot: int,
    ) -> list[int]:
        """Return the sorted absolute start slots at which a full run is allowed.

        A start slot ``s`` is allowed when the complete run fits both the
        optimization horizon and (if configured) a single allowed time window:

        - ``earliest_slot <= s`` and ``s + run_slots <= horizon_end_slot``
        - with ``time_windows`` set, the run's whole occupied span starting at
          ``s`` is contained in one window (respecting weekday/date constraints).

        No snapping is performed: every returned slot is a genuinely valid start.

        Args:
            slot0_datetime: Local, timezone-aware datetime of slot index 0.
            earliest_slot: First slot the optimizer may schedule at ("now").
            horizon_end_slot: Exclusive upper bound; a run must end at or before.

        Returns:
            Sorted list of allowed absolute start slots (may be empty).
        """
        run_slots = self.run_slots
        if run_slots <= 0:
            return []
        last_start = min(horizon_end_slot, self.total_slots) - run_slots
        first_start = max(earliest_slot, 0)
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
