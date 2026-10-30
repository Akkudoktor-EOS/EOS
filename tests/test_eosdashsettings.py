"""Tests for the EOSdash settings in the EOS configuration (eosdash/plan)."""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

import akkudoktoreos.server.dash.eosstatus as eosstatus
from akkudoktoreos.config.config import SettingsEOS
from akkudoktoreos.server.dash import plan
from akkudoktoreos.server.dash.dashsettings import (
    EOSdashCommonSettings,
    EOSdashPlanSettings,
)

SERVER = "http://127.0.0.1:8503"


class TestEOSdashPlanSettings:
    def test_defaults(self):
        settings = EOSdashPlanSettings()
        assert settings.visible == {}
        assert settings.color == {}
        assert settings.device_alias == {}
        assert settings.plan_table_open is False
        assert settings.energy_kwh is True
        assert settings.factor_percent is True
        assert settings.price_minor_unit is True
        assert EOSdashCommonSettings().plan.model_dump() == settings.model_dump()

    def test_valid_values(self):
        settings = EOSdashPlanSettings(
            color={"battery1_soc_factor": "#DAA520"},
            style={"pvforecast_ac_energy_wh": "bar"},
            width={"loadforecast_energy_wh": 5},
            line_dash={"mode_SELF_CONSUMPTION": "dotted"},
            alias={"mode_SELF_CONSUMPTION": "  Self consumption "},
        )
        assert settings.alias["mode_SELF_CONSUMPTION"] == "Self consumption"

    @pytest.mark.parametrize(
        "field, value",
        [
            ("color", {"col": "red"}),
            ("color", {"col": "#12345"}),
            ("style", {"col": "area"}),
            ("width", {"col": 0}),
            ("width", {"col": 6}),
            ("line_dash", {"col": "dashdot"}),
            ("alias", {"col": ""}),
            ("alias", {"col": "x" * 33}),
            ("device_alias", {"battery1": "   "}),
        ],
    )
    def test_invalid_values(self, field, value):
        with pytest.raises(ValidationError):
            EOSdashPlanSettings(**{field: value})


class TestEOSdashConfig:
    def test_config_has_eosdash_section(self, config_eos):
        assert config_eos.eosdash.plan.model_dump() == EOSdashPlanSettings().model_dump()

    def test_set_nested_value(self, config_eos):
        config_eos.set_nested_value("eosdash/plan/color", {"battery1_soc_factor": "#FF0000"})
        config_eos.set_nested_value("eosdash/plan/price_minor_unit", False)
        assert config_eos.eosdash.plan.color == {"battery1_soc_factor": "#FF0000"}
        assert config_eos.eosdash.plan.price_minor_unit is False
        with pytest.raises(ValueError):
            config_eos.set_nested_value("eosdash/plan/color", {"battery1_soc_factor": "red"})

    def test_merge_settings_from_dict(self, config_eos):
        config_eos.merge_settings_from_dict(
            {"eosdash": {"plan": {"device_alias": {"battery1": "House battery"}}}}
        )
        assert config_eos.eosdash.plan.device_alias == {"battery1": "House battery"}
        # Other plan settings keep their defaults
        assert config_eos.eosdash.plan.energy_kwh is True


@pytest.fixture
def eosdash_config():
    """EOSdash copy of the EOS configuration, restored after the test."""
    saved = eosstatus.eos_config
    eosstatus.eos_config = SettingsEOS(
        eosdash=EOSdashCommonSettings(
            plan=EOSdashPlanSettings(
                color={"battery1_soc_factor": "#00FF00"},
                alias={"mode_IDLE": "Idle"},
                device_alias={"battery1": "House battery"},
                price_minor_unit=False,
            )
        )
    )
    yield eosstatus.eos_config
    eosstatus.eos_config = saved


def put_response(config: SettingsEOS) -> MagicMock:
    """Response of EOS to a configuration update."""
    response = MagicMock()
    response.json.return_value = config.model_dump(mode="json")
    return response


