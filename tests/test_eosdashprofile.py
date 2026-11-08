"""Tests for the EOSdash battery profile page.

The page talks to EOS by REST. Its requests are sent to the real profile REST
router (on a fresh profile store), so the tests cover page, API and store.
"""

import json
from typing import Any, Iterator
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fasthtml.common import to_xml

from akkudoktoreos.devices.profile.batteryprofile import BatteryProfileSettings
from akkudoktoreos.devices.profile.inverterprofile import InverterProfileSettings
from akkudoktoreos.devices.profile.profilestore import ProfileStore, get_profile_store
from akkudoktoreos.server.dash import profile as page
from akkudoktoreos.server.rest.error import register_problem_handlers
from akkudoktoreos.server.rest.profile import router


class RestShim:
    """Stands in for the ``requests`` module of the page, forwarding to a TestClient."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.calls: list[tuple[str, str]] = []

    def _send(self, method: str, url: str, **kwargs: Any) -> Any:
        path = urlsplit(url).path
        self.calls.append((method, path))
        kwargs.pop("timeout", None)
        return getattr(self.client, method)(path, **kwargs)

    def get(self, url: str, **kwargs: Any) -> Any:
        return self._send("get", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> Any:
        return self._send("put", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> Any:
        return self._send("delete", url, **kwargs)


@pytest.fixture
def rest(config_eos, monkeypatch) -> Iterator[RestShim]:
    ProfileStore.reset_instance()
    app = FastAPI()
    register_problem_handlers(app)
    app.include_router(router)
    with TestClient(app) as client:
        shim = RestShim(client)
        monkeypatch.setattr(page, "requests", shim)
        yield shim
    ProfileStore.reset_instance()


def render(data: dict | None = None) -> str:
    return to_xml(page.Profile("localhost", 8503, data))


def generic_lfp() -> dict[str, Any]:
    path = get_profile_store().package_dir(BatteryProfileSettings) / "generic-lfp.json"
    return json.loads(path.read_text(encoding="utf-8"))


def user_profile(name: str) -> Any:
    return get_profile_store().load(BatteryProfileSettings, name)


class TestView:
    def test_default_profile_is_shown(self, rest):
        html = render()
        assert "Battery profiles" in html
        assert "generic-lfp (packaged)" in html
        assert "Generic LFP home battery" in html  # summary
        assert "Edit &#x27;generic-lfp&#x27;" in html or "Edit 'generic-lfp'" in html
        assert "read only" in html  # packaged hint
        assert "Charge limit [C-rate]" in html and "Discharge limit [C-rate]" in html

    def test_packaged_profile_has_no_delete_button(self, rest):
        assert ">Delete<" not in render()
        assert "Revert to packaged" not in render()

    def test_select_shows_another_profile(self, rest):
        get_profile_store().save(
            "my-battery",
            BatteryProfileSettings.model_validate({**generic_lfp(), "battery_model": "Mine"}),
        )
        html = render({"action": "select", "profile_name": "my-battery"})
        assert "my-battery (user)" in html
        assert "Mine" in html
        assert ">Delete<" in html

    def test_unreachable_eos_is_reported(self, rest, monkeypatch):
        def fail(url: str, **kwargs: Any) -> Any:
            raise ConnectionError("refused")

        monkeypatch.setattr(rest, "get", fail)
        html = render()
        assert "Can not retrieve battery profiles" in html and "refused" in html


class TestSave:
    def test_save_of_a_packaged_profile_creates_a_user_override(self, rest):
        document = {**generic_lfp(), "nominal_capacity_wh": 10240.0}
        html = render(
            {"action": "save", "profile_name": "generic-lfp", "document": json.dumps(document)}
        )
        assert "Saved battery profile &#x27;generic-lfp&#x27;" in html or "Saved battery profile 'generic-lfp'" in html
        assert user_profile("generic-lfp").nominal_capacity_wh == 10240.0
        assert "generic-lfp (user, overrides packaged)" in html
        assert "Revert to packaged" in html

    def test_save_as_creates_a_new_profile_and_selects_it(self, rest):
        html = render(
            {
                "action": "save_as",
                "profile_name": "generic-lfp",
                "new_name": "copy",
                "document": json.dumps({**generic_lfp(), "battery_model": "Copy"}),
            }
        )
        assert user_profile("copy").battery_model == "Copy"
        assert "copy (user)" in html
        assert "Edit &#x27;copy&#x27;" in html or "Edit 'copy'" in html

    def test_save_as_without_a_name_is_refused(self, rest):
        html = render(
            {"action": "save_as", "profile_name": "generic-lfp", "document": "{}", "new_name": " "}
        )
        assert "Enter a name for the new profile." in html
        assert ("put", "/v1/profile/battery/") not in rest.calls

    def test_invalid_json_keeps_the_edited_text(self, rest):
        html = render({"action": "save", "profile_name": "generic-lfp", "document": '{"broken": '})
        assert "not valid JSON" in html
        assert "{&quot;broken&quot;: " in html or '{"broken": ' in html
        assert not any(method == "put" for method, _ in rest.calls)

    def test_invalid_document_is_refused_by_eos_and_kept(self, rest):
        document = {**generic_lfp(), "nominal_capacity_wh": -5}
        html = render(
            {"action": "save", "profile_name": "generic-lfp", "document": json.dumps(document)}
        )
        assert "not saved" in html and "422" in html and "nominal_capacity_wh" in html
        assert "-5" in html  # the edited text is still in the editor
        assert get_profile_store().path(BatteryProfileSettings, "generic-lfp")[0].parent == (
            get_profile_store().package_dir(BatteryProfileSettings)
        )



class TestPreview:
    def test_preview_draws_the_edited_text_without_saving(self, rest):
        document = {**generic_lfp(), "battery_model": "Edited Model", "nominal_capacity_wh": 7777.0}
        html = render(
            {"action": "preview", "profile_name": "generic-lfp", "document": json.dumps(document)}
        )
        assert "Preview of unsaved changes" in html
        assert '<td class="">Edited Model</td>' in html  # summary of the edited document
        assert '<td class="">7777.0 Wh</td>' in html
        assert "Charge and discharge limits" in html and "Bokeh" in html
        assert not any(method in ("put", "delete") for method, _ in rest.calls)
        assert user_profile("generic-lfp").battery_model != "Edited Model"
        assert get_profile_store().path(BatteryProfileSettings, "generic-lfp")[0].parent == (
            get_profile_store().package_dir(BatteryProfileSettings)
        )

    def test_preview_of_invalid_json_is_reported_and_kept(self, rest):
        html = render(
            {"action": "preview", "profile_name": "generic-lfp", "document": '{"broken": '}
        )
        assert "not valid JSON" in html
        assert "{&quot;broken&quot;: " in html or '{"broken": ' in html

    @pytest.mark.parametrize("text", ["[1, 2]", "42", "null"])
    def test_preview_of_a_non_object_is_reported(self, rest, text):
        html = render({"action": "preview", "profile_name": "generic-lfp", "document": text})
        assert "must be a JSON object" in html

    def test_preview_of_an_invalid_document_is_reported_and_drawn(self, rest):
        document = {**generic_lfp(), "nominal_capacity_wh": -5}
        html = render(
            {"action": "preview", "profile_name": "generic-lfp", "document": json.dumps(document)}
        )
        assert "the document is invalid" in html and "nominal_capacity_wh" in html
        assert "-5" in html  # the edited text is still in the editor
        assert not any(method == "put" for method, _ in rest.calls)

    def test_editor_has_a_preview_button_and_explains_it(self, rest):
        html = render()
        assert "&quot;preview&quot;" in html or '"preview"' in html
        assert "Preview draws them from the text below without saving" in html


class TestDelete:
    def test_delete_of_a_user_profile(self, rest):
        get_profile_store().save("my-battery", BatteryProfileSettings.model_validate(generic_lfp()))
        html = render({"action": "delete", "profile_name": "my-battery"})
        assert "Deleted battery profile" in html
        assert "my-battery (user)" not in html  # no longer in the selection
        assert "generic-lfp (packaged)" in html

    def test_delete_of_an_override_reverts_to_the_packaged_profile(self, rest):
        document = {**generic_lfp(), "nominal_capacity_wh": 10240.0}
        get_profile_store().save("generic-lfp", BatteryProfileSettings.model_validate(document))
        html = render({"action": "delete", "profile_name": "generic-lfp"})
        assert "Reverted" in html
        assert "generic-lfp (packaged)" in html
        assert get_profile_store().load(BatteryProfileSettings, "generic-lfp").nominal_capacity_wh is None

    def test_delete_of_a_packaged_profile_is_reported(self, rest):
        html = render({"action": "delete", "profile_name": "generic-lfp"})
        assert "not deleted" in html and "409" in html


def test_page_is_served_by_eosdash():
    from akkudoktoreos.server import eosdash

    paths = {getattr(route, "path", None) for route in eosdash.app.routes}
    assert "/eosdash/profile" in paths


# --- efficiency plot ------------------------------------------------------

from akkudoktoreos.devices.profile.batteryprofile import (  # noqa: E402
    EfficiencyCurve,
    PrecomputedEfficiencyCurve,
)

CURVE_POINTS = {"c_rate": [0.1, 0.25, 0.5, 1.0], "efficiency": [0.90, 0.96, 0.95, 0.91]}


def make_curve(**changes: Any) -> EfficiencyCurve:
    return EfficiencyCurve.model_validate({**CURVE_POINTS, **changes})


def drawn_value(glyphs: dict[str, Any], c_rate: float) -> float | None:
    """The efficiency the drawn glyphs show at a C-rate, None where nothing is drawn."""
    xs, ys = glyphs["points"]
    for x, y in zip(xs, ys):
        if c_rate == x:
            return y  # a marker
    if xs[0] < c_rate < xs[-1]:
        glyph = glyphs["inside"][0]
        i = max(k for k in range(len(xs)) if xs[k] < c_rate)
        if glyph == "line":
            return ys[i] + (ys[i + 1] - ys[i]) * (c_rate - xs[i]) / (xs[i + 1] - xs[i])
        if glyph == "step":  # bokeh step(mode="center"): changes halfway between points
            return ys[i] if c_rate < (xs[i] + xs[i + 1]) / 2 else ys[i + 1]
        return None
    for seg_x, seg_y in glyphs["outside"]:
        if seg_x[0] <= c_rate <= seg_x[1]:
            return seg_y[0]
    return None


class TestEfficiencyGlyphs:
    @pytest.mark.parametrize("interpolation", ["linear", "nearest", "none"])
    @pytest.mark.parametrize("out_of_range", ["clamp_to_nearest", "reject"])
    def test_drawn_curve_is_the_lookup_of_eos(self, interpolation, out_of_range):
        curve = make_curve(interpolation=interpolation, out_of_range_behavior=out_of_range)
        lookup = PrecomputedEfficiencyCurve.from_curve(curve).lookup
        glyphs = page.efficiency_glyphs(curve, c_rate_max=1.25)
        midpoints = {(a + b) / 2 for a, b in zip(curve.c_rate, curve.c_rate[1:])}
        samples = [round(0.005 * k, 3) for k in range(0, 251)] + list(curve.c_rate)
        for c_rate in samples:
            if c_rate in midpoints and interpolation == "nearest":
                continue  # the step is vertical there
            expected = lookup(c_rate)
            actual = drawn_value(glyphs, c_rate)
            if expected is None:
                assert actual is None, c_rate
            else:
                assert actual == pytest.approx(expected), c_rate

    def test_glyph_per_interpolation(self):
        for interpolation, glyph in (("linear", "line"), ("nearest", "step"), ("none", None)):
            curve = make_curve(interpolation=interpolation)
            assert page.efficiency_glyphs(curve, 1.25)["inside"][0] == glyph

    def test_clamped_curve_is_extended_to_both_ends(self):
        curve = make_curve(out_of_range_behavior="clamp_to_nearest")
        assert page.efficiency_glyphs(curve, 1.25)["outside"] == [
            ([0.0, 0.1], [0.90, 0.90]),
            ([1.0, 1.25], [0.91, 0.91]),
        ]

    def test_rejected_curve_is_not_extended(self):
        curve = make_curve(out_of_range_behavior="reject")
        assert page.efficiency_glyphs(curve, 1.25)["outside"] == []


class TestEfficiencyPlot:
    def test_page_shows_the_efficiency_plot(self, rest):
        html = render()
        assert "Battery efficiency versus C-rate" in html
        assert "Charge (linear, out of range clamped)" in html
        assert "Discharge (linear, out of range clamped)" in html

    def test_missing_curve_is_noted(self):
        document = {**generic_lfp(), "discharge_efficiency": None}
        html = to_xml(page.EfficiencyPlot(document))
        assert "No discharge efficiency curve" in html
        assert "Charge (linear" in html and "Discharge (linear" not in html

    def test_without_curves_there_is_no_plot(self):
        document = {**generic_lfp(), "charge_efficiency": None, "discharge_efficiency": None}
        html = to_xml(page.EfficiencyPlot(document))
        assert "Battery efficiency versus C-rate" not in html
        assert "No charge efficiency curve" in html

    def test_invalid_curve_while_editing_is_reported(self):
        document = {**generic_lfp(), "charge_efficiency": {"c_rate": [0.5, 0.1], "efficiency": [1, 1]}}
        html = to_xml(page.EfficiencyPlot(document))
        assert "Invalid charge efficiency curve" in html
        assert "Discharge (linear" in html

    def test_power_tooltip_with_a_capacity(self):
        document = {**generic_lfp(), "nominal_capacity_wh": 10240.0}
        assert "@power_w" in to_xml(page.EfficiencyPlot(document))
        assert "@power_w" not in to_xml(page.EfficiencyPlot(generic_lfp()))

    def test_dark_theme(self, rest):
        assert "#1e1e1e" in render({"dark": "true"})
        assert "#1e1e1e" not in render({"dark": "false"})


# --- limit map heat maps --------------------------------------------------


def heatmap_value(image: dict[str, Any], temperature: float, soc: float) -> float:
    """The value the heat map shows at an operating point (cell under the point)."""
    grid = image["image"]
    n_temp, n_soc = grid.shape
    row = int((temperature - image["y"]) / image["dh"] * n_temp)
    col = int((soc - image["x"]) / image["dw"] * n_soc)
    return float(grid[row, col])


class TestLimitHeatMaps:
    @pytest.mark.parametrize("attribute, lookup", [("charge_map", "max_charge_crate"), ("discharge_map", "max_discharge_crate")])
    @pytest.mark.parametrize("interpolation", ["bilinear", "nearest"])
    def test_heatmap_shows_the_lookup_of_eos(self, attribute, lookup, interpolation):
        profile = BatteryProfileSettings.model_validate(
            {**generic_lfp(), "interpolation": interpolation}
        ).to_limits()
        image = page.limit_heatmap_image(profile, attribute)
        import math

        # Not exactly on a cell edge (half a step from a grid point): EOS rounds
        # half to even there, the drawing shows the edge.
        for temperature in [t / 4 + 0.01 for t in range(-40, 240, 3)]:
            for soc in [s / 2 + 0.01 for s in range(0, 200, 7)]:
                expected = getattr(profile, lookup)(temperature, soc)
                shown = heatmap_value(image, temperature, soc)
                if expected is None:
                    assert math.isnan(shown), (temperature, soc)
                else:
                    assert shown == pytest.approx(expected), (temperature, soc)

    def test_page_shows_both_heat_maps_and_the_breakpoint_table(self, rest):
        html = render()
        assert "Charge limit [C-rate] (as evaluated by EOS)" in html
        assert "Discharge limit [C-rate] (as evaluated by EOS)" in html
        assert "Breakpoint values" in html and "degC \\ SoC %" in html

    def test_document_that_does_not_compile_is_reported(self):
        document = {**generic_lfp(), "charge_limit_crate": {"values": [[0.5]]}}
        html = to_xml(page.LimitMaps(document))
        assert "The limit maps can not be compiled" in html
        assert "(as evaluated by EOS)" not in html


class TestDarkMode:
    @staticmethod
    def color_bars(dark: bool, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
        """The color bars added to a heat map plot."""
        import bokeh.plotting
        from bokeh.models.annotations import ColorBar

        added: list[Any] = []
        original = bokeh.plotting.figure.add_layout

        def capture(self: Any, obj: Any, place: Any = "center") -> None:
            added.append(obj)
            original(self, obj, place)

        monkeypatch.setattr(bokeh.plotting.figure, "add_layout", capture)
        profile = BatteryProfileSettings.model_validate(generic_lfp()).to_limits()
        page.LimitHeatMap(
            profile, generic_lfp(), "charge_map", "charge_limit_crate", "Charge", 1.0, dark
        )
        return [obj for obj in added if isinstance(obj, ColorBar)]

    def test_color_bar_is_dark_in_dark_mode(self, monkeypatch):
        (bar,) = self.color_bars(True, monkeypatch)
        assert bar.background_fill_color == page.DARK_BACKGROUND
        assert bar.major_label_text_color == "white"
        assert bar.title_text_color == "white"

    def test_color_bar_keeps_the_light_default_in_light_mode(self, monkeypatch):
        (bar,) = self.color_bars(False, monkeypatch)
        assert bar.background_fill_color == "#ffffff"


# ---------------------------------------------------------------------------
# Inverter profiles
# ---------------------------------------------------------------------------


def generic_hybrid() -> dict[str, Any]:
    path = get_profile_store().package_dir(InverterProfileSettings) / "generic-hybrid.json"
    return json.loads(path.read_text(encoding="utf-8"))


class TestInverterProfiles:
    def test_kind_selector_on_the_battery_page(self, rest):
        html = render()
        assert 'name="kind"' in html
        assert "Inverter profiles" in html
        assert "Battery profiles" in html

    def test_inverter_kind_shows_the_generic_profile(self, rest):
        html = render({"action": "kind", "kind": "inverter"})
        assert "Inverter profiles" in html
        assert "generic-hybrid (packaged)" in html
        assert "Generic hybrid inverter" in html
        assert ("get", "/v1/profile/inverter") in rest.calls
        # Inverter views, no battery limit maps.
        assert "Inverter efficiency versus load fraction" in html
        assert "Allowed power versus ambient temperature" in html
        assert "Charge and discharge limits" not in html

    def test_summary_shows_the_power_at_45_degc(self, rest):
        html = render({"action": "kind", "kind": "inverter"})
        assert "Power at 45 degC (default temperature)" in html
        assert "100 %" in html

    def test_unknown_kind_falls_back_to_battery(self, rest):
        html = render({"kind": "heatpump"})
        assert "Battery profiles" in html
        assert "generic-lfp (packaged)" in html

    def test_save_as_stores_an_inverter_profile(self, rest):
        document = generic_hybrid()
        document["nominal_power_w"] = 8000.0
        html = render(
            {
                "action": "save_as",
                "kind": "inverter",
                "profile_name": "generic-hybrid",
                "new_name": "my-inverter",
                "document": json.dumps(document),
            }
        )
        assert "Saved inverter profile" in html
        assert ("put", "/v1/profile/inverter/my-inverter") in rest.calls
        stored = get_profile_store().load(InverterProfileSettings, "my-inverter")
        assert stored.nominal_power_w == 8000.0

    def test_preview_validates_as_inverter_profile(self, rest):
        document = generic_hybrid()
        document["dc_to_ac_efficiency"]["efficiency"][0] = 1.5
        html = render(
            {
                "action": "preview",
                "kind": "inverter",
                "profile_name": "generic-hybrid",
                "document": json.dumps(document),
            }
        )
        assert "the document is invalid" in html
        assert ("put", "/v1/profile/inverter/generic-hybrid") not in rest.calls

    def test_actions_carry_the_kind(self, rest):
        html = render({"action": "kind", "kind": "inverter"})
        assert "&quot;kind&quot;: &quot;inverter&quot;" in html or '"kind": "inverter"' in html

    def test_without_derating_the_full_power_is_noted(self):
        document = generic_hybrid()
        del document["power_derating"]
        html = to_xml(page.DeratingPlot(document))
        assert "No power derating" in html

    def test_invalid_curves_while_editing_are_reported(self):
        document = generic_hybrid()
        document["ac_to_dc_efficiency"] = {"load_fraction": [0.5, 0.1], "efficiency": [0.9, 0.9]}
        document["power_derating"] = {"temperature_degC": [60.0, 40.0], "power_fraction": [1, 1]}
        assert "Invalid AC→DC (grid charge) efficiency curve" in to_xml(
            page.InverterEfficiencyPlot(document)
        )
        assert "Invalid power derating" in to_xml(page.DeratingPlot(document))
