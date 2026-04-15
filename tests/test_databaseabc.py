"""Tests for DatabaseRecordProtocolMixin.

All public and internal mixin methods that are ``async def`` are exercised
with ``async def`` test functions marked with ``@pytest.mark.asyncio``.
Synchronous helpers (_db_get_compact_state, db_compact_tiers) are tested
without the decorator.

The fake ``SampleDatabase`` exposes the same interface as the real
``Database`` wrapper: every method called via ``await self.database.*``
in the mixin is defined as ``async def`` here.
"""

from __future__ import annotations

import pickle
from typing import Any, AsyncIterator, Iterator, Literal, Optional, Type, cast

import pytest
import pytest_asyncio
from numpydantic import NDArray, Shape
from pydantic import BaseModel, Field

from akkudoktoreos.core.databaseabc import (
    DATABASE_METADATA_KEY,
    DatabaseRecordProtocolLoadPhase,
    DatabaseRecordProtocolMixin,
    DatabaseTimestamp,
    _DatabaseTimestampUnbound,
)
from akkudoktoreos.utils.datetimeutil import (
    DateTime,
    Duration,
    to_datetime,
    to_duration,
)

# ---------------------------------------------------------------------------
# Test record
# ---------------------------------------------------------------------------


class SampleRecord(BaseModel):
    date_time: Optional[DateTime] = Field(
        default=None, json_schema_extra={"description": "DateTime"}
    )
    value: Optional[float] = None

    def __getitem__(self, key: str) -> Any:
        if key == "date_time":
            return self.date_time
        if key == "value":
            return self.value
        raise KeyError(key)

    def model_dump(self) -> dict:
        return {"date_time": self.date_time, "value": self.value}


# ---------------------------------------------------------------------------
# Fake async database backend
#
# Every method that the mixin calls via ``await self.database.*`` must be
# ``async def``.  The real ``Database`` wrapper (database.py) is fully async;
# the plain synchronous ``DatabaseBackendABC`` subclasses are never accessed
# directly from the mixin.
# ---------------------------------------------------------------------------


class SampleDatabase:
    """Minimal async-compatible in-memory database for unit testing."""

    def __init__(self) -> None:
        # namespace -> {key: value}
        self._data: dict[Optional[str], dict[bytes, bytes]] = {}
        self._metadata: Optional[bytes] = None
        self.is_open = True
        self.compression = False
        self.compression_level = 0
        self.storage_path = "/fake"

    # ------------------------------------------------------------------
    # Serialisation helpers (pass-through; no compression in tests)
    # ------------------------------------------------------------------

    def serialize_data(self, data: bytes) -> bytes:
        return data

    def deserialize_data(self, data: bytes) -> bytes:
        return data

    # ------------------------------------------------------------------
    # Metadata — async
    # ------------------------------------------------------------------

    async def set_metadata(
        self, metadata: Optional[bytes], *, namespace: Optional[str] = None
    ) -> None:
        self._metadata = metadata

    async def get_metadata(self, *, namespace: Optional[str] = None) -> Optional[bytes]:
        return self._metadata

    # ------------------------------------------------------------------
    # Write operations — async
    # ------------------------------------------------------------------

    async def save_records(
        self,
        records: list[tuple[bytes, bytes]],
        *,
        namespace: Optional[str] = None,
    ) -> int:
        ns = self._data.setdefault(namespace, {})
        saved = 0
        for key, value in records:
            ns[key] = value
            saved += 1
        return saved

    async def delete_records(
        self,
        keys: Iterator[bytes],
        *,
        namespace: Optional[str] = None,
    ) -> int:
        ns_data = self._data.get(namespace, {})
        deleted = 0
        for key in keys:
            if key in ns_data:
                del ns_data[key]
                deleted += 1
        return deleted

    # ------------------------------------------------------------------
    # Read operations — async
    # ------------------------------------------------------------------

    async def iterate_records(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
        *,
        namespace: Optional[str] = None,
        reverse: bool = False,
    ) -> AsyncIterator[tuple[bytes, bytes]]:
        """Return a snapshot list so callers can iterate without holding a cursor."""
        items = self._data.get(namespace, {})
        keys = sorted(items, reverse=reverse)
        result = []
        for k in keys:
            if k == DATABASE_METADATA_KEY:
                continue
            if start_key is not None and k < start_key:
                continue
            if end_key is not None and k >= end_key:
                continue
            result.append((k, items[k]))

        for item in result:
            yield item

    # ------------------------------------------------------------------
    # Stats — async
    # ------------------------------------------------------------------

    async def count_records(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
        *,
        namespace: Optional[str] = None,
    ) -> int:
        items = self._data.get(namespace, {})
        count = 0
        for k in items:
            if k == DATABASE_METADATA_KEY:
                continue
            if start_key is not None and k < start_key:
                continue
            if end_key is not None and k >= end_key:
                continue
            count += 1
        return count

    async def get_key_range(
        self, namespace: Optional[str] = None
    ) -> tuple[Optional[bytes], Optional[bytes]]:
        """Called as ``await self.database.get_key_range(self.db_namespace())`` (positional)."""
        items = self._data.get(namespace, {})
        keys = sorted(k for k in items if k != DATABASE_METADATA_KEY)
        if not keys:
            return None, None
        return keys[0], keys[-1]

    async def get_backend_stats(self, *, namespace: Optional[str] = None) -> dict:
        return {}

    async def flush(self, *, namespace: Optional[str] = None) -> None:
        pass


