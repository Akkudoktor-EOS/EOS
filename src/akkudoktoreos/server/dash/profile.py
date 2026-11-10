"""Device profile editor for EOSdash.

Lists the battery and inverter profiles of the EOS profile store and lets the
user view, edit, save, copy and delete them. The profile kind is selected at the
top of the page. All access goes through the REST API of EOS
(``/v1/profile/<kind>``); EOS validates every document before it is saved.

Packaged profiles (shipped with EOS) are read only. Saving one stores a user
profile of the same name that overrides it; deleting that user profile reverts
to the packaged one. Saved profiles take effect at once in EOS, also for
running device controllers.

The document is edited as JSON. The summary and the Bokeh plots show the
stored profile, after a save the saved one. *Preview* draws them from the
editor text instead, without saving, so changes can be checked before they
reach EOS and the device controllers. The plots show:

- heat maps of the charge and discharge limit maps (SoC versus temperature),
  drawn from the limit maps compiled by EOS (``compile_battery_profile``), i.e.
  the dense lookup table EOS uses at runtime: the interpolation of the profile
  (bilinear, nearest, none), the hard temperature cutoffs (inhibited cells) and
  the C-rate unit are all as EOS applies them. The breakpoints of the document
  are marked; their values are also listed in a table,
- the charge and discharge efficiency curves.

The efficiency plot draws each curve the way EOS evaluates it
(``PrecomputedEfficiencyCurve.lookup``), by mapping the interpolation of the
profile to a Bokeh glyph:

- ``linear``: a line through the breakpoints,
- ``nearest``: a step that changes at the midpoints between breakpoints
  (``step(mode="center")``),
- ``none``: only the breakpoints (other C-rates have no efficiency),

and the out-of-range behavior to the parts left and right of the curve:
``clamp_to_nearest`` as dashed lines at the end values, ``reject`` as nothing.

For an inverter profile the page shows the DC→AC and AC→DC efficiency curves
over the load fraction and the power derating over the ambient temperature,
with the default temperature of 45 degC marked. These curves are read linearly
and clamped at their ends, which the dashed parts show.
"""

import json
from typing import Any, Optional, Union
from urllib.parse import quote, urlencode

import numpy as np
import requests
from bokeh.models import ColumnDataSource, HoverTool
from bokeh.models import Image as ImageGlyph
from bokeh.models import LinearColorMapper
from bokeh.models.annotations import ColorBar, Span
from bokeh.models.renderers import DataRenderer
from bokeh.palettes import Viridis256
from fasthtml.common import Option, Select
from loguru import logger
from monsterui.franken import (
    H4,
    A,
    ButtonT,
    Details,
    Div,
    DivHStacked,
    Form,
    Grid,
    Input,
    P,
    Summary,
    Table,
    Tbody,
    Td,
    TextArea,
    Th,
    Thead,
    Tr,
)

from akkudoktoreos.devices.profile.batteryprofile import (
    BatteryProfile,
    BatteryProfileSettings,
    EfficiencyCurve,
    EfficiencyInterpolation,
    OutOfRangeBehavior,
)
from akkudoktoreos.devices.profile.inverterprofile import (
    InverterEfficiencyCurve,
    InverterProfileSettings,
    PowerDeratingCurve,
)
from akkudoktoreos.devices.profile.profilestore import (
    PROFILE_NAME_PATTERN,
    RESERVED_PROFILE_NAMES,
    ProfileDocument,
)
from akkudoktoreos.devices.settings.inverterresolution import (
    INVERTER_DEFAULT_TEMPERATURE_DEGC,
)
from akkudoktoreos.server.dash.bokeh import (
    Bokeh,
    bokey_apply_theme_to_plot,
    create_figure,
)
from akkudoktoreos.server.dash.components import (
    ConfigButton,
    ConfigSection,
    Error,
    Info,
    JsonView,
    Success,
)
from akkudoktoreos.server.dash.context import request_url_for

PAGE_PATH = "/eosdash/profile"
PROFILE_SELECT_ID = "profile-select"
EDITOR_FORM_ID = "profile-editor"

# Profile kinds of the page: kind -> (label, default profile, document type).
PROFILE_KINDS: dict[str, tuple[str, str, type[ProfileDocument[Any]]]] = {
    "battery": ("Battery", "generic-lfp", BatteryProfileSettings),
    "inverter": ("Inverter", "generic-hybrid", InverterProfileSettings),
}
DEFAULT_KIND = "battery"
REQUEST_TIMEOUT_S = 10

# Plot heights [px], kept low for a condensed page.
LIMIT_MAP_HEIGHT_PX = 320
CURVE_PLOT_HEIGHT_PX = 280

# Explanatory text: the muted text color of the theme, readable in light and dark mode.
HINT_CLS = "text-sm text-muted-foreground"

# Collapsible sections of the profile page: (id, open on the first load).
SECTION_EDITOR = "editor"
SECTION_LIMITS = "limits"
SECTION_EFFICIENCY = "efficiency"
SECTION_JSON = "json"
SECTION_DERATING = "derating"
SECTION_DEFAULT_OPEN: dict[str, bool] = {
    SECTION_EDITOR: False,
    SECTION_DERATING: True,
    SECTION_LIMITS: True,
    SECTION_EFFICIENCY: True,
    SECTION_JSON: False,
}

# Sharing a profile: a new issue with the battery profile form of the EOS repository,
# filled in through URL parameters (one per form field id).
SHARE_ISSUE_URL = "https://github.com/Akkudoktor-EOS/EOS/issues/new"
# Issue form per profile kind: .github/ISSUE_TEMPLATE/<kind>-profile.yml, with the
# fields "<kind>_model", "profile_name" and "profile".
SHARE_ISSUE_TEMPLATE = "{kind}-profile.yml"
# Longer URLs are not reliably accepted by GitHub; the profile is then pasted by hand.
SHARE_URL_MAX_LENGTH = 8000

# Efficiency curves of a profile document: (document key, label, color).
EFFICIENCY_CURVES: tuple[tuple[str, str, str], ...] = (
    ("charge_efficiency", "Charge", "#2b83ba"),
    ("discharge_efficiency", "Discharge", "#d7191c"),
)
# Background of plots and their annotations in dark mode (as bokey_apply_theme_to_plot).
DARK_BACKGROUND = "#1e1e1e"
# The plot extends this much beyond the last breakpoint, to show the out-of-range behavior.
EFFICIENCY_PLOT_MARGIN = 0.25

