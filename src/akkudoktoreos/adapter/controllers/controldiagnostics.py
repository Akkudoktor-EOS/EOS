"""Device independent diagnostic series of a controller, for graphs in the dashboard.

The diagnostic log (``ControlDiagnosticLog``) is a bounded buffer of detailed events. It is meant
for debugging and holds only minutes of history at a 5 s control interval. Graphs need hours.
So the log folds selected numeric and state fields of the events into small per series ring
buffers (``DiagnosticSeriesStore``) while it records them. The ring buffers have their own
capacity and are not affected by ``drain()`` of the log.

What is folded is declared by the controller class as a series spec: the event name mapped to
the fields to track, or None to track all scalar fields of the event::

    DIAGNOSTIC_SERIES = {
        "readings_updated": None,
        "setpoint_calculated": ("setpoint_w", "charge_limit_w"),
    }

Series
------
- key: ``"<event>.<field>"``, e.g. ``"readings_updated.soc_percent"``.
- numeric (int, float, finite): one point per event, dtype float64. The unit is inferred from the
  field name suffix (``_w``, ``_wh``, ``_kwh``, ``_percent``, ``_c``, ``_ms``, ``_s``, ``_v``,
  ``_a``).
- state (str, bool): dtype object or bool. A point is stored only when the value changes. The
  value holds until the next point (``ffill`` for a frame).
- vector (non-empty tuple or list of numbers, e.g. the voltages of all cells of a battery): one
  point per event holding all values. Tracked only for fields named explicitly in the spec, never
  for an event spec of None. Non-finite values are stored as None (a gap). An empty tuple (the
  device reports no elements, e.g. no cells) is not tracked.
- None values are skipped, they leave a gap.

Storage and view
----------------
Writing happens on every event in the control thread, so the ring buffers are plain deques
(appending to a pandas object copies it). pandas is used at the boundary: ``view()`` builds a
``pd.Series`` with a timezone aware ``DatetimeIndex`` per series and returns it as
``PydanticDateTimeSeries``, the series model of EOS. ``DiagnosticView.to_dataframe()`` combines
series to a ``pd.DataFrame`` (columns: series keys). Vector series are returned separately as
``DiagnosticVectorSeries`` in ``DiagnosticView.vectors``.

The dashboard does not need to know the device: the view carries the series and suggested graphs
(series of one unit grouped in one graph, all state series in one graph of lanes, vector series
of one unit in one profile graph). Graph kinds:

- numeric: lines over time.
- state: lanes over time.
- profile: the latest value of every element of vector series over the element number, one line
  per series.
- heatmap: one vector series over time, the element number on the other axis, colour for the
  value. Only on explicit request (``DiagnosticGraph``), never suggested.

Points carry the sequence number of the log entry, so a client can poll for new points only.
"""

import math
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Optional, Sequence, cast

import pandas as pd
from pydantic import Field

from akkudoktoreos.core.pydantic import PydanticBaseModel, PydanticDateTimeSeries
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime

DEFAULT_SERIES_CAPACITY = 2000  # points per series, about 2.8 h at one point per 5 s
MAX_SERIES = 64  # protects against a spec with None for an event that has many fields

VectorValue = tuple[Optional[float], ...]
SeriesValue = float | str | bool | VectorValue
SeriesSpec = Mapping[str, Optional[Sequence[str]]]
SeriesKind = Literal["numeric", "state", "vector"]
GraphKind = Literal["numeric", "state", "profile", "heatmap"]

# Longer suffixes first: "_kwh" and "_wh" before "_w", "_ms" before "_s".
_UNIT_SUFFIXES = (
    ("_kwh", "kWh"),
    ("_wh", "Wh"),
    ("_ms", "ms"),
    ("_mv", "mV"),
    ("_percent", "%"),
    ("_w", "W"),
    ("_s", "s"),
    ("_c", "°C"),
    ("_v", "V"),
    ("_a", "A"),
)
_QUANTITY = {
    "W": "Power",
    "Wh": "Energy",
    "kWh": "Energy",
    "%": "Percent",
    "°C": "Temperature",
    "ms": "Duration",
    "s": "Time",
    "V": "Voltage",
    "mV": "Voltage",
    "A": "Current",
}
_UNIT_ORDER = ("W", "%", "°C", "ms", "s", "Wh", "kWh", "V", "mV", "A")


