import math

import pandas as pd

from akkudoktoreos.adapter.controllers.controldiagnostics import (
    MAX_SERIES,
    DiagnosticGraph,
    DiagnosticSeriesStore,
    DiagnosticView,
    infer_unit,
)

SPEC = {
    "readings_updated": None,
    "setpoint_calculated": ("setpoint_w", "inputs_valid"),
}
T0 = 1_761_900_000.0  # UTC epoch seconds, fixed


def fed_store(n: int = 10) -> DiagnosticSeriesStore:
    store = DiagnosticSeriesStore(capacity=100)
    store.configure(SPEC)
    for i in range(1, n + 1):
        t = T0 + 5 * i
        store.fold(i, t, "readings_updated", {"ac_power_w": i * 10, "soc_percent": None, "read_ms": 12.5, "label": "a"})
        store.fold(i, t + 0.2, "setpoint_calculated", {"setpoint_w": -i, "inputs_valid": i < 6})
        store.fold(i, t, "unknown", {"y": 1})
    return store


def test_unit_inference():
    names = ("ac_power_w", "energy_wh", "e_kwh", "soc_percent", "temperature_c", "read_ms", "ms", "age_s", "cell_voltages_mv", "u_v", "operation_factor")
    assert [infer_unit(n) for n in names] == ["W", "Wh", "kWh", "%", "°C", "ms", "ms", "s", "mV", "V", ""]


class TestDiagnosticSeriesStore:
    """Folding diagnostic events into series and the views of the series."""

    def test_tracking_and_gaps(self):
        view = fed_store().view(last_seq=10)
        assert set(view.series) == {
            "readings_updated.ac_power_w",
            "readings_updated.read_ms",
            "readings_updated.label",
            "setpoint_calculated.setpoint_w",
            "setpoint_calculated.inputs_valid",
        }  # None (soc) skipped, untracked events ignored
        assert len(view.get_series("readings_updated.ac_power_w")) == 10

    def test_series_are_pandas_with_tz_aware_index(self):
        view = fed_store().view(last_seq=10)
        power = view.get_series("readings_updated.ac_power_w")
        assert isinstance(power.index, pd.DatetimeIndex) and power.index.tz is not None
        assert power.dtype == "float64" and power.name == "readings_updated.ac_power_w"
        assert power.iloc[-1] == 100.0
        assert power.index[0].timestamp() == T0 + 5
        assert view.get_series("setpoint_calculated.inputs_valid").dtype == bool
        assert view.get_series("readings_updated.label").dtype == object

    def test_timezone_of_the_index(self):
        view = fed_store().view(last_seq=10, tz="Europe/Berlin")
        power = view.get_series("readings_updated.ac_power_w")
        assert isinstance(power.index, pd.DatetimeIndex)
        assert str(power.index.tz) == "Europe/Berlin"
        assert power.index[0].timestamp() == T0 + 5  # same instant

    def test_state_stored_on_change_only(self):
        state = fed_store().view(last_seq=10).get_series("setpoint_calculated.inputs_valid")
        assert list(state.values) == [True, False]
        assert [t.timestamp() for t in state.index] == [T0 + 5.2, T0 + 30.2]

    def test_graph_grouping_and_dataframe(self):
        view = fed_store().view(last_seq=10)
        graphs = {g.title: g for g in view.graphs}
        assert set(graphs["Power (W)"].series) == {"readings_updated.ac_power_w", "setpoint_calculated.setpoint_w"}
        assert graphs["Modes and states"].kind == "state"
        df = view.graph_dataframe(graphs["Power (W)"])
        assert list(df.columns) == graphs["Power (W)"].series
        assert len(df) == 20  # two events per cycle at slightly different times: sparse frame
        assert df["readings_updated.ac_power_w"].notna().sum() == 10
        states = view.graph_dataframe(graphs["Modes and states"]).ffill()
        assert states.notna().all().all() or states.iloc[0].isna().any()  # ffill fills after first point

    def test_explicit_graphs_replace_the_graphs_by_unit(self):
        graph = DiagnosticGraph(
            title="Setpoint", unit="W", kind="numeric", series=["setpoint_calculated.setpoint_w"]
        )
        view = fed_store().view(last_seq=10, graphs=[graph])
        assert [g.model_dump() for g in view.graphs] == [graph.model_dump()]

    def test_empty_graphs_suppress_graphs(self):
        view = fed_store().view(last_seq=10, graphs=())
        assert view.graphs == []
        assert "readings_updated.ac_power_w" in view.series

    def test_explicit_graphs_before_the_first_point(self):
        store = DiagnosticSeriesStore(capacity=10)
        store.configure(SPEC)
        graph = DiagnosticGraph(title="Power", unit="W", kind="numeric", series=["x.y_w"])
        view = store.view(last_seq=0, graphs=[graph])
        assert [g.title for g in view.graphs] == ["Power"]
        assert view.series == {}

    def test_incremental_and_reset(self):
        store = fed_store()
        view = store.view(last_seq=10, since_seq=7)
        assert len(view.get_series("readings_updated.ac_power_w")) == 3
        assert not view.reset
        view = store.view(last_seq=10, since_seq=99)
        assert view.reset and len(view.get_series("readings_updated.ac_power_w")) == 10

    def test_empty_series_in_dataframe(self):
        view = fed_store().view(last_seq=10, since_seq=10)  # nothing new
        assert all(len(view.get_series(k)) == 0 for k in view.series)
        df = view.to_dataframe()
        assert df.empty and set(df.columns) == set(view.series)

    def test_downsample_keeps_peaks_and_last(self):
        store = DiagnosticSeriesStore(capacity=1000)
        store.configure({"e": ("p_w",)})
        for i in range(1, 501):
            store.fold(i, T0 + i, "e", {"p_w": 5000 if i == 250 else 100})
        s = store.view(last_seq=500, max_points=20).get_series("e.p_w")
        assert len(s) <= 22
        assert s.max() == 5000.0 and s.index[-1].timestamp() == T0 + 500

    def test_capacity_and_series_limit(self):
        store = DiagnosticSeriesStore(capacity=5)
        store.configure({"e": None})
        for i in range(1, 21):
            store.fold(i, T0 + i, "e", {"a_w": i})
        assert list(store.view(last_seq=20).get_series("e.a_w").values) == [16.0, 17.0, 18.0, 19.0, 20.0]
        store.resize(2)
        assert len(store.view(last_seq=20).get_series("e.a_w")) == 2

    def test_view_never_returns_points_beyond_last_seq(self):
        # last_seq is read before the points are copied: points folded in between belong to the
        # next poll, otherwise the client gets them twice.
        store = fed_store()
        view = store.view(last_seq=6)
        assert len(view.get_series("readings_updated.ac_power_w")) == 6
        nxt = store.view(last_seq=10, since_seq=view.last_seq)
        assert len(nxt.get_series("readings_updated.ac_power_w")) == 4


