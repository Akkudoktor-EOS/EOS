import html
import json
import re
from typing import Any, Optional, Union

import pandas as pd
import requests
from babel.core import get_global
from babel.numbers import get_currency_precision, get_territory_currencies
from bokeh.core.properties import value as bokeh_value
from bokeh.models import (
    ColumnDataSource,
    CustomJS,
    FactorRange,
    HoverTool,
    LinearAxis,
    Range1d,
)
from bokeh.models.renderers import GlyphRenderer
from fasthtml.common import Button as FhButton
from fasthtml.common import Form, Input, Script, Style
from loguru import logger
from monsterui.franken import (
    Card,
    CheckboxX,
    Details,
    Div,
    DivLAligned,
    FormLabel,
    Grid,
    P,
    Summary,
    UkIcon,
)

import akkudoktoreos.server.dash.eosstatus as eosstatus
from akkudoktoreos.config.config import SettingsEOS
from akkudoktoreos.core.emplan import (
    DDBCInstruction,
    EnergyManagementInstruction,
    EnergyManagementPlan,
    FRBCInstruction,
    OMBCInstruction,
)
from akkudoktoreos.optimization.optimization import OptimizationSolution
from akkudoktoreos.server.dash.bokeh import (
    Bokeh,
    bokey_apply_theme_to_plot,
    create_figure,
)
from akkudoktoreos.server.dash.components import ConfigSection, Error
from akkudoktoreos.server.dash.context import request_url_for
from akkudoktoreos.server.dash.dashsettings import (
    EOSDASH_ALIAS_MAX_LEN,
    EOSDASH_LINE_WIDTH_MAX,
    EOSDASH_MODE_KEY_PREFIX,
    EOSdashPlanSettings,
)
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime

# bar width for 1 hour bars (time given in millseconds)
BAR_WIDTH_1HOUR = 1000 * 60 * 60


# Tailwind compatible color palette
color_palette = {
    "red-500": "#EF4444",  # red-500
    "orange-500": "#F97316",  # orange-500
    "amber-500": "#F59E0B",  # amber-500
    "yellow-500": "#EAB308",  # yellow-500
    "lime-500": "#84CC16",  # lime-500
    "green-500": "#22C55E",  # green-500
    "emerald-500": "#10B981",  # emerald-500
    "teal-500": "#14B8A6",  # teal-500
    "cyan-500": "#06B6D4",  # cyan-500
    "sky-500": "#0EA5E9",  # sky-500
    "blue-500": "#3B82F6",  # blue-500
    "indigo-500": "#6366F1",  # indigo-500
    "violet-500": "#8B5CF6",  # violet-500
    "purple-500": "#A855F7",  # purple-500
    "pink-500": "#EC4899",  # pink-500
    "rose-500": "#F43F5E",  # rose-500
}
# Color names
colors = list(color_palette.keys())

# Colums that are exclude from the the solution card display
# They are currently not used or are covered by others
solution_excludes = [
    "date_time",
    "_op_mode",
    "_fault_",
    "_outage_supply_",
    "_reserve_backup_",
    "_ramp_rate_control_",
    "_frequency_regulation_",
]

# Solution columns displayed by default
solution_visible_default: dict[str, bool] = {
    "pvforecast_ac_energy_wh": True,
    "elec_price_amt_kwh": True,
    "loadforecast_energy_wh": True,
    "grid_consumption_energy_wh": True,
    "grid_feedin_energy_wh": True,
}
# Solution columns with these suffixes are displayed by default (e.g. battery1_soc_factor)
solution_visible_default_suffixes = ("_soc_factor",)
# Solution columns displayed as bar chart by default
solution_style_default: dict[str, str] = {
    "pvforecast_ac_energy_wh": "bar",
}
# Solution columns plotted below the zero axis (energy flowing out of the house)
solution_negative_columns = ("grid_feedin_energy_wh",)
# Line width of the solution chart lines
SOLUTION_LINE_WIDTH = 2
SOLUTION_LINE_WIDTHS = tuple(range(1, EOSDASH_LINE_WIDTH_MAX + 1))
# Line dash of the solution chart lines: by default predictions are dotted,
# SoC factors dashed and everything else solid, except for these columns
SOLUTION_LINE_DASHES = ("solid", "dashed", "dotted")
solution_dash_default: dict[str, str] = {
    "loadforecast_energy_wh": "solid",
}
solution_color: dict[str, str] = {}

# Default colors of solution columns, following the openWB colors in Home Assistant
solution_color_default: dict[str, str] = {
    # Prediction
    "elec_price_amt_kwh": "#C084FC",  # light purple
    "feed_in_tariff_amt_kwh": "#B2DF8A",  # light green (feed-in)
    "loadforecast_energy_wh": "#7F7F7F",  # grey (house)
    "pvforecast_ac_energy_wh": "#33A02C",  # green (PV)
    # Solution
    "costs_amt": "#FF4D4D",  # light red
    "revenue_amt": "#10B981",  # emerald
    "grid_consumption_energy_wh": "#B22222",  # red (grid)
    "grid_feedin_energy_wh": "#B2DF8A",  # light green (feed-in)
    "load_energy_wh": "#7F7F7F",  # grey (house)
    "losses_energy_wh": "#6366F1",  # indigo
    # Instruction
    "genetic_ac_charge_factor": "#B22222",  # red (grid)
    "genetic_battery_grid_export_allowed_factor": "#84CC16",  # lime
    "genetic_dc_charge_factor": "#33A02C",  # green (PV)
    "genetic_discharge_allowed_factor": "#F5D061",  # light gold (storage)
    "genetic_ev_charge_factor": "#0EA5E9",  # sky
}
# Default colors of device columns, by device type
solution_color_battery_soc = "#DAA520"  # gold (storage)
solution_color_ev_soc = "#1F78B4"  # blue (charging)
solution_color_appliance_energy = "#FFFFFF"  # white

# Colors of the plan timeline, by operation mode
# The first four follow the EMS plan chart of the Home Assistant integration
plan_mode_color: dict[str, str] = {
    "SELF_CONSUMPTION": "#3A9A3A",  # green
    "IDLE": "#7A7A7A",  # grey
    "RUN": "#6F8EE0",  # blue
    "OFF": "#A12525",  # red
    "NON_EXPORT": "#D4A017",  # amber
    "FORCED_CHARGE": "#14B8A6",  # teal
    "FORCED_DISCHARGE": "#8B5CF6",  # violet
    "PEAK_SHAVING": "#F97316",  # orange
    "GRID_SUPPORT_EXPORT": "#EC4899",  # pink
    "GRID_SUPPORT_IMPORT": "#F43F5E",  # rose
    "FREQUENCY_REGULATION": "#0EA5E9",  # sky
    "RAMP_RATE_CONTROL": "#06B6D4",  # cyan
    "RESERVE_BACKUP": "#84CC16",  # lime
    "OUTAGE_SUPPLY": "#EAB308",  # yellow
    "FORCED_RUN": "#3B5BDB",  # dark blue
    "LIMIT_POWER": "#9AA5CE",  # light blue grey
    "DEFER": "#B08D57",  # brown
    "PAUSE": "#B08D57",  # brown
    "RESUME": "#6F8EE0",  # blue
    "FAULT": "#E11D48",  # crimson
}
plan_mode_color_default = "#A1A1AA"  # zinc-400

# White is not visible on the light theme, it is displayed dark grey there
LIGHT_THEME_WHITE_REPLACEMENT = "#4B5563"  # gray-600


# Charts use the system font like the rest of the page. Bokeh's default "helvetica"
# falls back to Arial on most systems, which is hard to read in small sizes.
CHART_FONT = 'system-ui, "Segoe UI", Roboto, sans-serif'
CHART_FONT_SIZE = "12px"
# Font of the HTML tooltips (inside a double quoted style attribute)
CHART_TOOLTIP_STYLE = f"font-family: {CHART_FONT.replace(chr(34), chr(39))}; font-size: 12px;"


def apply_chart_font(plot: Any) -> None:
    """Use the chart font for the title, the axes and the legend of a plot."""
    plot.title.text_font = CHART_FONT
    for axis in plot.xaxis + plot.yaxis:
        axis.axis_label_text_font = CHART_FONT
        axis.axis_label_text_font_size = CHART_FONT_SIZE
        axis.axis_label_text_font_style = "normal"
        axis.major_label_text_font = CHART_FONT
        axis.major_label_text_font_size = CHART_FONT_SIZE
    for legend in plot.legend:
        legend.label_text_font = CHART_FONT
        legend.label_text_font_size = CHART_FONT_SIZE


def theme_color(color: str, dark: bool) -> str:
    """Color as displayed on the current theme."""
    if not dark and color.upper() == "#FFFFFF":
        return LIGHT_THEME_WHITE_REPLACEMENT
    return color


# Display names of the devices chosen by the user (device_id -> alias), used on the
# whole plan page instead of the device ids
DEVICE_ALIAS_MAX_LEN = EOSDASH_ALIAS_MAX_LEN

