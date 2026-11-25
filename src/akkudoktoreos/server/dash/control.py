"""Control page of EOSdash: the status of one device controller.

The page shows one device controller at a time, selected from the configured controllers.
Everything comes from the REST API ``/v1/control`` of EOS:

- the state of the controller (running, start failed, stopped), its connection and error,
- the command it executes and its readings, reloaded every 5 s,
- its diagnostic graphs, reloaded every 30 s,
- the last raw events of its diagnostic log (collapsed, fetched while open, every 30 s),
- its settings (collapsed, read only).

The diagnostic graphs and events need the diagnostic log of the controller
(``diagnostic_log_capacity`` > 0); without it the page tells how to switch it on.

The reloaded parts are the contents of their sections: a section the user opened or closed
stays that way. Polling ends when another tab is opened, as the tab replaces the page content.
"""

import json
import math
from typing import Any, Optional, Union
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import requests
from bokeh.layouts import column
from bokeh.models import (
    ColumnDataSource,
    FixedTicker,
    HoverTool,
    LinearColorMapper,
    Plot,
    Range1d,
)
from bokeh.models.annotations import ColorBar
from bokeh.models.glyphs import Image
from bokeh.models.renderers import DataRenderer
from bokeh.palettes import Category10_10, Viridis256
from fasthtml.common import Option, Select, Table, Tbody, Td, Th, Thead, Tr
from loguru import logger
from monsterui.franken import Code, Div, P, Span

from akkudoktoreos.adapter.controllers.controldiagnostics import (
    DiagnosticGraph,
    DiagnosticVectorSeries,
    DiagnosticView,
)
from akkudoktoreos.server.dash.bokeh import (
    Bokeh,
    bokey_apply_theme_to_plot,
    create_figure,
)
from akkudoktoreos.server.dash.components import (
    Badge,
    ConfigSection,
    Error,
    Info,
    JsonView,
)
from akkudoktoreos.server.dash.context import request_url_for
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration

PAGE_PATH = "/eosdash/control"
STATUS_PATH = "/eosdash/control/status"
DIAGNOSTICS_PATH = "/eosdash/control/diagnostics"
EVENTS_PATH = "/eosdash/control/events"

STATUS_POLL = "every 5s"
DIAGNOSTICS_POLL = "every 30s"
# The events are fetched when their section is opened and polled only while it is open.
EVENTS_TRIGGER = (
    "toggle[this.closest('details').open] from:closest details, "
    "every 30s[this.closest('details').open]"
)

REQUEST_TIMEOUT_S = 10
DIAGNOSTICS_MAX_POINTS = 500
EVENTS_LIMIT = 100
EVENT_DATA_MAX_CHARS = 120
PLOT_HEIGHT_PX = 240
LANE_HEIGHT_PX = 36
# A heatmap column covers the time up to the next reading, but not more than this many typical
# reading intervals: a longer pause (EOS stopped, device offline) stays a visible gap.
HEATMAP_MAX_GAP_INTERVALS = 2.0
HEATMAP_SINGLE_COLUMN_MS = 60_000.0  # width of the column of the only reading
DEFAULT_TIMEZONE = "UTC"

HINT_CLS = "text-xs text-muted-foreground"

# Badge of a controller state: (icon, label, tone).
STATE_BADGES: dict[str, tuple[str, str, str]] = {
    "running": ("play", "running", "accent"),
    "start_failed": ("triangle-alert", "start failed", "warn"),
    "stopped": ("square", "stopped", "muted"),
}
STATE_LABELS = {state: label for state, (_, label, _) in STATE_BADGES.items()}

# Units of reading keys by suffix; longer suffixes first ("_kwh" before "_wh" before "_w").
READING_UNITS: tuple[tuple[str, str], ...] = (
    ("_kwh", "kWh"),
    ("_wh", "Wh"),
    ("_ms", "ms"),
    ("_percent", "%"),
    ("_degc", "°C"),
    ("_hz", "Hz"),
    ("_w", "W"),
    ("_s", "s"),
    ("_c", "°C"),
    ("_v", "V"),
    ("_a", "A"),
)


