"""Database persistence extension for data records with plugin architecture.

Provides an abstract database interface and concrete implementations for various
backends. This version exposes first-class "namespace" support: the Database
abstract interface and concrete implementations accept an optional `namespace`
argument on methods. LMDB uses named DBIs for namespaces; SQLite emulates
namespaces with a `namespace` column.
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
from pathlib import Path
from typing import (
    Any,
    AsyncIterator,
    Dict,
    Iterable,
    Iterator,
    List,
    Optional,
    Tuple,
)

import lmdb
from loguru import logger
from pydantic import Field, computed_field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import ConfigMixin, SingletonMixin
from akkudoktoreos.core.databaseabc import (
    DATABASE_METADATA_KEY,
    DatabaseBackendABC,
)

# Valid database providers
database_providers: List[str] = ["LMDB", "SQLite", "NoDB"]


class DatabaseCommonSettings(SettingsBaseModel):
    """Configuration model for database settings.

    Attributes:
        provider: Optional provider identifier (e.g. "LMDB").
        max_records_in_memory: Maximum records kept in memory before auto-save.
        auto_save: Whether to auto-save when threshold exceeded.
        batch_size: Batch size for batch operations.
    """

    model_config = {"validate_assignment": True}  # keeps field validation on assignment

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

    initial_load_window_h: Optional[int] = Field(
        default=None,
        ge=0,
        json_schema_extra={
            "description": (
                "Specifies the default duration of the initial load window when "
                "loading records from the database, in hours. "
                "If set to None, the full available range is loaded. "
                "The window is centered around the current time by default, "
                "unless a different center time is specified. "
                "Different database namespaces may define their own default windows."
            ),
            "examples": ["48", "None"],
        },
    )

    keep_duration_h: Optional[int] = Field(
        default=None,
        ge=0,
        json_schema_extra={
            "description": (
                "Default maximum duration records shall be kept in database [hours, none].\n"
                "None indicates forever. Database namespaces may have diverging definitions."
            ),
            "examples": [48, "none"],
        },
    )

    autosave_interval_sec: Optional[int] = Field(
        default=10,
        ge=5,
        json_schema_extra={
            "description": (
                "Automatic saving interval [seconds].\nSet to None to disable automatic saving."
            ),
            "examples": [5],
        },
    )

    compaction_interval_sec: Optional[int] = Field(
        default=3600,  # hourly
        ge=0,
        json_schema_extra={
            "description": (
                "Interval in between automatic tiered compaction runs [seconds].\n"
                "Compaction downsamples old records to reduce storage while retaining "
                "coverage. Set to None to disable automatic compaction."
            ),
            "examples": [3600],  # 1 hour
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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def provider_id(self) -> str:
        """Return the unique identifier for the database provider."""
        return "LMDB"

    def open(self, *, namespace: Optional[str] = None) -> None:
        """Open LMDB environment and optionally ensure a namespace DBI.

        Args:
            namespace: Optional default namespace to open (DBI created on demand).
        """
        if self.is_open:
            if namespace is not None:
                self._ensure_dbi(namespace=namespace)
            return

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
            self._ensure_dbi(namespace=namespace)

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

    def flush(self, *, namespace: Optional[str] = None) -> None:
        """Sync LMDB environment (writes to disk)."""
        if not isinstance(self.env, lmdb.Environment):
            raise ValueError(f"LMDB Environment is of wrong tpe `{type(self.env)}`.")

        with self.lock:
            self.env.sync()

    # ------------------------------------------------------------------
    # Namespace helpers
    # ------------------------------------------------------------------

    def _normalize_namespace(self, namespace: Optional[str]) -> Optional[str]:
        """Return explicit namespace or default if None."""
        return namespace if namespace is not None else self.default_namespace

    def _ensure_dbi(self, *, namespace: Optional[str]) -> Optional[Any]:
        """Open and cache a DBI for the given namespace.

        Args:
            namespace: Namespace name or None for the unnamed DB.

        Returns:
            DBI handle (implementation specific) or None for unnamed DB.
        """
        if not isinstance(self.env, lmdb.Environment):
            raise RuntimeError(f"LMDB Environment is of wrong tpe `{type(self.env)}`.")

        name = self._normalize_namespace(namespace)

        if name in self._dbis:
            return self._dbis[name]

        if name is None:
            dbi = None
        else:
            dbi = self.env.open_db(name.encode("utf-8"), create=True)

        self._dbis[name] = dbi
        return dbi

    # ------------------------------------------------------------------
    # Metadata Operations
    # ------------------------------------------------------------------

    def set_metadata(self, metadata: Optional[bytes], *, namespace: Optional[str] = None) -> None:
        """Save metadata for a given namespace.

        Metadata is treated separately from data records and stored as a single object.

        Args:
            metadata (bytes): Arbitrary metadata to save or None to delete metadata.
            namespace (Optional[str]): Optional namespace under which to store metadata.
        """
        if not isinstance(self.env, lmdb.Environment):
            raise RuntimeError(f"LMDB Environment is of wrong tpe `{type(self.env)}`.")

        dbi = self._ensure_dbi(namespace=namespace)

        with self.env.begin(write=True) as txn:
            if metadata is None:
                txn.delete(DATABASE_METADATA_KEY, db=dbi)
            else:
                txn.put(DATABASE_METADATA_KEY, metadata, db=dbi)

    def get_metadata(self, *, namespace: Optional[str] = None) -> Optional[bytes]:
        """Load metadata for a given namespace.

        Returns None if no metadata exists.

        Args:
            namespace (Optional[str]): Optional namespace whose metadata to retrieve.

        Returns:
            Optional[bytes]: The loaded metadata, or None if not found.
        """
        if not isinstance(self.env, lmdb.Environment):
            raise RuntimeError(f"LMDB Environment is of wrong tpe `{type(self.env)}`.")

        dbi = self._ensure_dbi(namespace=namespace)

        with self.env.begin(write=False) as txn:
            return txn.get(DATABASE_METADATA_KEY, db=dbi)

    # ------------------------------------------------------------------
    # Bulk Write Operations
    # ------------------------------------------------------------------

    def save_records(
        self,
        records: Iterable[tuple[bytes, bytes]],
        *,
        namespace: Optional[str] = None,
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
        if not isinstance(self.env, lmdb.Environment):
            raise RuntimeError(f"LMDB Environment is of wrong tpe `{type(self.env)}`.")

        dbi = self._ensure_dbi(namespace=namespace)

        saved = 0
        with self.lock:
            with self.env.begin(write=True) as txn:
                for key, value in records:
                    if txn.put(key, value, db=dbi):
                        saved += 1

        return saved

    def delete_records(
        self,
        keys: Iterable[bytes],
        *,
        namespace: Optional[str] = None,
    ) -> int:
        """Delete multiple records by key from the specified namespace.

        Args:
            keys: Iterable that provides the Byte keys to delete.
            namespace: Optional namespace.

        Returns:
            Number of records actually deleted.
        """
        if not isinstance(self.env, lmdb.Environment):
            raise RuntimeError("Database not open")

        dbi = self._ensure_dbi(namespace=namespace)

        deleted = 0
        with self.lock:
            with self.env.begin(write=True) as txn:
                for key in keys:
                    if txn.delete(key, db=dbi):
                        deleted += 1

        return deleted

    # ------------------------------------------------------------------
    # Read Operations
    # ------------------------------------------------------------------

    def iterate_records(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
        *,
        namespace: Optional[str] = None,
        reverse: bool = False,
    ) -> Iterator[tuple[bytes, bytes]]:
        """Iterate over records in a namespace with optional key bounds.

        The LMDB read transaction is fully closed before yielding any results,
        preventing reader-slot leaks even if the caller aborts iteration early.

        Args:
            start_key: Inclusive lower bound key, or None.
            end_key: Exclusive upper bound key, or None.
            namespace: Optional namespace to target.
            reverse: If True, iterate in descending key order.

        Yields:
            Tuples of (key, value).
        """
        if not isinstance(self.env, lmdb.Environment):
            raise RuntimeError(f"LMDB Environment is of wrong type `{type(self.env)}`.")

        dbi = self._ensure_dbi(namespace=namespace)
        META = DATABASE_METADATA_KEY

        results: list[tuple[bytes, bytes]] = []

        cursor = None
        txn = self.env.begin(write=False)
        try:
            cursor = txn.cursor(dbi)

            if reverse:
                # --- Position cursor for reverse scan ---

                if end_key is not None:
                    # Jump to first key >= end_key, then step one back
                    if cursor.set_range(end_key):
                        if not cursor.prev():
                            # No smaller key exists
                            return iter(())
                    else:
                        if not cursor.last():
                            return iter(())
                else:
                    if not cursor.last():
                        return iter(())

                while True:
                    key = cursor.key()
                    value = cursor.value()

                    if key != META:
                        if start_key is None or key >= start_key:
                            results.append((key, value))
                        else:
                            break

                    if not cursor.prev():
                        break

            else:
                # --- Position cursor for forward scan ---

                if start_key is not None:
                    if not cursor.set_range(start_key):
                        return iter(())
                else:
                    if not cursor.first():
                        return iter(())

                while True:
                    key = cursor.key()
                    value = cursor.value()

                    if end_key is not None and key >= end_key:
                        break

                    if key != META:
                        results.append((key, value))

                    if not cursor.next():
                        break

        finally:
            # Ensure reader slot is always released
            if cursor is not None:
                cursor.close()
            txn.abort()

        # Transaction is closed here — safe to yield
        return iter(results)

    # ------------------------------------------------------------------
    # Stats / Metadata
    # ------------------------------------------------------------------

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
        if not isinstance(self.env, lmdb.Environment):
            raise RuntimeError(f"LMDB Environment is of wrong tpe `{type(self.env)}`.")

        dbi = self._ensure_dbi(namespace=namespace)
        META = DATABASE_METADATA_KEY

        count = 0

        with self.env.begin(write=False) as txn:
            cursor = txn.cursor(db=dbi)

            # Position cursor
            if start_key:
                if not cursor.set_range(start_key):
                    return 0
            else:
                if not cursor.first():
                    return 0

            while True:
                key = cursor.key()

                if end_key and key >= end_key:
                    break

                if key != META:
                    count += 1

                if not cursor.next():
                    break

        return count

    def get_key_range(
        self,
        *,
        namespace: Optional[str] = None,
    ) -> tuple[Optional[bytes], Optional[bytes]]:
        """Return (min_key, max_key) in the given namespace or (None, None) if empty."""
        if not isinstance(self.env, lmdb.Environment):
            raise RuntimeError(f"LMDB Environment is of wrong tpe `{type(self.env)}`.")

        dbi = self._ensure_dbi(namespace=namespace)

        with self.env.begin(write=False) as txn:
            cursor = txn.cursor(db=dbi)

            if not cursor.first():
                return None, None

            min_key = cursor.key()
            if min_key == DATABASE_METADATA_KEY:
                if not cursor.next():
                    return None, None
                min_key = cursor.key()

            if not cursor.last():
                return None, None

            max_key = cursor.key()
            if max_key == DATABASE_METADATA_KEY:
                if not cursor.prev():
                    return None, None
                max_key = cursor.key()

            return min_key, max_key

    def get_backend_stats(self, *, namespace: Optional[str] = None) -> dict[str, Any]:
        """Get LMDB backend-specific statistics."""
        if not self.env:
            return {}

        dbi = self._ensure_dbi(namespace=namespace)

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

    def _ns(self, namespace: Optional[str]) -> str:
        """Normalize namespace for storage ('' for None)."""
        return namespace if namespace is not None else (self.default_namespace or "")

    def provider_id(self) -> str:
        """Return the unique identifier for the database provider."""
        return "SQLite"

    def open(self, *, namespace: Optional[str] = None) -> None:
        """Open SQLite connection and optionally set default namespace.

        Args:
            namespace: Optional default namespace to use when operations omit namespace.
        """
        if self.is_open:
            return

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

    def flush(self, *, namespace: Optional[str] = None) -> None:
        """Commit any pending transactions to disk (no-op if autocommit)."""
        if not isinstance(self.conn, sqlite3.Connection):
            raise RuntimeError(f"SQLite connection is of wrong tpe `{type(self.conn)}`.")

        with self.lock:
            self.conn.commit()

    def set_metadata(self, metadata: Optional[bytes], *, namespace: Optional[str] = None) -> None:
        """Save metadata for a given namespace.

        Metadata is treated separately from data records and stored as a single object.

        Args:
            metadata (bytes): Arbitrary metadata to save or None to delete metadata.
            namespace (Optional[str]): Optional namespace under which to store metadata.
        """
        if not isinstance(self.conn, sqlite3.Connection):
            raise RuntimeError("Database not open")

        ns = self._ns(namespace)

        with self.conn:
            # Ensure metadata table exists
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    namespace TEXT PRIMARY KEY,
                    value BLOB
                )
            """)

            if metadata is None:
                # Delete metadata for the namespace
                self.conn.execute("DELETE FROM metadata WHERE namespace=?", (ns,))
            else:
                # Insert or update metadata
                self.conn.execute(
                    """
                    INSERT INTO metadata(namespace, value)
                    VALUES (?, ?)
                    ON CONFLICT(namespace) DO UPDATE SET value=excluded.value
                """,
                    (ns, metadata),
                )

    def get_metadata(self, *, namespace: Optional[str] = None) -> Optional[bytes]:
        """Load metadata for a given namespace.

        Returns None if no metadata exists.

        Args:
            namespace (Optional[str]): Optional namespace whose metadata to retrieve.

        Returns:
            Optional[bytes]: The loaded metadata, or None if not found.
        """
        if not isinstance(self.conn, sqlite3.Connection):
            raise RuntimeError("Database not open")

        ns = self._ns(namespace)

        # Ensure metadata table exists
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    namespace TEXT PRIMARY KEY,
                    value BLOB
                )
            """)
            row = self.conn.execute(
                "SELECT value FROM metadata WHERE namespace=?", (ns,)
            ).fetchone()
        return row[0] if row else None

    def save_records(
        self,
        records: Iterable[tuple[bytes, bytes]],
        *,
        namespace: Optional[str] = None,
    ) -> int:
        """Bulk insert or replace records.

        Returns:
            Number of records written.
        """
        if not isinstance(self.conn, sqlite3.Connection):
            raise RuntimeError("Database not open")

        ns = self._ns(namespace)

        rows = [(ns, k, v) for k, v in records]
        if not rows:
            return 0

        with self.lock:
            with self.conn:
                self.conn.executemany(
                    "INSERT OR REPLACE INTO records (namespace, key, value) VALUES (?, ?, ?)",
                    rows,
                )

        return len(rows)

    def delete_records(
        self,
        keys: Iterable[bytes],
        *,
        namespace: Optional[str] = None,
    ) -> int:
        """Delete multiple records by key.

        Returns True if at least one row was deleted.
        """
        if not isinstance(self.conn, sqlite3.Connection):
            raise RuntimeError("Database not open")

        ns = self._ns(namespace)

        rows = [(ns, key) for key in keys]

        if not rows:
            return 0

        with self.lock:
            with self.conn:
                cursor = self.conn.executemany(
                    "DELETE FROM records WHERE namespace = ? AND key = ?",
                    rows,
                )

        return cursor.rowcount

    def iterate_records(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
        *,
        namespace: Optional[str] = None,
        reverse: bool = False,
    ) -> Iterator[Tuple[bytes, bytes]]:
        """Iterate records for a namespace within optional bounds.

        Snapshot-based iteration:
        - Query results are materialized while holding the lock.
        - Yields happen after releasing the lock.
        - Metadata key is excluded.
        - Range semantics: [start_key, end_key)

        Args:
            start_key: Inclusive lower bound or None.
            end_key: Exclusive upper bound or None.
            namespace: Optional namespace.
            reverse: If True iterate descending.

        Yields:
            (key, value) tuples ordered by key.
        """
        if not isinstance(self.conn, sqlite3.Connection):
            raise RuntimeError(f"SQLite connection is of wrong tpe `{type(self.conn)}`.")

        ns = self._ns(namespace)
        order = "DESC" if reverse else "ASC"

        where_clauses = ["namespace = ?", "key != ?"]
        params: List[Any] = [ns, DATABASE_METADATA_KEY]

        if start_key is not None:
            where_clauses.append("key >= ?")
            params.append(start_key)

        if end_key is not None:
            where_clauses.append("key < ?")
            params.append(end_key)

        where_sql = " AND ".join(where_clauses)
        sql = f"SELECT key, value FROM records WHERE {where_sql} ORDER BY key {order}"  # noqa: S608

        # Snapshot rows while holding lock
        with self.lock:
            cursor = self.conn.execute(sql, tuple(params))
            rows = cursor.fetchall()

        # Yield after releasing lock
        for k, v in rows:
            yield k, v

    def count_records(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
        *,
        namespace: Optional[str] = None,
    ) -> int:
        """Count records in [start_key, end_key) excluding metadata."""
        if not isinstance(self.conn, sqlite3.Connection):
            raise RuntimeError(f"SQLite connection is of wrong tpe `{type(self.conn)}`.")

        ns = self._ns(namespace)

        where_clauses = ["namespace = ?", "key != ?"]
        params: List[Any] = [ns, DATABASE_METADATA_KEY]

        if start_key is not None:
            where_clauses.append("key >= ?")
            params.append(start_key)

        if end_key is not None:
            where_clauses.append("key < ?")
            params.append(end_key)

        where_sql = " AND ".join(where_clauses)
        sql = f"SELECT COUNT(*) FROM records WHERE {where_sql}"  # noqa: S608

        with self.lock:
            cursor = self.conn.execute(sql, tuple(params))
            return int(cursor.fetchone()[0])

    def get_key_range(
        self, *, namespace: Optional[str] = None
    ) -> Tuple[Optional[bytes], Optional[bytes]]:
        """Return (min_key, max_key) for the namespace or (None, None) if empty."""
        if not isinstance(self.conn, sqlite3.Connection):
            raise ValueError(f"SQLite connection is of wrong tpe `{type(self.conn)}`.")

        ns = self._ns(namespace)
        with self.lock:
            cursor = self.conn.execute(
                "SELECT MIN(key), MAX(key) FROM records WHERE namespace = ? and key != ?",
                (ns, DATABASE_METADATA_KEY),
            )
            result = cursor.fetchone()
            return result[0], result[1]

    def get_backend_stats(self, *, namespace: Optional[str] = None) -> Dict[str, Any]:
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


# ==================== NoDB Implementation ====================


class NoDB(DatabaseBackendABC):
    """No-op database backend.

    This backend implements the full database backend interface but performs
    no actual persistence. It is intended for configurations where database
    persistence is disabled (`provider=None`).

    Characteristics:
        - All write operations are ignored.
        - All read operations return empty results.
        - No files or external resources are created.
        - Lifecycle operations are no-ops.
        - Compression is disabled.

    This backend allows the database wrapper to avoid special-case handling
    for a missing database provider by always providing a valid backend
    implementation.
    """

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the no-op backend."""
        super().__init__()
        self._is_open = True  # Stateless

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def provider_id(self) -> str:
        """Return the unique identifier for the database provider."""
        return "NoDB"

    def open(self, *, namespace: Optional[str] = None) -> None:
        """Mark backend as open.

        Args:
            namespace: Ignored.
        """
        self.default_namespace = namespace

    def close(self) -> None:
        """Mark backend as closed."""
        return

    def flush(self, *, namespace: Optional[str] = None) -> None:
        """Flush pending writes (no-op).

        Args:
            namespace: Ignored.
        """
        return

    # ------------------------------------------------------------------
    # Effective backend configuration
    # ------------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        """Return dummy not open."""
        return False

    @property
    def storage_path(self) -> Path:
        """Return dummy storage path."""
        return Path("/dev/null")

    @property
    def compression_level(self) -> int:
        """Compression is disabled."""
        return 0

    @property
    def compression(self) -> bool:
        """Compression is disabled."""
        return False

    # ------------------------------------------------------------------
    # Metadata operations
    # ------------------------------------------------------------------

    def set_metadata(
        self,
        metadata: Optional[bytes],
        *,
        namespace: Optional[str] = None,
    ) -> None:
        """Store metadata (ignored).

        Args:
            metadata: Ignored.
            namespace: Ignored.
        """
        return

    def get_metadata(
        self,
        *,
        namespace: Optional[str] = None,
    ) -> Optional[bytes]:
        """Load metadata.

        Always returns:
            None
        """
        return None

    # ------------------------------------------------------------------
    # Record operations
    # ------------------------------------------------------------------

    def save_records(
        self,
        records: Iterable[tuple[bytes, bytes]],
        *,
        namespace: Optional[str] = None,
    ) -> int:
        """Pretend to save records.

        Args:
            records: Ignored.
            namespace: Ignored.

        Returns:
            Always 0.
        """
        return 0

    def delete_records(
        self,
        keys: Iterable[bytes],
        *,
        namespace: Optional[str] = None,
    ) -> int:
        """Pretend to delete records.

        Args:
            keys: Ignored.
            namespace: Ignored.

        Returns:
            Always 0.
        """
        return 0

    def iterate_records(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
        *,
        namespace: Optional[str] = None,
        reverse: bool = False,
    ) -> Iterator[tuple[bytes, bytes]]:
        """Iterate records.

        Always yields:
            Nothing
        """
        return iter(())

    def count_records(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
        *,
        namespace: Optional[str] = None,
    ) -> int:
        """Count records.

        Returns:
            Always 0.
        """
        return 0

    def get_key_range(
        self,
        *,
        namespace: Optional[str] = None,
    ) -> tuple[Optional[bytes], Optional[bytes]]:
        """Return key range.

        Returns:
            Always (None, None).
        """
        return None, None

    def get_backend_stats(
        self,
        *,
        namespace: Optional[str] = None,
    ) -> dict[str, Any]:
        """Return backend statistics.

        Returns:
            Minimal backend information.
        """
        return {
            "backend": "none",
            "namespace": namespace,
            "persistent": False,
            "records": 0,
        }


