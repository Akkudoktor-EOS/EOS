"""Pytest tests for database persistence module.

Tests the abstract DataDB interface and concrete implementations (LMDB, SQLite).
Also tests the database integration with DataSequence/DataProvider classes.
"""

import pickle
import shutil
import tempfile
from pathlib import Path
from typing import Iterator, List, Type

import pytest
from pydantic import Field

from akkudoktoreos.core.coreabc import get_database
from akkudoktoreos.core.dataabc import (
    DataProvider,
    DataRecord,
    DataSequence,
)
from akkudoktoreos.core.datadb import (
    DataDB,
    LMDBDatabase,
    SQLiteDatabase,
)
from akkudoktoreos.utils.datetimeutil import to_datetime

# ==================== Test Fixtures ====================

@pytest.fixture
def temp_dir():
    """Create a temporary directory for test databases."""
    temp_path = Path(tempfile.mkdtemp())
    yield temp_path
    # Cleanup after test
    shutil.rmtree(temp_path, ignore_errors=True)


@pytest.fixture(params=["LMDB", "SQLite"])
def database_provider(request) -> str:
    """Parametrized fixture to test all database backends."""
    return request.param


@pytest.fixture
def database_instance(config_eos, database_provider: str, temp_dir: Path) -> Iterator[DataDB]:
    """Create a database instance for testing."""
    # Database is a singleton - ensure we get a new one
    #DataDB.reset_instance()
    #LMDBDatabase.reset_instance()
    #SQLiteDatabase.reset_instance()

    config_eos.database.compression_level = 6
    config_eos.database.provider = database_provider
    db = get_database()
    assert db.is_open is True
    assert db.provider_id() == config_eos.database.provider

    yield db

    # teardown
    config_eos.database.provider = None
    assert db.provider_id() == "None"



# ==================== Test Data Models ====================

class SampleDataRecord(DataRecord):
    """Test implementation of DataRecord."""
    temperature: float = Field(default=0.0)
    humidity: float = Field(default=0.0)
    pressure: float = Field(default=0.0)


class SampleDataSequence(DataSequence):
    """Test implementation of DataSequence with database support."""
    records: List[SampleDataRecord] = Field(default_factory=list)

    def db_namespace(self) -> str:
        return "SampleDataSequence"


class SampleDataProvider(DataProvider):
    """Test implementation of DataProvider with database support."""
    records: List[SampleDataRecord] = Field(default_factory=list)

    def provider_id(self) -> str:
        return "SampleDataProvider"

    def enabled(self) -> bool:
        return True

    def _update_data(self, force_update=False) -> None:
        pass


# ==================== Database Backend Tests ====================