class ControlApiError(Exception):
    """EOS could not be reached or answered with an error."""


# ---------------------------------------------------------------------------
# REST access
# ---------------------------------------------------------------------------
def _detail(response: Any) -> str:
    """Return the error detail of an EOS response."""
    try:
        detail = response.json().get("detail", "")
    except Exception:
        detail = getattr(response, "text", "")
    return str(detail)


def _get(url: str, **params: Any) -> Any:
    """GET from EOS and return the decoded JSON body.

    Raises:
        ControlApiError: If EOS cannot be reached or answers with an error.
    """
    try:
        response = requests.get(url, params=params or None, timeout=REQUEST_TIMEOUT_S)
    except Exception as exc:
        raise ControlApiError(f"EOS server not reachable: {exc}") from exc
    if response.status_code >= 400:
        raise ControlApiError(f"{response.status_code}: {_detail(response)}")
    return response.json()


def _server(eos_host: str, eos_port: Union[str, int]) -> str:
    return f"http://{eos_host}:{eos_port}"


def _timezone(server: str) -> str:
    """Timezone of EOS for the time axes, UTC if it is not available."""
    try:
        timezone = _get(f"{server}/v1/config/general/timezone")
    except ControlApiError:
        return DEFAULT_TIMEZONE
    return timezone if isinstance(timezone, str) and timezone else DEFAULT_TIMEZONE


def _partial_url(path: str, device_id: str) -> str:
    return f"{request_url_for(path)}?{urlencode({'device_id': device_id})}"


def _dark_vals() -> str:
    return 'js:{ "dark": window.matchMedia("(prefers-color-scheme: dark)").matches }'


def _is_dark(data: Optional[dict]) -> bool:
    return str((data or {}).get("dark", "")).lower() == "true"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def reading_unit(key: str) -> str:
    """Unit of a reading from its key suffix, "" if the key carries none."""
    name = key.rsplit(":", 1)[-1].lower()
    for suffix, unit in READING_UNITS:
        if name.endswith(suffix):
            return unit
    return ""


def format_value(value: Any, unit: str = "") -> str:
    """Display text of a reading or command value."""
    if value is None:
        return "–"
    if isinstance(value, bool):
        text = "yes" if value else "no"
    elif isinstance(value, float):
        text = f"{value:.2f}".rstrip("0").rstrip(".") if abs(value) < 1e6 else f"{value:.4g}"
    elif isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return f"{text} {unit}" if unit else text


def format_time(value: Optional[str], timezone: str) -> str:
    """Local time of an ISO date time, "–" if there is none."""
    if not value:
        return "–"
    try:
        return to_datetime(value, in_timezone=timezone).format("YYYY-MM-DD HH:mm:ss")
    except Exception:
        return str(value)


def format_age(value: Optional[str]) -> Optional[str]:
    """Age of an ISO date time, e.g. "3 s ago", None if there is none."""
    if not value:
        return None
    try:
        seconds = max(0.0, (to_datetime() - to_datetime(value)).total_seconds())
    except Exception:
        return None
    if seconds < 120:
        return f"{seconds:.0f} s ago"
    if seconds < 7200:
        return f"{seconds / 60:.0f} min ago"
    return f"{seconds / 3600:.1f} h ago"


def format_event_data(data: dict[str, Any]) -> str:
    """Compact ``key=value`` text of event data, shortened to EVENT_DATA_MAX_CHARS."""
    text = ", ".join(f"{key}={format_value(value)}" for key, value in data.items())
    if len(text) > EVENT_DATA_MAX_CHARS:
        text = text[: EVENT_DATA_MAX_CHARS - 1] + "…"
    return text


