"""Loading and cross-reference validation of thermal, heat-source and presence settings."""

# ruff: noqa: S101 -- pytest assertions are the test contract.

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from akkudoktoreos.config.config import SettingsEOS, SettingsEOSDefaults

SETTINGS: dict[str, Any] = {
    "measurement": {
        "channels": {
            "room_temperature": {"quantity": "temperature", "unit": "degC"},
            "presence_living": {"quantity": "presence", "unit": "1"},
        }
    },
    "thermal": {
        "mode": "shadow",
        "zones": {
            "living": {
                "zone_id": "living",
                "enabled": True,
                "room_temperature_measurement_key": "room_temperature",
                "model_reference": "model-v1",
                "presence_area_id": "living",
                "comfort": {
                    "min_temperature_c": 19.25,
                    "target_temperature_c": 20.45,
                    "max_temperature_c": 20.55,
                },
            }
        },
    },
    "devices": {
        "heat_sources": {
            "hp1": {
                "device_id": "hp1",
                "kind": "heat_pump",
                "enabled": True,
                "max_electric_power_w": 2000,
                "zone_ids": ["living"],
                "heat_pump": {"preset": "aira_8", "delivery_temperature_c": 35},
            }
        }
    },
    "presence": {
        "provider": "PresenceHistory",
        "zones": {"living": {"measurement_key": "presence_living"}},
    },
}


def settings(**changes: Any) -> dict[str, Any]:
    """Return a deep copy of the valid settings with top-level blocks replaced."""
    data = deepcopy(SETTINGS)
    data.update(changes)
    return data


class TestThermalConfigBlocks:
    def test_defaults_leave_heat_features_off(self):
        config = SettingsEOSDefaults()
        assert config.thermal.mode == "off"
        assert config.thermal.zones == {}
        assert config.devices.heat_sources == {}
        assert config.presence.provider is None

    def test_full_configuration_loads(self):
        config = SettingsEOSDefaults.model_validate(SETTINGS)
        assert config.thermal.zones["living"].enabled
        source = config.devices.heat_sources["hp1"]
        assert source.kind == "heat_pump"
        assert source.heat_pump is not None
        assert source.heat_pump.resolve_profile() is not None
        assert config.presence.provider == "PresenceHistory"

    def test_optional_settings_model_accepts_the_blocks(self):
        config = SettingsEOS.model_validate(
            {"thermal": SETTINGS["thermal"], "presence": SETTINGS["presence"]}
        )
        assert config.thermal is not None and config.presence is not None

    def test_heat_source_with_unknown_zone_is_rejected(self):
        data = settings()
        data["devices"]["heat_sources"]["hp1"]["zone_ids"] = ["missing"]
        with pytest.raises(ValidationError, match="unknown thermal zones"):
            SettingsEOSDefaults.model_validate(data)

    def test_two_heat_sources_for_one_zone_are_rejected(self):
        """One zone has one room temperature; two sources would give it two heat plans."""
        data = settings()
        data["devices"]["heat_sources"]["hp2"] = {
            **data["devices"]["heat_sources"]["hp1"],
            "device_id": "hp2",
        }
        with pytest.raises(ValidationError) as caught:
            SettingsEOSDefaults.model_validate(data)
        message = str(caught.value)
        assert "'living'" in message and "'hp1'" in message and "'hp2'" in message

    def test_one_heat_source_per_zone_stays_valid(self):
        data = settings()
        data["thermal"]["zones"]["bedroom"] = {
            **data["thermal"]["zones"]["living"],
            "zone_id": "bedroom",
            "presence_area_id": None,
        }
        data["devices"]["heat_sources"]["hp2"] = {
            **data["devices"]["heat_sources"]["hp1"],
            "device_id": "hp2",
            "zone_ids": ["bedroom"],
        }
        config = SettingsEOSDefaults.model_validate(data)
        assert set(config.devices.heat_sources) == {"hp1", "hp2"}
        # A disabled source plans nothing and may name the zone of another one.
        data["devices"]["heat_sources"]["hp2"].update(zone_ids=["living"], enabled=False)
        assert SettingsEOSDefaults.model_validate(data).devices.heat_sources["hp2"].zone_ids == [
            "living"
        ]

    def test_zone_with_unknown_presence_area_is_rejected(self):
        data = settings()
        data["thermal"]["zones"]["living"]["presence_area_id"] = "elsewhere"
        with pytest.raises(ValidationError, match="unknown presence area"):
            SettingsEOSDefaults.model_validate(data)

    def test_presence_provider_requires_typed_presence_channel(self):
        data = settings()
        data["measurement"]["channels"]["presence_living"] = {
            "quantity": "temperature",
            "unit": "degC",
        }
        with pytest.raises(ValidationError, match="typed EOS presence measurement channel"):
            SettingsEOSDefaults.model_validate(data)

    def test_heat_source_id_must_not_clash_with_another_device(self):
        data = settings()
        data["devices"]["batteries"] = {"hp1": {"device_id": "hp1", "capacity_wh": 1000}}
        with pytest.raises(ValidationError, match="unique across device groups"):
            SettingsEOSDefaults.model_validate(data)
