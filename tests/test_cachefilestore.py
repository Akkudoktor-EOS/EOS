"""Test Module for CacheFileStore Module."""

import io
import pickle
from datetime import date, datetime, time, timedelta
from time import sleep

import pytest

from akkudoktoreos.utils.cachefilestore import CacheFileStore, cache_in_file
from akkudoktoreos.utils.datetimeutil import to_datetime

# -----------------------------
# CacheFileStore
# -----------------------------


@pytest.fixture
def cache_store():
    """A pytest fixture that creates a new CacheFileStore instance for testing."""
    return CacheFileStore()


def test_generate_cache_file_key(cache_store):
    """Test cache file key generation based on URL and date."""
    key = "http://example.com"
    until_dt = to_datetime("2024-10-01").date()
    cache_file_key, cache_file_until_dt = cache_store._generate_cache_file_key(key, until_dt)
    assert cache_file_key is not None
    assert cache_file_until_dt == until_dt

    # Provide no until date - assure today EOD is used.
    until_dt = datetime.combine(date.today(), time.max)
    cache_file_key, cache_file_until_dt = cache_store._generate_cache_file_key(key, None)
    assert cache_file_until_dt == until_dt
    cache_file_key1, cache_file_until_dt1 = cache_store._generate_cache_file_key(key, until_dt)
    assert cache_file_key == cache_file_key1
    assert cache_file_until_dt == until_dt


def test_get_file_path(cache_store):
    """Test get file path from cache file object."""
    cache_file = cache_store.create("test_file", mode="w+", suffix=".txt")
    file_path = cache_store._get_file_path(cache_file)

    assert file_path is not None


def test_create_cache_file(cache_store):
    """Test the creation of a cache file and ensure it is stored correctly."""
    # Create a cache file for today's date
    cache_file = cache_store.create("test_file", mode="w+", suffix=".txt")

    # Check that the file exists in the store and is a file-like object
    assert cache_file is not None
    assert hasattr(cache_file, "name")
    assert cache_file.name.endswith(".txt")

    # Write some data to the file
    cache_file.seek(0)
    cache_file.write("Test data")
    cache_file.seek(0)  # Reset file pointer
    assert cache_file.read() == "Test data"


def test_get_cache_file(cache_store):
    """Test retrieving an existing cache file by key."""
    # Create a cache file and write data to it
    cache_file = cache_store.create("test_file", mode="w+")
    cache_file.seek(0)
    cache_file.write("Test data")
    cache_file.seek(0)

    # Retrieve the cache file and verify the data
    retrieved_file = cache_store.get("test_file")
    assert retrieved_file is not None
    retrieved_file.seek(0)
    assert retrieved_file.read() == "Test data"


def test_set_custom_file_object(cache_store):
    """Test setting a custom file-like object (BytesIO or StringIO) in the store."""
    # Create a BytesIO object and set it into the cache
    file_obj = io.BytesIO(b"Binary data")
    cache_store.set("binary_file", file_obj)

    # Retrieve the file from the store
    retrieved_file = cache_store.get("binary_file")
    assert isinstance(retrieved_file, io.BytesIO)
    retrieved_file.seek(0)
    assert retrieved_file.read() == b"Binary data"


def test_delete_cache_file(cache_store):
    """Test deleting a cache file from the store."""
    # Create multiple cache files
    cache_file1 = cache_store.create("file1")
    assert hasattr(cache_file1, "name")
    cache_file2 = cache_store.create("file2")
    assert hasattr(cache_file2, "name")

    # Ensure the files are in the store
    assert cache_store.get("file1") is cache_file1
    assert cache_store.get("file2") is cache_file2

    # Delete cache files
    cache_store.delete("file1")
    cache_store.delete("file2")

    # Ensure the store is empty
    assert cache_store.get("file1") is None
    assert cache_store.get("file2") is None