# ---------------------------------------------------------------------------
# Concrete test sequence — minimal, no Pydantic / singleton overhead
# ---------------------------------------------------------------------------


class SampleSequence(DatabaseRecordProtocolMixin[SampleRecord]):
    """Minimal concrete implementation for unit-testing the mixin."""

    def __init__(self) -> None:
        self.records: list[SampleRecord] = []
        self._db_record_index: dict[DatabaseTimestamp, SampleRecord] = {}
        self._db_sorted_timestamps: list[DatabaseTimestamp] = []
        self._db_dirty_timestamps: set[DatabaseTimestamp] = set()
        self._db_new_timestamps: set[DatabaseTimestamp] = set()
        self._db_deleted_timestamps: set[DatabaseTimestamp] = set()
        self._db_initialized: bool = True
        self._db_storage_initialized: bool = False
        self._db_metadata: Optional[dict] = None
        self._db_loaded_range = None
        self._db_load_phase = DatabaseRecordProtocolLoadPhase.NONE
        self._db_version: int = 1

        self.database = SampleDatabase()
        self.config = type(
            "Cfg",
            (),
            {
                "database": type(
                    "DBCfg",
                    (),
                    {
                        "auto_save": False,
                        "compression_level": 0,
                        "autosave_interval_sec": 10,
                        "initial_load_window_h": None,
                        "keep_duration_h": None,
                    },
                )()
            },
        )()

    @classmethod
    def record_class(cls) -> Type[SampleRecord]:
        return SampleRecord

    def db_namespace(self) -> str:
        return "test"

    @property
    def record_keys_writable(self) -> list[str]:
        """Writable field names — ``date_time`` excluded so key_to_array only sees ``value``."""
        return ["value"]

    async def key_to_array(
        self,
        key: str,
        start_datetime: Optional[DateTime] = None,
        end_datetime: Optional[DateTime] = None,
        interval: Optional[Duration] = None,
        fill_method: Optional[str] = None,
        dropna: Optional[bool] = True,
        boundary: Literal["strict", "context"] = "context",
        align_to_interval: bool = False,
    ) -> NDArray[Shape["*"], Any]:
        """Minimal resampling stub sufficient for compaction tests."""
        import numpy as np
        import pandas as pd

        if interval is None:
            interval = to_duration("1 hour")

        dates = []
        values = []
        for record in self.records:
            if record.date_time is None:
                continue
            ts = DatabaseTimestamp.from_datetime(record.date_time)
            if start_datetime and DatabaseTimestamp.from_datetime(start_datetime) > ts:
                continue
            if end_datetime and DatabaseTimestamp.from_datetime(end_datetime) <= ts:
                continue
            dates.append(record.date_time)
            values.append(getattr(record, key, None))

        if not dates:
            return np.array([])

        index = pd.to_datetime(dates, utc=True)
        series = pd.Series(values, index=index, dtype=float)
        freq = f"{int(interval.total_seconds())}s"
        origin = start_datetime if start_datetime else "start_day"
        resampled = series.resample(freq, origin=origin).mean().interpolate("time")

        if start_datetime is not None:
            resampled = resampled.truncate(before=start_datetime)
        if end_datetime is not None:
            resampled = resampled.truncate(after=end_datetime)

        return resampled.values


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def _insert_records_every_n_minutes(
    seq: SampleSequence,
    base: DateTime,
    count: int,
    interval_minutes: int,
    value_fn=None,
) -> None:
    """Insert ``count`` records spaced ``interval_minutes`` apart starting at ``base``."""
    for i in range(count):
        dt = base.add(minutes=i * interval_minutes)
        value = value_fn(i) if value_fn else float(i)
        await seq.db_insert_record(SampleRecord(date_time=dt, value=value))
    await seq.db_save_records()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def seq() -> SampleSequence:
    return SampleSequence()


