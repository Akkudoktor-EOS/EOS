"""Pytest tests for database persistence module.

Tests the abstract Database interface and concrete implementations (LMDB, SQLite).
Also tests the database integration with DataSequence/DataProvider classes via
DatabaseRecordProtocolMixin.

Design constraints honoured by these tests:
- DatabaseRecordProtocolMixin subclasses are singletons; tests reset state via
  _db_reset_state() helpers rather than re-instantiating.
- db_save_records() has no clear_memory or start/end parameters; memory management
  is separate from persistence.
- db_delete_records() has no clear_memory parameter.
- _db_ensure_loaded() is private; public callers use db_iterate_records() or
  db_load_records() which trigger loading internally.
- db_count_records() correctly combines storage_count + new_count - pending_deletes.
- db_vacuum() end_timestamp is already exclusive; no +1ms offset applied.
- db_save_records() returns saved_count + deleted_count.
"""

import pickle
import shutil
import tempfile
import time
from pathlib import Path
from typing import Iterator, List, Optional, Type

import pytest
from pydantic import Field

from akkudoktoreos.core.coreabc import get_database
from akkudoktoreos.core.dataabc import (
    DataProvider,
    DataRecord,
    DataSequence,
)
from akkudoktoreos.core.database import (
    Database,
    LMDBDatabase,
    SQLiteDatabase,
)
from akkudoktoreos.core.databaseabc import (
    DatabaseRecordProtocolLoadPhase,
    DatabaseTimestamp,
)
from akkudoktoreos.utils.datetimeutil import (
    DateTime,
    Duration,
    to_datetime,
    to_duration,
)

# ==================== Helpers ====================

def _clear_sequence_state(sequence) -> None:
    """Clear runtime DB state without re-instantiating the singleton.

    Does _NOT_ initialize the DB state.
    """
    sequence.db_delete_records()
    try:
        sequence._db_metadata = None
        sequence.database().set_metadata(None, namespace=sequence.db_namespace())
    except Exception:
        # Database may not be available, just skip
        pass
    try:
        del sequence._db_initialized
    except Exception:
        # May not be set
        pass

def _reset_sequence_state(sequence) -> None:
    """Reset runtime DB state without re-instantiating the singleton."""
    try:
        sequence.records = []
        del sequence._db_initialized
    except Exception:
        # May not be set
        pass
    sequence._db_ensure_initialized()


# ==================== Test Fixtures ====================

@pytest.fixture
def temp_dir():
    """Create a temporary directory for test databases."""
    temp_path = Path(tempfile.mkdtemp())
    yield temp_path
    shutil.rmtree(temp_path, ignore_errors=True)


@pytest.fixture(params=["LMDB", "SQLite"])
def database_provider(request) -> str:
    """Parametrize all database backend tests."""
    return request.param


@pytest.fixture
def database_instance(config_eos, database_provider: str) -> Iterator[Database]:
    """Open a database instance for testing and close it afterwards.

    Note: Database is a singleton — we configure and use it, then restore
    the provider to None so subsequent tests start clean.
    """
    config_eos.database.compression_level = 6
    config_eos.database.provider = database_provider
    db = get_database()

    assert db.is_open is True
    assert db.provider_id() == database_provider

    yield db

    # Teardown: close and reset provider so next fixture gets a fresh state
    db.close()
    config_eos.database.provider = None


# ==================== Test Data Models ====================

class SampleDataRecord(DataRecord):
    """Minimal DataRecord for testing."""
    temperature: float = Field(default=0.0)
    humidity: float = Field(default=0.0)
    pressure: float = Field(default=0.0)


class SampleDataSequence(DataSequence):
    """DataSequence subclass with database support."""
    records: List[SampleDataRecord] = Field(default_factory=list)

    @classmethod
    def record_class(cls) -> Type[SampleDataRecord]:
        return SampleDataRecord

    def db_namespace(self) -> str:
        return "SampleDataSequence"


