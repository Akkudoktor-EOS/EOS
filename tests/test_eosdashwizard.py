"""Tests for the EOSdash setup wizard."""

import json
from typing import Any

import pytest
from fasthtml.common import to_xml

from akkudoktoreos.optimization.optimization import OptimizationAlgorithm
from akkudoktoreos.server.dash import wizard
from akkudoktoreos.server.dash.configuration import create_config_details
from akkudoktoreos.server.dash.wizard import (
    PV_PLANES_PROVIDERS,
    WIZARD_STEPS,
    Wizard,
    check_devices,
    check_homeassistant,
    check_horizon,
    check_load,
    check_location,
    check_measurements,
    check_prices,
    check_pv,
    check_weather,
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

    def test_config_names_exist(self, config_details):
        """A renamed configuration value must not silently drop out of the wizard."""
        for step in WIZARD_STEPS:
            names = list(step.config_names)
            for choice in step.choices:
                assert choice.name in step.config_names, f"{step.id}: {choice.name}"
                for chosen in choice.config_names.values():
                    names.extend(chosen)
            for name in names:
                assert name in config_details, f"Step {step.id}: unknown {name}."
                assert config_details[name]["read-only"] == "rw", name
                assert not config_details[name]["deprecated"], name

    def test_choice_values_exist(self, config_details):
        """A renamed provider must not silently lose its settings in the wizard."""
        for step in WIZARD_STEPS:
            for choice in step.choices:
                if choice.name == "optimization.algorithm":
                    available = [algorithm.value for algorithm in OptimizationAlgorithm]
                else:
                    assert choice.name.endswith(".provider")
                    available = config_value(config_details, f"{choice.name}s")
                for value in choice.config_names:
                    assert value in available, f"Step {step.id}: unknown {value}."

    def test_pv_planes_providers_exist(self, config_details):
        available = config_value(config_details, "pvforecast.providers")
        for provider in PV_PLANES_PROVIDERS:
            assert provider in available

    def test_choice_follows_value(self, config_details):
        step = wizard_step("load")
        set_value(config_details, "load.provider", "LoadVrm")
        assert step_config_names(step, config_details) == [
            "load.provider",
            "load.vrm.site_id",
            "load.vrm.token",
        ]
        set_value(config_details, "load.provider", None)
        assert step_config_names(step, config_details) == ["load.provider"]

    def test_choice_with_list_value(self, config_details):
        step = wizard_step("measurements")
        set_value(config_details, "adapter.provider", ["HomeAssistant", "NodeRED"])
        names = step_config_names(step, config_details)
        assert "adapter.nodered.host" in names
        assert "adapter.nodered.port" in names
        assert len(names) == len(set(names))

    def test_homeassistant_step_shows_all_adapter_settings(self, config_details):
        """Every writable setting of the Home Assistant adapter is in its step."""
        adapter_names = [
            name
            for name, details in config_details.items()
            if name.startswith("adapter.homeassistant.")
            and name.count(".") == 2
            and details["read-only"] == "rw"
            and not details["deprecated"]
        ]
        assert adapter_names
        names = step_config_names(wizard_step("homeassistant"), config_details)
        assert sorted(names) == sorted(adapter_names)

    @pytest.mark.parametrize("algorithm", [algorithm.value for algorithm in OptimizationAlgorithm])
    def test_horizon_follows_algorithm(self, config_details, algorithm):
        set_value(config_details, "optimization.algorithm", algorithm)
        names = step_config_names(wizard_step("horizon"), config_details)
        horizons = [name for name in names if name.endswith(".horizon_hours")]
        assert horizons == [f"optimization.{algorithm.lower()}.horizon_hours"]

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

    def test_pv(self, config_details):
        set_value(config_details, "pvforecast.provider", None)
        assert "no PV system" in check_pv(config_details)[0]

        set_value(config_details, "pvforecast.provider", "PVForecastAkkudoktor")
        set_value(config_details, "pvforecast.planes", None)
        assert "at least one plane" in check_pv(config_details)[0]
        set_value(config_details, "pvforecast.planes", [{"peakpower": None}])
        assert "at least one plane" in check_pv(config_details)[0]
        set_value(config_details, "pvforecast.planes", [{"peakpower": 8.0}])
        assert check_pv(config_details) == []

        set_value(config_details, "pvforecast.provider", "PVForecastSolcast")
        set_value(config_details, "pvforecast.solcast.api_key", "")
        set_value(config_details, "pvforecast.solcast.site_id", "abcd")
        assert check_pv(config_details) == ["pvforecast.solcast.api_key is not set."]

        set_value(config_details, "pvforecast.provider", "PVForecastVrm")
        assert check_pv(config_details) == []

    def test_weather(self, config_details):
        set_value(config_details, "weather.provider", None)
        assert len(check_weather(config_details)) == 1
        set_value(config_details, "weather.provider", "BrightSky")
        assert check_weather(config_details) == []

    def test_prices(self, config_details):
        set_value(config_details, "elecprice.provider", None)
        set_value(config_details, "elecfee.provider", None)
        set_value(config_details, "feedintariff.provider", None)
        todo = check_prices(config_details)
        assert len(todo) == 2
        assert "electricity price provider" in todo[0]
        assert "feed-in tariff provider" in todo[1]

        set_value(config_details, "elecprice.provider", "ElecPriceEnergyCharts")
        set_value(config_details, "feedintariff.provider", "FeedInTariffEnergyCharts")
        assert check_prices(config_details) == []

        set_value(config_details, "elecprice.provider", "ElecPriceFixed")
        set_value(config_details, "elecfee.provider", "ElecFeeFixed")
        set_value(config_details, "feedintariff.provider", "FeedInTariffFixed")
        for name in (
            "elecprice.elecpricefixed.elecprice_marketprice_amt_kwh.windows",
            "elecfee.elecfeefixed.consumption_amt_kwh.windows",
            "elecfee.elecfeefixed.consumption_percent_amt.windows",
            "elecfee.elecfeefixed.feedin_amt_kwh.windows",
            "elecfee.elecfeefixed.feedin_percent_amt.windows",
            "feedintariff.feedintarifffixed.feed_in_tariff_amt_kwh.windows",
        ):
            set_value(config_details, name, [])
        assert len(check_prices(config_details)) == 3

        set_value(config_details, "elecfee.elecfeefixed.consumption_percent_amt.windows", [{}])
        assert len(check_prices(config_details)) == 2

    def test_load(self, config_details):
        set_value(config_details, "load.provider", None)
        assert len(check_load(config_details)) == 1

        set_value(config_details, "load.provider", "LoadAkkudoktor")
        set_value(config_details, "load.loadakkudoktor.loadakkudoktor_year_energy_kwh", None)
        assert len(check_load(config_details)) == 1
        set_value(config_details, "load.loadakkudoktor.loadakkudoktor_year_energy_kwh", 4500)
        assert check_load(config_details) == []

        set_value(config_details, "load.provider", "LoadAkkudoktorAdjusted")
        set_value(config_details, "measurement.load_emr_keys", None)
        assert check_load(config_details) == ["measurement.load_emr_keys is not set."]

    def test_devices(self, config_details):
        for kind in ("batteries", "electric_vehicles", "home_appliances"):
            set_value(config_details, f"devices.{kind}", None)
        assert len(check_devices(config_details)) == 1
        set_value(
            config_details,
            "devices.batteries",
            {"battery1": {"device_id": "battery1", "effective_nominal_capacity_wh": 10000}},
        )
        assert check_devices(config_details) == []

    def test_devices_without_size(self, config_details):
        """The packaged profiles are generic: each unit needs its size."""
        set_value(
            config_details,
            "devices.batteries",
            {"battery1": {"device_id": "battery1", "effective_nominal_capacity_wh": None}},
        )
        set_value(
            config_details,
            "devices.electric_vehicles",
            {"ev1": {"device_id": "ev1", "effective_nominal_capacity_wh": 60000}},
        )
        set_value(
            config_details,
            "devices.inverters",
            {"inverter1": {"device_id": "inverter1", "effective_nominal_power_w": None}},
        )
        todo = check_devices(config_details)
        assert len(todo) == 2
        assert "battery1 has no nominal_capacity_wh" in todo[0]
        assert "profile_adjustment.nominal_capacity_wh" in todo[0]
        assert "inverter1 has no nominal_power_w" in todo[1]

    def test_devices_size_of_a_configured_device(self, config_eos):
        """The check reads the effective size as the EOS server reports it."""
        config_eos.merge_settings_from_dict(
            {
                "devices": {
                    "max_batteries": 1,
                    "max_inverters": 1,
                    "batteries": {"battery1": {"device_id": "battery1"}},
                    "inverters": {
                        "inverter1": {
                            "device_id": "inverter1",
                            "profile_adjustment": {"nominal_power_w": 10000},
                        }
                    },
                }
            }
        )
        details = create_config_details(
            type(config_eos), json.loads(config_eos.model_dump_json())
        )
        todo = check_devices(details)
        assert len(todo) == 1
        assert todo[0].startswith("battery1 has no nominal_capacity_wh")

    def test_measurements(self, config_details):
        set_value(config_details, "devices.batteries", {"battery1": {}})
        set_value(config_details, "devices.electric_vehicles", {"ev1": {}})

        # Without an adapter no source for the SoC is known
        set_value(config_details, "adapter.provider", None)
        todo = check_measurements(config_details)
        assert len(todo) == 1
        assert "No adapter is selected" in todo[0]
        assert "of battery1, ev1 is known" in todo[0]

        # The mapping of the Home Assistant adapter is checked in its own step
        for providers in (["NodeRED"], ["HomeAssistant"]):
            set_value(config_details, "adapter.provider", providers)
            assert check_measurements(config_details) == []

    def test_homeassistant_not_selected(self, config_details, monkeypatch):
        monkeypatch.setattr(wizard, "is_home_assistant_addon", lambda: False)
        set_value(config_details, "devices.batteries", {"battery1": {}})
        for providers in (None, ["NodeRED"]):
            set_value(config_details, "adapter.provider", providers)
            assert check_homeassistant(config_details) == []

    def test_homeassistant(self, config_details, monkeypatch):
        monkeypatch.setattr(wizard, "is_home_assistant_addon", lambda: True)
        set_value(config_details, "devices.batteries", {"battery1": {}})
        set_value(config_details, "devices.electric_vehicles", {"ev1": {}})
        set_value(config_details, "adapter.provider", ["HomeAssistant"])
        measurements = "adapter.homeassistant.device_measurement_entity_ids"
        instructions = "adapter.homeassistant.device_instruction_entity_ids"

        set_value(config_details, measurements, None)
        set_value(config_details, instructions, None)
        todo = check_homeassistant(config_details)
        assert len(todo) == 3
        assert "battery1-soc-factor" in todo[0]
        assert "ev1-soc-factor" in todo[1]
        assert instructions in todo[2]

        set_value(config_details, measurements, {"battery1-soc-factor": "sensor.battery_soc"})
        set_value(config_details, instructions, ["sensor.eos_battery1"])
        todo = check_homeassistant(config_details)
        assert len(todo) == 1
        assert "ev1-soc-factor" in todo[0]

        set_value(
            config_details,
            measurements,
            {"battery1-soc-factor": "sensor.battery_soc", "ev1-soc-factor": "sensor.ev_soc"},
        )
        assert check_homeassistant(config_details) == []

    def test_homeassistant_outside_of_addon(self, config_details, monkeypatch):
        """The adapter cannot reach Home Assistant outside of an add-on."""
        monkeypatch.setattr(wizard, "is_home_assistant_addon", lambda: False)
        set_value(config_details, "adapter.provider", ["HomeAssistant"])
        set_value(config_details, "adapter.homeassistant.device_instruction_entity_ids", ["x"])
        todo = check_homeassistant(config_details)
        assert len(todo) == 1
        assert "add-on" in todo[0]

    def test_measurements_fresh_system(self, config_details):
        """A fresh system has no adapter and no devices: the step must not look done."""
        todo = check_measurements(config_details)
        assert len(todo) == 1
        assert "state of charge is known" in todo[0]

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

    @pytest.mark.parametrize("step_id", [step.id for step in WIZARD_STEPS])
    def test_every_step_renders(self, eos, step_id):
        html = to_xml(Wizard("localhost", 8503, {"wizard_step": step_id}))
        number = [step.id for step in WIZARD_STEPS].index(step_id) + 1
        assert f"Step {number} of {len(WIZARD_STEPS)}: {wizard_step(step_id).title}" in html

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

    def test_provider_settings_are_shown(self, eos, config):
        config["pvforecast"]["provider"] = "PVForecastSolcast"
        html = to_xml(Wizard("localhost", 8503, {"wizard_step": "pv"}))
        assert "pvforecast.solcast.api_key" in html
        assert "pvforecast.vrm.token" not in html

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

    def test_devices_step_has_an_example_for_every_device_type(self, eos):
        html = to_xml(Wizard("localhost", 8503, {"wizard_step": "devices"}))
        for device_type in ("Battery", "Inverter", "Electric vehicle", "Home appliance"):
            assert f"<strong>{device_type}</strong>" in html

    def test_devices_step_names_existing_settings(self, eos):
        """The device settings the step names exist (they moved into profiles before)."""
        from akkudoktoreos.devices.settings.batterysettings import (
            BatteriesCommonSettings,
            BatteryProfileAdjustment,
        )
        from akkudoktoreos.devices.settings.invertersettings import (
            InverterCommonSettings,
            InverterProfileAdjustment,
        )

        html = to_xml(Wizard("localhost", 8503, {"wizard_step": "devices"}))
        for model, names in (
            (BatteriesCommonSettings, ("battery_profile", "min_soc_percentage")),
            (BatteryProfileAdjustment, ("nominal_capacity_wh", "max_charge_power_w")),
            (InverterCommonSettings, ("inverter_profile", "battery_id")),
            (InverterProfileAdjustment, ("nominal_power_w",)),
        ):
            for name in names:
                assert name in model.model_fields
                assert f"<code>{name}</code>" in html or f"profile_adjustment.{name}" in html
        for stale in ("<code>capacity_wh</code>", "<code>charging_efficiency</code>", "<code>max_power_w</code>"):
            assert stale not in html

    def test_homeassistant_step_example_renders_yaml(self, eos):
        html = to_xml(Wizard("localhost", 8503, {"wizard_step": "homeassistant"}))
        assert "<pre" in html
        assert "sensor.eos_battery1" in html
        assert "add-on" in html