def test_clear_all_cache_files(cache_store):
    """Test clearing all cache files from the store."""
    # Create multiple cache files
    cache_file1 = cache_store.create("file1")
    assert hasattr(cache_file1, "name")
    cache_file2 = cache_store.create("file2")
    assert hasattr(cache_file2, "name")

    # Ensure the files are in the store
    assert cache_store.get("file1") is cache_file1
    assert cache_store.get("file2") is cache_file2

    # Clear all cache files
    cache_store.clear(clear_all=True)

    # Ensure the store is empty
    assert cache_store.get("file1") is None
    assert cache_store.get("file2") is None


def test_clear_cache_files_by_date(cache_store):
    """Test clearing cache files from the store by date."""
    # Create multiple cache files
    cache_file1 = cache_store.create("file1")
    assert hasattr(cache_file1, "name")
    cache_file2 = cache_store.create("file2")
    assert hasattr(cache_file2, "name")

    # Ensure the files are in the store
    assert cache_store.get("file1") is cache_file1
    assert cache_store.get("file2") is cache_file2

    # Clear cache files that are older than today
    cache_store.clear(before_datetime=datetime.combine(date.today(), time.min))

    # Ensure the files are in the store
    assert cache_store.get("file1") is cache_file1
    assert cache_store.get("file2") is cache_file2

    # Clear cache files that are older than tomorrow
    cache_store.clear(before_datetime=datetime.now() + timedelta(days=1))

    # Ensure the store is empty
    assert cache_store.get("file1") is None
    assert cache_store.get("file2") is None


def test_cache_file_with_date(cache_store):
    """Test creating and retrieving cache files with a specific date."""
    # Use a specific date for cache file creation
    specific_date = datetime(2023, 10, 10)
    cache_file = cache_store.create("dated_file", mode="w+", until_date=specific_date)

    # Write data to the cache file
    cache_file.write("Dated data")
    cache_file.seek(0)

    # Retrieve the cache file with the specific date
    retrieved_file = cache_store.get("dated_file", until_date=specific_date)
    assert retrieved_file is not None
    retrieved_file.seek(0)
    assert retrieved_file.read() == "Dated data"


def test_recreate_existing_cache_file(cache_store):
    """Test creating a cache file with an existing key does not overwrite the existing file."""
    # Create a cache file
    cache_file = cache_store.create("test_file", mode="w+")
    cache_file.write("Original data")
    cache_file.seek(0)

    # Attempt to recreate the same file (should return the existing one)
    new_file = cache_store.create("test_file")
    assert new_file is cache_file  # Should be the same object
    new_file.seek(0)
    assert new_file.read() == "Original data"  # Data should be preserved

    # Assure cache file store is a singleton
    cache_store2 = CacheFileStore()
    new_file = cache_store2.get("test_file")
    assert new_file is cache_file  # Should be the same object


def test_cache_store_is_singleton(cache_store):
    """Test re-creating a cache store provides the same store."""
    # Create a cache file
    cache_file = cache_store.create("test_file", mode="w+")
    cache_file.write("Original data")
    cache_file.seek(0)

    # Assure cache file store is a singleton
    cache_store2 = CacheFileStore()
    new_file = cache_store2.get("test_file")
    assert new_file is cache_file  # Should be the same object


def test_cache_in_file_decorator_caches_function_result(cache_store):
    """Test that the cache_in_file decorator caches a function result."""
    # Clear store to assure it is empty
    cache_store.clear(clear_all=True)
    assert len(cache_store._store) == 0

    # Define a simple function to decorate
    @cache_in_file(mode="w+")
    def my_function(until_date=None):
        return "Some expensive computation result"

    # Call the decorated function (should store result in cache)
    result = my_function(until_date=datetime.now() + timedelta(days=1))
    assert result == "Some expensive computation result"

    # Assert that the create method was called to store the result
    assert len(cache_store._store) == 1

    # Check if the result was written to the cache file
    key = next(iter(cache_store._store))
    cache_file = cache_store._store[key][0]
    assert cache_file is not None

    # Assert correct content was written to the file
    cache_file.seek(0)  # Move to the start of the file
    assert cache_file.read() == "Some expensive computation result"