class SampleDataProvider(DataProvider):
    """DataProvider subclass with database support."""
    records: List[SampleDataRecord] = Field(default_factory=list)

    @classmethod
    def record_class(cls) -> Type[SampleDataRecord]:
        return SampleDataRecord

    def provider_id(self) -> str:
        return "SampleDataProvider"

    def enabled(self) -> bool:
        return True

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        pass

    def db_namespace(self) -> str:
        return "SampleDataProvider"


# ==================== Database Backend Tests ====================

class TestDatabase:
    """Tests for the raw Database interface (both backends)."""

    def test_database_creation(self, config_eos, database_provider):
        config_eos.database.compression_level = 6
        config_eos.database.provider = database_provider
        db = get_database()

        assert db.is_open is True
        assert db.compression is True
        assert db.compression_level == 6
        # storage_path uses the concrete backend class name
        assert db.storage_path == (
            config_eos.general.data_folder_path / "db" / db._db.__class__.__name__.lower()
        )

    def test_database_open_close(self, database_instance):
        assert database_instance.is_open is True
        assert database_instance._db.connection is not None

        database_instance.close()
        assert database_instance._db.is_open is False

    def test_save_and_load_single_record(self, database_instance):
        key = b"2024-01-01T00:00:00+00:00"
        value = b"test_data_12345"

        database_instance.save_records([(key, value)])
        records = list(database_instance.iterate_records(key, key + b"\xff"))

        assert len(records) == 1
        assert records[0] == (key, value)

    def test_save_multiple_records(self, database_instance):
        records = [
            (b"2024-01-01T00:00:00+00:00", b"data1"),
            (b"2024-01-02T00:00:00+00:00", b"data2"),
            (b"2024-01-03T00:00:00+00:00", b"data3"),
        ]
        saved = database_instance.save_records(records)
        assert saved == len(records)

        loaded = list(database_instance.iterate_records())
        assert len(loaded) == len(records)
        for expected, actual in zip(records, loaded):
            assert expected == actual

    def test_load_records_with_range(self, database_instance):
        records = [
            (b"2024-01-01T00:00:00+00:00", b"data1"),
            (b"2024-01-02T00:00:00+00:00", b"data2"),
            (b"2024-01-03T00:00:00+00:00", b"data3"),
            (b"2024-01-04T00:00:00+00:00", b"data4"),
            (b"2024-01-05T00:00:00+00:00", b"data5"),
        ]
        database_instance.save_records(records)

        # Range is half-open: [2024-01-02, 2024-01-04)
        start_key = b"2024-01-02T00:00:00+00:00"
        end_key = b"2024-01-04T00:00:00+00:00"
        loaded = list(database_instance.iterate_records(start_key, end_key))

        assert len(loaded) == 2
        assert loaded[0][0] == b"2024-01-02T00:00:00+00:00"
        assert loaded[1][0] == b"2024-01-03T00:00:00+00:00"

    def test_delete_record(self, database_instance):
        key = b"2024-01-01T00:00:00+00:00"
        database_instance.save_records([(key, b"test_data")])
        assert database_instance.count_records() == 1

        deleted = database_instance.delete_records([key])
        assert deleted == 1
        assert database_instance.count_records() == 0

        # Deleting a non-existent key returns 0
        deleted = database_instance.delete_records([key])
        assert deleted == 0

    def test_count_records(self, database_instance):
        assert database_instance.count_records() == 0

        for i in range(10):
            key = f"2024-01-{i + 1:02d}T00:00:00+00:00".encode()
            database_instance.save_records([(key, b"data")])

        assert database_instance.count_records() == 10

    def test_get_key_range_empty(self, database_instance):
        min_key, max_key = database_instance.get_key_range()
        assert min_key is None
        assert max_key is None

    def test_get_key_range_with_records(self, database_instance):
        keys = [
            b"2024-01-01T00:00:00+00:00",
            b"2024-01-05T00:00:00+00:00",
            b"2024-01-03T00:00:00+00:00",
        ]
        for key in keys:
            database_instance.save_records([(key, b"data")])

        min_key, max_key = database_instance.get_key_range()
        assert min_key == b"2024-01-01T00:00:00+00:00"
        assert max_key == b"2024-01-05T00:00:00+00:00"

    def test_iterate_records_forward(self, database_instance):
        keys = [
            b"2024-01-01T00:00:00+00:00",
            b"2024-01-02T00:00:00+00:00",
            b"2024-01-03T00:00:00+00:00",
        ]
        for key in keys:
            database_instance.save_records([(key, b"data")])

        result_keys = [k for k, _ in database_instance.iterate_records()]
        assert result_keys == keys

    def test_iterate_records_reverse(self, database_instance):
        keys = [
            b"2024-01-01T00:00:00+00:00",
            b"2024-01-02T00:00:00+00:00",
            b"2024-01-03T00:00:00+00:00",
        ]
        for key in keys:
            database_instance.save_records([(key, b"data")])

        result_keys = [k for k, _ in database_instance.iterate_records(reverse=True)]
        assert result_keys == list(reversed(keys))

    def test_compression_reduces_size(self, config_eos, database_provider):
        large_data = b"A" * 10_000

        config_eos.database.provider = database_provider
        config_eos.database.compression_level = 9
        compressed = get_database().serialize_data(large_data)
        assert get_database().deserialize_data(compressed) == large_data

        config_eos.database.compression_level = 0
        uncompressed = get_database().serialize_data(large_data)
        assert get_database().deserialize_data(uncompressed) == large_data

        assert len(compressed) < len(uncompressed)

    def test_flush(self, database_instance):
        key = b"2024-01-01T00:00:00+00:00"
        database_instance.save_records([(key, b"test_data")])
        database_instance.flush()

        loaded = list(database_instance.iterate_records())
        assert len(loaded) == 1
        assert loaded[0] == (key, b"test_data")

    def test_backend_stats(self, database_instance):
        stats = database_instance.get_backend_stats()
        assert isinstance(stats, dict)
        assert "backend" in stats

        for i in range(10):
            key = f"2024-01-{i + 1:02d}T00:00:00+00:00".encode()
            database_instance.save_records([(key, b"data" * 100)])

        stats = database_instance.get_backend_stats()
        assert stats is not None

    def test_metadata_excluded_from_count(self, database_instance):
        """Metadata record stored under DATABASE_METADATA_KEY must not appear in count."""
        # Save a normal record
        database_instance.save_records([(b"2024-01-01T00:00:00+00:00", b"data")])
        count = database_instance.count_records()
        assert count == 1  # metadata excluded by backend implementation


