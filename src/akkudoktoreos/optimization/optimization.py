from typing import Optional, Union

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.pydantic import PydanticBaseModel, PydanticDateTimeDataFrame
from akkudoktoreos.utils.datetimeutil import DateTime


class GeneticCommonSettings(SettingsBaseModel):
    """General Genetic Optimization Algorithm Configuration."""

    individuals: Optional[int] = Field(
        default=300,
        ge=10,
        description="Number of individuals (solutions) to generate for the (initial) generation [>= 10]. Defaults to 300.",
        examples=[300],
    )

    generations: Optional[int] = Field(
        default=400,
        ge=10,
        description="Number of generations to evaluate the optimal solution [>= 10]. Defaults to 400.",
        examples=[400],
    )

    seed: Optional[int] = Field(
        default=None,
        ge=0,
        description="Fixed seed for genetic algorithm. Defaults to 'None' which means random seed.",
        examples=[None],
    )

    penalties: Optional[dict[str, Union[float, int, str]]] = Field(
        default=None,
        description="A dictionary of penalty function parameters consisting of a penalty function parameter name and the associated value.",
        examples=[
            {"ev_soc_miss": 10},
        ],
    )


class OptimizationCommonSettings(SettingsBaseModel):
    """General Optimization Configuration."""

    horizon_hours: Optional[int] = Field(
        default=24,
        ge=0,
        description="The general time window within which the energy optimization goal shall be achieved [h]. Defaults to 24 hours.",
        examples=[24],
    )

    interval: Optional[int] = Field(
        default=3600,
        ge=15 * 60,
        le=60 * 60,
        description="The optimization interval [sec].",
        examples=[60 * 60, 15 * 60],
    )

    genetic: Optional[GeneticCommonSettings] = Field(
        default=None,
        description="Genetic optimization algorithm configuration.",
        examples=[{"individuals": 400, "seed": None, "penalties": {"ev_soc_miss": 10}}],
    )


class OptimizationSolution(PydanticBaseModel):
    """General Optimization Solution."""

    id: str = Field(..., description="Unique ID for the optimization solution.")

    generated_at: DateTime = Field(..., description="Timestamp when the solution was generated.")

    comment: Optional[str] = Field(
        default=None, description="Optional comment or annotation for the solution."
    )

    valid_from: Optional[DateTime] = Field(
        default=None, description="Start time of the optimization solution."
    )

    valid_until: Optional[DateTime] = Field(
        default=None,
        description="End time of the optimization solution.",
    )

    total_losses_energy_wh: float = Field(
        description="The total losses in watt-hours over the entire period."
    )

    total_revenues_amt: float = Field(description="The total revenues [money amount].")

    total_costs_amt: float = Field(description="The total costs [money amount].")

    data: PydanticDateTimeDataFrame = Field(
        description=(
            "Datetime data frame with time series optimization data per optimization interval:"
            "- load_energy_wh: Load of all energy consumers in wh"
            "- grid_energy_wh: Grid energy feed in (negative) or consumption (positive) in wh"
            "- pv_prediction_energy_wh: PV energy prediction (positive) in wh"
            "- elec_price_prediction_amt_kwh: Electricity price prediction in money per kwh"
            "- costs_amt: Costs in money amount"
            "- revenue_amt: Revenue in money amount"
            "- losses_energy_wh: Energy losses in wh"
            "- <device-id>_operation_mode_id: Operation mode id of the device."
            "- <device-id>_operation_mode_factor: Operation mode factor of the device."
            "- <device-id>_soc_factor: State of charge of a battery/ electric vehicle device as factor of total capacity."
            "- <device-id>_energy_wh: Energy consumption (positive) of a device in wh."
        )
    )
