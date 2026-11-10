"""Tests for the EOSdash battery profile page.

The page talks to EOS by REST. Its requests are sent to the real profile REST
router (on a fresh profile store), so the tests cover page, API and store.
"""

import json
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fasthtml.common import to_xml
from monsterui.franken import ButtonT

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


def render_editing(profile_name: str = "generic-lfp", **data: Any) -> str:
    """Render the page in edit mode (after the Edit button)."""
    return render({"action": "edit", "profile_name": profile_name, **data})


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
        assert "Charge limit [C-rate]" in html and "Discharge limit [C-rate]" in html
        # The editor is shown only in edit mode.
        assert 'name="document"' not in html
        assert ">Edit<" in html
        assert "read only" in render_editing()  # packaged hint of the editor

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
        assert ">Delete<" in render_editing("my-battery")

    def test_unreachable_eos_is_reported(self, rest, monkeypatch):
        def fail(url: str, **kwargs: Any) -> Any:
            raise ConnectionError("refused")

        monkeypatch.setattr(rest, "get", fail)
        html = render()
        assert "Can not retrieve battery profiles" in html and "refused" in html
        # Same toolbar layout as the normal page: the kind selection stays usable.
        assert 'aria-label="Profile kind"' in html
        assert html.index('name="kind"') < html.index("Can not retrieve battery profiles")


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

    @pytest.mark.parametrize(
        ("new_name", "message"),
        [("vendor/model", "Invalid profile name"), ("C:evil", "Invalid profile name"),
         ("schema", "is reserved")],
    )
    def test_save_as_with_an_invalid_name_is_reported_without_request(self, rest, new_name, message):
        """The name rule of the store is checked before EOS is asked to save."""
        html = render(
            {
                "action": "save_as",
                "profile_name": "generic-lfp",
                "document": json.dumps(generic_lfp()),
                "new_name": new_name,
            }
        )
        assert message in html
        assert not any(method == "put" for method, _ in rest.calls)

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



class TestJsonView:
    """The profile as highlighted JSON, which EOSdash gives a copy button."""

    @staticmethod
    def json_block(html: str) -> str:
        """Return the content of the highlighted JSON code block."""
        start = html.index('<code class="language-json">') + len('<code class="language-json">')
        return html[start : html.index("</code>", start)]

    def test_stored_profile_is_shown_as_json(self, rest):
        html = render()
        assert "Stored profile &#x27;generic-lfp&#x27; as JSON" in html or (
            "Stored profile 'generic-lfp' as JSON" in html
        )
        block = self.json_block(html)
        assert "battery_model" in block
        assert generic_lfp()["battery_model"] in block

    def test_preview_shows_the_edited_document_as_json(self, rest):
        document = {**generic_lfp(), "battery_model": "Edited Model"}
        html = render(
            {"action": "preview", "profile_name": "generic-lfp", "document": json.dumps(document)}
        )
        assert "as JSON (not saved)" in html
        assert "Edited Model" in self.json_block(html)


class TestShareOnGitHub:
    """Share a profile as a GitHub issue with the profile form of its kind."""

    # (kind, packaged profile name)
    KINDS = [("battery", "generic-lfp"), ("inverter", "generic-hybrid")]

    @staticmethod
    def document(kind: str) -> dict[str, Any]:
        return generic_lfp() if kind == "battery" else generic_hybrid()

    @staticmethod
    def query(url: str) -> dict[str, str]:
        from urllib.parse import parse_qs, urlsplit

        parts = urlsplit(url)
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == page.SHARE_ISSUE_URL
        return {key: values[0] for key, values in parse_qs(parts.query).items()}

    @pytest.mark.parametrize(("kind", "name"), KINDS)
    def test_url_fills_in_the_issue_form(self, kind, name):
        document = self.document(kind)
        url, includes_document = page.share_issue_url(kind, name, document)
        assert includes_document
        assert len(url) <= page.SHARE_URL_MAX_LENGTH
        query = self.query(url)
        model = document[f"{kind}_model"]
        assert query["template"] == f"{kind}-profile.yml"
        assert query["profile_name"] == name
        assert query[f"{kind}_model"] == model
        assert query["title"] == f"[PROFILE]: {model}"
        assert json.loads(query["profile"]) == document

    @pytest.mark.parametrize(("kind", "name"), KINDS)
    def test_url_fields_match_the_issue_form(self, kind, name):
        """The URL parameters are ids of fields in the issue form of the kind."""
        import yaml

        template = page.SHARE_ISSUE_TEMPLATE.format(kind=kind)
        form_path = Path(__file__).parent.parent / ".github" / "ISSUE_TEMPLATE" / template
        form = yaml.safe_load(form_path.read_text(encoding="utf-8"))
        field_ids = {item.get("id") for item in form["body"]}
        query = self.query(page.share_issue_url(kind, name, self.document(kind))[0])
        assert set(query) - {"template", "title"} <= field_ids

    def test_large_profile_is_left_out(self, monkeypatch):
        monkeypatch.setattr(page, "SHARE_URL_MAX_LENGTH", 600)
        url, includes_document = page.share_issue_url("battery", "generic-lfp", generic_lfp())
        assert not includes_document
        assert "profile" not in self.query(url)
        assert len(url) <= 600

    def test_compact_json_is_used_if_indented_is_too_long(self, monkeypatch):
        document = generic_lfp()
        indented, _ = page.share_issue_url("battery", "generic-lfp", document)
        monkeypatch.setattr(page, "SHARE_URL_MAX_LENGTH", len(indented) - 1)
        url, includes_document = page.share_issue_url("battery", "generic-lfp", document)
        assert includes_document
        assert json.loads(self.query(url)["profile"]) == document

    def test_page_links_to_the_issue(self, rest):
        html = render()
        link = html[html.rindex("<a ", 0, html.index(page.SHARE_ISSUE_URL)) :]
        link = link[: link.index("</a>")]
        assert ">Share" in link
        assert 'target="_blank"' in link
        assert 'title="Opens a GitHub issue' in link

    def test_inverter_page_shares_with_the_inverter_form(self, rest):
        html = render({"action": "kind", "kind": "inverter"})
        assert "inverter-profile.yml" in html
        assert "battery-profile.yml" not in html


