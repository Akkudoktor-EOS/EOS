import json
from unittest.mock import AsyncMock

import numpy as np
import pytest
import pytest_asyncio
from pendulum import datetime, duration
from pydantic import ValidationError

from akkudoktoreos.core.coreabc import get_measurement
from akkudoktoreos.core.dataabc import DataSequence
from akkudoktoreos.measurement.measurement import (
    MeasurementChannelSettings,
    MeasurementCommonSettings,
    MeasurementDataRecord,
)
from akkudoktoreos.utils.datetimeutil import (
    compare_datetimes,
    to_datetime,
)


class TestMeasurementDataRecord:
    """Measurement-specific keys, values, and serialization.

    Generic native-field access is covered by TestDataRecord in test_dataabcrecord.py.
    """

    @pytest.fixture
    def sample_config(self, config_eos):
        """Fixture to configure the measurement keys on the global config."""
        config_eos.measurement.load_emr_keys = ["dish_washer_mr", "temp"]
        config_eos.measurement.pv_production_emr_keys = ["solar_power"]
        return config_eos

    @pytest.fixture
    def record(self, sample_config):
        """Fixture to create a sample MeasurementDataRecord with some measurements set."""
        rec = MeasurementDataRecord(date_time=None)
        rec.configured_data = {"dish_washer_mr": 123.0, "solar_power": 456.0}
        return rec

    def test_record_keys_includes_measurement_keys(self, record):
        """Ensure record_keys includes all configured measurement keys."""
        assert set(record.record_keys()) >= set(record.config.measurement.keys)

    def test_record_keys_writable_includes_measurement_keys(self, record):
        """Ensure record_keys_writable includes all configured measurement keys."""
        assert set(record.record_keys_writable()) >= set(record.config.measurement.keys)

    def test_getitem_existing_field(self, record):
        """Test that __getitem__ returns correct value for existing native field."""
        date_time = "2024-01-01T00:00:00+00:00"
        record.date_time = date_time
        assert compare_datetimes(record["date_time"], to_datetime(date_time)).equal

    def test_getitem_existing_measurement(self, record):
        """Test that __getitem__ retrieves existing measurement values."""
        assert record["dish_washer_mr"] == 123.0
        assert record["solar_power"] == 456.0

    def test_getitem_missing_measurement_returns_none(self, record):
        """Test that __getitem__ returns None for missing but known measurement keys."""
        assert record["temp"] is None

    def test_getitem_raises_keyerror(self, record):
        """Test that __getitem__ raises KeyError for completely unknown keys."""
        with pytest.raises(KeyError):
            _ = record["nonexistent"]

    def test_setitem_measurement(self, record):
        """Test setting a known measurement key using __setitem__."""
        record["temp"] = 25.5
        assert record["temp"] == 25.5

    def test_setitem_invalid_key_raises(self, record):
        """Test that __setitem__ raises KeyError for unknown keys."""
        with pytest.raises(KeyError):
            record["unknown_key"] = 123

    def test_delitem_measurement(self, record):
        """Test deleting a known measurement key using __delitem__."""
        del record["solar_power"]
        assert record["solar_power"] is None

    def test_delitem_unknown_raises(self, record):
        """Test that __delitem__ raises KeyError for unknown keys."""
        with pytest.raises(KeyError):
            del record["nonexistent"]

    def test_attribute_get_existing_measurement(self, record):
        """Test accessing an existing measurement via attribute."""
        assert record.dish_washer_mr == 123.0

    def test_attribute_get_missing_measurement(self, record):
        """Test accessing a missing but known measurement returns None."""
        assert record.temp is None

    def test_attribute_get_invalid_raises(self, record):
        """Test accessing an unknown attribute raises AttributeError."""
        with pytest.raises(AttributeError):
            _ = record.nonexistent

    def test_attribute_set_existing_measurement(self, record):
        """Test setting a known measurement key via attribute."""
        record.temp = 99.9
        assert record["temp"] == 99.9

    def test_attribute_set_invalid_raises(self, record):
        """Test setting an unknown attribute raises AttributeError."""
        with pytest.raises(AttributeError):
            record.invalid = 123

    def test_delattr_measurement(self, record):
        """Test deleting a known measurement key via attribute."""
        record.temp = 88.0
        del record.temp
        assert record.temp is None

    def test_delattr_ignored_missing_measurement_key(self, record):
        """Test deleting a known measurement key that was never set is a no-op."""
        del record.temp
        assert record.temp is None

    def test_len_and_iter(self, record):
        """Test that __len__ and __iter__ behave as expected."""
        keys = list(iter(record))
        assert set(record.record_keys_writable()) == set(keys)
        assert len(record) == len(keys)

    def test_hasattr_behavior(self, record):
        """Test that hasattr returns True for fields and known measurements."""
        assert hasattr(record, "date_time")
        assert hasattr(record, "dish_washer_mr")
        assert hasattr(record, "temp")  # allowed, even if not yet set
        assert not hasattr(record, "nonexistent")

    def test_model_validate_roundtrip(self, record):
        """Test that MeasurementDataRecord can be serialized and revalidated."""
        dumped = record.model_dump()
        restored = MeasurementDataRecord.model_validate(dumped)
        assert restored.dish_washer_mr == 123.0
        assert restored.solar_power == 456.0
        assert restored.temp is None  # not set

    def test_copy_preserves_measurements(self, record):
        """Test that copying preserves measurement values."""
        record.temp = 22.2
        copied = record.model_copy()
        assert copied.dish_washer_mr == 123.0
        assert copied.temp == 22.2
        assert copied is not record

    def test_inequality_differs_with_measurements(self, record):
        """Test that records with different measurements are not equal."""
        other = record.model_copy(deep=True)
        # Modify one measurement value in the copy
        other["dish_washer_mr"] = 999.9
        assert record != other

    def test_in_operator_for_measurements_and_fields(self, record):
        """Ensure 'in' works for both fields and configured measurement keys."""
        assert "dish_washer_mr" in record
        assert "solar_power" in record
        assert "date_time" in record  # standard field
        assert "temp" in record       # allowed but not yet set
        assert "unknown" not in record

    def test_dir_includes_measurement_keys(self, record):
        """`dir(record)` should include measurement keys for introspection.
         It shall not include the internal 'measurements' attribute.
        """
        keys = dir(record)
        assert "measurements" not in keys
        for key in record.config.measurement.keys:
            assert key in keys


