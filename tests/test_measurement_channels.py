from unittest.mock import AsyncMock
"""Contracts for typed channels sharing the existing measurement storage."""

# ruff: noqa: S101

import pytest
from pydantic import ValidationError

from akkudoktoreos.core.coreabc import get_measurement
from akkudoktoreos.measurement.measurement import (
    MeasurementChannelSettings,
    MeasurementCommonSettings,
    MeasurementDataRecord,
)


CHANNELS = {
    "house_power": dict(quantity="power", unit="W", integration_method="hold", max_gap_seconds=120),
    "house_meter": dict(quantity="cumulative_energy", unit="kWh"),
    "house_interval": dict(
        quantity="interval_energy", unit="Wh", interval_seconds=900, timestamp_reference="start"
    ),
}


@pytest.mark.parametrize(
    "definition",
    [
        dict(quantity="power", unit="kWh", integration_method="hold", max_gap_seconds=60),
        dict(quantity="power", unit="W"),
        dict(quantity="power", unit="W", integration_method="hold", max_gap_seconds=0),
        dict(quantity="cumulative_energy", unit="W"),
        dict(quantity="cumulative_energy", unit="kWh", timestamp_reference="end"),
        dict(quantity="interval_energy", unit="Wh", interval_seconds=900),
        dict(
            quantity="interval_energy", unit="Wh", interval_seconds=True, timestamp_reference="end"
        ),
    ],
)
def test_reject_ambiguous_channel(definition):
    with pytest.raises(ValidationError):
        MeasurementChannelSettings(**definition)


@pytest.mark.parametrize(
    "legacy_field",
    ["load_emr_keys", "grid_import_emr_keys", "grid_export_emr_keys", "pv_production_emr_keys"],
)
def test_legacy_keys_keep_meter_semantics(legacy_field):
    settings = MeasurementCommonSettings(**{legacy_field: ["legacy"]}, channels=CHANNELS)
    assert settings.keys == sorted(["legacy", *CHANNELS])
    compatible = MeasurementCommonSettings(
        **{legacy_field: ["legacy"]}, channels={"legacy": CHANNELS["house_meter"]}
    )
    assert compatible.keys == ["legacy"]
    with pytest.raises(ValidationError, match="must remain"):
        MeasurementCommonSettings(
            **{legacy_field: ["legacy"]}, channels={"legacy": CHANNELS["house_power"]}
        )


@pytest.mark.parametrize("key", ["", " x", "date_time", "configured_data", "keys", "_private"])
def test_reject_reserved_key(key):
    with pytest.raises(ValidationError):
        MeasurementCommonSettings(channels={key: CHANNELS["house_meter"]})


@pytest.mark.asyncio
async def test_existing_import_and_file_reload(config_eos, tmp_path, monkeypatch):
    """All three quantities retain their raw values through the existing file path."""
    from akkudoktoreos.core.dataabc import DataSequence

    measurement = get_measurement()
    previous_settings = config_eos.measurement
    previous_records = measurement.records
    previous_folder = config_eos.general.data_folder_path
    try:
        config_eos.measurement = MeasurementCommonSettings(channels=CHANNELS)
        config_eos.general.data_folder_path = tmp_path
        measurement._db_reset_state()
        values = dict(house_power=800.0, house_meter=12345.6, house_interval=200.0)
        for key, value in values.items():
            (await measurement.update_value("2026-09-10T18:00:00Z", key, value))
        assert set(values).issubset(measurement.record_keys)
        monkeypatch.setattr(DataSequence, "save", AsyncMock(return_value=False))
        monkeypatch.setattr(DataSequence, "load", AsyncMock(return_value=False))
        assert (await measurement.save())
        measurement._db_reset_state()
        assert (await measurement.load())
        assert len(measurement.records) == 1
        for key, value in values.items():
            assert measurement.records[0][key] == value
        restored = MeasurementDataRecord.model_validate_json(
            measurement.records[0].model_dump_json()
        )
        assert restored.configured_data == values
        settings = MeasurementCommonSettings.model_validate_json(
            config_eos.measurement.model_dump_json()
        )
        assert settings.model_dump() == config_eos.measurement.model_dump()
    finally:
        measurement._db_reset_state()
        measurement.records = previous_records
        config_eos.measurement = previous_settings
        config_eos.general.data_folder_path = previous_folder
