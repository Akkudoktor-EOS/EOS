"""Abstract database interface."""

from __future__ import annotations

import bisect
import gzip
import pickle
from abc import ABC, abstractmethod
from enum import Enum, auto
from pathlib import Path
from threading import Lock
from typing import (
    TYPE_CHECKING,
    Any,
    Final,
    Generic,
    Iterable,
    Iterator,
    Literal,
    Optional,
    Protocol,
    Self,
    Type,
    TypeVar,
    Union,
)

from loguru import logger
from numpydantic import NDArray, Shape

from akkudoktoreos.core.coreabc import (
    ConfigMixin,
    DatabaseMixin,
    SingletonMixin,
)
from akkudoktoreos.utils.datetimeutil import (
    DateTime,
    Duration,
    to_datetime,
    to_duration,
)

# Key used to store metadata
DATABASE_METADATA_KEY: bytes = b"__metadata__"

# ==================== Abstract Database Interface ====================


class DatabaseABC(ABC, ConfigMixin):
    """Abstract base class for database.

    All operations accept an optional `namespace` argument. Implementations should
    treat None as the default/root namespace. Concrete implementations can map
    namespace -> native namespace (LMDB DBI) or emulate namespaces (SQLite uses
    a namespace column).
    """

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Return whether the database connection is open."""
        raise NotImplementedError

    @property
    def storage_path(self) -> Path:
        """Storage path for the database."""
        return self.config.general.data_folder_path / "db" / self.__class__.__name__.lower()

    @property
    def compression_level(self) -> int:
        """Compression level for database record data."""
        return self.config.database.compression_level

    @property
    def compression(self) -> bool:
        """Whether to compress stored values."""
        return self.config.database.compression_level > 0

    # Lifecycle

    @abstractmethod
    def provider_id(self) -> str:
        """Return the unique identifier for the database provider.

        To be implemented by derived classes.
        """
        raise NotImplementedError

    @abstractmethod
    def open(self, namespace: Optional[str] = None) -> None:
        """Open database connection and optionally set default namespace.

        Args:
            namespace: Optional default namespace to prepare.

        Raises:
            RuntimeError: If the database cannot be opened.
        """
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Close the database connection and cleanup resources."""
        raise NotImplementedError

    @abstractmethod
    def flush(self, namespace: Optional[str] = None) -> None:
        """Force synchronization of pending writes to storage (optional per-namespace)."""
        raise NotImplementedError

    # Metadata operations

    @abstractmethod
    def set_metadata(self, metadata: Optional[bytes], *, namespace: Optional[str] = None) -> None:
        """Save metadata for a given namespace.

        Metadata is treated separately from data records and stored as a single object.

        Args:
            metadata (bytes): Arbitrary metadata to save or None to delete metadata.
            namespace (Optional[str]): Optional namespace under which to store metadata.
        """
        raise NotImplementedError

    @abstractmethod
    def get_metadata(self, namespace: Optional[str] = None) -> Optional[bytes]:
        """Load metadata for a given namespace.

        Returns None if no metadata exists.

        Args:
            namespace (Optional[str]): Optional namespace whose metadata to retrieve.

        Returns:
            Optional[bytes]: The loaded metadata, or None if not found.
        """
        raise NotImplementedError

    # Basic record operations

    @abstractmethod
    def save_records(
        self, records: Iterable[tuple[bytes, bytes]], namespace: Optional[str] = None
    ) -> int:
        """Save multiple records into the specified namespace (or default).

        Args:
            records: Iterable providing key, value tuples ordered by key:
                - key: Byte key (sortable) for the record.
                - value: Serialized (and optionally compressed) bytes to store.
            namespace: Optional namespace.

        Returns:
            Number of records saved.

        Raises:
            RuntimeError: If DB not open or write failed.
        """
        raise NotImplementedError

    @abstractmethod
    def delete_records(self, keys: Iterable[bytes], namespace: Optional[str] = None) -> int:
        """Delete multiple records by key from the specified namespace.

        Args:
            keys: Iterable that provides the Byte keys to delete.
            namespace: Optional namespace.

        Returns:
            Number of records actually deleted.
        """
        raise NotImplementedError

    @abstractmethod
    def iterate_records(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
        namespace: Optional[str] = None,
        reverse: bool = False,
    ) -> Iterator[tuple[bytes, bytes]]:
        """Iterate over records for a namespace with optional bounds.

        Args:
            start_key: Inclusive start key, or None.
            end_key: Exclusive end key, or None.
            namespace: Optional namespace to target.
            reverse: If True iterate in descending key order.

        Yields:
            Tuples of (key, record).
        """
        raise NotImplementedError

    @abstractmethod
    def count_records(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
        *,
        namespace: Optional[str] = None,
    ) -> int:
        """Count records in [start_key, end_key) excluding metadata in specified namespace.

        Excludes metadata records.
        """
        raise NotImplementedError

    @abstractmethod
    def get_key_range(
        self, namespace: Optional[str] = None
    ) -> tuple[Optional[bytes], Optional[bytes]]:
        """Return (min_key, max_key) in the given namespace or (None, None) if empty."""
        raise NotImplementedError

    @abstractmethod
    def get_backend_stats(self, namespace: Optional[str] = None) -> dict[str, Any]:
        """Get backend-specific statistics; implementations may return namespace-specific data."""
        raise NotImplementedError

    # Compression helpers

    def serialize_data(self, data: bytes) -> bytes:
        """Optionally compress raw pickled data before storage.

        Args:
            data: Raw pickled bytes.

        Returns:
            Possibly compressed bytes.
        """
        if self.compression:
            return gzip.compress(data, compresslevel=self.compression_level)
        return data

    def deserialize_data(self, data: bytes) -> bytes:
        """Optionally decompress stored data.

        Args:
            data: Stored bytes.

        Returns:
            Raw pickled bytes (decompressed if needed).
        """
        if len(data) >= 2 and data[:2] == b"\x1f\x8b":
            try:
                return gzip.decompress(data)
            except gzip.BadGzipFile:
                pass
        return data


class DatabaseBackendABC(DatabaseABC, SingletonMixin):
    """Abstract base class for database backends.

    All operations accept an optional `namespace` argument. Implementations should
    treat None as the default/root namespace. Concrete implementations can map
    namespace -> native namespace (LMDB DBI) or emulate namespaces (SQLite uses
    a namespace column).
    """

    connection: Any
    lock: Lock
    _is_open: bool
    default_namespace: Optional[str]

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the DatabaseBackendABC base.

        Args:
            **kwargs: Backend-specific options (ignored by base).
        """
        self.connection = None
        self.lock = Lock()
        self._is_open = False
        self.default_namespace = None

    @property
    def is_open(self) -> bool:
        """Return whether the database connection is open."""
        return self._is_open


# ==================== Database Record Protocol Mixin ====================


class DataRecordProtocol(Protocol):
    date_time: DateTime

    def __init__(self, date_time: Any) -> None: ...

    def __getitem__(self, key: str) -> Any: ...

    def model_dump(self) -> dict: ...


T_Record = TypeVar("T_Record", bound=DataRecordProtocol)


class DatabaseTimestamp(str):
    """ISO8601 UTC datetime string used as database timestamp.

    Must always be in UTC and lexicographically sortable.

    Example:
        "20241027T123456[Z]" # 2024-10-27 12:34:56
    """

    __slots__ = ()

    @classmethod
    def from_datetime(cls, dt: DateTime) -> "DatabaseTimestamp":
        if dt.tz is None:
            raise ValueError("Timezone-aware datetime required")

        return cls(dt.in_timezone("UTC").format("YYYYMMDDTHHmmss[Z]"))

    def to_datetime(self) -> DateTime:
        from pendulum import parse

        return parse(self)


class _DatabaseTimestampUnbound(str):
    """Sentinel type representing an unbounded datetime value for database usage.

    Instances of this class are designed to be totally ordered relative to
    ISO datetime strings:

    - UNBOUND_START is smaller than any other value.
    - UNBOUND_END is greater than any other value.

    This makes the type safe for:
    - sorted lists
    - bisect operations
    - dictionary keys
    - range queries

    The type inherits from `str` to remain maximally efficient for hashing
    and dictionary usage.
    """

    __slots__ = ("_is_start",)

    if TYPE_CHECKING:
        _is_start: bool

    def __new__(cls, value: str, is_start: bool) -> "_DatabaseTimestampUnbound":
        obj = super().__new__(cls, value)
        obj._is_start = is_start
        return obj

    def __lt__(self, other: object) -> bool:
        if isinstance(other, _DatabaseTimestampUnbound):
            return self._is_start and not other._is_start
        return self._is_start

    def __le__(self, other: object) -> bool:
        if isinstance(other, _DatabaseTimestampUnbound):
            return self._is_start or self is other
        return self._is_start

    def __gt__(self, other: object) -> bool:
        if isinstance(other, _DatabaseTimestampUnbound):
            return not self._is_start and other._is_start
        return not self._is_start

    def __ge__(self, other: object) -> bool:
        if isinstance(other, _DatabaseTimestampUnbound):
            return not self._is_start or self is other
        return not self._is_start

    def __repr__(self) -> str:
        return "UNBOUND_START" if self._is_start else "UNBOUND_END"


DatabaseTimestampType = Union[DatabaseTimestamp, _DatabaseTimestampUnbound]


# Public sentinels
UNBOUND_START: Final[_DatabaseTimestampUnbound] = _DatabaseTimestampUnbound(
    "UNBOUND_START", is_start=True
)
UNBOUND_END: Final[_DatabaseTimestampUnbound] = _DatabaseTimestampUnbound(
    "UNBOUND_END", is_start=False
)


class _DatabaseTimeWindowUnbound:
    """Sentinel representing an unbounded time window.

    This is distinct from `None`:
    - None → parameter not provided
    - UNBOUND_WINDOW → explicitly infinite duration

    Designed to:
    - be identity-compared (is)
    - be hashable
    - be safe for dict usage
    - avoid accidental equality with other values
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNBOUND_WINDOW"

    def __reduce__(self) -> str:
        # Ensures singleton behavior during pickling
        return "UNBOUND_WINDOW"


