"""Configuration compatibility for staged thermal data, without a server or devices."""

import json
import re
from asyncio import Lock
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from akkudoktoreos.config.config import ConfigEOS, SettingsEOS, SettingsEOSDefaults
from akkudoktoreos.core import ems as emsmodule
from akkudoktoreos.core.ems import EnergyManagement, EnergyManagementStage
from akkudoktoreos.core.emsettings import EnergyManagementMode
from akkudoktoreos.optimization import fleetrequest
from akkudoktoreos.optimization.fleet import FleetRequest
from akkudoktoreos.optimization.fleetrequest import FleetConfigRequest
from akkudoktoreos.optimization.genetic import configrequest
from akkudoktoreos.optimization.genetic.configrequest import ConfigOptimizationRequest
from akkudoktoreos.optimization.optimization import OptimizationAlgorithm
from akkudoktoreos.utils.datetimeutil import to_datetime


def _staged_config() -> dict[str, Any]:
    """Return generic complete descriptions; global planning remains off."""
    return {
        "thermal": {
            "mode": "off",
            "zones": {
                "zone-a": {
                    "enabled": True,
                    "room_temperature_measurement_key": "zone-a-room",
                    "presence_measurement_key": "zone-a-presence",
                    "model_reference": "model-a",
                    "comfort": {
                        "min_temperature_c": 19.0,
                        "target_temperature_c": 20.5,
                        "max_temperature_c": 21.0,
                    },
                }
            },
        },
        "devices": {
            "heat_sources": {
                "heat-a": {
                    "enabled": True,
                    "max_electric_power_w": 2000,
                    "electric_power_measurement_key": "heat-a-power",
                    "zone_ids": ["zone-a"],
                }
            }
        },
    }


def test_omitted_thermal_config_preserves_partial_and_disabled_defaults(
    config_eos: ConfigEOS,
) -> None:
    partial = SettingsEOS.model_validate({})
    assert partial.thermal is None
    assert config_eos.thermal.mode == "off"
    assert config_eos.thermal.zones == {}
    assert config_eos.devices.heat_sources == {}
    saved = json.loads(
        config_eos.model_dump_json(exclude_defaults=True, exclude_computed_fields=True)
    )
    assert saved.get("thermal", {}).get("mode", "off") == "off"


def test_staged_ids_and_json_round_trip_do_not_mutate_input(config_eos: ConfigEOS) -> None:
    supplied = _staged_config()
    before = deepcopy(supplied)
    config_eos.merge_settings_from_dict(supplied)
    assert supplied == before
    assert config_eos.thermal.zones["zone-a"].zone_id == "zone-a"
    assert config_eos.devices.heat_sources["heat-a"].device_id == "heat-a"
    encoded = config_eos.model_dump_json(exclude_computed_fields=True)
    restored = SettingsEOSDefaults.model_validate_json(encoded)
    # SettingsBaseModel has a private per-instance UUID; compare the persisted contract.
    assert restored.thermal.model_dump_json() == config_eos.thermal.model_dump_json()
    assert (
        restored.devices.model_dump(exclude_computed_fields=True)["heat_sources"]
        == (config_eos.devices.model_dump(exclude_computed_fields=True)["heat_sources"])
    )
    assert restored.thermal.mode == "off"
    assert "zone_id" not in supplied["thermal"]["zones"]["zone-a"]
    assert "device_id" not in supplied["devices"]["heat_sources"]["heat-a"]


def test_optional_coasting_and_total_budget_survive_native_config_round_trip(config_eos: ConfigEOS):
    config_eos.merge_settings_from_dict(
        {
            "thermal": {
                "planning_time_limit_seconds": 45.5,
                "zones": {"zone-a": {"allow_warm_coasting": True}},
            }
        }
    )
    restored = SettingsEOSDefaults.model_validate_json(
        config_eos.model_dump_json(exclude_computed_fields=True)
    )
    assert restored.thermal.planning_time_limit_seconds == 45.5
    assert restored.thermal.zones["zone-a"].allow_warm_coasting is True
    assert restored.thermal.mode == "off"


def test_staged_heat_sources_do_not_register_measurements(config_eos: ConfigEOS) -> None:
    before = list(config_eos.devices.measurement_keys)
    serialized = config_eos.devices.model_dump(exclude_computed_fields=True)
    old_devices = {
        name: serialized[name]
        for name in ("batteries", "electric_vehicles", "inverters", "home_appliances")
    }
    config_eos.merge_settings_from_dict(_staged_config())
    assert config_eos.devices.measurement_keys == before
    assert "heat-a-power" not in config_eos.devices.measurement_keys
    assert "zone-a-room" not in config_eos.devices.measurement_keys
    for name, old in old_devices.items():
        assert config_eos.devices.model_dump(exclude_computed_fields=True)[name] == old


