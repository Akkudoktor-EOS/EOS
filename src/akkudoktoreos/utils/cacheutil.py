"""Class for in-memory managing of cache files.

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

from __future__ import annotations

import hashlib
import inspect
import os
import pickle
import tempfile
import threading
from datetime import date, datetime, time, timedelta
from typing import (
    IO,
    Any,
    Callable,
    Generic,
    List,
    Literal,
    Optional,
    ParamSpec,
    TypeVar,
    Union,
)

from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)


T = TypeVar("T")
Param = ParamSpec("Param")
RetType = TypeVar("RetType")


class CacheFileStoreMeta(type, Generic[T]):
    """A thread-safe implementation of CacheFileStore."""

    _instances: dict[CacheFileStoreMeta[T], T] = {}

    _lock: threading.Lock = threading.Lock()
    """Lock object to synchronize threads on first access to CacheFileStore."""

    def __call__(cls) -> T:
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

    def __init__(self) -> None:
        """Initializes the CacheFileStore instance.

        This constructor sets up an empty key-value store (a dictionary) where each key
        corresponds to a cache file that is associated with a given key and an optional date.
        """
        self._store: dict[str, tuple[IO[bytes], datetime]] = {}
        self._store_lock = threading.Lock()

    def _generate_cache_file_key(
        self, key: str, until_datetime: Union[datetime, None]
    ) -> tuple[str, datetime]:
        """Generates a unique cache file key based on the key and date.

        The cache file key is a combination of the input key and the date (if provided),
        hashed using SHA-256 to ensure uniqueness.

        Args:
            key (str): The key that identifies the cache file.
            until_datetime (Optional[Any]): The datetime
                until the cache file is valid. The default is the current date at maximum time
                (23:59:59).

        Returns:
            A tuple of:
                str: A hashed string that serves as the unique identifier for the cache file.
                datetime: The datetime until the the cache file is valid.
        """
        if until_datetime is None:
            until_datetime = datetime.combine(date.today(), time.max)
        key_datetime = to_datetime(until_datetime, as_string="UTC")
        cache_key = hashlib.sha256(f"{key}{key_datetime}".encode("utf-8")).hexdigest()
        return (f"{cache_key}", until_datetime)

    def _get_file_path(self, file_obj: IO[bytes]) -> Optional[str]:
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

    def _until_datetime_by_options(
        self,
        until_date: Optional[Any] = None,
        until_datetime: Optional[Any] = None,
        with_ttl: Union[timedelta, str, int, float, None] = None,
    ) -> datetime:
        """Get until_datetime from the given options."""
        if until_datetime:
            until_datetime = to_datetime(until_datetime)
        elif with_ttl:
            with_ttl = to_duration(with_ttl)
            until_datetime = to_datetime(datetime.now() + with_ttl)
        elif until_date:
            until_datetime = to_datetime(to_datetime(until_date).date())
        else:
            # end of today
            until_datetime = to_datetime(datetime.combine(date.today(), time.max))
        return until_datetime

    def _is_valid_cache_item(
        self,
        cache_item: tuple[IO[bytes], datetime],
        until_datetime: Optional[datetime] = None,
        at_datetime: Optional[datetime] = None,
        before_datetime: Optional[datetime] = None,
    ) -> bool:
        cache_file_datetime = cache_item[1]  # Extract the datetime associated with the cache item
        if (
            (until_datetime and until_datetime == cache_file_datetime)
            or (at_datetime and at_datetime <= cache_file_datetime)
            or (before_datetime and cache_file_datetime < before_datetime)
        ):
            return True
        return False

    def _search(
        self,
        key: str,
        until_datetime: Optional[Any] = None,
        at_datetime: Optional[Any] = None,
        before_datetime: Optional[Any] = None,
    ) -> Optional[tuple[str, IO[bytes], datetime]]:
        """Searches for a cached item that matches the key and falls within the datetime range.

        This method looks for a cache item with a key that matches the given `key`, and whose associated
        datetime (`cache_file_datetime`) falls on or after the `at_datetime`. If both conditions are met,
        it returns the cache item. Otherwise, it returns `None`.

        Args:
            key (str): The key to identify the cache item.
            until_date (Optional[Any]): The date
                until the cache file is valid. Time of day is set to maximum time (23:59:59).
            at_datetime (Optional[Any]): The datetime to compare with the cache item's datetime.
            before_datetime (Optional[Any]): The datetime to compare the cache item's datetime to be before.

        Returns:
            Optional[tuple]: Returns the cache_file_key, chache_file, cache_file_datetime if found,
                             otherwise returns `None`.
        """
        # Convert input to datetime if they are not None
        until_datetime_dt: Optional[datetime] = None
        if until_datetime is not None:
            until_datetime_dt = to_datetime(until_datetime)
        at_datetime_dt: Optional[datetime] = None
        if at_datetime is not None:
            at_datetime_dt = to_datetime(at_datetime)
        before_datetime_dt: Optional[datetime] = None
        if before_datetime is not None:
            before_datetime_dt = to_datetime(before_datetime)

        for cache_file_key, cache_item in self._store.items():
            # Check if the cache file datetime matches the given criteria
            if self._is_valid_cache_item(
                cache_item,
                until_datetime=until_datetime_dt,
                at_datetime=at_datetime_dt,
                before_datetime=before_datetime_dt,
            ):
                # This cache file is within the given datetime range
                # Extract the datetime associated with the cache item
                cache_file_datetime = cache_item[1]

                # Generate a cache file key based on the given key and the cache file datetime
                generated_key, _until_dt = self._generate_cache_file_key(key, cache_file_datetime)

                if generated_key == cache_file_key:
                    # The key matches, return the key and the cache item
                    return (cache_file_key, cache_item[0], cache_file_datetime)

        # Return None if no matching cache item is found
        return None

    def create(
        self,
        key: str,
        until_date: Optional[Any] = None,
        until_datetime: Optional[Any] = None,
        with_ttl: Union[timedelta, str, int, float, None] = None,
        mode: str = "wb+",
        delete: bool = False,
        suffix: Optional[str] = None,
    ) -> IO[bytes]:
        """Creates a new file-like tempfile object associated with the given key.

        If a cache file with the given key and valid timedate already exists, the existing file is
        returned. Otherwise, a new tempfile object is created and stored in the key-value store.

        Args:
            key (str): The key to store the cache file under.
            until_date (Optional[Any]): The date
                until the cache file is valid. Time of day is set to maximum time (23:59:59).
            until_datetime (Optional[Any]): The datetime
                until the cache file is valid. Time of day is set to maximum time (23:59:59) if not
                provided.
            with_ttl (Union[timedelta, str, int, float, None], optional): The time to live that
                the cache file is valid. Time starts now.
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
        until_datetime_dt = self._until_datetime_by_options(
            until_datetime=until_datetime, until_date=until_date, with_ttl=with_ttl
        )

        cache_file_key, _ = self._generate_cache_file_key(key, until_datetime_dt)
        with self._store_lock:  # Synchronize access to _store
            if (cache_file_item := self._store.get(cache_file_key)) is not None:
                # File already available
                cache_file_obj = cache_file_item[0]
            else:
                cache_file_obj = tempfile.NamedTemporaryFile(
                    mode=mode, delete=delete, suffix=suffix
                )
                self._store[cache_file_key] = (cache_file_obj, until_datetime_dt)
            cache_file_obj.seek(0)
            return cache_file_obj

    def set(
        self,
        key: str,
        file_obj: IO[bytes],
        until_date: Optional[Any] = None,
        until_datetime: Optional[Any] = None,
        with_ttl: Union[timedelta, str, int, float, None] = None,
    ) -> None:
        """Stores a file-like object in the cache under the specified key and date.

        This method allows you to manually set a file-like object into the cache with a specific key
        and optional date.

        Args:
            key (str): The key to store the file object under.
            file_obj: The file-like object.
            until_date (Optional[Any]): The date
                until the cache file is valid. Time of day is set to maximum time (23:59:59).
            until_datetime (Optional[Any]): The datetime
                until the cache file is valid. Time of day is set to maximum time (23:59:59) if not
                provided.
            with_ttl (Union[timedelta, str, int, float, None], optional): The time to live that
                the cache file is valid. Time starts now.

        Raises:
            ValueError: If the key is already in store.

        Example:
            >>> cache_store.set('example_file', io.BytesIO(b'Some binary data'))
        """
        until_datetime_dt = self._until_datetime_by_options(
            until_datetime=until_datetime, until_date=until_date, with_ttl=with_ttl
        )

        cache_file_key, until_date = self._generate_cache_file_key(key, until_datetime_dt)
        with self._store_lock:  # Synchronize access to _store
            if cache_file_key in self._store:
                raise ValueError(f"Key already in store: `{key}`.")

            self._store[cache_file_key] = (file_obj, until_date)

    def get(
        self,
        key: str,
        until_date: Optional[Any] = None,
        until_datetime: Optional[Any] = None,
        at_datetime: Optional[Any] = None,
        before_datetime: Optional[Any] = None,
    ) -> Optional[IO[bytes]]:
        """Retrieves the cache file associated with the given key and validity datetime.

        If no cache file is found for the provided key and datetime, the method returns None.
        The retrieved file is a file-like object that can be read from or written to.

        Args:
            key (str): The key to retrieve the cache file for.
            until_date (Optional[Any]): The date
                until the cache file is valid. Time of day is set to maximum time (23:59:59).
            until_datetime (Optional[Any]): The datetime
                until the cache file is valid. Time of day is set to maximum time (23:59:59) if not
                provided.
            at_datetime (Optional[Any]): The datetime the
                cache file shall be valid at. Time of day is set to maximum time (23:59:59) if not
                provided. Defaults to the current datetime if None is provided.
            before_datetime (Optional[Any]): The datetime
                to compare the cache files datetime to be before.

        Returns:
            file_obj: The file-like cache object, or None if no file is found.

        Example:
            >>> cache_file = cache_store.get('example_file')
            >>> if cache_file:
            >>>     cache_file.seek(0)
            >>>     print(cache_file.read())  # Output: Cached data (if exists)
        """
        if until_datetime or until_date:
            until_datetime = self._until_datetime_by_options(
                until_datetime=until_datetime, until_date=until_date
            )
        elif at_datetime:
            at_datetime = to_datetime(at_datetime)
        elif before_datetime:
            before_datetime = to_datetime(before_datetime)
        else:
            at_datetime = to_datetime(datetime.now())

        with self._store_lock:  # Synchronize access to _store
            search_item = self._search(key, until_datetime, at_datetime, before_datetime)
            if search_item is None:
                return None
            return search_item[1]

    def delete(
        self,
        key: str,
        until_date: Optional[Any] = None,
        until_datetime: Optional[Any] = None,
        before_datetime: Optional[Any] = None,
    ) -> None:
        """Deletes the cache file associated with the given key and datetime.

        This method removes the cache file from the store.

        Args:
            key (str): The key of the cache file to delete.
            until_date (Optional[Any]): The date
                until the cache file is valid. Time of day is set to maximum time (23:59:59).
            until_datetime (Optional[Any]): The datetime
                until the cache file is valid. Time of day is set to maximum time (23:59:59) if not
                provided.
            before_datetime (Optional[Any]): The datetime
                the cache file shall become or be invalid at. Time of day is set to maximum time
                (23:59:59) if not provided. Defaults to tommorow start of day.
        """
        if until_datetime or until_date:
            until_datetime = self._until_datetime_by_options(
                until_datetime=until_datetime, until_date=until_date
            )
        elif before_datetime:
            before_datetime = to_datetime(before_datetime)
        else:
            today = datetime.now().date()  # Get today's date
            tomorrow = today + timedelta(days=1)  # Add one day to get tomorrow's date
            before_datetime = to_datetime(datetime.combine(tomorrow, time.min))

        with self._store_lock:  # Synchronize access to _store
            search_item = self._search(key, until_datetime, None, before_datetime)
            if search_item:
                cache_file_key = search_item[0]
                cache_file = search_item[1]
                cache_file_datetime = search_item[2]
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
        self,
        clear_all: bool = False,
        before_datetime: Optional[Any] = None,
    ) -> None:
        """Deletes all cache files or those expiring before `before_datetime`.

        Args:
            clear_all (bool, optional): Delete all cache files. Default is False.
            before_datetime (Optional[Any]): The
                threshold date. Cache files that are only valid before this date will be deleted.
                The default datetime is beginning of today.

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
                        before_datetime = to_datetime(before_datetime, to_maxtime=False)
                        # Convert the threshold date to a timestamp (seconds since epoch)
                        clear_timestamp = to_datetime(before_datetime).timestamp()
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
    force_update: Optional[bool] = None,
    until_date: Optional[Any] = None,
    until_datetime: Optional[Any] = None,
    with_ttl: Union[timedelta, str, int, float, None] = None,
    mode: Literal["w", "w+", "wb", "wb+", "r", "r+", "rb", "rb+"] = "wb+",
    delete: bool = False,
    suffix: Optional[str] = None,
) -> Callable[[Callable[Param, RetType]], Callable[Param, RetType]]:
    """Cache the output of a function into a temporary file.

    This decorator caches the result of a function call in a temporary file. The cache is
    identified by a key derived from the function's input arguments, excluding those specified
    in `ignore_params`. This is useful for caching results of expensive computations while
    avoiding redundant recalculations.

    The cache file is created using `CacheFileStore` and stored with the generated key. If a valid
    cache file exists, it is returned instead of recomputing the result. The cache expiration is
    controlled by the `until_date`, `until_datetime`, `with_ttl`, or `force_update` arguments.
    If these arguments are present in the function call, their values override those specified in
    the decorator.

    By default, cache files are pickled to save storage space unless a `suffix` is provided. The
    `mode` parameter allows specifying file modes for reading and writing, and the `delete`
    parameter controls whether the cache file is deleted after use.

    Args:
        ignore_params (List[str], optional):
            List of parameter names to ignore when generating the cache key. Useful for excluding
            non-deterministic or irrelevant inputs, such as timestamps or large constant objects.
        force_update (bool, optional):
            Forces the cache to update, bypassing any existing cached results. If not provided,
            the function will check for a `force_update` argument in the decorated function call.
        until_date (Optional[Any], optional):
            Date until which the cache file is valid. If a date is provided, the time is set to
            the end of the day (23:59:59). If not specified, the function call arguments are checked.
        until_datetime (Optional[Any], optional):
            Datetime until which the cache file is valid. Time of day is set to maximum time
            (23:59:59) if not provided.
        with_ttl (Union[timedelta, str, int, float, None], optional):
            Time-to-live (TTL) for the cache file, starting from the time of caching. Can be
            specified as a `timedelta`, a numeric value (in seconds), or a string.
        mode (Literal["w", "w+", "wb", "wb+", "r", "r+", "rb", "rb+"], optional):
            File mode for opening the cache file. Defaults to "wb+" (write-binary with updates).
        delete (bool, optional):
            If True, deletes the cache file after it is closed. Defaults to False.
        suffix (Optional[str], optional):
            A file suffix (e.g., ".txt" or ".json") for the cache file. Defaults to None. If not
            provided, files are pickled by default.

    Returns:
        Callable[[Callable[Param, RetType]], Callable[Param, RetType]]:
            A decorated function that caches its result in a temporary file.

    Example:
        >>> from datetime import date
        >>> @cache_in_file(suffix='.txt')
        >>> def expensive_computation(until_date=None):
        >>>     # Perform some expensive computation
        >>>     return 'Some large result'
        >>>
        >>> result = expensive_computation(until_date=date.today())

    Notes:
        - The cache key is based on the function arguments after excluding those in `ignore_params`.
        - If conflicting expiration parameters are provided (`until_date`, `until_datetime`,
          `with_ttl`), the one in the function call takes precedence.
    """

    def decorator(func: Callable[Param, RetType]) -> Callable[Param, RetType]:
        nonlocal \
            ignore_params, \
            force_update, \
            until_date, \
            until_datetime, \
            with_ttl, \
            mode, \
            delete, \
            suffix
        func_source_code = inspect.getsource(func)

        def wrapper(*args: Param.args, **kwargs: Param.kwargs) -> RetType:
            nonlocal \
                ignore_params, \
                force_update, \
                until_date, \
                until_datetime, \
                with_ttl, \
                mode, \
                delete, \
                suffix
            # Convert args to a dictionary based on the function's signature
            args_names = func.__code__.co_varnames[: func.__code__.co_argcount]
            args_dict = dict(zip(args_names, args))

            # Search for caching parameters of function and remove
            for param in ["force_update", "until_datetime", "with_ttl", "until_date"]:
                if param in kwargs:
                    if param == "force_update":
                        force_update = kwargs[param]  # type: ignore[assignment]
                        kwargs.pop("force_update")
                    if param == "until_datetime":
                        until_datetime = kwargs[param]
                        until_date = None
                        with_ttl = None
                    elif param == "with_ttl":
                        until_datetime = None
                        until_date = None
                        with_ttl = kwargs[param]  # type: ignore[assignment]
                    elif param == "until_date":
                        until_datetime = None
                        until_date = kwargs[param]
                        with_ttl = None
                    kwargs.pop("force_update", None)
                    kwargs.pop("until_datetime", None)
                    kwargs.pop("until_date", None)
                    kwargs.pop("with_ttl", None)
                    break

            # Remove ignored params
            kwargs_clone = kwargs.copy()
            for param in ignore_params:
                args_dict.pop(param, None)
                kwargs_clone.pop(param, None)

            # Create key based on argument names, argument values, and function source code
            key = str(args_dict) + str(kwargs_clone) + str(func_source_code)

            result: Optional[RetType | bytes] = None
            # Get cache file that is currently valid
            cache_file = CacheFileStore().get(key)
            if not force_update and cache_file is not None:
                # cache file is available
                try:
                    logger.debug("Used cache file for function: " + func.__name__)
                    cache_file.seek(0)
                    if "b" in mode:
                        result = pickle.load(cache_file)
                    else:
                        result = cache_file.read()
                except Exception as e:
                    logger.info(f"Read failed: {e}")
                    # Fail gracefully - force creation
                    force_update = True
            if force_update or cache_file is None:
                # Otherwise, call the function and save its result to the cache
                logger.debug("Created cache file for function: " + func.__name__)
                cache_file = CacheFileStore().create(
                    key,
                    mode=mode,
                    delete=delete,
                    suffix=suffix,
                    until_datetime=until_datetime,
                    until_date=until_date,
                    with_ttl=with_ttl,
                )
                result = func(*args, **kwargs)
                try:
                    # Assure we have an empty file
                    cache_file.truncate(0)
                    if "b" in mode:
                        pickle.dump(result, cache_file)
                    else:
                        cache_file.write(result)  # type: ignore[call-overload]
                except Exception as e:
                    logger.info(f"Write failed: {e}")
                    CacheFileStore().delete(key)
            return result  # type: ignore[return-value]

        return wrapper

    return decorator
