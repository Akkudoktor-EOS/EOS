"""Settings of the device by device optimizer: defaults, validation, loading, scope labels."""

from typing import Any

import pytest
from pydantic import ValidationError

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.config.configabc import ConfigScope
from akkudoktoreos.devices.devices import DevicesCommonSettings
from akkudoktoreos.devices.settings.batterysettings import (
    BATTERY_DEFAULT_DISCHARGE_RATES,
    BatteriesCommonSettings,
)
from akkudoktoreos.devices.settings.gridsettings import GridConnectionSettings
from akkudoktoreos.devices.settings.heatsourcesettings import HeatSourceSettings
from akkudoktoreos.devices.settings.homeappliancesettings import HomeApplianceCommonSettings
from akkudoktoreos.devices.settings.invertersettings import InverterCommonSettings
from akkudoktoreos.optimization.optimization import (
    OptimizationCommonSettings,
)
from akkudoktoreos.optimization.search.settings import SearchSettings
from akkudoktoreos.optimization.searchsettings import SearchCommonSettings, SearchEffort


def battery(**values: Any) -> BatteriesCommonSettings:
    return BatteriesCommonSettings(device_id="bat", **values)


def test_battery_discharge_levels() -> None:
    assert battery().discharge_rates == BATTERY_DEFAULT_DISCHARGE_RATES
    assert BATTERY_DEFAULT_DISCHARGE_RATES == [round(j / 10, 1) for j in range(1, 11)]
    assert battery(discharge_rates=None).discharge_rates == BATTERY_DEFAULT_DISCHARGE_RATES
    assert battery(discharge_rates=[]).discharge_rates == []  # plain on/off
    assert battery(discharge_rates=[1.0]).discharge_rates == [1.0]
    assert battery(discharge_rates=[1.0, 0.5, 0.5, 0.25]).discharge_rates == [0.25, 0.5, 1.0]
    assert battery(discharge_rates="0.5, 1.0").discharge_rates == [0.5, 1.0]
    for invalid in ([0.0, 0.5], [1.5], [-0.1], [float("nan")]):
        with pytest.raises(ValidationError):
            battery(discharge_rates=invalid)


def test_battery_pv_charge_is_always_on_by_default() -> None:
    assert battery().pv_charge_switchable is False
    assert battery(pv_charge_switchable=True).pv_charge_switchable is True


def test_grid_connection_settings() -> None:
    default = GridConnectionSettings()
    assert default.import_limit_w is None and default.export_limit_w is None
    assert default.import_limit_penalty_eur_per_kwh == 10.0
    assert DevicesCommonSettings().grid_connection.model_dump() == default.model_dump()
    limited = GridConnectionSettings(import_limit_w=11000, export_limit_w=7000)
    assert (limited.import_limit_w, limited.export_limit_w) == (11000.0, 7000.0)
    for invalid in ({"import_limit_w": 0}, {"export_limit_w": -1}, {"import_limit_w": "x"}):
        with pytest.raises(ValidationError):
            GridConnectionSettings(**invalid)
    with pytest.raises(ValidationError):
        GridConnectionSettings(import_limit_penalty_eur_per_kwh=-1)


def test_heat_source_settings() -> None:
    source = HeatSourceSettings(device_id="hp", max_electric_power_w=3000)
    assert source.comfort_penalty_eur_per_kh == 100.0
    assert source.protection_penalty_eur_per_kh == 1000.0
    custom = HeatSourceSettings(
        device_id="hp",
        max_electric_power_w=3000,
        comfort_penalty_eur_per_kh=50.0,
    )
    assert custom.comfort_penalty_eur_per_kh == 50.0
    with pytest.raises(ValidationError):
        HeatSourceSettings(device_id="hp", max_electric_power_w=3000, comfort_penalty_eur_per_kh=-1)