class TestLayout:
    """Share button at the top, collapsible sections for the details."""

    @staticmethod
    def section(html: str, title: str) -> str:
        """Return the opening <details> tag of the section with the title."""
        end = html.index(title)
        start = html.rindex("<details", 0, end)
        return html[start : html.index(">", start) + 1]

    def test_share_button_is_in_the_profile_selection(self, rest):
        html = render()
        share = html.index(page.SHARE_ISSUE_URL)
        assert html.index(">Edit<") < share
        assert share < html.index("as JSON")
        assert share < html.index("Charge and discharge limits")
        # Styled like the other buttons of EOSdash.
        assert str(ButtonT.primary) in html[html.rindex("<a ", 0, share) : html.index(">", share)]

    def test_no_editor_without_edit_mode(self, rest):
        """Without editing the editor takes no space, not even a collapsed line."""
        html = render()
        assert 'name="document"' not in html
        assert 'data-profile-section="editor"' not in html

    @pytest.mark.parametrize("action", ["edit", "preview", "save"])
    def test_editor_is_shown_after_edit_preview_and_save(self, rest, action):
        html = render(
            {
                "action": action,
                "profile_name": "generic-lfp",
                "document": json.dumps(generic_lfp()),
                "open_sections": "",
            }
        )
        assert 'name="document"' in html
        assert ">Close editor<" in html

    def test_editor_stays_open_on_a_profile_change(self, rest):
        html = render({"action": "select", "profile_name": "generic-lfp", "open_sections": "editor"})
        assert 'name="document"' in html

    @pytest.mark.parametrize("open_sections", ["json", "editor,json"])
    def test_close_ends_edit_mode(self, rest, open_sections):
        """Close in the editor and Close editor in the toolbar end edit mode."""
        html = render(
            {"action": "close", "profile_name": "generic-lfp", "open_sections": open_sections}
        )
        assert 'name="document"' not in html
        assert ">Edit<" in html

    def test_edit_button_sends_the_selected_profile(self, rest):
        html = render()
        button = html[html.rindex("<button", 0, html.index(">Edit<")) :]
        button = button[: button.index(">")]
        assert f'hx-include="#{page.PROFILE_SELECT_ID}"' in button
        assert f'id="{page.PROFILE_SELECT_ID}"' in html

    def test_editor_leaves_room_for_a_plot(self, rest):
        """The text area takes 40 % of the window height, not the whole window."""
        html = render_editing()
        area = html[html.rindex("<textarea", 0, html.index('name="document"')) :]
        assert "height: 40vh" in area[: area.index(">")]

    def test_summary_has_lines_between_all_cells(self, rest):
        """Cell borders draw lines also between wrapped rows, on the tab background."""
        html = render()
        summary = html[: html.index("Charge temperature")]
        grid = summary[summary.rindex('<div class="grid') :]
        grid_tag = grid[: grid.index(">")]
        cell_tag = grid[grid.index("<div", 1) :]
        cell_tag = cell_tag[: cell_tag.index(">")]
        # Full cell borders that overlap into single lines; no empty columns.
        assert "auto-fit" in grid_tag
        assert " border " in f"{cell_tag} " and "-mt-px" in cell_tag and "-ml-px" in cell_tag
        # No own background: the tab background shows (bg-background is black in dark mode).
        assert "bg-" not in grid_tag and "bg-" not in cell_tag

    def test_editor_action_bar_has_no_own_background(self, rest):
        html = render_editing()
        save = html.index("Save as")
        bar = html[html.rindex('<div class="flex flex-wrap items-center gap-2"', 0, save) :]
        assert "bg-" not in bar[: bar.index(">")]

    def test_selections_have_accessible_names(self, rest):
        html = render()
        assert 'aria-label="Profile kind"' in html
        assert 'aria-label="Battery profile"' in html

    def test_large_profile_shows_the_paste_hint_as_text(self, rest, monkeypatch):
        """Tooltips are not shown on touch devices: the paste instruction is visible text."""
        monkeypatch.setattr(page, "SHARE_URL_MAX_LENGTH", 600)
        html = render()
        assert f'<p class="{page.HINT_CLS}">The profile is too large to include' in html

    def test_saved_profiles_apply_at_once_is_explained(self, rest):
        assert "saved profiles apply at once, also to running device controllers" in render_editing()

    def test_toolbar_has_no_heading_and_starts_with_the_selection(self, rest):
        """The selections name the profile kind; no extra heading."""
        html = render()
        assert "<h3" not in html
        assert html.index('name="kind"') < html.index('name="profile_name"')
        assert html.index('name="profile_name"') < html.index(page.SHARE_ISSUE_URL)

    def test_status_follows_the_share_button(self, rest):
        html = render(
            {
                "action": "preview",
                "profile_name": "generic-lfp",
                "document": json.dumps(generic_lfp()),
            }
        )
        share = html.index(page.SHARE_ISSUE_URL)
        status = html.index("Preview of unsaved changes")
        assert share < status < html.index("Edit ")

    @pytest.mark.parametrize("title", ["Charge and discharge limits", "Efficiency"])
    def test_plot_sections_are_open(self, rest, title):
        assert " open" in self.section(render(), title)

    def test_hints_use_the_muted_text_color_of_the_theme(self, rest):
        """Readable in light and dark mode: no opacity on an inherited text color."""
        html = render_editing()
        assert "opacity-70" not in html
        hint = html.index("Preview updates the summary and plots without saving")
        assert "text-muted-foreground" in html[html.rindex("<p ", 0, hint) : hint]

    def test_json_section_is_last(self, rest):
        html = render()
        assert html.index("as JSON") > html.index("Efficiency")
        assert html.index("as JSON") > html.index("Charge and discharge limits")

    def test_preview_keeps_closed_sections_closed(self, rest):
        """The page reports the open sections with the action; a preview keeps them."""
        html = render(
            {
                "action": "preview",
                "profile_name": "generic-lfp",
                "document": json.dumps(generic_lfp()),
                "open_sections": "",
            }
        )
        assert " open" not in self.section(html, "Charge and discharge limits")
        assert " open" not in self.section(html, "Efficiency")
        assert " open" not in self.section(html, "as JSON")

    def test_selection_keeps_the_open_sections(self, rest):
        html = render(
            {"action": "select", "profile_name": "generic-lfp", "open_sections": "json,limits"}
        )
        assert " open" in self.section(html, "as JSON")
        assert " open" in self.section(html, "Charge and discharge limits")
        assert " open" not in self.section(html, "Efficiency")

    def test_actions_report_the_open_sections(self, rest):
        html = render()
        assert "open_sections" in html
        assert 'data-profile-section="limits"' in html

    def test_json_section_is_closed(self, rest):
        assert " open" not in self.section(render(), "as JSON")