def _item(label: str, value: Any) -> Div:
    """One compact label/value cell."""
    return Div(
        P(label, cls="text-xs text-muted-foreground leading-tight"),
        P(str(value), cls="text-sm font-medium leading-tight break-words"),
        cls="min-w-0 px-3 py-2 border -mt-px -ml-px",
    )


def _grid(items: list[tuple[str, Any]]) -> Div:
    """Wrapping grid of label/value cells with collapsed borders."""
    return Div(
        *(_item(label, value) for label, value in items),
        cls="grid grid-cols-[repeat(auto-fit,minmax(11rem,1fr))] pt-px pl-px",
    )


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
def StatusContent(status: dict[str, Any], timezone: str = DEFAULT_TIMEZONE) -> Div:
    """State, error, active command and readings of a controller."""
    state = str(status.get("state", "stopped"))
    icon, label, tone = STATE_BADGES.get(state, STATE_BADGES["stopped"])
    badges = [Badge(icon, label, tone)]
    if state == "running":
        badges.append(
            Badge("link", "connected", "accent")
            if status.get("connected")
            else Badge("unlink", "disconnected", "warn")
        )
    badges.append(Badge("cpu", str(status.get("controller_type", "")), "muted"))
    age = format_age(status.get("status_time"))
    header = Div(
        *badges,
        Span(f"updated {age}", cls=HINT_CLS) if age else None,
        cls="flex flex-wrap items-center gap-2",
    )

    parts: list[Any] = [header]
    if status.get("settings_pending"):
        parts.append(
            Info(
                "The configured settings differ from the ones this controller runs with. "
                "While the Control adapter is enabled, they are applied when the controller "
                "restarts: right after a setting is changed, otherwise at the next energy "
                "management run."
            )
        )
    if status.get("error"):
        title = "Start failed" if state == "start_failed" else "Error"
        parts.append(Error(f"{title}: {status['error']}"))

    command = status.get("active_command")
    if command:
        items: list[tuple[str, Any]] = [("Command", command.get("label") or "–")]
        items.extend(
            (key.replace("_", " ").capitalize(), format_value(value, reading_unit(key)))
            for key, value in (command.get("payload") or {}).items()
        )
        items.append(("Since", format_time(command.get("date_time"), timezone)))
        items.append(("Until", format_time(command.get("valid_until"), timezone)))
        parts.append(_grid(items))
    elif state == "running":
        parts.append(Info("No command: the device is in its safe state."))

    data: dict[str, Any] = status.get("data") or {}
    if data:
        parts.append(
            Div(
                Table(
                    Thead(Tr(Th("Reading"), Th("Value"))),
                    Tbody(
                        *(
                            Tr(Td(Code(key)), Td(format_value(value, reading_unit(key))))
                            for key, value in sorted(data.items())
                        )
                    ),
                    cls="text-sm",
                ),
                cls="overflow-x-auto",
            )
        )
    elif state == "running":
        parts.append(P("No readings yet.", cls=HINT_CLS))
    return Div(*parts, cls="space-y-3")


def StatusPartial(eos_host: str, eos_port: Union[str, int], data: Optional[dict] = None) -> Div:
    """Status content of one controller (polling target)."""
    server = _server(eos_host, eos_port)
    device_id = str((data or {}).get("device_id", ""))
    try:
        status = _get(f"{server}/v1/control/{device_id}/status")
    except ControlApiError as exc:
        return Error(f"Can not retrieve the status of controller '{device_id}': {exc}")
    return StatusContent(status, _timezone(server))


# ---------------------------------------------------------------------------
# Diagnostic graphs
# ---------------------------------------------------------------------------
def _local_index(series: pd.Series, timezone: str) -> pd.Series:
    """Series with a naive index in local time (Bokeh shows datetimes as given)."""
    index = pd.to_datetime(series.index, utc=True).tz_convert(timezone).tz_localize(None)
    return pd.Series(series.to_numpy(), index=index, name=series.name)


