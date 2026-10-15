"""Bounded, persistent issued PV forecasts and independently timed measurements."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, cast

import numpy as np
import pandas as pd

from akkudoktoreos.measurement.quality import MeasurementSample


class PVLearningStore:
    """Keep issuance and later observation separate; revisions cannot rewrite training."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
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
            """)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Use short atomic transactions and bound lock waits across ingestion/readout."""
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

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

    def resolve(self, signature: str, observed: pd.DataFrame, now: pd.Timestamp) -> None:
        """Attach first complete observations, preserving the original forecast."""
        updated = self.milliseconds(now)
        with self.connection() as db:
            for timestamp, values in observed.iterrows():
                target = self.milliseconds(cast(pd.Timestamp, timestamp))
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
                "SELECT * FROM forecasts WHERE signature=? AND target_ms>=? AND target_ms<? AND updated_ms>=? AND reference_actual_w IS NOT NULL ORDER BY updated_ms,id LIMIT ?",
                (signature, start_ms, end_ms, since_ms, limit),
            ).fetchall()
        return [{**dict(row), **json.loads(row["payload"]), "payload": None} for row in rows]

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
            ):
                db.execute(statement, (oldest,))
