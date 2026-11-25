"""Tests for the EOSdash control page.

The page talks to EOS by REST. Its requests are sent to the real control REST router, with the
controllers configured and the control adapter state set up by the tests, so the tests cover
page and API.
"""

from html import escape
from types import MappingProxyType
from typing import Any, Iterator
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fasthtml.common import Div, to_xml

from akkudoktoreos.adapter.control import ControlAdapter
from akkudoktoreos.adapter.controllers.controlabc import (
    ControlDiagnosticEntry,
    ControlDiagnosticLog,
    ControlStatus,
)
from akkudoktoreos.adapter.controllers.controldiagnostics import (
    DiagnosticGraph,
    DiagnosticVectorSeries,
)
from akkudoktoreos.adapter.controllers.sma.smastpe import (
    SMA_DIAGNOSTIC_SERIES,
    SmaCommand,
)
from akkudoktoreos.devices.devicesabc import BatteryOperationMode
from akkudoktoreos.server.dash import control as page
from akkudoktoreos.server.rest.control import router
from akkudoktoreos.server.rest.error import register_problem_handlers
from akkudoktoreos.utils.datetimeutil import to_datetime

CONTROLLERS = {
    "inverter1": {
        "controller_type": "sma:sunny-tripower-smart-energy",
        "host": "192.168.178.6",
        "battery_id": "battery1",
        "grid_power_key": "meter1:grid_net_w",
        "diagnostic_log_capacity": 100,
    },
    "meter1": {"controller_type": "sma:energy-meter", "serial": 1900123456},
}