DatabaseTimeWindowType = Union[Duration, None, _DatabaseTimeWindowUnbound]


UNBOUND_WINDOW: Final[_DatabaseTimeWindowUnbound] = _DatabaseTimeWindowUnbound()


class DatabaseRecordProtocol(Protocol, Generic[T_Record]):
    # ---- derived class required interface ----

    records: list[T_Record]

    def model_post_init(self, __context: Any) -> None: ...

    def model_copy(self, *, deep: bool = False) -> Self: ...

    # record class introspection
    @classmethod
    def record_class(cls) -> Type[T_Record]: ...

    # Duration for which records shall be kept in database storage
    def db_keep_duration(self) -> Optional[Duration]: ...

    # namespace
    def db_namespace(self) -> str: ...

    # ---- public DB interface ----

    def _db_reset_state(self) -> None: ...

    @property
    def db_enabled(self) -> bool: ...

    def db_timestamp_range(self) -> tuple[DatabaseTimestampType, DatabaseTimestampType]: ...

    def db_generate_timestamps(
        self,
        start_timestamp: DatabaseTimestamp,
        values_count: int,
        interval: Optional[Duration] = None,
    ) -> Iterator[DatabaseTimestamp]: ...

    def db_get_record(self, target_timestamp: DatabaseTimestamp) -> Optional[T_Record]: ...

    def db_insert_record(
        self,
        record: T_Record,
        *,
        mark_dirty: bool = True,
    ) -> None: ...

    def db_iterate_records(
        self,
        start_timestamp: Optional[DatabaseTimestampType] = None,
        end_timestamp: Optional[DatabaseTimestampType] = None,
    ) -> Iterator[T_Record]: ...

    def db_load_records(
        self,
        start_timestamp: Optional[DatabaseTimestampType] = None,
        end_timestamp: Optional[DatabaseTimestampType] = None,
    ) -> int: ...

    def db_delete_records(
        self,
        start_timestamp: Optional[DatabaseTimestampType] = None,
        end_timestamp: Optional[DatabaseTimestampType] = None,
    ) -> int: ...

    # ---- dirty tracking ----
    def db_mark_dirty_record(self, record: T_Record) -> None: ...

    def db_save_records(self) -> int: ...

    # ---- autosave ----
    def db_autosave(self) -> int: ...

    # ---- Remove old records from database to free space ----
    def db_vacuum(
        self,
        keep_hours: Optional[int] = None,
        keep_datetime: Optional[DatabaseTimestampType] = None,
    ) -> int: ...

    # ---- statistics about database storage ----
    def db_count_records(self) -> int: ...

    def db_get_stats(self) -> dict: ...


T_DatabaseRecordProtocol = TypeVar("T_DatabaseRecordProtocol", bound="DatabaseRecordProtocol")


class DatabaseRecordProtocolLoadPhase(Enum):
    """Database loading phases.

    NONE:
        No records have been loaded from the database.

    INITIAL:
        A limited initial time window has been loaded, typically centered
        around a target datetime.

    FULL:
        All records in the database have been loaded into memory.

    The phase controls whether further calls to ``db_ensure_loaded`` may
    trigger additional database access.
    """

    NONE = auto()  # nothing loaded
    INITIAL = auto()  # initial window loaded
    FULL = auto()  # fully expanded


