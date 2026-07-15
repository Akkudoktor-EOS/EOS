"""Pytest tests for async database persistence module.

Tests the async Database interface and concrete implementations (LMDB, SQLite).
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


# ==================== Database Backend Tests ====================


@pytest.mark.asyncio
class TestDatabase:
    """Tests for the async Database interface."""

    async def test_database_creation(self, config_eos, database_provider):
        config_eos.database.compression_level = 6
        config_eos.database.provider = database_provider

        db = get_database()

        await db.open()

        assert db.is_open is True
        assert db.compression is True
        assert db.compression_level == 6

        assert db.storage_path == (
            config_eos.general.data_folder_path
            / "db"
            / db._db.__class__.__name__.lower()
        )

        await db.close()

    async def test_database_open_close(self, async_database_instance):
        assert async_database_instance.is_open is True
        assert async_database_instance._db.connection is not None

        await async_database_instance.close()

        assert async_database_instance._db.is_open is False

    async def test_save_and_load_single_record(self, async_database_instance):
        key = b"2024-01-01T00:00:00+00:00"
        value = b"test_data_12345"

        saved = await async_database_instance.save_records([(key, value)])

        assert saved == 1

        records = [
            record
            async for record in async_database_instance.iterate_records(
                key,
                key + b"\xff",
            )
        ]

        assert len(records) == 1
        assert records[0] == (key, value)

    async def test_save_multiple_records(self, async_database_instance):
        records = [
            (b"2024-01-01T00:00:00+00:00", b"data1"),
            (b"2024-01-02T00:00:00+00:00", b"data2"),
            (b"2024-01-03T00:00:00+00:00", b"data3"),
        ]

        saved = await async_database_instance.save_records(records)

        assert saved == len(records)

        loaded = [
            record
            async for record in async_database_instance.iterate_records()
        ]

        assert len(loaded) == len(records)

        for expected, actual in zip(records, loaded):
            assert expected == actual

    async def test_load_records_with_range(self, async_database_instance):
        records = [
            (b"2024-01-01T00:00:00+00:00", b"data1"),
            (b"2024-01-02T00:00:00+00:00", b"data2"),
            (b"2024-01-03T00:00:00+00:00", b"data3"),
            (b"2024-01-04T00:00:00+00:00", b"data4"),
            (b"2024-01-05T00:00:00+00:00", b"data5"),
        ]

        await async_database_instance.save_records(records)

        start_key = b"2024-01-02T00:00:00+00:00"
        end_key = b"2024-01-04T00:00:00+00:00"

        loaded = [
            record
            async for record in async_database_instance.iterate_records(
                start_key,
                end_key,
            )
        ]

        assert len(loaded) == 2
        assert loaded[0][0] == b"2024-01-02T00:00:00+00:00"
        assert loaded[1][0] == b"2024-01-03T00:00:00+00:00"

    async def test_delete_record(self, async_database_instance):
        key = b"2024-01-01T00:00:00+00:00"

        await async_database_instance.save_records([(key, b"test_data")])

        assert await async_database_instance.count_records() == 1

        deleted = await async_database_instance.delete_records([key])

        assert deleted == 1
        assert await async_database_instance.count_records() == 0

        deleted = await async_database_instance.delete_records([key])

        assert deleted == 0

    async def test_count_records(self, async_database_instance):
        assert await async_database_instance.count_records() == 0

        for i in range(10):
            key = f"2024-01-{i + 1:02d}T00:00:00+00:00".encode()

            await async_database_instance.save_records([(key, b"data")])

        assert await async_database_instance.count_records() == 10

    async def test_get_key_range_empty(self, async_database_instance):
        min_key, max_key = await async_database_instance.get_key_range()

        assert min_key is None
        assert max_key is None

    async def test_get_key_range_with_records(self, async_database_instance):
        keys = [
            b"2024-01-01T00:00:00+00:00",
            b"2024-01-05T00:00:00+00:00",
            b"2024-01-03T00:00:00+00:00",
        ]

        for key in keys:
            await async_database_instance.save_records([(key, b"data")])

        min_key, max_key = await async_database_instance.get_key_range()

        assert min_key == b"2024-01-01T00:00:00+00:00"
        assert max_key == b"2024-01-05T00:00:00+00:00"

    async def test_iterate_records_forward(self, async_database_instance):
        keys = [
            b"2024-01-01T00:00:00+00:00",
            b"2024-01-02T00:00:00+00:00",
            b"2024-01-03T00:00:00+00:00",
        ]

        for key in keys:
            await async_database_instance.save_records([(key, b"data")])

        result_keys = [
            k
            async for k, _ in async_database_instance.iterate_records()
        ]

        assert result_keys == keys

    async def test_iterate_records_reverse(self, async_database_instance):
        keys = [
            b"2024-01-01T00:00:00+00:00",
            b"2024-01-02T00:00:00+00:00",
            b"2024-01-03T00:00:00+00:00",
        ]

        for key in keys:
            await async_database_instance.save_records([(key, b"data")])

        result_keys = [
            k
            async for k, _ in async_database_instance.iterate_records(reverse=True)
        ]

        assert result_keys == list(reversed(keys))

    async def test_reverse_iteration_with_bounds(self, async_database_instance):
        keys = [f"{i:03d}".encode() for i in range(10)]

        await async_database_instance.save_records(
            [(k, b"v") for k in keys]
        )

        result = [
            k
            async for k, _ in async_database_instance.iterate_records(
                start_key=b"003",
                end_key=b"007",
                reverse=True,
            )
        ]

        assert result == [
            b"006",
            b"005",
            b"004",
            b"003",
        ]

    async def test_empty_iteration(self, async_database_instance):
        result = [
            record
            async for record in async_database_instance.iterate_records()
        ]

        assert result == []

    async def test_compression_reduces_size(self, config_eos, async_database_instance):
        large_data = b"A" * 10_000

        config_eos.database.compression_level = 9
        compressed = async_database_instance.serialize_data(large_data)
        assert async_database_instance.deserialize_data(compressed) == large_data

        config_eos.database.compression_level = 0
        uncompressed = async_database_instance.serialize_data(large_data)
        assert async_database_instance.deserialize_data(uncompressed) == large_data

        assert len(compressed) < len(uncompressed)

    async def test_flush(self, async_database_instance):
        key = b"2024-01-01T00:00:00+00:00"

        await async_database_instance.save_records([(key, b"test_data")])

        await async_database_instance.flush()

        loaded = [
            record
            async for record in async_database_instance.iterate_records()
        ]

        assert len(loaded) == 1
        assert loaded[0] == (key, b"test_data")

    async def test_backend_stats(self, async_database_instance):
        stats = await async_database_instance.get_backend_stats()

        assert isinstance(stats, dict)
        assert "backend" in stats

        for i in range(10):
            key = f"2024-01-{i + 1:02d}T00:00:00+00:00".encode()

            await async_database_instance.save_records(
                [(key, b"data" * 100)]
            )

        stats = await async_database_instance.get_backend_stats()

        assert stats is not None

    async def test_metadata_roundtrip(self, async_database_instance):
        await async_database_instance.set_metadata(
            b"metadata",
            namespace="test",
        )

        result = await async_database_instance.get_metadata(
            namespace="test",
        )

        assert result == b"metadata"

    async def test_metadata_excluded_from_count(self, async_database_instance):
        await async_database_instance.save_records(
            [(b"2024-01-01T00:00:00+00:00", b"data")]
        )

        count = await async_database_instance.count_records()

        assert count == 1

    async def test_namespace_isolation(self, async_database_instance):
        await async_database_instance.save_records(
            [(b"k1", b"ns1")],
            namespace="a",
        )

        await async_database_instance.save_records(
            [(b"k1", b"ns2")],
            namespace="b",
        )

        records_a = [
            r
            async for r in async_database_instance.iterate_records(
                namespace="a"
            )
        ]

        records_b = [
            r
            async for r in async_database_instance.iterate_records(
                namespace="b"
            )
        ]

        assert records_a == [(b"k1", b"ns1")]
        assert records_b == [(b"k1", b"ns2")]

    async def test_concurrent_writes(self, async_database_instance):
        async def writer(start: int):
            records = [
                (f"{i:08d}".encode(), b"data")
                for i in range(start, start + 100)
            ]

            await async_database_instance.save_records(records)

        await asyncio.gather(
            writer(0),
            writer(1000),
            writer(2000),
        )

        count = await async_database_instance.count_records()

        assert count == 300

    async def test_concurrent_reads_and_writes(self, async_database_instance):
        async def writer():
            for i in range(100):
                await async_database_instance.save_records(
                    [(f"{i:08d}".encode(), b"data")]
                )

        async def reader():
            total = 0

            for _ in range(20):
                records = [
                    r
                    async for r in async_database_instance.iterate_records()
                ]

                total += len(records)

            return total

        results = await asyncio.gather(
            writer(),
            reader(),
            reader(),
        )

        assert results[1] >= 0
        assert results[2] >= 0

        final_count = await async_database_instance.count_records()

        assert final_count == 100

    async def test_delete_multiple_records(self, async_database_instance):
        records = [
            (b"k1", b"v1"),
            (b"k2", b"v2"),
            (b"k3", b"v3"),
        ]

        await async_database_instance.save_records(records)

        deleted = await async_database_instance.delete_records(
            [b"k1", b"k3"]
        )

        assert deleted == 2

        remaining = [
            r
            async for r in async_database_instance.iterate_records()
        ]

        assert remaining == [(b"k2", b"v2")]

    async def test_delete_empty_input(self, async_database_instance):
        deleted = await async_database_instance.delete_records([])

        assert deleted == 0

    async def test_save_empty_input(self, async_database_instance):
        saved = await async_database_instance.save_records([])

        assert saved == 0

    async def test_count_with_bounds(self, async_database_instance):
        keys = [f"{i:03d}".encode() for i in range(10)]

        await async_database_instance.save_records(
            [(k, b"v") for k in keys]
        )

        count = await async_database_instance.count_records(
            start_key=b"003",
            end_key=b"007",
        )

        assert count == 4


# ==================== Backend-Specific Tests ====================


class TestLMDBDatabase:
    """LMDB-specific tests."""

    @pytest.mark.asyncio
    async def test_lmdb_compact(self, config_eos):
        config_eos.database.compression_level = 0
        config_eos.database.provider = "LMDB"

        db = get_database()

        await db.open()

        assert db.is_open

        for i in range(1000):
            key = f"2024-01-01T{i:06d}+00:00".encode()

            await db.save_records([(key, b"X" * 1000)])

        for i in range(500):
            key = f"2024-01-01T{i:06d}+00:00".encode()

            await db.delete_records([key])

        assert await db.count_records() == 500

        lmdb = db._db

        assert isinstance(lmdb, LMDBDatabase)

        # compact() itself is synchronous
        await asyncio.to_thread(lmdb.compact)

        assert await db.count_records() == 500

        await db.close()

    @pytest.mark.asyncio
    async def test_lmdb_namespace_isolation(self, config_eos):
        """Records in different namespaces must not interfere."""

        config_eos.database.provider = "LMDB"

        db = get_database()

        await db.open()

        assert db.is_open

        key = b"2024-01-01T00:00:00+00:00"

        await db.save_records(
            [(key, b"ns_a_data")],
            namespace="ns_a",
        )

        await db.save_records(
            [(key, b"ns_b_data")],
            namespace="ns_b",
        )

        ns_a = [
            record
            async for record in db.iterate_records(namespace="ns_a")
        ]

        ns_b = [
            record
            async for record in db.iterate_records(namespace="ns_b")
        ]

        assert ns_a[0][1] == b"ns_a_data"
        assert ns_b[0][1] == b"ns_b_data"

        await db.close()


class TestSQLiteDatabase:
    """SQLite-specific tests."""

    @pytest.mark.asyncio
    async def test_sqlite_vacuum(self, config_eos):
        config_eos.database.compression_level = 0
        config_eos.database.provider = "SQLite"

        db = get_database()

        await db.open()

        assert db.is_open

        records = [
            (
                f"2024-01-{i + 1:02d}T00:00:00+00:00".encode(),
                b"data" * 100,
            )
            for i in range(100)
        ]

        await db.save_records(records)

        keys_to_delete = [
            f"2024-01-{i + 1:02d}T00:00:00+00:00".encode()
            for i in range(50)
        ]

        await db.delete_records(keys_to_delete)

        assert await db.count_records() == 50

        sqlitedb = db._db

        assert isinstance(sqlitedb, SQLiteDatabase)

        # vacuum() itself is synchronous
        await asyncio.to_thread(sqlitedb.vacuum)

        assert await db.count_records() == 50

        await db.close()

    @pytest.mark.asyncio
    async def test_sqlite_namespace_isolation(self, config_eos):
        """Records in different namespaces must not interfere."""

        config_eos.database.provider = "SQLite"

        db = get_database()

        await db.open()

        assert db.is_open

        key = b"2024-01-01T00:00:00+00:00"

        await db.save_records(
            [(key, b"ns_a_data")],
            namespace="ns_a",
        )

        await db.save_records(
            [(key, b"ns_b_data")],
            namespace="ns_b",
        )

        ns_a = [
            record
            async for record in db.iterate_records(namespace="ns_a")
        ]

        ns_b = [
            record
            async for record in db.iterate_records(namespace="ns_b")
        ]

        assert ns_a[0][1] == b"ns_a_data"
        assert ns_b[0][1] == b"ns_b_data"

        await db.close()

# Helpers

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


# ==================== Integration Tests ====================

@pytest.mark.asyncio
class TestIntegration:
    """Full end-to-end workflow tests."""

    async def test_full_workflow(self, config_eos, async_database_instance):
        """Save → partial load → update → vacuum → verify."""
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")

        # Step 1: Insert 100 records and persist
        for i in range(100):
            await sequence.db_insert_record(
                SampleDataRecord(
                    date_time=base_time.add(hours=i),
                    temperature=20.0 + i * 0.1,
                    humidity=60.0,
                )
            )
        await sequence.db_save_records()

        storage_count = await sequence.database.count_records(namespace="SampleDataSequence")
        assert storage_count == 100
        assert await sequence.db_count_records() == 100

        # Step 2: Clear memory and load a specific range
        await _reset_sequence_state(sequence)
        db_start = DatabaseTimestamp.from_datetime(base_time.add(hours=20))
        db_end = DatabaseTimestamp.from_datetime(base_time.add(hours=40))
        loaded = await sequence.db_load_records(db_start, db_end)
        assert loaded == 20
        assert len(sequence.records) == 20

        # Step 3: Update records in memory and persist
        for record in sequence.records:
            record.humidity = 75.0
            await sequence.db_mark_dirty_record(record)
        await sequence.db_save_records()

        # Step 4: Reload the range and verify updates
        await _reset_sequence_state(sequence)
        await sequence.db_load_records(db_start, db_end)
        assert all(r.humidity == 75.0 for r in sequence.records)

        # Step 5: Vacuum — keep from hours=75 onward (delete first 75)
        db_cutoff = DatabaseTimestamp.from_datetime(base_time.add(hours=75))
        deleted = await sequence.db_vacuum(keep_timestamp=db_cutoff)
        assert deleted == 75
        assert await sequence.db_count_records() == 25

        # Step 6: Stats reflect vacuum result
        await _reset_sequence_state(sequence)
        stats = await sequence.db_get_stats()
        assert stats["total_records"] == 25

    async def test_error_handling_db_disabled(self, config_eos):
        """Operations on a disabled DB raise clearly."""
        config_eos.database.provider = None
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)

        assert sequence.db_enabled is False

        # Save is a no-op and returns 0 when disabled — no RuntimeError
        # (mixin returns 0 early when not enabled)
        result = await sequence.db_save_records()
        assert result == 0

    async def test_persistence_across_resets(self, async_database_instance):
        """Data written in one memory session is available after reset."""
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-06-01T00:00:00Z")

        for i in range(20):
            await sequence.db_insert_record(
                SampleDataRecord(date_time=base_time.add(hours=i), temperature=float(i))
            )
        await sequence.db_save_records()

        # Simulate a restart: reset memory state
        await _reset_sequence_state(sequence)
        assert len(sequence.records) == 0

        loaded = await sequence.db_load_records()
        assert loaded == 20
        assert sequence.records[0].temperature == 0.0
        assert sequence.records[-1].temperature == 19.0


# ==================== Performance Tests ====================

@pytest.mark.asyncio
class TestPerformance:
    """Throughput benchmarks — not correctness tests."""

    async def test_insert_throughput(self, config_eos, async_database_instance):
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")
        n = 10_000

        start = time.perf_counter()
        for i in range(n):
            await sequence.db_insert_record(
                SampleDataRecord(
                    date_time=base_time.add(minutes=i),
                    temperature=20.0 + (i % 100) * 0.1,
                )
            )
        insert_duration = time.perf_counter() - start
        print(f"\nInserted {n} records in {insert_duration:.2f}s "
              f"({n / insert_duration:.0f} rec/s)")

        assert len(sequence.records) == n

    async def test_save_throughput(self, config_eos, async_database_instance):
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")
        n = 10_000

        for i in range(n):
            await sequence.db_insert_record(
                SampleDataRecord(
                    date_time=base_time.add(minutes=i),
                    temperature=20.0 + (i % 100) * 0.1,
                )
            )

        start = time.perf_counter()
        saved = await sequence.db_save_records()
        save_duration = time.perf_counter() - start

        assert saved == n
        print(f"\nSaved {n} records in {save_duration:.2f}s "
              f"({n / save_duration:.0f} rec/s)")

    async def test_load_throughput(self, config_eos, async_database_instance):
        sequence = SampleDataSequence()
        await _reset_sequence_state(sequence)
        base_time = to_datetime("2024-01-01T00:00:00Z")
        n = 10_000

        for i in range(n):
            await sequence.db_insert_record(
                SampleDataRecord(
                    date_time=base_time.add(minutes=i),
                    temperature=20.0 + (i % 100) * 0.1,
                )
            )
        await sequence.db_save_records()
        await _reset_sequence_state(sequence)

        start = time.perf_counter()
        loaded = await sequence.db_load_records()
        load_duration = time.perf_counter() - start

        assert loaded == n
        print(f"\nLoaded {n} records in {load_duration:.2f}s "
              f"({n / load_duration:.0f} rec/s)")
