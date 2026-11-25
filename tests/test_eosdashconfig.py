"""Test suite for the EOS Dash configuration module.

This module contains tests for utility functions related to retrieving and processing
configuration data using Pydantic models.
"""

import json
import re
from pathlib import Path
from typing import Iterator, Literal, Optional, Union
from unittest.mock import patch

import pytest
from fasthtml.common import to_xml
from monsterui.core import FastHTML
from pydantic import ValidationError
from pydantic.fields import FieldInfo
from starlette.testclient import TestClient

from akkudoktoreos.adapter.control import ControlAdapterCommonSettings
from akkudoktoreos.adapter.controllers.byd.bydbatterybox import BydBatteryBoxSettings
from akkudoktoreos.adapter.controllers.sma.smaenergymeter import SmaEnergyMeterSettings
from akkudoktoreos.adapter.controllers.sma.smastpe import SmaStpeSettings
from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.prediction.pvforecast import PVForecastPlaneSetting
from akkudoktoreos.server.dash.carditems import optional_model_is_on
from akkudoktoreos.server.dash.components import make_config_update_optional_model_form
from akkudoktoreos.server.dash.configuration import (
    config_card,
    create_config_details,
    get_default_value,
    get_nested_value,
    resolve_nested_types,
)
from akkudoktoreos.server.dash.uihints import (
    UI_HINTS,
    UiHint,
    discriminated_item_models,
    hint_for_indexed_field,
    optional_model_rows,
    resolve_entry_model,
    resolve_form_factory,
    resolve_item_model,
)

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")

FILE_TESTDATA_EOSSERVER_CONFIG_1 = DIR_TESTDATA.joinpath("eosserver_config_1.json")


class SampleModel(PydanticBaseModel):
    field1: str = "default_value"
    field2: int = 10


class TestEOSdashConfig:
    """Test case for EOS Dash configuration utility functions.

    This class tests functions for retrieving nested values, extracting default values,
    resolving nested types, and generating configuration details from Pydantic models.
    """

    def test_get_nested_value_from_dict(self):
        """Test retrieving a nested value from a dictionary using a sequence of keys."""
        data = {"a": {"b": {"c": 42}}}
        assert get_nested_value(data, ["a", "b", "c"]) == 42
        assert get_nested_value(data, ["a", "x"], default="not found") == "not found"
        with pytest.raises(TypeError):
            get_nested_value("not_a_dict", ["a"])  # type: ignore

    def test_get_nested_value_from_list(self):
        """Test retrieving a nested value from a list using a sequence of keys."""
        data = {"a": {"b": {"c": [42]}}}
        assert get_nested_value(data, ["a", "b", "c", 0]) == 42
        assert get_nested_value(data, ["a", "b", "c", "0"]) == 42

    def test_get_default_value(self):
        """Test retrieving the default value of a field based on FieldInfo metadata."""
        field_info = FieldInfo(default="test_value")
        assert get_default_value(field_info, True) == "test_value"
        field_info_no_default = FieldInfo()
        assert get_default_value(field_info_no_default, True) == ""
        assert get_default_value(field_info, False) == "N/A"

    def test_resolve_nested_types(self):
        """Test resolving nested types within a field, ensuring correct type extraction."""
        nested_types = resolve_nested_types(Union[int, str], [])
        assert (int, []) in nested_types
        assert (str, []) in nested_types

    def test_configuration(self):
        """Test extracting configuration details from a Pydantic model based on provided values."""
        values = {"field1": "custom_value", "field2": 20}
        config_details = create_config_details(SampleModel, values)

        assert any(
            item["name"] == "field1" and item["value"] == '"custom_value"'
            for key, item in config_details.items()
        )
        assert any(
            item["name"] == "field2" and item["value"] == "20"
            for key, item in config_details.items()
        )

    def test_configuration_eos(self, config_eos):
        """Test extracting EOS configuration details from EOS config based on provided values."""
        with FILE_TESTDATA_EOSSERVER_CONFIG_1.open("r", encoding="utf-8", newline=None) as fd:
            values = json.load(fd)
        config_details = create_config_details(type(config_eos), values)
        assert any(
            item["name"] == "server.eosdash_port" and item["value"] == "8504"
            for key, item in config_details.items()
        )
        assert any(
            item["name"] == "server.eosdash_host" and item["value"] == '"127.0.0.1"'
            for key, item in config_details.items()
        )

    def test_configuration_pvforecast_plane_settings(self):
        """Test extracting EOS PV forecast plane configuration details from EOS config based on provided values."""
        with FILE_TESTDATA_EOSSERVER_CONFIG_1.open("r", encoding="utf-8", newline=None) as fd:
            values = json.load(fd)
        config_details = create_config_details(
            PVForecastPlaneSetting, values, values_prefix=["pvforecast", "planes", "0"]
        )
        assert any(
            item["name"] == "pvforecast.planes.0.surface_azimuth" and item["value"] == "170"
            for key, item in config_details.items()
        )
        assert any(
            item["name"] == "pvforecast.planes.0.userhorizon"
            and item["value"] == "[20, 27, 22, 20]"
            for key, item in config_details.items()
        )