# Fields shown in the summary: (document key, label, unit).
SUMMARY_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("battery_model", "Model", ""),
    ("chemistry", "Chemistry", ""),
    ("nominal_capacity_wh", "Nominal capacity", "Wh"),
    ("nominal_voltage_v", "Nominal voltage", "V"),
    ("max_charge_power_w", "Max. charge power", "W"),
    ("max_discharge_power_w", "Max. discharge power", "W"),
    ("technical_min_soc_percent", "Technical min. SoC", "%"),
    ("technical_max_soc_percent", "Technical max. SoC", "%"),
    ("full_charge_interval_days", "Full charge interval", "d"),
    ("full_charge_detect_percentage", "Full charge detected from", "%"),
)

# Fields shown in the summary of an inverter profile: (document key, label, unit).
INVERTER_SUMMARY_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("inverter_model", "Model", ""),
    ("nominal_power_w", "Nominal power", "W"),
    ("max_ac_charge_power_w", "Max. AC charge power", "W"),
    ("ac_charge_limits_total_charge", "AC charge limit also limits PV charging", ""),
)
# Efficiency curves of an inverter profile document: (document key, label, color).
INVERTER_EFFICIENCY_CURVES: tuple[tuple[str, str, str], ...] = (
    ("dc_to_ac_efficiency", "DC→AC (discharge)", "#d7191c"),
    ("ac_to_dc_efficiency", "AC→DC (grid charge)", "#2b83ba"),
)


class ProfileApiError(Exception):
    """An EOS profile API request failed; the message is shown to the user."""


def _detail(response: Any) -> str:
    """Return the error detail of an EOS response."""
    try:
        detail = response.json().get("detail", "")
    except Exception:
        detail = getattr(response, "text", "")
    if isinstance(detail, list):  # FastAPI request validation errors
        detail = "; ".join(str(item.get("msg", item)) for item in detail)
    return str(detail)


def _request(method: str, url: str, **kwargs: Any) -> Any:
    """Send a request to EOS and return the decoded JSON body.

    Raises:
        ProfileApiError: If EOS cannot be reached or answers with an error.
    """
    try:
        response = getattr(requests, method)(url, timeout=REQUEST_TIMEOUT_S, **kwargs)
    except Exception as exc:
        raise ProfileApiError(f"EOS server not reachable: {exc}") from exc
    if response.status_code >= 400:
        raise ProfileApiError(f"{response.status_code}: {_detail(response)}")
    return response.json()


def _profile_url(server: str, kind: str, name: Optional[str] = None) -> str:
    url = f"{server}/v1/profile/{kind}"
    return url if name is None else f"{url}/{quote(name, safe='')}"


def _entry_label(entry: dict[str, Any]) -> str:
    """Label of a profile in the selection."""
    if entry["source"] == "package":
        return f"{entry['name']} (packaged)"
    if entry["packaged"]:
        return f"{entry['name']} (user, overrides packaged)"
    return f"{entry['name']} (user)"


def _format_value(value: Any, unit: str) -> str:
    if value is None:
        return "-"
    return f"{value} {unit}".strip()


def ProfileSection(
    title: str, *content: Any, open: bool = False, data_profile_section: str
) -> Details:
    """Compact collapsible section of the profile page (``ConfigSection``, compact).

    The section id is reported with every page action, so the page keeps the
    section open or closed when it is replaced.

    Args:
        title: Title shown in the summary.
        *content: Content of the section.
        open: Whether the section is open.
        data_profile_section: Section id, e.g. ``SECTION_LIMITS``.
    """
    return ConfigSection(
        title, *content, open=open, compact=True, data_profile_section=data_profile_section
    )


def _summary_item(label: str, value: Any) -> Div:
    """One compact label/value pair in the profile summary."""
    return Div(
        P(label, cls="text-xs text-muted-foreground leading-tight"),
        P(str(value), cls="text-sm font-medium leading-tight"),
        cls="min-w-0 px-3 py-2 border -mt-px -ml-px",
    )


def _summary_strip(items: list[tuple[str, Any]]) -> Div:
    """Render profile metadata as a compact, wrapping information grid.

    Every cell has a full border; the negative margins (and the grid padding
    that compensates them) let neighbouring borders overlap into single lines,
    also between wrapped rows and around a last row that is not full.
    ``auto-fit`` leaves no empty columns. Neither the grid nor the cells have a
    background of their own: the tab background shows.
    """
    return Div(
        *(_summary_item(label, value) for label, value in items),
        cls="grid grid-cols-[repeat(auto-fit,minmax(11rem,1fr))] pt-px pl-px",
    )


def ProfileSummary(document: dict[str, Any]) -> Div:
    """Compact key data of a battery profile document."""
    cutoffs = document.get("hard_cutoffs") or {}
    items = [(label, _format_value(document.get(key), unit)) for key, label, unit in SUMMARY_FIELDS]
    items.extend(
        [
            (
                "Charge temperature",
                f"{_format_value(cutoffs.get('charge_allowed_below_degC'), 'degC')} – "
                f"{_format_value(cutoffs.get('charge_allowed_above_degC'), 'degC')}",
            ),
            (
                "Discharge temperature",
                f"{_format_value(cutoffs.get('discharge_allowed_below_degC'), 'degC')} – "
                f"{_format_value(cutoffs.get('discharge_allowed_above_degC'), 'degC')}",
            ),
        ]
    )
    return _summary_strip(items)


def LimitMapTable(title: str, document: dict[str, Any], key: str) -> Div:
    """Read-only table of a limit map: temperature rows, SoC columns."""
    axes = document.get("axes") or {}
    temperatures = axes.get("temperature_degC") or []
    socs = axes.get("soc_percent") or []
    values = (document.get(key) or {}).get("values") or []
    unit = (document.get("units") or {}).get("limit", "")
    if not temperatures or not socs or len(values) != len(temperatures):
        return Div(H4(title), P("No limit map to show."))
    return Div(
        H4(f"{title} [{unit}]"),
        Div(
            Table(
                Thead(Tr(Th("degC \\ SoC %"), *(Th(str(soc)) for soc in socs))),
                Tbody(
                    *(
                        Tr(Th(str(temperature)), *(Td(str(value)) for value in row))
                        for temperature, row in zip(temperatures, values)
                    )
                ),
                cls="text-xs",
            ),
            cls="overflow-x-auto",
        ),
    )