@pytest.mark.asyncio
class TestMeasurement:
    """Test suite for the Measuremen class."""

    @pytest_asyncio.fixture
    async def measurement_eos(self, config_eos):
        """Fixture to create a Measurement instance."""
        # Load meter readings are in kWh
        config_eos.measurement.load_emr_keys = ["load0_mr", "load1_mr", "load2_mr", "load3_mr"]
        measurement = get_measurement()
        await measurement.delete_by_datetime(None, None)
        record0 = MeasurementDataRecord(
            date_time=datetime(2023, 1, 1, hour=0),
            load0_mr=100,
            load1_mr=200,
        )
        assert record0.load0_mr == 100
        assert record0.load1_mr == 200
        records = [
            MeasurementDataRecord(
                date_time=to_datetime("2023-01-01T00:00:00"),
                load0_mr=100,
                load1_mr=200,
            ),
            MeasurementDataRecord(
                date_time=to_datetime("2023-01-01T01:00:00"),
                load0_mr=150,
                load1_mr=250,
            ),
            MeasurementDataRecord(
                date_time=to_datetime("2023-01-01T02:00:00"),
                load0_mr=200,
                load1_mr=300,
            ),
            MeasurementDataRecord(
                date_time=to_datetime("2023-01-01T03:00:00"),
                load0_mr=250,
                load1_mr=350,
            ),
            MeasurementDataRecord(
                date_time=to_datetime("2023-01-01T04:00:00"),
                load0_mr=300,
                load1_mr=400,
            ),
            MeasurementDataRecord(
                date_time=to_datetime("2023-01-01T05:00:00"),
                load0_mr=350,
                load1_mr=450,
            ),
        ]
        for record in records:
            await measurement.insert_by_datetime(record)
        return measurement

    async def test_interval_count(self, measurement_eos):
        """Test interval count calculation."""
        start = to_datetime("2023-01-01T00:00:00")
        end = to_datetime("2023-01-01T03:00:00")
        interval = duration(hours=1)

        assert measurement_eos._interval_count(start, end, interval) == 3

    async def test_interval_count_invalid_end_before_start(self, measurement_eos):
        """Test interval count raises ValueError when end_datetime is before start_datetime."""
        start = to_datetime("2023-01-01T03:00:00")
        end = to_datetime("2023-01-01T00:00:00")
        interval = duration(hours=1)

        with pytest.raises(ValueError, match="end_datetime must be after start_datetime"):
            measurement_eos._interval_count(start, end, interval)

    async def test_interval_count_invalid_non_positive_interval(self, measurement_eos):
        """Test interval count raises ValueError when interval is non-positive."""
        start = to_datetime("2023-01-01T00:00:00")
        end = to_datetime("2023-01-01T03:00:00")

        with pytest.raises(ValueError, match="interval must be positive"):
            measurement_eos._interval_count(start, end, duration(hours=0))

    async def test_energy_from_meter_readings_valid_input(self, measurement_eos):
        """Test _energy_from_meter_readings with valid inputs and proper alignment of load data."""
        key = "load0_mr"
        start_datetime = to_datetime("2023-01-01T00:00:00")
        end_datetime = to_datetime("2023-01-01T05:00:00")
        interval = duration(hours=1)

        load_array = await measurement_eos._energy_from_meter_readings(
            key, start_datetime, end_datetime, interval
        )

        expected_load_array = np.array([50, 50, 50, 50, 50])  # Differences between consecutive readings
        np.testing.assert_array_equal(load_array, expected_load_array)

    async def test_energy_from_meter_readings_empty_array(self, measurement_eos):
        """Test _energy_from_meter_readings with no data (empty array)."""
        key = "load0_mr"
        start_datetime = to_datetime("2023-01-01T00:00:00")
        end_datetime = to_datetime("2023-01-01T05:00:00")
        interval = duration(hours=1)

        # Use empyt records array
        await measurement_eos.delete_by_datetime(start_datetime, end_datetime)

        load_array = await measurement_eos._energy_from_meter_readings(
            key, start_datetime, end_datetime, interval
        )

        # Expected: an array of zeros with one less than the number of intervals
        expected_size = (
            measurement_eos._interval_count(start_datetime, end_datetime + interval, interval) - 1
        )
        expected_load_array = np.zeros(expected_size)
        np.testing.assert_array_equal(load_array, expected_load_array)

    async def test_energy_from_meter_readings_misaligned_array(self, measurement_eos):
        """Test _energy_from_meter_readings with misaligned array size."""
        key = "load1_mr"
        interval = duration(hours=1)
        start_datetime = to_datetime("2023-01-01T00:00:00")
        end_datetime = to_datetime("2023-01-01T05:00:00")

        # Use misaligned array, latest interval set to 2 hours (instead of 1 hour)
        latest_record_datetime = to_datetime("2023-01-01T05:00:00")
        new_record_datetime = to_datetime("2023-01-01T06:00:00")
        record = await measurement_eos.get_by_datetime(latest_record_datetime)
        assert record is not None
        await measurement_eos.delete_by_datetime(start_datetime = latest_record_datetime,
                                                 end_datetime = new_record_datetime)
        record.date_time = new_record_datetime
        await measurement_eos.insert_by_datetime(record)

        # Check test setup
        dates, values = await measurement_eos.key_to_lists(key, start_datetime, None)
        assert dates == [
            to_datetime("2023-01-01T00:00:00"),
            to_datetime("2023-01-01T01:00:00"),
            to_datetime("2023-01-01T02:00:00"),
            to_datetime("2023-01-01T03:00:00"),
            to_datetime("2023-01-01T04:00:00"),
            to_datetime("2023-01-01T06:00:00"),
        ]
        assert values == [200, 250, 300, 350, 400, 450]
        array = await measurement_eos.key_to_array(key, start_datetime, end_datetime + interval, interval=interval)
        np.testing.assert_array_equal(array, [200, 250, 300, 350, 400, 425])

        load_array = await measurement_eos._energy_from_meter_readings(
            key, start_datetime, end_datetime, interval
        )

        expected_load_array = np.array([50., 50., 50., 50., 25.])  # Differences between consecutive readings
        np.testing.assert_array_equal(load_array, expected_load_array)

    async def test_energy_from_meter_readings_partial_data(self, measurement_eos, caplog):
        """Test _energy_from_meter_readings with partial data (misaligned but empty array)."""
        key = "load2_mr"
        start_datetime = to_datetime("2023-01-01T00:00:00")
        end_datetime = to_datetime("2023-01-01T05:00:00")
        interval = duration(hours=1)

        with caplog.at_level("DEBUG"):
            load_array = await measurement_eos._energy_from_meter_readings(
                key, start_datetime, end_datetime, interval
            )

        expected_size = (
            measurement_eos._interval_count(start_datetime, end_datetime + interval, interval) - 1
        )
        expected_load_array = np.zeros(expected_size)
        np.testing.assert_array_equal(load_array, expected_load_array)

    async def test_energy_from_meter_readings_negative_interval(self, measurement_eos):
        """Test _energy_from_meter_readings with a negative interval."""
        key = "load3_mr"
        start_datetime = to_datetime("2023-01-01T00:00:00")
        end_datetime = to_datetime("2023-01-01T05:00:00")
        interval = duration(hours=-1)

        with pytest.raises(ValueError, match="interval must be positive"):
            await measurement_eos._energy_from_meter_readings(key, start_datetime, end_datetime, interval)

    async def test_load_total_kwh(self, measurement_eos):
        """Test total load calculation."""
        start_datetime = to_datetime("2023-01-01T03:00:00")
        end_datetime = to_datetime("2023-01-01T05:00:00")
        interval = duration(hours=1)

        result = await measurement_eos.load_total_kwh(start_datetime=start_datetime, end_datetime=end_datetime, interval=interval)

        # Expected total load per interval
        expected = np.array([100, 100])  # Differences between consecutive meter readings
        np.testing.assert_array_equal(result, expected)

    async def test_load_total_kwh_no_data(self, measurement_eos):
        """Test total load calculation with no data."""
        measurement_eos.records = []
        start_datetime = to_datetime("2023-01-01T00:00:00")
        end_datetime = to_datetime("2023-01-01T03:00:00")
        interval = duration(hours=1)

        result = await measurement_eos.load_total_kwh(start_datetime=start_datetime, end_datetime=end_datetime, interval=interval)
        expected = np.zeros(3)  # No data, so all intervals are zero
        np.testing.assert_array_equal(result, expected)

    async def test_load_total_kwh_partial_intervals(self, measurement_eos):
        """Test total load calculation with partial intervals."""
        start_datetime = to_datetime("2023-01-01T00:30:00") # Start in the middle of an interval
        end_datetime = to_datetime("2023-01-01T01:30:00") # End in the middle of another interval
        interval = duration(hours=1)

        result = await measurement_eos.load_total_kwh(start_datetime=start_datetime, end_datetime=end_datetime, interval=interval)
        expected = np.array([100])  # Only one complete interval covered
        np.testing.assert_array_equal(result, expected)