# ==================== DatabaseRecordProtocolMixin Tests ====================

class TestDataSequenceDatabaseProtocol:
    """Tests for DatabaseRecordProtocolMixin via SampleDataSequence."""

    def test_db_enabled_when_db_open(self, database_instance):
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        assert sequence.db_enabled is True

    def test_db_disabled_when_db_closed(self, config_eos):
        config_eos.database.provider = None
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        assert sequence.db_enabled is False

    def test_insert_and_save_records(self, database_instance):
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(10):
            sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=20.0 + i)
            )

        # All 10 are dirty/new, none persisted yet
        assert len(sequence.records) == 10
        assert len(sequence._db_new_timestamps) == 10

        saved = sequence.db_save_records()
        assert saved == 10  # 10 inserts + 0 deletes
        assert len(sequence._db_dirty_timestamps) == 0
        assert len(sequence._db_new_timestamps) == 0

    def test_save_returns_insert_plus_delete_count(self, database_instance):
        """db_save_records() return value = saved_inserts + deleted_count."""
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(5):
            sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )
        # Persist the 5 records
        sequence.db_save_records()

        # Delete 2 of them
        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=2))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=4))
        deleted = sequence.db_delete_records(start_timestamp=db_start, end_timestamp=db_end)
        # Insert 3 new ones
        for i in range(10, 13):
            sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )

        result = sequence.db_save_records()
        # 3 inserts + 2 deletes = 5
        assert result == 5

    def test_load_records_from_db(self, database_instance):
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(10):
            sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=20.0 + i)
            )
        sequence.db_save_records()

        # Clear memory, then reload from DB
        _reset_sequence_state(sequence)
        loaded = sequence.db_load_records()

        assert loaded == 10
        assert len(sequence.records) == 10
        for i, record in enumerate(sequence.records):
            assert record.temperature == 20.0 + i

    def test_load_records_with_range(self, database_instance):
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(10):
            sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=20.0 + i)
            )
        sequence.db_save_records()
        _reset_sequence_state(sequence)

        # Load [hours=3, hours=7) → 4 records (3, 4, 5, 6)
        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=3))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=7))
        loaded = sequence.db_load_records(start_timestamp=db_start, end_timestamp=db_end)
        assert loaded == 4
        assert sequence.records[0].temperature == 23.0
        assert sequence.records[-1].temperature == 26.0

    def test_iterate_records_triggers_lazy_load(self, database_instance):
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(10):
            sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=20.0 + i)
            )
        sequence.db_save_records()
        _reset_sequence_state(sequence)

        # db_iterate_records calls _db_ensure_loaded internally
        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=2))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=5))
        records = list(sequence.db_iterate_records(start_timestamp=db_start, end_timestamp=db_end))
        assert len(records) == 3
        assert all(base_time.add(hours=2) <= r.date_time < base_time.add(hours=5) for r in records)

    def test_delete_records(self, database_instance):
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(6):
            sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=20.0)
            )
        sequence.db_save_records()

        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=2))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=5))
        deleted = sequence.db_delete_records(start_timestamp=db_start, end_timestamp=db_end)
        assert deleted == 3

        # Persist the deletions
        sequence.db_save_records()

        _reset_sequence_state(sequence)
        sequence.db_load_records()
        assert len(sequence.records) == 3

    def test_delete_tombstone_prevents_resurrection(self, database_instance):
        """Deleted records must not re-appear when db_load_records is called."""
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(3):
            sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )
        sequence.db_save_records()

        # Delete middle record
        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=1))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=2))
        deleted = sequence.db_delete_records(start_timestamp=db_start, end_timestamp=db_end)
        assert deleted == 1

        # Do NOT persist yet — tombstone lives only in memory
        # Loading should not resurrect the tombstoned record
        loaded = sequence.db_load_records()
        assert all(r.date_time != base_time.add(hours=1) for r in sequence.records)

    def test_insert_after_delete_clears_tombstone(self, database_instance):
        """Re-inserting a deleted datetime must clear its tombstone."""
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")
        dt = base_time.add(hours=5)

        sequence.db_insert_record(SampleDataRecord(date_time=dt, temperature=10.0))
        sequence.db_save_records()

        db_start = DatabaseTimestamp.from_datetime(dt)
        db_end = sequence._db_timestamp_after(db_start)
        deleted = sequence.db_delete_records(start_timestamp=db_start, end_timestamp=db_end)
        assert deleted == 1

        sequence.db_save_records()

        # Re-insert the same datetime
        sequence.db_insert_record(SampleDataRecord(date_time=dt, temperature=99.0))
        assert dt not in sequence._db_deleted_timestamps
        sequence.db_save_records()

        _reset_sequence_state(sequence)
        sequence.db_load_records()
        assert any(r.date_time == dt and r.temperature == 99.0 for r in sequence.records)

    def test_db_count_records_memory_only(self):
        """When db is disabled, count reflects memory only."""
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)

        # Without a live DB, db_enabled is False
        if sequence.db_enabled:
            pytest.skip("DB is open; this test requires it to be closed")

        base_time = to_datetime("2024-01-01T00:00:00Z")
        for i in range(5):
            sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i)),
                mark_dirty=False,
            )
        assert sequence.db_count_records() == 5

    def test_db_count_records_combined(self, database_instance):
        """db_count_records = storage + new_unpersisted - pending_deletes."""
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        # Persist 10 records
        for i in range(10):
            sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )
        sequence.db_save_records()

        # Add 3 new unpersisted records
        for i in range(10, 13):
            sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )

        # Delete 2 persisted records (not yet saved)
        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=0))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=2))
        deleted = sequence.db_delete_records(start_timestamp=db_start, end_timestamp=db_end)
        assert deleted == 2

        # storage=10, new=3, pending_deletes=2 → expected=11
        assert sequence.db_count_records() == 11

    def test_db_timestamp_range_empty(self, database_instance):
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        min_dt, max_dt = sequence.db_timestamp_range()
        assert min_dt is None
        assert max_dt is None

    def test_db_timestamp_range_with_records(self, database_instance):
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for hours in [0, 5, 10]:
            sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=hours), temperature=20.0)
            )
        sequence.db_save_records()
        _reset_sequence_state(sequence)

        min_dt, max_dt = sequence.db_timestamp_range()
        assert min_dt == DatabaseTimestamp.from_datetime(base_time)
        assert max_dt == DatabaseTimestamp.from_datetime(base_time.add(hours=10))

    def test_db_mark_dirty_triggers_save(self, database_instance):
        """Marking a record dirty causes it to be re-saved."""
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        record = SampleDataRecord(date_time=base_time, temperature=20.0)
        sequence.db_insert_record(record)
        sequence.db_save_records()

        # Mutate and mark dirty
        record.temperature = 99.0
        sequence.db_mark_dirty_record(record)
        sequence.db_save_records()

        # Reload and verify update was persisted
        _reset_sequence_state(sequence)
        sequence.db_load_records()
        assert sequence.records[0].temperature == 99.0

    def test_db_vacuum_keep_hours(self, database_instance):
        """db_vacuum(keep_hours=N) retains only the last N hours of records."""
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        # 240 hourly records = 10 days
        for i in range(240):
            sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=20.0)
            )
        sequence.db_save_records()
        _reset_sequence_state(sequence)

        keep_hours = 5 * 24  # keep last 5 days
        deleted = sequence.db_vacuum(keep_hours=keep_hours)

        assert deleted == 240 - keep_hours
        assert sequence.db_count_records() == keep_hours

    def test_db_vacuum_keep_timestamp(self, database_instance):
        """db_vacuum(keep_timestamp=T) deletes everything before T (exclusive)."""
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(10):
            sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )
        sequence.db_save_records()
        _reset_sequence_state(sequence)

        # Keep from hours=5 onward — delete [0, 5), i.e. 5 records
        cutoff = base_time.add(hours=5)
        db_cutoff = DatabaseTimestamp.from_datetime(cutoff)
        deleted = sequence.db_vacuum(keep_timestamp=db_cutoff)

        assert deleted == 5
        assert sequence.db_count_records() == 5

        # Verify the boundary record (hours=5) was NOT deleted
        _reset_sequence_state(sequence)
        sequence.db_load_records()
        assert any(r.date_time == cutoff for r in sequence.records)

    def test_db_vacuum_no_argument(self, database_instance, config_eos):
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        record = SampleDataRecord(date_time=base_time, temperature=20.0)
        sequence.db_insert_record(record)
        sequence.db_save_records()

        config_eos.database.keep_duration_h = None
        assert sequence.db_vacuum() == 0

        config_eos.database.keep_duration_h = 0
        assert sequence.db_vacuum() == 1

    def test_db_vacuum_keep_hours_zero_deletes_all(self, database_instance):
        """keep_hours=0 should delete all records."""
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(5):
            sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )
        sequence.db_save_records()
        _reset_sequence_state(sequence)

        deleted = sequence.db_vacuum(keep_hours=0)
        assert deleted == 5
        assert sequence.db_count_records() == 0

    def test_db_get_stats(self, database_instance):
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        stats = sequence.db_get_stats()

        assert stats["enabled"] is True
        assert "backend" in stats
        assert "path" in stats
        assert "memory_records" in stats
        assert "total_records" in stats
        assert "compression_enabled" in stats
        assert "timestamp_range" in stats
        assert stats["timestamp_range"]["min"] == "None"
        assert stats["timestamp_range"]["max"] == "None"

    def test_db_get_stats_disabled(self, config_eos):
        config_eos.database.provider = None
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        stats = sequence.db_get_stats()
        assert stats == {"enabled": False}

    def test_lazy_load_phase_none_to_initial(self, database_instance):
        """Phase transitions from NONE to INITIAL when a range is loaded via ensure_loaded."""
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        assert sequence._db_load_phase is DatabaseRecordProtocolLoadPhase.NONE

        base_time = to_datetime("2024-01-01T00:00:00Z")
        for i in range(10):
            sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )
        sequence.db_save_records()
        _reset_sequence_state(sequence)

        # Use db_iterate_records — it calls _db_ensure_loaded which owns phase transitions
        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=3))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=7))
        list(sequence.db_iterate_records(start_timestamp=db_start, end_timestamp=db_end))

        assert sequence._db_load_phase is DatabaseRecordProtocolLoadPhase.INITIAL

    def test_lazy_load_phase_initial_to_full(self, database_instance):
        """Phase transitions from INITIAL to FULL when iterate is called without range."""
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(10):
            sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )
        sequence.db_save_records()
        _reset_sequence_state(sequence)

        # Load partial range → INITIAL
        # Use db_iterate_records — it calls _db_ensure_loaded which owns phase transitions
        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=3))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=7))
        list(sequence.db_iterate_records(start_timestamp=db_start, end_timestamp=db_end))
        assert sequence._db_load_phase is DatabaseRecordProtocolLoadPhase.INITIAL

        # Iterate without range → escalates to FULL
        list(sequence.db_iterate_records())
        assert sequence._db_load_phase is DatabaseRecordProtocolLoadPhase.FULL

    def test_range_covered_skips_redundant_load(self, database_instance):
        """_db_range_covered prevents a second DB query for the same range."""
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(10):
            sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )
        sequence.db_save_records()
        _reset_sequence_state(sequence)

        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=2))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=8))
        list(sequence.db_iterate_records(start_timestamp=db_start, end_timestamp=db_end))

        # Loaded range is now set
        assert sequence._db_loaded_range is not None
        assert sequence._db_range_covered(db_start, db_end) is True

        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=0))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=20))
        assert sequence._db_range_covered(db_start, db_end) is False

    def test_loaded_range_not_clobbered_by_expansion(self, database_instance):
        """Expanding left or right must not narrow the tracked loaded range."""
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(24):
            sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )
        sequence.db_save_records()
        _reset_sequence_state(sequence)

        # Initial window: hours 8–16
        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=8))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=16))
        list(sequence.db_iterate_records(start_timestamp=db_start, end_timestamp=db_end))

        assert sequence._db_loaded_range is not None
        initial_start, initial_end = sequence._db_loaded_range
        assert initial_start is not None
        assert initial_end is not None

        # Expand left: load hours 4–8
        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=4))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=16))
        list(sequence.db_iterate_records(start_timestamp=db_start, end_timestamp=db_end))

        assert sequence._db_loaded_range is not None
        expanded_start, expanded_end = sequence._db_loaded_range
        assert expanded_start is not None
        assert expanded_end is not None

        # Left boundary must have moved left; right must not have shrunk
        assert expanded_start <= initial_start
        assert expanded_end >= initial_end

    def test_duplicate_insert_raises(self, database_instance):
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        dt = to_datetime("2024-01-01T00:00:00Z")

        sequence.db_insert_record(SampleDataRecord(date_time=dt, temperature=1.0))
        with pytest.raises(ValueError, match="Duplicate timestamp"):
            sequence.db_insert_record(SampleDataRecord(date_time=dt, temperature=2.0))

    def test_autosave_delegates_to_save_records(self, database_instance):
        """db_autosave() is equivalent to db_save_records()."""
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(3):
            sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )

        saved = sequence.db_autosave()
        assert saved == 3
        assert len(sequence._db_dirty_timestamps) == 0

    def test_metadata_round_trip(self, database_instance):
        """Metadata can be saved and loaded back correctly."""
        sequence = SampleDataSequence()

        _clear_sequence_state(sequence)
        assert sequence._db_metadata is None

        _reset_sequence_state(sequence)
        assert sequence._db_metadata is not None
        created = sequence._db_metadata["created"]
        assert sequence._db_metadata["version"] == 1

        _reset_sequence_state(sequence)
        assert sequence._db_metadata is not None
        assert sequence._db_metadata["created"] == created
        assert sequence._db_metadata["version"] == 1

    def test_initial_load_window_respected(self, database_instance):
        """db_initial_time_window limits the initial load from DB."""

        class WindowedSequence(SampleDataSequence):
            def db_namespace(self) -> str:
                return "WindowedSequence"

            def db_initial_time_window(self) -> Optional[Duration]:
                return to_duration("2 hours")

        sequence = WindowedSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T12:00:00Z")

        # Store 24 hourly records centred on base_time
        for i in range(24):
            sequence.db_insert_record(
                SampleDataRecord(
                    date_time=base_time.subtract(hours=12).add(hours=i),
                    temperature=float(i),
                )
            )
        sequence.db_save_records()

        _reset_sequence_state(sequence)

        # Trigger initial window load centred on base_time
        sequence.config.database.initial_load_window_h = 2
        db_center = DatabaseTimestamp.from_datetime(base_time)
        sequence._db_load_initial_window(center_timestamp=db_center)

        # Only records within ±2h of base_time should be in memory
        assert len(sequence.records) <= 5  # at most 4h window = 4–5 records
        assert sequence._db_load_phase is DatabaseRecordProtocolLoadPhase.INITIAL