class TestDataDB:
    """Test abstract DataDB interface and implementations."""

    def test_database_creation(self, config_eos, database_provider):
        """Test database creation and initialization."""
        config_eos.database.compression_level = 6
        config_eos.database.provider = database_provider
        db = get_database()

        assert db.storage_path == config_eos.general.data_folder_path / "db" / db._db.__class__.__name__.lower()
        assert db.compression is True
        assert db.compression_level == 6
        assert db.is_open is True

    def test_database_open_close(self, database_instance):
        """Test opening and closing database connection."""
        assert database_instance.is_open is True
        assert database_instance._db.connection is not None

        database_instance.close()
        assert database_instance._db.is_open is False

    def test_save_and_load_single_record(self, database_instance):
        """Test saving and loading a single record."""
        key = b'2024-01-01T00:00:00+00:00'
        value = b'test_data_12345'

        assert database_instance.is_open is True

        # Save
        database_instance.save_record(key, value)

        # Load
        records = list(database_instance.load_records(key, key + b'\xff'))
        assert len(records) == 1
        assert records[0] == (key, value)

    def test_save_multiple_records(self, database_instance):
        """Test saving and loading multiple records."""
        records_data = [
            (b'2024-01-01T00:00:00+00:00', b'data1'),
            (b'2024-01-02T00:00:00+00:00', b'data2'),
            (b'2024-01-03T00:00:00+00:00', b'data3'),
        ]

        # Save all
        for key, value in records_data:
            database_instance.save_record(key, value)

        # Load all
        loaded = list(database_instance.load_records())
        assert len(loaded) == len(records_data)

        for (expected_key, expected_value), (loaded_key, loaded_value) in zip(records_data, loaded):
            assert loaded_key == expected_key
            assert loaded_value == expected_value

    def test_load_records_with_range(self, database_instance):
        """Test loading records within a specific key range."""
        records_data = [
            (b'2024-01-01T00:00:00+00:00', b'data1'),
            (b'2024-01-02T00:00:00+00:00', b'data2'),
            (b'2024-01-03T00:00:00+00:00', b'data3'),
            (b'2024-01-04T00:00:00+00:00', b'data4'),
            (b'2024-01-05T00:00:00+00:00', b'data5'),
        ]

        for key, value in records_data:
            database_instance.save_record(key, value)

        # Load range [2024-01-02, 2024-01-04)
        start_key = b'2024-01-02T00:00:00+00:00'
        end_key = b'2024-01-04T00:00:00+00:00'
        loaded = list(database_instance.load_records(start_key, end_key))

        assert len(loaded) == 2
        assert loaded[0][0] == b'2024-01-02T00:00:00+00:00'
        assert loaded[1][0] == b'2024-01-03T00:00:00+00:00'

    def test_delete_record(self, database_instance):
        """Test deleting a record."""
        key = b'2024-01-01T00:00:00+00:00'
        value = b'test_data'

        # Save
        database_instance.save_record(key, value)
        assert database_instance.count_records() == 1

        # Delete
        result = database_instance.delete_record(key)
        assert result is True
        assert database_instance.count_records() == 0

        # Try to delete non-existent record
        result = database_instance.delete_record(key)
        assert result is False

    def test_count_records(self, database_instance):
        """Test counting records."""
        assert database_instance.count_records() == 0

        # Add records
        for i in range(10):
            key = f'2024-01-{i+1:02d}T00:00:00+00:00'.encode()
            database_instance.save_record(key, b'data')

        assert database_instance.count_records() == 10

    def test_get_key_range(self, database_instance):
        """Test getting min and max keys."""
        # Empty database
        min_key, max_key = database_instance.get_key_range()
        assert min_key is None
        assert max_key is None

        # Add records
        keys = [
            b'2024-01-01T00:00:00+00:00',
            b'2024-01-05T00:00:00+00:00',
            b'2024-01-03T00:00:00+00:00',
        ]
        for key in keys:
            database_instance.save_record(key, b'data')

        min_key, max_key = database_instance.get_key_range()
        assert min_key == b'2024-01-01T00:00:00+00:00'
        assert max_key == b'2024-01-05T00:00:00+00:00'

    def test_iterate_records_forward(self, database_instance):
        """Test iterating records in forward order."""
        keys = [
            b'2024-01-01T00:00:00+00:00',
            b'2024-01-02T00:00:00+00:00',
            b'2024-01-03T00:00:00+00:00',
        ]
        for key in keys:
            database_instance.save_record(key, b'data')

        result = list(database_instance.iterate_records())
        result_keys = [k for k, v in result]

        assert result_keys == keys

    def test_iterate_records_reverse(self, database_instance):
        """Test iterating records in reverse order."""
        keys = [
            b'2024-01-01T00:00:00+00:00',
            b'2024-01-02T00:00:00+00:00',
            b'2024-01-03T00:00:00+00:00',
        ]
        for key in keys:
            database_instance.save_record(key, b'data')

        result = list(database_instance.iterate_records(reverse=True))
        result_keys = [k for k, v in result]

        assert result_keys == list(reversed(keys))

    def test_compression(self, config_eos, database_provider, temp_dir):
        """Test data compression."""

        # Large repetitive data (highly compressible)
        large_data = b'A' * 10000

        config_eos.database.provider = database_provider

        # Database with compression
        config_eos.database.compression_level = 9
        serialized_compressed = get_database().serialize_data(large_data)
        deserialized_compressed = get_database().deserialize_data(serialized_compressed)

        # Database without compression
        config_eos.database.compression_level = 0
        serialized_uncompressed = get_database().serialize_data(large_data)
        deserialized_uncompressed = get_database().deserialize_data(serialized_uncompressed)

        # Compressed should be smaller
        assert len(serialized_compressed) < len(serialized_uncompressed)

        # Verify decompression works
        assert deserialized_compressed == large_data
        assert deserialized_uncompressed == large_data

    def test_flush(self, database_instance):
        """Test flushing writes to storage."""
        key = b'2024-01-01T00:00:00+00:00'
        value = b'test_data'

        database_instance.save_record(key, value)
        database_instance.flush()

        # Data should be persisted
        loaded = list(database_instance.load_records())
        assert len(loaded) == 1
        assert loaded[0] == (key, value)

    def test_backend_stats(self, database_instance):
        """Test getting backend-specific statistics."""
        stats = database_instance.get_backend_stats()

        assert 'backend' in stats
        assert isinstance(stats, dict)

        # Add some data
        for i in range(10):
            key = f'2024-01-{i+1:02d}T00:00:00+00:00'.encode()
            database_instance.save_record(key, b'data' * 100)

        stats = database_instance.get_backend_stats()
        assert stats is not None


