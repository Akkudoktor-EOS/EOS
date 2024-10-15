"""cachefilestore.py.

This module provides a class for in-memory managing of cache files.

The `CacheFileStore` class is a singleton-based, thread-safe key-value store for managing
temporary file objects, allowing the creation, retrieval, and management of cache files.

Classes:
--------
- CacheFileStore: A thread-safe, singleton class for in-memory managing of file-like cache objects.
- CacheFileStoreMeta: Metaclass for enforcing the singleton behavior in `CacheFileStore`.

Example usage:
--------------
    # CacheFileStore usage
    >>> cache_store = CacheFileStore()
    >>> cache_store.create('example_key')
    >>> cache_file = cache_store.get('example_key')
    >>> cache_file.write('Some data')
    >>> cache_file.seek(0)
    >>> print(cache_file.read())  # Output: 'Some data'

Notes:
------
- Cache files are automatically associated with the current date unless specified.
"""

import hashlib
import inspect
import os
import pickle
import tempfile
import threading
from datetime import date, datetime
from typing import List, Optional, Union

from akkudoktoreos.util import get_logger, to_datetime

logger = get_logger(__file__)


class CacheFileStoreMeta(type):
    """A thread-safe implementation of CacheFileStore."""

    _instances = {}

    _lock: threading.Lock = threading.Lock()
    """Lock object to synchronize threads on first access to CacheFileStore."""

    def __call__(cls):
        """Return CacheFileStore instance."""
        with cls._lock:
            if cls not in cls._instances:
                instance = super().__call__()
                cls._instances[cls] = instance
        return cls._instances[cls]