# Limit maps of a profile: (attribute of BatteryProfile, document key, label).
# Temperature resolution of the drawn heat maps at the hard cutoffs [degC].
HEATMAP_TEMPERATURE_RESOLUTION_DEGC: float = 0.1

LIMIT_MAPS: tuple[tuple[str, str, str], ...] = (
    ("charge_map", "charge_limit_crate", "Charge limit"),
    ("discharge_map", "discharge_limit_crate", "Discharge limit"),
)


def limit_heatmap_image(profile: BatteryProfile, attribute: str) -> dict[str, Any]:
    """Map a compiled limit map to the parameters of a Bokeh ``image`` glyph.

    The compiled map is the lookup table EOS uses: a query is rounded to the
    nearest cell (``PrecomputedChargeDischargeLimitMap.lookup``). Each cell is
    therefore drawn centered on its grid point, ``t_step`` x ``s_step`` wide.
    The hard cutoffs are checked on the exact temperature, so every cell row is
    drawn as rows of ``HEATMAP_TEMPERATURE_RESOLUTION_DEGC``; rows outside the
    cutoffs are blank.

    Args:
        profile: Compiled battery profile.
        attribute: ``"charge_map"`` or ``"discharge_map"``.

    Returns:
        ``image`` (rows: temperature, columns: SoC; NaN where EOS inhibits the
        direction), ``x``/``y`` (lower left corner, SoC % / degC), ``dw``/``dh``
        (width / height).
    """
    limit_map = getattr(profile, attribute)
    grid = np.asarray(limit_map.grid, dtype=float)
    rows_per_cell = max(1, round(limit_map.t_step / HEATMAP_TEMPERATURE_RESOLUTION_DEGC))
    grid = np.repeat(grid, rows_per_cell, axis=0)
    row_height = limit_map.t_step / rows_per_cell
    bottom = limit_map.t_min - limit_map.t_step / 2.0
    row_centers = bottom + (np.arange(grid.shape[0]) + 0.5) * row_height
    inhibited = np.zeros(grid.shape[0], dtype=bool)
    if limit_map.allowed_min_degC is not None:
        inhibited |= row_centers < limit_map.allowed_min_degC
    if limit_map.allowed_max_degC is not None:
        inhibited |= row_centers > limit_map.allowed_max_degC
    grid[inhibited, :] = np.nan
    return {
        "image": grid,
        "x": limit_map.s_min - limit_map.s_step / 2.0,
        "y": limit_map.t_min - limit_map.t_step / 2.0,
        "dw": limit_map.n_soc * limit_map.s_step,
        "dh": limit_map.n_temp * limit_map.t_step,
    }


def LimitHeatMap(
    profile: BatteryProfile,
    document: dict[str, Any],
    attribute: str,
    key: str,
    title: str,
    high: float,
    dark: bool,
) -> Any:
    """Bokeh heat map of one limit map, with the breakpoints of the document."""
    unit = profile.limit_unit
    image = limit_heatmap_image(profile, attribute)
    mapper = LinearColorMapper(
        palette=Viridis256, low=0.0, high=high, nan_color="#555555" if dark else "#c8c8c8"
    )
    plot = create_figure(
        title=f"{title} [{unit}] (as evaluated by EOS)",
        x_axis_label="SoC [%]",
        y_axis_label="Temperature [degC]",
        x_range=(image["x"], image["x"] + image["dw"]),
        y_range=(image["y"], image["y"] + image["dh"]),
        sizing_mode="stretch_width",
        height=LIMIT_MAP_HEIGHT_PX,
        tools="pan,wheel_zoom,box_zoom,reset,save",
    )
    image_renderer = plot.add_glyph(
        ColumnDataSource({"image": [image["image"]]}),
        ImageGlyph(
            image="image",
            x=image["x"],
            y=image["y"],
            dw=image["dw"],
            dh=image["dh"],
            color_mapper=mapper,
        ),
    )
    plot.add_tools(
        HoverTool(
            renderers=[image_renderer],
            tooltips=[
                ("SoC", "$x{0} %"),
                ("temperature", "$y{0.0} degC"),
                (f"limit [{unit}]", "@image{0.00}"),
            ],
        )
    )

    axes = document.get("axes") or {}
    temperatures = axes.get("temperature_degC") or []
    socs = axes.get("soc_percent") or []
    values = (document.get(key) or {}).get("values") or []
    if temperatures and socs and len(values) == len(temperatures):
        points = ColumnDataSource(
            {
                "soc": [soc for _ in temperatures for soc in socs],
                "temperature": [t for t in temperatures for _ in socs],
                "value": [v for row in values for v in row],
            }
        )
        point_renderer = plot.scatter(
            "soc",
            "temperature",
            source=points,
            size=6,
            fill_color="white",
            line_color="black",
        )
        plot.add_tools(
            HoverTool(
                renderers=[point_renderer],
                tooltips=[
                    ("breakpoint", "@soc %, @temperature degC"),
                    (f"limit [{unit}]", "@value"),
                ],
            )
        )
    color_bar = ColorBar(color_mapper=mapper, title=unit)
    if dark:
        # The theme of the plot does not reach annotations: the color bar has its own colors.
        color_bar.background_fill_color = DARK_BACKGROUND
        color_bar.background_fill_alpha = 1.0
        color_bar.title_text_color = "white"
        color_bar.major_label_text_color = "white"
        color_bar.major_tick_line_color = "white"
    plot.add_layout(color_bar, "right")
    plot.toolbar.autohide = True
    bokey_apply_theme_to_plot(plot, dark)
    return Bokeh(plot)