def infer_unit(field_name: str) -> str:
    """Unit of a field from its name, "" if the name carries none."""
    if field_name == "ms":
        return "ms"
    for suffix, unit in _UNIT_SUFFIXES:
        if field_name.endswith(suffix):
            return unit
    return ""


# --------------------------------------------------------------------------
# View model (what the dashboard gets)
# --------------------------------------------------------------------------


class DiagnosticGraph(PydanticBaseModel):
    """A suggested graph: series that belong together."""

    title: str
    unit: str
    kind: GraphKind
    series: list[str] = Field(
        description=(
            "Keys into DiagnosticView.series (numeric, state) or DiagnosticView.vectors "
            "(profile, heatmap)."
        )
    )
    index_label: str = Field(
        default="",
        description=(
            "Name of the numbered elements of vector series (profile and heatmap), e.g. 'Cell'."
        ),
    )


class DiagnosticVectorSeries(PydanticBaseModel):
    """Points of a vector series: all element values per point, element 1 first."""

    date_time: list[DateTime] = Field(description="Time of each point.")
    values: list[list[Optional[float]]] = Field(
        description="Element values of each point, None for a missing value."
    )

    def latest(self) -> list[Optional[float]]:
        """Element values of the newest point, empty without points."""
        return list(self.values[-1]) if self.values else []

    def to_dataframe(self) -> pd.DataFrame:
        """Points as pandas DataFrame: UTC time index, one column per element (1, 2, ...).

        Points with fewer elements than the longest one have NaN in the missing columns.
        """
        index = pd.to_datetime(
            [moment.timestamp() for moment in self.date_time], unit="s", utc=True
        )
        width = max((len(values) for values in self.values), default=0)
        rows = [
            [math.nan if v is None else v for v in values] + [math.nan] * (width - len(values))
            for values in self.values
        ]
        return pd.DataFrame(rows, index=index, columns=range(1, width + 1), dtype="float64")


class DiagnosticView(PydanticBaseModel):
    """Device independent view of the control operation for graphs.

    The series hold the points newer than the requested sequence number. numeric series have
    dtype float64, state series bool or object, the index is timezone aware.
    """

    device_id: str = ""
    last_seq: int = Field(description="Pass as since_seq on the next call for new points only.")
    reset: bool = Field(
        default=False,
        description="The requested since_seq is ahead of the log (restart): drop what you have.",
    )
    graphs: list[DiagnosticGraph]
    series: dict[str, PydanticDateTimeSeries]
    vectors: dict[str, DiagnosticVectorSeries] = Field(
        default_factory=dict, description="Vector series, e.g. the voltages of all cells."
    )

    def has_series(self, key: str) -> bool:
        """Whether a series or a vector series of this key is in the view."""
        return key in self.series or key in self.vectors

    def get_series(self, key: str) -> pd.Series:
        """One series as pandas Series named `key`."""
        return self.series[key].to_series().rename(key)

    def to_dataframe(self, keys: Optional[Sequence[str]] = None) -> pd.DataFrame:
        """Series as pandas DataFrame: time index, one column per series key.

        The series have points at different times (one per event), so the frame is sparse.
        Numeric columns have NaN between points. State columns are best forward filled
        (``df.ffill()``): a state holds until the next point.

        Args:
            keys: Series keys (e.g. ``graph.series``) in the column order wanted. Default: all.
        """
        wanted = list(self.series) if keys is None else list(keys)
        parts = []
        for key in wanted:
            series = self.get_series(key)
            if not series.empty:
                parts.append(series)
        if not parts:
            return pd.DataFrame(columns=wanted)
        return pd.concat(parts, axis=1, sort=True).reindex(columns=wanted)

    def graph_dataframe(self, graph: DiagnosticGraph) -> pd.DataFrame:
        """The series of one suggested graph as pandas DataFrame, see to_dataframe()."""
        return self.to_dataframe(graph.series)


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------


@dataclass
class _Series:
    unit: str
    kind: SeriesKind
    points: "deque[tuple[int, float, SeriesValue]]"  # (seq, epoch seconds, value)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _kind_of(value: Any, explicit: bool) -> Optional[SeriesKind]:
    """Kind of the series a value goes to, None if it is not tracked.

    Args:
        value: The field value.
        explicit: The field is named in the spec; vectors are tracked only then.
    """
    if isinstance(value, (bool, str)):
        return "state"
    if _is_number(value) and math.isfinite(value):
        return "numeric"
    if (
        explicit
        and isinstance(value, (tuple, list))
        and len(value) > 0
        and all(_is_number(v) for v in value)
    ):
        return "vector"
    return None