class TestMeasurementFileRestore:
    @pytest.fixture
    def file_measurements(self, config_eos, tmp_path, monkeypatch):
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
    async def test_json_roundtrip_restores_records_into_existing_singleton(self, file_measurements):
        measurement = file_measurements
        await measurement.update_value(to_datetime("2026-09-16T08:00:00Z"), "meter", 123.5)
        await measurement.update_value("2026-09-16T09:00:00Z", "meter", 124.0)
        assert await measurement.save()
        measurement._db_reset_state()

        assert await measurement.load()
        assert get_measurement() is measurement
        assert [record["meter"] for record in measurement.records] == [123.5, 124.0]
        restored_hours = []
        for record in measurement.records:
            assert record.date_time is not None
            restored_hours.append(record.date_time.in_timezone("UTC").hour)
        assert restored_hours == [8, 9]
        # Reloading must update matching timestamps, without duplicating them.
        assert await measurement.load()
        assert len(measurement.records) == 2

    @pytest.mark.asyncio
    async def test_json_restore_preserves_other_in_memory_timestamps(self, file_measurements):
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
    async def test_invalid_json_does_not_partially_restore(self, file_measurements, payload):
        measurement = file_measurements
        measurement._measurement_file_path().write_text(payload, encoding="utf-8")
        assert await measurement.load() is False
        assert measurement.records == []

    @pytest.mark.asyncio
    async def test_database_success_does_not_read_json(self, file_measurements, monkeypatch):
        measurement = file_measurements
        measurement._measurement_file_path().write_text("invalid json", encoding="utf-8")
        monkeypatch.setattr(DataSequence, "load", AsyncMock(return_value=True))
        assert await measurement.load()

    @pytest.mark.asyncio
    async def test_missing_json_reports_not_loaded(self, file_measurements):
        assert await file_measurements.load() is False