def LimitMaps(document: dict[str, Any], dark: bool = False, open: bool = True) -> Any:
    """Heat maps of the charge and discharge limit maps, and their breakpoint values.

    The document is compiled like EOS compiles it; a document that does not
    compile (e.g. while editing) is reported instead of drawn.
    """
    tables = Details(
        Summary("Breakpoint values", cls="cursor-pointer"),
        Grid(
            *(LimitMapTable(label, document, key) for _, key, label in LIMIT_MAPS),
            cols_max=2,
        ),
    )
    try:
        profile = BatteryProfileSettings.model_validate(document).to_limits()
    except ValueError as exc:
        return ProfileSection(
            "Charge and discharge limits",
            Error(f"The limit maps can not be compiled: {exc}"),
            tables,
            open=open,
            data_profile_section=SECTION_LIMITS,
        )
    grids = [
        np.asarray(getattr(profile, attribute).grid, dtype=float) for attribute, _, _ in LIMIT_MAPS
    ]
    finite = [grid[np.isfinite(grid)] for grid in grids]
    high = max((float(values.max()) for values in finite if values.size), default=1.0) or 1.0
    return ProfileSection(
        "Charge and discharge limits",
        Grid(
            *(
                LimitHeatMap(profile, document, attribute, key, label, high, dark)
                for attribute, key, label in LIMIT_MAPS
            ),
            cols_max=2,
        ),
        P(
            "Colors: the limit EOS uses at each SoC and temperature (both maps share the color "
            "scale). Gray: the direction is inhibited (hard temperature cutoff). Circles: the "
            "breakpoints of the profile document.",
            cls=HINT_CLS,
        ),
        tables,
        open=open,
        data_profile_section=SECTION_LIMITS,
    )


def efficiency_glyphs(curve: EfficiencyCurve, c_rate_max: float) -> dict[str, Any]:
    """Map an efficiency curve to the Bokeh glyphs that draw it as EOS evaluates it.

    Args:
        curve: Validated efficiency curve.
        c_rate_max: Right end of the plot [C-rate]; the left end is 0.

    Returns:
        A dict with:

        - ``points``: ``(c_rates, efficiencies)`` of the breakpoints, always drawn
          as markers.
        - ``inside``: ``(glyph, c_rates, efficiencies)`` between the first and last
          breakpoint: ``glyph`` is ``"line"`` (linear), ``"step"`` (nearest, step
          at the midpoints) or None (none: breakpoints only).
        - ``outside``: list of ``(c_rates, efficiencies)`` dashed segments left of
          the first and right of the last breakpoint (clamp to nearest); empty if
          out-of-range C-rates are rejected.
    """
    c_rates = list(curve.c_rate)
    efficiencies = list(curve.efficiency)
    glyph: Optional[str] = {
        EfficiencyInterpolation.LINEAR: "line",
        EfficiencyInterpolation.NEAREST: "step",
        EfficiencyInterpolation.NONE: None,
    }[curve.interpolation]
    outside: list[tuple[list[float], list[float]]] = []
    if curve.out_of_range_behavior == OutOfRangeBehavior.CLAMP_TO_NEAREST:
        if c_rates[0] > 0.0:
            outside.append(([0.0, c_rates[0]], [efficiencies[0]] * 2))
        if c_rate_max > c_rates[-1]:
            outside.append(([c_rates[-1], c_rate_max], [efficiencies[-1]] * 2))
    return {
        "points": (c_rates, efficiencies),
        "inside": (glyph, c_rates, efficiencies),
        "outside": outside,
    }


def _interpolation_label(curve: EfficiencyCurve) -> str:
    mode = {
        EfficiencyInterpolation.LINEAR: "linear",
        EfficiencyInterpolation.NEAREST: "nearest",
        EfficiencyInterpolation.NONE: "breakpoints only",
    }[curve.interpolation]
    outside = (
        "clamped"
        if curve.out_of_range_behavior == OutOfRangeBehavior.CLAMP_TO_NEAREST
        else "rejected"
    )
    return f"{mode}, out of range {outside}"


def EfficiencyPlot(document: dict[str, Any], dark: bool = False, open: bool = True) -> Any:
    """Bokeh plot of the charge and discharge efficiency curves of a profile document.

    Curves the document does not have (e.g. while editing) are reported as an
    error below the plot, as the profile requires both; invalid curves are
    reported instead of drawn.
    """
    curves: list[tuple[str, str, EfficiencyCurve]] = []
    notes: list[Any] = []
    for key, label, color in EFFICIENCY_CURVES:
        raw = document.get(key)
        if raw is None:
            notes.append(
                Error(
                    f"No {label.lower()} efficiency curve: the profile requires "
                    f"'{key}' and cannot be saved without it."
                )
            )
            continue
        try:
            curves.append((label, color, EfficiencyCurve.model_validate(raw)))
        except ValueError as exc:
            notes.append(Error(f"Invalid {label.lower()} efficiency curve: {exc}"))
    if not curves:
        return ProfileSection(
            "Efficiency", *notes, open=open, data_profile_section=SECTION_EFFICIENCY
        )

    c_rate_max = max(curve.c_rate[-1] for _, _, curve in curves) * (1.0 + EFFICIENCY_PLOT_MARGIN)
    capacity_wh = document.get("nominal_capacity_wh")
    tooltips = [
        ("", "@label"),
        ("C-rate", "@c_rate{0.00} C"),
        ("efficiency", "@efficiency_pct{0.0} %"),
    ]
    if capacity_wh:
        tooltips.insert(2, ("power", "@power_w{0} W"))

    plot = create_figure(
        title="Battery efficiency versus C-rate (as evaluated by EOS)",
        x_axis_label="C-rate [1/h]",
        y_axis_label="Efficiency [%]",
        x_range=(0.0, c_rate_max),
        sizing_mode="stretch_width",
        height=CURVE_PLOT_HEIGHT_PX,
        tools="pan,wheel_zoom,box_zoom,reset,save",
    )
    point_renderers: list[DataRenderer] = []
    for label, color, curve in curves:
        glyphs = efficiency_glyphs(curve, c_rate_max)
        legend = f"{label} ({_interpolation_label(curve)})"
        glyph, xs, ys = glyphs["inside"]
        ys_pct = [100.0 * y for y in ys]
        if glyph == "line":
            plot.line(xs, ys_pct, color=color, line_width=2, legend_label=legend)
        elif glyph == "step":
            plot.step(xs, ys_pct, mode="center", color=color, line_width=2, legend_label=legend)
        for seg_x, seg_y in glyphs["outside"]:
            plot.line(
                seg_x,
                [100.0 * y for y in seg_y],
                color=color,
                line_width=2,
                line_dash="dashed",
                line_alpha=0.6,
                legend_label=legend,
            )
        pxs, pys = glyphs["points"]
        source = ColumnDataSource(
            {
                "c_rate": pxs,
                "efficiency_pct": [100.0 * y for y in pys],
                "power_w": [c * capacity_wh for c in pxs] if capacity_wh else [None] * len(pxs),
                "label": [label] * len(pxs),
            }
        )
        point_renderers.append(
            plot.scatter(
                "c_rate", "efficiency_pct", source=source, size=8, color=color, legend_label=legend
            )
        )
    plot.add_tools(HoverTool(renderers=point_renderers, tooltips=tooltips))
    plot.legend.location = "bottom_left"
    plot.legend.click_policy = "hide"
    plot.toolbar.autohide = True
    bokey_apply_theme_to_plot(plot, dark)
    if dark:
        plot.legend.background_fill_color = DARK_BACKGROUND
        plot.legend.label_text_color = "white"
    return ProfileSection(
        "Efficiency",
        Bokeh(plot),
        P(
            "Dashed: C-rates outside the curve, clamped to its end value. Hover over a "
            "breakpoint for its values"
            + (f" (power for {capacity_wh} Wh)." if capacity_wh else "."),
            cls=HINT_CLS,
        ),
        *notes,
        open=open,
        data_profile_section=SECTION_EFFICIENCY,
    )