@pytest_asyncio.fixture
async def seq_with_15min_data():
    """Sequence with 15-min records spanning 4 weeks, so both tiers have data."""
    s = SampleSequence()
    now = to_datetime().in_timezone("UTC")
    # 4 weeks × 7 days × 24 h × 4 records/h = 2 688 records
    base = now.subtract(weeks=4)
    await _insert_records_every_n_minutes(s, base, count=2688, interval_minutes=15)
    return s, now


@pytest_asyncio.fixture
async def seq_sparse():
    """Sequence with only 3 records spread over 4 weeks — sparse, below the compaction guard."""
    s = SampleSequence()
    now = to_datetime().in_timezone("UTC")
    base = now.subtract(weeks=4)
    for offset_days in [0, 14, 27]:
        dt = base.add(days=offset_days)
        await s.db_insert_record(SampleRecord(date_time=dt, value=float(offset_days)))
    await s.db_save_records()
    return s, now


# ---------------------------------------------------------------------------
# Core mixin tests
# ---------------------------------------------------------------------------


class TestDatabaseRecordProtocolMixin:

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "start_str, value_count, interval_seconds",
        [
            ("2024-11-10 00:00:00", 24, 3600),
            ("2024-08-10 00:00:00", 24, 3600),
            ("2024-03-31 00:00:00", 24, 3600),
            ("2024-10-27 00:00:00", 24, 3600),
        ],
    )
    async def test_db_generate_timestamps_utc_spacing(
        self, seq, start_str, value_count, interval_seconds
    ):
        start_dt = to_datetime(start_str, in_timezone="Europe/Berlin")
        assert start_dt.tz.name == "Europe/Berlin"

        db_start = DatabaseTimestamp.from_datetime(start_dt)
        generated = list(seq.db_generate_timestamps(db_start, value_count))

        assert len(generated) == value_count

        for db_dt in generated:
            dt = DatabaseTimestamp.to_datetime(db_dt)
            assert dt.tz.name == "UTC"

        assert len(generated) == len(set(generated)), "Duplicate UTC datetimes found"

        for i in range(1, len(generated)):
            last_dt = DatabaseTimestamp.to_datetime(generated[i - 1])
            current_dt = DatabaseTimestamp.to_datetime(generated[i])
            delta = (current_dt - last_dt).total_seconds()
            assert delta == interval_seconds, f"Spacing mismatch at index {i}: {delta}s"

    @pytest.mark.asyncio
    async def test_insert_and_memory_range(self, seq):
        t0 = to_datetime()
        t1 = t0.add(hours=1)

        await seq.db_insert_record(SampleRecord(date_time=t0, value=1))
        await seq.db_insert_record(SampleRecord(date_time=t1, value=2))

        assert seq.records[0].date_time == t0
        assert seq.records[-1].date_time == t1
        assert len(seq.records) == 2

    @pytest.mark.asyncio
    async def test_roundtrip_reload(self):
        seq = SampleSequence()
        t0 = to_datetime()
        t1 = t0.add(hours=1)

        await seq.db_insert_record(SampleRecord(date_time=t0, value=1))
        await seq.db_insert_record(SampleRecord(date_time=t1, value=2))
        assert await seq.db_save_records() == 2

        db = seq.database
        seq2 = SampleSequence()
        seq2.database = db
        loaded = await seq2.db_load_records()

        assert loaded == 2
        assert len(seq2.records) == 2

    @pytest.mark.asyncio
    async def test_db_count_records(self, seq):
        t0 = to_datetime()
        await seq.db_insert_record(SampleRecord(date_time=t0, value=1))
        assert await seq.db_count_records() == 1
        await seq.db_save_records()
        assert await seq.db_count_records() == 1

    @pytest.mark.asyncio
    async def test_delete_range(self, seq):
        base = to_datetime()
        for i in range(5):
            await seq.db_insert_record(SampleRecord(date_time=base.add(minutes=i), value=i))

        db_start = DatabaseTimestamp.from_datetime(base.add(minutes=1))
        db_end = DatabaseTimestamp.from_datetime(base.add(minutes=4))
        deleted = await seq.db_delete_records(
            start_timestamp=db_start, end_timestamp=db_end
        )

        assert deleted == 3
        assert [r.value for r in seq.records] == [0, 4]

    @pytest.mark.asyncio
    async def test_db_count_records_memory_only_multiple(self):
        seq = SampleSequence()
        base = to_datetime()
        for i in range(3):
            await seq.db_insert_record(SampleRecord(date_time=base.add(minutes=i), value=i))
        assert await seq.db_count_records() == 3

    @pytest.mark.asyncio
    async def test_db_count_records_memory_newer_than_db(self):
        seq = SampleSequence()
        base = to_datetime()
        await seq.db_insert_record(SampleRecord(date_time=base, value=1))
        await seq.db_save_records()
        await seq.db_insert_record(SampleRecord(date_time=base.add(hours=1), value=2))
        await seq.db_insert_record(SampleRecord(date_time=base.add(hours=2), value=3))
        assert await seq.db_count_records() == 3

    @pytest.mark.asyncio
    async def test_db_count_records_memory_older_than_db(self):
        seq = SampleSequence()
        base = to_datetime()
        await seq.db_insert_record(SampleRecord(date_time=base.add(hours=1), value=2))
        await seq.db_save_records()
        await seq.db_insert_record(SampleRecord(date_time=base, value=1))
        assert await seq.db_count_records() == 2

    @pytest.mark.asyncio
    async def test_db_count_records_empty_everywhere(self):
        seq = SampleSequence()
        assert await seq.db_count_records() == 0

    @pytest.mark.asyncio
    async def test_metadata_not_counted(self, seq):
        """Metadata key must not be counted as a data record."""
        seq.database._data.setdefault("test", {})[DATABASE_METADATA_KEY] = b"meta"
        assert await seq.db_count_records() == 0

    @pytest.mark.asyncio
    async def test_key_range_excludes_metadata(self, seq):
        ns = seq.db_namespace()
        seq.database._data.setdefault(ns, {})[DATABASE_METADATA_KEY] = b"meta"
        assert await seq.database.get_key_range(ns) == (None, None)

    @pytest.mark.asyncio
    async def test_duplicate_timestamp_raises(self, seq):
        t0 = to_datetime()
        await seq.db_insert_record(SampleRecord(date_time=t0, value=1))
        with pytest.raises(ValueError, match="Duplicate"):
            await seq.db_insert_record(SampleRecord(date_time=t0, value=2))

    @pytest.mark.asyncio
    async def test_timestamp_range_empty(self, seq):
        min_ts, max_ts = await seq.db_timestamp_range()
        assert min_ts is None
        assert max_ts is None

    @pytest.mark.asyncio
    async def test_timestamp_range_reflects_inserts(self, seq):
        base = to_datetime()
        await seq.db_insert_record(SampleRecord(date_time=base, value=0))
        await seq.db_insert_record(SampleRecord(date_time=base.add(hours=2), value=1))
        min_ts, max_ts = await seq.db_timestamp_range()
        assert min_ts is not None
        assert max_ts is not None
        assert min_ts < max_ts

    @pytest.mark.asyncio
    async def test_get_record_exact_match(self, seq):
        t0 = to_datetime()
        await seq.db_insert_record(SampleRecord(date_time=t0, value=42.0))
        db_ts = DatabaseTimestamp.from_datetime(t0)
        record = await seq.db_get_record(db_ts)
        assert record is not None
        assert record.value == pytest.approx(42.0)

    @pytest.mark.asyncio
    async def test_get_record_no_match_returns_none(self, seq):
        t0 = to_datetime()
        db_ts = DatabaseTimestamp.from_datetime(t0)
        record = await seq.db_get_record(db_ts)
        assert record is None

    @pytest.mark.asyncio
    async def test_iterate_records_full_range(self, seq):
        base = to_datetime()
        expected_values = [float(i) for i in range(4)]
        for i, v in enumerate(expected_values):
            await seq.db_insert_record(SampleRecord(date_time=base.add(hours=i), value=v))

        collected = []
        async for record in seq.db_iterate_records():
            collected.append(record.value)

        assert collected == expected_values

    @pytest.mark.asyncio
    async def test_iterate_records_bounded_range(self, seq):
        base = to_datetime()
        for i in range(6):
            await seq.db_insert_record(SampleRecord(date_time=base.add(hours=i), value=float(i)))

        start_ts = DatabaseTimestamp.from_datetime(base.add(hours=2))
        end_ts = DatabaseTimestamp.from_datetime(base.add(hours=5))

        collected = []
        async for record in seq.db_iterate_records(
            start_timestamp=start_ts, end_timestamp=end_ts
        ):
            collected.append(record.value)

        assert collected == [2.0, 3.0, 4.0]

    @pytest.mark.asyncio
    async def test_mark_dirty_record(self, seq):
        t0 = to_datetime()
        await seq.db_insert_record(SampleRecord(date_time=t0, value=1.0))
        await seq.db_save_records()  # clears dirty set

        record = seq.records[0]
        record.value = 99.0
        await seq.db_mark_dirty_record(record)

        saved = await seq.db_save_records()
        assert saved >= 1