# Bokeh renders tooltips in a shadow DOM that the page theme does not reach. On the
# dark theme this hover callback adds a dark style sheet to the tooltips of the page.
DARK_TOOLTIP_JS = """
const now = Date.now();
if (window.eosdashDarkTooltip && now - window.eosdashDarkTooltip < 100) return;
window.eosdashDarkTooltip = now;
const css = ":host { --tooltip-color: #262626; --tooltip-text: #e5e5e5; "
    + "--tooltip-border: #555555; --tooltip-arrow-color: #555555; }";
function walk(root) {
    root.querySelectorAll("*").forEach((e) => {
        if (!e.shadowRoot) return;
        if (e.classList.contains("bk-Tooltip") && !e.shadowRoot.eosdashDark) {
            const sheet = new CSSStyleSheet();
            sheet.replaceSync(css);
            e.shadowRoot.adoptedStyleSheets = [...e.shadowRoot.adoptedStyleSheets, sheet];
            e.shadowRoot.eosdashDark = true;
        }
        walk(e.shadowRoot);
    });
}
walk(document);
"""


def tooltip_callback(dark: bool) -> Optional[CustomJS]:
    """Hover callback that styles the tooltips for the current theme."""
    return CustomJS(code=DARK_TOOLTIP_JS) if dark else None


def tooltip_text(text: str) -> str:
    """Text for a Bokeh HTML tooltip template, without template fields."""
    return html.escape(text).replace("@", "&#64;").replace("$", "&#36;")


# Visibility, aliases, colors and styles ("line" or "bar") of the solution columns chosen
# by the user are stored in the EOS configuration (eosdash/plan). The EMS plan commands
# (operation modes) are stored under mode keys ("mode_SELF_CONSUMPTION"): they apply to
# the plan timeline and to the operation mode factor columns of all devices in the
# solution chart.
MODE_KEY_PREFIX = EOSDASH_MODE_KEY_PREFIX

# Collapsible elements of the plan page (marked with data-keep-open); their open state
# is sent with every action so they stay open when the page is rendered again.
plan_open_sections: set[str] = set()

# EMS plan table with the instruction cards, collapsed by default. The user can choose
# to have it open by default (option in the gear menu of the table). The units of the
# solution chart are options in the gear menu of the chart.
PLAN_TABLE_ID = "ems-plan-table"
PLAN_OPTIONS = (
    "plan_table_open",
    # Units of the solution chart (gear menu of the chart)
    "energy_kwh",
    "factor_percent",
    "price_minor_unit",
)
# The gear menu of the plan table is only shown while the table is open
PLAN_TABLE_STYLE = f"#{PLAN_TABLE_ID}:not([open]) + .plan-table-options {{ display: none; }}"

# Close open popover menus (details marked with data-popover) on a click outside of them.
# Registered once per browser page, the plan page content is rendered again and again.
PLAN_POPOVER_SCRIPT = """
if (!window.eosdashPopoverClose) {
    window.eosdashPopoverClose = true;
    document.addEventListener("click", (evt) => {
        document.querySelectorAll("details[data-popover][open]").forEach((menu) => {
            if (!menu.contains(evt.target)) menu.open = false;
        });
    });
}
"""


# Charts of the plan page with a common time axis (Bokeh figure name)
TIME_AXIS_FIGURE_NAME = "eosdash-time-axis"

# Align the time axes of the plan page charts: the frames (plot areas) of all figures
# named TIME_AXIS_FIGURE_NAME get the same left and right edge on the page, by
# enlarging the smaller borders. Elements marked with data-time-axis (the KPIs) get
# margins so they span the same edges, the legends below the charts get columns over
# the full width of the time axis. Runs after the charts are rendered and on resize.
# Zoom and pan of the solution chart are applied to the other charts.
PLAN_ALIGN_SCRIPT = (
    """
if (!window.eosdashAlign) {
    const figures = () => {
        const views = [];
        if (window.Bokeh && Bokeh.index) {
            for (const view of Bokeh.index) {
                if (view.model && view.model.name === "%s" && view.frame && view.el.isConnected) {
                    views.push(view);
                }
            }
        }
        return views;
    };
    window.eosdashAlign = (pass = 0) => {
        const views = figures();
        if (!views.length) return;
        const edges = views.map((view) => {
            const box = view.el.getBoundingClientRect();
            return {
                width: box.width,
                left: box.left + view.frame.bbox.left,
                right: box.left + view.frame.bbox.right,
            };
        });
        const left = Math.max(...edges.map((e) => e.left));
        const right = Math.min(...edges.map((e) => e.right));
        let changed = false;
        views.forEach((view, i) => {
            const grow_left = left - edges[i].left;
            const grow_right = edges[i].right - right;
            if (grow_left > 0.5) {
                view.model.min_border_left = Math.round(view.frame.bbox.left + grow_left);
                changed = true;
            }
            if (grow_right > 0.5) {
                const border =
                    view.model.min_border_right ?? edges[i].width - view.frame.bbox.right;
                view.model.min_border_right = Math.round(border + grow_right);
                changed = true;
            }
        });
        // Check again after Bokeh has done the new layout
        if (changed && pass < 4) {
            setTimeout(() => window.eosdashAlign(pass + 1), 150);
            return;
        }
        for (const view of views) {
            const legend = view.model.below.find((model) => model.type === "Legend");
            if (!legend || !legend.items.length) continue;
            const items = legend.items.length;
            const ncols = legend.ncols === "auto" ? items : Math.min(legend.ncols, items);
            const width = view.frame.bbox.width - 2 * legend.margin - 2 * legend.padding;
            const column = (width - (ncols - 1) * legend.spacing) / ncols;
            const label_width = Math.floor(column - legend.glyph_width - legend.label_standoff);
            if (label_width > 0 && legend.label_width !== label_width) {
                legend.label_width = label_width;
            }
        }
        document.querySelectorAll("[data-time-axis]").forEach((el) => {
            const parent = el.parentElement.getBoundingClientRect();
            const style = getComputedStyle(el.parentElement);
            const inner_left = parent.left + parseFloat(style.paddingLeft);
            const inner_right = parent.right - parseFloat(style.paddingRight);
            el.style.marginLeft = Math.max(0, Math.round(left - inner_left)) + "px";
            el.style.marginRight = Math.max(0, Math.round(inner_right - right)) + "px";
        });
    };
    window.eosdashSyncRange = (range) => {
        for (const view of figures()) {
            if (view.model.x_range !== range) {
                view.model.x_range.start = range.start;
                view.model.x_range.end = range.end;
            }
        }
    };
    // Wait until the charts are rendered
    window.eosdashAlignSchedule = () => {
        let tries = 0;
        const timer = setInterval(() => {
            tries += 1;
            const views = figures();
            const rendered = views.every((view) => view.frame.bbox.width > 0);
            // Both charts, or only one if the other one is missing (e.g. no plan)
            if (rendered && (views.length >= 2 || (views.length && tries > 12))) {
                clearInterval(timer);
                window.eosdashAlign();
            } else if (tries > 40) {
                clearInterval(timer);
            }
        }, 250);
    };
    // Elements that become visible (totals view) are aligned too
    document.addEventListener("toggle", () => window.eosdashAlign(), true);
    let resize_timer = null;
    window.addEventListener("resize", () => {
        clearTimeout(resize_timer);
        resize_timer = setTimeout(() => window.eosdashAlign(), 250);
    });
}
window.eosdashAlignSchedule();
"""
    % TIME_AXIS_FIGURE_NAME
)


def plan_hx_vals(**vals: str) -> str:
    """Htmx values of a plan page control, including dark mode and open sections."""
    js_vals = "".join(f"{json.dumps(k)}: {json.dumps(v)}, " for k, v in vals.items())
    return (
        "js:{ "
        + js_vals
        + '"dark": window.matchMedia("(prefers-color-scheme: dark)").matches, '
        + '"open": Array.from(document.querySelectorAll("details[data-keep-open][open]"))'
        + '.map((e) => e.id).join(",") }'
    )


def plan_settings() -> EOSdashPlanSettings:
    """Plan page settings from the EOS configuration, defaults if there are none."""
    config = eosstatus.eos_config
    if config is None or config.eosdash is None:
        return EOSdashPlanSettings()
    return config.eosdash.plan


def plan_settings_put(server: str, key: str, value: Any) -> None:
    """Store a plan page setting in the EOS configuration (eosdash/plan/<key>).

    The configuration returned by EOS replaces the one of EOSdash, so the setting
    applies at once.
    """
    try:
        result = requests.put(f"{server}/v1/config/eosdash/plan/{key}", json=value, timeout=10)
        result.raise_for_status()
        eosstatus.eos_config = SettingsEOS(**result.json())
    except Exception as e:
        logger.warning(f"Can not store plan setting {key}: {e}")


def solution_default_color(col: str, config: Optional[SettingsEOS]) -> Optional[str]:
    """Default color of a solution column, None if there is no specific default.

    Device columns get their color by device type.
    """
    if col in solution_color_default:
        return solution_color_default[col]
    devices = config.devices if config else None
    if devices:
        for device_configs, suffix, color in (
            (devices.batteries, "_soc_factor", solution_color_battery_soc),
            (devices.electric_vehicles, "_soc_factor", solution_color_ev_soc),
            (devices.home_appliances, "_energy_wh", solution_color_appliance_energy),
        ):
            for device in (device_configs or {}).values():
                if col == f"{device.device_id}{suffix}":
                    return color
    return None


def mode_key(mode: str) -> str:
    """Settings key of an EMS plan command (operation mode)."""
    return f"{MODE_KEY_PREFIX}{mode}"