class TestPlanSettings:
    def test_defaults_without_config(self):
        saved = eosstatus.eos_config
        eosstatus.eos_config = None
        try:
            assert plan.plan_settings().model_dump() == EOSdashPlanSettings().model_dump()
            assert plan.plan_option("price_minor_unit") is True
        finally:
            eosstatus.eos_config = saved

    def test_settings_from_config(self, eosdash_config):
        assert plan.solution_column_color("battery1_soc_factor") == "#00FF00"
        assert plan.plan_mode_label("IDLE") == "Idle"
        assert plan.device_alias("battery1") == "House battery"
        assert plan.device_alias("ev11") == "ev11"
        assert plan.column_label("battery1_soc_factor", ["battery1"]) == "House battery soc_factor"
        assert plan.plan_option("price_minor_unit") is False
        assert plan.plan_option("energy_kwh") is True

    def test_put_replaces_config(self, eosdash_config):
        updated = eosdash_config.model_copy(deep=True)
        updated.eosdash.plan.price_minor_unit = True
        with patch.object(plan.requests, "put", return_value=put_response(updated)) as put:
            plan.plan_settings_put(SERVER, "price_minor_unit", True)
        put.assert_called_once_with(
            f"{SERVER}/v1/config/eosdash/plan/price_minor_unit", json=True, timeout=10
        )
        assert plan.plan_option("price_minor_unit") is True

    def test_put_failure_keeps_config(self, eosdash_config):
        with patch.object(plan.requests, "put", side_effect=OSError("no connection")):
            plan.plan_settings_put(SERVER, "price_minor_unit", True)
        assert plan.plan_option("price_minor_unit") is False

    @pytest.mark.parametrize(
        "data, key, value",
        [
            (
                {"action": "color", "renderer": "load", "color": "#abcdef"},
                "color",
                {"battery1_soc_factor": "#00FF00", "load": "#ABCDEF"},
            ),
            ({"action": "reset", "renderer": "battery1_soc_factor"}, "color", {}),
            ({"action": "visible", "renderer": "load", "load-visible": "true"}, "visible", {"load": True}),
            ({"action": "visible", "renderer": "load"}, "visible", {"load": False}),
            ({"action": "style", "renderer": "load", "style": "bar"}, "style", {"load": "bar"}),
            ({"action": "width", "renderer": "load", "width": "3"}, "width", {"load": 3}),
            ({"action": "dash", "renderer": "load", "dash": "dotted"}, "line_dash", {"load": "dotted"}),
            (
                {"action": "alias", "renderer": "load", "alias": "  House   load\x00 "},
                "alias",
                {"mode_IDLE": "Idle", "load": "House load"},
            ),
            ({"action": "alias", "renderer": "mode_IDLE", "alias": ""}, "alias", {}),
        ],
    )
    def test_solution_settings_action(self, eosdash_config, data, key, value):
        with patch.object(plan, "plan_settings_put") as put:
            plan.solution_settings_action(SERVER, data)
        put.assert_called_once_with(SERVER, key, value)

    @pytest.mark.parametrize(
        "data",
        [
            {"action": "color", "renderer": "load", "color": "red"},
            {"action": "style", "renderer": "load", "style": "area"},
            {"action": "width", "renderer": "load", "width": "9"},
            {"action": "dash", "renderer": "load", "dash": "dashdot"},
            {"action": "color", "color": "#ABCDEF"},
        ],
    )
    def test_solution_settings_action_invalid(self, eosdash_config, data):
        with patch.object(plan, "plan_settings_put") as put:
            plan.solution_settings_action(SERVER, data)
        put.assert_not_called()


class TestCurrencyMinorUnit:
    @pytest.mark.parametrize(
        "currency, expected",
        [
            ("EUR", ("ct", 100)),
            ("CHF", ("Rp.", 100)),
            ("SEK", ("1/100 SEK", 100)),
            ("KWD", ("1/1000 KWD", 1000)),
            ("JPY", None),
            (None, None),
        ],
    )
    def test_currency_minor_unit(self, currency, expected):
        assert plan.currency_minor_unit(currency) == expected

    def test_config_currency(self):
        config = SettingsEOS.model_validate({"general": {"timezone_override": "Europe/Zurich"}})
        assert plan.config_currency(config) == "CHF"