class TestEfficiencyHints:
    def test_missing_curve_is_reported_as_required(self, rest):
        document = {**generic_lfp()}
        del document["discharge_efficiency"]
        html = render(
            {"action": "preview", "profile_name": "generic-lfp", "document": json.dumps(document)}
        )
        assert "the profile requires &#x27;discharge_efficiency&#x27;" in html or (
            "the profile requires 'discharge_efficiency'" in html
        )
        assert "battery settings applies" not in html


class TestPreview:
    def test_preview_draws_the_edited_text_without_saving(self, rest):
        document = {**generic_lfp(), "battery_model": "Edited Model", "nominal_capacity_wh": 7777.0}
        html = render(
            {"action": "preview", "profile_name": "generic-lfp", "document": json.dumps(document)}
        )
        assert "Preview of unsaved changes" in html
        assert ">Edited Model</p>" in html  # summary of the edited document
        assert ">7777.0 Wh</p>" in html
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
        html = render_editing()
        assert "&quot;preview&quot;" in html or '"preview"' in html
        assert "Preview updates the summary and plots without saving" in html


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
        assert "Power at 45 degC" in html
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


class TestInverterLayout:
    """The inverter profiles use the same collapsible sections as the battery profiles."""

    @staticmethod
    def section(html: str, title: str) -> str:
        end = html.index(title)
        start = html.rindex("<details", 0, end)
        return html[start : html.index(">", start) + 1]

    def test_sections_are_collapsible_with_ids(self, rest):
        html = render({"action": "kind", "kind": "inverter"})
        for section_id in ("efficiency", "derating", "json"):
            assert f'data-profile-section="{section_id}"' in html
        editing = render(
            {"action": "edit", "kind": "inverter", "profile_name": "generic-hybrid"}
        )
        assert 'data-profile-section="editor"' in editing
        assert html.index("as JSON") > html.index("Power derating")
        assert "opacity-70" not in html

    def test_closed_sections_stay_closed(self, rest):
        html = render(
            {
                "action": "select",
                "kind": "inverter",
                "profile_name": "generic-hybrid",
                "open_sections": "json",
            }
        )
        assert " open" not in self.section(html, "Efficiency")
        assert " open" not in self.section(html, "Power derating")
        assert " open" in self.section(html, "as JSON")