# ==================== Backend-Specific Tests ====================

class TestLMDBDatabase:
    """LMDB-specific tests."""

    def test_lmdb_compact(self, config_eos):
        config_eos.database.compression_level = 0
        config_eos.database.provider = "LMDB"
        db = get_database()
        assert db.is_open

        for i in range(1000):
            key = f"2024-01-01T{i:06d}+00:00".encode()
            db.save_records([(key, b"X" * 1000)])

        for i in range(500):
            key = f"2024-01-01T{i:06d}+00:00".encode()
            db.delete_records([key])

        lmdb = db._database()
        assert isinstance(lmdb, LMDBDatabase)
        lmdb.compact()
        assert db.count_records() == 500
        db.close()

    def test_lmdb_namespace_isolation(self, config_eos):
        """Records in different namespaces must not interfere."""
        config_eos.database.provider = "LMDB"
        db = get_database()
        assert db.is_open

        key = b"2024-01-01T00:00:00+00:00"
        db.save_records([(key, b"ns_a_data")], namespace="ns_a")
        db.save_records([(key, b"ns_b_data")], namespace="ns_b")

        ns_a = list(db.iterate_records(namespace="ns_a"))
        ns_b = list(db.iterate_records(namespace="ns_b"))

        assert ns_a[0][1] == b"ns_a_data"
        assert ns_b[0][1] == b"ns_b_data"
        db.close()


