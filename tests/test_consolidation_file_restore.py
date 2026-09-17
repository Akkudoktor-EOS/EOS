"""Verify fallback JSON loading does not lose records through the singleton."""

from unittest.mock import AsyncMock

import pytest

from akkudoktoreos.core.coreabc import get_measurement
from akkudoktoreos.core.dataabc import DataSequence
from akkudoktoreos.utils.datetimeutil import to_datetime


@pytest.mark.asyncio
async def test_measurement_json_roundtrip(config_eos, tmp_path, monkeypatch):
    m = get_measurement()
    config_eos.measurement.load_emr_keys = ["meter"]
    config_eos.general.data_folder_path = tmp_path
    config_eos.database.provider = None
    m._db_reset_state()
    try:
        await m.update_value(to_datetime("2026-09-16T08:00:00Z"), "meter", 123.5)
        monkeypatch.setattr(DataSequence, "save", AsyncMock(return_value=False))
        monkeypatch.setattr(DataSequence, "load", AsyncMock(return_value=False))
        assert await m.save()
        m._db_reset_state()
        assert await m.load()
        assert len(m.records) == 1
        assert m.records[0]["meter"] == 123.5
    finally:
        m._db_reset_state()