def _series_label(key: str) -> str:
    """Legend label of a series key ``"<event>.<field>"``: the field."""
    return key.split(".", 1)[-1]


def _series_labels(keys: list[str]) -> dict[str, str]:
    """Legend labels of the series of one graph: the field, the whole key if fields repeat.

    E.g. the same field of two towers (``bms1_info.cell_voltages_mv``,
    ``bms2_info.cell_voltages_mv``) in one graph keeps the event to tell them apart.
    """
    fields = [_series_label(key) for key in keys]
    return {key: field if fields.count(field) == 1 else key for key, field in zip(keys, fields)}


def _local_ms(index: pd.DatetimeIndex, timezone: str) -> np.ndarray:
    """Milliseconds of a timezone aware index in naive local time (the Bokeh datetime axis)."""
    local = index.tz_convert(timezone).tz_localize(None)
    return np.asarray((local - pd.Timestamp(0)) / pd.Timedelta(milliseconds=1), dtype=float)


def NumericGraph(
    view: DiagnosticView, graph: Any, timezone: str, dark: bool, x_range: Range1d
) -> Plot:
    """Line plot of the numeric series of one graph, on the shared time axis ``x_range``."""
    title = f"{graph.title} [{graph.unit}]" if graph.unit else graph.title
    plot = create_figure(
        title=title,
        x_range=x_range,
        x_axis_type="datetime",
        x_axis_label=f"Time [{timezone}]",
        y_axis_label=graph.unit or None,
        sizing_mode="stretch_width",
        height=PLOT_HEIGHT_PX,
        tools="pan,wheel_zoom,box_zoom,reset,save",
    )
    renderers: list[DataRenderer] = []
    labels = _series_labels([key for key in graph.series if key in view.series])
    for index, key in enumerate(graph.series):
        if key not in view.series:
            continue
        series = _local_index(view.get_series(key), timezone)
        source = ColumnDataSource(
            {
                "date_time": series.index,
                "value": series.to_numpy(dtype=float),
                "label": [labels[key]] * len(series),
            }
        )
        color = Category10_10[index % len(Category10_10)]
        renderers.append(
            plot.line(
                "date_time",
                "value",
                source=source,
                color=color,
                line_width=2,
                legend_label=labels[key],
            )
        )
    plot.add_tools(
        HoverTool(
            renderers=renderers,
            tooltips=[("", "@label"), ("time", "@date_time{%H:%M:%S}"), ("value", "@value")],
            formatters={"@date_time": "datetime"},
        )
    )
    if renderers:
        plot.legend.location = "top_left"
        plot.legend.click_policy = "hide"
    plot.toolbar.autohide = True
    bokey_apply_theme_to_plot(plot, dark)
    return plot


