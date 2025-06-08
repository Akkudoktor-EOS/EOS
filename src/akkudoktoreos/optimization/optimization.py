from typing import List, Optional

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel


class OptimizationCommonSettings(SettingsBaseModel):
    """General Optimization Configuration.

    Attributes:
        hours (int): Number of hours for optimizations.
    """

    hours: Optional[int] = Field(
        default=48, ge=0, description="Number of hours into the future for optimizations."
    )

    penalty: Optional[int] = Field(default=10, description="Penalty factor used in optimization.")

    ev_available_charge_rates_percent: Optional[List[float]] = Field(
        default=[
            0.0,
            6.0 / 16.0,
            # 7.0 / 16.0,
            8.0 / 16.0,
            # 9.0 / 16.0,
            10.0 / 16.0,
            # 11.0 / 16.0,
            12.0 / 16.0,
            # 13.0 / 16.0,
            14.0 / 16.0,
            # 15.0 / 16.0,
            1.0,
        ],
        description="Charge rates available for the EV in percent of maximum charge.",
    )
