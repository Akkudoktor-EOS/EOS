"""Household appliance model.

An appliance runs complete cycles. A cycle has a load profile (energy per slot, resampled to
the slot grid) that starts at a start slot chosen by the plan. A run that is already in
progress is a fixed load from now on. Overlapping cycles add up.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from akkudoktoreos.devices.models.jit import njit


@dataclass(frozen=True)
class ApplianceParams:
    """Parameters of a household appliance.

    Attributes:
        run_energy_wh: Energy per slot of one complete cycle [Wh], from its start slot on.
        pinned_load_wh: Fixed load per slot of the horizon [Wh], e.g. the remainder of a run
            in progress; None for none.
    """

    run_energy_wh: tuple[float, ...]
    pinned_load_wh: Optional[tuple[float, ...]] = None


def pack_appliances(
    appliances: tuple[ApplianceParams, ...], slots: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Flatten appliance parameters into ``(run_energy, run_slots, pinned)``.

    ``run_energy`` is ``(n, longest run)`` padded with 0, ``run_slots`` the number of slots of
    each run, ``pinned`` is ``(n, slots)``.
    """
    count = len(appliances)
    width = max((len(a.run_energy_wh) for a in appliances), default=1)
    run_energy = np.zeros((count, max(width, 1)))
    run_slots = np.zeros(count, dtype=np.int64)
    pinned = np.zeros((count, slots))
    for index, appliance in enumerate(appliances):
        run_energy[index, : len(appliance.run_energy_wh)] = appliance.run_energy_wh
        run_slots[index] = len(appliance.run_energy_wh)
        if appliance.pinned_load_wh is not None:
            length = min(slots, len(appliance.pinned_load_wh))
            pinned[index, :length] = appliance.pinned_load_wh[:length]
    return run_energy, run_slots, pinned


@njit
def appliance_load_wh(
    run_energy: np.ndarray,
    run_slots: np.ndarray,
    pinned: np.ndarray,
    starts: np.ndarray,
    appliance: int,
    slots: int,
    out: np.ndarray,
) -> None:
    """Write the load curve of one appliance [Wh per slot] into ``out``.

    Args:
        run_energy: Energy per slot of one cycle, per appliance.
        run_slots: Number of slots of one cycle, per appliance.
        pinned: Fixed load per slot, per appliance.
        starts: Start slots of the appliance's cycles; negative means the cycle is not run.
        appliance: Index of the appliance.
        slots: Number of slots of the horizon.
        out: Result array of length ``slots``.
    """
    for slot in range(slots):
        out[slot] = pinned[appliance, slot]
    for position in range(starts.shape[0]):
        start = starts[position]
        if start < 0:
            continue
        length = min(run_slots[appliance], slots - start)
        for offset in range(length):
            out[start + offset] += run_energy[appliance, offset]
