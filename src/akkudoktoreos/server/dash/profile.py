"""Battery profile editor for EOSdash.

Lists the battery profiles of the EOS profile store and lets the user view,
edit, save, copy and delete them. All access goes through the REST API of EOS
(``/v1/profile/battery``); EOS validates every document before it is saved.

Packaged profiles (shipped with EOS) are read only. Saving one stores a user
profile of the same name that overrides it; deleting that user profile reverts
to the packaged one. Saved profiles take effect at once in EOS, also for
running device controllers.

The document is edited as JSON. Bokeh plots show the loaded document:

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
"""

import json
from typing import Any, Optional, Union
from urllib.parse import quote

import numpy as np
import requests
from bokeh.models import ColumnDataSource, HoverTool
from bokeh.models import Image as ImageGlyph
from bokeh.models import LinearColorMapper
from bokeh.models.annotations import ColorBar
from bokeh.models.renderers import DataRenderer
from bokeh.palettes import Viridis256
from fasthtml.common import Option, Select
from loguru import logger
from monsterui.franken import (
    H3,
    H4,
    Card,
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
from akkudoktoreos.server.dash.bokeh import (
    Bokeh,
    bokey_apply_theme_to_plot,
    create_figure,
)
from akkudoktoreos.server.dash.components import ConfigButton, Error, Success
from akkudoktoreos.server.dash.context import request_url_for

PROFILE_KIND = "battery"
DEFAULT_PROFILE = "generic-lfp"
PAGE_PATH = "/eosdash/profile"
EDITOR_FORM_ID = "battery-profile-editor"
REQUEST_TIMEOUT_S = 10

# Efficiency curves of a profile document: (document key, label, color).
EFFICIENCY_CURVES: tuple[tuple[str, str, str], ...] = (
    ("charge_efficiency", "Charge", "#2b83ba"),
    ("discharge_efficiency", "Discharge", "#d7191c"),
)
# Background of plots and their annotations in dark mode (as bokey_apply_theme_to_plot).
DARK_BACKGROUND = "#1e1e1e"
# The plot extends this much beyond the last breakpoint, to show the out-of-range behavior.
EFFICIENCY_PLOT_MARGIN = 0.25

# Fields shown in the summary card: (document key, label, unit).
SUMMARY_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("battery_model", "Model", ""),
    ("chemistry", "Chemistry", ""),
    ("nominal_capacity_wh", "Nominal capacity", "Wh"),
    ("nominal_voltage_v", "Nominal voltage", "V"),
    ("max_charge_power_w", "Max. charge power", "W"),
    ("max_discharge_power_w", "Max. discharge power", "W"),
    ("technical_min_soc_percent", "Technical min. SoC", "%"),
    ("technical_max_soc_percent", "Technical max. SoC", "%"),
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


def _profile_url(server: str, name: Optional[str] = None) -> str:
    url = f"{server}/v1/profile/{PROFILE_KIND}"
    return url if name is None else f"{url}/{quote(name, safe='/')}"


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


def ProfileSummary(document: dict[str, Any]) -> Card:
    """Key data of a battery profile document."""
    cutoffs = document.get("hard_cutoffs") or {}
    rows = [
        Tr(Td(label), Td(_format_value(document.get(key), unit)))
        for key, label, unit in SUMMARY_FIELDS
    ]
    rows.append(
        Tr(
            Td("Charge allowed"),
            Td(
                f"{_format_value(cutoffs.get('charge_allowed_below_degC'), 'degC')} .. "
                f"{_format_value(cutoffs.get('charge_allowed_above_degC'), 'degC')}"
            ),
        )
    )
    rows.append(
        Tr(
            Td("Discharge allowed"),
            Td(
                f"{_format_value(cutoffs.get('discharge_allowed_below_degC'), 'degC')} .. "
                f"{_format_value(cutoffs.get('discharge_allowed_above_degC'), 'degC')}"
            ),
        )
    )
    return Card(H4("Summary"), Table(Tbody(*rows)))


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
LIMIT_MAPS: tuple[tuple[str, str, str], ...] = (
    ("charge_map", "charge_limit_crate", "Charge limit"),
    ("discharge_map", "discharge_limit_crate", "Discharge limit"),
)


def limit_heatmap_image(profile: BatteryProfile, attribute: str) -> dict[str, Any]:
    """Map a compiled limit map to the parameters of a Bokeh ``image`` glyph.

    The compiled map is the lookup table EOS uses: a query is rounded to the
    nearest cell (``PrecomputedCargeDischargeLimitMap.lookup``). Each cell is
    therefore drawn centered on its grid point, ``t_step`` x ``s_step`` wide.

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
        height=400,
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


def LimitMaps(document: dict[str, Any], dark: bool = False) -> Any:
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
        return Card(
            H4("Charge and discharge limits"),
            Error(f"The limit maps can not be compiled: {exc}"),
            tables,
        )
    grids = [
        np.asarray(getattr(profile, attribute).grid, dtype=float) for attribute, _, _ in LIMIT_MAPS
    ]
    finite = [grid[np.isfinite(grid)] for grid in grids]
    high = max((float(values.max()) for values in finite if values.size), default=1.0) or 1.0
    return Card(
        H4("Charge and discharge limits"),
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
            cls="text-sm opacity-70",
        ),
        tables,
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


def EfficiencyPlot(document: dict[str, Any], dark: bool = False) -> Any:
    """Bokeh plot of the charge and discharge efficiency curves of a profile document.

    Curves the document does not have are noted below the plot (the efficiency of
    the battery settings applies then); invalid curves (e.g. while editing) are
    reported instead of drawn.
    """
    curves: list[tuple[str, str, EfficiencyCurve]] = []
    notes: list[Any] = []
    for key, label, color in EFFICIENCY_CURVES:
        raw = document.get(key)
        if raw is None:
            notes.append(
                P(
                    f"No {label.lower()} efficiency curve: the "
                    f"{label.lower()} efficiency of the battery settings applies.",
                    cls="text-sm opacity-70",
                )
            )
            continue
        try:
            curves.append((label, color, EfficiencyCurve.model_validate(raw)))
        except ValueError as exc:
            notes.append(Error(f"Invalid {label.lower()} efficiency curve: {exc}"))
    if not curves:
        return Card(H4("Efficiency"), *notes)

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
        height=350,
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
    return Div(
        Bokeh(plot, header="Efficiency"),
        P(
            "Dashed: C-rates outside the curve, clamped to its end value. Hover over a "
            "breakpoint for its values"
            + (f" (power for {capacity_wh} Wh)." if capacity_wh else "."),
            cls="text-sm opacity-70",
        ),
        *notes,
        cls="space-y-2",
    )


def _hx_vals(action: str) -> str:
    """Htmx values of a page action, with the dark mode of the browser."""
    return (
        f'js:{{ "action": "{action}", '
        '"dark": window.matchMedia("(prefers-color-scheme: dark)").matches }'
    )


def ProfileEditor(name: str, entry: Optional[dict[str, Any]], document_text: str) -> Card:
    """JSON editor of a profile with save, save-as and delete actions."""
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
            hx_vals=_hx_vals(action),
            **kwargs,
        )

    buttons: list[Any] = [action_button("Save", "save")]
    if is_user:
        buttons.append(
            action_button(
                delete_label,
                "delete",
                hx_confirm=(
                    f"Revert '{name}' to the packaged profile?"
                    if overrides_packaged
                    else f"Delete the battery profile '{name}'?"
                ),
            )
        )
    return Card(
        H4(f"Edit '{name}'"),
        P(save_hint, cls="text-sm opacity-70"),
        Form(
            Input(type="hidden", name="profile_name", value=name),
            TextArea(
                document_text,
                name="document",
                rows=30,
                spellcheck="false",
                cls="font-mono text-xs w-full",
            ),
            DivHStacked(
                *buttons,
                Input(
                    name="new_name",
                    placeholder="new profile name",
                    cls="uk-input w-64",
                ),
                action_button("Save as", "save_as"),
                cls="flex-wrap gap-2",
            ),
            id=EDITOR_FORM_ID,
            cls="space-y-2",
        ),
    )


def _apply_action(
    server: str, data: dict[str, Any]
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

    if action == "select":
        return None, name or None, None

    if action in ("save", "save_as"):
        target = name if action == "save" else str(data.get("new_name") or "").strip()
        if not target:
            return Error("Enter a name for the new profile."), name or None, text
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            return Error(f"The document is not valid JSON: {exc}"), name or None, text
        try:
            _request("put", _profile_url(server, target), json=document)
        except ProfileApiError as exc:
            return Error(f"Battery profile '{target}' not saved: {exc}"), name or None, text
        return Success(f"Saved battery profile '{target}'."), target, None

    if action == "delete":
        try:
            restored = _request("delete", _profile_url(server, name))
        except ProfileApiError as exc:
            return Error(f"Battery profile '{name}' not deleted: {exc}"), name or None, None
        if restored:
            return Success(f"Reverted '{name}' to the packaged profile."), name, None
        return Success(f"Deleted battery profile '{name}'."), None, None

    return None, name or None, None


def Profile(eos_host: str, eos_port: Union[str, int], data: Optional[dict] = None) -> Div:
    """Generate the battery profile page.

    Args:
        eos_host: Host name of the EOS server.
        eos_port: Port of the EOS server.
        data: Action of the page: ``action`` (``select``, ``save``, ``save_as``,
            ``delete``), ``profile_name``, ``new_name``, ``document`` (JSON) and
            ``dark`` (``"true"`` for the dark theme of the plots).

    Returns:
        The page content.
    """
    server = f"http://{eos_host}:{eos_port}"
    dark = str((data or {}).get("dark", "")).lower() == "true"
    status, selected, document_text = _apply_action(server, data or {})

    try:
        entries: list[dict[str, Any]] = _request("get", _profile_url(server))
    except ProfileApiError as exc:
        message = f"Can not retrieve battery profiles from {server}: {exc}"
        logger.warning(message)
        return Div(status, Error(message), cls="space-y-4")

    names = [entry["name"] for entry in entries]
    if selected not in names:
        if document_text is None or not selected:
            selected = DEFAULT_PROFILE if DEFAULT_PROFILE in names else (names or [None])[0]
    entry = next((e for e in entries if e["name"] == selected), None)

    document: dict[str, Any] = {}
    load_error = None
    if selected is not None and entry is not None:
        try:
            document = _request("get", _profile_url(server, selected))
        except ProfileApiError as exc:
            load_error = Error(f"Can not load battery profile '{selected}': {exc}")
    if document_text is None:
        document_text = json.dumps(document, indent=2) if document else ""
    else:
        # Show the preview of the edited text if it is valid JSON.
        try:
            document = json.loads(document_text)
        except json.JSONDecodeError:
            pass

    selection = Card(
        H4("Battery profile"),
        Select(
            *(
                Option(_entry_label(e), value=e["name"], selected=e["name"] == selected)
                for e in entries
            ),
            name="profile_name",
            hx_post=request_url_for(PAGE_PATH),
            hx_target="#page-content",
            hx_swap="innerHTML",
            hx_trigger="change",
            hx_vals=_hx_vals("select"),
            cls="border rounded px-3 py-2",
        ),
        P(
            "Packaged profiles are shipped with EOS and read only. Saved profiles apply at "
            "once, also to running device controllers.",
            cls="text-sm opacity-70",
        ),
    )

    content: list[Any] = [H3("Battery profiles"), status, load_error, selection]
    if selected is None:
        content.append(P("No battery profiles available."))
    else:
        content.append(ProfileSummary(document) if document else None)
        content.append(ProfileEditor(selected, entry, document_text))
        if document:
            content.append(LimitMaps(document, dark))
            content.append(EfficiencyPlot(document, dark))
    return Div(*(c for c in content if c is not None), cls="space-y-4")
