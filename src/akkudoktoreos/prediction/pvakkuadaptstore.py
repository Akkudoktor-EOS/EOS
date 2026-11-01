"""Bounded, persistent issued PV forecasts and independently timed measurements."""

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
from collections.abc import Coroutine
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar, Iterator, TypeVar, cast

import numpy as np
import pandas as pd

from akkudoktoreos.core.coreabc import get_config, get_database
from akkudoktoreos.measurement.quality import MeasurementSample

T = TypeVar("T")


class _DatabaseWorker:
    """Use the existing asynchronous EOS database from numerical worker threads."""

    _loop: ClassVar[asyncio.AbstractEventLoop | None] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()
    _pid: ClassVar[int] = 0

    @classmethod
    def run(cls, operation: Coroutine[Any, Any, T]) -> T:
        """Run database frontend operations on a shared, process-local I/O loop."""
        with cls._lock:
            if cls._loop is None or cls._pid != os.getpid():
                cls._loop = asyncio.new_event_loop()
                cls._pid = os.getpid()
                threading.Thread(
                    target=cls._loop.run_forever, daemon=True, name="pv-database"
                ).start()
            loop = cls._loop
        return asyncio.run_coroutine_threadsafe(operation, loop).result(timeout=120)


class PVLearningStore:
    """Persist immutable issue/target records through the configured EOS database.

    SQLite is only an in-memory query index. Durable records live in the existing
    EOS backend namespaces. NoDB intentionally learns in memory without persistence.
    The old private SQLite file is read-only migration input and remains untouched.
    """

    _keys = {
        "samples": ("key", "time_ms"),
        "forecasts": ("signature", "issue_ms", "target_ms"),
        "baselines": ("signature", "target_ms"),
        "latest": ("target_ms",),
        "metadata": ("key",),
        "weather": ("issue_ms",),
        "longforecasts": ("signature", "issue_ms", "target_ms"),
    }

    def __init__(self, path: Path):
        self.path = path
        self.database = get_database()
        self.persistent = get_config().database.provider not in (None, "NoDB")
        self._lock = threading.RLock()
        self._db = sqlite3.connect(":memory:", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._initializing = True
        self._saved_sequence = 0
        migrated = None
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS samples (
                    key TEXT NOT NULL, time_ms INTEGER NOT NULL, value REAL,
                    quality TEXT NOT NULL, available_ms INTEGER NOT NULL,
                    PRIMARY KEY(key,time_ms));
                CREATE TABLE IF NOT EXISTS forecasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signature TEXT NOT NULL, issue_ms INTEGER NOT NULL, target_ms INTEGER NOT NULL,
                    lead_minutes REAL NOT NULL, payload TEXT NOT NULL,
                    actual_w REAL, reference_actual_w REAL, available_ms INTEGER,
                    updated_ms INTEGER NOT NULL,
                    UNIQUE(signature,issue_ms,target_ms));
                CREATE INDEX IF NOT EXISTS forecast_target ON forecasts(signature,target_ms);
                CREATE TABLE IF NOT EXISTS baselines (
                    signature TEXT NOT NULL, target_ms INTEGER NOT NULL,
                    issue_ms INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(signature,target_ms));
                CREATE TABLE IF NOT EXISTS latest (
                    target_ms INTEGER PRIMARY KEY, issue_ms INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS weather (issue_ms INTEGER PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS longforecasts (
                    signature TEXT NOT NULL, issue_ms INTEGER NOT NULL, target_ms INTEGER NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(signature,issue_ms,target_ms));
            """)
        if self.persistent:
            _DatabaseWorker.run(self.database.open(namespace="pvakkuadapt"))
            migrated = _DatabaseWorker.run(self.database.get_metadata(namespace="pvakkuadapt"))
            if path.is_file() and not migrated:
                self._import_legacy()
            else:
                self._load_backend()
        self._track_changes()
        self._initializing = False
        if self.persistent and not migrated:
            if path.is_file():
                for table, keys in self._keys.items():
                    expression = ",".join(keys)
                    self._db.execute(
                        f"INSERT OR REPLACE INTO _changes SELECT ?,json_array({expression}) FROM {table}",  # noqa: S608
                        (table,),
                    )  # noqa: S608
                self._db.commit()
            self._flush()
            _DatabaseWorker.run(self.database.flush())
            _DatabaseWorker.run(
                self.database.set_metadata(
                    self.dump(
                        dict(
                            schema_version=1,
                            migrated_from=str(path) if path.is_file() else None,
                            counts=self.counts(),
                        )
                    ).encode(),
                    namespace="pvakkuadapt",
                )
            )
            _DatabaseWorker.run(self.database.flush())

    def _import_legacy(self) -> None:
        """Read the old archive without changing data, IDs or availability timestamps."""
        with sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True) as old:
            old.row_factory = sqlite3.Row
            for table in ("samples", "forecasts", "baselines", "latest", "metadata"):
                cursor = old.execute("SELECT * FROM " + table)  # noqa: S608 -- fixed table list
                while batch := cursor.fetchmany(2000):
                    self._insert_rows(table, [dict(row) for row in batch])
            sequence = old.execute(
                "SELECT seq FROM sqlite_sequence WHERE name='forecasts'"
            ).fetchone()
            if sequence:
                self._restore_sequence(int(sequence[0]))

    def _restore_sequence(self, sequence: int) -> None:
        current = self._db.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='forecasts'"
        ).fetchone()
        if current:
            self._db.execute(
                "UPDATE sqlite_sequence SET seq=? WHERE name='forecasts'",
                (max(sequence, current[0]),),
            )
        else:
            self._db.execute("INSERT INTO sqlite_sequence VALUES ('forecasts',?)", (sequence,))
        self._db.commit()

    def _insert_rows(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        if table not in self._keys:
            raise ValueError("Unknown PV record type")
        columns = tuple(rows[0])
        allowed = {row[1] for row in self._db.execute("PRAGMA table_info(" + table + ")")}
        if set(columns) != allowed:
            raise ValueError("Invalid PV database record columns")
        placeholders = ",".join("?" for _ in columns)
        self._db.executemany(
            f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) VALUES ({placeholders})",  # noqa: S608
            [tuple(row[column] for column in columns) for row in rows],
        )
        self._db.commit()

    async def _backend_rows(self, table: str) -> list[dict[str, Any]]:
        return [
            json.loads(self.database.deserialize_data(value))
            async for _, value in self.database.iterate_records(namespace="pvakkuadapt." + table)
        ]

    def _load_backend(self) -> None:
        for table in self._keys:
            self._insert_rows(table, _DatabaseWorker.run(self._backend_rows(table)))
        sequence = _DatabaseWorker.run(
            self.database.get_metadata(namespace="pvakkuadapt.forecasts")
        )
        if sequence:
            self._restore_sequence(int(sequence))
            self._saved_sequence = int(sequence)

    def _track_changes(self) -> None:
        self._db.execute(
            "CREATE TABLE _changes (table_name TEXT, record_key TEXT, PRIMARY KEY(table_name,record_key))"
        )
        for table, keys in self._keys.items():
            for event, source in (("INSERT", "NEW"), ("UPDATE", "NEW"), ("DELETE", "OLD")):
                expression = ",".join(source + "." + key for key in keys)
                self._db.execute(
                    f"CREATE TRIGGER track_{table}_{event} AFTER {event} ON {table} BEGIN "  # noqa: S608
                    f"INSERT OR REPLACE INTO _changes VALUES ('{table}',json_array({expression})); END"
                )
        self._db.commit()

    def _flush(self) -> None:
        if self._initializing:
            return
        if self.persistent:
            for table, keys in self._keys.items():
                changed = self._db.execute(
                    "SELECT record_key FROM _changes WHERE table_name=?", (table,)
                ).fetchall()
                writes, deletes = [], []
                for (encoded_key,) in changed:
                    identity = json.loads(encoded_key)
                    row = self._db.execute(
                        f"SELECT * FROM {table} WHERE " + " AND ".join(k + "=?" for k in keys),  # noqa: S608
                        identity,
                    ).fetchone()  # noqa: S608
                    key = encoded_key.encode()
                    if row is None:
                        deletes.append(key)
                    else:
                        writes.append(
                            (key, self.database.serialize_data(self.dump(dict(row)).encode()))
                        )
                if writes:
                    count = _DatabaseWorker.run(
                        self.database.save_records(writes, namespace="pvakkuadapt." + table)
                    )
                    if count != len(writes):
                        raise RuntimeError("Incomplete PV database write")
                if deletes:
                    _DatabaseWorker.run(
                        self.database.delete_records(deletes, namespace="pvakkuadapt." + table)
                    )
            sequence = self._db.execute(
                "SELECT seq FROM sqlite_sequence WHERE name='forecasts'"
            ).fetchone()
            if sequence and sequence[0] > self._saved_sequence:
                _DatabaseWorker.run(
                    self.database.set_metadata(
                        str(sequence[0]).encode(), namespace="pvakkuadapt.forecasts"
                    )
                )
                self._saved_sequence = int(sequence[0])
        self._db.execute("DELETE FROM _changes")
        self._db.commit()

    def counts(self) -> dict[str, int]:
        """Domain record counts for migration verification and diagnostics."""
        return {
            table: self._db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]  # noqa: S608
            for table in self._keys
        }  # noqa: S608 -- fixed internal table names

    def storage_status(self) -> dict[str, Any]:
        """Describe persistence explicitly rather than silently creating a private DB."""
        return dict(
            backend=get_config().database.provider or "NoDB",
            persistent=self.persistent,
            namespace="pvakkuadapt",
            records=self.counts(),
        )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Use short atomic transactions and bound lock waits across ingestion/readout."""
        with self._lock:
            if not self._initializing:
                self._flush()
            with self._db:
                yield self._db
            self._flush()

    @staticmethod
    def milliseconds(timestamp: pd.Timestamp) -> int:
        """Milliseconds from an explicitly zoned timestamp."""
        value = pd.Timestamp(timestamp)
        if value.tzinfo is None:
            raise ValueError("Explicit timezone required.")
        return int(value.timestamp() * 1000)

    @staticmethod
    def dump(payload: Any) -> str:
        """Serialize finite values; missing model inputs remain null."""

        def clean(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: clean(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [clean(item) for item in value]
            if isinstance(value, pd.Timestamp):
                return value.isoformat() if pd.notna(value) else None
            if isinstance(value, (float, np.floating)):
                return float(value) if np.isfinite(value) else None
            if isinstance(value, np.integer):
                return int(value)
            if value is pd.NaT:
                return None
            return value

        return json.dumps(clean(payload), allow_nan=False, separators=(",", ":"))

    def metadata(self, key: str, value: Any = None) -> Any:
        """Read or atomically replace separately named model/status metadata."""
        with self.connection() as db:
            if value is not None:
                db.execute(
                    "INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
                    (key, self.dump(value)),
                )
            row = db.execute("SELECT payload FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def import_samples(self, samples: list[MeasurementSample], now: pd.Timestamp) -> int:
        """Retain the first real arrival; a changed value gets its new availability."""
        received = self.milliseconds(now)
        values = [
            (
                sample.key,
                self.milliseconds(pd.Timestamp(sample.date_time)),
                sample.value,
                sample.quality.status,
                received,
            )
            for sample in samples
        ]
        if any(time > received for _, time, *_ in values):
            raise ValueError("Future PV measurements cannot be imported.")
        with self.connection() as db:
            db.executemany(
                """INSERT INTO samples VALUES (?,?,?,?,?)
                ON CONFLICT(key,time_ms) DO UPDATE SET value=excluded.value, quality=excluded.quality,
                available_ms=CASE WHEN samples.value IS excluded.value AND samples.quality=excluded.quality
                THEN samples.available_ms ELSE excluded.available_ms END""",
                values,
            )
        return len(values)

    def samples(self, key: str, start: pd.Timestamp, cutoff: pd.Timestamp) -> list[dict[str, Any]]:
        """Exclude future source timestamps and samples received after the cutoff."""
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM samples WHERE key=? AND time_ms>=? AND time_ms<=? AND available_ms<=? ORDER BY time_ms",
                    (
                        key,
                        self.milliseconds(start),
                        self.milliseconds(cutoff),
                        self.milliseconds(cutoff),
                    ),
                )
            ]

    def remember(
        self,
        signature: str,
        issue: pd.Timestamp,
        rows: list[tuple[pd.Timestamp, dict[str, Any]]],
        timezone: str,
    ) -> None:
        """Archive each short lead once and freeze previous-day physical baselines."""
        issued = self.milliseconds(issue)
        with self.connection() as db:
            for timestamp, payload in rows:
                target = self.milliseconds(timestamp)
                if target < issued:
                    continue
                text = self.dump({**payload, "signature": signature})
                db.execute(
                    "INSERT INTO latest VALUES (?,?,?) ON CONFLICT(target_ms) DO UPDATE SET issue_ms=excluded.issue_ms,payload=excluded.payload",
                    (target, issued, text),
                )
                day_start = pd.Timestamp(timestamp).tz_convert(timezone).normalize()
                existing = db.execute(
                    "SELECT issue_ms FROM baselines WHERE signature=? AND target_ms=?",
                    (signature, target),
                ).fetchone()
                if existing is None or issued < self.milliseconds(day_start):
                    db.execute(
                        "INSERT INTO baselines VALUES (?,?,?,?) ON CONFLICT(signature,target_ms) DO UPDATE SET issue_ms=excluded.issue_ms,payload=excluded.payload",
                        (signature, target, issued, text),
                    )
                lead = (target - issued) / 60000
                if lead < 120:
                    db.execute(
                        "INSERT OR IGNORE INTO forecasts(signature,issue_ms,target_ms,lead_minutes,payload,updated_ms) VALUES (?,?,?,?,?,?)",
                        (signature, issued, target, lead, text, issued),
                    )

    def archive_weather(self, data: dict[str, Any], received: pd.Timestamp) -> str:
        """Archive each received response once, retaining only genuinely future inputs.

        Open-Meteo does not expose the numerical model's issuance here. The first
        local receipt is recorded explicitly; returned past data never become
        historical forecasts. Cache hits preserve that first receipt.
        """
        digest = hashlib.sha256(self.dump(data).encode()).hexdigest()
        with self.connection() as db:
            if db.execute(
                "SELECT 1 FROM weather WHERE json_extract(payload,'$.digest')=? LIMIT 1", (digest,)
            ).fetchone():
                return digest
            raw = data["minutely_15"]
            times = pd.DatetimeIndex(pd.to_datetime(raw["time"], utc=True))
            starts = times - pd.Timedelta(minutes=15)
            keep = (starts >= received) & (starts <= received + pd.Timedelta(hours=48))
            subset = {
                key: [value for value, valid in zip(values, keep) if valid]
                for key, values in raw.items()
            }
            payload = dict(
                digest=digest,
                received_at=received.isoformat(),
                source_issue_at=None,
                timestamp_semantics="interval_end",
                minutely_15=subset,
                units=data.get("minutely_15_units", {}),
                elevation=data.get("elevation"),
            )
            db.execute(
                "INSERT OR IGNORE INTO weather VALUES (?,?)",
                (self.milliseconds(received), self.dump(payload)),
            )
        return digest

    def remember_long(
        self, signature: str, issue: pd.Timestamp, rows: list[tuple[pd.Timestamp, dict[str, Any]]]
    ) -> None:
        """Freeze an hourly snapshot per future target, bounding archive growth."""
        issued = self.milliseconds(issue)
        bucket = self.milliseconds(issue.floor("h"))
        with self.connection() as db:
            for timestamp, payload in rows:
                target = self.milliseconds(timestamp)
                if not issued < target <= issued + 48 * 3600000:
                    continue
                db.execute(
                    "INSERT OR IGNORE INTO longforecasts VALUES (?,?,?,?)",
                    (
                        signature,
                        bucket,
                        target,
                        self.dump({**payload, "issued_ms": issued, "response_version": 1}),
                    ),
                )

    def long_rows(
        self, signature: str, start: pd.Timestamp, cutoff: pd.Timestamp
    ) -> list[dict[str, Any]]:
        """Read issued long-horizon candidates, with no reconstructed past weather."""
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM longforecasts WHERE signature=? AND target_ms>=? AND target_ms<? ORDER BY target_ms,issue_ms",
                (signature, self.milliseconds(start), self.milliseconds(cutoff)),
            ).fetchall()
        return [{**dict(row), **json.loads(row["payload"]), "payload": None} for row in rows]

    def resolve(self, signature: str, observed: pd.DataFrame, now: pd.Timestamp) -> None:
        """Attach first complete observations, preserving the original forecast."""
        updated = self.milliseconds(now)
        with self.connection() as db:
            for timestamp, values in observed.iterrows():
                target = self.milliseconds(cast(pd.Timestamp, timestamp))
                if target + 900000 > updated:
                    continue
                if (
                    pd.notna(values.actual_w)
                    and np.isfinite(float(values.actual_w))
                    and pd.notna(values.available_at)
                ):
                    if self.milliseconds(values.available_at) <= updated:
                        db.execute(
                            "UPDATE forecasts SET actual_w=?,updated_ms=? WHERE signature=? AND target_ms=? AND actual_w IS NULL",
                            (float(values.actual_w), updated, signature, target),
                        )
                if pd.isna(values.reference_actual_w) or not np.isfinite(
                    float(values.reference_actual_w)
                ):
                    continue
                availability = self.milliseconds(values.available_at)
                if availability > updated or target + 900000 > updated:
                    continue
                actual = (
                    float(values.actual_w)
                    if pd.notna(values.actual_w) and np.isfinite(float(values.actual_w))
                    else None
                )
                db.execute(
                    """UPDATE forecasts SET reference_actual_w=?,actual_w=?,available_ms=?,updated_ms=?
                    WHERE signature=? AND target_ms=? AND reference_actual_w IS NULL""",
                    (
                        float(values.reference_actual_w),
                        actual,
                        availability,
                        updated,
                        signature,
                        target,
                    ),
                )
                if actual is not None:
                    db.execute(
                        "UPDATE forecasts SET actual_w=?,updated_ms=? WHERE signature=? AND target_ms=? AND actual_w IS NULL AND reference_actual_w IS NOT NULL",
                        (actual, updated, signature, target),
                    )

    def training(self, signature: str, cutoff: pd.Timestamp, days: int = 21) -> pd.DataFrame:
        """Return archived inputs with independently available reference targets."""
        end = self.milliseconds(cutoff)
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM forecasts WHERE signature=? AND issue_ms>=? AND available_ms<=? ORDER BY issue_ms,target_ms",
                (signature, end - days * 86400000, end),
            ).fetchall()
        result = []
        for row in rows:
            values = json.loads(row["payload"])
            if values.get("features") is None or not values.get("reference_sky_w"):
                continue
            result.append(
                {
                    **values["features"],
                    "issue": pd.Timestamp(row["issue_ms"], unit="ms", tz="UTC"),
                    "time": pd.Timestamp(row["target_ms"], unit="ms", tz="UTC"),
                    "lead_minutes": row["lead_minutes"],
                    "target_available_at": pd.Timestamp(row["available_ms"], unit="ms", tz="UTC"),
                    "target_residual": (
                        row["reference_actual_w"] - values["reference_calibrated_w"]
                    )
                    / values["reference_sky_w"],
                    "reference_sky_w": values["reference_sky_w"],
                }
            )
        return pd.DataFrame(result)

    def baselines(
        self, signature: str, start: pd.Timestamp, cutoff: pd.Timestamp
    ) -> list[tuple[pd.Timestamp, dict[str, Any]]]:
        """Return genuine issued physical baselines only, never revised weather."""
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM baselines WHERE signature=? AND target_ms>=? AND target_ms<? AND issue_ms<=target_ms ORDER BY target_ms",
                (signature, self.milliseconds(start), self.milliseconds(cutoff)),
            ).fetchall()
        return [
            (pd.Timestamp(row["target_ms"], unit="ms", tz="UTC"), json.loads(row["payload"]))
            for row in rows
        ]

    def comparisons(
        self, signature: str, start_ms: int, end_ms: int, limit: int = 2000, since_ms: int = 0
    ) -> list[dict[str, Any]]:
        """Export bounded matched forecasts, keeping issue time and lead for Grafana."""
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM forecasts WHERE signature=? AND target_ms>=? AND target_ms<? AND updated_ms>=? AND (reference_actual_w IS NOT NULL OR actual_w IS NOT NULL) ORDER BY updated_ms,id LIMIT ?",
                (signature, start_ms, end_ms, since_ms, limit),
            ).fetchall()
        return [{**dict(row), **json.loads(row["payload"]), "payload": None} for row in rows]

    def validation_rows(
        self, signature: str, start: pd.Timestamp, end: pd.Timestamp
    ) -> list[dict[str, Any]]:
        """Read the complete validation window; public export pagination is separate."""
        with self.connection() as db:
            rows = db.execute(
                "SELECT issue_ms,target_ms,lead_minutes,payload FROM forecasts WHERE signature=? AND target_ms>=? AND target_ms<? ORDER BY target_ms,issue_ms",
                (signature, self.milliseconds(start), self.milliseconds(end)),
            ).fetchall()
        return [{**dict(row), **json.loads(row["payload"]), "payload": None} for row in rows]

    def metric_rows(
        self, signature: str, start: pd.Timestamp, end: pd.Timestamp
    ) -> list[dict[str, Any]]:
        """Full-window scalar projection avoids decoding every feature/model payload."""
        with self.connection() as db:
            rows = db.execute(
                """SELECT target_ms,lead_minutes,actual_w,reference_actual_w,
                COALESCE(json_extract(payload,'$.algorithm_version'),1) AS algorithm_version,
                json_extract(payload,'$.plant_raw_w') AS plant_raw_w,
                json_extract(payload,'$.plant_calibrated_w') AS plant_calibrated_w,
                json_extract(payload,'$.plant_local_w') AS plant_local_w,
                json_extract(payload,'$.plant_adaptive_w') AS plant_adaptive_w,
                json_extract(payload,'$.reference_raw_w') AS reference_raw_w,
                json_extract(payload,'$.reference_calibrated_w') AS reference_calibrated_w,
                json_extract(payload,'$.reference_local_w') AS reference_local_w,
                json_extract(payload,'$.reference_adaptive_w') AS reference_adaptive_w
                FROM forecasts WHERE signature=? AND target_ms>=? AND target_ms<?
                AND (actual_w IS NOT NULL OR reference_actual_w IS NOT NULL)""",
                (signature, self.milliseconds(start), self.milliseconds(end)),
            ).fetchall()
        return [dict(row) for row in rows]

    def daily_metrics(
        self, signature: str, start: pd.Timestamp, end: pd.Timestamp, timezone: str
    ) -> list[dict[str, Any]]:
        """Evaluate frozen pre-midnight forecasts only against complete local days."""
        begin, until = self.milliseconds(start), self.milliseconds(end)
        with self.connection() as db:
            rows = db.execute(
                """SELECT b.target_ms,b.issue_ms,b.payload,o.actual_w FROM baselines b
                LEFT JOIN (SELECT target_ms,MAX(actual_w) actual_w FROM forecasts
                WHERE signature=? AND target_ms>=? AND target_ms<? GROUP BY target_ms) o
                ON b.target_ms=o.target_ms WHERE b.signature=? AND b.target_ms>=? AND b.target_ms<? ORDER BY b.target_ms""",
                (signature, begin, until, signature, begin, until),
            ).fetchall()
        days: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            time = pd.Timestamp(row["target_ms"], unit="ms", tz="UTC").tz_convert(timezone)
            days.setdefault(str(time.date()), []).append(
                {**dict(row), **json.loads(row["payload"])}
            )
        result = []
        for day, part in days.items():
            left = pd.Timestamp(day, tz=timezone)
            right = left + pd.DateOffset(days=1)
            expected = int((right - left).total_seconds() / 900)
            complete = (
                right <= end
                and len(part) == expected
                and all(
                    r["actual_w"] is not None and r["issue_ms"] < self.milliseconds(left)
                    for r in part
                )
            )
            result.append(
                dict(
                    day=day,
                    complete=bool(complete),
                    slots=len(part),
                    expected_slots=expected,
                    observed_slots=sum(r["actual_w"] is not None for r in part),
                    actual_kwh=sum(r["actual_w"] for r in part) * 0.25 / 1000 if complete else None,
                    **{
                        method + "_kwh": sum(r["plant_" + method + "_w"] for r in part)
                        * 0.25
                        / 1000
                        if complete
                        and all(r.get("plant_" + method + "_w") is not None for r in part)
                        else None
                        for method in ("raw", "calibrated", "local", "adaptive")
                    },
                )
            )
        return result

    def latest(self, start_ms: int) -> list[dict[str, Any]]:
        """Current public curve, distinct from the immutable validation archive."""
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM latest WHERE target_ms>=? ORDER BY target_ms LIMIT 1600", (start_ms,)
            ).fetchall()
        return [
            {
                "target_ms": row["target_ms"],
                "issue_ms": row["issue_ms"],
                **json.loads(row["payload"]),
            }
            for row in rows
        ]

    def prune(self, cutoff: pd.Timestamp, days: int = 60) -> None:
        """Bound only this provider's private archive; ordinary EOS data is untouched."""
        oldest = self.milliseconds(cutoff) - days * 86400000
        with self.connection() as db:
            for statement in (
                "DELETE FROM samples WHERE time_ms<?",
                "DELETE FROM forecasts WHERE target_ms<?",
                "DELETE FROM baselines WHERE target_ms<?",
                "DELETE FROM latest WHERE target_ms<?",
                "DELETE FROM weather WHERE issue_ms<?",
                "DELETE FROM longforecasts WHERE target_ms<?",
            ):
                db.execute(statement, (oldest,))