def StateGraph(
    view: DiagnosticView, graph: Any, timezone: str, dark: bool, x_range: Range1d
) -> Plot:
    """Lanes of the state series of one graph, on the shared time axis ``x_range``.

    A value holds until the next change; the last one until the end of ``x_range`` (now).
    """
    keys = [key for key in graph.series if key in view.series]
    labels = _series_labels(keys)
    plot = create_figure(
        title=graph.title,
        x_range=x_range,
        x_axis_type="datetime",
        x_axis_label=f"Time [{timezone}]",
        sizing_mode="stretch_width",
        height=max(PLOT_HEIGHT_PX // 2, LANE_HEIGHT_PX * (len(keys) + 2)),
        tools="pan,wheel_zoom,box_zoom,reset,save",
    )
    end = x_range.end
    colors: dict[str, str] = {}
    lanes: dict[str, Any] = {
        name: [] for name in ("lane", "left", "right", "value", "color", "label")
    }
    for lane, key in enumerate(keys):
        series = _local_index(view.get_series(key), timezone)
        times = list(series.index)
        for position, (start, value) in enumerate(zip(times, series.to_numpy())):
            text = format_value(value.item() if hasattr(value, "item") else value)
            stop = times[position + 1] if position + 1 < len(times) else max(end, start)
            color = colors.setdefault(text, Category10_10[len(colors) % len(Category10_10)])
            lanes["lane"].append(lane)
            lanes["left"].append(start)
            lanes["right"].append(stop)
            lanes["value"].append(text)
            lanes["color"].append(color)
            lanes["label"].append(labels[key])
    source = ColumnDataSource(lanes)
    renderer = plot.hbar(
        y="lane", left="left", right="right", height=0.7, color="color", source=source
    )
    plot.text(
        x="left",
        y="lane",
        text="value",
        source=source,
        text_baseline="middle",
        text_font_size="10px",
        x_offset=3,
        text_color="white",
    )
    plot.y_range = Range1d(start=-0.6, end=max(len(keys), 1) - 0.4)
    plot.yaxis.ticker = FixedTicker(ticks=list(range(len(keys))))
    plot.yaxis.major_label_overrides = {lane: labels[key] for lane, key in enumerate(keys)}
    plot.add_tools(
        HoverTool(
            renderers=[renderer],
            tooltips=[("", "@label"), ("value", "@value"), ("since", "@left{%H:%M:%S}")],
            formatters={"@left": "datetime"},
        )
    )
    plot.toolbar.autohide = True
    bokey_apply_theme_to_plot(plot, dark)
    return plot


def _graph_label(graph: DiagnosticGraph) -> str:
    return f"{graph.title} [{graph.unit}]" if graph.unit else graph.title


def ProfileGraph(view: DiagnosticView, graph: DiagnosticGraph, timezone: str, dark: bool) -> Plot:
    """The latest values of vector series over the element number, one line per series."""
    index_label = graph.index_label or "Number"
    plot = create_figure(
        title=_graph_label(graph),
        x_axis_label=index_label,
        y_axis_label=graph.unit or None,
        sizing_mode="stretch_width",
        height=PLOT_HEIGHT_PX,
        tools="pan,wheel_zoom,box_zoom,reset,save",
    )
    keys = [key for key in graph.series if key in view.vectors]
    labels = _series_labels(keys)
    renderers: list[DataRenderer] = []
    for position, key in enumerate(keys):
        vector = view.vectors[key]
        values = vector.latest()
        if not values:
            continue
        read_at = format_time(vector.date_time[-1].isoformat(), timezone)
        source = ColumnDataSource(
            {
                "number": list(range(1, len(values) + 1)),
                "value": [math.nan if value is None else value for value in values],
                "label": [labels[key]] * len(values),
                "read_at": [read_at] * len(values),
            }
        )
        color = Category10_10[position % len(Category10_10)]
        renderers.append(
            plot.line("number", "value", source=source, color=color, legend_label=labels[key])
        )
        plot.scatter("number", "value", source=source, color=color, size=4)
    plot.add_tools(
        HoverTool(
            renderers=renderers,
            tooltips=[
                ("", "@label"),
                (index_label.lower(), "@number"),
                ("value", "@value"),
                ("read", "@read_at"),
            ],
        )
    )
    if renderers:
        plot.legend.location = "top_left"
        plot.legend.click_policy = "hide"
    plot.toolbar.autohide = True
    bokey_apply_theme_to_plot(plot, dark)
    return plot


def _heatmap_columns(
    vector: DiagnosticVectorSeries, timezone: str
) -> tuple[list[np.ndarray], np.ndarray, np.ndarray]:
    """One image column per reading: the values, the local start time and the width (ms).

    A column reaches to the next reading, at most ``HEATMAP_MAX_GAP_INTERVALS`` typical
    (median) reading intervals.
    """
    frame = vector.to_dataframe()
    starts = _local_ms(pd.DatetimeIndex(frame.index), timezone)
    if len(starts) > 1:
        steps = np.diff(starts)
        widths = np.minimum(
            np.append(steps, np.median(steps)), HEATMAP_MAX_GAP_INTERVALS * np.median(steps)
        )
    else:
        widths = np.full(len(starts), HEATMAP_SINGLE_COLUMN_MS)
    images = [row.reshape(-1, 1) for row in frame.to_numpy(dtype=float)]
    return images, starts, widths


def HeatmapGraph(
    view: DiagnosticView, graph: DiagnosticGraph, timezone: str, dark: bool, x_range: Range1d
) -> Optional[Plot]:
    """A vector series over time on the shared time axis, colour for the value.

    Shows the first series of the graph that has points, None if none has.
    """
    key = next((k for k in graph.series if k in view.vectors and view.vectors[k].values), None)
    if key is None:
        return None
    images, starts, widths = _heatmap_columns(view.vectors[key], timezone)
    count = max(len(image) for image in images)
    values = np.concatenate([image.ravel() for image in images])
    low = float(np.nanmin(values)) if np.isfinite(values).any() else 0.0
    high = float(np.nanmax(values)) if np.isfinite(values).any() else 1.0
    if high <= low:  # one value only: give the colour scale some width
        low, high = low - 0.5, high + 0.5
    mapper = LinearColorMapper(palette=Viridis256, low=low, high=high, nan_color="rgba(0, 0, 0, 0)")
    index_label = graph.index_label or "Number"
    plot = create_figure(
        title=_graph_label(graph),
        x_range=x_range,
        y_range=Range1d(start=0.5, end=count + 0.5),
        x_axis_type="datetime",
        x_axis_label=f"Time [{timezone}]",
        y_axis_label=index_label,
        sizing_mode="stretch_width",
        height=PLOT_HEIGHT_PX,
        tools="pan,wheel_zoom,box_zoom,reset,save",
    )
    source = ColumnDataSource(
        {
            "image": images,
            "x": starts,
            "dw": widths,
            "y": [0.5] * len(images),
            "dh": [float(len(image)) for image in images],
        }
    )
    renderer = plot.add_glyph(
        source, Image(image="image", x="x", y="y", dw="dw", dh="dh", color_mapper=mapper)
    )
    plot.add_layout(ColorBar(color_mapper=mapper, title=graph.unit or None), "right")
    plot.add_tools(
        HoverTool(
            renderers=[renderer],
            tooltips=[
                ("time", "$x{%F %H:%M:%S}"),
                (index_label.lower(), "$y{0}"),
                ("value", "@image"),
            ],
            formatters={"$x": "datetime"},
        )
    )
    plot.toolbar.autohide = True
    bokey_apply_theme_to_plot(plot, dark)
    return plot


def _time_range(view: DiagnosticView, keys: list[str], timezone: str) -> Range1d:
    """Shared time axis of the graphs: from the first sample of ``keys`` up to now (local)."""
    # Converted to pandas at the boundary: the graphs use a pandas time index.
    now = pd.Timestamp(to_datetime(in_timezone="UTC"))
    end = _local_index(pd.Series([0], index=[now]), timezone).index[0]
    starts = [
        _local_index(series, timezone).index.min()
        for series in (view.get_series(key) for key in keys if key in view.series)
        if len(series)
    ] + [
        _local_index(pd.Series([0], index=[pd.Timestamp(vector.date_time[0])]), timezone).index[0]
        for vector in (view.vectors[key] for key in keys if key in view.vectors)
        if vector.date_time
    ]
    start = min(starts) if starts else end
    if start >= end:
        start = end - pd.Timedelta(to_duration("1 minute"))
    return Range1d(start=start, end=end)


def DiagnosticsContent(
    view: DiagnosticView, timezone: str = DEFAULT_TIMEZONE, dark: bool = False
) -> Div:
    """Diagnostic graphs of a controller."""
    graphs = [graph for graph in view.graphs if any(view.has_series(k) for k in graph.series)]
    if not graphs:
        return Div(P("No diagnostic data collected yet.", cls=HINT_CLS))
    # One time axis for all graphs over time: lanes, lines and heatmaps line up, pan and zoom
    # move all of them. Profiles are over the element number.
    keys = [
        key
        for graph in graphs
        if graph.kind != "profile"
        for key in graph.series
        if view.has_series(key)
    ]
    x_range = _time_range(view, keys, timezone)
    plots: list[Plot] = []
    for graph in graphs:
        if graph.kind == "state":
            plots.append(StateGraph(view, graph, timezone, dark, x_range))
        elif graph.kind == "profile":
            plots.append(ProfileGraph(view, graph, timezone, dark))
        elif graph.kind == "heatmap":
            heatmap = HeatmapGraph(view, graph, timezone, dark, x_range)
            if heatmap is not None:
                plots.append(heatmap)
        else:
            plots.append(NumericGraph(view, graph, timezone, dark, x_range))
    layout = column(
        *plots,
        sizing_mode="stretch_width",
        height=sum(plot.height or 0 for plot in plots),
    )
    return Div(Bokeh(layout))


def DiagnosticsPartial(
    eos_host: str, eos_port: Union[str, int], data: Optional[dict] = None
) -> Div:
    """Diagnostic graphs of one controller (polling target)."""
    server = _server(eos_host, eos_port)
    device_id = str((data or {}).get("device_id", ""))
    try:
        view = DiagnosticView.model_validate(
            _get(
                f"{server}/v1/control/{device_id}/diagnostics",
                max_points=DIAGNOSTICS_MAX_POINTS,
            )
        )
    except (ControlApiError, ValueError) as exc:
        return Error(f"Can not retrieve the diagnostics of controller '{device_id}': {exc}")
    return DiagnosticsContent(view, _timezone(server), _is_dark(data))


# ---------------------------------------------------------------------------
# Diagnostic events
# ---------------------------------------------------------------------------
def EventsContent(events: list[dict[str, Any]], timezone: str = DEFAULT_TIMEZONE) -> Div:
    """Table of the raw diagnostic events, newest first."""
    if not events:
        return Div(P("No diagnostic events recorded yet.", cls=HINT_CLS))
    rows = []
    for event in events:
        event_data = event.get("data") or {}
        rows.append(
            Tr(
                Td(format_time(event.get("date_time"), timezone), cls="whitespace-nowrap"),
                Td(str(event.get("seq", ""))),
                Td(Code(str(event.get("event", "")))),
                Td(
                    format_event_data(event_data),
                    title=json.dumps(event_data, ensure_ascii=False, indent=1),
                    cls="font-mono break-all",
                ),
            )
        )
    return Div(
        P(f"Last {len(events)} events, newest first. Hover for the full data.", cls=HINT_CLS),
        Div(
            Table(
                Thead(Tr(Th("Time"), Th("Seq"), Th("Event"), Th("Data"))),
                Tbody(*rows),
                cls="text-xs",
            ),
            cls="overflow-x-auto max-h-[60vh] overflow-y-auto",
        ),
        cls="space-y-2",
    )


def EventsPartial(eos_host: str, eos_port: Union[str, int], data: Optional[dict] = None) -> Div:
    """Raw diagnostic events of one controller (polling target)."""
    server = _server(eos_host, eos_port)
    device_id = str((data or {}).get("device_id", ""))
    try:
        events = _get(f"{server}/v1/control/{device_id}/events", limit=EVENTS_LIMIT)
    except ControlApiError as exc:
        return Error(f"Can not retrieve the events of controller '{device_id}': {exc}")
    return EventsContent(events, _timezone(server))


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
def _diagnostics_off(device_id: str) -> Div:
    return Info(
        "Diagnostics are off. Set 'diagnostic_log_capacity' (e.g. 1000) of "
        f"'adapter/control/controllers/{device_id}' in the Config tab to collect them."
    )


def Control(eos_host: str, eos_port: Union[str, int], data: Optional[dict] = None) -> Div:
    """Generate the control page: the status of the selected device controller.

    Args:
        eos_host: Host name of the EOS server.
        eos_port: Port of the EOS server.
        data: ``device_id`` of the selected controller and ``dark`` (``"true"`` for the dark
            theme of the plots).

    Returns:
        The page content.
    """
    server = _server(eos_host, eos_port)
    data = data or {}
    dark = _is_dark(data)
    try:
        controllers: list[dict[str, Any]] = _get(f"{server}/v1/control")
    except ControlApiError as exc:
        message = f"Can not retrieve the device controllers from {server}: {exc}"
        logger.warning(message)
        return Div(Error(message), cls="space-y-3")
    if not controllers:
        return Div(
            Info(
                "No device controller is configured. Add one in the Config tab under "
                "'adapter/control/controllers'."
            ),
            cls="space-y-3",
        )

    ids = [str(controller["device_id"]) for controller in controllers]
    requested = str(data.get("device_id") or "")
    notice = None
    if requested and requested not in ids:
        notice = Info(f"Controller '{requested}' is no longer configured.")
    device_id = requested if requested in ids else ids[0]
    timezone = _timezone(server)

    selector = Select(
        *(
            Option(
                f"{controller['device_id']} — "
                f"{STATE_LABELS.get(controller.get('state', ''), controller.get('state', ''))}",
                value=controller["device_id"],
                selected=controller["device_id"] == device_id,
            )
            for controller in controllers
        ),
        name="device_id",
        hx_post=request_url_for(PAGE_PATH),
        hx_target="#page-content",
        hx_swap="innerHTML",
        hx_trigger="change",
        hx_vals=_dark_vals(),
        cls="border rounded px-3 py-2",
        title="Select device controller",
        aria_label="Device controller",
    )

    try:
        status: dict[str, Any] = _get(f"{server}/v1/control/{device_id}/status")
        status_content: Any = StatusContent(status, timezone)
    except ControlApiError as exc:
        status = {}
        status_content = Error(f"Can not retrieve the status of controller '{device_id}': {exc}")
    diagnostics_enabled = bool(
        next(c for c in controllers if c["device_id"] == device_id).get("diagnostics_enabled")
    )

    status_card = Div(
        status_content,
        id="control-status",
        hx_get=_partial_url(STATUS_PATH, device_id),
        hx_trigger=STATUS_POLL,
        hx_swap="innerHTML",
        cls="border rounded-md px-3 py-3",
    )

    if diagnostics_enabled:
        diagnostics_body: Any = Div(
            DiagnosticsPartial(eos_host, eos_port, {"device_id": device_id, "dark": dark}),
            id="control-diagnostics",
            hx_get=_partial_url(DIAGNOSTICS_PATH, device_id),
            hx_trigger=DIAGNOSTICS_POLL,
            hx_swap="innerHTML",
            hx_vals=_dark_vals(),
        )
        events_body: Any = Div(
            P("Loading…", cls=HINT_CLS),
            id="control-events",
            hx_get=_partial_url(EVENTS_PATH, device_id),
            hx_trigger=EVENTS_TRIGGER,
            hx_swap="innerHTML",
        )
    else:
        diagnostics_body = _diagnostics_off(device_id)
        events_body = _diagnostics_off(device_id)

    settings = status.get("settings") or {}
    return Div(
        Div(selector, notice, cls="flex flex-wrap items-center gap-2"),
        status_card,
        ConfigSection("Diagnostic graphs", diagnostics_body, open=True, compact=True),
        ConfigSection("Diagnostic events", events_body, open=False, compact=True),
        ConfigSection(
            "Settings",
            JsonView(settings),
            P(
                f"Change them in the Config tab under 'adapter/control/controllers/{device_id}'.",
                cls=HINT_CLS,
            ),
            open=False,
            compact=True,
        ),
        cls="space-y-3",
    )