class TestSQLiteDatabase:
    """SQLite-specific tests."""

    def test_sqlite_vacuum(self, config_eos):
        config_eos.database.compression_level = 0
        config_eos.database.provider = "SQLite"
        db = get_database()
        assert db.is_open

        records = [
            (f"2024-01-{i + 1:02d}T00:00:00+00:00".encode(), b"data" * 100)
            for i in range(100)
        ]
        db.save_records(records)

        keys_to_delete = [f"2024-01-{i + 1:02d}T00:00:00+00:00".encode() for i in range(50)]
        db.delete_records(keys_to_delete)

        sqlitedb = db._database()
        assert isinstance(sqlitedb, SQLiteDatabase)
        sqlitedb.vacuum()

        assert db.count_records() == 50
        db.close()

    def test_sqlite_namespace_isolation(self, config_eos):
        """Records in different namespaces must not interfere."""
        config_eos.database.provider = "SQLite"
        db = get_database()
        assert db.is_open

        key = b"2024-01-01T00:00:00+00:00"
        db.save_records([(key, b"ns_a_data")], namespace="ns_a")
        db.save_records([(key, b"ns_b_data")], namespace="ns_b")

        ns_a = list(db.iterate_records(namespace="ns_a"))
        ns_b = list(db.iterate_records(namespace="ns_b"))

        assert ns_a[0][1] == b"ns_a_data"
        assert ns_b[0][1] == b"ns_b_data"
        db.close()