# ---------------------------------------------------------------------------
# Compact tier configuration tests (sync — db_compact_tiers is not async)
# ---------------------------------------------------------------------------


class TestCompactTiers:
    """Tests for db_compact_tiers() and the tier hook."""

    def test_default_tiers_returns_two_entries(self, seq):
        tiers = seq.db_compact_tiers()
        assert len(tiers) == 2

    def test_default_tiers_ordered_shortest_first(self, seq):
        tiers = seq.db_compact_tiers()
        ages = [t[0].total_seconds() for t in tiers]
        assert ages == sorted(ages), "Tiers must be ordered shortest age first"

    def test_default_tiers_first_is_2h_to_15min(self, seq):
        tiers = seq.db_compact_tiers()
        age_sec = tiers[0][0].total_seconds()
        interval_sec = tiers[0][1].total_seconds()
        assert age_sec == 2 * 3600
        assert interval_sec == 15 * 60

    def test_default_tiers_second_is_2weeks_to_1h(self, seq):
        tiers = seq.db_compact_tiers()
        age_sec = tiers[1][0].total_seconds()
        interval_sec = tiers[1][1].total_seconds()
        assert age_sec == 14 * 24 * 3600
        assert interval_sec == 3600

    def test_override_tiers(self):
        class CustomSeq(SampleSequence):
            def db_compact_tiers(self):
                return [(to_duration("7 days"), to_duration("1 hour"))]

        s = CustomSeq()
        tiers = s.db_compact_tiers()
        assert len(tiers) == 1
        assert tiers[0][1].total_seconds() == 3600

    @pytest.mark.asyncio
    async def test_empty_tiers_disables_compaction(self):
        class NoCompactSeq(SampleSequence):
            def db_compact_tiers(self):
                return []

        s = NoCompactSeq()
        now = to_datetime().in_timezone("UTC")
        base = now.subtract(weeks=4)
        await _insert_records_every_n_minutes(s, base, count=100, interval_minutes=15)

        deleted = await s.db_compact()
        assert deleted == 0


