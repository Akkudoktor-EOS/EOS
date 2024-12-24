"""Test Module for CacheFileStore Module."""

import io
import pickle
from datetime import date, datetime, timedelta
from time import sleep

import pytest

from akkudoktoreos.utils.cacheutil import CacheFileStore, cache_in_file
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime, to_duration

# -----------------------------
# CacheFileStore
# -----------------------------


@pytest.fixture
def cache_store():
    """A pytest fixture that creates a new CacheFileStore instance for testing."""
    cache = CacheFileStore()
    cache.clear(clear_all=True)
    assert len(cache._store) == 0
    return cache


def test_generate_cache_file_key(cache_store):
    """Test cache file key generation based on URL and date."""
    key = "http://example.com"

    # Provide until date - assure until_dt is used.
    until_dt = to_datetime("2024-10-01")
    cache_file_key, cache_file_until_dt, ttl_duration = cache_store._generate_cache_file_key(
        key=key, until_datetime=until_dt
    )
    assert cache_file_key is not None
    assert compare_datetimes(cache_file_until_dt, until_dt).equal

    # Provide until date again - assure same key is generated.
    cache_file_key1, cache_file_until_dt1, ttl_duration1 = cache_store._generate_cache_file_key(
        key=key, until_datetime=until_dt
    )
    assert cache_file_key1 == cache_file_key
    assert compare_datetimes(cache_file_until_dt1, until_dt).equal

    # Provide no until date - assure today EOD is used.
    no_until_dt = to_datetime().end_of("day")
    cache_file_key, cache_file_until_dt, ttl_duration = cache_store._generate_cache_file_key(key)
    assert cache_file_key is not None
    assert compare_datetimes(cache_file_until_dt, no_until_dt).equal

    # Provide with_ttl - assure until_dt is used.
    until_dt = to_datetime().add(hours=1)
    cache_file_key, cache_file_until_dt, ttl_duration = cache_store._generate_cache_file_key(
        key, with_ttl="1 hour"
    )
    assert cache_file_key is not None
    assert compare_datetimes(cache_file_until_dt, until_dt).approximately_equal
    assert ttl_duration == to_duration("1 hour")

    # Provide with_ttl again - assure same key is generated.
    until_dt = to_datetime().add(hours=1)
    cache_file_key1, cache_file_until_dt1, ttl_duration1 = cache_store._generate_cache_file_key(
        key=key, with_ttl="1 hour"
    )
    assert cache_file_key1 == cache_file_key
    assert compare_datetimes(cache_file_until_dt1, until_dt).approximately_equal
    assert ttl_duration1 == to_duration("1 hour")

    # Provide different with_ttl - assure different key is generated.
    until_dt = to_datetime().add(hours=1, minutes=1)
    cache_file_key2, cache_file_until_dt2, ttl_duration2 = cache_store._generate_cache_file_key(
        key=key, with_ttl="1 hour 1 minute"
    )
    assert cache_file_key2 != cache_file_key
    assert compare_datetimes(cache_file_until_dt2, until_dt).approximately_equal
    assert ttl_duration2 == to_duration("1 hour 1 minute")


def test_get_file_path(cache_store):
    """Test get file path from cache file object."""
    cache_file = cache_store.create("test_file", mode="w+", suffix=".txt")
    file_path = cache_store._get_file_path(cache_file)

    assert file_path is not None