# ---------------------------------------------------------------------------
# Device controllers: map of a discriminated union of settings classes
# ---------------------------------------------------------------------------

CONTROLLERS = "adapter.control.controllers"

STPE_SETTINGS = {
    "controller_type": "sma:sunny-tripower-smart-energy",
    "host": "192.168.1.6",
    "battery_id": "battery1",
    "grid_power_key": "meter1:grid_net_w",
}
METER_SETTINGS = {"controller_type": "sma:energy-meter", "serial": 1900123456}


def _controller_config_details(
    config_eos: ConfigEOS,
    controllers: dict[str, dict],
    batteries: Optional[dict[str, dict]] = None,
) -> dict[str, dict]:
    """Config details of the EOS config with the given controllers and battery map.

    The values are not validated by ``ConfigEOS``, so an invalid controller can be shown, too.
    """
    values = config_eos.model_dump(mode="json")
    try:
        control = ControlAdapterCommonSettings.model_validate({"controllers": controllers})
        values["adapter"]["control"] = control.model_dump(mode="json")
    except ValidationError:
        values["adapter"]["control"] = {"controllers": controllers, "shared_data_keys": []}
    values["devices"]["batteries"] = batteries or {}
    return create_config_details(type(config_eos), values)


class _KindA(PydanticBaseModel):
    kind: Literal["a"] = "a"


class _KindWithoutDefault(PydanticBaseModel):
    kind: Literal["b"]


class _KindNoLiteral(PydanticBaseModel):
    kind: str = "c"


def _eosdash_app() -> FastHTML:
    """Return the EOSdash app, imported lazily.

    Importing ``eosdash`` initializes the configuration. At collection time no test
    configuration directory is set yet, so the configuration file would be written to the
    working directory.

    Returns:
        FastHTML: The EOSdash app.
    """
    from akkudoktoreos.server import eosdash

    return eosdash.app


def _render_controllers(config_details: dict[str, dict]) -> str:
    card = config_card(config_details[CONTROLLERS], config_details)
    return to_xml(card)