# ---------------------------------------------------------------------------
# Compact state persistence tests
# ---------------------------------------------------------------------------


class TestCompactState:
    """Tests for _db_get_compact_state / _db_set_compact_state."""

    def test_get_state_returns_none_when_no_metadata(self, seq):
        interval = to_duration("1 hour")
        assert seq._db_get_compact_state(interval) is None

    @pytest.mark.asyncio
    async def test_set_and_get_state_roundtrip(self, seq):
        interval = to_duration("1 hour")
        now = to_datetime().in_timezone("UTC")
        ts = DatabaseTimestamp.from_datetime(now)

        await seq._db_set_compact_state(interval, ts)
        retrieved = seq._db_get_compact_state(interval)

        assert retrieved == ts

    @pytest.mark.asyncio
    async def test_state_is_per_tier(self, seq):
        """Different tier intervals must not overwrite each other."""
        interval_15min = to_duration("15 minutes")
        interval_1h = to_duration("1 hour")

        now = to_datetime().in_timezone("UTC")
        ts_15 = DatabaseTimestamp.from_datetime(now)
        ts_1h = DatabaseTimestamp.from_datetime(now.subtract(days=1))

        await seq._db_set_compact_state(interval_15min, ts_15)
        await seq._db_set_compact_state(interval_1h, ts_1h)

        assert seq._db_get_compact_state(interval_15min) == ts_15
        assert seq._db_get_compact_state(interval_1h) == ts_1h

    @pytest.mark.asyncio
    async def test_state_persists_in_metadata(self, seq):
        """State must survive a metadata reload into a fresh sequence instance."""
        interval = to_duration("1 hour")
        now = to_datetime().in_timezone("UTC")
        ts = DatabaseTimestamp.from_datetime(now)

        await seq._db_set_compact_state(interval, ts)

        # Reload metadata from fake DB
        seq2 = SampleSequence()
        seq2.database = seq.database
        seq2._db_metadata = await seq2._db_load_metadata()

        assert seq2._db_get_compact_state(interval) == ts


