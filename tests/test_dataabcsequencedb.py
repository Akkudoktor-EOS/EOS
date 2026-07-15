"""Pytest tests for async DataSequence with persistence.

Tests the async DataSequence with database persistence.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from pathlib import Path
from typing import AsyncIterator, Optional, Type

import pytest
import pytest_asyncio
from pydantic import Field

from akkudoktoreos.core.coreabc import get_database
from akkudoktoreos.core.dataabc import DataProvider, DataRecord, DataSequence
from akkudoktoreos.core.database import Database, LMDBDatabase, SQLiteDatabase
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


@pytest_asyncio.fixture
async def async_database_instance(
    config_eos,
    database_provider: str,
) -> AsyncIterator[Database]:
    """Open a database instance for testing and close it afterwards."""
    config_eos.database.compression_level = 6
    config_eos.database.provider = database_provider

    db = get_database()

    await db.open()

    assert db.provider_id() == database_provider
    assert db.is_open is True

    yield db

    await db.close()

    config_eos.database.provider = None


# ==================== Helpers ====================

async def _clear_sequence_state(sequence) -> None:
    """Clear runtime DB state without re-instantiating the singleton.

    Does _NOT_ initialize the DB state.
    """
    await sequence.db_delete_records()
    try:
        sequence._db_metadata = None
        await sequence.database().set_metadata(None, namespace=sequence.db_namespace())
    except Exception:
        # Database may not be available, just skip
        pass
    try:
        del sequence._db_initialized
    except Exception:
        # May not be set
        pass


async def _reset_sequence_state(sequence) -> None:
    """Reset runtime DB state without re-instantiating the singleton."""
    try:
        sequence.records = []
        del sequence._db_initialized
    except Exception:
        # May not be set
        pass
    await sequence._db_ensure_initialized()


# Sample Data

class SampleDataRecord(DataRecord):
    """Minimal DataRecord for testing."""
    temperature: float = Field(default=0.0)
    humidity: float = Field(default=0.0)
    pressure: float = Field(default=0.0)


class SampleDataSequence(DataSequence):
    """DataSequence subclass with database support."""
    records: list[SampleDataRecord] = Field(default_factory=list)

    @classmethod
    def record_class(cls) -> Type[SampleDataRecord]:
        return SampleDataRecord

    def db_namespace(self) -> str:
        return "SampleDataSequence"


class SampleDataProvider(DataProvider):
    """DataProvider subclass with database support."""
    records: list[SampleDataRecord] = Field(default_factory=list)

    @classmethod
    def record_class(cls) -> Type[SampleDataRecord]:
        return SampleDataRecord

    def provider_id(self) -> str:
        return "SampleDataProvider"

    def enabled(self) -> bool:
        return True

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        pass

    def db_namespace(self) -> str:
        return "SampleDataProvider"


# ==================== DatabaseRecordProtocolMixin Tests ====================

@pytest.mark.asyncio
class TestDataSequenceDatabaseProtocol:
    """Tests for DatabaseRecordProtocolMixin via SampleDataSequence."""

    async def test_db_enabled_when_db_open(self, async_database_instance):
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        assert sequence.db_enabled is True

    async def test_db_disabled_when_db_closed(self, config_eos):
        config_eos.database.provider = None
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        assert sequence.db_enabled is False

    async def test_insert_and_save_records(self, async_database_instance):
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(10):
            await sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=20.0 + i)
            )

        # All 10 are dirty/new, none persisted yet
        assert len(sequence.records) == 10
        assert len(sequence._db_new_timestamps) == 10

        saved = await sequence.db_save_records()
        assert saved == 10  # 10 inserts + 0 deletes
        assert len(sequence._db_dirty_timestamps) == 0
        assert len(sequence._db_new_timestamps) == 0

    async def test_save_returns_insert_plus_delete_count(self, async_database_instance):
        """db_save_records() return value = saved_inserts + deleted_count."""
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(5):
            await sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )
        # Persist the 5 records
        await sequence.db_save_records()

        # Delete 2 of them
        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=2))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=4))
        deleted = await sequence.db_delete_records(start_timestamp=db_start, end_timestamp=db_end)
        # Insert 3 new ones
        for i in range(10, 13):
            await sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )

        result = await sequence.db_save_records()
        # 3 inserts + 2 deletes = 5
        assert result == 5

    async def test_load_records_from_db(self, async_database_instance):
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(10):
            await sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=20.0 + i)
            )
        await sequence.db_save_records()

        # Clear memory, then reload from DB
        await _reset_sequence_state(sequence)
        loaded = await sequence.db_load_records()

        assert loaded == 10
        assert len(sequence.records) == 10
        for i, record in enumerate(sequence.records):
            assert record.temperature == 20.0 + i

    async def test_load_records_with_range(self, async_database_instance):
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(10):
            await sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=20.0 + i)
            )
        await sequence.db_save_records()
        await _reset_sequence_state(sequence)

        # Load [hours=3, hours=7) → 4 records (3, 4, 5, 6)
        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=3))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=7))
        loaded = await sequence.db_load_records(start_timestamp=db_start, end_timestamp=db_end)
        assert loaded == 4
        assert sequence.records[0].temperature == 23.0
        assert sequence.records[-1].temperature == 26.0

    async def test_iterate_records_triggers_lazy_load(self, async_database_instance):
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(10):
            await sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=20.0 + i)
            )
        await sequence.db_save_records()
        await _reset_sequence_state(sequence)

        # db_iterate_records calls _db_ensure_loaded internally
        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=2))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=5))
        records = [record async for record in sequence.db_iterate_records(start_timestamp=db_start, end_timestamp=db_end)]
        assert len(records) == 3
        assert all(base_time.add(hours=2) <= r.date_time < base_time.add(hours=5) for r in records)

    async def test_delete_records(self, async_database_instance):
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(6):
            await sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=20.0)
            )
        await sequence.db_save_records()

        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=2))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=5))
        deleted = await sequence.db_delete_records(start_timestamp=db_start, end_timestamp=db_end)
        assert deleted == 3

        # Persist the deletions
        await sequence.db_save_records()

        await _reset_sequence_state(sequence)
        await sequence.db_load_records()
        assert len(sequence.records) == 3

    async def test_delete_tombstone_prevents_resurrection(self, async_database_instance):
        """Deleted records must not re-appear when db_load_records is called."""
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(3):
            await sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )
        await sequence.db_save_records()

        # Delete middle record
        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=1))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=2))
        deleted = await sequence.db_delete_records(start_timestamp=db_start, end_timestamp=db_end)
        assert deleted == 1

        # Do NOT persist yet — tombstone lives only in memory
        # Loading should not resurrect the tombstoned record
        loaded = await sequence.db_load_records()
        assert all(r.date_time != base_time.add(hours=1) for r in sequence.records)

    async def test_insert_after_delete_clears_tombstone(self, async_database_instance):
        """Re-inserting a deleted datetime must clear its tombstone."""
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")
        dt = base_time.add(hours=5)

        await sequence.db_insert_record(SampleDataRecord(date_time=dt, temperature=10.0))
        await sequence.db_save_records()

        db_start = DatabaseTimestamp.from_datetime(dt)
        db_end = sequence._db_timestamp_after(db_start)
        deleted = await sequence.db_delete_records(start_timestamp=db_start, end_timestamp=db_end)
        assert deleted == 1

        await sequence.db_save_records()

        # Re-insert the same datetime
        await sequence.db_insert_record(SampleDataRecord(date_time=dt, temperature=99.0))
        assert dt not in sequence._db_deleted_timestamps
        await sequence.db_save_records()

        await _reset_sequence_state(sequence)
        await sequence.db_load_records()
        assert any(r.date_time == dt and r.temperature == 99.0 for r in sequence.records)

    async def test_db_count_records_memory_only(self):
        """When db is disabled, count reflects memory only."""
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)

        # Without a live DB, db_enabled is False
        if sequence.db_enabled:
            pytest.skip("DB is open; this test requires it to be closed")

        base_time = to_datetime("2024-01-01T00:00:00Z")
        for i in range(5):
            await sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i)),
                mark_dirty=False,
            )
        count = await sequence.db_count_records()
        assert count == 5

    async def test_db_count_records_combined(self, async_database_instance):
        """db_count_records = storage + new_unpersisted - pending_deletes."""
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        # Persist 10 records
        for i in range(10):
            await sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )
        await sequence.db_save_records()

        # Add 3 new unpersisted records
        for i in range(10, 13):
            await sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )

        # Delete 2 persisted records (not yet saved)
        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=0))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=2))
        deleted = await sequence.db_delete_records(start_timestamp=db_start, end_timestamp=db_end)
        assert deleted == 2

        # storage=10, new=3, pending_deletes=2 → expected=11
        count = await sequence.db_count_records()
        assert count == 11

    async def test_db_timestamp_range_empty(self, async_database_instance):
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        min_dt, max_dt = await sequence.db_timestamp_range()
        assert min_dt is None
        assert max_dt is None

    async def test_db_timestamp_range_with_records(self, async_database_instance):
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for hours in [0, 5, 10]:
            await sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=hours), temperature=20.0)
            )
        await sequence.db_save_records()
        await _reset_sequence_state(sequence)

        min_dt, max_dt = await sequence.db_timestamp_range()
        assert min_dt == DatabaseTimestamp.from_datetime(base_time)
        assert max_dt == DatabaseTimestamp.from_datetime(base_time.add(hours=10))

    async def test_db_mark_dirty_triggers_save(self, async_database_instance):
        """Marking a record dirty causes it to be re-saved."""
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        record = SampleDataRecord(date_time=base_time, temperature=20.0)
        await sequence.db_insert_record(record)
        await sequence.db_save_records()

        # Mutate and mark dirty
        record.temperature = 99.0
        await sequence.db_mark_dirty_record(record)
        await sequence.db_save_records()

        # Reload and verify update was persisted
        await _reset_sequence_state(sequence)
        await sequence.db_load_records()
        assert sequence.records[0].temperature == 99.0

    async def test_db_vacuum_keep_hours(self, async_database_instance):
        """db_vacuum(keep_hours=N) retains only the last N hours of records."""
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        # 240 hourly records = 10 days
        for i in range(240):
            await sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=20.0)
            )
        await sequence.db_save_records()
        await _reset_sequence_state(sequence)

        keep_hours = 5 * 24  # keep last 5 days
        deleted = await sequence.db_vacuum(keep_hours=keep_hours)

        assert deleted == 240 - keep_hours
        count = await sequence.db_count_records()
        assert count == keep_hours

    async def test_db_vacuum_keep_timestamp(self, async_database_instance):
        """db_vacuum(keep_timestamp=T) deletes everything before T (exclusive)."""
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(10):
            await sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )
        await sequence.db_save_records()
        await _reset_sequence_state(sequence)

        # Keep from hours=5 onward — delete [0, 5), i.e. 5 records
        cutoff = base_time.add(hours=5)
        db_cutoff = DatabaseTimestamp.from_datetime(cutoff)
        deleted = await sequence.db_vacuum(keep_timestamp=db_cutoff)

        assert deleted == 5
        count = await sequence.db_count_records()
        assert count == 5

        # Verify the boundary record (hours=5) was NOT deleted
        await _reset_sequence_state(sequence)
        await sequence.db_load_records()
        assert any(r.date_time == cutoff for r in sequence.records)

    async def test_db_vacuum_no_argument(self, async_database_instance, config_eos):
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        record = SampleDataRecord(date_time=base_time, temperature=20.0)
        await sequence.db_insert_record(record)
        await sequence.db_save_records()

        config_eos.database.keep_duration_h = None
        deleted = await sequence.db_vacuum()
        assert deleted == 0

        config_eos.database.keep_duration_h = 0
        deleted = await sequence.db_vacuum()
        assert deleted == 1

    async def test_db_vacuum_keep_hours_zero_deletes_all(self, async_database_instance):
        """keep_hours=0 should delete all records."""
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(5):
            await sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )
        await sequence.db_save_records()
        await _reset_sequence_state(sequence)

        deleted = await sequence.db_vacuum(keep_hours=0)
        assert deleted == 5
        count = await sequence.db_count_records()
        assert count == 0

    async def test_db_get_stats(self, async_database_instance):
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        stats = await sequence.db_get_stats()

        assert stats["enabled"] is True
        assert "backend" in stats
        assert "path" in stats
        assert "memory_records" in stats
        assert "total_records" in stats
        assert "compression_enabled" in stats
        assert "timestamp_range" in stats
        assert stats["timestamp_range"]["min"] == "None"
        assert stats["timestamp_range"]["max"] == "None"

    async def test_db_get_stats_disabled(self, config_eos):
        config_eos.database.provider = None
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        stats = await sequence.db_get_stats()
        assert stats == {"enabled": False}

    async def test_lazy_load_phase_none_to_initial(self, async_database_instance):
        """Phase transitions from NONE to INITIAL when a range is loaded via ensure_loaded."""
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        assert sequence._db_load_phase is DatabaseRecordProtocolLoadPhase.NONE

        base_time = to_datetime("2024-01-01T00:00:00Z")
        for i in range(10):
            await sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )
        await sequence.db_save_records()
        await _reset_sequence_state(sequence)

        # Use db_iterate_records — it calls _db_ensure_loaded which owns phase transitions
        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=3))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=7))
        records = [record async for record in sequence.db_iterate_records(start_timestamp=db_start, end_timestamp=db_end)]

        assert sequence._db_load_phase is DatabaseRecordProtocolLoadPhase.INITIAL

    async def test_lazy_load_phase_initial_to_full(self, async_database_instance):
        """Phase transitions from INITIAL to FULL when iterate is called without range."""
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(10):
            await sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )
        await sequence.db_save_records()
        await _reset_sequence_state(sequence)

        # Load partial range → INITIAL
        # Use db_iterate_records — it calls _db_ensure_loaded which owns phase transitions
        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=3))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=7))
        records = [record async for record in sequence.db_iterate_records(start_timestamp=db_start, end_timestamp=db_end)]
        assert sequence._db_load_phase is DatabaseRecordProtocolLoadPhase.INITIAL

        # Iterate without range → escalates to FULL
        records = [record async for record in sequence.db_iterate_records()]
        assert sequence._db_load_phase is DatabaseRecordProtocolLoadPhase.FULL

    async def test_range_covered_skips_redundant_load(self, async_database_instance):
        """_db_range_covered prevents a second DB query for the same range."""
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(10):
            await sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )
        await sequence.db_save_records()
        await _reset_sequence_state(sequence)

        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=2))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=8))
        records = [record async for record in sequence.db_iterate_records(start_timestamp=db_start, end_timestamp=db_end)]

        # Loaded range is now set
        assert sequence._db_loaded_range is not None
        assert sequence._db_range_covered(db_start, db_end) is True

        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=0))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=20))
        assert sequence._db_range_covered(db_start, db_end) is False

    async def test_loaded_range_not_clobbered_by_expansion(self, async_database_instance):
        """Expanding left or right must not narrow the tracked loaded range."""
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        for i in range(24):
            await sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )
        await sequence.db_save_records()
        await _reset_sequence_state(sequence)

        # Initial window: hours 8–16
        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=8))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=16))
        records = [record async for record in sequence.db_iterate_records(start_timestamp=db_start, end_timestamp=db_end)]

        assert sequence._db_loaded_range is not None
        initial_start, initial_end = sequence._db_loaded_range
        assert initial_start is not None
        assert initial_end is not None

        # Expand left: load hours 4–8
        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=4))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=16))
        records = [record async for record in sequence.db_iterate_records(start_timestamp=db_start, end_timestamp=db_end)]

        assert sequence._db_loaded_range is not None
        expanded_start, expanded_end = sequence._db_loaded_range
        assert expanded_start is not None
        assert expanded_end is not None

        # Left boundary must have moved left; right must not have shrunk
        assert expanded_start <= initial_start
        assert expanded_end >= initial_end

    async def test_duplicate_insert_raises(self, async_database_instance):
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        dt = to_datetime("2024-01-01T00:00:00Z")

        await sequence.db_insert_record(SampleDataRecord(date_time=dt, temperature=1.0))
        with pytest.raises(ValueError, match="Duplicate timestamp"):
            await sequence.db_insert_record(SampleDataRecord(date_time=dt, temperature=2.0))

    async def test_metadata_round_trip(self, async_database_instance):
        """Metadata can be saved and loaded back correctly."""
        sequence = SampleDataSequence()

        await _clear_sequence_state(sequence)
        assert sequence._db_metadata is None

        await _reset_sequence_state(sequence)
        assert sequence._db_metadata is not None
        created = sequence._db_metadata["created"]
        assert sequence._db_metadata["version"] == 1

        await _reset_sequence_state(sequence)
        assert sequence._db_metadata is not None
        assert sequence._db_metadata["created"] == created
        assert sequence._db_metadata["version"] == 1

    async def test_initial_load_window_respected(self, async_database_instance):
        """db_initial_time_window limits the initial load from DB."""

        class WindowedSequence(SampleDataSequence):
            def db_namespace(self) -> str:
                return "WindowedSequence"

            def db_initial_time_window(self) -> Optional[Duration]:
                return to_duration("2 hours")

        sequence = WindowedSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T12:00:00Z")

        # Store 24 hourly records centred on base_time
        for i in range(24):
            await sequence.db_insert_record(
                SampleDataRecord(
                    date_time=base_time.subtract(hours=12).add(hours=i),
                    temperature=float(i),
                )
            )
        await sequence.db_save_records()

        await _reset_sequence_state(sequence)

        # Trigger initial window load centred on base_time
        sequence.config.database.initial_load_window_h = 2
        db_center = DatabaseTimestamp.from_datetime(base_time)
        await sequence._db_load_initial_window(center_timestamp=db_center)

        # Only records within ±2h of base_time should be in memory
        assert len(sequence.records) <= 5  # at most 4h window = 4–5 records
        assert sequence._db_load_phase is DatabaseRecordProtocolLoadPhase.INITIAL