def solution_column_color(col: str) -> str:
    """Color of a solution column or mode key, user choice first."""
    color = plan_settings().color.get(col)
    if color:
        return color
    if col.startswith(MODE_KEY_PREFIX):
        return plan_mode_color.get(col[len(MODE_KEY_PREFIX) :], plan_mode_color_default)
    return solution_color.get(col, plan_mode_color_default)


def solution_column_style(col: str) -> str:
    """Style of a solution column: "line" or "bar", user choice first."""
    return plan_settings().style.get(col) or solution_style_default.get(col, "line")


def solution_column_width(col: str) -> int:
    """Line width of a solution column, user choice first."""
    return int(plan_settings().width.get(col) or SOLUTION_LINE_WIDTH)


def solution_column_dash(col: str, is_prediction: bool) -> str:
    """Line dash of a solution column: "solid", "dashed" or "dotted", user choice first."""
    dash = plan_settings().line_dash.get(col) or solution_dash_default.get(col)
    if dash:
        return dash
    if col.endswith("_soc_factor"):
        return "dashed"
    if is_prediction:
        return "dotted"
    return "solid"


# Value axes of the solution chart in display order: energy is the main axis
SOLUTION_AXES = ("energy", "power", "factor", "amt_kwh", "amt")
SOLUTION_AXES_LEFT = ("energy", "power")


# Totals view of the solution chart: display order and icon by column name prefix
SOLUTION_TOTALS_ICONS = (
    ("grid_consumption", "factory"),
    ("pvforecast", "sun"),
    ("grid_feedin", "utility-pole"),
    ("load", "house"),
    ("losses", "flame"),
    ("costs", "banknote"),
    ("revenue", "coins"),
)
SOLUTION_TOTALS_ID = "solution-totals"
# Show the icon of the view that a click on the toggle opens
SOLUTION_TOTALS_STYLE = (
    f"#{SOLUTION_TOTALS_ID}:not([open]) .totals-hide, "
    f"#{SOLUTION_TOTALS_ID}[open] .totals-show {{ display: none; }}"
)


def solution_total_icon(col: str, config: Optional[SettingsEOS]) -> tuple[float, str]:
    """Display order and icon of a column in the totals view."""
    for rank, (prefix, icon) in enumerate(SOLUTION_TOTALS_ICONS):
        if col.startswith(prefix):
            return rank, icon
    devices = config.devices if config else None
    if devices:
        # Devices after the grid feed-in
        for device_configs, icon in (
            (devices.batteries, "battery-charging"),
            (devices.electric_vehicles, "car"),
            (devices.home_appliances, "washing-machine"),
        ):
            for device in (device_configs or {}).values():
                if col.startswith(f"{device.device_id}_"):
                    return 2.5, icon
    return len(SOLUTION_TOTALS_ICONS), "zap"


def config_currency(config: Optional[SettingsEOS]) -> Optional[str]:
    """ISO 4217 code of the currency at the configured location, if known.

    The configuration has no currency setting. The currency is derived from the country
    of the configured timezone (e.g. Europe/Berlin -> DE -> EUR).
    """
    timezone = config.general.timezone if config and config.general else None
    territory = get_global("zone_territories").get(timezone) if timezone else None
    currencies = get_territory_currencies(territory) if territory else []
    return currencies[0] if currencies else None


# Symbols of the minor unit of some currencies, other currencies are shown as e.g. "1/100 SEK"
CURRENCY_MINOR_UNIT_SYMBOLS = {
    "EUR": "ct",
    "CHF": "Rp.",
    "GBP": "p",
    "USD": "¢",
}


def currency_minor_unit(currency: Optional[str]) -> Optional[tuple[str, int]]:
    """Symbol and factor of the minor unit of a currency, None if it has none or is unknown.

    The number of minor unit digits comes from CLDR (e.g. EUR -> 2, JPY -> 0).
    """
    if not currency:
        return None
    digits = get_currency_precision(currency)
    if digits <= 0:
        return None
    factor = 10**digits
    return CURRENCY_MINOR_UNIT_SYMBOLS.get(currency, f"1/{factor} {currency}"), factor


def column_axis(col: str) -> Optional[str]:
    """Value axis of a solution column, None for an unknown unit."""
    if col.endswith("power_w"):
        return "power"
    if col.endswith("energy_wh"):
        return "energy"
    if col.endswith("factor") or col.endswith("mode"):
        return "factor"
    if col.endswith("amt_kwh"):
        return "amt_kwh"
    if col.endswith("amt"):
        return "amt"
    return None


def plan_option(option: str) -> bool:
    """Value of a plan page option, user choice first."""
    return bool(getattr(plan_settings(), option))


def solution_column_visible(col: str) -> bool:
    """Visibility of a solution column, user choice first."""
    visible = plan_settings().visible.get(col)
    if visible is not None:
        return bool(visible)
    return solution_visible_default.get(col, False) or col.endswith(
        solution_visible_default_suffixes
    )


def alias_clean(alias: str) -> str:
    """Alias without control characters and repeated whitespace, limited in length."""
    alias = "".join(c for c in alias if c.isprintable())
    return " ".join(alias.split())[:DEVICE_ALIAS_MAX_LEN].strip()


def device_alias(device_id: str) -> str:
    """Display name of a device: the user chosen alias or the device id."""
    return alias_clean(plan_settings().device_alias.get(device_id, "")) or device_id


def config_device_ids(config: Optional[SettingsEOS]) -> list[str]:
    """Device ids of the configuration: batteries, cars, appliances."""
    device_ids: list[str] = []
    if config and config.devices:
        for devices in (
            config.devices.batteries,
            config.devices.electric_vehicles,
            config.devices.home_appliances,
        ):
            if devices:
                device_ids.extend(device.device_id for device in devices.values())
    return device_ids


def op_factor_device_mode(col: str, device_ids: list[str]) -> Optional[tuple[str, str]]:
    """Device id and operation mode of an operation mode factor column, else None."""
    suffix = "_op_factor"
    if not col.endswith(suffix):
        return None
    # Longest id first, so "ev11" is not taken for "ev1"
    for device_id in sorted(device_ids, key=len, reverse=True):
        if col.startswith(f"{device_id}_"):
            return device_id, col[len(device_id) + 1 : -len(suffix)].upper()
    # Unknown device: longest mode first, so "forced_run" is not taken for "run"
    for mode in sorted(plan_mode_color, key=len, reverse=True):
        if col.endswith(f"_{mode.lower()}{suffix}"):
            return col[: -len(f"_{mode.lower()}{suffix}")], mode
    return None


def column_key(col: str, device_ids: list[str]) -> str:
    """Settings key of a solution column: the mode key for operation mode factors."""
    device_mode = op_factor_device_mode(col, device_ids)
    return mode_key(device_mode[1]) if device_mode else col


def plan_mode_label(mode: str) -> str:
    """Display name of an EMS plan command: the user chosen alias or the mode."""
    return alias_clean(plan_settings().alias.get(mode_key(mode), "")) or mode


def column_label(col: str, device_ids: list[str]) -> str:
    """Display name of a solution column.

    The user chosen alias first. Otherwise the device id prefix is replaced by the
    device alias, operation mode factors are named "<device> <mode>".
    """
    alias = alias_clean(plan_settings().alias.get(col, ""))
    if alias:
        return alias
    device_mode = op_factor_device_mode(col, device_ids)
    if device_mode:
        return f"{device_alias(device_mode[0])} {plan_mode_label(device_mode[1])}"
    # Longest id first, so "ev11" is not taken for "ev1"
    for device_id in sorted(device_ids, key=len, reverse=True):
        if col.startswith(f"{device_id}_"):
            alias = alias_clean(plan_settings().device_alias.get(device_id, ""))
            if alias:
                return f"{alias} {col[len(device_id) + 1 :]}"
            break
    return col


# Approximate pixel sizes to decide whether a bar label fits into its bar
PLAN_CHART_WIDTH_PX = 1000
PLAN_LABEL_CHAR_PX = 6.5

HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def validate_source(source: ColumnDataSource, x_col: str = "date_time") -> None:
    data = source.data

    # 1. Source has data at all
    if not data:
        raise ValueError("ColumnDataSource has no data.")

    # 2. x_col must be present
    if x_col not in data:
        raise ValueError(f"Missing expected x-axis column '{x_col}' in source.")

    # 3. All columns must have equal length
    lengths = {len(v) for v in data.values()}
    if len(lengths) != 1:
        raise ValueError(f"ColumnDataSource columns have mismatched lengths: {lengths}")

    # 4. Must have at least one non-x column
    y_columns = [c for c in data.keys() if c != x_col]
    if not y_columns:
        raise ValueError("No y-value columns found for plotting (only x-axis present).")

    # 5. Each y-column must have at least one valid value
    for col in y_columns:
        values = [v for v in data[col] if v is not None]
        if not values:
            raise ValueError(f"Column '{col}' contains only None/NaN or is empty.")