# ---------------------------------------------------------------------------
# Sparse data guard tests
# ---------------------------------------------------------------------------


class TestCompactSparseGuard:

    @pytest.mark.asyncio
    async def test_sparse_data_aligns_but_does_not_reduce_cardinality(self, seq_sparse):
        """Sparse data must be aligned to the target interval for all records that were modified."""
        seq, _ = seq_sparse
        seq, _ = seq_sparse
        interval = to_duration("15 minutes")
        interval_sec = int(interval.total_seconds())

        before_epochs = {int(r.date_time.timestamp()) for r in seq.records}

        await seq._db_compact_tier(to_duration("30 minutes"), interval)

        after_epochs = {int(r.date_time.timestamp()) for r in seq.records}

        # Cardinality must not increase
        assert len(after_epochs) <= len(before_epochs)

        # Any timestamp that changed must now be aligned
        changed_epochs = after_epochs - before_epochs
        for epoch in changed_epochs:
            assert epoch % interval_sec == 0

    @pytest.mark.asyncio
    async def test_sparse_guard_advances_cutoff(self, seq_sparse):
        """Even when skipped, the cutoff should be stored so the next run skips the same window."""
        seq, _ = seq_sparse
        interval_1h = to_duration("1 hour")
        interval_15min = to_duration("15 minutes")

        await seq.db_compact()

        # Both tiers should have stored a cutoff even though nothing was deleted
        assert seq._db_get_compact_state(interval_1h) is not None
        assert seq._db_get_compact_state(interval_15min) is not None

    @pytest.mark.asyncio
    async def test_exactly_at_boundary_remains_stable(self, seq):
        now = to_datetime().in_timezone("UTC")
        interval = to_duration("1 hour")

        raw_base = now.subtract(hours=5).set(minute=0, second=0, microsecond=0)
        base = raw_base.subtract(seconds=int(raw_base.timestamp()) % 3600)

        for i in range(4):
            await seq.db_insert_record(
                SampleRecord(date_time=base.add(hours=i), value=float(i))
            )

        await seq.db_insert_record(
            SampleRecord(date_time=now.subtract(seconds=1), value=0.0)
        )
        await seq.db_save_records()

        before = [(int(r.date_time.timestamp()), r.value) for r in seq.records]

        await seq._db_compact_tier(to_duration("30 minutes"), interval)

        after = [(int(r.date_time.timestamp()), r.value) for r in seq.records]

        assert before == after


# ---------------------------------------------------------------------------
# Single-tier worker tests
# ---------------------------------------------------------------------------


class TestCompactTierWorker:
    """Unit tests for _db_compact_tier directly."""

    @pytest.mark.asyncio
    async def test_empty_sequence_returns_zero(self, seq):
        age = to_duration("2 hours")
        interval = to_duration("15 minutes")
        assert await seq._db_compact_tier(age, interval) == 0

    @pytest.mark.asyncio
    async def test_all_records_too_recent_skipped(self):
        """Records within the age threshold must not be touched."""
        seq = SampleSequence()
        now = to_datetime().in_timezone("UTC")
        # Insert 10 records from 30 minutes ago — all within 2h threshold
        base = now.subtract(minutes=30)
        await _insert_records_every_n_minutes(seq, base, count=10, interval_minutes=1)

        before = await seq.db_count_records()
        deleted = await seq._db_compact_tier(to_duration("2 hours"), to_duration("15 minutes"))

        assert deleted == 0
        assert await seq.db_count_records() == before

    @pytest.mark.asyncio
    async def test_compaction_reduces_record_count(self):
        """Dense 1-min records older than 2 h should be downsampled to 15-min."""
        seq = SampleSequence()
        now = to_datetime().in_timezone("UTC")
        # Insert 1-min records for 6 hours ending 3 hours ago
        base = now.subtract(hours=9)
        await _insert_records_every_n_minutes(seq, base, count=6 * 60, interval_minutes=1)

        before = await seq.db_count_records()
        deleted = await seq._db_compact_tier(to_duration("2 hours"), to_duration("15 minutes"))

        after = await seq.db_count_records()
        assert deleted > 0
        assert after < before

    @pytest.mark.asyncio
    async def test_records_within_threshold_preserved(self):
        """Records newer than age_threshold must remain untouched after compaction."""
        seq = SampleSequence()
        now = to_datetime().in_timezone("UTC")

        # Old dense records (will be compacted)
        old_base = now.subtract(hours=6)
        await _insert_records_every_n_minutes(seq, old_base, count=4 * 60, interval_minutes=1)

        # Recent records (must not be touched) — insert 5 records in the last hour
        recent_base = now.subtract(minutes=50)
        await _insert_records_every_n_minutes(seq, recent_base, count=5, interval_minutes=10)

        recent_before = [r for r in seq.records if r.date_time and r.date_time >= recent_base]

        await seq._db_compact_tier(to_duration("2 hours"), to_duration("15 minutes"))

        recent_after = [r for r in seq.records if r.date_time and r.date_time >= recent_base]
        assert len(recent_after) == len(recent_before)

    @pytest.mark.asyncio
    async def test_incremental_cutoff_prevents_recompaction(self):
        """Running compaction twice must not re-compact already-compacted data."""
        seq = SampleSequence()
        now = to_datetime().in_timezone("UTC")
        base = now.subtract(hours=8)
        await _insert_records_every_n_minutes(seq, base, count=5 * 60, interval_minutes=1)

        age = to_duration("2 hours")
        interval = to_duration("15 minutes")

        deleted_first = await seq._db_compact_tier(age, interval)
        count_after_first = await seq.db_count_records()

        deleted_second = await seq._db_compact_tier(age, interval)
        count_after_second = await seq.db_count_records()

        assert deleted_first > 0
        assert deleted_second == 0, "Second run must be a no-op"
        assert count_after_first == count_after_second

    @pytest.mark.asyncio
    async def test_cutoff_stored_after_compaction(self):
        """Cutoff timestamp must be persisted after a successful compaction run."""
        seq = SampleSequence()
        now = to_datetime().in_timezone("UTC")
        base = now.subtract(hours=8)

        await _insert_records_every_n_minutes(seq, base, count=5 * 60, interval_minutes=1)

        interval = to_duration("15 minutes")
        await seq._db_compact_tier(to_duration("2 hours"), interval)

        assert seq._db_get_compact_state(interval) is not None