class RestShim:
    """Stands in for the ``requests`` module of the page, forwarding to a TestClient."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.calls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        path = urlsplit(url).path
        self.calls.append(path)
        kwargs.pop("timeout", None)
        return self.client.get(path, **kwargs)


class FakeController:
    def __init__(self, status: ControlStatus, settings_pending: bool = False) -> None:
        self.status = status
        self.settings_pending = settings_pending

    def settings_changed(self, settings: Any) -> bool:
        return self.settings_pending


@pytest.fixture
def adapter(config_eos) -> Iterator[ControlAdapter]:
    config_eos.merge_settings_from_dict({"adapter": {"control": {"controllers": CONTROLLERS}}})
    adapter = ControlAdapter()
    saved = (adapter.controllers, adapter.diagnostic_logs, adapter.start_errors)
    adapter.controllers = {}
    adapter.diagnostic_logs = {}
    adapter.start_errors = {}
    yield adapter
    adapter.controllers, adapter.diagnostic_logs, adapter.start_errors = saved


@pytest.fixture
def rest(adapter: ControlAdapter, monkeypatch: pytest.MonkeyPatch) -> Iterator[RestShim]:
    app = FastAPI()
    register_problem_handlers(app)
    app.include_router(router)
    with TestClient(app) as client:
        shim = RestShim(client)
        monkeypatch.setattr(page, "requests", shim)
        yield shim


def render(data: dict | None = None) -> str:
    return to_xml(page.Control("localhost", 8503, data))


def run_inverter(adapter: ControlAdapter, settings_pending: bool = False, **status: Any) -> None:
    values: dict[str, Any] = {"date_time": to_datetime(), "connected": True}
    values.update(status)
    adapter.controllers["inverter1"] = FakeController(  # type: ignore[assignment]
        ControlStatus(**values), settings_pending
    )


def record(log: ControlDiagnosticLog, event: str, **data: Any) -> None:
    log.record(
        ControlDiagnosticEntry(date_time=to_datetime(), event=event, data=MappingProxyType(data))
    )


def diagnostic_log(adapter: ControlAdapter) -> ControlDiagnosticLog:
    """Enabled log of inverter1 with the series of the SMA STP-SE."""
    log = adapter.diagnostic_logs["inverter1"] = ControlDiagnosticLog(capacity=100)
    log.configure_series(SMA_DIAGNOSTIC_SERIES)
    return log


class TestSelector:
    def test_all_controllers_are_listed_with_their_state(
        self, rest: RestShim, adapter: ControlAdapter
    ) -> None:
        run_inverter(adapter)
        adapter.start_errors["meter1"] = "OSError: no network interface"
        html = render()
        assert "inverter1 — running" in html
        assert "meter1 — start failed" in html

    def test_first_controller_is_selected_by_default(self, rest: RestShim) -> None:
        render()
        assert "/v1/control/inverter1/status" in rest.calls

    def test_posted_selection_is_shown(self, rest: RestShim, adapter: ControlAdapter) -> None:
        adapter.start_errors["meter1"] = "OSError: no network interface"
        html = render({"device_id": "meter1"})
        assert "/v1/control/meter1/status" in rest.calls
        assert "Start failed: OSError: no network interface" in html

    def test_removed_controller_falls_back_to_the_first(self, rest: RestShim) -> None:
        html = render({"device_id": "gone"})
        assert "Controller &#x27;gone&#x27; is no longer configured" in html or (
            "Controller 'gone' is no longer configured" in html
        )
        assert "/v1/control/inverter1/status" in rest.calls


class TestStatus:
    def test_command_and_readings_are_shown(self, rest: RestShim, adapter: ControlAdapter) -> None:
        now = to_datetime()
        run_inverter(
            adapter,
            active_command=SmaCommand(
                date_time=now,
                valid_until=now.add(hours=1),
                label="FORCED_CHARGE",
                mode=BatteryOperationMode.FORCED_CHARGE,
                operation_factor=0.6,
            ),
            data=MappingProxyType({"bat_soc_percent": 55, "ac_power_w": 1234.5}),
        )
        html = render()
        assert "FORCED_CHARGE" in html
        assert "0.6" in html
        assert "55 %" in html
        assert "1234.5 W" in html
        assert "connected" in html

    def test_running_controller_without_command_is_in_its_safe_state(
        self, rest: RestShim, adapter: ControlAdapter
    ) -> None:
        run_inverter(adapter)
        assert "No command: the device is in its safe state." in render()

    def test_pending_settings_are_hinted(self, rest: RestShim, adapter: ControlAdapter) -> None:
        hint = "The configured settings differ from the ones this controller runs with."
        run_inverter(adapter)
        assert hint not in render()
        run_inverter(adapter, settings_pending=True)
        assert hint in render()

    def test_status_is_polled_every_5_s(self, rest: RestShim) -> None:
        html = render()
        assert 'hx-trigger="every 5s"' in html
        assert "/eosdash/control/status?device_id=inverter1" in html

    def test_status_partial(self, rest: RestShim, adapter: ControlAdapter) -> None:
        run_inverter(adapter, connected=False, error="read timeout")
        html = to_xml(page.StatusPartial("localhost", 8503, {"device_id": "inverter1"}))
        assert "disconnected" in html
        assert "Error: read timeout" in html


class TestDiagnostics:
    def test_graphs_are_rendered(self, rest: RestShim, adapter: ControlAdapter) -> None:
        log = diagnostic_log(adapter)
        for soc in (50, 51, 52):
            record(log, "readings_updated", soc_percent=soc, ac_power_w=100.0 * soc)
        record(log, "command_received", mode="IDLE", operation_factor=1.0)
        record(log, "command_received", mode="FORCED_CHARGE", operation_factor=0.5)

        html = to_xml(page.DiagnosticsPartial("localhost", 8503, {"device_id": "inverter1"}))

        assert "Bokeh" in html  # plots embedded
        assert "No diagnostic data collected yet." not in html

    def test_graphs_share_one_time_axis(
        self, rest: RestShim, adapter: ControlAdapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = diagnostic_log(adapter)
        record(log, "readings_updated", soc_percent=50, ac_power_w=100.0)
        record(log, "command_received", mode="IDLE", operation_factor=1.0)
        layouts: list[Any] = []

        def capture(layout: Any) -> Div:
            layouts.append(layout)
            return Div()

        monkeypatch.setattr(page, "Bokeh", capture)

        page.DiagnosticsPartial("localhost", 8503, {"device_id": "inverter1"})

        assert len(layouts) == 1  # one layout for all graphs
        plots = layouts[0].children
        assert len(plots) > 1
        x_range = plots[0].x_range
        assert all(plot.x_range is x_range for plot in plots)
        assert x_range.start < x_range.end

    def test_profiles_and_heatmaps(
        self, rest: RestShim, adapter: ControlAdapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = adapter.diagnostic_logs["inverter1"] = ControlDiagnosticLog(capacity=100)
        cells = ["bms1_info.cell_voltages_mv", "bms2_info.cell_voltages_mv"]
        log.configure_series(
            {"bms1_info": ("soc_percent", "cell_voltages_mv"), "bms2_info": ("cell_voltages_mv",)},
            (
                DiagnosticGraph(title="SoC", unit="%", kind="numeric", series=["bms1_info.soc_percent"]),
                DiagnosticGraph(title="Cells", unit="mV", kind="profile", series=cells, index_label="Cell"),
                DiagnosticGraph(title="Tower 1", unit="mV", kind="heatmap", series=cells[:1]),
                DiagnosticGraph(title="Tower 3", unit="mV", kind="heatmap", series=["bms3_info.x_mv"]),
            ),
        )
        for offset in (0, 1):
            record(log, "bms1_info", soc_percent=50, cell_voltages_mv=(3300 + offset, 3310, 3320))
            record(log, "bms2_info", cell_voltages_mv=(3305, 3315))
        layouts: list[Any] = []

        def capture(layout: Any) -> Div:
            layouts.append(layout)
            return Div()

        monkeypatch.setattr(page, "Bokeh", capture)

        page.DiagnosticsPartial("localhost", 8503, {"device_id": "inverter1"})

        soc, profile, heatmap = layouts[0].children  # no heatmap without data (tower 3)
        assert profile.title.text == "Cells [mV]" and profile.xaxis[0].axis_label == "Cell"
        assert profile.x_range is not soc.x_range  # over the cell number, not the time
        legend = [item.label["value"] for item in profile.legend[0].items]
        assert legend == cells  # the same field of two towers: the whole key
        assert heatmap.x_range is soc.x_range  # shares the time axis
        assert heatmap.y_range.end == 3.5  # three cells
        image = heatmap.renderers[0]
        assert len(image.data_source.data["image"]) == 2  # one column per reading
        assert any(type(item).__name__ == "ColorBar" for item in heatmap.right)

    def test_heatmap_columns_reach_to_the_next_reading_but_keep_gaps(self) -> None:
        start = to_datetime("2026-11-24T10:00:00+00:00")
        seconds = (0, 600, 1200, 10_000)
        vector = DiagnosticVectorSeries(
            date_time=[start.add(seconds=offset) for offset in seconds],
            values=[[3300.0, None]] * len(seconds),
        )
        images, starts, widths = page._heatmap_columns(vector, "Europe/Berlin")
        assert list(widths) == [600_000.0, 600_000.0, 1_200_000.0, 600_000.0]
        assert starts[1] - starts[0] == 600_000.0
        assert images[0].shape == (2, 1)

    def test_no_data_yet(self, rest: RestShim, adapter: ControlAdapter) -> None:
        diagnostic_log(adapter)
        html = to_xml(page.DiagnosticsPartial("localhost", 8503, {"device_id": "inverter1"}))
        assert "No diagnostic data collected yet." in html

    def test_graphs_are_polled_every_30_s_inside_their_section(self, rest: RestShim) -> None:
        html = render()
        assert 'hx-trigger="every 30s"' in html
        # The section element is not replaced: only its content is the polling target.
        start = html.index('id="control-diagnostics"')
        assert html.rfind("<details", 0, start) > html.rfind("</details>", 0, start)

    def test_diagnostics_off_tells_how_to_switch_them_on(self, rest: RestShim) -> None:
        html = render({"device_id": "meter1"})
        assert "Diagnostics are off" in html
        assert "adapter/control/controllers/meter1" in html
        # The hints of the graphs and events sections and the settings section.
        assert html.count(f'hx-get="{escape(page.config_url("meter1"))}"') == 3
        assert 'id="control-diagnostics"' not in html
        assert 'id="control-events"' not in html


class TestEvents:
    def test_events_are_loaded_when_opened_and_polled_while_open(self, rest: RestShim) -> None:
        html = render()
        assert "/eosdash/control/events?device_id=inverter1" in html
        assert "toggle[this.closest(&#x27;details&#x27;).open]" in html or (
            "toggle[this.closest('details').open]" in html
        )
        # Not fetched with the page: only when the section is opened.
        assert "/v1/control/inverter1/events" not in rest.calls

    def test_events_table_newest_first(self, rest: RestShim, adapter: ControlAdapter) -> None:
        log = diagnostic_log(adapter)
        record(log, "register_write", address=40151, values=(0, 803))
        record(log, "command_received", mode="IDLE")

        html = to_xml(page.EventsPartial("localhost", 8503, {"device_id": "inverter1"}))

        assert html.index("command_received") < html.index("register_write")
        assert "address=40151" in html

    def test_no_events_yet(self, rest: RestShim, adapter: ControlAdapter) -> None:
        diagnostic_log(adapter)
        html = to_xml(page.EventsPartial("localhost", 8503, {"device_id": "inverter1"}))
        assert "No diagnostic events recorded yet." in html


class TestPage:
    def test_settings_are_shown_collapsed(self, rest: RestShim) -> None:
        html = render()
        assert "192.168.178.6" in html
        start = html.index(">Settings<")
        details = html.rfind("<details", 0, start)
        assert " open" not in html[details : html.index(">", details)]

    def test_settings_link_to_the_config_tab(self, rest: RestShim) -> None:
        html = render()
        start = html.index(">Settings<")
        assert f'hx-get="{escape(page.config_url("inverter1"))}"' in html[start:]
        assert 'hx-target="#page-content"' in html[start:]

    def test_config_url_opens_the_controller_card(self) -> None:
        query = parse_qs(urlsplit(page.config_url("inverter1")).query)
        assert query == {
            "search": ["adapter.control.controllers"],
            "open": ["adapter.control.controllers.inverter1"],
        }

    def test_no_controller_configured(self, rest: RestShim, config_eos) -> None:
        config_eos.adapter.control.controllers = {}
        assert "No device controller is configured" in render()

    def test_unreachable_eos_is_reported(
        self, rest: RestShim, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(url: str, **kwargs: Any) -> Any:
            raise ConnectionError("connection refused")

        monkeypatch.setattr(rest, "get", fail)
        assert "EOS server not reachable" in render()


class TestFormatting:
    @pytest.mark.parametrize(
        "key, unit",
        [
            ("grid_import_kwh", "kWh"),
            ("energy_wh", "Wh"),
            ("ac_power_w", "W"),
            ("bat_soc_percent", "%"),
            ("bat_temperature_c", "°C"),
            ("inverter1:bat_power_w", "W"),
            ("bat_soc_factor", ""),
            ("mode", ""),
        ],
    )
    def test_reading_unit_from_the_key_suffix(self, key: str, unit: str) -> None:
        assert page.reading_unit(key) == unit

    def test_format_value(self) -> None:
        assert page.format_value(1234.5, "W") == "1234.5 W"
        assert page.format_value(2.0) == "2"
        assert page.format_value(True) == "yes"
        assert page.format_value(None) == "–"

    def test_long_event_data_is_shortened(self) -> None:
        text = page.format_event_data({"values": list(range(200))})
        assert len(text) == page.EVENT_DATA_MAX_CHARS
        assert text.endswith("…")