def test_until_datetime_by_options(cache_store):
    """Test until datetime calculation based on options."""
    now = to_datetime()

    # Test with until_datetime
    result, ttl_duration = cache_store._until_datetime_by_options(until_datetime=now)
    assert result == now
    assert ttl_duration is None

    # -- From now on we expect a until_datetime in one hour
    ttl_duration_expected = to_duration("1 hour")

    # Test with with_ttl as timedelta
    until_datetime_expected = to_datetime().add(hours=1)
    ttl = timedelta(hours=1)
    result, ttl_duration = cache_store._until_datetime_by_options(with_ttl=ttl)
    assert compare_datetimes(result, until_datetime_expected).approximately_equal
    assert ttl_duration == ttl_duration_expected

    # Test with with_ttl as int (seconds)
    until_datetime_expected = to_datetime().add(hours=1)
    ttl_seconds = 3600
    result, ttl_duration = cache_store._until_datetime_by_options(with_ttl=ttl_seconds)
    assert compare_datetimes(result, until_datetime_expected).approximately_equal
    assert ttl_duration == ttl_duration_expected

    # Test with with_ttl as string ("1 hour")
    until_datetime_expected = to_datetime().add(hours=1)
    ttl_string = "1 hour"
    result, ttl_duration = cache_store._until_datetime_by_options(with_ttl=ttl_string)
    assert compare_datetimes(result, until_datetime_expected).approximately_equal
    assert ttl_duration == ttl_duration_expected

    # -- From now on we expect a until_datetime today at end of day
    until_datetime_expected = to_datetime().end_of("day")
    ttl_duration_expected = None

    # Test default case (end of today)
    result, ttl_duration = cache_store._until_datetime_by_options()
    assert compare_datetimes(result, until_datetime_expected).equal
    assert ttl_duration == ttl_duration_expected

    # -- From now on we expect a until_datetime in one day at end of day
    until_datetime_expected = to_datetime().add(days=1).end_of("day")
    assert ttl_duration == ttl_duration_expected

    # Test with until_date as date
    until_date = date.today() + timedelta(days=1)
    result, ttl_duration = cache_store._until_datetime_by_options(until_date=until_date)
    assert compare_datetimes(result, until_datetime_expected).equal
    assert ttl_duration == ttl_duration_expected

    # -- Test with multiple options (until_datetime takes precedence)
    specific_datetime = to_datetime().add(days=2)
    result, ttl_duration = cache_store._until_datetime_by_options(
        until_date=to_datetime().add(days=1).date(),
        until_datetime=specific_datetime,
        with_ttl=ttl,
    )
    assert compare_datetimes(result, specific_datetime).equal
    assert ttl_duration is None

    # Test with invalid inputs
    with pytest.raises(ValueError):
        cache_store._until_datetime_by_options(until_date="invalid-date")
    with pytest.raises(ValueError):
        cache_store._until_datetime_by_options(with_ttl="invalid-ttl")
    with pytest.raises(ValueError):
        cache_store._until_datetime_by_options(until_datetime="invalid-datetime")


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
    cache_store.clear(before_datetime=to_datetime().start_of("day"))

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
    cache_file = cache_store._store[key].cache_file
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
    result = my_function(until_date=to_datetime().add(days=1))
    assert result == "New result"

    # Assert result was written to cache file
    key = next(iter(cache_store._store))
    cache_file = cache_store._store[key].cache_file
    assert cache_file is not None
    cache_file.seek(0)  # Move to the start of the file
    assert cache_file.read() == result

    # Modify cache file
    result2 = "Cached result"
    cache_file.seek(0)
    cache_file.write(result2)

    # Call the decorated function again (should get result from cache)
    result = my_function(until_date=to_datetime().add(days=1))
    assert result == result2


def test_cache_in_file_decorator_forces_update_data(cache_store):
    """Test that the cache_in_file decorator reuses cached file on subsequent calls."""
    # Clear store to assure it is empty
    cache_store.clear(clear_all=True)
    assert len(cache_store._store) == 0

    # Define a simple function to decorate
    @cache_in_file(mode="w+")
    def my_function(until_date=None):
        return "New result"

    until_date = to_datetime().add(days=1).date()

    # Call the decorated function (should store result in cache)
    result1 = "New result"
    result = my_function(until_date=until_date)
    assert result == result1

    # Assert result was written to cache file
    key = next(iter(cache_store._store))
    cache_file = cache_store._store[key].cache_file
    assert cache_file is not None
    cache_file.seek(0)  # Move to the start of the file
    assert cache_file.read() == result

    # Modify cache file
    result2 = "Cached result"
    cache_file.seek(0)
    cache_file.write(result2)
    cache_file.seek(0)  # Move to the start of the file
    assert cache_file.read() == result2

    # Call the decorated function again with force update (should get result from function)
    result = my_function(until_date=until_date, force_update=True)  # type: ignore[call-arg]
    assert result == result1

    # Assure result was written to the same cache file
    cache_file.seek(0)  # Move to the start of the file
    assert cache_file.read() == result1


def test_cache_in_file_handles_ttl(cache_store):
    """Test that the cache_infile decorator handles the with_ttl parameter."""

    # Define a simple function to decorate
    @cache_in_file(mode="w+")
    def my_function():
        return "New result"

    # Call the decorated function
    result1 = my_function(with_ttl="1 second")  # type: ignore[call-arg]
    assert result1 == "New result"
    assert len(cache_store._store) == 1
    key = list(cache_store._store.keys())[0]

    # Assert result was written to cache file
    key = next(iter(cache_store._store))
    cache_file = cache_store._store[key].cache_file
    assert cache_file is not None
    cache_file.seek(0)  # Move to the start of the file
    assert cache_file.read() == result1

    # Modify cache file
    result2 = "Cached result"
    cache_file.seek(0)
    cache_file.write(result2)
    cache_file.seek(0)  # Move to the start of the file
    assert cache_file.read() == result2

    # Call the decorated function again
    result = my_function(with_ttl="1 second")  # type: ignore[call-arg]
    cache_file.seek(0)  # Move to the start of the file
    assert cache_file.read() == result2
    assert result == result2

    # Wait one second to let the cache time out
    sleep(2)

    # Call again - cache should be timed out
    result = my_function(with_ttl="1 second")  # type: ignore[call-arg]
    assert result == result1


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
    cache_file = cache_store._store[key].cache_file
    assert len(cache_store._store) == 1
    assert cache_file is not None
    cache_file.seek(0)
    result1 = pickle.load(cache_file)
    assert result1 == result

    # Access cache
    result = my_function(until_date=datetime.now() + timedelta(days=1))
    assert len(cache_store._store) == 1
    assert cache_store._store[key].cache_file is not None
    assert result1 == result
