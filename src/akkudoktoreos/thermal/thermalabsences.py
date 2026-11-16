"""Persistent store for reported absences set at run time (REST), merged into the presence forecast.

Entries survive a restart (JSON file in the EOS data folder, atomic replace). They are combined with
``presence.absences`` from the configuration and with absences sent in a plan request. A running
absence is never deleted by "back home": its end is set to now, so it keeps being excluded from
learning while it lies inside the history window.
"""

import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, model_validator

from akkudoktoreos.prediction.presence import PresenceAbsence

MAX_ENTRIES = 64


class AbsenceInput(BaseModel):
    """Body of a new or replaced absence. ``start`` defaults to now, ``end`` None = until revoked."""

    model_config = ConfigDict(extra="forbid")
    start: datetime | None = None
    end: datetime | None = None
    zone_ids: list[str] = Field(default_factory=list, max_length=16)
    label: str | None = Field(default=None, max_length=80)


class AbsenceRecord(BaseModel):
    """One stored absence; bounds are timezone-aware."""

    model_config = ConfigDict(extra="forbid")
    id: str
    start: datetime
    end: datetime | None = None
    zone_ids: list[str] = Field(default_factory=list, max_length=16)
    label: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def validate_period(self) -> "AbsenceRecord":
        """Aware bounds and a positive period."""
        if self.start.tzinfo is None or (self.end is not None and self.end.tzinfo is None):
            raise ValueError("Stored absence bounds must be timezone aware")
        if self.end is not None and self.end <= self.start:
            raise ValueError("Absence end must be after its start")
        return self

    def as_absence(self) -> PresenceAbsence:
        """Convert to the object the presence forecast understands."""
        return PresenceAbsence(
            start=self.start, end=self.end, zone_ids=list(self.zone_ids), label=self.label
        )

    def active_at(self, now: datetime) -> bool:
        """True while the absence is running."""
        return self.start <= now and (self.end is None or now < self.end)


class AbsenceStore:
    """Thread-safe JSON store; every change is written before it is reported."""

    def __init__(self, path: Path, timezone_name: str, history_days: int = 89) -> None:
        self.path = Path(path)
        self.tz = ZoneInfo(timezone_name)
        self.keep = timedelta(days=history_days + 2)
        self._lock = threading.Lock()

    # -- helpers -------------------------------------------------------------------------
    def _aware(self, value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=self.tz)

    def _read(self) -> list[AbsenceRecord]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return [AbsenceRecord.model_validate(row) for row in raw["absences"]]
        except (
            Exception
        ) as exc:  # a damaged file must never stop planning, nor be overwritten silently
            backup = self.path.with_name(
                self.path.name + f".damaged-{int(datetime.now().timestamp())}"
            )
            os.replace(self.path, backup)
            logger.error(
                f"Absence store unreadable ({exc}); moved to {backup.name}, starting empty"
            )
            return []

    def _write(self, rows: list[AbsenceRecord], now: datetime) -> None:
        rows = [r for r in rows if r.end is None or r.end > now - self.keep]
        if len(rows) > MAX_ENTRIES:
            raise ValueError(f"At most {MAX_ENTRIES} stored absences")
        payload = {"version": 1, "absences": [json.loads(r.model_dump_json()) for r in rows]}
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def _record(self, body: AbsenceInput, now: datetime, ident: str) -> AbsenceRecord:
        start = self._aware(body.start) if body.start is not None else now
        end = self._aware(body.end) if body.end is not None else None
        return AbsenceRecord(
            id=ident, start=start, end=end, zone_ids=body.zone_ids, label=body.label
        )

    # -- public API ------------------------------------------------------------------------
    def entries(self) -> list[AbsenceRecord]:
        """All stored entries, oldest first."""
        with self._lock:
            return sorted(self._read(), key=lambda r: r.start)

    def planning_absences(self) -> tuple[PresenceAbsence, ...]:
        """Entries as presence absences for the planner."""
        return tuple(r.as_absence() for r in self.entries())

    def add(self, body: AbsenceInput, now: datetime | None = None) -> AbsenceRecord:
        """Store a new absence and return it with its id."""
        now = now or datetime.now(timezone.utc)
        with self._lock:
            rows = self._read()
            record = self._record(body, now, uuid.uuid4().hex[:12])
            rows.append(record)
            self._write(rows, now)
            return record

    def replace(self, ident: str, body: AbsenceInput, now: datetime | None = None) -> AbsenceRecord:
        """Create or replace the entry with this id (idempotent for clients that keep their own id)."""
        now = now or datetime.now(timezone.utc)
        with self._lock:
            rows = [r for r in self._read() if r.id != ident]
            record = self._record(body, now, ident)
            rows.append(record)
            self._write(rows, now)
            return record

    def revoke(self, ident: str, now: datetime | None = None) -> AbsenceRecord | None:
        """Back home: end a running entry now (kept for learning); delete a future or just-reported one."""
        now = now or datetime.now(timezone.utc)
        with self._lock:
            rows = self._read()
            target = next((r for r in rows if r.id == ident), None)
            if target is None:
                return None
            if target.start > now - timedelta(
                seconds=60
            ):  # not yet running (or only just reported): nothing to keep
                rows.remove(target)
            elif target.end is None or target.end > now:
                target = target.model_copy(
                    update={"end": max(now, target.start + timedelta(seconds=1))}
                )
                rows = [target if r.id == ident else r for r in rows]
            self._write(rows, now)
            return target

    def back_home(self, now: datetime | None = None) -> list[AbsenceRecord]:
        """End every running absence now."""
        now = now or datetime.now(timezone.utc)
        ended = []
        for record in self.entries():
            if record.active_at(now):
                result = self.revoke(record.id, now)
                if result is not None:
                    ended.append(result)
        return ended

    def summary(self, now: datetime | None = None) -> dict:
        """Entries plus the running and the next one, in the installation's time zone."""
        now = now or datetime.now(timezone.utc)
        rows = self.entries()
        active = next((r for r in rows if r.active_at(now)), None)
        upcoming = next((r for r in rows if r.start > now), None)

        def local(record: AbsenceRecord | None):
            if record is None:
                return None
            data = json.loads(record.model_dump_json())
            data["start"] = record.start.astimezone(self.tz).isoformat()
            data["end"] = record.end.astimezone(self.tz).isoformat() if record.end else None
            return data

        return {
            "now": now.astimezone(self.tz).isoformat(),
            "timezone": str(self.tz),
            "active": local(active),
            "next": local(upcoming),
            "absences": [local(r) for r in rows],
        }