def test_the_retired_heat_source_fields_are_dropped_on_load() -> None:
    """A configuration with the retired staged-model fields loads without them, nothing else changes."""
    old = {
        "device_id": "hp",
        "max_electric_power_w": 3000,
        "stages": 7,
        "min_run_seconds": 600,
        "min_pause_seconds": 300,
        "comfort_penalty_eur_per_kh": 50.0,
    }
    loaded = HeatSourceSettings.model_validate(old)
    expected = HeatSourceSettings(
        device_id="hp", max_electric_power_w=3000, comfort_penalty_eur_per_kh=50.0
    )
    assert loaded.model_dump() == expected.model_dump()
    for name in ("stages", "min_run_seconds", "min_pause_seconds"):
        assert name not in HeatSourceSettings.model_fields
        assert name not in loaded.model_dump()
    with pytest.raises(ValidationError):  # other unknown fields stay an error
        HeatSourceSettings.model_validate({**old, "unknown_field": 1})


def test_search_settings() -> None:
    default = SearchCommonSettings()
    assert default.time_budget_seconds is None and default.effort == SearchEffort.NORMAL
    assert OptimizationCommonSettings().search.model_dump() == default.model_dump()
    assert SearchCommonSettings(time_budget_seconds=30, effort=SearchEffort.HIGH).effort == (
        SearchEffort.HIGH
    )
    for invalid in ({"time_budget_seconds": 0}, {"effort": "EXTREME"}):
        with pytest.raises(ValidationError):
            SearchCommonSettings(**invalid)


def test_the_settings_load_from_a_configuration(config_eos: ConfigEOS) -> None:
    config_eos.merge_settings_from_dict(
        {
            "optimization": {
                "search": {"time_budget_seconds": 12.5, "effort": "LOW"},
            },
            "devices": {
                "grid_connection": {"import_limit_w": 9000, "import_limit_penalty_eur_per_kwh": 5},
                "batteries": {
                    "bat1": {
                        "profile_adjustment": {"nominal_capacity_wh": 8000},
                        "discharge_rates": [0.5, 1.0],
                        "pv_charge_switchable": True,
                    }
                },
            },
        }
    )
    assert config_eos.optimization.search.time_budget_seconds == 12.5
    assert config_eos.optimization.search.effort == SearchEffort.LOW
    assert config_eos.devices.grid_connection.import_limit_w == 9000.0
    assert config_eos.devices.grid_connection.import_limit_penalty_eur_per_kwh == 5.0
    assert config_eos.devices.batteries is not None
    assert config_eos.devices.batteries["bat1"].discharge_rates == [0.5, 1.0]
    assert config_eos.devices.batteries["bat1"].pv_charge_switchable is True
    # The search settings of the algorithm can hold the levels of the battery.
    levels = SearchSettings(battery_discharge_levels=((0.5, 1.0),), battery_pv_switchable=(True,))
    assert levels.discharge_levels_of(0) == (0.5, 1.0)
    assert levels.discharge_levels_of(1) == SearchSettings().discharge_levels
    assert levels.pv_switchable_of(0)
    assert levels.pv_switchable_of(1) == SearchSettings().pv_charge_switchable
    with pytest.raises(ValueError, match="battery_discharge_levels"):
        SearchSettings(battery_discharge_levels=((0.0, 1.0),))


def scope(model: Any, field: str) -> list[str]:
    extra = model.model_fields[field].json_schema_extra
    assert isinstance(extra, dict)
    return list(extra["x-scope"])


def test_the_scope_labels_name_the_new_algorithm() -> None:
    label = str(ConfigScope.OHMRAKEL42)
    for model, field in (
        (BatteriesCommonSettings, "discharge_rates"),
        (BatteriesCommonSettings, "pv_charge_switchable"),
        (GridConnectionSettings, "import_limit_w"),
        (GridConnectionSettings, "export_limit_w"),
        (GridConnectionSettings, "import_limit_penalty_eur_per_kwh"),
        (HeatSourceSettings, "comfort_penalty_eur_per_kh"),
        (HeatSourceSettings, "protection_penalty_eur_per_kh"),
        (SearchCommonSettings, "time_budget_seconds"),
        (SearchCommonSettings, "effort"),
        (DevicesCommonSettings, "grid_connection"),
        (DevicesCommonSettings, "heat_sources"),
        # Fields the former optimizers use are used by this one, too.
        (HomeApplianceCommonSettings, "consumption_wh"),
        (HomeApplianceCommonSettings, "cycle_time_windows"),
        (HomeApplianceCommonSettings, "deadline_datetime"),
        (BatteriesCommonSettings, "min_soc_deadline_datetime"),
        (BatteriesCommonSettings, "full_charge_soc_percentage"),
        (InverterCommonSettings, "inverter_profile"),
        (DevicesCommonSettings, "batteries"),
    ):
        assert label in scope(model, field), (model.__name__, field)


