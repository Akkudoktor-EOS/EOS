"""Settings of the device by device search.

Kept in a module of its own, free of the compiled optimizer code, so that reading the
configuration does not import the numerical packages.
"""

from enum import StrEnum
from typing import Optional

from pydantic import Field

from akkudoktoreos.config.configabc import ConfigScope, SettingsBaseModel


class SearchEffort(StrEnum):
    """How much work the search puts into one planning.

    - LOW: coarse state grid and few passes; fastest.
    - NORMAL: the measured default.
    - HIGH: finer state grid and more passes; slower, rarely better by more than a percent.
    """

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"


class SearchCommonSettings(SettingsBaseModel):
    """Effort limits of the device by device search."""

    time_budget_seconds: Optional[float] = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        json_schema_extra={
            "x-scope": [str(ConfigScope.OHMRAKEL42)],
            "description": (
                "Time after which the search starts no further moves [s]. The result is never "
                "worse than the start plan; a move in progress is finished. None means no limit, "
                "the search then ends when no move improves the plan."
            ),
            "examples": [None, 30.0],
        },
    )
    effort: SearchEffort = Field(
        default=SearchEffort.NORMAL,
        json_schema_extra={
            "x-scope": [str(ConfigScope.OHMRAKEL42)],
            "description": (
                f"Search effort [{' | '.join(level.value for level in SearchEffort)}]. "
                "Sets the resolution of the state grid and the number of passes over the devices."
            ),
            "examples": ["NORMAL", "LOW"],
        },
    )