class TestControllerConfigCard:
    """The controllers map renders one card per controller with the fields of its type."""

    def test_entry_models_by_controller_type(self):
        models = discriminated_item_models(UI_HINTS[CONTROLLERS])
        assert models == {
            "sma:energy-meter": SmaEnergyMeterSettings,
            "sma:sunny-tripower-smart-energy": SmaStpeSettings,
            "byd:battery-box-premium": BydBatteryBoxSettings,
        }
        assert resolve_entry_model(UI_HINTS[CONTROLLERS], METER_SETTINGS) is SmaEnergyMeterSettings
        assert resolve_entry_model(UI_HINTS[CONTROLLERS], {"controller_type": "x:y"}) is None
        assert resolve_entry_model(UI_HINTS[CONTROLLERS], None) is None

    def test_entry_models_reject_a_union_member_without_model(self):
        hint = UiHint(
            form="map_items",
            item_path="some.map",
            item_discriminator="kind",
            item_model=Union[_KindA, int],
        )
        with pytest.raises(ValueError, match="no Pydantic model"):
            discriminated_item_models(hint)

    def test_entry_models_take_the_type_from_the_literal(self):
        # With or without a default: the controller types have none (they are always saved).
        hint = UiHint(
            form="map_items",
            item_path="some.map",
            item_discriminator="kind",
            item_model=Union[_KindA, _KindWithoutDefault],
        )
        assert discriminated_item_models(hint) == {"a": _KindA, "b": _KindWithoutDefault}

    def test_entry_models_reject_a_discriminator_without_literal(self):
        hint = UiHint(
            form="map_items",
            item_path="some.map",
            item_discriminator="kind",
            item_model=Union[_KindA, _KindNoLiteral],
        )
        with pytest.raises(ValueError, match="needs a Literal of one str"):
            discriminated_item_models(hint)

    def test_entry_without_discriminator_uses_item_model(self):
        hint = UI_HINTS["devices.batteries"]
        assert resolve_entry_model(hint, {}) is resolve_item_model(hint)
        assert discriminated_item_models(hint) == {}

    def test_each_entry_shows_the_fields_of_its_type(self, config_eos):
        html = _render_controllers(
            _controller_config_details(
                config_eos, {"inv1": STPE_SETTINGS, "meter1": METER_SETTINGS}
            )
        )
        assert f"{CONTROLLERS}.inv1.host" in html
        assert f"{CONTROLLERS}.inv1.pv_curtailment" in html
        assert f"{CONTROLLERS}.inv1.serial" not in html
        assert f"{CONTROLLERS}.meter1.serial" in html
        assert f"{CONTROLLERS}.meter1.host" not in html

    def test_controller_type_is_read_only(self, config_eos):
        html = _render_controllers(_controller_config_details(config_eos, {"inv1": STPE_SETTINGS}))
        # Other fields of the entry can be updated, the controller type cannot.
        assert f'value="{CONTROLLERS}.inv1.port"' in html
        assert f'value="{CONTROLLERS}.inv1.controller_type"' not in html

    def test_battery_id_selects_a_configured_battery(self, config_eos):
        html = _render_controllers(
            _controller_config_details(
                config_eos, {"inv1": STPE_SETTINGS}, batteries={"battery1": {}, "battery2": {}}
            )
        )
        select = _select_html(html, "adapter-control-controllers-inv1-battery_id-value-select")
        assert 'value="battery1"' in select
        assert 'value="battery2"' in select

    def test_battery_id_without_batteries_is_text_input(self, config_eos):
        html = _render_controllers(_controller_config_details(config_eos, {"inv1": STPE_SETTINGS}))
        assert "adapter-control-controllers-inv1-battery_id-value-select" not in html
        assert f'value="{CONTROLLERS}.inv1.battery_id"' in html

    def test_grid_power_key_selects_a_shared_data_key(self, config_eos):
        html = _render_controllers(
            _controller_config_details(
                config_eos, {"inv1": STPE_SETTINGS, "meter1": METER_SETTINGS}
            )
        )
        select = _select_html(html, "adapter-control-controllers-inv1-grid_power_key-value-select")
        assert 'value="meter1:grid_net_w"' in select
        assert 'value="inv1:bat_soc_percent"' in select

    def test_add_entry_asks_for_the_type_and_its_required_fields(self, config_eos):
        html = _render_controllers(_controller_config_details(config_eos, {}))
        select = _select_html(html, "adapter-control-controllers-new-type")
        assert 'value="sma:energy-meter"' in select
        assert 'value="sma:sunny-tripower-smart-energy"' in select

        fieldsets = re.findall(r"<fieldset[^>]*>.*?</fieldset>", html, flags=re.S)
        by_type = {
            re.search(r'data-entry-type="([^"]+)"', fs).group(1): fs  # type: ignore[union-attr]
            for fs in fieldsets
        }
        meter, stpe = by_type["sma:energy-meter"], by_type["sma:sunny-tripower-smart-energy"]
        # The first type is selected: its required fields are enabled, the others not.
        assert "disabled" not in meter.split(">", 1)[0]
        assert "disabled" in stpe.split(">", 1)[0]
        assert 'placeholder="serial"' in meter
        # The type select sets the type, it is no required input (it has no default).
        assert 'placeholder="controller_type"' not in html
        for field_name in ("host", "battery_id", "grid_power_key"):
            assert f'placeholder="{field_name}"' in stpe

    def test_add_entry_checks_the_form_before_the_request(self, config_eos):
        html = _render_controllers(_controller_config_details(config_eos, {}))
        add = _html_from_id_to_form_end(html, "adapter-control-controllers-add-entry")
        button = re.search(r"<button[^>]*>\s*<uk-icon icon=\"plus\"", add, flags=re.S)
        assert button
        # htmx sends nothing while a required input (e.g. the name) is empty; the browser shows why.
        assert 'hx-validate="true"' in button.group(0)
        assert 'onclick="this.form.reportValidity()"' in button.group(0)

    def test_add_entry_is_laid_out_like_the_other_settings(self, config_eos):
        html = _render_controllers(_controller_config_details(config_eos, {}))
        add = _html_from_id_to_form_end(html, "adapter-control-controllers-add-entry")
        # The label on the left, the form with all inputs on the right
        assert add.index(">add entry</p>") < add.index("<form")
        form = add[add.index("<form") :]
        assert form.index("name (required)") < form.index('id="adapter-control-controllers-new-key"')
        assert form.index(">controller_type</p>") < form.index(
            'id="adapter-control-controllers-new-type"'
        )

    def test_open_name_expands_the_controller_card(self, config_eos):
        config_details = _controller_config_details(
            config_eos, {"inv1": STPE_SETTINGS, "meter1": METER_SETTINGS}
        )
        html = to_xml(
            config_card(config_details[CONTROLLERS], config_details, f"{CONTROLLERS}.inv1")
        )
        assert _details_is_open(html, f"{CONTROLLERS}.inv1")
        assert not _details_is_open(html, f"{CONTROLLERS}.meter1")
        # The outer card of the map is open, too.
        assert html.lstrip().startswith("<div") and "<details" in html
        assert " open" in html[html.index("<details") : html.index(">", html.index("<details"))]

    def test_unknown_controller_type_shows_the_entry_as_json(self, config_eos):
        html = _render_controllers(
            _controller_config_details(config_eos, {"bad1": {"controller_type": "x:y"}})
        )
        assert "Unknown controller_type" in html
        assert f"{CONTROLLERS}.bad1.host" not in html

    def test_select_without_options_falls_back_to_text(self):
        config_details = {"some.list": {"value": "[]"}, "some.map": {"value": '{"a": 1}'}}
        assert (
            resolve_form_factory(UiHint(form="select", options_from="some.list"), config_details)
            is None
        )
        assert resolve_form_factory(UiHint(form="select", options_from="some.map"), config_details)

    def test_entry_options_without_values_fall_back_to_free_text(self):
        hint = UiHint(form="map", options_from_entry="keys")
        details = {"some.map.a.keys": {"value": "[]"}}
        form = resolve_form_factory(hint, details, entry_name="some.map.a")
        assert form is not None
        html = to_xml(form("some.map.a.sources", "{}"))
        assert not re.search(r'<select[^>]*name="some-map-a-sources_set_value"', html)
        assert re.search(r'<input[^>]*name="some-map-a-sources_set_value"', html)

    def test_entry_options_take_precedence_over_options_from(self):
        hint = UiHint(form="select", options_from="global.keys", options_from_entry="keys")
        details = {
            "global.keys": {"value": '["global"]'},
            "some.map.a.keys": {"value": '["own"]'},
        }
        form = resolve_form_factory(hint, details, entry_name="some.map.a")
        assert form is not None
        html = to_xml(form("some.map.a.choice", '"own"'))
        assert 'value="own"' in html
        assert 'value="global"' not in html
        # Without an entry the hint falls back to options_from
        form = resolve_form_factory(hint, details)
        assert form is not None
        assert 'value="global"' in to_xml(form("x.choice", '"global"'))

    def test_pv_curtailment_off_shows_switch_on_and_hides_its_fields(self, config_eos):
        html = _render_controllers(_controller_config_details(config_eos, {"inv1": STPE_SETTINGS}))
        assert "adapter-control-controllers-inv1-pv_curtailment-switch-on" in html
        assert "adapter-control-controllers-inv1-pv_curtailment-switch-off" not in html
        assert f"{CONTROLLERS}.inv1.pv_curtailment.rated_power_w" not in html
        # Switching on writes the defaults of the model
        switch_on = _form_html_by_id(
            html, "adapter-control-controllers-inv1-pv_curtailment-switch-on"
        )
        assert f'key: "{CONTROLLERS}.inv1.pv_curtailment"' in switch_on
        assert '"deadband_w": 50.0' in switch_on

    def test_pv_curtailment_on_shows_its_fields_and_a_confirmed_switch_off(self, config_eos):
        settings = dict(STPE_SETTINGS, pv_curtailment={"rated_power_w": 10000})
        html = _render_controllers(_controller_config_details(config_eos, {"inv1": settings}))
        assert f"{CONTROLLERS}.inv1.pv_curtailment.rated_power_w" in html
        assert "adapter-control-controllers-inv1-pv_curtailment-switch-on" not in html
        switch_off = _details_html_by_id(
            html, "adapter-control-controllers-inv1-pv_curtailment-switch-off"
        )
        # The first click only opens the confirm button, the second one writes null.
        summary = switch_off[: switch_off.index("</summary>")]
        assert "hx-put" not in summary
        confirm = switch_off[switch_off.index("</summary>") :]
        assert "Confirm switch off" in confirm
        assert f'key: "{CONTROLLERS}.inv1.pv_curtailment", value: "null"' in confirm

    def test_entry_header_shows_the_type_and_hides_the_json(self, config_eos):
        html = _render_controllers(
            _controller_config_details(config_eos, {"meter1": METER_SETTINGS})
        )
        summary = _summary_html(html, f"{CONTROLLERS}.meter1")
        assert "sma:energy-meter" in summary
        assert "<pre" not in summary
        json_section = _details_html_by_id(html, "adapter-control-controllers-meter1-json")
        assert " open" not in json_section.split(">", 1)[0]
        assert "1900123456" in json_section

    def test_raw_json_update_is_in_a_collapsed_advanced_section(self, config_eos):
        html = _render_controllers(
            _controller_config_details(config_eos, {"meter1": METER_SETTINGS})
        )
        advanced = _details_html_by_id(html, "adapter-control-controllers-advanced")
        assert " open" not in advanced.split(">", 1)[0]
        assert f'value="{CONTROLLERS}"' in advanced  # the raw update form
        outer_summary = html[html.index("<summary") : html.index("</summary>")]
        assert "<pre" not in outer_summary

    def test_advanced_section_opens_after_a_failed_update(self, config_eos):
        config_details = _controller_config_details(config_eos, {"meter1": METER_SETTINGS})
        latest = {CONTROLLERS: {"error": "Can not set: boom", "value": "{}", "open": True}}
        with patch.dict(
            "akkudoktoreos.server.dash.configuration.config_update_latest", latest, clear=True
        ):
            html = _render_controllers(config_details)
        advanced = _details_html_by_id(html, "adapter-control-controllers-advanced")
        assert " open" in advanced.split(">", 1)[0]
        # The error is shown above the section, visible while it is collapsed, too.
        assert html.index("boom") < html.index('id="adapter-control-controllers-advanced"')

    def test_advanced_section_stays_closed_after_a_later_update(self, config_eos):
        config_details = _controller_config_details(config_eos, {"meter1": METER_SETTINGS})
        latest = {
            CONTROLLERS: {"error": "Can not set: boom", "value": "{}", "open": False},
            "general.latitude": {"error": None, "value": "52.5", "open": True},
        }
        with patch.dict(
            "akkudoktoreos.server.dash.configuration.config_update_latest", latest, clear=True
        ):
            html = _render_controllers(config_details)
        advanced = _details_html_by_id(html, "adapter-control-controllers-advanced")
        assert " open" not in advanced.split(">", 1)[0]
        assert "boom" in html  # the error is still shown

    def test_switch_shows_the_current_state_after_a_failed_switch_on(self, config_eos):
        config_details = _controller_config_details(config_eos, {"inv1": STPE_SETTINGS})
        name = f"{CONTROLLERS}.inv1.pv_curtailment"
        latest = {name: {"error": "Can not set: boom", "value": '{"hold_s": 60}', "open": True}}
        with patch.dict(
            "akkudoktoreos.server.dash.configuration.config_update_latest", latest, clear=True
        ):
            html = _render_controllers(config_details)
        # Still off: switching on can be retried, the fields stay hidden.
        assert "adapter-control-controllers-inv1-pv_curtailment-switch-on" in html
        assert "adapter-control-controllers-inv1-pv_curtailment-switch-off" not in html
        assert f"{name}.hold_s" not in html


