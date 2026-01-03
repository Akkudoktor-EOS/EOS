from typing import Optional, Union

from pydantic import Field, computed_field, model_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.core.pydantic import (
    PydanticBaseModel,
    PydanticDateTimeDataFrame,
)
from akkudoktoreos.utils.datetimeutil import DateTime


class GeneticCommonSettings(SettingsBaseModel):
    """General Genetic Optimization Algorithm Configuration."""

    individuals: Optional[int] = Field(
        default=300,
        ge=10,
        json_schema_extra={
            "description": "Number of individuals (solutions) to generate for the (initial) generation [>= 10]. Defaults to 300.",
            "examples": [300],
        },
    )

    generations: Optional[int] = Field(
        default=400,
        ge=10,
        json_schema_extra={
            "description": "Number of generations to evaluate the optimal solution [>= 10]. Defaults to 400.",
            "examples": [400],
        },
    )

    seed: Optional[int] = Field(
        default=None,
        ge=0,
        json_schema_extra={
            "description": "Fixed seed for genetic algorithm. Defaults to 'None' which means random seed.",
            "examples": [None],
        },
    )

    penalties: Optional[dict[str, Union[float, int, str]]] = Field(
        default=None,
        json_schema_extra={
            "description": "A dictionary of penalty function parameters consisting of a penalty function parameter name and the associated value.",
            "examples": [
                {"ev_soc_miss": 10},
            ],
        },
    )


class OptimizationCommonSettings(SettingsBaseModel):
    """General Optimization Configuration."""

    horizon_hours: Optional[int] = Field(
        default=24,
        ge=0,
        json_schema_extra={
            "description": "The general time window within which the energy optimization goal shall be achieved [h]. Defaults to 24 hours.",
            "examples": [24],
        },
    )

    interval: Optional[int] = Field(
        default=3600,
        ge=15 * 60,
        le=60 * 60,
        json_schema_extra={
            "description": "The optimization interval [sec].",
            "examples": [60 * 60, 15 * 60],
        },
    )

    algorithm: Optional[str] = Field(
        default="GENETIC",
        json_schema_extra={"description": "The optimization algorithm.", "examples": ["GENETIC"]},
    )

    genetic: Optional[GeneticCommonSettings] = Field(
        default=None,
        json_schema_extra={
            "description": "Genetic optimization algorithm configuration.",
            "examples": [{"individuals": 400, "seed": None, "penalties": {"ev_soc_miss": 10}}],
        },
    )

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def keys(self) -> list[str]:
        """The keys of the solution."""
        try:
            ems_eos = get_ems()
        except:
            # ems might not be initialized
            return []

        key_list = []
        optimization_solution = ems_eos.optimization_solution()
        if optimization_solution:
            # Prepare mapping
            df = optimization_solution.solution.to_dataframe()
            key_list = df.columns.tolist()
        return sorted(set(key_list))

    # Validators
    @model_validator(mode="after")
    def _enforce_algorithm_configuration(self) -> "OptimizationCommonSettings":
        """Ensure algorithm default configuration is set."""
        if self.algorithm is not None:
            if self.algorithm.lower() == "genetic" and self.genetic is None:
                self.genetic = GeneticCommonSettings()
        return self


class OptimizationSolution(PydanticBaseModel):
    """General Optimization Solution."""

    id: str = Field(
        ..., json_schema_extra={"description": "Unique ID for the optimization solution."}
    )

    generated_at: DateTime = Field(
        ..., json_schema_extra={"description": "Timestamp when the solution was generated."}
    )

    comment: Optional[str] = Field(
        default=None,
        json_schema_extra={"description": "Optional comment or annotation for the solution."},
    )

    valid_from: Optional[DateTime] = Field(
        default=None, json_schema_extra={"description": "Start time of the optimization solution."}
    )

    valid_until: Optional[DateTime] = Field(
        default=None, json_schema_extra={"description": "End time of the optimization solution."}
    )

    total_losses_energy_wh: float = Field(
        json_schema_extra={"description": "The total losses in watt-hours over the entire period."}
    )

    total_revenues_amt: float = Field(
        json_schema_extra={"description": "The total revenues [money amount]."}
    )

    total_costs_amt: float = Field(
        json_schema_extra={"description": "The total costs [money amount]."}
    )

    fitness_score: set[float] = Field(
        json_schema_extra={"description": "The fitness score as a set of fitness values."}
    )

    prediction: PydanticDateTimeDataFrame = Field(
        json_schema_extra={
            "description": (
                "Datetime data frame with time series prediction data per optimization interval:"
                "- pv_energy_wh: PV energy prediction (positive) in wh"
                "- elec_price_amt_kwh: Electricity price prediction in money per kwh"
                "- feed_in_tariff_amt_kwh: Feed in tariff prediction in money per kwh"
                "- weather_temp_air_celcius: Temperature in °C"
                "- loadforecast_energy_wh: Load mean energy prediction in wh"
                "- loadakkudoktor_std_energy_wh: Load energy standard deviation prediction in wh"
                "- loadakkudoktor_mean_energy_wh: Load mean energy prediction in wh"
            )
        }
    )

    solution: PydanticDateTimeDataFrame = Field(
        json_schema_extra={
            "description": (
                "Datetime data frame with time series solution data per optimization interval:"
                "- load_energy_wh: Load of all energy consumers in wh"
                "- grid_energy_wh: Grid energy feed in (negative) or consumption (positive) in wh"
                "- costs_amt: Costs in money amount"
                "- revenue_amt: Revenue in money amount"
                "- losses_energy_wh: Energy losses in wh"
                "- <device-id>_operation_mode_id: Operation mode id of the device."
                "- <device-id>_operation_mode_factor: Operation mode factor of the device."
                "- <device-id>_soc_factor: State of charge of a battery/ electric vehicle device as factor of total capacity."
                "- <device-id>_energy_wh: Energy consumption (positive) of a device in wh."
            )
        }
    )