def solution_settings_action(server: str, data: dict) -> None:
    """Store a setting changed with the controls of a solution column or command."""
    action = data.get("action", None)
    col = data.get("renderer", None)
    if not col:
        return
    settings = plan_settings()
    if action == "visible":
        plan_settings_put(
            server, "visible", {**settings.visible, col: bool(data.get(f"{col}-visible", False))}
        )
    elif action == "color":
        color = str(data.get("color", ""))
        if HEX_COLOR_RE.match(color):
            plan_settings_put(server, "color", {**settings.color, col: color.upper()})
    elif action == "style":
        style = str(data.get("style", ""))
        if style in ("line", "bar"):
            plan_settings_put(server, "style", {**settings.style, col: style})
    elif action == "width":
        try:
            width = int(data.get("width", 0))
        except (TypeError, ValueError):
            width = 0
        if width in SOLUTION_LINE_WIDTHS:
            plan_settings_put(server, "width", {**settings.width, col: width})
    elif action == "dash":
        dash = str(data.get("dash", ""))
        if dash in SOLUTION_LINE_DASHES:
            plan_settings_put(server, "line_dash", {**settings.line_dash, col: dash})
    elif action == "alias":
        alias = alias_clean(str(data.get("alias", "")))
        aliases = {k: v for k, v in settings.alias.items() if k != col}
        if alias:
            aliases[col] = alias
        plan_settings_put(server, "alias", aliases)
    elif action == "reset":
        plan_settings_put(server, "color", {k: v for k, v in settings.color.items() if k != col})