# ==================== Integration Tests ====================

class TestIntegration:
    """Full end-to-end workflow tests."""

    def test_full_workflow(self, config_eos, database_instance):
        """Save → partial load → update → vacuum → verify."""
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        # Step 1: Insert 100 records and persist
        for i in range(100):
            sequence.db_insert_record(
                SampleDataRecord(
                    date_time=base_time.add(hours=i),
                    temperature=20.0 + i * 0.1,
                    humidity=60.0,
                )
            )
        sequence.db_save_records()

        storage_count = sequence.database.count_records(namespace="SampleDataSequence")
        assert storage_count == 100
        assert sequence.db_count_records() == 100

        # Step 2: Clear memory and load a specific range
        _reset_sequence_state(sequence)
        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=20))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=40))
        loaded = sequence.db_load_records(db_start, db_end)
        assert loaded == 20
        assert len(sequence.records) == 20

        # Step 3: Update records in memory and persist
        for record in sequence.records:
            record.humidity = 75.0
            sequence.db_mark_dirty_record(record)
        sequence.db_save_records()

        # Step 4: Reload the range and verify updates
        _reset_sequence_state(sequence)
        sequence.db_load_records(db_start, db_end)
        assert all(r.humidity == 75.0 for r in sequence.records)

        # Step 5: Vacuum — keep from hours=75 onward (delete first 75)
        db_cutoff = DatabaseTimestamp.from_datetime(base_time.add(hours=75))
        deleted = sequence.db_vacuum(keep_timestamp=db_cutoff)
        assert deleted == 75
        assert sequence.db_count_records() == 25

        # Step 6: Stats reflect vacuum result
        _reset_sequence_state(sequence)
        stats = sequence.db_get_stats()
        assert stats["total_records"] == 25

    def test_error_handling_db_disabled(self, config_eos):
        """Operations on a disabled DB raise clearly."""
        config_eos.database.provider = None
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)

        assert sequence.db_enabled is False

        # Save is a no-op and returns 0 when disabled — no RuntimeError
        # (mixin returns 0 early when not enabled)
        result = sequence.db_save_records()
        assert result == 0

    def test_persistence_across_resets(self, database_instance):
        """Data written in one memory session is available after reset."""
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-06-01T00:00:00Z")

        for i in range(20):
            sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )
        sequence.db_save_records()

        # Simulate a restart: reset memory state
        _reset_sequence_state(sequence)
        assert len(sequence.records) == 0

        loaded = sequence.db_load_records()
        assert loaded == 20
        assert sequence.records[0].temperature == 0.0
        assert sequence.records[-1].temperature == 19.0


