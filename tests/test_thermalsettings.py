"""Isolated validation of staged zone and electric heat-source settings."""

# ruff: noqa: S101 -- pytest assertions are the test contract.

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from akkudoktoreos.devices.settings.heatsourcesettings import (
    HeatPumpConfiguration,
    HeatSourceSettings,
)
from akkudoktoreos.thermal.thermalmodels import ThermalComfortBand
from akkudoktoreos.thermal.thermalsettings import (
    ThermalCommonSettings,
    ThermalDistributionMeasurementBindings,
    ThermalDistributionObserverSettings,
    ThermalZoneSettings,
    require_thermal_control_disabled,
)


def enabled_zone() -> dict[str, Any]:
    """Return explicit generic zone inputs, without installation defaults."""
    return {
        "zone_id": "zone1",
        "enabled": True,
        "room_temperature_measurement_key": "room_temperature",
        "model_reference": "model-v1",
        "comfort": {
            "min_temperature_c": 19.25,
            "target_temperature_c": 20.45,
            "max_temperature_c": 20.55,
        },
    }


class TestThermalSettings:
    def test_distribution_defaults_do_not_enable_new_states_or_measurements(self):
        zone = ThermalZoneSettings(zone_id="zone1")
        assert zone.distribution is None
        assert zone.distribution_observer is None
        assert all(value is None for value in zone.distribution_measurements.model_dump().values())

    def test_distribution_bindings_and_observer_require_explicit_valid_inputs(self):
        with pytest.raises(ValidationError):
            ThermalDistributionMeasurementBindings(water_temperature_measurement_key=" ")
        with pytest.raises(ValidationError):
            ThermalDistributionObserverSettings.model_validate({})
        with pytest.raises(ValidationError):
            ThermalDistributionObserverSettings(maximum_emitter_uncertainty_c=True)
        with pytest.raises(ValidationError):
            ThermalDistributionObserverSettings(maximum_emitter_uncertainty_c=0.0)
        with pytest.raises(ValidationError):
            ThermalZoneSettings.model_validate(
                {
                    "zone_id": "zone1",
                    "distribution_observer": {"maximum_emitter_uncertainty_c": 0.5},
                }
            )
        with pytest.raises(ValidationError):
            ThermalZoneSettings.model_validate(
                {
                    "zone_id": "zone1",
                    "distribution_measurements": {"water_temperature_measurement_key": "water"},
                }
            )

    def test_distribution_json_roundtrip_preserves_typed_air_and_bindings(self):
        payload = {
            "zones": {
                "room1": {
                    "distribution": {
                        "kind": "air",
                        "air_capacity_j_per_k": 100000.0,
                        "air_temperature_bounds": {
                            "min_temperature_c": 5.0,
                            "max_temperature_c": 60.0,
                        },
                        "fan_levels": [{"level_id": "low", "conductance_w_per_k": 100.0}],
                        "default_fan_level_id": "low",
                    },
                    "distribution_measurements": {"air_temperature_measurement_key": "near"},
                }
            }
        }
        before = deepcopy(payload)
        settings = ThermalCommonSettings.model_validate(payload)
        restored = ThermalCommonSettings.model_validate_json(settings.model_dump_json())
        assert payload == before
        assert restored.model_dump() == settings.model_dump()
        distribution = restored.zones["room1"].distribution
        assert distribution is not None and distribution.kind == "air"

    def test_defaults_are_off_and_contain_no_zone_or_temperature(self):
        settings = ThermalCommonSettings()
        assert settings.mode == "off"
        assert settings.zones == {}
        assert settings.plan_ttl_seconds == 1800
        assert settings.absence_delay_seconds == 1800
        assert settings.measurement_max_age_seconds == 180
        assert settings.presence_max_age_seconds == 90
        require_thermal_control_disabled(settings)

    def test_shadow_mode_keeps_standard_electrical_planning_available(self):
        settings = ThermalCommonSettings(mode="shadow")
        require_thermal_control_disabled(settings)
        assert settings.mode == "shadow"

    def test_control_mode_is_valid_configuration_but_cannot_control(self):
        settings = ThermalCommonSettings(mode="control")
        with pytest.raises(ValueError, match="not implemented"):
            require_thermal_control_disabled(settings)
        assert settings.mode == "control"

    def test_map_key_supplies_zone_identity_without_mutating_input(self):
        payload: dict[str, Any] = {"zones": {"zone1": {}}}
        original = deepcopy(payload)
        settings = ThermalCommonSettings.model_validate(payload)
        assert settings.zones["zone1"].zone_id == "zone1"
        assert not settings.zones["zone1"].enabled
        assert settings.zones["zone1"].comfort is None
        assert payload == original

    @pytest.mark.parametrize("key,zone_id", [("zone1", "zone2"), ("", ""), (" ", " ")])
    def test_invalid_zone_mapping_is_rejected(self, key, zone_id):
        with pytest.raises(ValidationError):
            ThermalCommonSettings.model_validate({"zones": {key: {"zone_id": zone_id}}})

    def test_zone_instance_identity_is_checked(self):
        zone = ThermalZoneSettings(zone_id="zone1")
        assert ThermalCommonSettings(zones={"zone1": zone}).zones["zone1"] is zone
        with pytest.raises(ValidationError):
            ThermalCommonSettings(zones={"zone2": zone})

    @pytest.mark.parametrize(
        "missing", ["room_temperature_measurement_key", "model_reference", "comfort"]
    )
    def test_enabled_zone_requires_declared_inputs(self, missing):
        payload = enabled_zone()
        payload.pop(missing)
        with pytest.raises(ValidationError, match="Enabled thermal zones require"):
            ThermalZoneSettings.model_validate(payload)

    def test_enabled_zone_can_use_schedule_without_presence_sensor(self):
        zone = ThermalZoneSettings.model_validate(enabled_zone())
        assert zone.enabled
        assert zone.presence_measurement_key is None
        assert isinstance(zone.comfort, ThermalComfortBand)
        assert zone.comfort.target_temperature_c == 20.45

    @pytest.mark.parametrize(
        "field",
        [
            "zone_id",
            "room_temperature_measurement_key",
            "presence_measurement_key",
            "model_reference",
        ],
    )
    def test_supplied_zone_identifiers_cannot_be_blank(self, field):
        payload = {"zone_id": "zone1", field: " "}
        with pytest.raises(ValidationError, match="must not be blank"):
            ThermalZoneSettings.model_validate(payload)

    def test_presence_supplies_timing_and_config_has_only_temperature_preferences(self):
        zone = ThermalZoneSettings(
            zone_id="zone1", presence_area_id="area1", unoccupied_comfort=enabled_zone()["comfort"]
        )
        assert zone.presence_area_id == "area1"
        assert zone.unoccupied_comfort is not None
        assert zone.unoccupied_comfort.target_temperature_c == 20.45
        assert zone.comfort is None
        with pytest.raises(ValidationError, match="Extra inputs"):
            ThermalZoneSettings.model_validate({"zone_id": "zone1", "comfort_windows": []})

    @pytest.mark.parametrize(
        "field",
        [
            "plan_ttl_seconds",
            "absence_delay_seconds",
            "measurement_max_age_seconds",
            "presence_max_age_seconds",
        ],
    )
    @pytest.mark.parametrize("value", [0, -1, True, False, 1.5, float("nan"), float("inf")])
    def test_durations_are_positive_integer_seconds(self, field, value):
        with pytest.raises(ValidationError):
            ThermalCommonSettings.model_validate({field: value})

    def test_plan_lease_is_configurable_without_a_fixed_thirty_minute_cap(self):
        assert ThermalCommonSettings(plan_ttl_seconds=3600).plan_ttl_seconds == 3600

    @pytest.mark.parametrize("value", [0, -1, True, False, "60", float("nan"), float("inf"), 3601])
    def test_total_planning_budget_requires_a_finite_bounded_positive_number(self, value):
        with pytest.raises(ValidationError):
            ThermalCommonSettings(planning_time_limit_seconds=value)

    def test_total_budget_and_warm_coasting_are_explicit_and_roundtrip(self):
        settings = ThermalCommonSettings.model_validate(
            {
                "planning_time_limit_seconds": 12.5,
                "zones": {"area1": {"allow_warm_coasting": True}},
            }
        )
        restored = ThermalCommonSettings.model_validate_json(settings.model_dump_json())
        assert restored.planning_time_limit_seconds == 12.5
        assert restored.zones["area1"].allow_warm_coasting is True

    @pytest.mark.parametrize("value", [0, 1, "true", None])
    def test_warm_coasting_requires_boolean_opt_in(self, value):
        with pytest.raises(ValidationError):
            ThermalZoneSettings(zone_id="area1", allow_warm_coasting=value)

    def test_unknown_thermal_fields_are_not_silently_ignored(self):
        with pytest.raises(ValidationError):
            ThermalCommonSettings.model_validate({"automatic_start": True})