def _clamped_segments(
    xs: list[float], ys: list[float], x_min: float, x_max: float
) -> list[tuple[list[float], list[float]]]:
    """Dashed extensions of a clamped curve to the left and right plot edges."""
    segments: list[tuple[list[float], list[float]]] = []
    if x_min < xs[0]:
        segments.append(([x_min, xs[0]], [ys[0], ys[0]]))
    if x_max > xs[-1]:
        segments.append(([xs[-1], x_max], [ys[-1], ys[-1]]))
    return segments


def _style_legend(plot: Any, dark: bool) -> None:
    plot.legend.location = "bottom_left"
    plot.legend.click_policy = "hide"
    plot.toolbar.autohide = True
    bokey_apply_theme_to_plot(plot, dark)
    if dark:
        plot.legend.background_fill_color = DARK_BACKGROUND
        plot.legend.label_text_color = "white"


def _derating_fraction(document: dict[str, Any], temperature_degC: float) -> Optional[float]:
    """Allowed power fraction of an inverter document at a temperature, None if invalid."""
    raw = document.get("power_derating")
    if raw is None:
        return 1.0
    try:
        return PowerDeratingCurve.model_validate(raw).to_curve().lookup(temperature_degC)
    except ValueError:
        return None


def InverterSummary(document: dict[str, Any]) -> Div:
    """Compact key data of an inverter profile document."""
    items: list[tuple[str, Any]] = []
    for key, label, unit in INVERTER_SUMMARY_FIELDS:
        value = document.get(key)
        text = ("yes" if value else "no") if isinstance(value, bool) else _format_value(value, unit)
        items.append((label, text))
    fraction = _derating_fraction(document, INVERTER_DEFAULT_TEMPERATURE_DEGC)
    items.append(
        (
            f"Power at {INVERTER_DEFAULT_TEMPERATURE_DEGC:g} degC",
            "-" if fraction is None else f"{100.0 * fraction:.0f} %",
        )
    )
    return _summary_strip(items)


def InverterEfficiencyPlot(document: dict[str, Any], dark: bool = False, open: bool = True) -> Any:
    """Bokeh plot of the DC→AC and AC→DC efficiency curves of an inverter profile.

    Invalid curves (e.g. while editing) are reported instead of drawn.
    """
    curves: list[tuple[str, str, InverterEfficiencyCurve]] = []
    notes: list[Any] = []
    for key, label, color in INVERTER_EFFICIENCY_CURVES:
        raw = document.get(key)
        if raw is None:
            notes.append(Error(f"The {label} efficiency curve is missing."))
            continue
        try:
            curves.append((label, color, InverterEfficiencyCurve.model_validate(raw)))
        except ValueError as exc:
            notes.append(Error(f"Invalid {label} efficiency curve: {exc}"))
    if not curves:
        return ProfileSection(
            "Efficiency", *notes, open=open, data_profile_section=SECTION_EFFICIENCY
        )

    x_max = max(1.0, max(curve.load_fraction[-1] for _, _, curve in curves)) * (
        1.0 + EFFICIENCY_PLOT_MARGIN
    )
    nominal_power_w = document.get("nominal_power_w")
    tooltips = [
        ("", "@label"),
        ("load fraction", "@load_fraction{0.00}"),
        ("efficiency", "@efficiency_pct{0.0} %"),
    ]
    if nominal_power_w:
        tooltips.insert(2, ("AC power", "@power_w{0} W"))
    plot = create_figure(
        title="Inverter efficiency versus load fraction (as evaluated by EOS)",
        x_axis_label="Load fraction (AC power / nominal power)",
        y_axis_label="Efficiency [%]",
        x_range=(0.0, x_max),
        sizing_mode="stretch_width",
        height=CURVE_PLOT_HEIGHT_PX,
        tools="pan,wheel_zoom,box_zoom,reset,save",
    )
    point_renderers: list[DataRenderer] = []
    for label, color, curve in curves:
        xs = list(curve.load_fraction)
        ys = [100.0 * y for y in curve.efficiency]
        plot.line(xs, ys, color=color, line_width=2, legend_label=label)
        for seg_x, seg_y in _clamped_segments(xs, ys, 0.0, x_max):
            plot.line(
                seg_x,
                seg_y,
                color=color,
                line_width=2,
                line_dash="dashed",
                line_alpha=0.6,
                legend_label=label,
            )
        source = ColumnDataSource(
            {
                "load_fraction": xs,
                "efficiency_pct": ys,
                "power_w": [x * nominal_power_w for x in xs]
                if nominal_power_w
                else [None] * len(xs),
                "label": [label] * len(xs),
            }
        )
        point_renderers.append(
            plot.scatter(
                "load_fraction",
                "efficiency_pct",
                source=source,
                size=8,
                color=color,
                legend_label=label,
            )
        )
    plot.add_tools(HoverTool(renderers=point_renderers, tooltips=tooltips))
    _style_legend(plot, dark)
    return ProfileSection(
        "Efficiency",
        Bokeh(plot),
        P(
            "Linear between the breakpoints. Dashed: load fractions outside the curve, "
            "clamped to its end value.",
            cls=HINT_CLS,
        ),
        *notes,
        open=open,
        data_profile_section=SECTION_EFFICIENCY,
    )