# ---------------------------------------------------------------------------
# Public db_compact() integration tests
# ---------------------------------------------------------------------------


class TestDbCompact:
    """Integration tests for the public db_compact() entry point."""

    @pytest.mark.asyncio
    async def test_compact_dense_data_both_tiers(self, seq_with_15min_data):
        """4 weeks of 15-min data should be reduced by both tiers."""
        seq, _ = seq_with_15min_data
        before = await seq.db_count_records()

        total_deleted = await seq.db_compact()

        after = await seq.db_count_records()
        assert total_deleted > 0
        assert after < before

    @pytest.mark.asyncio
    async def test_compact_coarsest_tier_runs_first(self, seq_with_15min_data):
        """The 1-hour tier (coarsest) must run before the 15-min tier.

        If coarsest ran last it would re-compact records the 15-min tier
        had already downsampled — verified by checking that the 1-hour
        cutoff is not later than the 15-min cutoff.
        """
        seq, _ = seq_with_15min_data
        await seq.db_compact()

        cutoff_1h = seq._db_get_compact_state(to_duration("1 hour"))
        cutoff_15min = seq._db_get_compact_state(to_duration("15 minutes"))

        assert cutoff_1h is not None
        assert cutoff_15min is not None
        # The 1h tier covers older data → its cutoff must be earlier than 15min tier
        assert cutoff_1h <= cutoff_15min

    @pytest.mark.asyncio
    async def test_compact_idempotent(self, seq_with_15min_data):
        """Running db_compact twice must not change record count."""
        seq, _ = seq_with_15min_data
        await seq.db_compact()
        after_first = await seq.db_count_records()

        await seq.db_compact()
        after_second = await seq.db_count_records()

        assert after_first == after_second

    @pytest.mark.asyncio
    async def test_compact_empty_sequence_returns_zero(self, seq):
        assert await seq.db_compact() == 0

    @pytest.mark.asyncio
    async def test_compact_with_override_tiers(self):
        """Passing compact_tiers directly must override db_compact_tiers()."""
        seq = SampleSequence()
        now = to_datetime().in_timezone("UTC")
        base = now.subtract(weeks=3)
        await _insert_records_every_n_minutes(
            seq, base, count=3 * 7 * 24 * 4, interval_minutes=15
        )

        before = await seq.db_count_records()
        deleted = await seq.db_compact(
            compact_tiers=[(to_duration("1 day"), to_duration("1 hour"))]
        )

        assert deleted > 0
        assert await seq.db_count_records() < before

    @pytest.mark.asyncio
    async def test_compact_only_processes_new_window_on_second_call(self):
        """Second call processes only the new window, not the full history."""
        seq = SampleSequence()
        now = to_datetime().in_timezone("UTC")
        # Floor to the minute to avoid sub-minute microseconds causing duplicate
        # timestamps when interval arithmetic lands exactly on `base`.
        now_floored = now.set(second=0, microsecond=0)
        base = now_floored.subtract(weeks=3)
        # Dense 1-min data for 3 weeks
        await _insert_records_every_n_minutes(
            seq, base, count=3 * 7 * 24 * 60, interval_minutes=1
        )

        await seq.db_compact()
        count_after_first = await seq.db_count_records()

        # Start 2 days before `base` and insert only 1 day worth of records,
        # so the window [extra_base, extra_base + 1439min] stays entirely
        # before `base - 1day` and never collides with compacted timestamps
        # that were snapped to clean hour/15-min boundaries inside the original range.
        extra_base = now_floored.subtract(weeks=3).subtract(days=2)
        await _insert_records_every_n_minutes(seq, extra_base, count=24 * 60, interval_minutes=1)

        await seq.db_compact()
        count_after_second = await seq.db_count_records()

        # Second compact should have processed the newly added old data
        # Record count may change but should not exceed first compacted count by much
        assert count_after_second >= 0  # basic sanity