def SolutionCard(solution: OptimizationSolution, config: SettingsEOS, data: Optional[dict]) -> Grid:
    """Creates a optimization solution card.

    Args:
        data (Optional[dict]): Incoming data containing action and category for processing.
    """
    global colors, color_palette
    dark = False
    if data and data.get("dark", None) == "true":
        dark = True

    df = solution.solution.to_dataframe()
    if df.empty or len(df.columns) <= 1:
        raise ValueError(
            f"Solution DataFrame is empty or missing plottable columns: {list(df.columns)}"
        )
    if "date_time" not in df.columns:
        raise ValueError(f"Solution DataFrame is missing column 'date_time': {list(df.columns)}")
    solution_columns = list(df.columns)
    instruction_columns = [
        instruction
        for instruction in solution_columns
        if instruction.endswith("op_mode")
        or instruction.endswith("op_factor")
        or instruction.startswith("genetic_")
    ]
    solution_columns = [x for x in solution_columns if x not in instruction_columns]

    prediction_df = solution.prediction.to_dataframe()
    if prediction_df.empty:
        raise ValueError(
            f"Prediction DataFrame is empty or missing plottable columns: {list(prediction_df.columns)}"
        )
    if "date_time" not in prediction_df.columns:
        raise ValueError(
            f"Prediction DataFrame is missing column 'date_time': {list(prediction_df.columns)}"
        )
    # Only plot if there are actual data columns beyond date_time
    prediction_columns = [c for c in prediction_df.columns if c != "date_time"]
    # No prediction data to plot — skip prediction section silently
    if prediction_columns:
        prediction_columns_to_join = prediction_df.columns.difference(df.columns)
        df = df.join(prediction_df[prediction_columns_to_join], how="inner")

    # Show energy flowing out of the house below the zero axis
    for col in solution_negative_columns:
        if col in df.columns:
            df[col] = 0.0 - df[col]  # 0.0 - x avoids -0.0 in the mouseover

    # Exclude columns that currently do not have a value
    excludes = list(solution_excludes)
    for instruction in instruction_columns:
        if instruction.endswith("op_mode") and df[instruction].eq(0).all():
            # Exclude op_mode and op_factor if all op_mode is 0
            excludes.append(instruction)
            excludes.append(f"{instruction[:-4]}factor")

    # Make bokey plot in local time at location
    # Determine desired timezone
    if config.general is None or config.general.timezone is None:
        date_time_tz = "Europe/Berlin"
    else:
        date_time_tz = config.general.timezone
    # Ensure original date_time is parsed as UTC and convert to local time
    df["date_time_local"] = (
        pd.to_datetime(df["date_time"], utc=True).dt.tz_convert(date_time_tz).dt.tz_localize(None)
    )

    # Units chosen in the gear menu of the chart
    energy_kwh = plan_option("energy_kwh")
    factor_percent = plan_option("factor_percent")
    currency = config_currency(config)
    currency_unit = currency or "Amount"
    minor_unit = currency_minor_unit(currency)
    price_minor = minor_unit if plan_option("price_minor_unit") else None
    for col in df.columns:
        if energy_kwh and col.endswith("energy_wh"):
            df[col] = df[col] / 1000.0
        elif factor_percent and col.endswith("factor"):
            df[col] = df[col] * 100.0
        elif price_minor and col.endswith("amt_kwh"):
            df[col] = df[col] * price_minor[1]
    price_unit = f"{price_minor[0] if price_minor else currency_unit}/kWh"
    axis_labels = {
        "energy": "Energy [kWh]" if energy_kwh else "Energy [Wh]",
        "power": "Power [W]",
        "factor": "Factor [%]" if factor_percent else "Factor [0.0..1.0]",
        "amt_kwh": f"Electricity Price [{price_unit}]",
        "amt": f"Amount [{currency_unit}]",
    }
    # Unit and number format in the mouseover
    axis_units = {
        "energy": ("kWh", "0.00") if energy_kwh else ("Wh", "0"),
        "power": ("W", "0"),
        "factor": ("%", "0") if factor_percent else ("", "0.00"),
        "amt_kwh": (price_unit, "0.0") if price_minor else (f"{currency or ''}/kWh", "0.00"),
        "amt": (currency or "", "0.00"),
    }

    source = ColumnDataSource(df)
    validate_source(source)

    if eosstatus.eos_health is not None:
        last_run_datetime = eosstatus.eos_health["energy-management"]["last_run_datetime"]
    else:
        last_run_datetime = "unknown"

    # Step of the solution, bars cover the interval that starts at date_time
    step = df["date_time_local"].diff().median()
    step_ms = pd.Timedelta(step).total_seconds() * 1000 if pd.notna(step) else 0.0
    if not step_ms:
        step_ms = BAR_WIDTH_1HOUR

    # Settings, color and value axis of the columns
    device_ids = config_device_ids(config)
    renderer_colors: dict[str, str] = {}
    column_settings: dict[str, tuple[str, Optional[str], bool]] = {}

    # Have an index for the colors of predictions, solutions and instructions.
    prediction_color_idx = 0
    solution_color_idx = int(len(colors) * 0.33) + 1
    instruction_color_idx = int(len(colors) * 0.66) + 1
    for col in sorted(df.columns):
        # Exclude some columns that are currently not used or are covered by others
        if any(exclude in col for exclude in excludes):
            continue
        if col not in solution_color:
            if col in prediction_columns:
                color = colors[prediction_color_idx % len(colors)]
                prediction_color_idx += 3
            elif col in solution_columns:
                color = colors[solution_color_idx % len(colors)]
                solution_color_idx += 3
            else:
                color = colors[instruction_color_idx % len(colors)]
                instruction_color_idx += 3
            # Remember the default color of this column
            solution_color[col] = solution_default_color(col, config) or color_palette[color]
        # Operation mode factors share the settings of their EMS plan command
        key = column_key(col, device_ids)
        axis = column_axis(col)
        if axis is None:
            # Skip columns with unrecognized suffix rather than raising
            logger.warning(f"Skipping column with unrecognized suffix: {col}")
        renderer_colors[col] = theme_color(solution_column_color(key), dark)
        column_settings[col] = (key, axis, axis is not None and solution_column_visible(key))

    # Only the axes of the displayed columns. The zero line is at the same height on all
    # axes, so values below zero (feed-in) read correctly on every axis.
    axis_ranges: dict[str, list[float]] = {}
    for col, (key, axis, visible) in column_settings.items():
        if visible and axis is not None:
            low, high = axis_ranges.get(axis, [0.0, 0.0])
            axis_ranges[axis] = [min(low, float(df[col].min())), max(high, float(df[col].max()))]
    if "factor" in axis_ranges:
        axis_ranges["factor"][1] = max(axis_ranges["factor"][1], 100.0 if factor_percent else 1.0)
    if not axis_ranges:
        axis_ranges["energy"] = [0.0, 1.0]
    for value_range in axis_ranges.values():
        if value_range[1] <= 0.0:
            # Only values below zero, leave some room above
            value_range[1] = -value_range[0] * 0.1 or 1.0
    below = max(-low / (high - low) for low, high in axis_ranges.values())
    for value_range in axis_ranges.values():
        low = -below / (1.0 - below) * value_range[1]
        span = value_range[1] - low
        # add 5% to min and max values for better display
        value_range[0], value_range[1] = low - 0.05 * span, value_range[1] + 0.05 * span
    shown_axes = [axis for axis in SOLUTION_AXES if axis in axis_ranges]
    main_axis = shown_axes[0]

    plot = create_figure(
        title=f"Optimization Solution - last run: {last_run_datetime}",
        name=TIME_AXIS_FIGURE_NAME,
        x_range=Range1d(
            start=df["date_time_local"].min(),
            end=df["date_time_local"].max() + pd.Timedelta(milliseconds=step_ms),
        ),
        x_axis_type="datetime",
        y_axis_label=axis_labels[main_axis],
        y_axis_location="left" if main_axis in SOLUTION_AXES_LEFT else "right",
        sizing_mode="stretch_width",
        y_range=Range1d(start=axis_ranges[main_axis][0], end=axis_ranges[main_axis][1]),
        height=400,
    )
    # Energy and power on the left, factors and amounts on the right
    range_names = {main_axis: "default"}
    plot.extra_y_ranges = {
        axis: Range1d(start=axis_ranges[axis][0], end=axis_ranges[axis][1])
        for axis in shown_axes[1:]
    }
    for axis in shown_axes[1:]:
        range_names[axis] = axis
        plot.add_layout(
            LinearAxis(y_range_name=axis, axis_label=axis_labels[axis]),
            "left" if axis in SOLUTION_AXES_LEFT else "right",
        )

    plot.toolbar.autohide = True

    # Zoom and pan also move the plan timeline
    sync_range = CustomJS(code="if (window.eosdashSyncRange) window.eosdashSyncRange(cb_obj);")
    plot.x_range.js_on_change("start", sync_range)
    plot.x_range.js_on_change("end", sync_range)

    # Bars cover the interval that starts at date_time (like step mode "after")
    source.data["date_time_bar"] = df["date_time_local"] + pd.Timedelta(milliseconds=step_ms / 2)

    # Create line renderers for each column
    renderers = {}
    for col, (key, axis, visible) in column_settings.items():
        label = column_label(col, device_ids)
        color = renderer_colors[col]
        r: Optional[GlyphRenderer[Any]]
        if visible and axis is not None:
            if solution_column_style(key) == "bar":
                r = plot.vbar(
                    x="date_time_bar",
                    top=col,
                    width=step_ms * 0.8,
                    source=source,
                    legend_label=label,
                    fill_color=color,
                    fill_alpha=0.5,
                    line_color=color,
                    y_range_name=range_names[axis],
                )
            else:
                r = plot.step(
                    x="date_time_local",
                    y=col,
                    mode="after",
                    source=source,
                    legend_label=label,
                    color=color,
                    line_dash=solution_column_dash(key, col in prediction_columns),
                    line_width=solution_column_width(key),
                    y_range_name=range_names[axis],
                )
        else:
            r = None
        renderers[col] = r

    # Legend below the plot, with the aliases and colors of the displayed columns
    if plot.legend:
        legend = plot.legend[0]
        plot.center.remove(legend)
        legend.location = "center_left"
        legend.orientation = "horizontal"
        legend.ncols = 4
        legend.glyph_height = 10
        legend.glyph_width = 20
        legend.spacing = 4
        legend.border_line_color = None
        plot.add_layout(legend, "below")

    # Mouseover shows the values of all displayed columns at the hovered time.
    # An invisible line on the zero axis does the hit testing for all of them.
    visible_columns = [col for col, r in renderers.items() if r is not None]
    if visible_columns:
        source.data["hover_y"] = [0.0] * len(df)
        hover_renderer = plot.line(
            x="date_time_local",
            y="hover_y",
            source=source,
            line_alpha=0.0,
        )

        def tooltip_value(col: str) -> str:
            unit, number_format = axis_units[str(column_settings[col][1])]
            return f"@{{{col}}}{{{number_format}}} {unit}".rstrip()

        # Labels in the color of their graph, devices by alias, values with unit
        tooltips = (
            f'<div style="{CHART_TOOLTIP_STYLE} line-height: 1.5;">'
            '<div style="opacity: 0.7;">@date_time_local{%d.%m. %H:%M}</div>'
            + "".join(
                f'<div><span style="color: {renderer_colors[col]};">'
                f"{tooltip_text(column_label(col, device_ids))}</span>: "
                f"{tooltip_value(col)}</div>"
                for col in visible_columns
            )
            + "</div>"
        )
        plot.add_tools(
            HoverTool(
                renderers=[hover_renderer],
                mode="vline",
                tooltips=tooltips,
                formatters={"@date_time_local": "datetime"},
                callback=tooltip_callback(dark),
            )
        )
    bokey_apply_theme_to_plot(plot, dark)
    apply_chart_font(plot)
    if plot.legend:
        plot.legend[0].background_fill_color = "#1e1e1e" if dark else "white"
        plot.legend[0].label_text_color = "white" if dark else "black"
    # An axis with a single displayed column gets the color of that column
    for y_axis in plot.yaxis:
        axis_columns = [
            col
            for col in visible_columns
            if range_names.get(str(column_settings[col][1])) == y_axis.y_range_name
        ]
        if len(axis_columns) == 1:
            color = renderer_colors[axis_columns[0]]
            y_axis.axis_label_text_color = color
            y_axis.major_label_text_color = color
            y_axis.axis_line_color = color
            y_axis.major_tick_line_color = color
            y_axis.minor_tick_line_color = color

    # Totals of the displayed energy and amount columns over the time range of the chart
    column_totals: list[tuple[float, str, str, str, float]] = []
    for col in visible_columns:
        axis = column_settings[col][1]
        if axis not in ("energy", "amt"):
            # Factors and prices can not be added up
            continue
        total = float(df[col].sum())
        if col in solution_negative_columns:
            # Shown below the zero axis in the chart, as a positive total here
            total = 0.0 - total
        rank, icon = solution_total_icon(col, config)
        column_totals.append((rank, col, str(axis), icon, total))
    column_totals.sort(key=lambda t: t[0])
    total_max = {
        axis: max([abs(t[4]) for t in column_totals if t[2] == axis] + [0.0])
        for axis in ("energy", "amt")
    }
    total_rows = []
    for rank, col, axis, icon, total in column_totals:
        if axis == "energy":
            value_text = f"{total:.2f} kWh" if energy_kwh else f"{total:.0f} Wh"
        else:
            value_text = f"{total:.2f} {currency}" if currency else f"{total:.2f}"
        percent = abs(total) / total_max[axis] * 100.0 if total_max[axis] else 0.0
        total_rows.append(
            (icon, column_label(col, device_ids), renderer_colors[col], value_text, percent)
        )
    period_start = df["date_time_local"].min()
    period_end = df["date_time_local"].max() + pd.Timedelta(milliseconds=step_ms)
    totals_view = SolutionTotals(
        total_rows, f"{period_start:%d.%m. %H:%M} - {period_end:%d.%m. %H:%M}", dark
    )

    def amount(value: Any, scale: float = 1.0) -> str:
        if value is None:
            return "-"
        if isinstance(value, (set, list, tuple)):
            # fitness score is a set of fitness values
            return ", ".join(amount(v, scale) for v in sorted(value)) or "-"
        return f"{value * scale:.2f}"

    def money(value: Any) -> str:
        text = amount(value)
        return f"{text} {currency}" if currency and text != "-" else text

    totals = Card(
        Div(
            *[
                Div(
                    P(label, cls="text-xs opacity-70"),
                    P(value, cls="font-bold"),
                    cls="uk-text-center",
                )
                for label, value in (
                    ("Total revenues", money(solution.total_revenues_amt)),
                    ("Total costs", money(solution.total_costs_amt)),
                    ("Total losses", f"{amount(solution.total_losses_energy_wh, 0.001)} kWh"),
                    ("Fitness score", amount(solution.fitness_score)),
                )
            ],
            # Spans the time axis of the chart (PLAN_ALIGN_SCRIPT)
            data_time_axis="true",
            cls="grid grid-cols-4 gap-3",
        ),
    )

    # --- Collapsible sections to toggle, name, color and style the datasets ---
    def column_rows(columns: list[str]) -> list[Div]:
        return [
            SolutionColumnRow(col, column_label(col, device_ids), dark, col in prediction_columns)
            for col in renderers
            if col in columns
        ]

    # Instruction: the EMS plan commands (operation modes) of the plan and of the
    # operation mode factor columns, then the values of the optimizer
    mode_columns: dict[str, list[str]] = {}
    for col in renderers:
        device_mode = op_factor_device_mode(col, device_ids)
        if device_mode:
            mode_columns.setdefault(device_mode[1], []).append(col)
    plan_modes = [
        str(mode)
        for mode in (
            getattr(instruction, "operation_mode_id", None)
            for instruction in (eosstatus.eos_plan.instructions if eosstatus.eos_plan else [])
        )
        if mode
    ]
    modes = list(dict.fromkeys(plan_modes + list(mode_columns)))
    other_modes = [mode for mode in plan_mode_color if mode not in modes]

    def mode_row(mode: str) -> Div:
        return SolutionColumnRow(
            mode_key(mode),
            plan_mode_label(mode),
            dark,
            False,
            name=mode,
            enabled=mode in mode_columns,
        )

    instruction_rows: list[Any] = [mode_row(mode) for mode in modes]
    if other_modes:
        instruction_rows.append(
            Details(
                Summary("More commands", cls="cursor-pointer text-xs opacity-70"),
                Div(*[mode_row(mode) for mode in other_modes], cls="space-y-2 mt-2"),
                id="solution-more-modes",
                open="solution-more-modes" in plan_open_sections,
                data_keep_open="true",
            )
        )
    optimizer_rows = column_rows(
        [col for col in instruction_columns if not op_factor_device_mode(col, device_ids)]
    )
    if optimizer_rows:
        instruction_rows += [P("Optimizer", cls="text-xs font-bold opacity-70 pt-2")]
        instruction_rows += optimizer_rows

    devices = config_device_ids(config)
    if eosstatus.eos_plan:
        devices += [
            instruction.resource_id
            for instruction in eosstatus.eos_plan.instructions
            if instruction.id is not None
        ]
    devices = list(dict.fromkeys(devices))

    sections = [
        ("solution-prediction", "Prediction", column_rows(prediction_columns)),
        ("solution-solution", "Solution", column_rows(solution_columns)),
        ("solution-instruction", "Instruction", instruction_rows),
        ("solution-devices", "Devices", [DeviceRow(device_id) for device_id in devices]),
    ]
    # Commands under "More commands" are not counted
    counts = {"solution-instruction": len(modes) + len(optimizer_rows)}

    chart_option_items = [("energy_kwh", "Energy in kWh"), ("factor_percent", "Factors in %")]
    if minor_unit:
        chart_option_items.append(("price_minor_unit", f"Electricity Price in {minor_unit[0]}/kWh"))
    chart_options = PlanOptionsMenu("solution-chart-options", chart_option_items)
    return Div(
        totals,
        Div(
            Bokeh(plot),
            totals_view,
            # Top right corner of the chart background, left of the Bokeh toolbar
            Div(chart_options, style="position: absolute; top: 21px; right: 48px; z-index: 20;"),
            cls="relative",
        ),
        Div(
            *[
                ConfigSection(
                    f"{title} ({counts.get(section_id, len(rows))})",
                    *rows,
                    open=section_id in plan_open_sections,
                )(id=section_id, data_keep_open="true")
                for section_id, title, rows in sections
            ],
            cls="grid grid-cols-4 gap-3 items-start",
        ),
        Script(PLAN_POPOVER_SCRIPT),
        Script(PLAN_ALIGN_SCRIPT),
        cls="w-full space-y-3",
    )