class TestOptionalModelSwitch:
    """Optional sub-models below the top level get an on/off switch."""

    @pytest.mark.parametrize(
        ("value", "is_on"),
        [("null", False), ('"<unknown>"', False), ("not json", False), ("{}", True)],
    )
    def test_optional_model_is_on(self, value, is_on):
        assert optional_model_is_on(value) is is_on

    def test_optional_sub_model_gets_a_row_of_its_own(self, config_eos):
        entry = SmaStpeSettings.model_validate(STPE_SETTINGS).model_dump(mode="json")
        values = {"adapter": {"control": {"controllers": {"inv1": entry}}}}
        details = create_config_details(
            SmaStpeSettings, values, values_prefix=CONTROLLERS.split(".") + ["inv1"]
        )
        row = details[f"{CONTROLLERS}.inv1.pv_curtailment"]
        assert row["optional-model"].__name__ == "SmaPvCurtailmentSettings"
        assert row["value"] == "null"
        assert row["read-only"] == "rw"
        assert f"{CONTROLLERS}.inv1.pv_curtailment.rated_power_w" in details
        # Top-level groups are optional in the model only: no row, no switch
        details = _controller_config_details(config_eos, {})
        assert "general" not in details
        assert "devices" not in details

    def test_rows_switched_and_hidden(self):
        details: dict[str, dict] = {
            "a.on": {"value": '{"x": 1}', "optional-model": SampleModel},
            "a.on.x": {"value": "1"},
            "a.off": {"value": "null", "optional-model": SampleModel},
            "a.off.x": {"value": '"<unknown>"'},
            "a.offset": {"value": "1"},  # shares the prefix "a.off" but is no sub-field
            "a.result": {"value": "null", "optional-model": SampleModel},
            "a.result.x": {"value": '"<unknown>"'},
        }
        hints = {"a.result": UiHint(optional_switch=False)}
        switched, hidden = optional_model_rows(details, hints.get)
        assert switched == {"a.on", "a.off"}
        assert hidden == {"a.off.x", "a.result"}

    def test_capacity_estimate_has_no_switch(self, config_eos):
        hint = hint_for_indexed_field(
            "devices.batteries.bat1.capacity_estimate", "devices.batteries"
        )
        assert hint is not None and not hint.optional_switch
        hint = hint_for_indexed_field(
            "devices.batteries.bat1.capacity_estimation", "devices.batteries"
        )
        assert hint is None or hint.optional_switch

    def test_switch_asks_for_required_fields_first(self):
        class _Required(PydanticBaseModel):
            needed: float
            other: int = 3

        html = to_xml(make_config_update_optional_model_form(_Required, "null")("a.b", "null"))
        assert 'placeholder="needed"' in html
        assert "required" in html
        assert "other" in html  # default sent along
        assert "Switch on" in html
        assert 'hx-validate="true"' in html

    def test_config_card_of_an_optional_sub_model_is_a_switch(self):
        details = {
            "a.b": {
                "name": "a.b",
                "value": "null",
                "default": "null",
                "description": "Optional sub-model",
                "deprecated": None,
                "scope": None,
                "read-only": "rw",
                "type": "Optional[SampleModel]",
                "optional-model": SampleModel,
            }
        }
        html = to_xml(config_card(details["a.b"], details))
        assert "a-b-switch-on" in html
        assert "default_value" in html  # defaults of the model are written