def DeratingPlot(document: dict[str, Any], dark: bool = False, open: bool = True) -> Any:
    """Bokeh plot of the power derating of an inverter profile over the temperature."""
    raw = document.get("power_derating")
    if raw is None:
        return ProfileSection(
            "Power derating",
            P("No power derating: the full nominal power applies at every temperature."),
            open=open,
            data_profile_section=SECTION_DERATING,
        )
    try:
        derating = PowerDeratingCurve.model_validate(raw)
    except ValueError as exc:
        return ProfileSection(
            "Power derating",
            Error(f"Invalid power derating: {exc}"),
            open=open,
            data_profile_section=SECTION_DERATING,
        )

    xs = list(derating.temperature_degC)
    ys = [100.0 * y for y in derating.power_fraction]
    x_min = min(xs[0], INVERTER_DEFAULT_TEMPERATURE_DEGC) - 10.0
    x_max = max(xs[-1], INVERTER_DEFAULT_TEMPERATURE_DEGC) + 10.0
    plot = create_figure(
        title="Allowed power versus ambient temperature (as evaluated by EOS)",
        x_axis_label="Ambient temperature [degC]",
        y_axis_label="Allowed power [% of nominal]",
        x_range=(x_min, x_max),
        y_range=(0.0, 105.0),
        sizing_mode="stretch_width",
        height=CURVE_PLOT_HEIGHT_PX,
        tools="pan,wheel_zoom,box_zoom,reset,save",
    )
    color = "#1a9641"
    plot.line(xs, ys, color=color, line_width=2, legend_label="Derating")
    for seg_x, seg_y in _clamped_segments(xs, ys, x_min, x_max):
        plot.line(
            seg_x,
            seg_y,
            color=color,
            line_width=2,
            line_dash="dashed",
            line_alpha=0.6,
            legend_label="Derating",
        )
    source = ColumnDataSource({"temperature": xs, "power_pct": ys})
    points = plot.scatter("temperature", "power_pct", source=source, size=8, color=color)
    plot.add_tools(
        HoverTool(
            renderers=[points],
            tooltips=[("temperature", "@temperature{0.0} degC"), ("power", "@power_pct{0} %")],
        )
    )
    plot.add_layout(
        Span(
            location=INVERTER_DEFAULT_TEMPERATURE_DEGC,
            dimension="height",
            line_color="gray",
            line_dash="dotted",
            line_width=2,
        )
    )
    _style_legend(plot, dark)
    return ProfileSection(
        "Power derating",
        Bokeh(plot),
        P(
            f"Dotted: {INVERTER_DEFAULT_TEMPERATURE_DEGC:g} degC, the temperature EOS assumes "
            "without a measurement of '<device_id>-temperature-degc'. Dashed: temperatures "
            "outside the curve, clamped to its end value.",
            cls=HINT_CLS,
        ),
        open=open,
        data_profile_section=SECTION_DERATING,
    )


def _hx_vals(action: str, kind: Optional[str] = None) -> str:
    """Htmx values of a page action.

    Besides the action: the profile kind, the dark mode of the browser, and the
    sections that are open now, so the page keeps them open or closed when it
    is replaced. A section that is no ``details`` element (the editor) counts
    as open while it is on the page.
    """
    kind_value = f'"kind": "{kind}", ' if kind is not None else ""
    return (
        f'js:{{ "action": "{action}", {kind_value}'
        '"dark": window.matchMedia("(prefers-color-scheme: dark)").matches, '
        '"open_sections": Array.from(document.querySelectorAll('
        '"#page-content [data-profile-section]"))'
        '.filter(d => d.tagName !== "DETAILS" || d.open)'
        '.map(d => d.dataset.profileSection).join(",") }'
    )


# Actions that use the editor: it stays open after them.
EDITOR_ACTIONS = ("edit", "preview", "save", "save_as")


def editor_open(data: dict[str, Any]) -> bool:
    """Return whether the page shows the editor (edit mode).

    Edit mode starts with the Edit button and ends with Close; it is kept across
    the other actions, e.g. a profile change, through the reported open
    sections.

    Args:
        data: Values of the page request.
    """
    action = data.get("action")
    if action == "close":
        return False
    return action in EDITOR_ACTIONS or section_open(data, SECTION_EDITOR)


def EditButton(kind: str, editing: bool) -> Any:
    """Toolbar button that opens or closes the editor of the selected profile."""
    return ConfigButton(
        "Close editor" if editing else "Edit",
        hx_post=request_url_for(PAGE_PATH),
        hx_target="#page-content",
        hx_swap="innerHTML",
        hx_include=f"#{PROFILE_SELECT_ID}",
        hx_vals=_hx_vals("close" if editing else "edit", kind),
    )


def section_open(data: dict[str, Any], section: str) -> bool:
    """Return whether a section of the page is open.

    As the user left it if the request reports the open sections, otherwise
    the default of the first load.

    Args:
        data: Values of the page request.
        section: Section id, e.g. ``SECTION_LIMITS``.
    """
    if "open_sections" not in data:
        return SECTION_DEFAULT_OPEN[section]
    return section in str(data["open_sections"]).split(",")