# ==================== DataSequence Database Protocol Tests ====================

class TestDataSequenceDatabaseProtocol:
    """Test database integration with DataSequence."""

    def test_load(self, database_instance):
        """Test initializing database with a sequence."""
        assert database_instance.is_open == True
        assert database_instance == get_database()

        sequence = SampleDataSequence()

        sequence.load()

        assert sequence.db_enabled is True
        assert sequence.database == database_instance

    def test_save_and_load_records(self, database_instance, temp_dir):
        """Test saving and loading data records."""
        sequence = SampleDataSequence()
        sequence.load()

        # Create test records
        base_time = to_datetime('2024-01-01T00:00:00Z')
        for i in range(10):
            record = SampleDataRecord(
                date_time=base_time.add(hours=i),
                temperature=20.0 + i,
                humidity=60.0 + i,
                pressure=1013.0 + i
            )
            sequence.records.append(record)

        # Save to database
        saved_count = sequence.db_save_records(clear_memory=False)
        assert saved_count == 10

        # Clear memory
        sequence.records.clear()
        assert len(sequence.records) == 0

        # Load from database
        loaded_count = sequence.db_load_records()
        assert loaded_count == 10
        assert len(sequence.records) == 10

        # Verify data
        for i, record in enumerate(sequence.records):
            assert record.temperature == 20.0 + i
            assert record.humidity == 60.0 + i
            assert record.pressure == 1013.0 + i

    def test_save_with_datetime_range(self, database_instance):
        """Test saving only specific datetime range."""
        sequence = SampleDataSequence()
        sequence.load()

        # Create records
        base_time = to_datetime('2024-01-01T00:00:00Z')
        for i in range(10):
            record = SampleDataRecord(
                date_time=base_time.add(hours=i),
                temperature=20.0 + i
            )
            sequence.records.append(record)

        # Save only middle range
        start_dt = base_time.add(hours=3)
        end_dt = base_time.add(hours=7)
        saved_count = sequence.db_save_records(
            clear_memory=False,
            start_datetime=start_dt,
            end_datetime=end_dt
        )

        assert saved_count == 4  # Hours 3, 4, 5, 6
        assert sequence.db_count_records() == 4

    def test_load_with_datetime_range(self, database_instance):
        """Test loading only specific datetime range."""
        sequence = SampleDataSequence()
        sequence.load()

        # Create and save records
        base_time = to_datetime('2024-01-01T00:00:00Z')
        for i in range(10):
            record = SampleDataRecord(
                date_time=base_time.add(hours=i),
                temperature=20.0 + i
            )
            sequence.records.append(record)

        saved_count = sequence.db_save_records(clear_memory=True)

        assert saved_count == 10
        assert sequence.db_count_records() == saved_count

        # Load only middle range
        start_dt = base_time.add(hours=3)
        end_dt = base_time.add(hours=7)
        loaded_count = sequence.db_load_records(
            start_datetime=start_dt,
            end_datetime=end_dt,
            merge=False
        )

        assert loaded_count == 4
        assert len(sequence.records) == 4
        assert sequence.records[0].temperature == 23.0  # Hour 3
        assert sequence.records[-1].temperature == 26.0  # Hour 6

    def test_db_ensure_loaded_lazy_loading(self, database_instance):
        """Test lazy loading with db_ensure_loaded."""
        sequence = SampleDataSequence()
        sequence.load()

        # Create and save records
        base_time = to_datetime('2024-01-01T00:00:00Z')
        for i in range(20):
            record = SampleDataRecord(
                date_time=base_time.add(hours=i),
                temperature=20.0 + i
            )
            sequence.records.append(record)
        sequence.db_save_records(clear_memory=True)

        # Ensure loaded should load data
        start_dt = base_time.add(hours=5)
        end_dt = base_time.add(hours=15)
        sequence.db_ensure_loaded(start_dt, end_dt)

        assert len(sequence.records) == 10

        # Second call with same range should not reload
        initial_records = sequence.records.copy()
        sequence.db_ensure_loaded(start_dt, end_dt)
        assert sequence.records == initial_records

    def test_auto_save_trigger(self, config_eos, database_instance):
        """Test auto-save when memory threshold is exceeded."""
        config_eos.database.max_records_in_memory = 10
        config_eos.database.auto_save = True
        sequence = SampleDataSequence()
        sequence.load()

        # Add records beyond threshold
        base_time = to_datetime('2024-01-01T00:00:00Z')
        for i in range(15):
            record = SampleDataRecord(
                date_time=base_time.add(hours=i),
                temperature=20.0 + i
            )
            sequence.insert_by_datetime(record)

        # Should have auto-saved older records
        # Recent records kept in memory
        assert len(sequence.records) <= 10

        # Total records in database should include saved ones
        total_in_db = sequence.db_count_records()
        assert total_in_db > 0

    def test_db_save_records_batch(self, database_instance):
        """Test batch saving records."""
        sequence = SampleDataSequence()
        sequence.load()

        # Create records
        base_time = to_datetime('2024-01-01T00:00:00Z')
        records_to_save = []
        for i in range(5):
            record = SampleDataRecord(
                date_time=base_time.add(hours=i),
                temperature=20.0 + i
            )
            records_to_save.append(record)
            sequence.records.append(record)

        # Batch save
        saved_count = sequence.db_save_records_batch(records_to_save, clear_memory=True)

        assert saved_count == 5
        assert len(sequence.records) == 0
        assert sequence.db_count_records() == 5

    def test_db_vacuum(self, database_instance):
        """Test vacuuming old records."""
        sequence = SampleDataSequence()
        sequence.load()

        # Create records over 10 days
        base_time = to_datetime('2024-01-01T00:00:00Z')
        for i in range(240):  # 10 days * 24 hours
            record = SampleDataRecord(
                date_time=base_time.add(hours=i),
                temperature=20.0
            )
            sequence.records.append(record)
        sequence.db_save_records(clear_memory=True)

        assert sequence.db_count_records() == 240

        # Vacuum: keep only last 5 days
        deleted = sequence.db_vacuum(keep_hours=5*24)

        assert deleted == 120  # 5 days * 24 hours
        assert sequence.db_count_records() == 120

    def test_db_datetime_range(self, database_instance):
        """Test getting datetime range from database."""
        sequence = SampleDataSequence()
        sequence.load()

        # Empty database
        min_dt, max_dt = sequence.db_datetime_range()
        assert min_dt is None
        assert max_dt is None

        # Add records
        base_time = to_datetime('2024-01-01T00:00:00Z')
        for i in [0, 5, 10]:
            record = SampleDataRecord(
                date_time=base_time.add(hours=i),
                temperature=20.0
            )
            sequence.records.append(record)
        sequence.db_save_records(clear_memory=True)

        # Check range
        min_dt, max_dt = sequence.db_datetime_range()
        assert min_dt == base_time
        assert max_dt == base_time.add(hours=10)

    def test_db_get_stats(self, database_instance):
        """Test getting database statistics."""
        sequence = SampleDataSequence()
        sequence.load()

        stats = sequence.db_get_stats()

        assert stats['enabled'] is True
        assert 'backend' in stats
        assert 'path' in stats
        assert 'memory_records' in stats
        assert 'total_records' in stats
        assert 'compression_enabled' in stats
        assert 'datetime_range' in stats

    def test_db_flush(self, database_instance):
        """Test flushing database writes."""
        sequence = SampleDataSequence()
        assert sequence.load() == True
        assert sequence.db_count_records() == 0

        # Add record
        record = SampleDataRecord(
            date_time=to_datetime('2024-01-01T00:00:00Z'),
            temperature=20.0
        )
        sequence.records.append(record)
        sequence.db_save_records(clear_memory=False)

        # Flush
        sequence.db_flush()

        # Should be persisted
        assert sequence.db_count_records() == 1


