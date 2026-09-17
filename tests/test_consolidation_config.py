"""Contracts required by the combined EOS configuration."""
import copy

import pytest
from pydantic import ValidationError

from akkudoktoreos.config.configmigrate import migrate_config_data
from akkudoktoreos.devices.devices import DevicesCommonSettings
from akkudoktoreos.devices.settings.invertersettings import InverterCommonSettings


def test_device_map_supplies_stable_identity():
    raw = {"batteries": {"house": {"capacity_wh": 12000}}}
    a = DevicesCommonSettings.model_validate(raw)
    b = DevicesCommonSettings.model_validate(raw)
    assert a.batteries is not None and b.batteries is not None
    assert a.batteries["house"].device_id == b.batteries["house"].device_id == "house"
    assert "house-soc-factor" in a.measurement_keys


def test_device_map_rejects_conflicting_identity():
    with pytest.raises(ValidationError, match="device_id"):
        DevicesCommonSettings.model_validate({"batteries": {"house": {"device_id": "other"}}})


@pytest.mark.parametrize("as_list", [False, True])
def test_migration_preserves_lcos_and_input(as_list):
    battery = {"device_id": "house", "capacity_wh": 12000,
               "levelized_cost_of_storage_kwh": 0.123}
    raw = {"devices": {"batteries": [battery] if as_list else {"house": battery}}}
    original = copy.deepcopy(raw)
    migrated = migrate_config_data(raw)
    assert migrated.devices.batteries is not None
    assert migrated.devices.batteries["house"].levelized_cost_of_storage_amt_kwh == 0.123
    assert raw == original


@pytest.mark.parametrize("converter", ["to_genetic_param", "to_genetic0_param"])
def test_inverter_conversion_requires_output_limit(converter):
    settings = InverterCommonSettings(device_id="inverter")
    with pytest.raises(ValueError, match="max_power_w"):
        getattr(settings, converter)()
    settings.max_power_w = 4200
    assert getattr(settings, converter)().max_power_wh == 4200
