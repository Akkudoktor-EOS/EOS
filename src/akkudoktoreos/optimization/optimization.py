from typing import List, Optional

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.logging import get_logger

logger = get_logger(__name__)


class OptimizationCommonSettings(SettingsBaseModel):
    """Base configuration for optimization settings.

    Attributes:
        optimization_hours (int): Number of hours for optimizations.
    """

    optimization_hours: Optional[int] = Field(
        default=24, ge=0, description="Number of hours into the future for optimizations."
    )

    optimization_penalty: Optional[int] = Field(
        default=10, description="Penalty factor used in optimization."
    )

    optimization_ev_available_charge_rates_percent: Optional[List[float]] = Field(
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