# ==================== Backend-Specific Tests ====================

class TestLMDBDatabase:
    """Test LMDB-specific features."""

    def test_lmdb_compact(self, config_eos):
        """Test LMDB compaction."""
        config_eos.database.compression_level = 0
        db = LMDBDatabase()
        db.open()

        # Add lots of data
        for i in range(1000):
            key = f'2024-01-01T{i:06d}:00:00+00:00'.encode()
            value = b'X' * 1000
            db.save_record(key, value)

        # Delete half
        for i in range(500):
            key = f'2024-01-01T{i:06d}:00:00+00:00'.encode()
            db.delete_record(key)

        # Get stats before compaction
        stats_before = db.get_backend_stats()

        # Compact
        db.compact()

        # Get stats after compaction
        stats_after = db.get_backend_stats()

        # Should still have 500 records
        assert db.count_records() == 500

        db.close()


class TestSQLiteDatabase:
    """Test SQLite-specific features."""

    def test_sqlite_vacuum(self, config_eos):
        """Test SQLite vacuum."""
        config_eos.database.compression_level = 0
        db = SQLiteDatabase()
        db.open()

        # Add data
        for i in range(100):
            key = f'2024-01-{i+1:02d}T00:00:00+00:00'.encode()
            value = b'data' * 100
            db.save_record(key, value)

        # Delete some
        for i in range(50):
            key = f'2024-01-{i+1:02d}T00:00:00+00:00'.encode()
            db.delete_record(key)

        # Vacuum
        db.vacuum()

        # Should still have 50 records
        assert db.count_records() == 50

        db.close()


