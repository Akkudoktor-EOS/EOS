"""Battery reserve for unplanned consumption.

The optimizer plans the battery for the forecast load only. A reserve keeps
energy in the battery for consumption the forecast does not contain, during
the time windows in which it is likely and grid energy is expensive:

- By the start of a window the planned state of charge has to reach the
  reserve, charged from PV or the grid in the slots before.
- Inside a window planned consumption does not discharge the battery below
  the reserve. The battery itself may still go down to its minimum SoC for
  unplanned consumption; the plan instructions do not limit it.
- If a run starts inside a window below the reserve (the reserve was used),
  that window keeps the start SoC: no refill at the window's prices and no
  further planned discharge. The next window again requires the full reserve.

The functions here are pure; the optimizer applies the result as a discharge
floor in the simulation and as a fitness penalty.
"""

from dataclasses import dataclass
from typing import Sequence

from akkudoktoreos.config.configabc import TimeWindow, TimeWindowSequence
from akkudoktoreos.utils.datetimeutil import DateTime

# Default reserve window when only the reserve is configured: before the
# first demand in the morning until the end of the evening peak.
DEFAULT_RESERVE_WINDOW_START = "06:00"
DEFAULT_RESERVE_WINDOW_DURATION = "16 hours"


def default_reserve_time_windows() -> TimeWindowSequence:
    """Return the default reserve window, 06:00-22:00 every day in local time."""
    window = TimeWindow.model_validate(
        {"start_time": DEFAULT_RESERVE_WINDOW_START, "duration": DEFAULT_RESERVE_WINDOW_DURATION}
    )
    return TimeWindowSequence(windows=[window])


def reserve_window_mask(
    windows: TimeWindowSequence,
    slot0_datetime: DateTime,
    start_slot: int,
    end_slot: int,
    slot_seconds: float,
) -> list[bool]:
    """Return whether each slot starts inside a reserve window.

    Args:
        windows: Reserve windows in local wall-clock time.
        slot0_datetime: Start of slot 0, in the local time zone the windows
            are read in.
        start_slot: First slot (the current one).
        end_slot: Exclusive end slot.
        slot_seconds: Slot duration [s].

    Returns:
        One flag per slot ``start_slot ... end_slot - 1``.
    """
    return [
        windows.contains(slot0_datetime.add(seconds=slot * slot_seconds))
        for slot in range(start_slot, end_slot)
    ]


@dataclass(frozen=True)
class ReserveTargets:
    """Reserve of one run, indexed from the current slot.

    Attributes:
        floor_percent: SoC planned discharge must not go below, per slot [%].
            0.0 outside the windows (the battery minimum applies).
        required_percent: SoC required at each slot boundary [%]: index 0 is
            the start of the current slot, index ``i`` the start of slot ``i``
            and the last one the end of the run. 0.0 means no requirement.
        relaxed_slots: Number of leading window slots that keep the start SoC
            because the run started inside a window below the reserve.
    """

    floor_percent: list[float]
    required_percent: list[float]
    relaxed_slots: int


def reserve_targets(
    reserve_percent: float,
    in_window: Sequence[bool],
    start_soc_percent: float,
) -> ReserveTargets:
    """Return the discharge floor and the required SoC of one run.

    Args:
        reserve_percent: Reserve [%].
        in_window: Window flag per slot from the current slot on, see
            ``reserve_window_mask``.
        start_soc_percent: State of charge at the start of the current slot [%].

    Returns:
        The targets. The SoC at the start of the current slot is measured, so
        it is never required.
    """
    slots = len(in_window)
    floor = [reserve_percent if inside else 0.0 for inside in in_window]

    # Started inside a window below the reserve: hold, refill for the next window.
    relaxed = 0
    if slots and in_window[0] and start_soc_percent < reserve_percent:
        while relaxed < slots and in_window[relaxed]:
            relaxed += 1
        held = max(start_soc_percent, 0.0)
        floor[:relaxed] = [held] * relaxed

    # A boundary is required when a window slot starts or ends there: the
    # reserve has to be there when the window opens and still when it closes.
    required = [0.0] * (slots + 1)
    for boundary in range(1, slots + 1):
        value = floor[boundary - 1] if in_window[boundary - 1] else 0.0
        if boundary < slots and in_window[boundary]:
            value = max(value, floor[boundary])
        required[boundary] = value

    return ReserveTargets(floor_percent=floor, required_percent=required, relaxed_slots=relaxed)


def reserve_shortfall(required_percent: Sequence[float], soc_percent: Sequence[float]) -> float:
    """Return the summed shortfall of the SoC below the requirement [% x boundaries].

    Args:
        required_percent: Required SoC per slot boundary, see ``ReserveTargets``.
        soc_percent: Planned SoC at the same boundaries. Missing or non-finite
            values count as no shortfall.

    Returns:
        Sum of ``required - soc`` over the boundaries where the SoC is lower.
    """
    shortfall = 0.0
    for required, soc in zip(required_percent, soc_percent):
        if required > 0.0 and soc == soc and soc < required - 1e-9:
            shortfall += required - soc
    return shortfall