def _details_is_open(html: str, heading: str) -> bool:
    """Whether the ``<details>`` element around the given ``<h4>`` heading is open."""
    start = html.index(f">{heading}</h4>")
    details = html.rfind("<details", 0, start)
    return " open" in html[details : html.index(">", details)]


def _select_html_by_name(html: str, name: str) -> str:
    """Return the ``<select>`` element with the given name from rendered HTML."""
    match = re.search(rf'<select[^>]*name="{re.escape(name)}".*?</select>', html, flags=re.S)
    assert match, f"no select {name}"
    return match.group(0)


def _summary_html(html: str, heading: str) -> str:
    """Return the ``<summary>`` element around the given ``<h4>`` heading."""
    start = html.index(f">{heading}</h4>")
    summary = html.rfind("<summary", 0, start)
    return html[summary : html.index("</summary>", start)]


def _form_html_by_id(html: str, form_id: str) -> str:
    """Return the ``<form>`` element with the given id from rendered HTML."""
    match = re.search(rf'<form[^>]*id="{re.escape(form_id)}".*?</form>', html, flags=re.S)
    assert match, f"no form {form_id}"
    return match.group(0)


def _html_from_id_to_form_end(html: str, element_id: str) -> str:
    """Return the HTML from the element with the given id up to its ``</form>``."""
    start = html.rfind("<", 0, html.index(f'id="{element_id}"'))
    return html[start : html.index("</form>", start) + len("</form>")]