class DatabaseRecordProtocolMixin(
    ConfigMixin,
    DatabaseMixin,
    Generic[T_Record],  # for typing only
):
    """Database Record Protocol Mixin.

    Completely manages in memory records and database storage.

    Expects records with date_time (DatabaseTimestamp) property and the a record list
    in self.records of the derived class.

    DatabaseRecordProtocolMixin expects the derived classes to be singletons.
    """

    # Tell mypy these attributes exist (will be provided by subclasses)
    if TYPE_CHECKING:
        records: list[T_Record]

        @classmethod
        def record_class(cls) -> Type[T_Record]: ...

        @property
        def record_keys_writable(self) -> list[str]: ...

        def key_to_array(
            self,
            key: str,
            start_datetime: Optional[DateTime] = None,
            end_datetime: Optional[DateTime] = None,
            interval: Optional[Duration] = None,
            fill_method: Optional[str] = None,
            dropna: Optional[bool] = True,
            boundary: Literal["strict", "context"] = "context",
            align_to_interval: bool = False,
        ) -> NDArray[Shape["*"], Any]: ...

    # Database configuration

    def db_initial_time_window(self) -> Optional[Duration]:
        """Return the initial time window used for database loading.

        This window defines the initial symmetric time span around a target datetime
        that should be loaded from the database when no explicit search time window
        is specified. It serves as a loading hint and may be expanded by the caller
        if no records are found within the initial range.

        Subclasses may override this method to provide a domain-specific default.

        Returns:
            The initial loading time window as a Duration, or ``None`` to indicate
            that no initial window constraint should be applied.
        """
        return None

    # -----------------------------------------------------
    # Initialization
    # -----------------------------------------------------

    def _db_ensure_initialized(self) -> None:
        """Initialize DB runtime state.

        Idempotent — safe to call multiple times.
        """
        if not getattr(self, "_db_initialized", None):
            # record datetime to record mapping for fast lookup
            self._db_record_index: dict[DatabaseTimestamp, T_Record] = {}
            self._db_sorted_timestamps: list[DatabaseTimestamp] = []

            # Loading phase tracking
            self._db_load_phase: DatabaseRecordProtocolLoadPhase = (
                DatabaseRecordProtocolLoadPhase.NONE
            )
            # Range of timestamps the was already queried from database storage during load
            self._db_loaded_range: Optional[tuple[DatabaseTimestampType, DatabaseTimestampType]] = (
                None
            )

            # Dirty tracking
            # - dirty records since last save
            self._db_dirty_timestamps: set[DatabaseTimestamp] = set()
            # - records added since last save
            self._db_new_timestamps: set[DatabaseTimestamp] = set()
            # - deleted records since last save
            self._db_deleted_timestamps: set[DatabaseTimestamp] = set()

            self._db_version: int = 1

            # Storage
            self._db_metadata: Optional[dict] = None
            self._db_storage_initialized: bool = False

            self._db_initialized: bool = True

        if not self._db_storage_initialized and self.db_enabled:
            # Metadata
            existing_metadata = self._db_load_metadata()
            if existing_metadata:
                self._db_metadata = existing_metadata
            else:
                self._db_metadata = {
                    "version": self._db_version,
                    "created": to_datetime(as_string=True),
                    "provider_id": getattr(self, "provider_id", lambda: "unknown")(),
                    "compression": self.database.compression,
                    "backend": self.database.__class__.__name__,
                }
                self._db_save_metadata(self._db_metadata)

            logger.info(
                f"Initialized {self.database.__class__.__name__}:{self.db_namespace()} storage at "
                f"{self.database.storage_path} "
                f"autosave_interval_sec={self.config.database.autosave_interval_sec})"
            )

            self._db_storage_initialized = True

    def model_post_init(self, __context: Any) -> None:
        """Initialize DB state attributes immediately after Pydantic construction."""
        # Always call super() first — other mixins may also define model_post_init
        super().model_post_init(__context)  # type: ignore[misc]
        self._db_ensure_initialized()

    # -----------------------------------------------------
    # Helpers
    # -----------------------------------------------------

    def _db_key_from_timestamp(self, dt: DatabaseTimestamp) -> bytes:
        """Convert database timestamp to a sortable database backend key."""
        return dt.encode("utf-8")

    def _db_key_to_timestamp(self, dbkey: bytes) -> DatabaseTimestamp:
        """Convert database backend key back to database timestamp."""
        return DatabaseTimestamp(dbkey.decode("utf-8"))

    def _db_timestamp_after(self, timestamp: DatabaseTimestamp) -> DatabaseTimestamp:
        """Get database timestamp after this timestamp.

        A minimal time span is added to the DatabaseTimestamp to get the first possible timestamp
        after DatabaseTimestamp.
        """
        target = DatabaseTimestamp.to_datetime(timestamp)
        db_datetime_after = DatabaseTimestamp.from_datetime(target.add(seconds=1))
        return db_datetime_after

    def db_previous_timestamp(
        self,
        timestamp: DatabaseTimestamp,
    ) -> Optional[DatabaseTimestamp]:
        """Find the largest timestamp < given timestamp.

        Search memory-first, then fallback to database if necessary.
        """
        self._db_ensure_initialized()

        # Step 1: Memory-first search
        if self._db_sorted_timestamps:
            idx = bisect.bisect_left(self._db_sorted_timestamps, timestamp)
            if idx > 0:
                return self._db_sorted_timestamps[idx - 1]

        # Step 2: Check if DB might contain older keys
        if not self.db_enabled:
            return None

        db_min_key, _ = self.database.get_key_range(self.db_namespace())
        if db_min_key is None:
            return None

        db_min_ts = self._db_key_to_timestamp(db_min_key)
        if timestamp <= db_min_ts:
            return None

        # Step 3: Load left part of DB if not already in memory
        # We want records < timestamp
        start_key = None
        end_key = self._db_key_from_timestamp(timestamp)

        # Only load if timestamp is out of currently loaded memory
        if self._db_loaded_range:
            loaded_start, _ = self._db_loaded_range
            if isinstance(loaded_start, DatabaseTimestamp) and timestamp > loaded_start:
                # Already partially loaded, restrict iterator to unloaded portion
                start_key = self._db_key_from_timestamp(loaded_start)

        previous_ts: Optional[DatabaseTimestamp] = None
        for key, _ in self.database.iterate_records(
            start_key=start_key,
            end_key=end_key,
            namespace=self.db_namespace(),
        ):
            ts = self._db_key_to_timestamp(key)
            if ts in self._db_deleted_timestamps:
                continue
            previous_ts = ts  # last one before `timestamp`

        return previous_ts

    def db_next_timestamp(
        self,
        timestamp: DatabaseTimestamp,
    ) -> Optional[DatabaseTimestamp]:
        """Find the smallest timestamp > given timestamp.

        Search memory-first, then fallback to database if necessary.
        """
        self._db_ensure_initialized()

        # Step 1: Memory-first search
        if self._db_sorted_timestamps:
            idx = bisect.bisect_right(self._db_sorted_timestamps, timestamp)
            if idx < len(self._db_sorted_timestamps):
                return self._db_sorted_timestamps[idx]

        # Step 2: Check if DB might contain newer keys
        if not self.db_enabled:
            return None

        _, db_max_key = self.database.get_key_range(self.db_namespace())
        if db_max_key is None:
            return None

        db_max_ts = self._db_key_to_timestamp(db_max_key)
        if timestamp >= db_max_ts:
            return None

        # Step 3: Search right part of DB if not already in memory
        timestamp_key = self._db_key_from_timestamp(timestamp)
        start_key = timestamp_key
        end_key = None

        # Restrict iterator to unloaded portion if partially loaded
        if self._db_loaded_range:
            _, loaded_end = self._db_loaded_range
            # Assumes everything < loaded_end is fully represented in memory.
            if isinstance(loaded_end, DatabaseTimestamp) and timestamp < loaded_end:
                start_key = self._db_key_from_timestamp(max(timestamp, loaded_end))

        for key, _ in self.database.iterate_records(
            start_key=start_key,
            end_key=end_key,
            namespace=self.db_namespace(),
        ):
            if key == timestamp_key:
                # skip
                continue

            ts = self._db_key_to_timestamp(key)

            # Check for deleted (only necessary for database - memory already removed
            if ts in self._db_deleted_timestamps:
                continue

            return ts  # first valid one

        return None

    def _db_serialize_record(self, record: T_Record) -> bytes:
        """Serialize a DataRecord to bytes."""
        if self.database is None:
            raise ValueError("Database not defined.")
        data = pickle.dumps(record.model_dump(), protocol=pickle.HIGHEST_PROTOCOL)
        return self.database.serialize_data(data)

    def _db_deserialize_record(self, data: bytes) -> T_Record:
        """Deserialize bytes to a DataRecord."""
        if self.database is None:
            raise ValueError("Database not defined.")
        data = self.database.deserialize_data(data)
        record_data = pickle.loads(data)  # noqa: S301
        return self.record_class()(**record_data)

    def _db_save_metadata(self, metadata: dict) -> None:
        """Save metadata to database."""
        if not self.db_enabled:
            return

        key = DATABASE_METADATA_KEY
        value = pickle.dumps(metadata)
        self.database.set_metadata(value, namespace=self.db_namespace())

    def _db_load_metadata(self) -> Optional[dict]:
        """Load metadata from database."""
        if not self.db_enabled:
            return None

        try:
            value = self.database.get_metadata(namespace=self.db_namespace())
            return pickle.loads(value)  # noqa: S301
        except Exception:
            logger.debug("Can not load metadata.")
        return None

    def _db_reset_state(self) -> None:
        self.records = []
        self._db_loaded_range = None
        self._db_load_phase = DatabaseRecordProtocolLoadPhase.NONE
        try:
            del self._db_initialized
        except:
            logger.debug("_db_reset_state called on uninitialized sequence")

    def _db_clone_empty(self: T_DatabaseRecordProtocol) -> T_DatabaseRecordProtocol:
        """Create an empty internal clone for database operations.

        The clone shares configuration and database access implicitly via
        ConfigMixin and DatabaseMixin, but contains no in-memory records
        or loaded-range state.

        Internal helper for database workflows only.
        """
        clone = self.model_copy(deep=True)
        clone._db_reset_state()

        return clone

    def _search_window(
        self,
        center_timestamp: Optional[DatabaseTimestampType],
        time_window: DatabaseTimeWindowType,
    ) -> tuple[DatabaseTimestampType, DatabaseTimestampType]:
        """Compute a symmetric search window around a center timestamp.

        This method always returns valid database boundary values.

        Args:
            center_timestamp: Center of the window. Defaults to current UTC time
                if None. Must not be an unbounded timestamp sentinel.
            time_window: Total width of the search window.
                Half is applied on each side of center_timestamp.
                - None: interpreted as unbounded.
                - UNBOUND_WINDOW: interpreted as unbounded.
                - Duration: symmetric bounded interval.

        Returns:
            A tuple (start, end) representing a half-open interval.
            Always returns valid database timestamp boundaries:
            either concrete timestamps or (UNBOUND_START, UNBOUND_END).

        Raises:
            TypeError: If center_timestamp is an unbounded timestamp sentinel.
            ValueError: If time_window is a negative Duration.
        """
        # Unbounded cases → full DB range
        if time_window is None or isinstance(time_window, _DatabaseTimeWindowUnbound):
            return UNBOUND_START, UNBOUND_END

        if isinstance(center_timestamp, _DatabaseTimestampUnbound):
            raise TypeError("center_timestamp cannot be of unbounded timestamp type.")

        # Resolve center
        if center_timestamp is None:
            center = to_datetime().in_timezone("UTC")
        else:
            center = DatabaseTimestamp.to_datetime(center_timestamp)

        duration = to_duration(time_window)

        if duration.total_seconds() < 0:
            raise ValueError("time_window must be non-negative")

        # Use duration arithmetic to avoid float precision issues
        half = duration / 2

        start = center - half
        end = center + half

        return (
            DatabaseTimestamp.from_datetime(start),
            DatabaseTimestamp.from_datetime(end),
        )

    def _db_range_covered(
        self,
        start_timestamp: DatabaseTimestampType,
        end_timestamp: DatabaseTimestampType,
    ) -> bool:
        """Return True if [start_timestamp, end_timestamp) is fully covered.

        Args:
            start_timestamp: Inclusive lower boundary of the requested range.
            end_timestamp: Exclusive upper boundary of the requested range.

        Returns:
            True if the requested half-open interval is completely contained
            within the loaded database range.

        Raises:
            TypeError: If start_timestamp or end_timestamp is None.
        """
        if start_timestamp is None or end_timestamp is None:
            raise TypeError(
                "start_timestamp and end_timestamp must not be None. "
                "Use UNBOUND_START / UNBOUND_END instead."
            )

        if not isinstance(start_timestamp, (str, _DatabaseTimestampUnbound)):
            raise TypeError(
                f"Invalid start_timestamp type: {type(start_timestamp)}. "
                "Must be DatabaseTimestamp or unbound sentinel."
            )

        if not isinstance(end_timestamp, (str, _DatabaseTimestampUnbound)):
            raise TypeError(
                f"Invalid end_timestamp type: {type(end_timestamp)}. "
                "Must be DatabaseTimestamp or unbound sentinel."
            )

        if self._db_loaded_range is None:
            return False

        loaded_start, loaded_end = self._db_loaded_range

        if loaded_start is None or loaded_end is None:
            return False

        return loaded_start <= start_timestamp and end_timestamp <= loaded_end

    def _db_load_initial_window(
        self,
        center_timestamp: Optional[DatabaseTimestampType] = None,
    ) -> None:
        """Load an initial time window of records from the database.

        This method establishes the first lazy-loading window when the load phase
        is ``NONE``. It queries the database for records within a symmetric time
        interval around ``center_timestamp`` and transitions the load phase to
        ``INITIAL``.

        The loaded interval is recorded in ``self._db_loaded_range`` and represents
        **database coverage**, not memory continuity. That is:

            - All database records in the half-open interval
                [start_timestamp, end_timestamp) have been queried.
            - Records within that interval are either loaded into memory or
                confirmed absent.
            - The interval does not imply that memory contains continuous records.

        The loaded range is later expanded incrementally if additional
        out-of-window ranges are requested.

        If ``center_timestamp`` is not provided, the current time is used.

        Args:
            center_timestamp (DatabaseTimestampType):
                The central reference time for the initial loading window.
                If None, the current time is used.

        Side Effects:

            * Loads records from persistent storage into memory.
            * Sets ``self._db_loaded_range`` by db_load_records().
            * Sets ``self._db_load_phase`` to ``INITIAL``.

        Notes:
            * The loaded range uses half-open interval semantics:
              [start_timestamp, end_timestamp).
            * This method does not perform a full database load.
            * Empty query results still establish coverage for the interval,
              preventing redundant database queries.
        """
        if not self.db_enabled:
            return

        # Redundant guard - should only be called from load phase None
        if self._db_load_phase is not DatabaseRecordProtocolLoadPhase.NONE:
            raise RuntimeError(
                "_db_load_initial_window() may only be called when load phase is NONE."
            )

        window_h = self.config.database.initial_load_window_h
        if window_h is None:
            start, end = self._search_window(center_timestamp, UNBOUND_WINDOW)
        else:
            window = to_duration(window_h * 3600)
            start, end = self._search_window(center_timestamp, window)

        self.db_load_records(start, end)

        self._db_load_phase = DatabaseRecordProtocolLoadPhase.INITIAL

    def _db_load_full(self) -> int:
        """Load all remaining records from the database into memory.

        This method performs a **full load** of the database, ensuring that all
        records are present in memory. After this operation, the `_db_load_phase`
        will be set to FULL, and `_db_loaded_range` will cover all known records.

        **State transitions:**

            * Allowed only from the INITIAL phase (partial window loaded) or NONE
              (nothing loaded yet).
            * If already FULL, the method is a no-op and returns 0.

        Returns:
            int: Number of records loaded from the database during this operation.

        Raises:
            RuntimeError: If called from an invalid load phase.
        """
        if not self.db_enabled:
            return 0

        # Guard: must only run from NONE or INITIAL
        if self._db_load_phase not in (
            DatabaseRecordProtocolLoadPhase.NONE,
            DatabaseRecordProtocolLoadPhase.INITIAL,
        ):
            raise RuntimeError(
                "_db_load_full() may only be called when load phase is NONE or INITIAL."
            )

        # Perform full database load (memory is authoritative; skips duplicates)
        # This also sets _db_loaded_range
        loaded_count = self.db_load_records()

        # Update state
        self._db_load_phase = DatabaseRecordProtocolLoadPhase.FULL

        return loaded_count

    def _extend_boundaries(
        self,
        start_timestamp: DatabaseTimestampType,
        end_timestamp: DatabaseTimestampType,
    ) -> tuple[DatabaseTimestampType, DatabaseTimestampType]:
        """Find nearest database records outside requested range.

        Returns:
            (new_start, new_end) timestamps to fully cover requested range including neighbors.
        """
        if start_timestamp is None or end_timestamp is None:
            # Make mypy happy
            raise RuntimeError(f"timestamps shall be non None: {start_timestamp}, {end_timestamp}")

        new_start, new_end = start_timestamp, end_timestamp

        # Extend start
        if (
            not isinstance(start_timestamp, _DatabaseTimestampUnbound)
            and self._db_sorted_timestamps
            and start_timestamp < self._db_sorted_timestamps[0]
        ):
            # There may be earlier DB records
            # Reverse iterate to get nearest smaller key
            for key, _ in self.database.iterate_records(
                start_key=UNBOUND_START,
                end_key=self._db_key_from_timestamp(start_timestamp),
                namespace=self.db_namespace(),
                reverse=True,
            ):
                ts = self._db_key_to_timestamp(key)

                if ts in self._db_deleted_timestamps:
                    continue

                if ts < start_timestamp:
                    new_start = ts
                break  # first valid record is the nearest

        # Extend end
        if (
            not isinstance(end_timestamp, _DatabaseTimestampUnbound)
            and self._db_sorted_timestamps
            and end_timestamp > self._db_sorted_timestamps[-1]
        ):
            # There may be later DB records
            for key, _ in self.database.iterate_records(
                start_key=self._db_key_from_timestamp(end_timestamp),
                end_key=UNBOUND_END,
                namespace=self.db_namespace(),
            ):
                ts = self._db_key_to_timestamp(key)

                if ts in self._db_deleted_timestamps:
                    continue

                if ts >= end_timestamp:
                    new_end = ts
                break  # first valid record is the nearest

        return new_start, new_end

    def _db_ensure_loaded(
        self,
        start_timestamp: Optional[DatabaseTimestampType] = None,
        end_timestamp: Optional[DatabaseTimestampType] = None,
        *,
        center_timestamp: Optional[DatabaseTimestampType] = None,
    ) -> None:
        """Ensure database records for a given timestamp range are available in memory.

        Lazy loading is performed in phases: NONE -> INITIAL -> FULL

        1. **NONE**: No records loaded yet.

            * If a range is provided, load exactly that range.
            * If no range, load an initial window around `center_timestamp`.

        2. **INITIAL**: A partial window is loaded.

            * If requested range extends beyond loaded window, expand left/right as needed.
            * If no range requested, escalate to FULL.

        3. **FULL**: All records already loaded. Nothing to do.

        Args:
            start_timestamp (DatabaseTimestampType): Inclusive start of desired range.
            end_timestamp (DatabaseTimestampType): Exclusive end of desired range.
            center_timestamp (DatabaseTimestampType): Center for initial window if nothing loaded.

        Notes:
            * Only used for preparing memory for subsequent queries; does not return records.
            * `center_timestamp` is ignored once an initial window has been established.
        """
        if not self.db_enabled:
            return

        # Normalize boundaries immediately (strict DB layer rule)
        if start_timestamp is None:
            start_timestamp = UNBOUND_START
        if end_timestamp is None:
            end_timestamp = UNBOUND_END

        # Shortcut: memory already covers the extended range
        if self._db_sorted_timestamps:
            mem_start, mem_end = self._db_sorted_timestamps[0], self._db_sorted_timestamps[-1]

            # Case 1: bounded request
            if (
                start_timestamp is not UNBOUND_START
                and end_timestamp is not UNBOUND_END
                and mem_start < start_timestamp
                and mem_end >= end_timestamp
            ):
                return

            # Case 2: unbounded request only safe if FULL
            if (
                self._db_load_phase is DatabaseRecordProtocolLoadPhase.FULL
                and (start_timestamp is UNBOUND_START or mem_start < start_timestamp)
                and (end_timestamp is UNBOUND_END or mem_end >= end_timestamp)
            ):
                return

        # Phase 0: NOTHING LOADED
        if self._db_load_phase is DatabaseRecordProtocolLoadPhase.NONE:
            if start_timestamp is UNBOUND_START and end_timestamp is UNBOUND_END:
                self._db_load_initial_window(center_timestamp)
                # _db_load_initial_window sets _db_loaded_range and _db_load_phase
            else:
                # Load the records
                loaded = self.db_load_records(start_timestamp, end_timestamp)
                self._db_load_phase = DatabaseRecordProtocolLoadPhase.INITIAL
            return

        if center_timestamp is not None:
            logger.debug(
                f"Center timestamp parameter '{center_timestamp}' given outside of load phase NONE"
            )

        # Phase 1: INITIAL WINDOW (PARTIAL)
        if self._db_load_phase is DatabaseRecordProtocolLoadPhase.INITIAL:
            # Escalate to FULL if no range is specified
            if self._db_loaded_range is None:
                # Should never happen
                raise RuntimeError("_db_loaded_range shall set when load phase is INITIAL")

            if self._db_range_covered(start_timestamp, end_timestamp):
                return  # already have it

            if start_timestamp == UNBOUND_START and end_timestamp == UNBOUND_END:
                self._db_load_full()
                return

            current_start, current_end = self._db_loaded_range
            if current_start is None or current_end is None:
                raise RuntimeError(
                    "_db_loaded_range shall not be set to (None, None) when load phase is INITIAL"
                )

            # Left expansion
            if start_timestamp < current_start:
                self.db_load_records(start_timestamp, current_start)

            # Right expansion
            if end_timestamp > current_end:
                self.db_load_records(current_end, end_timestamp)

            return

        # Phase 2: FULL
        # Everything already loaded, nothing to do
        return

    # ---- derived class required interface ----

    def db_keep_duration(self) -> Optional[Duration]:
        """Duration for which database records should be retained.

        Used when removing old records from database to free space.

        Defaults to general database configuration.

        May be provided by derived class.

        Returns:
            Duration or None (forever).
        """
        duration_h: Optional[Duration] = self.config.database.keep_duration_h
        if duration_h is None:
            return None
        return to_duration(duration_h * 3600)

    def db_namespace(self) -> str:
        """Namespace of database.

        To be implemented by derived class.
        """
        raise NotImplementedError

    # ---- public DB interface ----

    @property
    def db_enabled(self) -> bool:
        return self.database.is_open

    def db_timestamp_range(
        self,
    ) -> tuple[Optional[DatabaseTimestamp], Optional[DatabaseTimestamp]]:
        """Get the timestamp range of records in database.

        Regards records in storage plus extra records in memory.
        """
        # Defensive call - model_post_init() may not have initialized metadata
        self._db_ensure_initialized()

        if self._db_sorted_timestamps:
            memory_min_timestamp: Optional[DatabaseTimestamp] = self._db_sorted_timestamps[0]
            memory_max_timestamp: Optional[DatabaseTimestamp] = self._db_sorted_timestamps[-1]
        else:
            memory_min_timestamp = None
            memory_max_timestamp = None

        if not self.db_enabled:
            return memory_min_timestamp, memory_max_timestamp

        db_min_key, db_max_key = self.database.get_key_range(self.db_namespace())

        if db_min_key is None or db_max_key is None:
            return memory_min_timestamp, memory_max_timestamp

        storage_min_timestamp = self._db_key_to_timestamp(db_min_key)
        storage_max_timestamp = self._db_key_to_timestamp(db_max_key)

        if memory_min_timestamp and memory_min_timestamp < storage_min_timestamp:
            min_timestamp = memory_min_timestamp
        else:
            min_timestamp = storage_min_timestamp
        if memory_max_timestamp and memory_max_timestamp > storage_max_timestamp:
            max_timestamp = memory_max_timestamp
        else:
            max_timestamp = storage_max_timestamp

        return min_timestamp, max_timestamp

    def db_generate_timestamps(
        self,
        start_timestamp: DatabaseTimestamp,
        values_count: int,
        interval: Optional[Duration] = None,
    ) -> Iterator[DatabaseTimestamp]:
        """Generate database timestamps using fixed absolute time stepping.

        The iterator advances strictly in UTC, guaranteeing constant
        spacing in seconds across daylight saving transitions.

        Returned database timestamps are in UTC. This avoids ambiguity during
        fall-back transitions and prevents accidental overwriting when
        inserting into UTC-normalized storage backends.

        Args:
            start_timestamp (DatabaseTimestamp): Starting database timestamp.
            values_count (int): Number of timestamps to generate.
            interval (Optional[Duration]): Fixed duration between timestamps.
                Defaults to 1 hour if not provided.

        Yields:
            DatabaseTimestamp: UTC-based database timestamps.

        Raises:
            ValueError: If values_count is negative.
        """
        if values_count < 0:
            raise ValueError("values_count must be non-negative")

        if interval is None:
            interval = Duration(hours=1)

        step_seconds = int(interval.total_seconds())

        current_utc = DatabaseTimestamp.to_datetime(start_timestamp)

        for _ in range(values_count):
            yield DatabaseTimestamp.from_datetime(current_utc)
            current_utc = current_utc.add(seconds=step_seconds)

    def db_get_record(
        self,
        target_timestamp: DatabaseTimestamp,
        *,
        time_window: DatabaseTimeWindowType = None,
    ) -> Optional[T_Record]:
        """Get the record at or nearest to the specified timestamp.

        The search strategies are:

        * None - exact match only.
        * UNBOUND_WINDOW - nearest record across all stored records.
        * Duration - nearest record within a symmetric window of this total width around
          target_timestamp.

        Args:
            target_timestamp: The timestamp to search for.
            time_window: Controls the search strategy (None, UNBOUND_WINDOW, Duration).

        Returns:
            Exact match, nearest record within the window, or None.
        """
        self._db_ensure_initialized()

        if time_window is None:
            # Exact match only — load the minimal range containing this point
            self._db_ensure_loaded(
                target_timestamp,
                self._db_timestamp_after(target_timestamp),
                center_timestamp=target_timestamp,
            )
            return self._db_record_index.get(target_timestamp, None)

        # load the relevant range
        # in case of unbounded escalates to FULL
        search_start, search_end = self._search_window(target_timestamp, time_window)
        self._db_ensure_loaded(search_start, search_end, center_timestamp=target_timestamp)

        # Exact match first (works for all three cases once loaded)
        record = self._db_record_index.get(target_timestamp, None)
        if record is not None:
            return record

        # Nearest-neighbour search
        idx = bisect.bisect_left(self._db_sorted_timestamps, target_timestamp)
        candidates = []
        if idx < len(self._db_sorted_timestamps):
            candidates.append(self.records[idx])
        if idx > 0:
            candidates.append(self.records[idx - 1])
        if not candidates:
            return None

        record = min(
            candidates,
            key=lambda r: abs(
                (r.date_time - DatabaseTimestamp.to_datetime(target_timestamp)).total_seconds()
            ),
        )

        # For bounded windows, enforce the distance constraint
        if not isinstance(time_window, _DatabaseTimeWindowUnbound):
            half_seconds = to_duration(time_window).total_seconds() / 2
            if (
                abs(
                    (
                        record.date_time - DatabaseTimestamp.to_datetime(target_timestamp)
                    ).total_seconds()
                )
                > half_seconds
            ):
                return None

        return record

    def db_insert_record(
        self,
        record: T_Record,
        *,
        mark_dirty: bool = True,
    ) -> None:
        # Defensive call - model_post_init() may not have initialized metadata
        self._db_ensure_initialized()

        # Ensure normalized to UTC
        db_record_date_time = DatabaseTimestamp.from_datetime(record.date_time)

        self._db_ensure_loaded(
            start_timestamp=db_record_date_time,
            end_timestamp=db_record_date_time,
        )

        # Memory only
        if db_record_date_time in self._db_record_index:
            # No duplicates allowed
            raise ValueError(f"Duplicate timestamp {record.date_time} -> {db_record_date_time}")

        if db_record_date_time in self._db_deleted_timestamps:
            # Clear tombstone - if we are re-inserting
            self._db_deleted_timestamps.discard(db_record_date_time)

        # insert
        index = bisect.bisect_left(self._db_sorted_timestamps, db_record_date_time)
        self._db_sorted_timestamps.insert(index, db_record_date_time)
        self.records.insert(index, record)
        self._db_record_index[db_record_date_time] = record

        if mark_dirty:
            self._db_dirty_timestamps.add(db_record_date_time)
            self._db_new_timestamps.add(db_record_date_time)

    # -----------------------------------------------------
    # Load (range)
    # -----------------------------------------------------

    def db_load_records(
        self,
        start_timestamp: Optional[DatabaseTimestampType] = None,
        end_timestamp: Optional[DatabaseTimestampType] = None,
    ) -> int:
        """Load records from database into memory.

        Merges database records into in-memory records while preserving:
        - Memory-only records
        - Sorted order
        - No duplicates (DB overwrites memory)

        This requested load range is extended to include the first record < start_timestamp
        and the first record >= end_timestamp, so nearest-neighbor searches do not require
        additional DB lookups.

        The `_db_loaded_range` is updated to reflect the total timestamp span
        currently present in memory after this method completes.

        Args:
            start_timestamp: Load records from this timestamp (inclusive)
            end_timestamp: Load records until this timestamp (exclusive)

        Returns:
            Number of records loaded from database

        Note:
            record.date_time shall be DateTime or None
        """
        # Defensive call - model_post_init() may not have initialized metadata
        self._db_ensure_initialized()

        if not self.db_enabled:
            return 0

        # Normalize boundaries immediately (strict DB layer rule)
        if start_timestamp is None:
            start_timestamp = UNBOUND_START
        if end_timestamp is None:
            end_timestamp = UNBOUND_END

        # Extend boundaries to include first record < start and first record >= end
        query_start, query_end = self._extend_boundaries(start_timestamp, end_timestamp)

        if isinstance(query_start, _DatabaseTimestampUnbound):
            start_key = None
        else:
            start_key = self._db_key_from_timestamp(query_start)
        if isinstance(query_end, _DatabaseTimestampUnbound):
            end_key = None
        else:
            end_key = self._db_key_from_timestamp(query_end)

        namespace = self.db_namespace()

        loaded_count = 0

        # Iterate DB records (already sorted by key)
        for db_key, value in self.database.iterate_records(
            start_key=start_key,
            end_key=end_key,
            namespace=namespace,
        ):
            if db_key == DATABASE_METADATA_KEY:
                continue

            record = self._db_deserialize_record(value)
            db_record_date_time = DatabaseTimestamp.from_datetime(record.date_time)

            # Do not resurrect explicitly deleted records
            if db_record_date_time in self._db_deleted_timestamps:
                continue

            # ---- Memory is authoritative: skip if already present
            if db_record_date_time in self._db_record_index:
                continue

            # Insert sorted
            # - do not call self.db_insert_record - may call db_load_records recursively
            # - see self.db_insert_record(record, mark_dirty=False)
            index = bisect.bisect_left(self._db_sorted_timestamps, db_record_date_time)
            self._db_sorted_timestamps.insert(index, db_record_date_time)
            self.records.insert(index, record)
            self._db_record_index[db_record_date_time] = record

            loaded_count += 1

        # Update range of timestamps the was already queried from database storage during load
        if self._db_loaded_range is None:
            # First load - initialize
            self._db_loaded_range = query_start, query_end
        else:
            current_start, current_end = self._db_loaded_range
            if query_start < current_start:
                current_start = query_start
            if query_end > current_end:
                current_end = query_end
            self._db_loaded_range = current_start, current_end

        return loaded_count

    # -----------------------------------------------------
    # Delete (range)
    # -----------------------------------------------------

    def db_delete_records(
        self,
        start_timestamp: Optional[DatabaseTimestampType] = None,
        end_timestamp: Optional[DatabaseTimestampType] = None,
    ) -> int:
        # Defensive call - model_post_init() may not have initialized metadata
        self._db_ensure_initialized()

        # Deletion is global — ensure we see everything
        self._db_ensure_loaded(
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
        )

        to_delete: list[DatabaseTimestamp] = []

        for dt in list(self._db_sorted_timestamps):
            if start_timestamp and dt < start_timestamp:
                continue
            if end_timestamp and dt >= end_timestamp:
                continue
            to_delete.append(dt)

        for dt in to_delete:
            record = self._db_record_index.pop(dt, None)
            if record is not None:
                idx = bisect.bisect_left(self._db_sorted_timestamps, dt)
                if idx < len(self._db_sorted_timestamps) and self._db_sorted_timestamps[idx] == dt:
                    self._db_sorted_timestamps.pop(idx)
                try:
                    self.records.remove(record)
                except Exception as ex:
                    logger.debug(f"Failed to remove record: {ex}")

            # Mark for physical deletion
            self._db_deleted_timestamps.add(dt)

            # If it was dirty (new record), cancel the insert instead
            self._db_dirty_timestamps.discard(dt)
            self._db_new_timestamps.discard(dt)

        return len(to_delete)

    # -----------------------------------------------------
    # Iteration from DB (no duplicates)
    # -----------------------------------------------------

    def db_iterate_records(
        self,
        start_timestamp: Optional[DatabaseTimestampType] = None,
        end_timestamp: Optional[DatabaseTimestampType] = None,
    ) -> Iterator[T_Record]:
        """Iterate records in requested range.

        Ensures storage is loaded into memory first,
        then iterates over in-memory records only.
        """
        # Defensive call - model_post_init() may not have initialized metadata
        self._db_ensure_initialized()

        # Ensure memory contains required range
        self._db_ensure_loaded(
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
        )

        for record in self.records:
            record_date_time_timestamp = DatabaseTimestamp.from_datetime(record.date_time)

            if start_timestamp and record_date_time_timestamp < start_timestamp:
                continue

            if end_timestamp and record_date_time_timestamp >= end_timestamp:
                break

            if record_date_time_timestamp in self._db_deleted_timestamps:
                continue

            yield record

    # -----------------------------------------------------
    # Dirty tracking
    # -----------------------------------------------------

    def db_mark_dirty_record(self, record: T_Record) -> None:
        # Defensive call - model_post_init() may not have initialized metadata
        self._db_ensure_initialized()

        record_date_time_timestamp = DatabaseTimestamp.from_datetime(record.date_time)
        self._db_dirty_timestamps.add(record_date_time_timestamp)

    # -----------------------------------------------------
    # Bulk save (flush dirty only)
    # -----------------------------------------------------

    def db_save_records(self) -> int:
        # Defensive call - model_post_init() may not have initialized metadata
        self._db_ensure_initialized()

        if not self.db_enabled:
            return 0

        if not self._db_dirty_timestamps and not self._db_deleted_timestamps:
            return 0

        namespace = self.db_namespace()

        # safer order: saves first, deletes last

        # --- handle inserts/updates ---
        save_items = []
        for dt in self._db_dirty_timestamps:
            record = self._db_record_index.get(dt)
            if record:
                key = self._db_key_from_timestamp(dt)
                value = self._db_serialize_record(record)
                save_items.append((key, value))
        saved_count = len(save_items)
        if saved_count:
            self.database.save_records(save_items, namespace=namespace)
        self._db_dirty_timestamps.clear()
        self._db_new_timestamps.clear()

        # --- handle deletions ---
        if self._db_deleted_timestamps:
            delete_keys = [self._db_key_from_timestamp(dt) for dt in self._db_deleted_timestamps]
            self.database.delete_records(delete_keys, namespace=namespace)
        deleted_count = len(self._db_deleted_timestamps)
        self._db_deleted_timestamps.clear()

        return saved_count + deleted_count

    def db_autosave(self) -> int:
        return self.db_save_records()

    def db_vacuum(
        self,
        keep_hours: Optional[int] = None,
        keep_timestamp: Optional[DatabaseTimestampType] = None,
    ) -> int:
        """Remove old records from database to free space.

        Semantics:

        - keep_hours is relative to the DB's max timestamp: cutoff = db_max - keep_hours, and records
            with timestamp < cutoff are deleted.
        - keep_timestamp is an absolute cutoff; records with timestamp < cutoff are deleted (exclusive).

        Uses self.keep_duration() if both of keep_hours and keep_timestamp are None.

        Args:
            keep_hours: Keep only records from the last N hours (relative to the data's max timestamp)
            keep_timestamp: Keep only records from this timestamp on (absolute cutoff)

        Returns:
            Number of records deleted
        """
        # Defensive call - model_post_init() may not have initialized metadata
        self._db_ensure_initialized()

        if keep_hours is None and keep_timestamp is None:
            keep_duration = self.db_keep_duration()
            if keep_duration is None:
                # No vacuum if all is None
                logger.info(
                    f"Vacuum requested for database '{self.db_namespace()}' but keep limit is infinite."
                )
                return 0
            keep_hours = keep_duration.hours

        if keep_hours is not None:
            _, db_max = self.db_timestamp_range()
            if db_max is None or isinstance(db_max, _DatabaseTimestampUnbound):
                # No records
                return 0  # nothing to delete
            if keep_hours <= 0:
                db_cutoff_timestamp: DatabaseTimestampType = UNBOUND_END
            else:
                # cutoff = first record we want to delete; everything before is removed
                datetime_max: DateTime = DatabaseTimestamp.to_datetime(db_max)
                db_cutoff_timestamp = DatabaseTimestamp.from_datetime(
                    datetime_max.subtract(hours=keep_hours - 1)
                )
        elif keep_timestamp is not None:
            db_cutoff_timestamp = keep_timestamp
        else:
            raise ValueError("Must specify either keep_hours or keep_timestamp")

        # Delete records
        deleted_count = self.db_delete_records(end_timestamp=db_cutoff_timestamp)

        self.db_save_records()

        logger.info(
            f"Vacuumed {deleted_count} old records from database '{self.db_namespace()}' "
            f"(before {db_cutoff_timestamp})"
        )
        return deleted_count

    def db_count_records(self) -> int:
        """Return total logical number of records.

        Memory is authoritative. If DB is enabled but not fully loaded,
        we conservatively include storage-only records.
        """
        # Defensive call - model_post_init() may not have initialized metadata
        self._db_ensure_initialized()

        if not self.db_enabled:
            return len(self.records)

        # If fully loaded, memory is complete view
        if self._db_load_phase is DatabaseRecordProtocolLoadPhase.FULL:
            return len(self.records)

        storage_count = self.database.count_records(namespace=self.db_namespace())
        pending_deletes = len(self._db_deleted_timestamps)
        new_count = len(self._db_new_timestamps)

        return storage_count + new_count - pending_deletes

    def db_get_stats(self) -> dict:
        """Get comprehensive statistics about database storage.

        Returns:
            Dictionary with statistics
        """
        if not self.db_enabled:
            return {"enabled": False}

        ns = self.db_namespace()

        stats = {
            "enabled": True,
            "backend": self.database.__class__.__name__,
            "path": str(self.database.storage_path),
            "memory_records": len(self.records),
            "compression_enabled": self.database.compression,
            "keep_duration_h": self.config.database.keep_duration_h,
            "autosave_interval_sec": self.config.database.autosave_interval_sec,
            "total_records": self.database.count_records(namespace=ns),
        }

        # Add backend-specific stats
        stats.update(self.database.get_backend_stats(namespace=ns))

        min_timestamp, max_timestamp = self.db_timestamp_range()
        stats["timestamp_range"] = {
            "min": str(min_timestamp),
            "max": str(max_timestamp),
        }

        return stats

    # ==================== Tiered Compaction ====================

    def db_compact_tiers(self) -> list[tuple[Duration, Duration]]:
        """Compaction tiers as (age_threshold, target_interval) pairs.

        Records older than age_threshold are downsampled to target_interval.
        Tiers must be ordered from shortest to longest age threshold.

        Default policy:

            - older than 2 hours  → 15 min resolution
            - older than 14 days  → 1 hour resolution

        Return empty list to disable compaction entirely.
        Override in derived classes for domain-specific behaviour.

        Example override to disable:

            .. code-block python

                def db_compact_tiers(self):
                    return []

        Example override for price data (already at 15 min, skip first tier):

            .. code-block python

                def db_compact_tiers(self):
                    return [
                        (to_duration("2 weeks"), to_duration("1 hour")),
                    ]

        .. comment
        """
        return [
            (to_duration("2 hours"), to_duration("15 minutes")),
            (to_duration("14 days"), to_duration("1 hour")),
        ]

    # ------------------------------------------------------------------
    # Compaction state helpers (stored in namespace metadata)
    # ------------------------------------------------------------------

    def _db_get_compact_state(
        self,
        tier_interval: Duration,
    ) -> Optional[DatabaseTimestamp]:
        """Load the last compaction cutoff timestamp for a given tier interval.

        Args:
            tier_interval: The target interval that identifies this tier.

        Returns:
            The last cutoff DatabaseTimestamp, or None if never compacted.
        """
        if self._db_metadata is None:
            return None
        key = f"last_compact_cutoff_{int(tier_interval.total_seconds())}"
        cutoff_str = self._db_metadata.get(key)
        return DatabaseTimestamp(cutoff_str) if cutoff_str else None

    def _db_set_compact_state(
        self,
        tier_interval: Duration,
        cutoff_ts: DatabaseTimestamp,
    ) -> None:
        """Persist the last compaction cutoff timestamp for a given tier interval.

        Args:
            tier_interval: The target interval that identifies this tier.
            cutoff_ts: The cutoff timestamp to store.
        """
        if self._db_metadata is None:
            self._db_metadata = {}
        key = f"last_compact_cutoff_{int(tier_interval.total_seconds())}"
        self._db_metadata[key] = str(cutoff_ts)
        self._db_save_metadata(self._db_metadata)

    # ------------------------------------------------------------------
    # Single-tier worker
    # ------------------------------------------------------------------

    def _db_compact_tier(
        self,
        age_threshold: Duration,
        target_interval: Duration,
    ) -> int:
        """Downsample records older than age_threshold to target_interval resolution.

        Only processes the window [last_compact_cutoff, new_cutoff) so repeated
        runs are cheap.

        The window boundaries are snapped to UTC epoch-aligned interval boundaries
        before processing:

        - ``window_start`` is floored to the nearest interval boundary at or before
            the raw start.  This guarantees that the first resampled bucket always
            sits on a clock-round timestamp (e.g. :00/:15/:30/:45 for 15 min) and
            that consecutive runs produce gapless, non-overlapping coverage.
        - ``window_end`` (the new cutoff stored in metadata) is also floored, so
            the boundary stored in metadata is always interval-aligned.  Records
            between the floored cutoff and the raw cutoff (``newest - age_threshold``)
            are left untouched and will be picked up on the next run once more data
            arrives and the floored cutoff advances.

        Skips resampling entirely when the existing record count is already at or
        below the number of buckets resampling would produce (sparse-data guard).
        When data is sparse but timestamps are misaligned the guard is bypassed and
        timestamps are snapped to interval boundaries without changing values.

        Args:
            age_threshold: Records older than (newest - age_threshold) are compacted.
            target_interval: Target resolution after compaction.

        Returns:
            Number of original records deleted (before re-insertion of downsampled
            records). Returns 0 if skipped.
        """
        self._db_ensure_initialized()

        interval_sec = int(target_interval.total_seconds())
        if interval_sec <= 0:
            return 0

        # ---- Determine raw new cutoff ------------------------------------
        _, db_max = self.db_timestamp_range()
        if db_max is None or isinstance(db_max, _DatabaseTimestampUnbound):
            return 0

        newest_dt = DatabaseTimestamp.to_datetime(db_max)
        raw_cutoff_dt = newest_dt - age_threshold

        # Snap new_cutoff DOWN to the nearest interval boundary.
        # Records in [floored_cutoff, raw_cutoff) are left alone until the next
        # run — they are inside the age window but straddle an incomplete bucket.
        raw_cutoff_epoch = int(raw_cutoff_dt.timestamp())
        floored_cutoff_epoch = (raw_cutoff_epoch // interval_sec) * interval_sec
        new_cutoff_dt = DateTime.fromtimestamp(floored_cutoff_epoch, tz="UTC")
        new_cutoff_ts = DatabaseTimestamp.from_datetime(new_cutoff_dt)

        # ---- Determine window start (incremental) ------------------------
        last_cutoff_ts = self._db_get_compact_state(target_interval)

        if last_cutoff_ts is not None and last_cutoff_ts >= new_cutoff_ts:
            logger.debug(
                f"Namespace '{self.db_namespace()}' tier {target_interval} already "
                f"compacted up to {new_cutoff_ts}, skipping."
            )
            return 0

        db_min, _ = self.db_timestamp_range()
        if db_min is None or isinstance(db_min, _DatabaseTimestampUnbound):
            return 0

        # Raw window start: last cutoff or absolute db minimum
        raw_window_start_ts = last_cutoff_ts if last_cutoff_ts is not None else db_min
        if raw_window_start_ts >= new_cutoff_ts:
            return 0

        raw_window_start_dt = DatabaseTimestamp.to_datetime(raw_window_start_ts)

        # Snap window_start DOWN to the nearest interval boundary so the first
        # resampled bucket is clock-aligned. This may pull the window slightly
        # earlier than the last stored cutoff, which is safe: key_to_array with
        # boundary="strict" only reads the window we pass and the re-insert step
        # is idempotent for already-compacted records (they will simply be
        # overwritten with the same values).
        raw_start_epoch = int(raw_window_start_dt.timestamp())
        floored_start_epoch = (raw_start_epoch // interval_sec) * interval_sec
        window_start_dt = DateTime.fromtimestamp(floored_start_epoch, tz="UTC")
        window_start_ts = DatabaseTimestamp.from_datetime(window_start_dt)

        window_end_dt = new_cutoff_dt  # exclusive upper bound, already aligned
        window_end_ts = new_cutoff_ts

        # ---- Sparse-data guard -------------------------------------------
        existing_count = self.database.count_records(
            start_key=self._db_key_from_timestamp(window_start_ts),
            end_key=self._db_key_from_timestamp(window_end_ts),
            namespace=self.db_namespace(),
        )

        window_sec = int((window_end_dt - window_start_dt).total_seconds())
        # Maximum number of buckets resampling could produce (ceiling division)
        resampled_count = (window_sec + interval_sec - 1) // interval_sec

        if existing_count == 0:
            # Nothing in window — just advance the cutoff
            self._db_set_compact_state(target_interval, new_cutoff_ts)
            return 0

        if existing_count <= resampled_count:
            # Data is already sparse — check whether timestamps are aligned.
            # If every record already sits on an interval boundary, nothing to do.
            # If any are misaligned, snap them in place without resampling.
            records_in_window = [
                r
                for r in self.records
                if r.date_time is not None and window_start_dt <= r.date_time < window_end_dt
            ]
            misaligned = [
                r for r in records_in_window if int(r.date_time.timestamp()) % interval_sec != 0
            ]
            if not misaligned:
                logger.debug(
                    f"Skipping tier {target_interval} compaction for "
                    f"namespace '{self.db_namespace()}': "
                    f"existing={existing_count} <= resampled={resampled_count} "
                    f"and all timestamps already aligned "
                    f"(window={window_start_dt}..{window_end_dt})"
                )
                self._db_set_compact_state(target_interval, new_cutoff_ts)
                return 0

            # ---- Sparse but misaligned: full window rewrite -----------------
            # Delete the entire window and reinsert floor-snapped records.
            # Deleting first guarantees no duplicate-timestamp ValueError on
            # reinsert, even when an already-aligned record sits at the same
            # epoch that a misaligned record floors to.
            logger.debug(
                f"Rewriting sparse window in namespace '{self.db_namespace()}' "
                f"tier {target_interval} (existing={existing_count}, "
                f"resampled={resampled_count})"
            )

            # Build snapped buckets from ALL records in window.
            # Process chronologically so the earliest record's values win when
            # multiple records floor to the same bucket.
            snapped_bucket: dict[int, dict[str, Any]] = {}
            for r in sorted(records_in_window, key=lambda x: x.date_time):
                ts_epoch = int(r.date_time.timestamp())
                snapped_epoch = (ts_epoch // interval_sec) * interval_sec
                bucket = snapped_bucket.setdefault(snapped_epoch, {})
                for key in self.record_keys_writable:
                    if key == "date_time":
                        continue
                    try:
                        val = r[key]
                    except KeyError:
                        continue
                    if val is not None and bucket.get(key) is None:
                        bucket[key] = val

            # Delete entire window (aligned + misaligned)
            deleted = self.db_delete_records(
                start_timestamp=window_start_ts,
                end_timestamp=window_end_ts,
            )

            # Reinsert one record per bucket
            for snapped_epoch, values in snapped_bucket.items():
                if not values:
                    continue
                snapped_dt = DateTime.fromtimestamp(snapped_epoch, tz="UTC")
                record = self.record_class()(date_time=snapped_dt, **values)
                self.db_insert_record(record, mark_dirty=True)

            self.db_save_records()
            self._db_set_compact_state(target_interval, new_cutoff_ts)
            logger.info(
                f"Rewrote sparse window in namespace '{self.db_namespace()}' "
                f"tier {target_interval}: deleted={deleted}, "
                f"reinserted={len(snapped_bucket)} buckets "
                f"(window={window_start_dt}..{window_end_dt})"
            )
            return deleted

        # ---- Full resampling path ----------------------------------------
        # boundary="context" is used here instead of "strict" so that key_to_array
        # can include one record on each side of the window for proper interpolation
        # at the edges. The truncation inside key_to_array then clips the result
        # back to [window_start_dt, window_end_dt) so no out-of-window values are
        # ever written back. align_to_interval=True ensures buckets land on
        # clock-round timestamps regardless of window_start_dt precision.
        compacted_data: dict[str, Any] = {}
        compacted_timestamps: list[DateTime] = []

        for key in self.record_keys_writable:
            if key == "date_time":
                continue
            try:
                array = self.key_to_array(
                    key,
                    start_datetime=window_start_dt,
                    end_datetime=window_end_dt,
                    interval=target_interval,
                    fill_method="time",
                    boundary="context",
                    align_to_interval=True,
                )
            except (KeyError, TypeError, ValueError):
                continue  # non-numeric or missing key — skip silently

            if len(array) == 0:
                continue

            # Build the shared timestamp spine once from the first successful key.
            # The spine is derived from the actual resampled index, not from
            # db_generate_timestamps, so it matches exactly what key_to_array
            # produced (epoch-aligned, truncated to window).
            if not compacted_timestamps:
                raw_start_epoch_aligned = (
                    int(window_start_dt.timestamp()) // interval_sec
                ) * interval_sec
                first_bucket_epoch = raw_start_epoch_aligned
                # Advance to first bucket >= window_start_dt (truncation in key_to_array
                # removes any bucket before window_start_dt)
                while first_bucket_epoch < int(window_start_dt.timestamp()):
                    first_bucket_epoch += interval_sec
                compacted_timestamps = [
                    DateTime.fromtimestamp(first_bucket_epoch + i * interval_sec, tz="UTC")
                    for i in range(len(array))
                ]

            # Guard against length mismatch between keys
            if len(array) == len(compacted_timestamps):
                compacted_data[key] = array

        if not compacted_data or not compacted_timestamps:
            # Nothing to write back — still advance cutoff
            self._db_set_compact_state(target_interval, new_cutoff_ts)
            return 0

        # ---- Delete originals, re-insert downsampled records -------------
        deleted = self.db_delete_records(
            start_timestamp=window_start_ts,
            end_timestamp=window_end_ts,
        )

        for i, dt in enumerate(compacted_timestamps):
            values = {
                key: arr[i]
                for key, arr in compacted_data.items()
                if i < len(arr) and arr[i] is not None
            }
            if values:
                record = self.record_class()(date_time=dt, **values)
                self.db_insert_record(record, mark_dirty=True)

        self.db_save_records()

        # Persist the aligned new cutoff for this tier
        self._db_set_compact_state(target_interval, new_cutoff_ts)

        logger.info(
            f"Compacted tier {target_interval}: deleted {deleted} records in "
            f"namespace '{self.db_namespace()}' "
            f"(window={window_start_dt}..{window_end_dt}, "
            f"reinserted={len(compacted_timestamps)})"
        )
        return deleted

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def db_compact(
        self,
        compact_tiers: Optional[list[tuple[Duration, Duration]]] = None,
    ) -> int:
        """Apply tiered compaction policy to all records in this namespace.

        Tiers are processed coarsest-first (longest age threshold first) to
        avoid compacting fine-grained data that an inner tier would immediately
        re-compact anyway.

        Args:
            compact_tiers: Override tiers for this call. If None, uses
                db_compact_tiers(). Each entry is (age_threshold, target_interval),
                ordered shortest to longest age threshold.

        Returns:
            Total number of original records deleted across all tiers.
        """
        if compact_tiers is None:
            compact_tiers = self.db_compact_tiers()

        if not compact_tiers:
            return 0

        total_deleted = 0

        # Coarsest tier first (reversed) to avoid redundant work
        for age_threshold, target_interval in reversed(compact_tiers):
            total_deleted += self._db_compact_tier(age_threshold, target_interval)

        return total_deleted