class CacheFileStore(metaclass=CacheFileStoreMeta):
    """A key-value store that manages file-like tempfile objects to be used as cache files.

    Cache files are associated with a date. If no date is specified, the cache files are
    associated with the current date by default. The class provides methods to create
    new cache files, retrieve existing ones, delete specific files, and clear all cache
    entries.

    CacheFileStore is a thread-safe singleton. Only one store instance will ever be created.

    Attributes:
        store (dict): A dictionary that holds the in-memory cache file objects
                      with their associated keys and dates.

    Example usage:
        >>> cache_store = CacheFileStore()
        >>> cache_store.create('example_file')
        >>> cache_file = cache_store.get('example_file')
        >>> cache_file.write('Some data')
        >>> cache_file.seek(0)
        >>> print(cache_file.read())  # Output: 'Some data'
    """

    def __init__(self):
        """Initializes the CacheFileStore instance.

        This constructor sets up an empty key-value store (a dictionary) where each key
        corresponds to a cache file that is associated with a given key and an optional date.
        """
        self._store = {}
        self._store_lock = threading.Lock()

    def __del__(self):
        """Clear the store on store deletion."""
        self.clear(clear_all=True)

    def _generate_cache_file_key(
        self, key: str, valid_until: Union[datetime, date, str, int, float, None]
    ) -> (str, datetime):
        """Generates a unique cache file key based on the key and date.

        The cache file key is a combination of the input key and the date (if provided),
        hashed using SHA-256 to ensure uniqueness.

        Args:
            key (str): The key that identifies the cache file.
            valid_until (Union[datetime, date, str, int, float, None]): The date to associate with
                the cache file. If None is provided, the current date is used.

        Returns:
            str: A hashed string that serves as the unique identifier for the cache file.
            date: The date until the the cache file valid.
        """
        if valid_until is None:
            valid_until = date.today()
        valid_until = to_datetime(valid_until, as_string="%Y-%m-%d")
        cache_key = hashlib.sha256(f"{key}{valid_until}".encode("utf-8")).hexdigest()
        return (f"{cache_key}", to_datetime(valid_until).date())

    def _get_file_path(self, file_obj):
        """Retrieve the file path from a file-like object.

        Args:
            file_obj: A file-like object (e.g., an instance of
                NamedTemporaryFile, BytesIO, StringIO) from which to retrieve the
                file path.

        Returns:
            str or None: The file path if available, or None if the file-like
            object does not provide a file path.
        """
        file_path = None
        if hasattr(file_obj, "name"):
            file_path = file_obj.name  # Get the file path from the cache file object
        return file_path

    def _search(self, key: str, valid_at: Union[datetime, date, str, int, float]):
        valid_at = to_datetime(valid_at).date()

        for cache_file_key, cache_item in self._store.items():
            if valid_at <= cache_item[1]:
                # This cache file fits to the given date
                generated_key, _valid_until = self._generate_cache_file_key(key, cache_item[1])
                if generated_key == cache_file_key:
                    # Also the key fits
                    return cache_item
        return None

    def create(
        self,
        key: str,
        valid_until: Union[datetime, date, str, int, float, None] = None,
        mode: str = "wb+",
        delete: bool = False,
        suffix: Optional[str] = None,
    ):
        """Creates a new file-like tempfile object associated with the given key.

        If a cache file with the given key already exists, the existing file is returned.
        Otherwise, a new tempfile object is created and stored in the key-value store.

        Args:
            key (str): The key to store the cache file under.
            valid_until (Union[datetime, date, str, int, float, None], optional): The date to
                associate with the cache file. Defaults to the current date if None is provided.
            mode (str, optional): The mode in which the tempfile is opened
                (e.g., 'w+', 'r+', 'wb+'). Defaults to 'wb+'.
            delete (bool, optional): Whether to delete the file after it is closed.
                Defaults to False (keeps the file).
            suffix (str, optional): The suffix for the cache file (e.g., '.txt', '.log').
                Defaults to None.

        Returns:
            file_obj: A file-like object representing the cache file.

        Example:
            >>> cache_file = cache_store.create('example_file', suffix='.txt')
            >>> cache_file.write('Some cached data')
            >>> cache_file.seek(0)
            >>> print(cache_file.read())  # Output: 'Some cached data'
        """
        cache_file_key, valid_until = self._generate_cache_file_key(key, valid_until)
        with self._store_lock:  # Synchronize access to _store
            if cache_file_key in self._store:
                # File already available
                cache_file_obj, valid_until = self._store.get(cache_file_key)
            else:
                cache_file_obj = tempfile.NamedTemporaryFile(
                    mode=mode, delete=delete, suffix=suffix
                )
                self._store[cache_file_key] = (cache_file_obj, valid_until)
            cache_file_obj.seek(0)
            return cache_file_obj

    def set(
        self, key: str, file_obj, valid_until: Union[datetime, date, str, int, float, None] = None
    ):
        """Stores a file-like object in the cache under the specified key and date.

        This method allows you to manually set a file-like object into the cache with a specific key
        and optional date.

        Args:
            key (str): The key to store the file object under.
            file_obj: The file-like object.
            valid_until (Union[datetime, date, str, int, float, None], optional): The date to associate
                with the cache file. Defaults to the current date if None is provided.

        Raises:
            ValueError: If the key is already in store.

        Example:
            >>> cache_store.set('example_file', io.BytesIO(b'Some binary data'))
        """
        cache_file_key, valid_until = self._generate_cache_file_key(key, valid_until)
        with self._store_lock:  # Synchronize access to _store
            if cache_file_key in self._store:
                raise ValueError(f"Key already in store: `{key}`.")

            self._store[cache_file_key] = (file_obj, valid_until)

    def get(
        self,
        key: str,
        valid_until: Union[datetime, date, str, int, float, None] = None,
        valid_at: Union[datetime, date, str, int, float, None] = None,
    ):
        """Retrieves the cache file associated with the given key and validity date.

        If no cache file is found for the provided key and date, the method returns None.
        The retrieved file is a file-like object that can be read from or written to.

        Args:
            key (str): The key to retrieve the cache file for.
            valid_until (Union[datetime, date, str, int, float, None], optional): The date the cache
                file is valid up to.
            valid_at (Union[datetime, date, str, int, float, None], optional): The date the cache
                file shall be valid at. Defaults to the current date if None is provided.

        Returns:
            file_obj: The file-like cache object, or None if no file is found.

        Example:
            >>> cache_file = cache_store.get('example_file')
            >>> if cache_file:
            >>>     cache_file.seek(0)
            >>>     print(cache_file.read())  # Output: Cached data (if exists)
        """
        if valid_until is None and valid_at is None:
            # Default to valid at today
            valid_at = datetime.now().date()

        with self._store_lock:  # Synchronize access to _store
            if valid_until:
                # Valid until given
                cache_file_key, valid_until = self._generate_cache_file_key(key, valid_until)
                cache_item = self._store.get(cache_file_key, None)
            else:
                # Valid at given
                cache_item = self._search(key, valid_at)
            if cache_item is None:
                return None
            return cache_item[0]

    def delete(self, key, valid_until: Union[datetime, date, str, int, float, None] = None):
        """Deletes the cache file associated with the given key and date.

        This method removes the cache file from the store.

        Args:
            key (str): The key of the cache file to delete.
            valid_until (Union[datetime, date, str, int, float, None], optional): The date the cache
                file is valid up to. Defaults to the current date if None is provided.
        """
        cache_file_key, valid_until = self._generate_cache_file_key(key, valid_until)
        with self._store_lock:  # Synchronize access to _store
            if cache_file_key in self._store:
                cache_file, valid_until = self._store[cache_file_key]
                file_path = self._get_file_path(cache_file)
                if file_path is None:
                    logger.warning(
                        f"The cache file with key '{cache_file_key}' is an in memory "
                        f"file object. Will only delete store entry but not file."
                    )
                    self._store.pop(cache_file_key)
                    return
                file_path = cache_file.name  # Get the file path from the cache file object
                del self._store[cache_file_key]
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        logger.debug(f"Deleted cache file: {file_path}")
                    except OSError as e:
                        logger.error(f"Error deleting cache file {file_path}: {e}")

    def clear(
        self, clear_all=False, clear_until: Union[datetime, date, str, int, float, None] = None
    ):
        """Deletes all cache files or that were last modifed before clear_untiltime.

        Args:
            clear_all (bool, optional): Delete all cache files. Default is False.
            clear_until (Union[datetime, date, str, int, float, None], optional): The
                threshold date. Cache files that are only valid before this date will be deleted.
                The default date is today.

        Raises:
            OSError: If there's an error during file deletion.
        """
        delete_keys = []  # List of keys to delete, prevent deleting when traversing the store
        clear_timestamp = None

        with self._store_lock:  # Synchronize access to _store
            for cache_file_key, cache_item in self._store.items():
                cache_file = cache_item[0]

                # Some weired logic to prevent calling to_datetime on clear_all.
                # Clear_all may be set on __del__. At this time some info for to_datetime will
                # not be available anymore.
                clear_file = clear_all
                if not clear_all:
                    if clear_timestamp is None:
                        clear_until = to_datetime(clear_until).date()
                        # Convert the threshold date to a timestamp (seconds since epoch)
                        clear_timestamp = to_datetime(clear_until).timestamp()
                    cache_file_timestamp = to_datetime(cache_item[1]).timestamp()
                    if cache_file_timestamp < clear_timestamp:
                        clear_file = True

                if clear_file:
                    # We have to clear this cache file
                    delete_keys.append(cache_file_key)

                    file_path = self._get_file_path(cache_file)

                    if file_path is None:
                        # In memory file like object
                        logger.warning(
                            f"The cache file with key '{cache_file_key}' is an in memory "
                            f"file object. Will only delete store entry but not file."
                        )
                        continue

                    if not os.path.exists(file_path):
                        # Already deleted
                        logger.warning(f"The cache file '{file_path}' was already deleted.")
                        continue

                    # Finally remove the file
                    try:
                        os.remove(file_path)
                        logger.debug(f"Deleted cache file: {file_path}")
                    except OSError as e:
                        logger.error(f"Error deleting cache file {file_path}: {e}")

            for delete_key in delete_keys:
                del self._store[delete_key]


