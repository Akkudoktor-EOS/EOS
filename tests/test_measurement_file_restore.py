"""Regression coverage for measurement persistence through the JSON fallback."""

import json
from unittest.mock import AsyncMock

import pytest

from akkudoktoreos.core.coreabc import get_measurement
from akkudoktoreos.core.dataabc import DataSequence


@pytest.fixture
def file_measurements(config_eos, tmp_path, monkeypatch):
    measurement = get_measurement()
    config_eos.measurement.load_emr_keys = ["meter"]
    config_eos.general.data_folder_path = tmp_path
    config_eos.database.provider = None
    measurement._db_reset_state()
    monkeypatch.setattr(DataSequence, "save", AsyncMock(return_value=False))
    monkeypatch.setattr(DataSequence, "load", AsyncMock(return_value=False))
    try:
        yield measurement
    finally:
        measurement._db_reset_state()


@pytest.mark.asyncio
async def test_json_roundtrip_restores_records_into_existing_singleton(file_measurements):
    measurement = file_measurements
    await measurement.update_value("2026-09-16T08:00:00Z", "meter", 123.5)
    await measurement.update_value("2026-09-16T09:00:00Z", "meter", 124.0)
    assert await measurement.save()
    measurement._db_reset_state()

    assert await measurement.load()
    assert get_measurement() is measurement
    assert [record["meter"] for record in measurement.records] == [123.5, 124.0]
    assert [record.date_time.in_timezone("UTC").hour for record in measurement.records] == [8, 9]
    # Reloading must update matching timestamps, without duplicating them.
    assert await measurement.load()
    assert len(measurement.records) == 2


@pytest.mark.asyncio
async def test_json_restore_preserves_other_in_memory_timestamps(file_measurements):
    measurement = file_measurements
    await measurement.update_value("2026-09-16T08:00:00Z", "meter", 123.5)
    assert await measurement.save()
    measurement._db_reset_state()
    await measurement.update_value("2026-09-16T09:00:00Z", "meter", 124.0)
    assert await measurement.load()
    assert [record["meter"] for record in measurement.records] == [123.5, 124.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    "invalid json",
    json.dumps({"records": [
        {"date_time": "2026-09-16T08:00:00Z", "meter": 123.5},
        {"date_time": "not a date", "meter": 124.0},
    ]}),
])
async def test_invalid_json_does_not_partially_restore(file_measurements, payload):
    measurement = file_measurements
    measurement._measurement_file_path().write_text(payload, encoding="utf-8")
    assert await measurement.load() is False
    assert measurement.records == []


@pytest.mark.asyncio
async def test_database_success_does_not_read_json(file_measurements, monkeypatch):
    measurement = file_measurements
    measurement._measurement_file_path().write_text("invalid json", encoding="utf-8")
    monkeypatch.setattr(DataSequence, "load", AsyncMock(return_value=True))
    assert await measurement.load()


@pytest.mark.asyncio
async def test_missing_json_reports_not_loaded(file_measurements):
    assert await file_measurements.load() is False