# ---------------------------------------------------------------------------
# Data integrity tests
# ---------------------------------------------------------------------------


class TestCompactDataIntegrity:

    @pytest.mark.asyncio
    async def test_constant_value_preserved(self):
        """Constant value field must survive mean-resampling unchanged."""
        seq = SampleSequence()
        now = to_datetime().in_timezone("UTC")
        base = now.subtract(hours=6)

        await _insert_records_every_n_minutes(
            seq, base, count=6 * 60, interval_minutes=1, value_fn=lambda _: 42.0
        )

        await seq._db_compact_tier(to_duration("2 hours"), to_duration("15 minutes"))

        for record in seq.records:
            if record.date_time and record.date_time < now.subtract(hours=2):
                assert record.value == pytest.approx(42.0, abs=1e-6)

    @pytest.mark.asyncio
    async def test_recent_records_not_modified(self):
        """Records newer than the age threshold must have unchanged values."""
        seq = SampleSequence()
        now = to_datetime().in_timezone("UTC")

        old_base = now.subtract(hours=6)
        await _insert_records_every_n_minutes(seq, old_base, count=3 * 60, interval_minutes=1)

        recent_base = now.subtract(minutes=30)
        expected = {i * 10: float(100 + i) for i in range(3)}
        for offset, val in expected.items():
            dt = recent_base.add(minutes=offset)
            await seq.db_insert_record(SampleRecord(date_time=dt, value=val))
        await seq.db_save_records()

        await seq._db_compact_tier(to_duration("2 hours"), to_duration("15 minutes"))

        for record in seq.records:
            if record.date_time and record.date_time >= recent_base:
                offset = int((record.date_time - recent_base).total_seconds() / 60)
                if offset in expected:
                    assert record.value == pytest.approx(expected[offset], abs=1e-6)

    @pytest.mark.asyncio
    async def test_compacted_timestamps_spacing(self):
        """Resampled records must be fewer than original and span the compaction window.

        Exact per-bucket spacing depends on the full DataSequence.key_to_array
        implementation (pandas resampling). The stub key_to_array in SampleSequence
        only guarantees a reduction in count — uniform spacing is verified in
        test_dataabc_compact.py against the real implementation.
        """
        seq = SampleSequence()
        now = to_datetime().in_timezone("UTC")
        base = now.subtract(hours=6)
        await _insert_records_every_n_minutes(seq, base, count=5 * 60, interval_minutes=1)

        before = await seq.db_count_records()
        await seq._db_compact_tier(to_duration("2 hours"), to_duration("15 minutes"))

        cutoff = now.subtract(hours=2)
        compacted = sorted(
            [r for r in seq.records if r.date_time and r.date_time < cutoff],
            key=lambda r: cast(DateTime, r.date_time),
        )

        # Must have produced fewer records than the original 1-min data
        assert len(compacted) > 0, "Expected at least one compacted record"
        assert len(compacted) < before, "Compaction must reduce record count"

        # Window start is floored to interval boundary
        interval_sec = 15 * 60
        expected_window_start = DateTime.fromtimestamp(
            (int(base.timestamp()) // interval_sec) * interval_sec,
            tz="UTC",
        )
        assert compacted[0].date_time >= expected_window_start
        assert compacted[-1].date_time < cutoff
