from typing import Optional

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel


class OptimizationCommonSettings(SettingsBaseModel):
    """General Optimization Configuration.

    Attributes:
        hours (int): Number of hours for optimizations.
    """

    hours: Optional[int] = Field(
        default=24, ge=0, description="Number of hours into the future for optimizations."
    )

    interval: Optional[int] = Field(
        default=3600,
        ge=15 * 60,
        le=60 * 60,
        description="The optimization interval [sec].",
        examples=[60 * 60, 15 * 60],
    )

    penalty: Optional[int] = Field(default=10, description="Penalty factor used in optimization.")
