from enum import StrEnum
from typing import Any, Literal, Optional, Union

from pydantic import Field, computed_field, model_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import documentation_mode, get_ems
from akkudoktoreos.core.pydantic import (
    PydanticBaseModel,
    PydanticDateTimeDataFrame,
)
from akkudoktoreos.optimization.searchsettings import SearchCommonSettings
from akkudoktoreos.utils.datetimeutil import DateTime
from akkudoktoreos.utils.gridinterpolator import GridInterpolatorBackend


class TerminalValueMode(StrEnum):
    """How the energy left in the battery at the end of the horizon is valued.

    Modes
    -----
    - AUTO:
        Solve the deterministic forecast tail and apply a conservative
        continuation proxy at its end. Tail values may decrease with SOC when
        empty capacity is valuable. With a zero tail, use the proxy directly.

    - FIXED:
        Credit every stored kWh with the configured
        ``terminal_value_euro_per_kwh`` (or ``preis_euro_pro_wh_akku`` of the
        request). The historical behaviour; a value of 0 makes the optimizer
        empty the battery towards the end of the horizon.
    """

    AUTO = "AUTO"
    FIXED = "FIXED"


def normalize_optimization_settings(value: Any) -> Any:
    """Move active legacy planning settings to their canonical top-level keys."""
    if not isinstance(value, dict):
        return value
    data = dict(value)
    algorithm = data.pop("algorithm", None)
    genetic = data.pop("genetic", None) or {}
    genetic0 = data.pop("genetic0", None) or {}
    classic = algorithm == "GENETIC0" or (
        algorithm is None and not genetic and (genetic0 or "hours" in data or "penalty" in data)
    )
    legacy = genetic0 if classic else genetic
    for key in (
        "interval_sec",
        "horizon_hours",
        "tail_horizon_hours",
        "measurement_max_age_seconds",
        "terminal_value_mode",
        "terminal_value_euro_per_kwh",
        "terminal_value_window_hours",
        "penalties",
    ):
        if key in legacy:
            data.setdefault(key, legacy[key])
    if "interval" in data:
        data.setdefault("interval_sec", data.pop("interval"))
    if "hours" in data:
        data.setdefault("horizon_hours", data.pop("hours"))
    if "penalty" in data:
        data.setdefault("penalties", {"ev_soc_miss": float(data.pop("penalty"))})
    if classic:
        data.setdefault("interval_sec", 3600)
        data.setdefault("tail_horizon_hours", 0)
        data.setdefault("terminal_value_mode", "FIXED")
    return data


class OptimizationCommonSettings(SettingsBaseModel):
    """Settings of the device-by-device optimizer."""

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_settings(cls, value: Any) -> Any:
        """Load persisted configurations from previous releases."""
        return normalize_optimization_settings(value)

    interval_sec: Literal[900, 3600] = Field(
        default=3600,
        json_schema_extra={
            "description": "The optimization interval [sec]. Defaults to 3600 seconds (1 hour)",
            "examples": [60 * 60, 15 * 60],
        },
    )

    horizon_hours: int = Field(
        default=24,
        ge=0,
        json_schema_extra={
            "description": "The general time window within which the energy optimization goal shall be achieved [h]. Defaults to 24 hours.",
            "examples": [24],
        },
    )

    measurement_max_age_seconds: int = Field(
        default=300,
        gt=0,
        description=(
            "Maximum age of SoC measurements used for configuration-based optimization [s]. "
            "Without an explicit SoC or usable measurement, the initial SoC defaults to 0%."
        ),
    )

    tail_horizon_hours: int = Field(
        default=48,
        ge=0,
        json_schema_extra={
            "description": "Forecast lookahead after the control horizon [h]. No tail commands are issued. Set 0 to disable."
        },
    )

    terminal_value_mode: TerminalValueMode = Field(
        default=TerminalValueMode.AUTO,
        json_schema_extra={
            "description": (
                "How to value the energy left in the battery at the end of the "
                "control horizon. AUTO solves the forecast tail with an AUTO "
                "continuation proxy at its end (or only the proxy if tail is zero); FIXED "
                "uses 'terminal_value_euro_per_kwh'. Defaults to AUTO."
            ),
            "examples": ["AUTO", "FIXED"],
        },
    )

    terminal_value_euro_per_kwh: float = Field(
        default=0.0,
        json_schema_extra={
            "description": (
                "Value assigned to usable battery energy remaining at the end of the "
                "optimization horizon [EUR/kWh]. This terminal value is independent "
                "of the battery LCOS. Only used with terminal_value_mode = FIXED. "
                "Defaults to 0 EUR/kWh."
            ),
            "examples": [0.0, 0.20],
        },
    )

    terminal_value_window_hours: int = Field(
        default=24,
        ge=1,
        json_schema_extra={
            "description": (
                "Length of the trailing window at the effective tail end the AUTO continuation "
                "curve is derived from [h]. One day covers a full load and PV "
                "cycle. Defaults to 24 hours."
            ),
            "examples": [24],
        },
    )

    # --- Penalties (existing) -------------------------------------------------

    penalties: dict[str, Union[float, int, str]] = Field(
        default_factory=lambda: dict[str, float | int | str](
            ev_soc_miss=10,
            ac_charge_break_even=1.0,
            battery_full_charge_miss=10,
        ),
        json_schema_extra={
            "description": "Penalty parameters used in fitness evaluation.",
            "examples": [{"ev_soc_miss": 10}],
        },
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def horizon(self) -> int:
        """Number of optimization steps."""
        if self.interval_sec is None or self.interval_sec == 0 or self.horizon_hours is None:
            return 0
        num_steps = int(float(self.horizon_hours * 3600) / self.interval_sec)
        return num_steps

    search: SearchCommonSettings = Field(
        default_factory=SearchCommonSettings,
        json_schema_extra={
            "description": "OHMRAKEL42 optimizer configuration.",
            "examples": [{"time_budget_seconds": 30.0, "effort": "NORMAL"}],
        },
    )

    self_consumption_interpolator: GridInterpolatorBackend = Field(
        default=GridInterpolatorBackend.SCIPY,
        json_schema_extra={
            "description": (
                "How the inverter's self-consumption probability table is evaluated "
                "[scipy | numpy]. 'scipy' (default) uses scipy.interpolate. 'numpy' gives "
                "the same values without importing SciPy, which saves about 35 MB RSS "
                "after the first optimization run (useful on devices with little RAM); "
                "a run takes about 2.5 % longer."
            ),
            "examples": ["scipy", "numpy"],
        },
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def keys(self) -> list[str]:
        """The keys of the solution."""
        if documentation_mode():
            # Documentation shows no runtime state, e.g. of a previous run.
            return []
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