def SolutionTotals(
    rows: list[tuple[str, str, str, str, float]], period: str, dark: bool
) -> Details:
    """Totals of the solution chart as bars, shown instead of the chart.

    A click on the icon at the top left corner of the chart switches between the chart
    and the totals.

    Args:
        rows (list[tuple[str, str, str, str, float]]): Icon, label, color, value text and
            bar length in percent of each displayed column.
        period (str): Time range of the totals.
        dark (bool): Dark theme.
    """

    def total_row(icon: str, label: str, color: str, value: str, percent: float) -> Div:
        return Div(
            Div(
                DivLAligned(
                    UkIcon(icon=icon, height=20, width=20, style=f"color: {color};"),
                    P(label, title=label, style=f"color: {color};", cls="text-xs truncate"),
                    cls="gap-2 min-w-0",
                ),
                P(value, cls="text-sm font-bold uk-text-center"),
                Div(),
                cls="grid grid-cols-3 items-center",
            ),
            Div(
                style=f"width: {max(percent, 0.5):.1f}%; height: 6px; border-radius: 3px; "
                f"background: {color};"
            ),
            cls="space-y-1",
        )

    background = "#1e1e1e" if dark else "white"
    return Details(
        Summary(
            UkIcon(icon="chart-bar", title="Show totals", cls="totals-show"),
            UkIcon(icon="chart-line", title="Show chart", cls="totals-hide"),
            # Top left corner of the chart background
            style="position: absolute; top: 21px; left: 21px; z-index: 20; padding: 6px;",
            cls="list-none cursor-pointer opacity-70 hover:opacity-100 border border-secondary "
            "rounded-lg",
        ),
        Div(
            P(f"Totals {period}", cls="text-xs font-bold opacity-70 uk-text-center"),
            Div(
                *[total_row(*row) for row in rows]
                or [P("No energy or amount values displayed.", cls="text-xs uk-text-center")],
                # Spans the time axis of the chart (PLAN_ALIGN_SCRIPT)
                data_time_axis="true",
                cls="space-y-3",
            ),
            # Covers the chart background
            style=f"position: absolute; inset: 17px; z-index: 10; padding: 1.75rem 0 1rem 0; "
            f"background: {background}; overflow-y: auto;",
            cls="space-y-4",
        ),
        Style(SOLUTION_TOTALS_STYLE),
        id=SOLUTION_TOTALS_ID,
        open=SOLUTION_TOTALS_ID in plan_open_sections,
        data_keep_open="true",
        style="border: none;",
    )


def SolutionStyleMenu(col: str, is_prediction: bool) -> Details:
    """Icon of the current display style; a click opens a menu for style, width and dash."""
    style = solution_column_style(col)
    width = solution_column_width(col)
    dash = solution_column_dash(col, is_prediction)
    menu_id = f"{col}-style-menu"

    def option(action: str, value: Any, content: Any, selected: bool, title: str) -> FhButton:
        return FhButton(
            content,
            title=title,
            hx_post=request_url_for("/eosdash/plan"),
            hx_target="#page-content",
            hx_swap="innerHTML",
            hx_vals=plan_hx_vals(
                category="solution", action=action, renderer=col, **{action: str(value)}
            ),
            cls="px-2 py-1 rounded text-xs border "
            + ("border-current font-bold" if selected else "border-transparent opacity-70"),
        )

    def dash_sample(value: str) -> Div:
        return Div(style=f"width: 28px; border-top: 2px {value} currentColor;")

    rows = [
        DivLAligned(
            option("style", "line", UkIcon(icon="chart-line"), style == "line", "Line"),
            option("style", "bar", UkIcon(icon="chart-column"), style == "bar", "Bar"),
            cls="gap-1",
        )
    ]
    if style == "line":
        rows += [
            P("Width", cls="text-xs opacity-70 mt-2"),
            DivLAligned(
                *[
                    option("width", w, str(w), w == width, f"Width {w}")
                    for w in SOLUTION_LINE_WIDTHS
                ],
                cls="gap-1",
            ),
            P("Line", cls="text-xs opacity-70 mt-2"),
            DivLAligned(
                *[
                    option("dash", d, dash_sample(d), d == dash, d.capitalize())
                    for d in SOLUTION_LINE_DASHES
                ],
                cls="gap-1",
            ),
        ]
    return Details(
        Summary(
            UkIcon(icon="chart-line" if style == "line" else "chart-column"),
            title="Display style",
            cls="list-none cursor-pointer opacity-80 hover:opacity-100",
        ),
        Div(
            *rows,
            cls="absolute right-0 top-full z-50 mt-1 p-2 rounded-md border shadow-lg bg-background",
        ),
        id=menu_id,
        open=menu_id in plan_open_sections,
        data_keep_open="true",
        data_popover="true",
        cls="relative shrink-0",
    )


def AliasMenu(menu_id: str, name: str, alias: str, vals: dict[str, str]) -> Details:
    """Alias icon; a click opens a dialog to enter the display name.

    Args:
        menu_id (str): Id of the dialog element.
        name (str): The id that gets the alias (column, mode or device id).
        alias (str): The current alias, empty if there is none.
        vals (dict[str, str]): htmx values that address the alias action.
    """
    return Details(
        Summary(
            UkIcon(icon="tag", height=16, width=16),
            title=f"Alias: {alias}" if alias else "Alias",
            cls="list-none cursor-pointer "
            + ("opacity-100" if alias else "opacity-50 hover:opacity-100"),
        ),
        Div(
            P(f"Alias for {name}", cls="text-xs opacity-70 truncate"),
            Form(
                Input(
                    type="text",
                    name="alias",
                    value=alias,
                    placeholder=name,
                    maxlength=str(DEVICE_ALIAS_MAX_LEN),
                    cls="uk-input h-7 px-2 text-xs w-full",
                ),
                DivLAligned(
                    FhButton(
                        "Remove",
                        type="button",
                        hx_post=request_url_for("/eosdash/plan"),
                        hx_target="#page-content",
                        hx_swap="innerHTML",
                        hx_params="not alias",
                        hx_vals=plan_hx_vals(close=menu_id, **vals),
                        cls="text-xs underline opacity-70 hover:opacity-100"
                        + ("" if alias else " invisible"),
                    ),
                    FhButton("Save", type="submit", cls="uk-btn uk-btn-primary uk-btn-sm"),
                    cls="justify-between",
                ),
                hx_post=request_url_for("/eosdash/plan"),
                hx_target="#page-content",
                hx_swap="innerHTML",
                hx_vals=plan_hx_vals(close=menu_id, **vals),
                cls="space-y-2",
            ),
            cls="absolute right-0 top-full z-50 mt-1 p-2 w-64 space-y-2 rounded-md border "
            "shadow-lg bg-background",
        ),
        id=menu_id,
        open=menu_id in plan_open_sections,
        data_keep_open="true",
        data_popover="true",
        cls="relative shrink-0",
    )


def DeviceRow(device_id: str) -> Div:
    """Creates the alias control of one device."""
    alias = plan_settings().device_alias.get(device_id, "")
    return Div(
        P(alias or device_id, title=device_id, cls="text-xs flex-1 min-w-0 truncate"),
        P(device_id, cls="text-xs opacity-50 shrink-0") if alias else None,
        AliasMenu(
            f"device-{device_id}-alias-menu",
            device_id,
            alias,
            {"category": "device_alias", "action": "alias", "device": device_id},
        ),
        cls="flex items-center gap-2",
    )


