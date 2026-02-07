"""Database persistence extension for data records with plugin architecture.

Provides an abstract database interface and concrete implementations for various
backends. This version exposes first-class "namespace" support: the DataDB
abstract interface and concrete implementations accept an optional `namespace`
argument on methods. LMDB uses named DBIs for namespaces; SQLite emulates
namespaces with a `namespace` column.
"""

from __future__ import annotations

import gzip
import shutil
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterator, List, Optional, Tuple

import lmdb
from loguru import logger
from pydantic import Field, computed_field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import ConfigMixin, SingletonMixin

# Key used to store metadata
DATADB_METADATA_KEY: bytes = b"__metadata__"

# Valid database providers
database_providers: List[str] = ["LMDB", "SQLite"]


class DatabaseCommonSettings(SettingsBaseModel):
    """Configuration model for database settings.

    Attributes:
        provider: Optional provider identifier (e.g. "LMDB").
        max_records_in_memory: Maximum records kept in memory before auto-save.
        auto_save: Whether to auto-save when threshold exceeded.
        batch_size: Batch size for batch operations.
    """

    provider: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": "Database provider id of provider to be used.",
            "examples": ["LMDB"],
        },
    )

    compression_level: int = Field(
        default=9,
        ge=0,
        le=9,
        json_schema_extra={
            "description": "Compression level for database record data.",
            "examples": [0, 9],
        },
    )

    max_records_in_memory: int = Field(
        default=1000,
        json_schema_extra={
            "description": "Maximum records to keep in memory before auto-save.",
            "examples": [1000],
        },
    )

    auto_save: bool = Field(
        default=True,
        json_schema_extra={
            "description": "Enable automatic saving when threshold exceeded.",
            "examples": [True],
        },
    )

    batch_size: int = Field(
        default=100,
        json_schema_extra={
            "description": "Number of records to process in batch operations.",
            "examples": [100],
        },
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def providers(self) -> List[str]:
        """Return available database provider ids."""
        return database_providers

    @field_validator("provider", mode="after")
    @classmethod
    def validate_provider(cls, value: Optional[str]) -> Optional[str]:
        """Validate provider is in allowed list.

        Args:
            value: provider value to validate.

        Returns:
            The validated provider or None.

        Raises:
            ValueError: if provider is not in the allowed list.
        """
        if value is None or value in database_providers:
            return value
        raise ValueError(
            f"Provider '{value}' is not a valid database provider: {database_providers}."
        )


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

    # Basic record operations

    @abstractmethod
    def save_record(self, key: bytes, value: bytes, namespace: Optional[str] = None) -> None:
        """Save a single record into the specified namespace (or default).

        Args:
            key: Byte key (sortable) for the record.
            value: Serialized (and optionally compressed) bytes to store.
            namespace: Optional namespace.

        Raises:
            RuntimeError: If DB not open or write failed.
        """
        raise NotImplementedError

    @abstractmethod
    def load_records(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
        namespace: Optional[str] = None,
    ) -> Iterator[Tuple[bytes, bytes]]:
        """Yield records in [start_key, end_key) for the specified namespace.

        Args:
            start_key: Inclusive start key or None.
            end_key: Exclusive end key or None.
            namespace: Optional namespace.

        Yields:
            Tuples of (key, value) ordered by key.
        """
        raise NotImplementedError

    @abstractmethod
    def delete_record(self, key: bytes, namespace: Optional[str] = None) -> bool:
        """Delete a record by key from the specified namespace.

        Args:
            key: Byte key to delete.
            namespace: Optional namespace.

        Returns:
            True if a record was deleted, False otherwise.
        """
        raise NotImplementedError

    @abstractmethod
    def count_records(self, namespace: Optional[str] = None) -> int:
        """Return the number of records in the specified namespace (excluding metadata)."""
        raise NotImplementedError

    @abstractmethod
    def get_key_range(
        self, namespace: Optional[str] = None
    ) -> Tuple[Optional[bytes], Optional[bytes]]:
        """Return (min_key, max_key) in the given namespace or (None, None) if empty."""
        raise NotImplementedError

    @abstractmethod
    def iterate_records(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
        namespace: Optional[str] = None,
        reverse: bool = False,
    ) -> Iterator[Tuple[bytes, bytes]]:
        """Iterate over records for a namespace with optional bounds.

        Args:
            start_key: Inclusive start key, or None.
            end_key: Exclusive end key, or None.
            namespace: Optional namespace to target.
            reverse: If True iterate in descending key order.

        Yields:
            Tuples of (key, value).
        """
        raise NotImplementedError

    @abstractmethod
    def flush(self, namespace: Optional[str] = None) -> None:
        """Force synchronization of pending writes to storage (optional per-namespace)."""
        raise NotImplementedError

    @abstractmethod
    def get_backend_stats(self, namespace: Optional[str] = None) -> Dict[str, Any]:
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


# ==================== LMDB Implementation ====================


class LMDBDatabase(DatabaseBackendABC):
    """LMDB implementation using named DBIs for namespaces."""

    env: Optional[lmdb.Environment]
    _dbis: Dict[Optional[str], Optional[Any]]

    def __init__(
        self,
        map_size: int = 10 * 1024 * 1024 * 1024,
        **kwargs: Any,
    ) -> None:
        """Initialize LMDB backend.

        Args:
            storage_path: directory to store LMDB files.
            compression: whether to compress values.
            compression_level: gzip compression level.
            map_size: maximum LMDB map size.
        """
        super().__init__()
        self.map_size = map_size
        self.env = None
        self._dbis = {None: None}

    def provider_id(self) -> str:
        """Return the unique identifier for the database provider."""
        return "LMDB"

    def open(self, namespace: Optional[str] = None) -> None:
        """Open LMDB environment and optionally ensure a namespace DBI.

        Args:
            namespace: Optional default namespace to open (DBI created on demand).
        """
        self.storage_path.mkdir(parents=True, exist_ok=True)

        self.env = lmdb.open(
            str(self.storage_path),
            map_size=self.map_size,
            max_dbs=128,
            writemap=True,
            map_async=True,
            metasync=False,
            sync=False,
            lock=True,
        )

        self.connection = self.env
        self._is_open = True
        self.default_namespace = namespace

        if namespace is not None:
            self._ensure_dbi(namespace)

        logger.debug("Opened LMDB at %s (default_namespace=%s)", self.storage_path, namespace)

    def close(self) -> None:
        """Close the LMDB environment and clear cached DBIs."""
        if self.env:
            self.env.sync()
            self.env.close()
            self.env = None
            self.connection = None
            self._is_open = False
            self._dbis.clear()
            logger.debug("Closed LMDB at %s", self.storage_path)

    # DBI helpers

    def _normalize_namespace(self, namespace: Optional[str]) -> Optional[str]:
        """Return explicit namespace or default if None."""
        return namespace if namespace is not None else self.default_namespace

    def _ensure_dbi(self, namespace: Optional[str]) -> Optional[Any]:
        """Open and cache a DBI for the given namespace.

        Args:
            namespace: Namespace name or None for the unnamed DB.

        Returns:
            DBI handle (implementation specific) or None for unnamed DB.
        """
        if not isinstance(self.env, lmdb.Environment):
            raise ValueError(f"LMDB Environment is of wrong tpe `{type(self.env)}`.")

        name = self._normalize_namespace(namespace)
        if name in self._dbis:
            return self._dbis[name]

        if name is None:
            dbi = None
        else:
            nm = name.encode("utf-8")
            dbi = self.env.open_db(nm, create=True)
        self._dbis[name] = dbi
        return dbi

    # Low-level operations

    def save_record(self, key: bytes, value: bytes, namespace: Optional[str] = None) -> None:
        """Save a key/value in the chosen namespace DBI.

        Args:
            key: Byte key.
            value: Byte value.
            namespace: Optional namespace.
        """
        if not isinstance(self.env, lmdb.Environment):
            raise ValueError(f"LMDB Environment is of wrong tpe `{type(self.env)}`.")

        dbi = self._ensure_dbi(namespace)
        with self.lock:
            with self.env.begin(write=True) as txn:
                txn.put(key, value, db=dbi)

    def load_records(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
        namespace: Optional[str] = None,
    ) -> Iterator[Tuple[bytes, bytes]]:
        """Yield records in [start_key, end_key) from the chosen DBI.

        Args:
            start_key: Inclusive start key.
            end_key: Exclusive end key.
            namespace: Optional namespace.

        Yields:
            Tuples of (key, value).
        """
        if not isinstance(self.env, lmdb.Environment):
            raise ValueError(f"LMDB Environment is of wrong tpe `{type(self.env)}`.")

        dbi = self._ensure_dbi(namespace)

        with self.env.begin(write=False) as txn:
            cursor = txn.cursor(db=dbi)

            if start_key:
                if not cursor.set_range(start_key):
                    return
            else:
                if not cursor.first():
                    return

            for k, v in cursor:
                if end_key and k >= end_key:
                    break
                yield k, v

    def delete_record(self, key: bytes, namespace: Optional[str] = None) -> bool:
        """Delete a key in the specified DBI.

        Returns:
            True if deleted, False otherwise.
        """
        if not isinstance(self.env, lmdb.Environment):
            raise ValueError(f"LMDB Environment is of wrong tpe `{type(self.env)}`.")

        dbi = self._ensure_dbi(namespace)
        with self.lock:
            with self.env.begin(write=True) as txn:
                return txn.delete(key, db=dbi)

    def count_records(self, namespace: Optional[str] = None) -> int:
        """Return number of entries in the DBI, excluding metadata if present."""
        if not isinstance(self.env, lmdb.Environment):
            raise ValueError(f"LMDB Environment is of wrong tpe `{type(self.env)}`.")

        dbi = self._ensure_dbi(namespace)
        with self.env.begin(write=False) as txn:
            stat = txn.stat(db=dbi)
            count = int(stat.get("entries", 0))
            if txn.get(DATADB_METADATA_KEY, db=dbi) is not None:
                count -= 1
            return count

    def get_key_range(
        self, namespace: Optional[str] = None
    ) -> Tuple[Optional[bytes], Optional[bytes]]:
        """Return (min_key, max_key) for the DBI, or (None, None) if empty."""
        if not isinstance(self.env, lmdb.Environment):
            raise ValueError(f"LMDB Environment is of wrong tpe `{type(self.env)}`.")

        dbi = self._ensure_dbi(namespace)

        with self.env.begin(write=False) as txn:
            cursor = txn.cursor(db=dbi)

            if not cursor.first():
                return None, None

            min_key = cursor.key()
            if min_key == DATADB_METADATA_KEY:
                if not cursor.next():
                    return None, None
                min_key = cursor.key()

            cursor.last()
            max_key = cursor.key()
            if max_key == DATADB_METADATA_KEY:
                if not cursor.prev():
                    return None, None
                max_key = cursor.key()

            return min_key, max_key

    def iterate_records(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
        namespace: Optional[str] = None,
        reverse: bool = False,
    ) -> Iterator[Tuple[bytes, bytes]]:
        """Iterate records for a DBI without holding a Python lock across yields.

        The LMDB read-only transaction provides a snapshot; callers should be aware
        they're iterating a snapshot of the DB at the transaction start.

        Args:
            start_key: Inclusive start key, or None.
            end_key: Exclusive end key, or None.
            namespace: Optional namespace.
            reverse: If True iterate descending.

        Yields:
            Tuples of (key, value) for the selected DBI.
        """
        if not isinstance(self.env, lmdb.Environment):
            raise ValueError(f"LMDB Environment is of wrong tpe `{type(self.env)}`.")

        dbi = self._ensure_dbi(namespace)
        METADATA_KEY = DATADB_METADATA_KEY

        with self.env.begin(write=False) as txn:
            cursor = txn.cursor(db=dbi)

            if reverse:
                if end_key is not None:
                    found = cursor.set_range(end_key)
                    if not found:
                        if not cursor.last():
                            return
                    else:
                        if cursor.key() >= end_key:
                            if not cursor.prev():
                                return
                else:
                    if not cursor.last():
                        return

                while True:
                    key = cursor.key()
                    value = cursor.value()
                    if start_key is not None and key < start_key:
                        break
                    if key != METADATA_KEY:
                        yield key, value
                    if not cursor.prev():
                        break
            else:
                if start_key is not None:
                    found = cursor.set_range(start_key)
                    if not found:
                        return
                else:
                    if not cursor.first():
                        return

                while True:
                    key = cursor.key()
                    value = cursor.value()
                    if end_key is not None and key >= end_key:
                        break
                    if key != METADATA_KEY:
                        yield key, value
                    if not cursor.next():
                        break

    def flush(self, namespace: Optional[str] = None) -> None:
        """Sync LMDB environment (writes to disk)."""
        if not isinstance(self.env, lmdb.Environment):
            raise ValueError(f"LMDB Environment is of wrong tpe `{type(self.env)}`.")

        with self.lock:
            self.env.sync()

    def get_backend_stats(self, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Return LMDB-specific statistics with optional namespace annotation."""
        if not self.env:
            return {}
        dbi = self._ensure_dbi(namespace)
        with self.env.begin(write=False) as txn:
            stat = txn.stat(db=dbi)
            info = self.env.info()
            return {
                "backend": "lmdb",
                "entries": int(stat.get("entries", 0)),
                "page_size": stat.get("psize"),
                "depth": stat.get("depth"),
                "branch_pages": stat.get("branch_pages"),
                "leaf_pages": stat.get("leaf_pages"),
                "overflow_pages": stat.get("overflow_pages"),
                "map_size": info.get("map_size"),
                "last_pgno": info.get("last_pgno"),
                "last_txnid": info.get("last_txnid"),
                "namespace": namespace or self.default_namespace,
            }

    def compact(self) -> None:
        """Compact LMDB by copying a compact snapshot and atomically replacing files.

        Raises:
            RuntimeError: If the environment is not open.
        """
        if not self.env:
            raise RuntimeError("Database not open")

        logger.info("Starting LMDB compaction...")

        orig_path = Path(self.storage_path)
        backup_parent = orig_path.parent
        backup_dir = backup_parent / f"{orig_path.name}_compact_tmp"
        final_backup_dir = backup_parent / f"{orig_path.name}_compact"

        try:
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
            if final_backup_dir.exists():
                shutil.rmtree(final_backup_dir)
        except Exception:
            logger.exception("Failed to remove existing backup dirs before compaction")

        try:
            backup_dir.mkdir(parents=True, exist_ok=False)
            with self.lock:
                self.env.copy(str(backup_dir), compact=True)
            try:
                self.close()
            except Exception:
                logger.exception(
                    "Failed to close LMDB environment after copy; proceeding with replacement"
                )

            try:
                if orig_path.exists():
                    shutil.rmtree(orig_path)
                shutil.move(str(backup_dir), str(final_backup_dir))
                shutil.move(str(final_backup_dir), str(orig_path))
            except Exception as exc:
                logger.exception(
                    "Failed to replace original LMDB files with compacted copy: %s", exc
                )
                try:
                    if final_backup_dir.exists() and not orig_path.exists():
                        shutil.move(str(final_backup_dir), str(orig_path))
                except Exception:
                    logger.exception("Failed to restore original LMDB after failed replacement")
                raise

            try:
                self.open()
            except Exception:
                logger.exception("Failed to re-open LMDB after compaction; DB may be closed")
                raise

            logger.info("LMDB compaction completed successfully: %s", str(self.storage_path))
        finally:
            try:
                if backup_dir.exists():
                    shutil.rmtree(backup_dir)
                if final_backup_dir.exists():
                    shutil.rmtree(final_backup_dir)
            except Exception:
                logger.exception("Failed to clean up temporary backup directories after compaction")


# ==================== SQLite Implementation ====================


class SQLiteDatabase(DatabaseBackendABC):
    """SQLite implementation that stores a `namespace` column to emulate namespaces."""

    db_file: Path
    conn: Optional[Any]

    def __init__(self, **kwargs: Any) -> None:
        """Initialize SQLite backend."""
        super().__init__()
        self.db_file = self.storage_path / "data.db"
        self.conn = None

    def provider_id(self) -> str:
        """Return the unique identifier for the database provider."""
        return "SQLite"

    def open(self, namespace: Optional[str] = None) -> None:
        """Open SQLite connection and optionally set default namespace.

        Args:
            namespace: Optional default namespace to use when operations omit namespace.
        """
        self.storage_path.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(
            str(self.db_file),
            isolation_level=None,  # autocommit
            check_same_thread=False,
        )

        # Create table with namespace column and composite primary key (namespace, key)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                namespace TEXT NOT NULL DEFAULT '',
                key BLOB NOT NULL,
                value BLOB NOT NULL,
                PRIMARY KEY (namespace, key)
            )
            """
        )

        # Index to accelerate range queries per namespace
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_namespace_key ON records(namespace, key)")

        self.connection = self.conn
        self._is_open = True
        self.default_namespace = namespace
        logger.debug("Opened SQLite at %s (default_namespace=%s)", self.db_file, namespace)

    def close(self) -> None:
        """Close SQLite connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
            self.connection = None
            self._is_open = False
            logger.debug("Closed SQLite at %s", self.db_file)

    def _ns(self, namespace: Optional[str]) -> str:
        """Normalize namespace for storage ('' for None)."""
        return namespace if namespace is not None else (self.default_namespace or "")

    def save_record(self, key: bytes, value: bytes, namespace: Optional[str] = None) -> None:
        """Insert or replace a record in SQLite under the given namespace."""
        if not isinstance(self.conn, sqlite3.Connection):
            raise ValueError(f"SQLite connection is of wrong tpe `{type(self.conn)}`.")

        ns = self._ns(namespace)
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO records (namespace, key, value) VALUES (?, ?, ?)",
                (ns, key, value),
            )

    def load_records(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
        namespace: Optional[str] = None,
    ) -> Iterator[Tuple[bytes, bytes]]:
        """Yield records for a namespace in [start_key, end_key)."""
        if not isinstance(self.conn, sqlite3.Connection):
            raise ValueError(f"SQLite connection is of wrong tpe `{type(self.conn)}`.")

        ns = self._ns(namespace)
        params: List[Any] = [ns]
        where_clauses: List[str] = ["namespace = ?"]

        if start_key is not None:
            where_clauses.append("key >= ?")
            params.append(start_key)
        if end_key is not None:
            where_clauses.append("key < ?")
            params.append(end_key)

        where_sql = " AND ".join(where_clauses)
        sql = f"SELECT key, value FROM records WHERE {where_sql} ORDER BY key"  # noqa: S608

        with self.lock:
            cursor = self.conn.execute(sql, tuple(params))
            rows = cursor.fetchall()

        for k, v in rows:
            yield k, v

    def delete_record(self, key: bytes, namespace: Optional[str] = None) -> bool:
        """Delete a namespaced record from SQLite.

        Returns:
            True if a row was deleted.
        """
        if not isinstance(self.conn, sqlite3.Connection):
            raise ValueError(f"SQLite connection is of wrong tpe `{type(self.conn)}`.")

        ns = self._ns(namespace)
        with self.lock:
            cursor = self.conn.execute(
                "DELETE FROM records WHERE namespace = ? AND key = ?", (ns, key)
            )
            return cursor.rowcount > 0

    def count_records(self, namespace: Optional[str] = None) -> int:
        """Return count of records for the given namespace."""
        if not isinstance(self.conn, sqlite3.Connection):
            raise ValueError(f"SQLite connection is of wrong tpe `{type(self.conn)}`.")

        ns = self._ns(namespace)
        with self.lock:
            cursor = self.conn.execute(
                "SELECT COUNT(*) FROM records WHERE namespace = ? AND key != ?",
                (ns, DATADB_METADATA_KEY),
            )
            return int(cursor.fetchone()[0])

    def get_key_range(
        self, namespace: Optional[str] = None
    ) -> Tuple[Optional[bytes], Optional[bytes]]:
        """Return (min_key, max_key) for the namespace or (None, None) if empty."""
        if not isinstance(self.conn, sqlite3.Connection):
            raise ValueError(f"SQLite connection is of wrong tpe `{type(self.conn)}`.")

        ns = self._ns(namespace)
        with self.lock:
            cursor = self.conn.execute(
                "SELECT MIN(key), MAX(key) FROM records WHERE namespace = ? and key != ?",
                (ns, DATADB_METADATA_KEY),
            )
            result = cursor.fetchone()
            return result[0], result[1]

    def iterate_records(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
        namespace: Optional[str] = None,
        reverse: bool = False,
    ) -> Iterator[Tuple[bytes, bytes]]:
        """Snapshot-based iteration for a namespace.

        The implementation snapshots the query results while holding the lock and
        yields from the in-memory list after releasing the lock, so writers can
        acquire the lock concurrently.

        Args:
            start_key: Inclusive start key or None.
            end_key: Exclusive end key or None.
            namespace: Optional namespace.
            reverse: If True iterate descending.

        Yields:
            Tuples of (key, value).
        """
        if not isinstance(self.conn, sqlite3.Connection):
            raise ValueError(f"SQLite connection is of wrong tpe `{type(self.conn)}`.")

        order = "DESC" if reverse else "ASC"
        ns = self._ns(namespace)
        where_clauses: List[str] = ["namespace = ?", "key != ?"]
        params: List[Any] = [ns, DATADB_METADATA_KEY]

        if start_key is not None:
            where_clauses.append("key >= ?")
            params.append(start_key)
        if end_key is not None:
            where_clauses.append("key < ?")
            params.append(end_key)

        where_sql = " AND ".join(where_clauses)
        sql = f"SELECT key, value FROM records WHERE {where_sql} ORDER BY key {order}"  # noqa: S608

        with self.lock:
            cursor = self.conn.execute(sql, tuple(params))
            rows = cursor.fetchall()  # snapshot

        for k, v in rows:
            yield k, v

    def flush(self, namespace: Optional[str] = None) -> None:
        """Commit any pending transactions to disk (no-op if autocommit)."""
        if not isinstance(self.conn, sqlite3.Connection):
            raise ValueError(f"SQLite connection is of wrong tpe `{type(self.conn)}`.")

        with self.lock:
            self.conn.commit()

    def get_backend_stats(self, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Return SQLite-specific stats and namespace metrics."""
        if not self.conn:
            return {}
        ns = self._ns(namespace)
        with self.lock:
            cursor = self.conn.execute(
                "SELECT page_count, page_size FROM pragma_page_count(), pragma_page_size()"
            )
            page_count, page_size = cursor.fetchone()
            cursor = self.conn.execute("SELECT COUNT(*) FROM records WHERE namespace = ?", (ns,))
            namespace_count = int(cursor.fetchone()[0])
            return {
                "backend": "sqlite",
                "page_count": page_count,
                "page_size": page_size,
                "database_size": page_count * page_size,
                "file_path": str(self.db_file),
                "namespace": ns,
                "namespace_count": namespace_count,
            }

    def vacuum(self) -> None:
        """Run SQLite VACUUM to reduce file size."""
        if not self.conn:
            raise RuntimeError("Database not open")
        with self.lock:
            self.conn.execute("VACUUM")
        logger.info("SQLite vacuum completed")


# ==================== Generic Database Implementation ====================


class DataDB(DatabaseABC, SingletonMixin):
    """Generic database.

    All operations accept an optional `namespace` argument. Implementations should
    treat None as the default/root namespace. Concrete implementations can map
    namespace -> native namespace (LMDB DBI) or emulate namespaces (SQLite uses
    a namespace column).
    """

    _db: Optional[DatabaseBackendABC] = None

    @classmethod
    def reset_instance(cls) -> None:
        """Resets the singleton instance, forcing it to be recreated on next access."""
        with cls._lock:
            # Close current database backend
            if cls._db:
                cls._db.close()
                cls._db = None
            # Remove current database instance
            if cls in cls._instances:
                del cls._instances[cls]
                logger.debug(f"{cls.__name__} singleton instance has been reset.")

    def __init__(self) -> None:
        """Initialize database."""
        super().__init__()
        self._db = None

    def _setup_db(self) -> None:
        """Setup database."""
        provider_id = self.config.database.provider
        database: Optional[DatabaseBackendABC] = None
        if provider_id is None:
            database = None
        elif provider_id == "LMDB":
            database = LMDBDatabase()
        elif provider_id == "SQLite":
            database = SQLiteDatabase()
        else:
            raise RuntimeError("Invalid database provider '{provider_id}'")
        if self._db is not None:
            self._db.close()
        self._db = database

    def _database(self) -> DatabaseBackendABC:
        """Get database."""
        provider_id = self.config.database.provider
        if provider_id is None:
            raise RuntimeError("Database not configured")

        if self._db is None or self._db.provider_id() != provider_id:
            # No database or configuration does not match
            self._setup_db()
            if self._db is None:
                raise RuntimeError("Database not configured")

        if not self._db.is_open:
            self._db.open()

        return self._db

    def provider_id(self) -> str:
        """Return the unique identifier for the database provider."""
        try:
            return self._database().provider_id()
        except:
            return "None"

    @property
    def is_open(self) -> bool:
        """Return whether the database connection is open."""
        try:
            return self._database().is_open
        except:
            return False

    @property
    def storage_path(self) -> Path:
        """Storage path for the database."""
        return self._database().storage_path

    @property
    def compression_level(self) -> int:
        """Compression level for database record data."""
        return self._database().compression_level

    @property
    def compression(self) -> bool:
        """Whether to compress stored values."""
        return self._database().compression_level > 0

    # Lifecycle

    def open(self, namespace: Optional[str] = None) -> None:
        """Open database connection and optionally set default namespace.

        Args:
            namespace: Optional default namespace to prepare.

        Raises:
            RuntimeError: If the database cannot be opened.
        """
        self._database().open(namespace)

    def close(self) -> None:
        """Close the database connection and cleanup resources."""
        self._database().close()

    # Basic record operations

    def save_record(self, key: bytes, value: bytes, namespace: Optional[str] = None) -> None:
        """Save a single record into the specified namespace (or default).

        Args:
            key: Byte key (sortable) for the record.
            value: Serialized (and optionally compressed) bytes to store.
            namespace: Optional namespace.

        Raises:
            RuntimeError: If DB not open or write failed.
        """
        self._database().save_record(key, value, namespace)

    def load_records(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
        namespace: Optional[str] = None,
    ) -> Iterator[Tuple[bytes, bytes]]:
        """Yield records in [start_key, end_key) for the specified namespace.

        Args:
            start_key: Inclusive start key or None.
            end_key: Exclusive end key or None.
            namespace: Optional namespace.

        Yields:
            Tuples of (key, value) ordered by key.
        """
        return self._database().load_records(start_key, end_key, namespace)

    def delete_record(self, key: bytes, namespace: Optional[str] = None) -> bool:
        """Delete a record by key from the specified namespace.

        Args:
            key: Byte key to delete.
            namespace: Optional namespace.

        Returns:
            True if a record was deleted, False otherwise.
        """
        return self._database().delete_record(key, namespace)

    def count_records(self, namespace: Optional[str] = None) -> int:
        """Return the number of records in the specified namespace (excluding metadata)."""
        return self._database().count_records(namespace)

    def get_key_range(
        self, namespace: Optional[str] = None
    ) -> Tuple[Optional[bytes], Optional[bytes]]:
        """Return (min_key, max_key) in the given namespace or (None, None) if empty."""
        return self._database().get_key_range(namespace)

    def iterate_records(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
        namespace: Optional[str] = None,
        reverse: bool = False,
    ) -> Iterator[Tuple[bytes, bytes]]:
        """Iterate over records for a namespace with optional bounds.

        Args:
            start_key: Inclusive start key, or None.
            end_key: Exclusive end key, or None.
            namespace: Optional namespace to target.
            reverse: If True iterate in descending key order.

        Yields:
            Tuples of (key, value).
        """
        return self._database().iterate_records(start_key, end_key, namespace, reverse)

    def flush(self, namespace: Optional[str] = None) -> None:
        """Force synchronization of pending writes to storage (optional per-namespace)."""
        return self._database().flush(namespace)

    def get_backend_stats(self, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Get backend-specific statistics; implementations may return namespace-specific data."""
        return self._database().get_backend_stats(namespace)