def ProfileEditor(
    kind: str,
    name: str,
    entry: Optional[dict[str, Any]],
    document_text: str,
) -> Div:
    """JSON editor with a compact action bar, shown in edit mode only.

    The text area takes 40 % of the window height, so a plot below stays
    visible while editing; it can be resized. Without edit mode the page has
    no editor at all, not even a collapsed line.

    Args:
        kind: Profile kind, e.g. ``"battery"``.
        name: Profile name.
        entry: List entry of the profile, None if it is not stored.
        document_text: Text of the editor.
    """
    is_user = entry is not None and entry["source"] == "user"
    overrides_packaged = entry is not None and entry["packaged"]
    if entry is None or is_user:
        save_hint = f"Save to the user profile '{name}'."
    else:
        save_hint = (
            f"'{name}' is packaged with EOS and read only: saving stores a user profile of the "
            "same name that overrides it."
        )
    delete_label = "Revert to packaged" if overrides_packaged else "Delete"

    def action_button(label: str, action: str, **kwargs: Any) -> Any:
        return ConfigButton(
            label,
            hx_post=request_url_for(PAGE_PATH),
            hx_target="#page-content",
            hx_swap="innerHTML",
            hx_include=f"#{EDITOR_FORM_ID}",
            hx_vals=_hx_vals(action, kind),
            **kwargs,
        )

    buttons: list[Any] = [action_button("Preview", "preview"), action_button("Save", "save")]
    if is_user:
        buttons.append(
            action_button(
                delete_label,
                "delete",
                hx_confirm=(
                    f"Revert '{name}' to the packaged profile?"
                    if overrides_packaged
                    else f"Delete the {PROFILE_KINDS[kind][0].lower()} profile '{name}'?"
                ),
            )
        )
    return Div(
        H4(f"Edit '{name}'", cls="text-base"),
        P(
            f"{save_hint} Preview updates the summary and plots without saving; saved "
            "profiles apply at once, also to running device controllers.",
            cls=HINT_CLS,
        ),
        Form(
            Input(type="hidden", name="profile_name", value=name),
            TextArea(
                document_text,
                name="document",
                spellcheck="false",
                cls="font-mono text-xs w-full",
                style="height: 40vh; min-height: 12rem; resize: vertical; tab-size: 2;",
            ),
            DivHStacked(
                *buttons,
                Input(
                    name="new_name",
                    placeholder="new profile name",
                    cls="uk-input w-64",
                ),
                action_button("Save as", "save_as"),
                action_button("Close", "close"),
                cls="flex flex-wrap items-center gap-2",
            ),
            id=EDITOR_FORM_ID,
            cls="space-y-2",
        ),
        cls="border rounded-md px-3 py-2 space-y-2",
        data_profile_section=SECTION_EDITOR,
    )


def share_issue_url(kind: str, name: str, document: dict[str, Any]) -> tuple[str, bool]:
    """Return the URL of a new GitHub issue that shares a profile.

    The issue uses the profile form of the kind in the EOS repository (see
    ``SHARE_ISSUE_TEMPLATE``); the URL fills in its fields. The profile document is indented for the reviewers, compact
    if that is too long, and left out if the URL would still get longer than
    ``SHARE_URL_MAX_LENGTH``.

    Args:
        kind: Profile kind, e.g. ``"battery"``.
        name: Profile name.
        document: Profile document.

    Returns:
        ``(url, includes_document)``.
    """
    model_key = f"{kind}_model"
    model = str(document.get(model_key) or name)
    params = {
        "template": SHARE_ISSUE_TEMPLATE.format(kind=kind),
        "title": f"[PROFILE]: {model}",
        model_key: model,
        "profile_name": name,
    }
    for text in (
        json.dumps(document, indent=1, ensure_ascii=False),
        json.dumps(document, separators=(",", ":"), ensure_ascii=False),
    ):
        url = f"{SHARE_ISSUE_URL}?{urlencode({**params, 'profile': text})}"
        if len(url) <= SHARE_URL_MAX_LENGTH:
            return url, True
    return f"{SHARE_ISSUE_URL}?{urlencode(params)}", False


def ProfileJson(name: str, document: dict[str, Any], preview: bool, open: bool = False) -> Details:
    """The profile document as highlighted JSON, in a collapsible section.

    Shows the same document as the summary and the plots: the stored profile,
    or the edited text during a preview. The copy button comes with the
    highlight.js setup of EOSdash, as for the JSON values of the configuration.
    """
    title = (
        f"Preview of '{name}' as JSON (not saved)"
        if preview
        else f"Stored profile '{name}' as JSON"
    )
    return ProfileSection(title, JsonView(document), open=open, data_profile_section=SECTION_JSON)


def ShareButton(kind: str, name: str, document: dict[str, Any], preview: bool) -> Div:
    """Link that shares the shown profile as a GitHub issue.

    Opens a new issue with the profile form of the kind in the browser, filled
    in with the document; nothing is sent by EOS. The hint is the tooltip of
    the link; if the profile is too large for the URL, it is also shown as
    text, because the user has to paste the profile by hand.

    Returns:
        The link, followed by the visible hint if the profile has to be pasted.
    """
    url, includes_document = share_issue_url(kind, name, document)
    if not includes_document:
        hint = (
            "The profile is too large to include automatically. Copy it from the JSON view "
            "and paste it into the GitHub issue."
        )
    elif preview:
        hint = "Opens a GitHub issue containing the previewed, unsaved profile."
    else:
        hint = "Opens a GitHub issue containing this profile."
    link = A(
        "Share",
        href=url,
        target="_blank",
        rel="noopener noreferrer",
        title=hint,
        aria_label=f"Share on GitHub. {hint}",
        # Same size as the Edit button next to it (ConfigButton).
        cls=f"px-4 py-2 rounded {ButtonT.primary}",
    )
    if includes_document:
        return Div(link, cls="flex items-center")
    return Div(link, P(hint, cls=HINT_CLS), cls="flex flex-wrap items-center gap-2")


def profile_name_error(name: str) -> Optional[str]:
    """Return why a profile name is invalid, None if it is valid.

    Checks the rule of the profile store before a request is sent, so a name
    like 'vendor/model' gets a clear message instead of a path EOS does not route.
    """
    if name in RESERVED_PROFILE_NAMES:
        return f"The profile name '{name}' is reserved."
    if not PROFILE_NAME_PATTERN.fullmatch(name):
        return (
            f"Invalid profile name '{name}': use letters, digits, '.', '_' and '-', "
            "starting with a letter or digit (at most 100 characters)."
        )
    return None