def _details_html_by_id(html: str, details_id: str) -> str:
    """Return the ``<details>`` element with the given id, up to its first ``</details>``."""
    start = html.index(f'id="{details_id}"')
    details = html.rfind("<details", 0, start)
    return html[details : html.index("</details>", start)]


def _select_html(html: str, select_id: str) -> str:
    """Return the ``<select>`` element with the given id from rendered HTML."""
    match = re.search(rf'<select[^>]*id="{re.escape(select_id)}".*?</select>', html, flags=re.S)
    assert match, f"no select {select_id}"
    return match.group(0)


class TestConfigurationSearch:
    """The Config page filters by the search term of its URL."""

    @pytest.fixture
    def client(self, config_eos: ConfigEOS) -> Iterator[TestClient]:
        values = json.loads(config_eos.model_dump_json())
        with patch("akkudoktoreos.server.dash.configuration.get_config", return_value=values):
            yield TestClient(_eosdash_app())

    def test_search_from_url_filters_the_cards(self, client: TestClient):
        html = client.get("/eosdash/configuration", params={"search": "eosdash_port"}).text
        assert 'value="eosdash_port"' in html
        assert "server.eosdash_port" in html
        assert "general.latitude" not in html

    def test_without_search_all_cards_are_shown(self, client: TestClient):
        html = client.get("/eosdash/configuration").text
        assert "server.eosdash_port" in html
        assert "general.latitude" in html

    def test_get_never_updates_the_configuration(self, client: TestClient):
        with patch("akkudoktoreos.server.dash.configuration.config_update") as config_update:
            client.get(
                "/eosdash/configuration",
                params={"action": "update", "key": "general.latitude", "value": "1"},
            )
        config_update.assert_not_called()

    def test_open_from_url_expands_the_controller_card(self, config_eos: ConfigEOS):
        values = json.loads(config_eos.model_dump_json())
        control = ControlAdapterCommonSettings.model_validate(
            {"controllers": {"inv1": STPE_SETTINGS}}
        )
        values["adapter"]["control"] = control.model_dump(mode="json")
        with patch("akkudoktoreos.server.dash.configuration.get_config", return_value=values):
            html = (
                TestClient(_eosdash_app())
                .get(
                    "/eosdash/configuration",
                    params={"search": CONTROLLERS, "open": f"{CONTROLLERS}.inv1"},
                )
                .text
            )
        assert _details_is_open(html, f"{CONTROLLERS}.inv1")
        assert "general.latitude" not in html

    def test_search_box_has_the_included_id(self, client: TestClient):
        html = client.get("/eosdash/configuration").text
        search_box = re.search(r'<input[^>]*name="search"[^>]*>', html)
        assert search_box
        assert 'id="config-search"' in search_box.group(0)