def test_cache_in_file_decorator_uses_cache(cache_store):
    """Test that the cache_in_file decorator reuses cached file on subsequent calls."""
    # Clear store to assure it is empty
    cache_store.clear(clear_all=True)
    assert len(cache_store._store) == 0

    # Define a simple function to decorate
    @cache_in_file(mode="w+")
    def my_function(until_date=None):
        return "New result"

    # Call the decorated function (should store result in cache)
    result = my_function(until_date=datetime.now() + timedelta(days=1))
    assert result == "New result"

    # Assert result was written to cache file
    key = next(iter(cache_store._store))
    cache_file = cache_store._store[key][0]
    assert cache_file is not None
    cache_file.seek(0)  # Move to the start of the file
    assert cache_file.read() == result

    # Modify cache file
    result2 = "Cached result"
    cache_file.seek(0)
    cache_file.write(result2)

    # Call the decorated function again (should get result from cache)
    result = my_function(until_date=datetime.now() + timedelta(days=1))
    assert result == result2


def test_cache_in_file_decorator_forces_update(cache_store):
    """Test that the cache_in_file decorator reuses cached file on subsequent calls."""
    # Clear store to assure it is empty
    cache_store.clear(clear_all=True)
    assert len(cache_store._store) == 0

    # Define a simple function to decorate
    @cache_in_file(mode="w+")
    def my_function(until_date=None):
        return "New result"

    until_date = datetime.now() + timedelta(days=1)

    # Call the decorated function (should store result in cache)
    result1 = "New result"
    result = my_function(until_date=until_date)
    assert result == result1

    # Assert result was written to cache file
    key = next(iter(cache_store._store))
    cache_file = cache_store._store[key][0]
    assert cache_file is not None
    cache_file.seek(0)  # Move to the start of the file
    assert cache_file.read() == result

    # Modify cache file
    result2 = "Cached result"
    cache_file.seek(0)
    cache_file.write(result2)

    # Call the decorated function again with force update (should get result from function)
    result = my_function(until_date=until_date, force_update=True)  # type: ignore[call-arg]
    assert result == result1

    # Assure result was written to the same cache file
    cache_file.seek(0)  # Move to the start of the file
    assert cache_file.read() == result1


def test_cache_in_file_handles_ttl(cache_store):
    """Test that the cache_infile decorator handles the with_ttl parameter."""
    # Clear store to assure it is empty
    cache_store.clear(clear_all=True)
    assert len(cache_store._store) == 0

    # Define a simple function to decorate
    @cache_in_file(mode="w+")
    def my_function():
        return "New result"

    # Call the decorated function
    result = my_function(with_ttl="1 second")  # type: ignore[call-arg]

    # Overwrite cache file
    key = next(iter(cache_store._store))
    cache_file = cache_store._store[key][0]
    assert cache_file is not None
    cache_file.seek(0)  # Move to the start of the file
    cache_file.write("Modified result")
    cache_file.seek(0)  # Move to the start of the file
    assert cache_file.read() == "Modified result"

    result = my_function(with_ttl="1 second")  # type: ignore[call-arg]
    assert result == "Modified result"

    # Wait one second to let the cache time out
    sleep(1)

    # Call again - cache should be timed out
    result = my_function(with_ttl="1 second")  # type: ignore[call-arg]
    assert result == "New result"


def test_cache_in_file_handles_bytes_return(cache_store):
    """Test that the cache_infile decorator handles bytes returned from the function."""
    # Clear store to assure it is empty
    cache_store.clear(clear_all=True)
    assert len(cache_store._store) == 0

    # Define a function that returns bytes
    @cache_in_file()
    def my_function(until_date=None) -> bytes:
        return b"Some binary data"

    # Call the decorated function
    result = my_function(until_date=datetime.now() + timedelta(days=1))

    # Check if the binary data was written to the cache file
    key = next(iter(cache_store._store))
    cache_file = cache_store._store[key][0]
    assert len(cache_store._store) == 1
    assert cache_file is not None
    cache_file.seek(0)
    result1 = pickle.load(cache_file)
    assert result1 == result

    # Access cache
    result = my_function(until_date=datetime.now() + timedelta(days=1))
    assert len(cache_store._store) == 1
    assert cache_store._store[key][0] is not None
    assert result1 == result