# ==================== Integration Tests ====================

class TestIntegration:
    """Integration tests with full workflow."""

    def test_full_workflow(self, config_eos, database_instance):
        """Test complete workflow: save, load, update, vacuum."""
        config_eos.database.max_records_in_memory=50
        config_eos.database.auto_save=True
        sequence = SampleDataSequence()
        sequence.load()

        # Step 1: Add initial data
        base_time = to_datetime('2024-01-01T00:00:00Z')
        for i in range(100):
            record = SampleDataRecord(
                date_time=base_time.add(hours=i),
                temperature=20.0 + i * 0.1,
                humidity=60.0
            )
            sequence.insert_by_datetime(record)

        # Step 2: Verify auto-save happened
        assert len(sequence.records) == 50
        assert sequence.db_count_records() == 50

        # Step 3: Load specific range
        sequence.records.clear()
        start_dt = base_time.add(hours=20)
        end_dt = base_time.add(hours=40)
        sequence.db_load_records(start_dt, end_dt, merge=False)

        assert len(sequence.records) == 20
        assert sequence.db_count_records() == 50

        # Step 4: Update records
        for record in sequence.records:
            record.humidity = 70.0
        sequence.db_save_records(clear_memory=False)

        # Step 5: Verify updates
        sequence.records.clear()
        sequence.db_load_records(start_dt, end_dt, merge=False)

        for record in sequence.records:
            assert record.humidity == 70.0
        assert len(sequence.records) == 20
        assert sequence.db_count_records() == 50

        # Step 6: Vacuum old data
        cutoff = base_time.add(hours=25)
        deleted = sequence.db_vacuum(keep_datetime=cutoff)
        assert deleted == 25

        # Step 7: Verify remaining data
        remaining = sequence.db_count_records()
        assert remaining == 25

        # Step 8: Get statistics
        stats = sequence.db_get_stats()
        assert stats['total_records'] == 25

    def test_error_handling(self, config_eos):
        """Test error handling in various scenarios."""
        config_eos.database.provider = None
        sequence = SampleDataSequence()

        assert sequence.load() == False

        # Error: Using database before initialization
        with pytest.raises(RuntimeError, match="not enabled"):
            sequence.db_save_records()

        # Initialize properly
        config_eos.database.provider = "LMDB"
        assert sequence.load() == True

        # Error: Invalid vacuum parameters
        with pytest.raises(ValueError, match="Must specify either"):
            sequence.db_vacuum()

        sequence.database.close()


# ==================== Performance Tests ====================

class TestPerformance:
    """Performance and stress tests."""

    def test_large_dataset(self, database_instance):
        """Test handling large datasets."""
        sequence = SampleDataSequence()
        sequence.load()

        # Add 10,000 records
        base_time = to_datetime('2024-01-01T00:00:00Z')
        for i in range(10000):
            record = SampleDataRecord(
                date_time=base_time.add(minutes=i),
                temperature=20.0 + (i % 100) * 0.1
            )
            sequence.records.append(record)

        # Save all
        import time
        start = time.time()
        saved = sequence.db_save_records(clear_memory=True)
        duration = time.time() - start

        assert saved == 10000
        print(f"\nSaved 10,000 records in {duration:.2f}s ({saved/duration:.0f} records/sec)")

        # Load all
        start = time.time()
        loaded = sequence.db_load_records()
        duration = time.time() - start

        assert loaded == 10000
        print(f"Loaded 10,000 records in {duration:.2f}s ({loaded/duration:.0f} records/sec)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
