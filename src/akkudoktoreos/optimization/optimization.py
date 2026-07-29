from typing import Optional

from pydantic import Field, computed_field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.core.pydantic import (
    PydanticBaseModel,
    PydanticDateTimeDataFrame,
)
from akkudoktoreos.optimization.genetic0.genetic0settings import Genetic0CommonSettings
from akkudoktoreos.optimization.genetic.geneticsettings import GeneticCommonSettings
from akkudoktoreos.utils.datetimeutil import DateTime


def optimization_algorithms() -> list[str]:
    """Valid optimization algorithms."""
    # Return static built-in optimization algorithms.
    return [
        "GENETIC",
        "GENETIC0",
    ]


class OptimizationCommonSettings(SettingsBaseModel):
    """General Optimization Configuration."""

    algorithm: str = Field(
        default="GENETIC",
        json_schema_extra={
            "description": "The optimization algorithm. Defaults to GENETIC",
            "examples": ["GENETIC", "GENETIC0"],
        },
    )

    genetic: GeneticCommonSettings = Field(
        default_factory=GeneticCommonSettings,
        json_schema_extra={
            "description": "GENETIC optimization algorithm configuration.",
            "examples": [{"individuals": 400, "seed": None, "penalties": {"ev_soc_miss": 10}}],
        },
    )

    genetic0: Genetic0CommonSettings = Field(
        default_factory=Genetic0CommonSettings,
        json_schema_extra={
            "description": "GENETIC0 optimization algorithm configuration.",
            "examples": [{"individuals": 400, "seed": None, "penalties": {"ev_soc_miss": 10}}],
        },
    )

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def algorithms(self) -> list[str]:
        """Available optimization algorithms."""
        return optimization_algorithms()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def keys(self) -> list[str]:
        """The keys of the solution."""
        try:
            ems_eos = get_ems()
        except Exception:
            # ems might not be initialized
            return []

        key_list = []
        optimization_solution = ems_eos.optimization_solution()
        if optimization_solution:
            # Prepare mapping
            df = optimization_solution.solution.to_dataframe()
            key_list = df.columns.tolist()
        return sorted(set(key_list))


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
