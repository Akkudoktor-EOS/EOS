from typing import Optional, Union

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel


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

    hours: Optional[int] = Field(
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