CHANNELS = {
    "house_power": dict(quantity="power", unit="W", integration_method="hold", max_gap_seconds=120),
    "house_meter": dict(quantity="cumulative_energy", unit="kWh"),
    "house_interval": dict(
        quantity="interval_energy", unit="Wh", interval_seconds=900, timestamp_reference="start"
    ),
}


class TestMeasurementChannels:
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
    def test_reject_ambiguous_channel(self, definition):
        with pytest.raises(ValidationError):
            MeasurementChannelSettings(**definition)

    @pytest.mark.parametrize(
        "legacy_field",
        ["load_emr_keys", "grid_import_emr_keys", "grid_export_emr_keys", "pv_production_emr_keys"],
    )
    def test_legacy_keys_keep_meter_semantics(self, legacy_field):
        settings = MeasurementCommonSettings.model_validate(dict(**{legacy_field: ["legacy"]}, channels=CHANNELS))
        assert settings.keys == sorted(["legacy", *CHANNELS])
        compatible = MeasurementCommonSettings.model_validate(dict(
            **{legacy_field: ["legacy"]}, channels={"legacy": CHANNELS["house_meter"]}
        ))
        assert compatible.keys == ["legacy"]
        with pytest.raises(ValidationError, match="must remain"):
            MeasurementCommonSettings.model_validate(dict(
                **{legacy_field: ["legacy"]}, channels={"legacy": CHANNELS["house_power"]}
            ))

    @pytest.mark.parametrize("key", ["", " x", "date_time", "configured_data", "keys", "_private"])
    def test_reject_reserved_key(self, key):
        with pytest.raises(ValidationError):
            MeasurementCommonSettings.model_validate(dict(channels={key: CHANNELS["house_meter"]}))

    @pytest.mark.asyncio
    async def test_existing_import_and_file_reload(self, config_eos, tmp_path, monkeypatch):
        """All three quantities retain their raw values through the existing file path."""
        measurement = get_measurement()
        previous_settings = config_eos.measurement
        previous_records = measurement.records
        previous_folder = config_eos.general.data_folder_path
        try:
            config_eos.measurement = MeasurementCommonSettings.model_validate(dict(channels=CHANNELS))
            config_eos.general.data_folder_path = tmp_path
            measurement._db_reset_state()
            values = dict(house_power=800.0, house_meter=12345.6, house_interval=200.0)
            for key, value in values.items():
                (await measurement.update_value(to_datetime("2026-09-10T18:00:00Z"), key, value))
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