class TestHeatSourceSettings:
    def test_heat_pump_uses_selected_catalog_profile_without_actuation(self):
        source = HeatSourceSettings(
            device_id="wp1",
            kind="heat_pump",
            max_electric_power_w=3000,
            heat_pump=HeatPumpConfiguration(preset="wolf_cha07", delivery_temperature_c=35),
        )
        assert not source.enabled
        assert source.heat_pump is not None
        assert source.heat_pump.resolve_profile().heat_pump_type == "air_to_water"

    def test_heat_pump_map_selection_is_explicit_and_catalog_is_a_config_enum(self):
        with pytest.raises(ValidationError):
            HeatPumpConfiguration()
        with pytest.raises(ValidationError):
            HeatPumpConfiguration.model_validate({"preset": "made-up"})
        with pytest.raises(ValidationError):
            HeatSourceSettings(kind="heat_pump", max_electric_power_w=3000)
        schema = HeatPumpConfiguration.model_json_schema()
        choices = schema["properties"]["preset"]["anyOf"][0]["enum"]
        assert len(choices) == 26

    def test_physical_source_is_staged_and_has_no_device_protocol_defaults(self):
        source = HeatSourceSettings(device_id="heater1", max_electric_power_w=2000)
        assert source.kind == "electric_room"
        assert source.enabled is False
        assert source.zone_ids == []
        assert source.min_electric_power_w == 0
        assert source.electric_power_measurement_key is None
        with pytest.raises(ValidationError):
            HeatSourceSettings.model_validate({"device_id": "heater1"})

    @pytest.mark.parametrize("value", [0, -1, True, False, float("nan"), float("inf")])
    def test_maximum_power_is_explicit_positive_finite_watts(self, value):
        with pytest.raises(ValidationError):
            HeatSourceSettings.model_validate(
                {"device_id": "heater1", "max_electric_power_w": value}
            )

    @pytest.mark.parametrize("value", [-1, True, float("nan"), float("inf"), 2001])
    def test_minimum_power_is_finite_and_within_maximum(self, value):
        with pytest.raises(ValidationError):
            HeatSourceSettings.model_validate(
                {
                    "device_id": "heater1",
                    "max_electric_power_w": 2000,
                    "min_electric_power_w": value,
                }
            )

    def test_equal_minimum_and_maximum_can_describe_a_fixed_power_source(self):
        source = HeatSourceSettings(
            device_id="heater1",
            max_electric_power_w=1000,
            min_electric_power_w=1000,
            enabled=True,
            zone_ids=["zone1"],
            electric_power_measurement_key="heater_power",
        )
        assert source.min_electric_power_w == source.max_electric_power_w
        assert source.electric_power_measurement_key == "heater_power"

    @pytest.mark.parametrize("zones", [["zone1", "zone1"], [""], [" "]])
    def test_ambiguous_or_blank_zone_bindings_are_rejected(self, zones):
        with pytest.raises(ValidationError):
            HeatSourceSettings.model_validate(
                {"device_id": "heater1", "max_electric_power_w": 1000, "zone_ids": zones}
            )

    def test_enabled_source_requires_a_zone_binding(self):
        with pytest.raises(ValidationError, match="at least one zone ID"):
            HeatSourceSettings.model_validate(
                {"device_id": "heater1", "max_electric_power_w": 1000, "enabled": True}
            )

    @pytest.mark.parametrize("field", ["device_id", "electric_power_measurement_key"])
    def test_source_identifiers_cannot_be_blank(self, field):
        with pytest.raises(ValidationError, match="must not be blank"):
            HeatSourceSettings.model_validate(
                {"device_id": "heater1", "max_electric_power_w": 1000, field: " "}
            )

    def test_source_cannot_configure_ir_transport(self):
        with pytest.raises(ValidationError):
            HeatSourceSettings.model_validate(
                {"device_id": "heater1", "max_electric_power_w": 1000, "ir_protocol": "device"}
            )