def _apply_action(
    server: str, kind: str, data: dict[str, Any]
) -> tuple[Optional[Any], Optional[str], Optional[str]]:
    """Run the requested action against EOS.

    Returns:
        ``(status, selected, document_text)``: the status alert (or None), the
        profile to select (or None for the default) and the editor text to show
        instead of the stored document (kept after a failed save, so edits are
        not lost).
    """
    action = data.get("action")
    name = str(data.get("profile_name") or "").strip()
    text = str(data.get("document") or "")
    label, _, document_type = PROFILE_KINDS[kind]
    label = label.lower()

    if action == "kind":
        return None, None, None

    if action == "select":
        return None, name or None, None

    if action == "preview":
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            return Error(f"The document is not valid JSON: {exc}"), name or None, text
        if not isinstance(document, dict):
            return Error("The document must be a JSON object."), name or None, text
        try:
            document_type.model_validate(document)
        except ValueError as exc:
            return (
                Error(f"Preview of unsaved changes to '{name}', the document is invalid: {exc}"),
                name or None,
                text,
            )
        return (
            Info(f"Preview of unsaved changes to '{name}'. Save to apply them."),
            name or None,
            text,
        )

    if action in ("save", "save_as"):
        target = name if action == "save" else str(data.get("new_name") or "").strip()
        if not target:
            return Error("Enter a name for the new profile."), name or None, text
        name_error = profile_name_error(target)
        if name_error:
            return Error(name_error), name or None, text
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            return Error(f"The document is not valid JSON: {exc}"), name or None, text
        try:
            _request("put", _profile_url(server, kind, target), json=document)
        except ProfileApiError as exc:
            return (
                Error(f"{label.capitalize()} profile '{target}' not saved: {exc}"),
                name or None,
                text,
            )
        return Success(f"Saved {label} profile '{target}'."), target, None

    if action == "delete":
        try:
            restored = _request("delete", _profile_url(server, kind, name))
        except ProfileApiError as exc:
            return (
                Error(f"{label.capitalize()} profile '{name}' not deleted: {exc}"),
                name or None,
                None,
            )
        if restored:
            return Success(f"Reverted '{name}' to the packaged profile."), name, None
        return Success(f"Deleted {label} profile '{name}'."), None, None

    return None, name or None, None


def Profile(eos_host: str, eos_port: Union[str, int], data: Optional[dict] = None) -> Div:
    """Generate the device profile page.

    Args:
        eos_host: Host name of the EOS server.
        eos_port: Port of the EOS server.
        data: Action of the page: ``kind`` (``battery``, ``inverter``),
            ``action`` (``kind``, ``select``, ``preview``, ``save``, ``save_as``,
            ``delete``), ``profile_name``, ``new_name``, ``document`` (JSON) and
            ``dark`` (``"true"`` for the dark theme of the plots).

    Returns:
        The page content.
    """
    server = f"http://{eos_host}:{eos_port}"
    data = data or {}
    dark = str(data.get("dark", "")).lower() == "true"
    kind = str(data.get("kind") or DEFAULT_KIND)
    if kind not in PROFILE_KINDS:
        kind = DEFAULT_KIND
    label, default_profile, _ = PROFILE_KINDS[kind]
    status, selected, document_text = _apply_action(server, kind, data)

    kind_selection = Div(
        Select(
            *(
                Option(f"{kind_label} profiles", value=key, selected=key == kind)
                for key, (kind_label, _, _) in PROFILE_KINDS.items()
            ),
            name="kind",
            hx_post=request_url_for(PAGE_PATH),
            hx_target="#page-content",
            hx_swap="innerHTML",
            hx_trigger="change",
            hx_vals=_hx_vals("kind"),
            cls="border rounded px-3 py-2",
            title="Select profile kind",
            aria_label="Profile kind",
        ),
        cls="flex items-center",
    )

    try:
        entries: list[dict[str, Any]] = _request("get", _profile_url(server, kind))
    except ProfileApiError as exc:
        message = f"Can not retrieve {label.lower()} profiles from {server}: {exc}"
        logger.warning(message)
        return Div(
            Div(
                kind_selection,
                Div(status, cls="min-w-0 flex-1") if status is not None else None,
                cls="flex flex-wrap items-center gap-2",
            ),
            Error(message),
            cls="space-y-3",
        )

    names = [entry["name"] for entry in entries]
    if selected not in names:
        if document_text is None or not selected:
            selected = default_profile if default_profile in names else (names or [None])[0]
    entry = next((e for e in entries if e["name"] == selected), None)

    document: dict[str, Any] = {}
    load_error = None
    if selected is not None and entry is not None:
        try:
            document = _request("get", _profile_url(server, kind, selected))
        except ProfileApiError as exc:
            load_error = Error(f"Can not load {label.lower()} profile '{selected}': {exc}")
    preview = False
    if document_text is None:
        document_text = json.dumps(document, indent=2) if document else ""
    else:
        # Show the preview of the edited text if it is a JSON object.
        try:
            edited = json.loads(document_text)
        except json.JSONDecodeError:
            edited = None
        if isinstance(edited, dict):
            document = edited
            preview = True

    editing = selected is not None and editor_open(data)
    selection = Div(
        Select(
            *(
                Option(_entry_label(e), value=e["name"], selected=e["name"] == selected)
                for e in entries
            ),
            name="profile_name",
            id=PROFILE_SELECT_ID,
            hx_post=request_url_for(PAGE_PATH),
            hx_target="#page-content",
            hx_swap="innerHTML",
            hx_trigger="change",
            hx_vals=_hx_vals("select", kind),
            cls="border rounded px-3 py-2 min-w-64",
            title="Select profile",
            aria_label=f"{label} profile",
        ),
        EditButton(kind, editing) if selected else None,
        ShareButton(kind, selected, document, preview) if selected and document else None,
        cls="flex flex-wrap items-center gap-2",
    )

    # One toolbar: the selections name the profile kind, the status of the last
    # action follows the share button and takes the rest of the row.
    content: list[Any] = [
        Div(
            kind_selection,
            selection,
            Div(status, cls="min-w-0 flex-1") if status is not None else None,
            cls="flex flex-wrap items-center gap-2",
        ),
        load_error,
    ]
    if selected is None:
        content.append(P(f"No {label.lower()} profiles available."))
    else:
        summary = InverterSummary if kind == "inverter" else ProfileSummary
        content.append(summary(document) if document else None)
        if editing:
            content.append(ProfileEditor(kind, selected, entry, document_text))
        if document:
            efficiency_open = section_open(data, SECTION_EFFICIENCY)
            if kind == "inverter":
                content.append(InverterEfficiencyPlot(document, dark, open=efficiency_open))
                content.append(
                    DeratingPlot(document, dark, open=section_open(data, SECTION_DERATING))
                )
            else:
                content.append(LimitMaps(document, dark, open=section_open(data, SECTION_LIMITS)))
                content.append(EfficiencyPlot(document, dark, open=efficiency_open))
            content.append(
                ProfileJson(selected, document, preview, open=section_open(data, SECTION_JSON))
            )
    return Div(*(c for c in content if c is not None), cls="space-y-3")
