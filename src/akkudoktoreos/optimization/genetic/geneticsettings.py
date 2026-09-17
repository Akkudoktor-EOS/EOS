"""Settings for the GENETIC optimization algorithm.

Kept in an extra module to avoid cyclic dependencies on package import.
"""

from enum import StrEnum
from typing import Any, Literal, Optional, Union

from pydantic import Field, computed_field

from akkudoktoreos.config.configabc import SettingsBaseModel


def normalize_genetic_settings(value: Any) -> Any:
    """Preserve old feature settings in the current algorithm-specific namespace.

    Explicit nested values win. The classic flat configuration without GENETIC
    markers remains available to the existing GENETIC0 migration.
    """
    if not isinstance(value, dict):
        return value
    feature_keys = (
        "tail_horizon_hours",
        "terminal_value_mode",
        "terminal_value_euro_per_kwh",
        "terminal_value_window_hours",
        "measurement_max_age_seconds",
    )
    is_genetic = value.get("algorithm") == "GENETIC" or any(key in value for key in feature_keys)
    if not is_genetic or not any(
        key in value for key in (*feature_keys, "interval", "horizon_hours")
    ):
        return value
    result = dict(value)
    nested = dict(result.get("genetic") or {})
    for key in (*feature_keys, "interval", "horizon_hours"):
        if key in result:
            nested.setdefault("interval_sec" if key == "interval" else key, result.pop(key))
    result["genetic"] = nested
    return result


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


class GeneticCommonSettings(SettingsBaseModel):
    """GENETIC Optimization Algorithm Configuration."""

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

    individuals: Optional[int] = Field(
        default=300,
        ge=10,
        json_schema_extra={
            "description": "Number of individuals (solutions) in the population [>= 10]. Defaults to 300.",
            "examples": [300],
        },
    )

    generations: Optional[int] = Field(
        default=400,
        ge=10,
        json_schema_extra={
            "description": "Number of generations to evolve [>= 10]. Defaults to 400.",
            "examples": [400],
        },
    )

    seed: Optional[int] = Field(
        default=None,
        ge=0,
        json_schema_extra={
            "description": "Random seed for reproducibility. None = random.",
            "examples": [None, 42],
        },
    )

    measurement_max_age_seconds: int = Field(
        default=300,
        gt=0,
        description="Maximum age of SoC measurements for configuration-based optimization [s].",
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
