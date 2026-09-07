"""Regression coverage for model typing and its runtime validation boundaries."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pendulum
import pytest

from akkudoktoreos.adapter import homeassistant
from akkudoktoreos.adapter.homeassistant import (
    HomeAssistantAdapter,
    HomeAssistantAdapterCommonSettings,
)
from akkudoktoreos.adapter.nodered import NodeREDAdapter
from akkudoktoreos.config.config import ConfigEOS, GeneralSettings
from akkudoktoreos.config.configabc import TimeWindow, TimeWindowSequence
from akkudoktoreos.core.cachesettings import CacheCommonSettings
from akkudoktoreos.core.dataabc import DataRecord, DataSequence
from akkudoktoreos.core.databaseabc import DatabaseTimestamp
from akkudoktoreos.core.ems import EnergyManagement
from akkudoktoreos.core.pydantic import PydanticDateTimeData
from akkudoktoreos.devices.devices import (
    BATTERY_DEFAULT_CHARGE_RATES,
    BatteriesCommonSettings,
)
from akkudoktoreos.measurement.measurement import Measurement, MeasurementDataRecord
from akkudoktoreos.prediction.elecpricefixed import ElecPriceFixed
from akkudoktoreos.prediction.pvforecastforecastsolar import PVForecastForecastSolar
from akkudoktoreos.prediction.pvforecastpvnode import PVForecastPVNode
from akkudoktoreos.server import eos
from akkudoktoreos.server.rest.error import EOSProblem
from akkudoktoreos.utils.datetimeutil import to_datetime


def test_path_defaults_keep_the_json_representation() -> None:
    cache = CacheCommonSettings()
    general = GeneralSettings()
    assert cache.subpath == Path("cache")
    assert general.data_output_subpath == Path("output")
    assert cache.model_dump(mode="json")["subpath"] == "cache"
    assert general.model_dump(mode="json")["data_output_subpath"] == "output"
    assert general.model_dump(mode="json", exclude_defaults=True)["data_output_subpath"] == "output"


def test_generic_collections_keep_runtime_field_types() -> None:
    assert DataSequence._get_key_types(DataSequence, "records") == [list, DataRecord]
    assert TimeWindowSequence._get_key_types(TimeWindowSequence, "windows") == [list, TimeWindow]


def test_default_charge_rates_are_an_independent_float_array() -> None:
    rates = BatteriesCommonSettings.validate_and_sort_charge_rates(None)
    assert isinstance(rates, np.ndarray)
    np.testing.assert_array_equal(rates, BATTERY_DEFAULT_CHARGE_RATES)
    rates[0] = 0.5
    assert BATTERY_DEFAULT_CHARGE_RATES[0] == 0.0


def test_time_series_metadata_is_normalized_without_changing_wire_types() -> None:
    model = PydanticDateTimeData.model_validate(
        {"start_datetime": "2024-01-01T00:00:00Z", "interval": "1 hour", "values": [1, 2]}
    )
    assert isinstance(model.root["start_datetime"], pendulum.DateTime)
    assert isinstance(model.root["interval"], pendulum.Duration)
    schema = PydanticDateTimeData.model_json_schema()
    assert schema["additionalProperties"]["anyOf"][0] == {"type": "string"}


def test_database_timestamp_requires_a_datetime() -> None:
    with pytest.raises(ValueError, match="Timezone-aware datetime required"):
        DatabaseTimestamp.from_datetime(None)  # type: ignore[arg-type]  # Invalid input on purpose.


@pytest.mark.asyncio
async def test_database_insert_rejects_a_record_without_a_timestamp() -> None:
    sequence = DataSequence[DataRecord]()
    with pytest.raises(ValueError, match="Database records require a datetime"):
        await sequence.db_insert_record(DataRecord())
    assert sequence.records == []


@pytest.mark.parametrize(
    "property_name",
    ["homeassistant_entity_ids", "eos_solution_entity_ids", "eos_device_instruction_entity_ids"],
)
def test_homeassistant_settings_reject_a_misregistered_provider(
    monkeypatch: pytest.MonkeyPatch, property_name: str
) -> None:
    adapter = MagicMock()
    adapter.provider_by_id.return_value = NodeREDAdapter()
    monkeypatch.setattr(homeassistant, "get_adapter", lambda: adapter)

    settings = HomeAssistantAdapterCommonSettings()
    with pytest.raises(TypeError, match="HomeAssistant provider must be a HomeAssistantAdapter"):
        getattr(settings, property_name)


@pytest.mark.parametrize(
    "property_name",
    ["homeassistant_entity_ids", "eos_solution_entity_ids", "eos_device_instruction_entity_ids"],
)
def test_homeassistant_settings_preserve_entity_lookup_and_unavailability(
    monkeypatch: pytest.MonkeyPatch, property_name: str
) -> None:
    provider = HomeAssistantAdapter()
    lookup = MagicMock(return_value=["sensor.eos_example"])
    monkeypatch.setattr(HomeAssistantAdapter, f"get_{property_name}", lookup)
    adapter = MagicMock()
    adapter.provider_by_id.return_value = provider
    monkeypatch.setattr(homeassistant, "get_adapter", lambda: adapter)

    settings = HomeAssistantAdapterCommonSettings()
    assert getattr(settings, property_name) == ["sensor.eos_example"]
    lookup.side_effect = ConnectionError("Home Assistant is unavailable")
    assert getattr(settings, property_name) == []
    adapter.provider_by_id.side_effect = ValueError("Provider unavailable during initialization")
    assert getattr(settings, property_name) == []


@pytest.mark.parametrize("operation", ["revert_settings", "list_backups"])
def test_backup_operations_report_an_uninitialized_path_as_an_invariant_failure(
    config_eos: ConfigEOS, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    monkeypatch.setattr(ConfigEOS, "_config_file_path", None)
    with pytest.raises(AssertionError, match="Configuration file path is not initialized"):
        if operation == "revert_settings":
            config_eos.revert_settings("missing")
        else:
            config_eos.list_backups()


def test_energy_management_initializes_its_start_datetime_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = MagicMock(return_value=to_datetime("2024-01-01T12:34:56+01:00"))
    monkeypatch.setattr("akkudoktoreos.core.ems.to_datetime", clock)
    monkeypatch.setattr(EnergyManagement, "_start_datetime", None)
    ems = EnergyManagement()

    first = ems.start_datetime
    assert first == to_datetime("2024-01-01T12:00:00+01:00")
    assert ems.start_datetime is first
    clock.assert_called_once_with()


def test_dashboard_details_accept_top_level_settings_and_nested_eos_models(
    config_eos: ConfigEOS,
) -> None:
    from akkudoktoreos.server.dash.configuration import create_config_details

    values = config_eos.model_dump(mode="json")
    top_level = create_config_details(ConfigEOS, values)
    nested = create_config_details(GeneralSettings, values, ["general"])
    assert top_level["general.latitude"] == nested["general.latitude"]


@pytest.mark.asyncio
async def test_energy_calculation_requires_resolvable_record_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    measurement = Measurement()
    measurement.records = [MeasurementDataRecord()]
    monkeypatch.setattr(Measurement, "min_datetime", AsyncMock(return_value=None))
    monkeypatch.setattr(Measurement, "max_datetime", AsyncMock(return_value=None))
    with pytest.raises(ValueError, match="Start and end datetimes are required"):
        await measurement.load_total_kwh()


@pytest.mark.asyncio
async def test_prediction_import_rejects_a_provider_without_import_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = MagicMock(spec=ElecPriceFixed)
    provider.enabled.return_value = True
    prediction = MagicMock()
    prediction.provider_by_id.return_value = provider
    monkeypatch.setattr(eos, "get_prediction", lambda: prediction)
    with pytest.raises(EOSProblem, match="does not support data imports") as exc_info:
        await eos.fastapi_prediction_import_provider(
            provider_id="ElecPriceFixed", data={"values": [1]}
        )
    assert exc_info.value.status == 400


@pytest.mark.parametrize("provider_type", [PVForecastPVNode, PVForecastForecastSolar])
def test_pv_timestamp_parsing_rejects_durations(provider_type: type) -> None:
    with pytest.raises(ValueError, match="Expected a datetime"):
        provider_type()._to_utc_datetime("PT1H", "UTC")