def _to_vector(values: Sequence[float]) -> VectorValue:
    return tuple(float(v) if math.isfinite(v) else None for v in values)


def _downsample(
    points: list[tuple[int, float, SeriesValue]], max_points: int
) -> list[tuple[int, float, SeriesValue]]:
    """Reduce to about max_points keeping the minimum and maximum of every time bucket.

    A plain stride (or a mean resample) would drop the power peaks a control graph is interested
    in. The last point is always kept, it is the current value.
    """
    if len(points) <= max_points:
        return points
    buckets = max(max_points // 2, 1)
    size = len(points) / buckets
    out: list[tuple[int, float, SeriesValue]] = []
    for b in range(buckets):
        chunk = points[int(b * size) : int((b + 1) * size)]
        if not chunk:
            continue
        low = min(chunk, key=lambda p: p[2])
        high = max(chunk, key=lambda p: p[2])
        picked = {id(low): low, id(high): high}
        out.extend(sorted(picked.values(), key=lambda p: p[0]))
    if out and out[-1] is not points[-1]:
        out.append(points[-1])
    return out


def _downsample_last(
    points: list[tuple[int, float, SeriesValue]], max_points: int
) -> list[tuple[int, float, SeriesValue]]:
    """Reduce to at most max_points keeping the last point of every bucket.

    For vector series: a minimum or maximum is not defined for a whole vector, the last point
    of a bucket is a real reading. The last point is always kept, it is the current value.
    """
    if len(points) <= max_points:
        return points
    size = len(points) / max_points
    return [points[min(int((b + 1) * size), len(points)) - 1] for b in range(max_points)]


def _to_vector_series(
    points: list[tuple[int, float, SeriesValue]], tz: Optional[str]
) -> DiagnosticVectorSeries:
    zone = "UTC" if tz is None else tz
    return DiagnosticVectorSeries(
        date_time=[to_datetime(p[1], in_timezone=zone) for p in points],
        values=[list(cast(VectorValue, p[2])) for p in points],
    )


def _to_pydantic_series(
    points: list[tuple[int, float, SeriesValue]], kind: SeriesKind, tz: Optional[str]
) -> PydanticDateTimeSeries:
    """Points to a pandas Series with a timezone aware index, as the EOS series model."""
    index = pd.to_datetime([p[1] for p in points], unit="s", utc=True)
    if tz is not None:
        index = index.tz_convert(tz)
    values = [p[2] for p in points]
    if kind == "numeric":
        dtype = "float64"
    else:
        dtype = "bool" if values and all(isinstance(v, bool) for v in values) else "object"
    return PydanticDateTimeSeries.from_series(pd.Series(values, index=index, dtype=dtype), tz=tz)


def _build_graphs(meta: Mapping[str, tuple[str, SeriesKind]]) -> list[DiagnosticGraph]:
    """Group the series into graphs.

    Numeric series by unit, all state series in one graph of lanes, vector series by unit in
    profile graphs.
    """
    numeric: dict[str, list[str]] = {}
    vectors: dict[str, list[str]] = {}
    states: list[str] = []
    for key, (unit, kind) in sorted(meta.items()):
        if kind == "state":
            states.append(key)
        elif kind == "vector":
            vectors.setdefault(unit, []).append(key)
        else:
            numeric.setdefault(unit, []).append(key)

    def order(unit: str) -> tuple[int, str]:
        return (_UNIT_ORDER.index(unit) if unit in _UNIT_ORDER else len(_UNIT_ORDER), unit)

    graphs = [
        DiagnosticGraph(
            title=f"{_QUANTITY[unit]} ({unit})" if unit in _QUANTITY else "Other",
            unit=unit,
            kind="numeric",
            series=numeric[unit],
        )
        for unit in sorted(numeric, key=order)
    ]
    if states:
        graphs.append(
            DiagnosticGraph(title="Modes and states", unit="", kind="state", series=states)
        )
    graphs.extend(
        DiagnosticGraph(
            title=f"{_QUANTITY[unit]} profile ({unit})" if unit in _QUANTITY else "Profile",
            unit=unit,
            kind="profile",
            series=vectors[unit],
        )
        for unit in sorted(vectors, key=order)
    )
    return graphs


class DiagnosticSeriesStore:
    """Thread-safe ring buffers of the tracked diagnostic fields.

    Written by the control thread (``fold()``, called by the diagnostic log), read from the
    event loop (``view()``).
    """

    def __init__(self, capacity: int = DEFAULT_SERIES_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._lock = threading.Lock()
        self._capacity = capacity
        self._spec: dict[str, Optional[tuple[str, ...]]] = {}
        self._series: dict[str, _Series] = {}

    @property
    def configured(self) -> bool:
        """Whether any event is tracked."""
        return bool(self._spec)

    def configure(self, spec: SeriesSpec) -> None:
        """Set what is tracked. Series already collected are kept."""
        new = {event: None if names is None else tuple(names) for event, names in spec.items()}
        with self._lock:
            self._spec = new

    def fold(self, seq: int, time_s: float, event: str, data: Mapping[str, Any]) -> None:
        """Take the tracked fields of one event into the series (time: UTC epoch seconds)."""
        with self._lock:
            if event not in self._spec:
                return
            names = self._spec[event]
            for name in data.keys() if names is None else names:
                if name not in data:
                    continue
                value = data[name]
                kind = _kind_of(value, explicit=names is not None)
                if kind is None:
                    continue
                key = f"{event}.{name}"
                series = self._series.get(key)
                if series is None:
                    if len(self._series) >= MAX_SERIES:
                        continue
                    series = _Series(
                        "" if kind == "state" else infer_unit(name),
                        kind,
                        deque(maxlen=self._capacity),
                    )
                    self._series[key] = series
                elif series.kind != kind:
                    continue  # the type of a field changed, keep the series consistent
                if kind == "state" and series.points and series.points[-1][2] == value:
                    continue  # unchanged
                if kind == "numeric":
                    value = float(value)
                elif kind == "vector":
                    value = _to_vector(value)
                series.points.append((seq, time_s, value))

    def resize(self, capacity: int) -> None:
        """Change the points per series. Shrinking keeps the newest points."""
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        with self._lock:
            self._capacity = capacity
            for series in self._series.values():
                series.points = deque(series.points, maxlen=capacity)

    def clear(self) -> None:
        """Drop all collected points and series."""
        with self._lock:
            self._series = {}

    def view(
        self,
        last_seq: int,
        since_seq: int = 0,
        max_points: int = 300,
        tz: Optional[str] = None,
        graphs: Optional[Sequence[DiagnosticGraph]] = None,
    ) -> DiagnosticView:
        """The series with the points newer than `since_seq`, reduced to about `max_points`.

        Args:
            last_seq: Sequence number of the newest log entry (returned as ``last_seq``).
            since_seq: Only points of newer log entries. 0: everything held.
            max_points: Upper bound of points per numeric and vector series, at least 2.
            tz: Timezone name of the series index. None: UTC.
            graphs: Explicit graph definitions. ``None`` builds graphs automatically by unit;
                an empty sequence suppresses graphs. Explicit definitions are returned even
                before their series have received their first point.
        """
        reset = since_seq > last_seq
        if reset:
            since_seq = 0
        max_points = max(max_points, 2)
        with self._lock:
            snapshot = {
                # Not beyond last_seq: a point folded after the caller read last_seq would
                # be returned now and again by the next poll.
                key: (s.unit, s.kind, [p for p in s.points if since_seq < p[0] <= last_seq])
                for key, s in self._series.items()
            }
        series: dict[str, PydanticDateTimeSeries] = {}
        vectors: dict[str, DiagnosticVectorSeries] = {}
        for key, (_, kind, points) in snapshot.items():
            if kind == "vector":
                vectors[key] = _to_vector_series(_downsample_last(points, max_points), tz)
                continue
            if kind == "numeric":
                points = _downsample(points, max_points)
            series[key] = _to_pydantic_series(points, kind, tz)
        meta = {key: (unit, kind) for key, (unit, kind, _) in snapshot.items()}
        return DiagnosticView(
            last_seq=last_seq,
            reset=reset,
            graphs=list(graphs) if graphs is not None else _build_graphs(meta),
            series=series,
            vectors=vectors,
        )
