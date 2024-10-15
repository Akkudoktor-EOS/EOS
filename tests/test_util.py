"""Test Module for Utilities Module."""

import io
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from akkudoktoreos.util import CacheFileStore, to_datetime

# -----------------------------
# to_datetime
# -----------------------------


def test_to_datetime():
    """Test date conversion as needed by PV forecast data."""
    date_time = to_datetime(
        "2024-10-07T10:20:30.000+02:00", to_timezone="Europe/Berlin", to_naiv=False
    )
    expected_date_time = datetime(2024, 10, 7, 10, 20, 30, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    assert date_time == expected_date_time

    date_time = to_datetime(
        "2024-10-07T10:20:30.000+02:00", to_timezone="Europe/Berlin", to_naiv=True
    )
    expected_date_time = datetime(2024, 10, 7, 10, 20, 30, 0)
    assert date_time == expected_date_time

    date_time = to_datetime("2024-10-07", to_timezone="Europe/Berlin", to_naiv=False)
    expected_date_time = datetime(2024, 10, 7, 0, 0, 0, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    assert date_time == expected_date_time

    date_time = to_datetime("2024-10-07", to_timezone="Europe/Berlin", to_naiv=True)
    expected_date_time = datetime(2024, 10, 7, 0, 0, 0, 0)
    assert date_time == expected_date_time


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
    key_date = "2024-10-01"
    cache_file_key, cache_file_key_date = cache_store._generate_cache_file_key(key, key_date)
    expected_file_key = "0f6b92d1be8ef1e6a0b440de2963a7b847b54a8af267f2fab7f8756f30d733ac"
    assert cache_file_key == expected_file_key
    assert cache_file_key_date == key_date


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


def test_clear_cache_files(cache_store):
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
    cache_store.clear()

    # Ensure the store is empty
    assert cache_store.get("file1") is None
    assert cache_store.get("file2") is None


def test_cache_file_with_date(cache_store):
    """Test creating and retrieving cache files with a specific date."""
    # Use a specific date for cache file creation
    specific_date = datetime(2023, 10, 10)
    cache_file = cache_store.create("dated_file", key_date=specific_date)

    # Write data to the cache file
    cache_file.write("Dated data")
    cache_file.seek(0)

    # Retrieve the cache file with the specific date
    retrieved_file = cache_store.get("dated_file", key_date=specific_date)
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
