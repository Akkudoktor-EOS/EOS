from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from akkudoktoreos.adapter.homeassistant import HomeAssistantAdapter
from akkudoktoreos.core.ems import EnergyManagementStage


@pytest.fixture(autouse=True)
def cec_databases():
    """This adapter test needs no CEC reference-device data."""
    yield None


class _AdapterHarness:
    """Provide synthetic adapter dependencies without a Home Assistant instance."""

    _update_data = HomeAssistantAdapter._update_data
    _resolve_device_measurement_key = staticmethod(
        HomeAssistantAdapter._resolve_device_measurement_key
    )

    def __init__(self, configured_key: str, registered_keys: set[str]) -> None:
        homeassistant = SimpleNamespace(
            config_entity_ids=None,
            device_measurement_entity_ids={configured_key: "sensor.synthetic_battery_soc"},
            load_emr_entity_ids=None,
            grid_export_emr_entity_ids=None,
            grid_import_emr_entity_ids=None,
            pv_production_emr_entity_ids=None,
        )
        self.config = SimpleNamespace(
            adapter=SimpleNamespace(homeassistant=homeassistant),
            devices=SimpleNamespace(measurement_keys=list(registered_keys)),
        )
        self.ems = SimpleNamespace(stage=lambda: EnergyManagementStage.DATA_ACQUISITION)
        self.ems_start_datetime = object()
        self.measurement = SimpleNamespace(update_value=AsyncMock())
        self.get_entity_state = lambda _entity_id: "0.75"
        self.update_datetime = None


@pytest.mark.asyncio
async def test_homeassistant_maps_legacy_soc_key_to_registered_key() -> None:
    adapter = _AdapterHarness("battery1_soc_factor", {"battery1-soc-factor"})

    await HomeAssistantAdapter._update_data(cast(HomeAssistantAdapter, adapter))

    adapter.measurement.update_value.assert_awaited_once_with(
        adapter.ems_start_datetime, "battery1-soc-factor", 0.75
    )


def test_canonical_key_is_preserved() -> None:
    assert HomeAssistantAdapter._resolve_device_measurement_key(
        "battery1-soc-factor", {"battery1-soc-factor"}
    ) == "battery1-soc-factor"


def test_unknown_custom_key_is_preserved() -> None:
    assert HomeAssistantAdapter._resolve_device_measurement_key(
        "custom_measurement", {"battery1-soc-factor"}
    ) == "custom_measurement"


def test_ambiguous_legacy_key_is_not_guessed() -> None:
    key = "battery_1_soc_factor"
    registered_keys = {"battery-1-soc-factor", "battery_1-soc-factor"}

    assert HomeAssistantAdapter._resolve_device_measurement_key(key, registered_keys) == key