# ==================== Performance Tests ====================

class TestPerformance:
    """Throughput benchmarks — not correctness tests."""

    def test_insert_throughput(self, config_eos, database_instance):
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")
        n = 10_000

        start = time.perf_counter()
        for i in range(n):
            sequence.db_insert_record(
                SampleDataRecord(
                    date_time=base_time.add(minutes=i),
                    temperature=20.0 + (i % 100) * 0.1,
                )
            )
        insert_duration = time.perf_counter() - start
        print(f"\nInserted {n} records in {insert_duration:.2f}s "
              f"({n / insert_duration:.0f} rec/s)")

        assert len(sequence.records) == n

    def test_save_throughput(self, config_eos, database_instance):
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")
        n = 10_000

        for i in range(n):
            sequence.db_insert_record(
                SampleDataRecord(
                    date_time=base_time.add(minutes=i),
                    temperature=20.0 + (i % 100) * 0.1,
                )
            )

        start = time.perf_counter()
        saved = sequence.db_save_records()
        save_duration = time.perf_counter() - start

        assert saved == n
        print(f"\nSaved {n} records in {save_duration:.2f}s "
              f"({n / save_duration:.0f} rec/s)")

    def test_load_throughput(self, config_eos, database_instance):
        sequence = SampleDataSequence()
        _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")
        n = 10_000

        for i in range(n):
            sequence.db_insert_record(
                SampleDataRecord(
                    date_time=base_time.add(minutes=i),
                    temperature=20.0 + (i % 100) * 0.1,
                )
            )
        sequence.db_save_records()
        _reset_sequence_state(sequence)

        start = time.perf_counter()
        loaded = sequence.db_load_records()
        load_duration = time.perf_counter() - start

        assert loaded == n
        print(f"\nLoaded {n} records in {load_duration:.2f}s "
              f"({n / load_duration:.0f} rec/s)")
