"""Start slot candidates of household appliances.

An appliance has one start slot per cycle, chosen from the allowed slots of that cycle with a
minimum gap to the previous cycle. The candidates are evaluated exactly (a batch of full
simulations), so no approximation is involved: the best candidate that beats the current start
is taken.
"""

import math
from typing import Optional

import numpy as np

from akkudoktoreos.optimization.search.settings import ApplianceSchedule


def joint_size(schedule: ApplianceSchedule, cycles: int) -> int:
    """Return an upper bound of the number of start combinations of all cycles."""
    count, windows = schedule.runnable(cycles)
    return math.prod(max(len(window), 1) for window in windows[:count])


def joint_rows(schedule: ApplianceSchedule, cycles: int) -> list[np.ndarray]:
    """Return every feasible combination of start slots of the cycles of an appliance."""
    count, windows = schedule.runnable(cycles)
    rows: list[list[int]] = []

    def extend(prefix: list[int], previous: Optional[int]) -> None:
        cycle = len(prefix)
        if cycle == cycles:
            rows.append(list(prefix))
            return
        if cycle >= count:
            extend(prefix + [-1], previous)
            return
        options = windows[cycle]
        if previous is not None:
            options = options[options >= previous + schedule.min_gap_slots]
        for start in options:
            extend(prefix + [int(start)], int(start))

    extend([], None)
    return [np.asarray(row, dtype=np.int64) for row in rows]


def cycle_rows(schedule: ApplianceSchedule, current: np.ndarray, cycle: int) -> list[np.ndarray]:
    """Return the rows in which only one cycle moves to another feasible start slot."""
    count, windows = schedule.runnable(len(current))
    if cycle >= count:
        return []
    options = windows[cycle]
    if cycle > 0:
        options = options[options >= int(current[cycle - 1]) + schedule.min_gap_slots]
    if cycle + 1 < count:
        options = options[options <= int(current[cycle + 1]) - schedule.min_gap_slots]
    rows = []
    for start in options:
        if int(start) != int(current[cycle]):
            row = current.copy()
            row[cycle] = start
            rows.append(row)
    return rows
