"""Device by device search for the energy plan.

``DeviceSearch`` improves a plan device by device (dynamic programming over the state of charge of
one storage against the rest of the house), then pairs of storages, and accepts a move only if
the exact objective falls. See ``search.DeviceSearch`` for the entry point.
"""

from akkudoktoreos.optimization.search.checks import Violation, check_plan, repair_plan
from akkudoktoreos.optimization.search.search import (
    DeviceRef,
    DeviceSearch,
    SearchResult,
    SearchStats,
)
from akkudoktoreos.optimization.search.settings import (
    ApplianceSchedule,
    SearchSettings,
    SearchSpace,
)
from akkudoktoreos.optimization.search.warmstart import empty_plan, shift_plan

__all__ = [
    "ApplianceSchedule",
    "DeviceRef",
    "DeviceSearch",
    "SearchResult",
    "SearchSettings",
    "SearchSpace",
    "SearchStats",
    "Violation",
    "check_plan",
    "empty_plan",
    "repair_plan",
    "shift_plan",
]