# ==================== Generic Database ====================


class Database(ConfigMixin, SingletonMixin):
    """Generic database.

    All operations accept an optional `namespace` argument. Implementations should
    treat None as the default/root namespace. Concrete implementations can map
    namespace -> native namespace (LMDB DBI) or emulate namespaces (SQLite uses
    a namespace column).
    """

    _db: DatabaseBackendABC = NoDB()

    @classmethod
    def reset_instance(cls) -> None:
        """Resets the singleton instance, forcing it to be recreated on next access."""
        with cls._lock:
            # Close current database backend
            cls._db.close()
            cls._db = NoDB()
            # Remove current database instance
            if cls in cls._instances:
                del cls._instances[cls]
                logger.debug(f"{cls.__name__} singleton instance has been reset.")

    def __init__(self) -> None:
        """Initialize database."""
        super().__init__()
        self._db = NoDB()

    # Database helpers

    @property
    def _database_lock(self) -> asyncio.Lock:
        """Per-instance asyncio lock guarding database operations.

        The lock guards the database state during async operations.
        """
        # Lock must be a loop-local asyncio lock to provide singleton + pytest async compatibility.
        loop = asyncio.get_running_loop()

        try:
            locks = object.__getattribute__(self, "_database_locks")
        except AttributeError:
            locks = {}
            object.__setattr__(self, "_database_locks", locks)

        lock = locks.get(loop)

        if lock is None:
            lock = asyncio.Lock()
            locks[loop] = lock

        return lock

    async def _ensure_open(
        self,
        *,
        namespace: Optional[str] = None,
    ) -> DatabaseBackendABC:
        """Ensure the configured backend exists, is open, and namespace is prepared.

        Expects the database lock to already be held when called.

        Args:
            namespace: Optional namespace to prepare/open.

        Returns:
            The active and opened backend instance.

        Raises:
            RuntimeError: If no database provider is configured.
        """
        provider_id = self.config.database.provider
        old_provider_id = self._db.provider_id()

        if old_provider_id != provider_id:
            # No database or configuration does not match
            logger.debug(
                f"Switching database provider from '{old_provider_id}' to '{provider_id}'."
            )
            old_db = self._db

            database: DatabaseBackendABC
            if provider_id is None or provider_id == "NoDB":
                database = NoDB()
            elif provider_id == "LMDB":
                database = LMDBDatabase()
            elif provider_id == "SQLite":
                database = SQLiteDatabase()
            else:
                raise RuntimeError(f"Invalid database provider '{provider_id}'")

            # Auto-close database that was used before
            if old_db.is_open:
                old_db.close()

            self._db = database

        if not self._db.is_open:
            await asyncio.to_thread(self._db.open, namespace=namespace)
        elif namespace is not None:
            # Allow backend to lazily prepare namespace resources
            await asyncio.to_thread(self._db.open, namespace=namespace)

        return self._db

    async def _run_db(
        self,
        method_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Execute a synchronous database backend operation in a thread-safe, non-blocking way.

        The backend is automatically initialized/opened before execution.
        If a ``namespace`` keyword argument is present, the namespace is
        prepared during backend initialization.

        This helper ensures that all interactions with the underlying synchronous
        database backend are:

        - **Serialized** using an instance-level ``asyncio.Lock`` to protect backend state.
        - **Non-blocking** by offloading execution to a worker thread via
          ``asyncio.to_thread``.

        It should be used for all backend calls that may perform blocking I/O or
        CPU-bound work.

        Args:
            method_name: The synchronous callable to execute (a backend method).
            *args: Positional arguments forwarded to ``method``.
            **kwargs: Keyword arguments forwarded to ``method``.

        Returns:
            The return value of ``method``.

        Raises:
            Any exception raised by ``func`` is propagated unchanged.

        Notes:
            - The callable is executed in a separate thread, so it must be thread-safe.
            - The database lock is held for the duration of the call, ensuring that
              no concurrent operations interfere with backend state.
            - Avoid passing coroutines or async functions to this method; it is
              intended strictly for synchronous callables.
        """
        namespace = kwargs.get("namespace")

        async with self._database_lock:
            db = await self._ensure_open(namespace=namespace)

            # Get actual method. _ensure_open may have changed the backend
            method = getattr(db, method_name)

            def backend_call() -> Any:
                result = method(
                    *args,
                    **kwargs,
                )

                # Materialize iterators inside worker thread
                if isinstance(result, Iterator):
                    return list(result)

                return result

            return await asyncio.to_thread(backend_call)

    def provider_id(self) -> str:
        """Return the unique identifier for the database provider."""
        return self._db.provider_id()

    @property
    def is_open(self) -> bool:
        """Return whether the database connection is open."""
        return self._db.is_open

    @property
    def storage_path(self) -> Path:
        """Return effective storage path of active backend."""
        return self._db.storage_path

    @property
    def compression_level(self) -> int:
        """Return effective compression level of active backend."""
        return self._db.compression_level

    @property
    def compression(self) -> bool:
        """Return whether the active backend compresses stored values.

        Returns:
            True if compression is enabled for the active backend,
            False if disabled,
            or None if no backend is currently initialized.
        """
        return self._db.compression_level > 0

    # Lifecycle

    async def ensure_open(
        self,
        *,
        namespace: Optional[str] = None,
    ) -> DatabaseBackendABC:
        """Ensure the configured backend exists, is open, and namespace is prepared.

        Args:
            namespace: Optional namespace to prepare/open.

        Returns:
            The active and opened backend instance.

        Raises:
            RuntimeError: If no database provider is configured.
        """
        async with self._database_lock:
            return await self._ensure_open(namespace=namespace)

    async def open(self, *, namespace: Optional[str] = None) -> None:
        """Ensure the database backend is initialized and open.

        If the backend is already open, this operation is a no-op except
        that the backend may prepare the specified namespace.

        Args:
            namespace: Optional namespace to prepare/open.

        Raises:
            RuntimeError: If no database provider is configured or opening fails.
        """
        async with self._database_lock:
            await self._ensure_open(namespace=namespace)

    async def close(self) -> None:
        """Close the database connection and cleanup resources."""
        async with self._database_lock:
            if self._db is not None and self._db.is_open:
                await asyncio.to_thread(self._db.close)

    async def flush(self, *, namespace: Optional[str] = None) -> None:
        """Force synchronization of pending writes to storage (optional per-namespace)."""
        await self._run_db("flush", namespace=namespace)

    # Metadata operations

    async def set_metadata(
        self, metadata: Optional[bytes], *, namespace: Optional[str] = None
    ) -> None:
        """Save metadata for a given namespace.

        Metadata is treated separately from data records and stored as a single object.

        Args:
            metadata (bytes): Arbitrary metadata to save or None to delete metadata.
            namespace (Optional[str]): Optional namespace under which to store metadata.
        """
        await self._run_db(
            "set_metadata",
            metadata,
            namespace=namespace,
        )

    async def get_metadata(self, *, namespace: Optional[str] = None) -> Optional[bytes]:
        """Load metadata for a given namespace.

        Returns None if no metadata exists.

        Args:
            namespace (Optional[str]): Optional namespace whose metadata to retrieve.

        Returns:
            Optional[bytes]: The loaded metadata, or None if not found.
        """
        return await self._run_db(
            "get_metadata",
            namespace=namespace,
        )

    # Basic record operations

    async def save_records(
        self, records: Iterable[tuple[bytes, bytes]], *, namespace: Optional[str] = None
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
        return await self._run_db(
            "save_records",
            records,
            namespace=namespace,
        )

    async def delete_records(
        self, keys: Iterable[bytes], *, namespace: Optional[str] = None
    ) -> int:
        """Delete multiple records by key from the specified namespace.

        Args:
            keys: Iterable that provides the Byte keys to delete.
            namespace: Optional namespace.

        Returns:
            Number of records actually deleted.
        """
        return await self._run_db(
            "delete_records",
            keys,
            namespace=namespace,
        )

    async def iterate_records(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
        *,
        namespace: Optional[str] = None,
        reverse: bool = False,
    ) -> AsyncIterator[tuple[bytes, bytes]]:
        """Iterate over records for a namespace with optional bounds.

        Args:
            start_key: Inclusive start key, or None.
            end_key: Exclusive end key, or None.
            namespace: Optional namespace to target.
            reverse: If True iterate in descending key order.

        Yields:
            Tuples of (key, record).
        """
        records: list[tuple[bytes, bytes]] = await self._run_db(
            "iterate_records",
            start_key,
            end_key,
            namespace=namespace,
            reverse=reverse,
        )

        for item in records:
            yield item

    async def count_records(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
        *,
        namespace: Optional[str] = None,
    ) -> int:
        """Count records in [start_key, end_key) excluding metadata in specified namespace.

        Excludes metadata records.
        """
        return await self._run_db(
            "count_records",
            start_key,
            end_key,
            namespace=namespace,
        )

    async def get_key_range(
        self, *, namespace: Optional[str] = None
    ) -> Tuple[Optional[bytes], Optional[bytes]]:
        """Return (min_key, max_key) in the given namespace or (None, None) if empty."""
        return await self._run_db(
            "get_key_range",
            namespace=namespace,
        )

    async def get_backend_stats(self, *, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Get backend-specific statistics; implementations may return namespace-specific data."""
        return await self._run_db(
            "get_backend_stats",
            namespace=namespace,
        )

    # Compression helpers

    def serialize_data(self, data: bytes) -> bytes:
        """Optionally compress raw pickled data before storage.

        Args:
            data: Raw pickled bytes.

        Returns:
            Possibly compressed bytes.
        """
        return self._db.serialize_data(data)

    def deserialize_data(self, data: bytes) -> bytes:
        """Optionally decompress stored data.

        Args:
            data: Stored bytes.

        Returns:
            Raw pickled bytes (decompressed if needed).
        """
        return self._db.deserialize_data(data)