def SolutionColumnRow(
    col: str,
    label: str,
    dark: bool,
    is_prediction: bool,
    name: Optional[str] = None,
    enabled: bool = True,
) -> Div:
    """Creates the controls of one solution column or EMS plan command.

    Visibility, alias, color and display style.

    Args:
        col (str): Settings key, the column name or the mode key of a command.
        label (str): Display name.
        dark (bool): Dark theme.
        is_prediction (bool): Prediction columns are dotted by default.
        name (Optional[str]): The id that gets the alias, defaults to the column.
        enabled (bool): Whether there is something to display in the solution chart.
    """
    name = name or col
    is_user_color = col in plan_settings().color
    color = solution_column_color(col)
    return Div(
        CheckboxX(
            id=f"{col}-visible",
            name=f"{col}-visible",
            value="true",
            checked=enabled and solution_column_visible(col),
            disabled=not enabled,
            title=None if enabled else "Not in the current solution",
            hx_post=request_url_for("/eosdash/plan"),
            hx_target="#page-content",
            hx_swap="innerHTML",
            hx_vals=plan_hx_vals(category="solution", action="visible", renderer=col),
        ),
        FormLabel(
            label,
            fr=f"{col}-visible",
            title=name,
            style=f"color: {theme_color(color, dark)}",
            cls="text-xs flex-1 min-w-0 truncate",
        ),
        AliasMenu(
            f"{col}-alias-menu",
            name,
            plan_settings().alias.get(col, ""),
            {"category": "solution", "action": "alias", "renderer": col},
        ),
        Input(
            type="color",
            name="color",
            value=color,
            title="Color",
            hx_post=request_url_for("/eosdash/plan"),
            hx_trigger="change",
            hx_target="#page-content",
            hx_swap="innerHTML",
            hx_vals=plan_hx_vals(category="solution", action="color", renderer=col),
            cls="h-6 w-8 shrink-0 cursor-pointer border-0 bg-transparent p-0",
        ),
        SolutionStyleMenu(col, is_prediction),
        FhButton(
            UkIcon(icon="refresh-ccw", height=14, width=14),
            title="Reset color to default",
            hx_post=request_url_for("/eosdash/plan"),
            hx_target="#page-content",
            hx_swap="innerHTML",
            hx_vals=plan_hx_vals(category="solution", action="reset", renderer=col),
            cls="shrink-0 opacity-60 hover:opacity-100" + ("" if is_user_color else " invisible"),
        ),
        cls="flex items-center gap-2",
    )


def plan_color(mode: str) -> str:
    """Color of an operation mode, user choice first."""
    return solution_column_color(mode_key(mode))


def PlanChartCard(
    plan: EnergyManagementPlan,
    solution: OptimizationSolution,
    config: SettingsEOS,
    data: Optional[dict],
) -> Div:
    """Creates a timeline chart of the plan instructions, one row per resource.

    Each instruction is active from its execution time until the next instruction
    of the same resource. The last instruction of a resource is active until the
    end of the optimization solution.

    Args:
        plan (EnergyManagementPlan): The energy management plan.
        solution (OptimizationSolution): The optimization solution (gives the plan end).
        config (SettingsEOS): The EOS configuration.
        data (Optional[dict]): Incoming data containing the dark mode flag.

    Returns:
        Div: The plan timeline chart.
    """
    dark = bool(data and data.get("dark", None) == "true")

    # Determine desired timezone
    if config.general is None or config.general.timezone is None:
        date_time_tz = "Europe/Berlin"
    else:
        date_time_tz = config.general.timezone

    def to_local(dt: Any) -> pd.Timestamp:
        return pd.Timestamp(to_datetime(dt)).tz_convert(date_time_tz).tz_localize(None)

    # Collect the instructions per resource, the latest instruction wins on equal times
    resource_steps: dict[str, dict[pd.Timestamp, EnergyManagementInstruction]] = {}
    for instruction in plan.instructions:
        if instruction.id is None:
            continue
        resource_id = instruction.resource_id
        resource_steps.setdefault(resource_id, {})[to_local(instruction.execution_time)] = (
            instruction
        )
    if not resource_steps:
        raise ValueError("Plan has no instructions.")

    # The timeline shows the time range of the solution chart, so both time axes align
    solution_times = pd.to_datetime(solution.solution.to_dataframe()["date_time"], utc=True)
    solution_times = solution_times.dt.tz_convert(date_time_tz).dt.tz_localize(None)
    step = solution_times.diff().median()
    if pd.isna(step):
        step = pd.Timedelta(hours=1)
    plan_start = solution_times.min()
    plan_end = solution_times.max() + step

    # Order the rows like the devices in the configuration: batteries, cars, appliances
    device_order = config_device_ids(config)
    resources = [r for r in device_order if r in resource_steps]
    resources += sorted(r for r in resource_steps if r not in resources)

    # Rows are named by alias, the names must be unique
    resource_labels: dict[str, str] = {}
    for resource_id in resources:
        label = device_alias(resource_id)
        if label in resource_labels.values():
            label = f"{label} ({resource_id})"
        resource_labels[resource_id] = label

    total_ms = (plan_end - plan_start).total_seconds() * 1000
    bars: dict[str, list] = {
        k: [] for k in ("resource", "start", "end", "mode", "mode_label", "text", "color", "factor")
    }
    for resource_id in resources:
        steps = sorted(resource_steps[resource_id].items())
        for idx, (start, instruction) in enumerate(steps):
            end = steps[idx + 1][0] if idx + 1 < len(steps) else plan_end
            # Cut to the displayed time range
            start, end = max(start, plan_start), min(end, plan_end)
            if end <= start:
                continue
            mode = str(getattr(instruction, "operation_mode_id", None) or instruction.type)
            factor = getattr(instruction, "operation_mode_factor", None)
            mode_label = plan_mode_label(mode)
            label = mode_label if mode_label != mode else mode.lower()
            if factor is not None and 0.0 < factor < 1.0:
                label += f" {factor * 100:.0f}%"
            # Only print the label if it fits into the bar
            bar_px = (end - start).total_seconds() * 1000 / total_ms * PLAN_CHART_WIDTH_PX
            text = label if bar_px > len(label) * PLAN_LABEL_CHAR_PX + 12 else ""
            bars["resource"].append(resource_labels[resource_id])
            bars["start"].append(start)
            bars["end"].append(end)
            bars["mode"].append(mode)
            bars["mode_label"].append(mode_label)
            bars["text"].append(text)
            bars["color"].append(theme_color(plan_color(mode), dark))
            bars["factor"].append("" if factor is None else f"{factor:.2f}")
    source = ColumnDataSource(pd.DataFrame(bars))

    background = "#1e1e1e" if dark else "white"
    plot = create_figure(
        title="EMS Plan",
        name=TIME_AXIS_FIGURE_NAME,
        x_axis_type="datetime",
        y_range=FactorRange(factors=[resource_labels[r] for r in reversed(resources)]),
        x_range=Range1d(start=plan_start, end=plan_end),
        sizing_mode="stretch_width",
        height=90 + 45 * len(resources),
        toolbar_location=None,
    )
    bars_renderer = plot.hbar(
        y="resource",
        left="start",
        right="end",
        height=0.75,
        color="color",
        line_color=background,
        line_width=3,
        legend_group="mode_label",
        source=source,
    )
    bars_renderer.glyph.update(border_radius=5)
    plot.text(
        x="start",
        y="resource",
        text="text",
        x_offset=8,
        text_baseline="middle",
        text_color="white",
        text_font=bokeh_value(CHART_FONT),  # a plain string would be read as a column name
        text_font_size="12px",
        source=source,
    )
    plot.add_tools(
        HoverTool(
            tooltips=(
                f'<div style="{CHART_TOOLTIP_STYLE} line-height: 1.5;">'
                '<div style="font-weight: bold;">@resource</div>'
                '<div><span style="color: @color;">&#9632; @mode_label</span> @factor</div>'
                '<div style="opacity: 0.7;">@start{%d.%m. %H:%M} - @end{%d.%m. %H:%M}</div>'
                "</div>"
            ),
            formatters={"@start": "datetime", "@end": "datetime"},
            callback=tooltip_callback(dark),
        )
    )
    plot.ygrid.grid_line_color = None
    plot.yaxis.axis_line_color = None
    plot.yaxis.major_tick_line_color = None
    plot.outline_line_color = None

    # Move the legend below the plot
    legend = plot.legend[0]
    plot.center.remove(legend)
    legend.orientation = "horizontal"
    legend.location = "center_left"
    legend.glyph_height = 10
    legend.glyph_width = 20
    legend.border_line_color = None
    plot.add_layout(legend, "below")

    bokey_apply_theme_to_plot(plot, dark)
    apply_chart_font(plot)
    # Device names larger than the axis numbers
    plot.yaxis.major_label_text_font_size = "14px"
    legend.background_fill_color = background
    legend.label_text_color = "white" if dark else "black"

    return Div(Bokeh(plot), cls="w-full")