@pytest.mark.parametrize("wrong_reference", ["zone-a-id", "heat-a-id", "unknown-zone"])
def test_cross_references_reject_mismatched_ids(
    config_eos: ConfigEOS, wrong_reference: str
) -> None:
    values = _staged_config()
    if wrong_reference == "zone-a-id":
        values["thermal"]["zones"]["zone-a"]["zone_id"] = "another-zone"
    elif wrong_reference == "heat-a-id":
        values["devices"]["heat_sources"]["heat-a"]["device_id"] = "another-source"
    else:
        values["devices"]["heat_sources"]["heat-a"]["zone_ids"] = ["missing-zone"]
    before = deepcopy(values)
    with pytest.raises(ValidationError):
        SettingsEOSDefaults.model_validate(values)
    assert values == before


def test_heat_source_id_cannot_collide_with_existing_device(config_eos: ConfigEOS) -> None:
    values = _staged_config()
    values["devices"]["batteries"] = {"heat-a": {"capacity_wh": 1000}}
    with pytest.raises(ValidationError, match="unique across device groups"):
        SettingsEOSDefaults.model_validate(values)


def test_optional_device_limits_and_missing_groups_remain_valid(config_eos: ConfigEOS) -> None:
    values = _staged_config()
    for name in ("batteries", "electric_vehicles", "inverters", "home_appliances"):
        values["devices"][name] = {} if name == "home_appliances" else None
        values["devices"]["max_" + name] = None
    restored = SettingsEOSDefaults.model_validate(values)
    assert restored.devices.heat_sources["heat-a"].zone_ids == ["zone-a"]
    assert restored.thermal.mode == "off"


def test_public_schema_exposes_disabled_thermal_contract_without_invented_power() -> None:
    schema = ConfigEOS.model_json_schema()
    assert "thermal" in schema["properties"]
    definitions = schema["$defs"]
    mode = definitions["ThermalCommonSettings"]["properties"]["mode"]
    assert mode["default"] == "off"
    assert set(mode["enum"]) == {"off", "shadow", "control"}
    assert "max_electric_power_w" in definitions["HeatSourceSettings"]["required"]
    assert definitions["HeatSourceSettings"]["properties"]["enabled"]["default"] is False
    assert definitions["ThermalZoneSettings"]["properties"]["enabled"]["default"] is False


def test_documented_config_json_preserves_explicit_planning_mode(config_eos: ConfigEOS) -> None:
    from akkudoktoreos.thermal.thermalconfigrequest import ThermalConfigRequest

    path = Path(__file__).parents[1] / "docs/akkudoktoreos/thermalconfiguration.md"
    examples = re.findall(r"```json\s*(.*?)```", path.read_text(encoding="utf-8"), re.DOTALL)
    assert examples
    for example in examples:
        values = json.loads(example)
        original = deepcopy(values)
        if {"zone_id", "heat_source_id", "basis_control_revision"} <= values.keys():
            intent = ThermalConfigRequest.model_validate(values)
            assert intent.master_power == values.get("master_power", "On")
        else:
            validated = SettingsEOSDefaults.model_validate(values)
            assert validated.thermal.mode == values.get("thermal", {}).get("mode", "off")
        assert values == original


@pytest.mark.asyncio
@pytest.mark.parametrize("thermal_mode", ["off", "shadow"])
async def test_standard_staged_config_leaves_resolved_fleet_request_identical(
    config_eos: ConfigEOS, monkeypatch: pytest.MonkeyPatch, thermal_mode: str
) -> None:
    config_eos.merge_settings_from_dict(
        {
            "optimization": {
                "fleet": {
                    "horizon_hours": 1,
                    "interval_sec": 900,
                    "import_limit_w": 4000,
                    "export_limit_w": 1000,
                    "prevent_export_at_negative_market_price": False,
                }
            },
            "devices": {
                "max_batteries": None,
                "batteries": {"storage-a": {"capacity_wh": 1000, "max_charge_power_w": 500}},
                "electric_vehicles": {},
                "inverters": {},
                "home_appliances": {},
            },
        }
    )
    start = to_datetime("2026-01-10T10:15:00Z", in_timezone="UTC")
    monkeypatch.setattr(
        fleetrequest,
        "get_ems",
        lambda: SimpleNamespace(start_datetime=start, observation_datetime=start),
    )
    measurement = Mock(key_to_lists=AsyncMock(side_effect=AssertionError("unexpected measurement")))
    prediction = Mock(update_data=AsyncMock(side_effect=AssertionError("unexpected provider")))
    monkeypatch.setattr(FleetConfigRequest, "measurement", measurement)
    monkeypatch.setattr(FleetConfigRequest, "prediction", prediction)
    data = {
        "soc": {"storage-a": 50},
        "forecasts": {
            "total_load": [200.0] * 96,
            "pv_forecast_wh": [100.0] * 96,
            "electricity_price_per_wh": [0.0003] * 96,
            "feed_in_tariff_per_wh": [0.0001] * 96,
        },
    }
    original_data = deepcopy(data)
    request = FleetConfigRequest.model_validate(data)
    before = await request.resolve()
    staged = _staged_config()
    staged["thermal"]["mode"] = thermal_mode
    config_eos.merge_settings_from_dict(staged)
    config_before = config_eos.model_dump_json()
    after = await request.resolve()
    assert before.model_dump_json() == after.model_dump_json()
    assert after.load_w == [800.0] * 4
    assert [device.device_id for device in after.devices] == ["storage-a"]
    assert data == original_data
    assert config_eos.model_dump_json() == config_before
    measurement.key_to_lists.assert_not_awaited()
    prediction.update_data.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["control"])
