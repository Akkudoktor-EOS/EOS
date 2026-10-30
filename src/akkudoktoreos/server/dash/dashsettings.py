"""Settings for EOSdash.

Kept in an extra module to avoid cyclic dependencies on package import.
"""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from akkudoktoreos.config.configabc import SettingsBaseModel

# Settings key prefix of an EMS plan command (operation mode), e.g. "mode_SELF_CONSUMPTION"
EOSDASH_MODE_KEY_PREFIX = "mode_"
EOSDASH_ALIAS_MAX_LEN = 32
EOSDASH_LINE_WIDTH_MAX = 5

HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")]
Alias = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=EOSDASH_ALIAS_MAX_LEN)
]
LineWidth = Annotated[int, Field(ge=1, le=EOSDASH_LINE_WIDTH_MAX)]


class EOSdashPlanSettings(SettingsBaseModel):
    """EOSdash plan page settings.

    Keys are the columns of the optimization solution chart (e.g. ``battery1_soc_factor``)
    or EMS plan commands (operation modes) with the prefix ``mode_``
    (e.g. ``mode_SELF_CONSUMPTION``). A command applies to the plan timeline and to the
    operation mode factor columns of all devices. Only choices of the user are stored,
    everything else uses the defaults of EOSdash.
    """

    visible: dict[str, bool] = Field(
        default_factory=dict,
        json_schema_extra={
            "description": "Visibility of solution chart columns and commands.",
            "examples": [{"grid_feedin_energy_wh": False}],
        },
    )
    alias: dict[str, Alias] = Field(
        default_factory=dict,
        json_schema_extra={
            "description": "Display names of solution chart columns and commands.",
            "examples": [{"mode_SELF_CONSUMPTION": "Self consumption"}],
        },
    )
    color: dict[str, HexColor] = Field(
        default_factory=dict,
        json_schema_extra={
            "description": "Colors of solution chart columns and commands (#RRGGBB).",
            "examples": [{"battery1_soc_factor": "#DAA520"}],
        },
    )
    style: dict[str, Literal["line", "bar"]] = Field(
        default_factory=dict,
        json_schema_extra={
            "description": "Chart type of solution chart columns and commands.",
            "examples": [{"pvforecast_ac_energy_wh": "bar"}],
        },
    )
    width: dict[str, LineWidth] = Field(
        default_factory=dict,
        json_schema_extra={
            "description": "Line width (1..5) of solution chart columns and commands.",
            "examples": [{"loadforecast_energy_wh": 3}],
        },
    )
    line_dash: dict[str, Literal["solid", "dashed", "dotted"]] = Field(
        default_factory=dict,
        json_schema_extra={
            "description": "Line dash of solution chart columns and commands.",
            "examples": [{"elec_price_amt_kwh": "dotted"}],
        },
    )
    device_alias: dict[str, Alias] = Field(
        default_factory=dict,
        json_schema_extra={
            "description": "Display names of devices (device id -> name) on the plan page.",
            "examples": [{"battery1": "House battery"}],
        },
    )
    plan_table_open: bool = Field(
        default=False,
        json_schema_extra={
            "description": "Energy management plan table open on page load. Defaults to False."
        },
    )
    energy_kwh: bool = Field(
        default=True,
        json_schema_extra={
            "description": "Energy in kWh instead of Wh in the solution chart. Defaults to True."
        },
    )
    factor_percent: bool = Field(
        default=True,
        json_schema_extra={
            "description": "Factors in % instead of 0.0..1.0 in the solution chart. Defaults to True."
        },
    )
    price_minor_unit: bool = Field(
        default=True,
        json_schema_extra={
            "description": (
                "Electricity price in the minor unit of the currency per kWh (e.g. ct/kWh for "
                "EUR, Rp./kWh for CHF) instead of currency/kWh in the solution chart. Has no "
                "effect for currencies without a minor unit. Defaults to True."
            )
        },
    )


class EOSdashCommonSettings(SettingsBaseModel):
    """EOS-Dash Configuration."""

    plan: EOSdashPlanSettings = Field(
        default_factory=EOSdashPlanSettings,
        json_schema_extra={"description": "Plan page settings."},
    )
