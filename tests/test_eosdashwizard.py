"""Tests for the EOSdash setup wizard."""

import json
from typing import Any

import pytest
from fasthtml.common import to_xml

from akkudoktoreos.optimization.optimization import OptimizationAlgorithm
from akkudoktoreos.server.dash import wizard
from akkudoktoreos.server.dash.configuration import create_config_details
from akkudoktoreos.server.dash.wizard import (
    WIZARD_STEPS,
    Wizard,
    check_horizon,
    check_location,
    config_value,
    step_config_names,
    step_todo,
    wizard_step,
)


@pytest.fixture
def config(config_eos) -> dict[str, Any]:
    """The default EOS configuration as the EOS server returns it."""
    return json.loads(config_eos.model_dump_json())


@pytest.fixture
def config_details(config_eos, config) -> dict[str, dict]:
    """Configuration details of the default EOS configuration."""
    return create_config_details(type(config_eos), config)


def set_value(config_details: dict[str, dict], name: str, value: Any) -> None:
    """Set the current value in configuration details."""
    config_details[name]["value"] = json.dumps(value)


class TestWizardSteps:
    """The step definitions."""

    def test_step_ids_are_unique(self):
        ids = [step.id for step in WIZARD_STEPS]
        assert len(ids) == len(set(ids))

    def test_steps_have_title_and_intro(self):
        for step in WIZARD_STEPS:
            assert step.title
            assert step.intro.strip()

    @pytest.mark.parametrize("algorithm", [algorithm.value for algorithm in OptimizationAlgorithm])
    def test_config_names_exist(self, config_details, algorithm):
        """A renamed configuration value must not silently drop out of the wizard."""
        set_value(config_details, "optimization.algorithm", algorithm)
        for step in WIZARD_STEPS:
            assert len(step_config_names(step, config_details)) == len(step.config_names), (
                f"Step '{step.id}' names unknown configuration values for {algorithm}."
            )

    def test_config_names_are_writable(self, config_details):
        for step in WIZARD_STEPS:
            for name in step_config_names(step, config_details):
                assert config_details[name]["read-only"] == "rw", name
                assert not config_details[name]["deprecated"], name

    def test_wizard_step(self):
        assert wizard_step("location").id == "location"
        assert wizard_step(None) is WIZARD_STEPS[0]
        assert wizard_step("unknown") is WIZARD_STEPS[0]

    def test_config_value(self, config_details):
        assert config_value(config_details, "prediction.hours") == 48
        assert config_value(config_details, "no.such.value", "default") == "default"


class TestWizardChecks:
    """What the steps report as still to do."""

    def test_location_default(self, config_details):
        todo = check_location(config_details)
        assert len(todo) == 1
        assert "default" in todo[0]

    def test_location_set(self, config_details):
        set_value(config_details, "general.latitude", 48.137)
        set_value(config_details, "general.longitude", 11.575)
        set_value(config_details, "general.timezone", "Europe/Berlin")
        assert check_location(config_details) == []

    def test_location_missing(self, config_details):
        set_value(config_details, "general.latitude", None)
        todo = check_location(config_details)
        assert len(todo) == 1
        assert "not set" in todo[0]

    def test_location_without_timezone(self, config_details):
        set_value(config_details, "general.latitude", 48.137)
        set_value(config_details, "general.timezone", None)
        todo = check_location(config_details)
        assert len(todo) == 1
        assert "time zone" in todo[0]

    def test_horizon_done(self, config_details):
        set_value(config_details, "ems.mode", "OPTIMIZATION")
        assert check_horizon(config_details) == []

    def test_horizon_disabled(self, config_details):
        set_value(config_details, "ems.mode", "DISABLED")
        todo = check_horizon(config_details)
        assert len(todo) == 1
        assert "DISABLED" in todo[0]

    @pytest.mark.parametrize("algorithm", [algorithm.value for algorithm in OptimizationAlgorithm])
    @pytest.mark.parametrize("hours", [None, 12])
    def test_horizon_prediction_too_short(self, config_details, algorithm, hours):
        set_value(config_details, "ems.mode", "OPTIMIZATION")
        set_value(config_details, "optimization.algorithm", algorithm)
        set_value(config_details, f"optimization.{algorithm.lower()}.horizon_hours", 24)
        set_value(config_details, "prediction.hours", hours)
        todo = check_horizon(config_details)
        assert len(todo) == 1
        assert f"optimization.{algorithm.lower()}.horizon_hours (24)" in todo[0]

    def test_step_without_check_has_nothing_to_do(self, config_details):
        assert step_todo(wizard_step("start"), config_details) == []


class TestWizardPage:
    """The rendered wizard page."""

    @pytest.fixture
    def eos(self, monkeypatch, config) -> dict[str, list]:
        """Replace the access to the EOS server; records the calls."""
        calls: dict[str, list] = {"update": [], "save": []}

        def save_config(eos_host, eos_port):
            calls["save"].append((eos_host, eos_port))
            return "saved-by-test"

        monkeypatch.setattr(wizard, "get_config", lambda eos_host, eos_port: config)
        monkeypatch.setattr(
            wizard, "config_update", lambda eos_host, eos_port, data: calls["update"].append(data)
        )
        monkeypatch.setattr(wizard, "save_config", save_config)
        return calls

    def test_first_step_is_default(self, eos):
        html = to_xml(Wizard("localhost", 8503))
        assert f"Step 1 of {len(WIZARD_STEPS)}: Start" in html
        assert 'id="wizard-next"' in html
        assert 'id="wizard-back"' not in html

    def test_step_shows_only_its_config(self, eos):
        html = to_xml(Wizard("localhost", 8503, {"wizard_step": "location"}))
        assert "Location" in html
        assert "general.latitude" in html
        assert "general.longitude" in html
        # Example, status and navigation
        assert "Munich" in html
        assert "still the default values" in html
        assert 'id="wizard-back"' in html
        assert 'id="wizard-next"' in html
        # Configuration of other steps is not shown
        assert "ems.interval" not in html
        assert "server.port" not in html

    def test_requests_carry_the_step(self, eos):
        """Updates sent by the configuration cards must come back to the wizard."""
        page = Wizard("localhost", 8503, {"wizard_step": "horizon"})
        assert page.attrs["id"] == "wizard"
        assert json.loads(page.attrs["hx-vals"]) == {"wizard_step": "horizon"}

    def test_horizon_step_follows_algorithm(self, eos, config):
        config["optimization"]["algorithm"] = "GENETIC0"
        html = to_xml(Wizard("localhost", 8503, {"wizard_step": "horizon"}))
        assert "optimization.genetic0.horizon_hours" in html
        assert "optimization.genetic.horizon_hours" not in html

    def test_update(self, eos):
        data = {
            "wizard_step": "location",
            "action": "update",
            "key": "general.latitude",
            "value": "48.137",
        }
        html = to_xml(Wizard("localhost", 8503, data))
        assert eos["update"] == [data]
        assert eos["save"] == []
        assert f"Step 2 of {len(WIZARD_STEPS)}: Location" in html

    def test_finish_step(self, eos):
        html = to_xml(Wizard("localhost", 8503, {"wizard_step": "finish"}))
        assert 'id="wizard-save"' in html
        assert 'id="wizard-next"' not in html
        # Summary of all steps with a check
        assert "still the default values" in html
        assert eos["save"] == []

    def test_save(self, eos):
        html = to_xml(Wizard("localhost", 8503, {"wizard_step": "finish", "action": "save_to_file"}))
        assert eos["save"] == [("localhost", 8503)]
        assert "saved-by-test" in html
