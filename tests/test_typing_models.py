"""Regression coverage for model typing and its runtime validation boundaries."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pendulum
import pytest

from akkudoktoreos.config.config import GeneralSettings
from akkudoktoreos.config.configabc import TimeWindow, TimeWindowSequence
from akkudoktoreos.core.cachesettings import CacheCommonSettings
from akkudoktoreos.core.dataabc import DataRecord, DataSequence
from akkudoktoreos.core.databaseabc import DatabaseTimestamp
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
        DatabaseTimestamp.from_datetime(None)


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