def cache_in_file(
    ignore_params: List[str] = [],
    valid_until=None,
    mode: str = "wb+",
    delete: bool = False,
    suffix: Optional[str] = None,
):
    """Decorator to cache the output of a function into a temporary file.

    The decorator caches function output to a cache file based on its inputs as key to identify the
    cache file. Ignore parameters are used to avoid key generation on non-deterministic inputs, such
    as time values. We can also ignore parameters that are slow to serialize/constant across runs,
    such as large objects.

    The cache file is created using `CacheFileStore` and stored with the generated key.
    If the file exists in the cache and has not expired, it is returned instead of recomputing the
    result.

    The decorator scans the arguments of the decorated function for a 'valid_until' parameter. The
    value of this parameter will be used instead of the one given in the decorator if available.

    Content of cache files without a suffix are transparently pickled to save file space.

    Args:
        ignore_params (List[str], optional):
        valid_until (Union[datetime, date, str, int, float, None], optional):
            The expiration date of the cached file. If None, the cache file will be
            valid until the current date.
        mode (str, optional): The mode in which the file will be opened. Defaults to 'wb+'.
        delete (bool, optional): Whether the cache file will be deleted after being closed.
            Defaults to False.
        suffix (str, optional): A suffix for the cache file, such as an extension (e.g., '.txt').
            Defaults to None.

    Returns:
        callable: A decorated function that caches its result in a file.

    Example:
        >>> @cache_in_file(suffix = '.txt')
        >>> def expensive_computation(valid_until = None):
        >>>     # Perform some expensive computation
        >>>     return 'Some large result'
        >>>
        >>> result = expensive_computation(valid_until = date.today())
    """

    def decorator(func):
        nonlocal ignore_params, valid_until, mode, delete, suffix
        func_source_code = inspect.getsource(func)

        def wrapper(*args, **kwargs):
            nonlocal ignore_params, valid_until, mode, delete, suffix
            # Convert args to a dictionary based on the function's signature
            args_names = func.__code__.co_varnames[: func.__code__.co_argcount]
            args_dict = dict(zip(args_names, args))
            kwargs_clone = kwargs.copy()

            # Search for valid_until in parameters of function
            if "valid_until" in kwargs:
                valid_until = kwargs["valid_until"]
                kwargs_clone.pop("valid_until", None)
            # Remove ignored params
            for param in ignore_params:
                args_dict.pop(param, None)
                kwargs_clone.pop(param, None)

            # Create key based on argument names, argument values, and function source code
            key = str(args_dict) + str(kwargs_clone) + str(func_source_code)
            cache_file = CacheFileStore().get(key, valid_at=datetime.now())

            result = None
            if cache_file is not None:
                # cache file is available
                try:
                    logger.debug("Used cache file for function: " + func.__name__)
                    cache_file.seek(0)
                    if "b" in mode:
                        result = pickle.load(cache_file)
                    else:
                        result = cache_file.read()
                except Exception:
                    logger.info("Read failed")
            else:
                # Otherwise, call the function and save its result to the cache
                logger.debug("Created cache file for function: " + func.__name__)
                cache_file = CacheFileStore().create(
                    key, mode=mode, delete=delete, suffix=suffix, valid_until=valid_until
                )
                result = func(*args, **kwargs)
                try:
                    if "b" in mode:
                        pickle.dump(result, cache_file)
                    else:
                        cache_file.write(result)
                except Exception as e:
                    logger.info(f"Write failed: {e}")
            return result

        return wrapper

    return decorator