@pytest.mark.parametrize(
    ("old", "expected"),
    [
        (
            {
                "algorithm": "GENETIC",
                "genetic": {
                    "interval_sec": 900,
                    "horizon_hours": 25,
                    "tail_horizon_hours": 12,
                    "terminal_value_mode": "FIXED",
                    "terminal_value_euro_per_kwh": 0.2,
                    "measurement_max_age_seconds": 123,
                    "penalties": {"ev_soc_miss": 15},
                    "workers": 2,
                },
                "genetic0": {"horizon_hours": 30},
            },
            {
                "interval_sec": 900,
                "horizon_hours": 25,
                "tail_horizon_hours": 12,
                "terminal_value_mode": "FIXED",
                "terminal_value_euro_per_kwh": 0.2,
                "measurement_max_age_seconds": 123,
                "penalties": {"ev_soc_miss": 15},
            },
        ),
        (
            {
                "algorithm": "GENETIC0",
                "genetic0": {"horizon_hours": 19, "penalties": {"ev_soc_miss": 17}},
                "genetic": {"interval_sec": 900, "horizon_hours": 25},
            },
            {
                "interval_sec": 3600,
                "horizon_hours": 19,
                "terminal_value_mode": "FIXED",
                "tail_horizon_hours": 0,
                "penalties": {"ev_soc_miss": 17},
            },
        ),
        (
            {"hours": 21, "penalty": 18},
            {"horizon_hours": 21, "tail_horizon_hours": 0, "penalties": {"ev_soc_miss": 18}},
        ),
        (
            {"algorithm": "OHMRAKEL42", "genetic": {"interval_sec": 900}, "horizon_hours": 17},
            {"interval_sec": 900, "horizon_hours": 17},
        ),
    ],
)
def test_optimizer_migration_from_existing_file(tmp_path, old, expected):
    import json

    from akkudoktoreos.config.config import SettingsEOSDefaults
    from akkudoktoreos.config.configmigrate import migrate_config_file

    config = tmp_path / "EOS.config.json"
    backup = tmp_path / "EOS.config.backup.json"
    initial = {
        "general": {"version": "0.0.0"},
        "optimization": old,
        "ems": {"interval": 420},
    }
    config.write_text(json.dumps(initial), encoding="utf8")
    assert migrate_config_file(config, backup)
    assert json.loads(backup.read_text(encoding="utf8")) == initial
    stored = json.loads(config.read_text(encoding="utf8"))
    persisted = stored["optimization"]
    assert not {"algorithm", "genetic", "genetic0", "workers"} & persisted.keys()
    loaded = SettingsEOSDefaults.model_validate(stored)
    settings = loaded.optimization.model_dump()
    assert {key: settings[key] for key in expected} == expected
    assert loaded.ems.interval == 420
    assert migrate_config_file(config, backup)
    assert json.loads(config.read_text(encoding="utf8")) == stored


def test_invalid_legacy_optimizer_settings_leave_the_original_file_in_place(tmp_path):
    from akkudoktoreos.config.configmigrate import migrate_config_file

    config = tmp_path / "EOS.config.json"
    backup = tmp_path / "EOS.config.backup.json"
    original = '{"optimization":{"algorithm":"GENETIC","genetic":{"interval_sec":600}}}'
    config.write_text(original, encoding="utf8")

    assert not migrate_config_file(config, backup)
    assert config.read_text(encoding="utf8") == original
    assert not backup.exists()