@pytest.mark.parametrize("resolver", ["fleet", "genetic"])
async def test_control_mode_fails_before_resolver_data_access(
    config_eos: ConfigEOS, monkeypatch: pytest.MonkeyPatch, mode: str, resolver: str
) -> None:
    config_eos.merge_settings_from_dict({"thermal": {"mode": mode}})
    cls = FleetConfigRequest if resolver == "fleet" else ConfigOptimizationRequest
    module = fleetrequest if resolver == "fleet" else configrequest
    get_ems = Mock(side_effect=AssertionError("unexpected EMS lookup"))
    measurement = Mock(key_to_lists=AsyncMock())
    prediction = Mock(update_data=AsyncMock())
    monkeypatch.setattr(module, "get_ems", get_ems)
    monkeypatch.setattr(cls, "measurement", measurement)
    monkeypatch.setattr(cls, "prediction", prediction)
    with pytest.raises(ValueError, match="not implemented"):
        await cls().resolve()
    get_ems.assert_not_called()
    measurement.key_to_lists.assert_not_awaited()
    prediction.update_data.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["control"])
@pytest.mark.parametrize("algorithm", list(OptimizationAlgorithm))
async def test_control_mode_aborts_ems_before_cache_or_adapter_and_preserves_plan(
    config_eos: ConfigEOS,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    algorithm: OptimizationAlgorithm,
) -> None:
    config_eos.merge_settings_from_dict({"thermal": {"mode": mode}})
    ems = EnergyManagement()
    monkeypatch.setattr(EnergyManagement, "_run_lock", Lock())
    monkeypatch.setattr(EnergyManagement, "_stage", EnergyManagementStage.IDLE)
    previous = Mock(name="previous_plan")
    monkeypatch.setattr(EnergyManagement, "_plan", previous)
    old_solution = Mock(name="previous_solution")
    monkeypatch.setattr(EnergyManagement, "_optimization_solution", old_solution)
    cache = Mock(side_effect=AssertionError("unexpected cache access"))
    monkeypatch.setattr(emsmodule, "CacheEnergyManagementStore", cache)
    adapter = Mock(update_data=AsyncMock())
    prediction = Mock(update_data=AsyncMock())
    monkeypatch.setattr(EnergyManagement, "adapter", adapter)
    monkeypatch.setattr(EnergyManagement, "prediction", prediction)
    start = ems.start_datetime
    with pytest.raises(ValueError, match="not implemented"):
        await ems.run(mode=EnergyManagementMode.OPTIMIZATION, algorithm=algorithm)
    cache.assert_not_called()
    adapter.update_data.assert_not_awaited()
    prediction.update_data.assert_not_awaited()
    assert ems.plan() is previous
    assert ems.optimization_solution() is old_solution
    assert ems.start_datetime == start
    assert ems.stage() == EnergyManagementStage.IDLE


@pytest.mark.parametrize("field", ["thermal", "heat_sources", "thermal_plan"])
def test_pure_fleet_request_rejects_unimplemented_thermal_fields(field: str) -> None:
    data = {
        "start_datetime": "2026-01-10T08:00:00Z",
        "load_w": [500],
        "pv_w": [0],
        "import_price_eur_per_kwh": [0.3],
        "export_price_eur_per_kwh": [0.1],
        "import_limit_w": 2000,
        "export_limit_w": 1000,
        field: {},
    }
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        FleetRequest.model_validate(data)