class TestEmrKeyLists:
    """The energy meter reading key lists take typed keys and suggest the known ones."""

    NAME = "measurement.grid_import_emr_keys"

    def _render(self, config_eos: ConfigEOS, current: Optional[list[str]]) -> str:
        config_eos.merge_settings_from_dict(
            {"measurement": {"grid_import_emr_keys": current, "load_emr_keys": ["load0_emr"]}}
        )
        details = _controller_config_details(config_eos, {"meter1": METER_SETTINGS})
        return to_xml(config_card(details[self.NAME], details))

    def test_add_field_takes_any_key_and_suggests_the_known_ones(self, config_eos):
        html = self._render(config_eos, ["grid_import_emr"])
        add_input = re.search(
            r'<input[^>]*id="measurement-grid_import_emr_keys-add-value-input"[^>]*>', html
        )
        assert add_input and "required" in add_input.group(0)
        assert 'list="measurement-grid_import_emr_keys-add-value-options"' in add_input.group(0)
        assert "measurement-grid_import_emr_keys-add-value-select" not in html  # no fixed select
        datalist = re.search(r"<datalist[^>]*>.*?</datalist>", html, flags=re.S)
        assert datalist
        suggested = set(re.findall(r'<option value="([^"]*)"', datalist.group(0)))
        # The defined measurement keys, not the keys already listed
        assert "load0_emr" in suggested
        assert "grid_import_emr" not in suggested

    def test_add_checks_the_typed_key_before_the_request(self, config_eos):
        html = self._render(config_eos, None)
        form = re.search(
            r"<form[^>]*>(?:(?!</form>).)*measurement-grid_import_emr_keys-add-value-input.*?</form>",
            html,
            flags=re.S,
        )
        assert form and 'hx-validate="true"' in form.group(0)

    def test_all_four_lists_use_the_suggestions(self):
        for name in ("load", "grid_export", "grid_import", "pv_production"):
            hint = UI_HINTS[f"measurement.{name}_emr_keys"]
            assert hint.form == "select_list" and hint.free_text
            assert hint.options_from == "measurement.keys"

    def test_controller_readings_are_picked_from_the_shared_data_keys(self, config_eos):
        details = _controller_config_details(config_eos, {"meter1": METER_SETTINGS})
        name = "adapter.control.grid_import_emr_data_keys"
        html = to_xml(config_card(details[name], details))
        select = _select_html(html, "adapter-control-grid_import_emr_data_keys-add-value-select")
        assert 'value="meter1:grid_import_kwh"' in select  # a pick-list, no free text
        assert "add-value-input" not in html
        for role in ("load", "grid_export", "grid_import", "pv_production"):
            hint = UI_HINTS[f"adapter.control.{role}_emr_data_keys"]
            assert hint.form == "select_list" and not hint.free_text
            assert hint.options_from == "adapter.control.shared_data_keys"

    def test_device_measurements_map_device_keys_to_shared_data_keys(self):
        hint = UI_HINTS["adapter.control.device_measurement_data_keys"]
        assert hint.form == "map"
        assert hint.param_from == "devices.measurement_keys"
        assert hint.options_from == "adapter.control.shared_data_keys"

    def test_options_from_several_paths_are_merged(self):
        details = {"a.keys": {"value": '["x", "y"]'}, "b.keys": {"value": '["y", "z"]'}}
        hint = UiHint(form="select_list", options_from=["a.keys", "b.keys"])
        form = resolve_form_factory(hint, details)
        assert form is not None
        options = re.findall(r'<option value="([^"]+)"', to_xml(form("c.list", "[]")))
        assert options == ["x", "y", "z"]