def test_json_roundtrip():
    view = fed_store().view(last_seq=10)
    again = DiagnosticView.model_validate_json(view.model_dump_json())
    pd.testing.assert_series_equal(
        view.get_series("readings_updated.ac_power_w"),
        again.get_series("readings_updated.ac_power_w"),
    )
    # Compare the content: PydanticBaseModel has a private _uuid per instance, which
    # BaseModel.__eq__ takes into account, so equal graphs are not == as objects.
    assert [g.model_dump() for g in again.graphs] == [g.model_dump() for g in view.graphs]
    assert (again.last_seq, again.reset, again.device_id) == (view.last_seq, view.reset, view.device_id)


VECTOR_SPEC = {"bms1_info": ("cell_voltages_mv", "soc_percent"), "bms2_info": ("cell_voltages_mv",)}


def vector_store(n: int = 10, capacity: int = 100) -> DiagnosticSeriesStore:
    store = DiagnosticSeriesStore(capacity=capacity)
    store.configure(VECTOR_SPEC)
    for i in range(1, n + 1):
        store.fold(i, T0 + 600 * i, "bms1_info", {"cell_voltages_mv": (3300 + i, 3310, 3320), "soc_percent": 50})
    return store


class TestVectorSeries:
    """Vector series of the store: all element values of an event field per point."""

    def test_points_hold_all_values(self):
        view = vector_store().view(last_seq=10, tz="Europe/Berlin")
        vector = view.vectors["bms1_info.cell_voltages_mv"]
        assert vector.values[0] == [3301.0, 3310.0, 3320.0]
        assert vector.latest() == [3310.0, 3310.0, 3320.0]
        assert vector.date_time[0].timestamp() == T0 + 600
        assert vector.date_time[0].timezone_name == "Europe/Berlin"
        assert "bms1_info.cell_voltages_mv" not in view.series  # vectors are kept apart
        assert view.has_series("bms1_info.cell_voltages_mv") and view.has_series("bms1_info.soc_percent")

    def test_tracked_only_when_named_explicitly(self):
        store = DiagnosticSeriesStore(capacity=10)
        store.configure({"bms1_info": None})
        store.fold(1, T0, "bms1_info", {"cell_voltages_mv": (3300, 3310), "soc_percent": 50})
        view = store.view(last_seq=1)
        assert view.vectors == {}
        assert set(view.series) == {"bms1_info.soc_percent"}

    def test_non_numbers_are_not_tracked_and_non_finite_values_are_gaps(self):
        store = DiagnosticSeriesStore(capacity=10)
        store.configure({"e": ("names", "flags", "values_mv")})
        store.fold(1, T0, "e", {"names": ("a", "b"), "flags": (True, False), "values_mv": (1, math.nan, 3.5)})
        store.fold(2, T0 + 1, "e", {"names": (), "values_mv": ()})  # no elements: nothing to show
        view = store.view(last_seq=2)
        assert set(view.vectors) == {"e.values_mv"} and view.series == {}
        assert view.vectors["e.values_mv"].values == [[1.0, None, 3.5]]

    def test_one_series_against_the_series_limit(self):
        store = DiagnosticSeriesStore(capacity=10)
        store.configure({"e": tuple(f"v{i}_mv" for i in range(MAX_SERIES + 1))})
        store.fold(1, T0, "e", {f"v{i}_mv": tuple(range(128)) for i in range(MAX_SERIES + 1)})
        assert len(store.view(last_seq=1).vectors) == MAX_SERIES

    def test_incremental(self):
        view = vector_store().view(last_seq=10, since_seq=7)
        assert len(view.vectors["bms1_info.cell_voltages_mv"].values) == 3

    def test_downsampling_keeps_the_last_reading_of_each_bucket(self):
        view = vector_store(n=100).view(last_seq=100, max_points=10)
        values = view.vectors["bms1_info.cell_voltages_mv"].values
        assert [v[0] for v in values] == [3300.0 + 10 * b for b in range(1, 11)]

    def test_dataframe_pads_shorter_points(self):
        store = DiagnosticSeriesStore(capacity=10)
        store.configure({"e": ("t_c",)})
        store.fold(1, T0, "e", {"t_c": (20, 21)})
        store.fold(2, T0 + 600, "e", {"t_c": (22, 23, 24)})
        frame = store.view(last_seq=2).vectors["e.t_c"].to_dataframe()
        assert list(frame.columns) == [1, 2, 3]
        assert isinstance(frame.index, pd.DatetimeIndex) and str(frame.index.tz) == "UTC"
        assert frame.index[0].timestamp() == T0
        assert math.isnan(frame.iloc[0][3]) and frame.iloc[1][3] == 24.0

    def test_profile_graphs_by_unit_without_heatmaps(self):
        store = vector_store()
        store.fold(11, T0 + 7000, "bms2_info", {"cell_voltages_mv": (3300, 3301, 3302)})
        graphs = store.view(last_seq=11).graphs
        assert [(g.title, g.kind, g.series) for g in graphs] == [
            ("Percent (%)", "numeric", ["bms1_info.soc_percent"]),
            ("Voltage profile (mV)", "profile", ["bms1_info.cell_voltages_mv", "bms2_info.cell_voltages_mv"]),
        ]

    def test_scalar_series_have_no_vectors(self):
        assert fed_store().view(last_seq=10).vectors == {}

    def test_json_roundtrip(self):
        view = vector_store().view(last_seq=10, tz="Europe/Berlin")
        again = DiagnosticView.model_validate_json(view.model_dump_json())
        key = "bms1_info.cell_voltages_mv"
        assert again.vectors[key].values == view.vectors[key].values
        pd.testing.assert_frame_equal(again.vectors[key].to_dataframe(), view.vectors[key].to_dataframe())