def PlanOptionsMenu(menu_id: str, options: list[tuple[str, str]]) -> Details:
    """Gear icon; a click opens a menu with plan page options.

    Args:
        menu_id (str): Id of the menu element.
        options (list[tuple[str, str]]): Option names (PLAN_OPTIONS) and their labels.
    """
    return Details(
        Summary(
            UkIcon(icon="settings", height=16, width=16),
            title="Options",
            style="padding: 6px;",
            cls="list-none cursor-pointer opacity-70 hover:opacity-100",
        ),
        Div(
            P("Options", cls="text-sm font-bold"),
            *[
                DivLAligned(
                    CheckboxX(
                        id=option,
                        name=option,
                        value="true",
                        checked=plan_option(option),
                        hx_post=request_url_for("/eosdash/plan"),
                        hx_target="#page-content",
                        hx_swap="innerHTML",
                        hx_vals=plan_hx_vals(category="plan_option", option=option),
                    ),
                    FormLabel(label, fr=option, cls="text-xs"),
                    cls="gap-2",
                )
                for option, label in options
            ],
            cls="absolute right-0 top-full z-50 mt-1 p-2 w-56 space-y-2 rounded-md border "
            "shadow-lg bg-background",
        ),
        id=menu_id,
        open=menu_id in plan_open_sections,
        data_keep_open="true",
        data_popover="true",
        cls="relative",
    )


def PlanTable(cards: list[Any], open: bool) -> Div:
    """Creates the collapsible EMS plan table with the instruction cards.

    While the table is open a gear menu at the top right offers the table options.

    Args:
        cards (list[Any]): The instruction cards.
        open (bool): Whether the table is rendered open.

    Returns:
        Div: The collapsible plan table with its options menu.
    """
    options = PlanOptionsMenu(f"{PLAN_TABLE_ID}-options", [("plan_table_open", "Open by default")])
    return Div(
        Style(PLAN_TABLE_STYLE),
        ConfigSection(f"Energy Management Plan ({len(cards)})", *cards, open=open)(
            id=PLAN_TABLE_ID, data_keep_open="true"
        ),
        Div(
            options,
            cls="plan-table-options",
            style="position: absolute; top: 1.25rem; right: 1rem;",
        ),
        cls="relative",
    )


def InstructionCard(
    instruction: EnergyManagementInstruction, config: SettingsEOS, data: Optional[dict]
) -> Card:
    """Creates a styled instruction card for displaying instruction details.

    This function generates a instruction card that is displayed in the UI with
    various sections such as instruction name, type, description, default value,
    current value, and error details. It supports both read-only and editable modes.

    Args:
        instruction (EnergyManagementInstruction): The instruction.
        data (Optional[dict]): Incoming data containing action and category for processing.

    Returns:
        Card: A styled Card component containing the instruction details.
    """
    if instruction.id is None:
        return Error("Instruction without id encountered. Can not handle")
    idx = instruction.id.find("@")
    resource_id = instruction.id[:idx] if idx != -1 else instruction.id
    execution_time = to_datetime(instruction.execution_time, as_string=True)
    description = instruction.type
    summary = None
    # Search an icon that fits to device_id
    if (
        config.devices
        and config.devices.batteries
        and any(
            battery_config.device_id == resource_id
            for battery_config in config.devices.batteries.values()
        )
    ):
        # This is a battery
        if getattr(instruction, "operation_mode_id", None) in ("CHARGE",):
            icon = "battery-charging"
        else:
            icon = "battery"
    elif (
        config.devices
        and config.devices.electric_vehicles
        and any(
            electric_vehicle_config.device_id == resource_id
            for electric_vehicle_config in config.devices.electric_vehicles.values()
        )
    ):
        # This is a car battery
        icon = "car"
    elif (
        config.devices
        and config.devices.home_appliances
        and any(
            home_appliance.device_id == resource_id
            for home_appliance in config.devices.home_appliances.values()
        )
    ):
        # This is a home appliance
        icon = "washing-machine"
    else:
        icon = "play"

    # Initialize defaults so all code paths are covered
    summary = summary or ""
    summary_detail = ""

    mode = ""
    if isinstance(instruction, OMBCInstruction):
        mode = f"{instruction.operation_mode_id}"
        summary = plan_mode_label(mode)
        summary_detail = f"{instruction.operation_mode_factor:.2f}"
    elif isinstance(instruction, (DDBCInstruction, FRBCInstruction)):
        mode = f"{instruction.operation_mode_id}"
        summary = plan_mode_label(mode)
        summary_detail = f"{instruction.operation_mode_factor}"

    return Card(
        Details(
            Summary(
                Grid(
                    Grid(
                        DivLAligned(
                            UkIcon(icon=icon),
                            P(execution_time),
                        ),
                        DivLAligned(
                            P(device_alias(resource_id), title=resource_id),
                        ),
                    ),
                    P(summary, title=mode or None),
                    P(summary_detail),
                ),
                cls="list-none",
            ),
            Grid(
                P(description),
                P("TBD"),
            ),
        ),
        cls="w-full",
    )


def Plan(eos_host: str, eos_port: Union[str, int], data: Optional[dict] = None) -> Div:
    """Generates the plan dashboard layout.

    Args:
        eos_host (str): The hostname of the EOS server.
        eos_port (Union[str, int]): The port of the EOS server.
        data (Optional[dict], optional): Incoming data to trigger plan actions. Defaults to None.

    Returns:
        Div: A `Div` component containing the assembled admin interface.
    """
    global plan_open_sections
    server = f"http://{eos_host}:{eos_port}"

    print("Plan: ", data)

    # Collapsible elements that the user has open (sent by the plan page controls).
    # Without (page load) the elements get their default open state.
    keep_open_state = bool(data and "open" in data)
    if data and "open" in data:
        plan_open_sections = {s for s in str(data["open"]).split(",") if s}
    elif data:
        plan_open_sections = set()
    if data and data.get("close"):
        plan_open_sections.discard(str(data["close"]))

    if (
        eosstatus.eos_config is None
        or eosstatus.eos_solution is None
        or eosstatus.eos_plan is None
        or eosstatus.eos_health is None
        or compare_datetimes(
            to_datetime(eosstatus.eos_plan.generated_at),
            to_datetime(eosstatus.eos_health["energy-management"]["last_run_datetime"]),
        ).lt
    ):
        # Get current configuration from server
        try:
            result = requests.get(f"{server}/v1/config", timeout=10)
            result.raise_for_status()
        except requests.exceptions.HTTPError as err:
            detail = result.json()["detail"]
            return Error(f"Can not retrieve configuration from {server}: {err},\n{detail}")
        eosstatus.eos_config = SettingsEOS(**result.json())

        # Get the optimization solution
        try:
            result = requests.get(
                f"{server}/v1/energy-management/optimization/solution", timeout=10
            )
            result.raise_for_status()
            solution_json = result.json()
        except requests.exceptions.HTTPError as e:
            detail = result.json()["detail"]
            warning_msg = f"Can not retrieve optimization solution from {server}: {e},\n{detail}"
            logger.warning(warning_msg)
            return Error(warning_msg)
        except Exception as e:
            warning_msg = f"Can not retrieve optimization solution from {server}: {e}"
            logger.warning(warning_msg)
            return Error(warning_msg)
        eosstatus.eos_solution = OptimizationSolution(**solution_json)

        # Get the plan
        try:
            result = requests.get(f"{server}/v1/energy-management/plan", timeout=10)
            result.raise_for_status()
            plan_json = result.json()
        except requests.exceptions.HTTPError as e:
            detail = result.json()["detail"]
            warning_msg = f"Can not retrieve plan from {server}: {e},\n{detail}"
            logger.warning(warning_msg)
            return Error(warning_msg)
        except Exception as e:
            warning_msg = f"Can not retrieve plan from {server}: {e}"
            logger.warning(warning_msg)
            return Error(warning_msg)
        eosstatus.eos_plan = EnergyManagementPlan(**plan_json, data=data)

    # Device alias changed in the plan settings menu
    if data and data.get("category", None) == "device_alias" and data.get("action") == "alias":
        device_id = str(data.get("device", ""))
        known = set(config_device_ids(eosstatus.eos_config)) | {
            i.resource_id for i in eosstatus.eos_plan.instructions if i.id is not None
        }
        if device_id in known:
            alias = alias_clean(str(data.get("alias", "")))
            aliases = {k: v for k, v in plan_settings().device_alias.items() if k != device_id}
            if alias and alias != device_id:
                aliases[device_id] = alias
            plan_settings_put(server, "device_alias", aliases)

    # Setting of a solution column or command changed
    if data and data.get("category", None) == "solution":
        solution_settings_action(server, data)

    # Option changed in a gear menu
    if data and data.get("category", None) == "plan_option":
        option = str(data.get("option", ""))
        if option in PLAN_OPTIONS:
            plan_settings_put(server, option, bool(data.get(option, False)))

    rows = [
        SolutionCard(eosstatus.eos_solution, eosstatus.eos_config, data=data),
    ]
    try:
        rows.append(
            PlanChartCard(
                eosstatus.eos_plan, eosstatus.eos_solution, eosstatus.eos_config, data=data
            )
        )
    except Exception as e:
        logger.warning(f"Can not create plan chart: {e}")
        rows.append(Error(f"Can not create plan chart: {e}"))
    if keep_open_state:
        table_open = PLAN_TABLE_ID in plan_open_sections
    else:
        table_open = plan_option("plan_table_open")
    cards = [
        InstructionCard(instruction, eosstatus.eos_config, data=data)
        for instruction in eosstatus.eos_plan.instructions
    ]
    rows.append(PlanTable(cards, table_open))
    return Div(*rows, cls="space-y-4")

    # return Div(f"Plan:\n{json.dumps(plan_json, indent=4)}")
