"""Compaction tests for DataSequence and DataContainer.

These tests sit on top of the full DataSequence / DataProvider / DataContainer
stack (dataabc.py) and exercise compaction end-to-end, including the
DataContainer delegation path.

A temporary SQLite database is configured for the entire test session via the
`configure_database` autouse fixture so that DataSequence instances — which
use the real Database singleton via DatabaseMixin — have a working backend.
"""

from typing import List, Optional, Type

import numpy as np
import pytest
from pydantic import Field

from akkudoktoreos.core.dataabc import (
    DataContainer,
    DataProvider,
    DataRecord,
    DataSequence,
)
from akkudoktoreos.core.database import Database
from akkudoktoreos.core.databaseabc import DatabaseTimestamp
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime, to_duration

# ---------------------------------------------------------------------------
# Minimal concrete record / sequence / provider
# ---------------------------------------------------------------------------


class EnergyRecord(DataRecord):
    """Simple numeric record for compaction testing."""

    power_w: Optional[float] = Field(
        default=None, json_schema_extra={"description": "Power in Watts"}
    )
    price_eur: Optional[float] = Field(
        default=None, json_schema_extra={"description": "Price in EUR/kWh"}
    )


class EnergySequence(DataSequence):
    records: List[EnergyRecord] = Field(
        default_factory=list,
        json_schema_extra={"description": "List of energy records"},
    )

    @classmethod
    def record_class(cls) -> Type[EnergyRecord]:
        return EnergyRecord

    def db_namespace(self) -> str:
        return "energy_test"


class PriceSequence(DataSequence):
    """Price data — overrides tiers to keep 15-min resolution for 2 weeks."""

    records: List[EnergyRecord] = Field(
        default_factory=list,
        json_schema_extra={"description": "List of price records"},
    )

    @classmethod
    def record_class(cls) -> Type[EnergyRecord]:
        return EnergyRecord

    def db_namespace(self) -> str:
        return "price_test"

    def db_compact_tiers(self):
        # Price data: skip first tier (already at target resolution for 2 weeks)
        return [(to_duration("14 days"), to_duration("1 hour"))]


class EnergyProvider(DataProvider):
    records: List[EnergyRecord] = Field(
        default_factory=list,
        json_schema_extra={"description": "List of energy records"},
    )

    @classmethod
    def record_class(cls) -> Type[EnergyRecord]:
        return EnergyRecord

    def provider_id(self) -> str:
        return "EnergyProvider"

    def enabled(self) -> bool:
        return True

    def _update_data(self, force_update=False) -> None:
        pass

    def db_namespace(self) -> str:
        return self.provider_id()


class PriceProvider(DataProvider):
    records: List[EnergyRecord] = Field(
        default_factory=list,
        json_schema_extra={"description": "List of price records"},
    )

    @classmethod
    def record_class(cls) -> Type[EnergyRecord]:
        return EnergyRecord

    def provider_id(self) -> str:
        return "PriceProvider"

    def enabled(self) -> bool:
        return True

    def _update_data(self, force_update=False) -> None:
        pass

    def db_namespace(self) -> str:
        return self.provider_id()

    def db_compact_tiers(self):
        return [(to_duration("14 days"), to_duration("1 hour"))]


class EnergyContainer(DataContainer):
    providers: List[DataProvider] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _aligned_base(now: DateTime, interval_minutes: int = 15) -> DateTime:
    """Floor ``now`` to the nearest ``interval_minutes`` boundary.

    All fixtures that feed _fill_sequence use this so that compacted timestamps
    are predictably on clock-round boundaries and tests are deterministic.
    """
    interval_sec = interval_minutes * 60
    epoch = int(now.timestamp())
    return now.subtract(seconds=epoch % interval_sec).set(microsecond=0)


def _fill_sequence(
    seq: DataSequence,
    base: DateTime,
    count: int,
    interval_minutes: int,
    power_w: float = 1000.0,
    price_eur: float = 0.25,
) -> None:
    """Insert ``count`` EnergyRecords spaced ``interval_minutes`` apart.

    ``base`` should be interval-aligned (use ``_aligned_base``) so that
    compacted bucket timestamps are deterministic across all tests.
    """
    for i in range(count):
        dt = base.add(minutes=i * interval_minutes)
        rec = EnergyRecord(date_time=dt, power_w=power_w + i, price_eur=price_eur)
        seq.db_insert_record(rec)
    seq.db_save_records()


def _reset_singletons() -> None:
    """Reset all singleton classes used in these tests.

    DataProvider and DataSequence inherit SingletonMixin, meaning each subclass
    only ever has one instance. Without resetting between tests, state from one
    test (records, compaction metadata, monkey-patches) leaks into the next.
    """
    for cls in (EnergySequence, PriceSequence, EnergyProvider, PriceProvider, EnergyContainer):
        try:
            cls.reset_instance()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def configure_database(tmp_path):
    """Configure a fresh temporary SQLite database for every test.

    DataSequence uses the real Database singleton via DatabaseMixin.
    Without an open database backend, count_records() and all other DB
    operations raise RuntimeError('Database not configured').

    This fixture:
    1. Resets the Database singleton so the previous test's state is gone.
    2. Points the database config at a fresh per-test tmp_path directory.
    3. Opens a SQLite backend.
    4. Resets all sequence/provider/container singletons before and after.
    5. Tears everything down cleanly after each test.
    """
    _reset_singletons()

    # Reset the Database singleton itself
    Database.reset_instance()

    # Patch config to use SQLite in tmp_path
    db = Database()
    db.config.database.provider = "SQLite"
    db.config.general.data_folder_path = tmp_path
    db.open()

    yield

    # Teardown
    try:
        db.close()
    finally:
        _reset_singletons()
        try:
            Database.reset_instance()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def energy_seq():
    """Fresh EnergySequence with no data."""
    return EnergySequence()


@pytest.fixture
def dense_energy_seq():
    """EnergySequence with 4 weeks of 15-min records (~2688 records).

    The base timestamp is floored to a 15-min boundary so compacted bucket
    timestamps are deterministic and on clock-round marks.
    """
    seq = EnergySequence()
    now = to_datetime().in_timezone("UTC")
    base = _aligned_base(now.subtract(weeks=4), interval_minutes=15)
    _fill_sequence(seq, base, count=4 * 7 * 24 * 4, interval_minutes=15)
    return seq, now


@pytest.fixture
def dense_price_seq():
    """PriceSequence with 4 weeks of 15-min records.

    The base timestamp is floored to a 15-min boundary so compacted bucket
    timestamps are deterministic and on clock-round marks.
    """
    seq = PriceSequence()
    now = to_datetime().in_timezone("UTC")
    base = _aligned_base(now.subtract(weeks=4), interval_minutes=15)
    _fill_sequence(seq, base, count=4 * 7 * 24 * 4, interval_minutes=15)
    return seq, now


@pytest.fixture
def energy_container(energy_seq):
    """DataContainer with one EnergyProvider and one PriceProvider."""
    ep = EnergyProvider()
    pp = PriceProvider()
    container = EnergyContainer(providers=[ep, pp])
    return container, ep, pp


# ---------------------------------------------------------------------------
# DataSequence — tier configuration
# ---------------------------------------------------------------------------


class TestDataSequenceCompactTiers:

    def test_default_tiers_two_entries(self, energy_seq):
        tiers = energy_seq.db_compact_tiers()
        assert len(tiers) == 2

    def test_default_first_tier_2h_15min(self, energy_seq):
        tiers = energy_seq.db_compact_tiers()
        age_sec = tiers[0][0].total_seconds()
        interval_sec = tiers[0][1].total_seconds()
        assert age_sec == 2 * 3600
        assert interval_sec == 15 * 60

    def test_default_second_tier_2weeks_1h(self, energy_seq):
        tiers = energy_seq.db_compact_tiers()
        age_sec = tiers[1][0].total_seconds()
        interval_sec = tiers[1][1].total_seconds()
        assert age_sec == 14 * 24 * 3600
        assert interval_sec == 3600

    def test_price_sequence_overrides_to_single_tier(self):
        seq = PriceSequence()
        tiers = seq.db_compact_tiers()
        assert len(tiers) == 1
        assert tiers[0][0].total_seconds() == 14 * 24 * 3600
        assert tiers[0][1].total_seconds() == 3600

    def test_empty_tiers_disables_compaction(self):
        class NoCompact(EnergySequence):
            def db_compact_tiers(self):
                return []

        seq = NoCompact()
        now = to_datetime().in_timezone("UTC")
        base = _aligned_base(now.subtract(weeks=4), interval_minutes=15)
        _fill_sequence(seq, base, count=500, interval_minutes=15)
        assert seq.db_compact() == 0


# ---------------------------------------------------------------------------
# DataSequence — compaction behaviour
# ---------------------------------------------------------------------------


class TestDataSequenceCompact:

    def test_empty_sequence_returns_zero(self, energy_seq):
        assert energy_seq.db_compact() == 0

    def test_dense_data_reduces_count(self, dense_energy_seq):
        seq, _ = dense_energy_seq
        before = seq.db_count_records()
        deleted = seq.db_compact()
        assert deleted > 0
        assert seq.db_count_records() < before

    def test_all_fields_compacted(self, dense_energy_seq):
        """Both power_w and price_eur should be present on compacted records."""
        seq, now = dense_energy_seq
        seq.db_compact()

        cutoff = now.subtract(weeks=2)
        old_records = [r for r in seq.records if r.date_time and r.date_time < cutoff]

        assert len(old_records) > 0
        for rec in old_records:
            assert rec.power_w is not None, "power_w must survive compaction"
            assert rec.price_eur is not None, "price_eur must survive compaction"

    def test_recent_records_untouched(self, dense_energy_seq):
        """Records within 2 hours of now must not be compacted."""
        seq, now = dense_energy_seq
        cutoff = now.subtract(hours=2)

        # Snapshot recent values
        recent_before = {
            DatabaseTimestamp.from_datetime(r.date_time): r.power_w
            for r in seq.records
            if r.date_time and r.date_time >= cutoff
        }

        seq.db_compact()

        recent_after = {
            DatabaseTimestamp.from_datetime(r.date_time): r.power_w
            for r in seq.records
            if r.date_time and r.date_time >= cutoff
        }

        assert recent_before == recent_after

    def test_idempotent(self, dense_energy_seq):
        seq, _ = dense_energy_seq
        seq.db_compact()
        after_first = seq.db_count_records()

        seq.db_compact()
        after_second = seq.db_count_records()

        assert after_first == after_second

    def test_price_sequence_preserves_15min_in_recent_2weeks(self, dense_price_seq):
        """PriceSequence keeps 15-min resolution for data younger than 2 weeks."""
        seq, now = dense_price_seq
        seq.db_compact()

        two_weeks_ago = now.subtract(weeks=2)
        recent_records = [
            r for r in seq.records
            if r.date_time and r.date_time >= two_weeks_ago
        ]
        # Should still have ~4 records per hour = 15-min resolution
        if len(recent_records) > 1:
            diffs = []
            sorted_recs = sorted(recent_records, key=lambda r: r.date_time)
            for i in range(1, min(len(sorted_recs), 10)):
                diff = (sorted_recs[i].date_time - sorted_recs[i - 1].date_time).total_seconds()
                diffs.append(diff)
            # Average spacing should be ~15 min, not 60 min
            avg_spacing = sum(diffs) / len(diffs)
            assert avg_spacing <= 20 * 60, (
                f"Expected ~15min spacing in recent 2 weeks, got {avg_spacing/60:.1f} min"
            )

    def test_price_sequence_compacts_older_than_2weeks_to_1h(self, dense_price_seq):
        """PriceSequence compacts data older than 2 weeks to 1-hour resolution."""
        seq, now = dense_price_seq
        seq.db_compact()

        two_weeks_ago = now.subtract(weeks=2)
        old_records = sorted(
            [r for r in seq.records if r.date_time and r.date_time < two_weeks_ago],
            key=lambda r: r.date_time,
        )

        if len(old_records) > 1:
            diffs = []
            for i in range(1, min(len(old_records), 10)):
                diff = (old_records[i].date_time - old_records[i - 1].date_time).total_seconds()
                diffs.append(diff)
            avg_spacing = sum(diffs) / len(diffs)
            assert avg_spacing >= 50 * 60, (
                f"Expected ~1h spacing for old price data, got {avg_spacing/60:.1f} min"
            )

    def test_compact_with_custom_tiers_argument(self, dense_energy_seq):
        """db_compact(compact_tiers=...) overrides the instance's tiers."""
        seq, _ = dense_energy_seq
        before = seq.db_count_records()

        deleted = seq.db_compact(
            compact_tiers=[(to_duration("1 day"), to_duration("1 hour"))]
        )

        assert deleted > 0
        assert seq.db_count_records() < before

    def test_compacted_timestamps_are_clock_aligned(self, dense_energy_seq):
        """All timestamps produced by compaction must sit on UTC clock boundaries.

        _db_compact_tier floors its cutoff timestamps to interval boundaries, so
        the boundary between tiers is not exactly ``now - age`` but the floored
        version of it.  We compute the same floored cutoffs here.

        - Records older than floored 2-week cutoff → multiple of 3600 s
        - Records in floored 2h..2week band        → multiple of 900 s
        - Records younger than floored 2h cutoff   → unchanged
        """
        seq, now = dense_energy_seq
        seq.db_compact()

        # _db_compact_tier floors new_cutoff from db_max, not from wall-clock now.
        # Compute the same floored cutoffs that the implementation used.
        _, db_max_ts = seq.db_timestamp_range()
        # DatabaseTimestamp already imported at top of file
        db_max_epoch = int(DatabaseTimestamp.to_datetime(db_max_ts).timestamp())
        two_weeks_cutoff_epoch = ((db_max_epoch - 14*24*3600) // 3600) * 3600
        two_hours_cutoff_epoch  = ((db_max_epoch - 2*3600)    // 900)  * 900

        for rec in seq.records:
            if rec.date_time is None:
                continue
            epoch = int(rec.date_time.timestamp())
            if epoch < two_weeks_cutoff_epoch:
                assert epoch % 3600 == 0, (
                    f"Old record {rec.date_time} not on hour boundary"
                )
            elif epoch < two_hours_cutoff_epoch:
                assert epoch % 900 == 0, (
                    f"Mid record {rec.date_time} not on 15-min boundary"
                )


# ---------------------------------------------------------------------------
# DataSequence — data integrity after compaction
# ---------------------------------------------------------------------------


class TestDataSequenceCompactIntegrity:

    @staticmethod
    def _tier_cutoff(now, age_seconds: int, interval_seconds: int):
        """Compute the floored compaction cutoff the same way _db_compact_tier does.

        _db_compact_tier floors new_cutoff_dt to the interval boundary, so
        ``newest - age_threshold`` rounded down.  Tests must use the same value
        to correctly classify which tier a record falls into.
        """
        import math
        raw_epoch = int(now.subtract(seconds=age_seconds).timestamp())
        floored_epoch = (raw_epoch // interval_seconds) * interval_seconds
        return now.__class__.fromtimestamp(floored_epoch, tz=now.tzinfo)

    def test_constant_power_preserved(self):
        """Mean resampling of a constant must equal the constant."""
        seq = EnergySequence()
        now = to_datetime().in_timezone("UTC")
        # Use aligned base so bucket boundaries are deterministic
        base = _aligned_base(now.subtract(hours=6), interval_minutes=15)

        for i in range(6 * 60):  # 1-min records for 6 hours
            dt = base.add(minutes=i)
            seq.db_insert_record(EnergyRecord(date_time=dt, power_w=500.0, price_eur=0.30))
        seq.db_save_records()

        seq._db_compact_tier(to_duration("2 hours"), to_duration("15 minutes"))

        cutoff = now.subtract(hours=2)
        for rec in seq.records:
            if rec.date_time and rec.date_time < cutoff:
                assert rec.power_w == pytest.approx(500.0, abs=1e-3)
                assert rec.price_eur == pytest.approx(0.30, abs=1e-6)

    def test_record_count_monotonically_decreases(self):
        """Each successive tier run should never increase record count."""
        seq = EnergySequence()
        now = to_datetime().in_timezone("UTC")
        base = _aligned_base(now.subtract(weeks=4), interval_minutes=15)
        _fill_sequence(seq, base, count=4 * 7 * 24 * 4, interval_minutes=15)

        counts = [seq.db_count_records()]
        for age, interval in reversed(seq.db_compact_tiers()):
            seq._db_compact_tier(age, interval)
            counts.append(seq.db_count_records())

        for i in range(1, len(counts)):
            assert counts[i] <= counts[i - 1], (
                f"Record count increased from {counts[i-1]} to {counts[i]} at tier {i}"
            )

    def test_no_duplicate_timestamps_after_compaction(self, dense_energy_seq):
        """Compaction must not create duplicate timestamps."""
        seq, _ = dense_energy_seq
        seq.db_compact()

        timestamps = [
            DatabaseTimestamp.from_datetime(r.date_time)
            for r in seq.records
            if r.date_time is not None
        ]
        assert len(timestamps) == len(set(timestamps)), "Duplicate timestamps after compaction"

    def test_timestamps_remain_sorted(self, dense_energy_seq):
        """Records must remain in ascending order after compaction."""
        seq, _ = dense_energy_seq
        seq.db_compact()

        dts = [r.date_time for r in seq.records if r.date_time is not None]
        assert dts == sorted(dts)

    def test_compacted_old_timestamps_on_1h_boundaries(self, dense_energy_seq):
        """Records older than the floored 2-week cutoff must be on whole-hour UTC boundaries.

        _db_compact_tier floors new_cutoff to the interval boundary, so we must
        use the same floored cutoff to decide which records were compacted by the
        1-hour tier.  Records between the floored and raw cutoff may still be at
        15-min resolution from the previous tier.
        """
        seq, now = dense_energy_seq
        seq.db_compact()

        # _db_compact_tier floors new_cutoff from db_max (the newest record),
        # not from wall-clock now.  Derive the same floored cutoff here.
        _, db_max_ts = seq.db_timestamp_range()
        # DatabaseTimestamp already imported at top of file
        db_max_epoch = int(DatabaseTimestamp.to_datetime(db_max_ts).timestamp())
        two_weeks_cutoff_epoch = ((db_max_epoch - 14*24*3600) // 3600) * 3600
        two_weeks_cutoff_dt = DateTime.fromtimestamp(two_weeks_cutoff_epoch, tz="UTC")

        old_records = [r for r in seq.records if r.date_time and r.date_time < two_weeks_cutoff_dt]

        assert len(old_records) > 0, "Expected compacted records older than 2-week floored cutoff"
        for rec in old_records:
            epoch = int(rec.date_time.timestamp())
            assert epoch % 3600 == 0, (
                f"Old record at {rec.date_time} is not on an hour boundary"
            )

    def test_compacted_mid_timestamps_on_15min_boundaries(self):
        """Records compacted by the 15-min tier must land on 15-min UTC boundaries.

        We run _db_compact_tier directly with the 2h/15min tier on a sequence
        of 1-min records spanning 6 hours, then verify every compacted record
        sits on a :00/:15/:30/:45 UTC mark.

        The implementation computes new_cutoff as floor(newest - age, 900).
        We replicate that exact calculation to identify which records were in
        the compaction window.
        """
        seq = EnergySequence()
        now = to_datetime().in_timezone("UTC")
        base = _aligned_base(now.subtract(hours=6), interval_minutes=15)

        # 1-min records for 6 hours; newest record is at base + 359 min
        for i in range(6 * 60):
            dt = base.add(minutes=i)
            seq.db_insert_record(EnergyRecord(date_time=dt, power_w=500.0, price_eur=0.30))
        seq.db_save_records()

        seq._db_compact_tier(to_duration("2 hours"), to_duration("15 minutes"))

        # Replicate the implementation's floored cutoff exactly:
        # newest_dt = last inserted record = base + 359min
        # new_cutoff = floor(newest_dt - 2h, 900)
        newest_dt = base.add(minutes=6 * 60 - 1)
        raw_cutoff_epoch = int(newest_dt.subtract(hours=2).timestamp())
        window_end_epoch = (raw_cutoff_epoch // 900) * 900

        # Records before window_end_epoch must all be on 15-min boundaries
        compacted = [
            r for r in seq.records
            if r.date_time is not None
            and int(r.date_time.timestamp()) < window_end_epoch
        ]

        assert len(compacted) > 0, (
            f"Expected compacted records before window_end={window_end_epoch}; "
            f"got records at {[int(r.date_time.timestamp()) for r in seq.records if r.date_time]}"
        )
        for rec in compacted:
            assert rec.date_time is not None
            epoch = int(rec.date_time.timestamp())
            assert epoch % 900 == 0, (
                f"15-min-tier record at {rec.date_time} (epoch={epoch}) "
                f"is not on a 15-min boundary (epoch % 900 = {epoch % 900})"
            )

    def test_no_compacted_timestamps_between_boundaries(self, dense_energy_seq):
        """After compaction no record timestamp must fall between expected bucket boundaries.

        Records older than the floored 2-week cutoff (processed by the 1h tier)
        must be on hour marks.  Records in the 15-min band must be on 15-min marks.
        """
        seq, now = dense_energy_seq
        seq.db_compact()

        # Derive floored cutoffs from db_max — same reference as the implementation.
        _, db_max_ts = seq.db_timestamp_range()
        # DatabaseTimestamp already imported at top of file
        db_max_epoch = int(DatabaseTimestamp.to_datetime(db_max_ts).timestamp())
        two_weeks_cutoff_epoch = ((db_max_epoch - 14*24*3600) // 3600) * 3600
        two_hours_cutoff_epoch  = ((db_max_epoch - 2*3600)    // 900)  * 900

        for rec in seq.records:
            if rec.date_time is None:
                continue
            epoch = int(rec.date_time.timestamp())
            if epoch < two_weeks_cutoff_epoch:
                assert epoch % 3600 == 0, (
                    f"Record at {rec.date_time} is not hour-aligned in 1h-tier region"
                )
            elif epoch < two_hours_cutoff_epoch:
                assert epoch % (15 * 60) == 0, (
                    f"Record at {rec.date_time} is not 15min-aligned in 15min-tier region"
                )


# ---------------------------------------------------------------------------
# DataContainer — delegation
# ---------------------------------------------------------------------------


class TestDataContainerCompact:

    def test_compact_delegates_to_all_providers(self, energy_container):
        container, ep, pp = energy_container
        now = to_datetime().in_timezone("UTC")

        # Fill both providers with 4 weeks of 15-min data
        base = _aligned_base(now.subtract(weeks=4), interval_minutes=15)
        _fill_sequence(ep, base, count=4 * 7 * 24 * 4, interval_minutes=15)
        _fill_sequence(pp, base, count=4 * 7 * 24 * 4, interval_minutes=15)

        ep_before = ep.db_count_records()
        pp_before = pp.db_count_records()

        container.db_compact()

        assert ep.db_count_records() < ep_before, "EnergyProvider records should be compacted"
        assert pp.db_count_records() < pp_before, "PriceProvider records should be compacted"

    def test_compact_empty_container_no_error(self):
        container = EnergyContainer(providers=[])
        container.db_compact()  # must not raise

    def test_compact_provider_tiers_respected(self, energy_container):
        """PriceProvider with single 2-week tier must not compact recent 15-min data."""
        container, ep, pp = energy_container
        now = to_datetime().in_timezone("UTC")

        base = _aligned_base(now.subtract(weeks=4), interval_minutes=15)
        _fill_sequence(pp, base, count=4 * 7 * 24 * 4, interval_minutes=15)

        container.db_compact()

        # Price data in last 2 weeks should still be at 15-min resolution
        two_weeks_ago = now.subtract(weeks=2)
        recent = sorted(
            [r for r in pp.records if r.date_time and r.date_time >= two_weeks_ago],
            key=lambda r: r.date_time,
        )
        if len(recent) > 1:
            diff = (recent[1].date_time - recent[0].date_time).total_seconds()
            assert diff <= 20 * 60, (
                f"PriceProvider recent data should be ~15min, got {diff/60:.1f} min"
            )

    def test_compact_raises_on_provider_failure(self):
        """A provider that raises during compaction must bubble up as RuntimeError.

        Monkey-patching is blocked by Pydantic v2's __setattr__ validation, so
        we use a subclass that overrides db_compact instead.
        """
        class BrokenProvider(EnergyProvider):
            def db_compact(self, *args, **kwargs):
                raise ValueError("simulated failure")

            def provider_id(self) -> str:
                # Distinct id so it doesn't collide with EnergyProvider singleton
                return "BrokenProvider"

            def db_namespace(self) -> str:
                return self.provider_id()

        bp = BrokenProvider()
        container = EnergyContainer(providers=[bp])

        with pytest.raises(RuntimeError, match="fails on db_compact"):
            container.db_compact()

    def test_compact_idempotent_on_container(self, energy_container):
        container, ep, pp = energy_container
        now = to_datetime().in_timezone("UTC")
        base = _aligned_base(now.subtract(weeks=4), interval_minutes=15)
        _fill_sequence(ep, base, count=4 * 7 * 24 * 4, interval_minutes=15)
        _fill_sequence(pp, base, count=4 * 7 * 24 * 4, interval_minutes=15)

        container.db_compact()
        ep_after_first = ep.db_count_records()
        pp_after_first = pp.db_count_records()

        container.db_compact()
        assert ep.db_count_records() == ep_after_first
        assert pp.db_count_records() == pp_after_first


# ---------------------------------------------------------------------------
# Sparse guard — DataSequence level
# ---------------------------------------------------------------------------
#
# The sparse guard distinguishes three cases:
#
#   1. Sparse + already aligned  →  skip entirely (deleted=0, count unchanged)
#   2. Sparse + misaligned       →  snap timestamps in place (deleted>0, but
#                                    count stays the same or decreases if two
#                                    records collide on the same bucket)
#   3. Sparse collision          →  two records snap to the same bucket; values
#                                    are merged key-by-key; count decreases by 1
# ---------------------------------------------------------------------------


class TestDataSequenceSparseGuard:

    # ------------------------------------------------------------------
    # Case 1: sparse + already aligned → pure skip
    # ------------------------------------------------------------------

    def test_sparse_aligned_data_not_modified(self):
        """Sparse records that already sit on interval boundaries must not be touched.

        deleted must be 0 and record count must be unchanged.
        """
        seq = EnergySequence()
        now = to_datetime().in_timezone("UTC")
        base = now.subtract(weeks=4)

        # Insert exactly 3 records, each snapped to a whole hour (aligned)
        for offset_days in [0, 14, 27]:
            raw = base.add(days=offset_days)
            # Floor to nearest hour boundary so timestamp is already aligned
            aligned = raw.set(minute=0, second=0, microsecond=0)
            seq.db_insert_record(EnergyRecord(date_time=aligned, power_w=100.0))
        seq.db_save_records()

        before = seq.db_count_records()
        deleted = seq.db_compact()

        assert deleted == 0, "Aligned sparse records must not be deleted"
        assert seq.db_count_records() == before, "Record count must not change"

    def test_sparse_aligned_data_values_untouched(self):
        """Values of aligned sparse records must be preserved exactly."""
        seq = EnergySequence()
        now = to_datetime().in_timezone("UTC")
        base = now.subtract(weeks=4).set(minute=0, second=0, microsecond=0)

        seq.db_insert_record(EnergyRecord(date_time=base, power_w=42.0, price_eur=0.99))
        seq.db_save_records()

        seq.db_compact()

        remaining = [r for r in seq.records if r.date_time == base]
        assert len(remaining) == 1
        assert remaining[0].power_w == pytest.approx(42.0)
        assert remaining[0].price_eur == pytest.approx(0.99)

    # ------------------------------------------------------------------
    # Case 2: sparse + misaligned → timestamp snapping
    # ------------------------------------------------------------------

    @staticmethod
    def _make_snapping_seq(now, offsets_minutes, interval_minutes=10, age_minutes=30):
        """Build a sequence guaranteed to enter the sparse-snapping path.

        Key insight: _db_compact_tier measures age_threshold from db_max (the
        newest record in the database), not from wall-clock now.  We therefore
        insert a "newest anchor" record 1 second before now so that
        db_max ≈ now, making cutoff = db_max - age_threshold ≈ now - age_minutes.

        Critically, _db_compact_tier FLOORS the cutoff to the interval boundary:
            window_end_epoch = floor(anchor_epoch - age_sec, interval_sec)

        We replicate that exact floor here so that all test records are
        guaranteed to land before window_end regardless of what wall-clock
        time the test runs at (UTC CI vs. local non-UTC machines).

        The test records are placed at base + offset_minutes where base is
        chosen so that base + max(offsets) < window_end.

        resampled_count = window_width / interval_sec (ceiling).
        We require len(offsets_minutes) > resampled_count so the snapping
        path is entered rather than the pure-skip path.

        Returns (seq, age_threshold, target_interval, record_datetimes).
        """
        age_td = to_duration(f"{age_minutes} minutes")
        interval_td = to_duration(f"{interval_minutes} minutes")
        interval_sec = interval_minutes * 60
        age_sec = age_minutes * 60

        # Replicate the exact window_end the implementation will compute:
        #   anchor = now - 1s
        #   raw_cutoff = anchor - age_td
        #   window_end = floor(raw_cutoff, interval_sec)
        anchor_epoch = int(now.subtract(seconds=1).timestamp())
        raw_cutoff_epoch = anchor_epoch - age_sec
        window_end_epoch = (raw_cutoff_epoch // interval_sec) * interval_sec

        # Place base interval_sec before window_end so all records
        # (base + max_offset) are safely inside [window_start, window_end).
        # We need: base_epoch + max(offsets)*60 < window_end_epoch
        # Use: base_epoch = window_end_epoch - (max_offset + 2*interval_minutes + 1) * 60
        # Then floor base to interval boundary.
        max_offset = max(offsets_minutes) if offsets_minutes else 0
        margin_sec = (max_offset + 2 * interval_minutes + 1) * 60
        raw_base_epoch = window_end_epoch - margin_sec
        base_epoch = (raw_base_epoch // interval_sec) * interval_sec
        base = DateTime.fromtimestamp(base_epoch, tz="UTC")

        seq = EnergySequence()
        dts = []
        for off in offsets_minutes:
            dt = base.add(minutes=off)
            seq.db_insert_record(EnergyRecord(date_time=dt, power_w=float(off * 10)))
            dts.append(dt)

        # Newest anchor: makes db_max ≈ now so cutoff = now - age_threshold
        anchor = now.subtract(seconds=1)
        seq.db_insert_record(EnergyRecord(date_time=anchor, power_w=0.0))
        seq.db_save_records()
        return seq, age_td, interval_td, dts

    def test_sparse_misaligned_records_are_snapped(self):
        """Sparse misaligned records must be moved to the nearest boundary.

        Uses a tight window (30 min age, 10 min interval → 3 resampled buckets)
        with 4 misaligned records so existing_count(4) > resampled_count(3) and
        the snapping path is entered deterministically.
        """
        now = to_datetime().in_timezone("UTC")
        # 4 records at :03, :08, :13, :18 — all misaligned for a 10-min interval
        seq, age_td, interval_td, dts = self._make_snapping_seq(
            now, offsets_minutes=[3, 8, 13, 18]
        )
        n_test_records = len([3, 8, 13, 18])
        deleted = seq._db_compact_tier(age_td, interval_td)
        after = seq.db_count_records()

        assert deleted == n_test_records, (
            f"All {n_test_records} in-window records must be deleted (whole-window delete); "
            f"got deleted={deleted}"
        )

        # Compute expected snapped buckets using the ABSOLUTE epochs of the
        # inserted records (same arithmetic _db_compact_tier uses), not
        # offset-relative floor division.  This is correct on any host timezone.
        interval_sec = 10 * 60
        snapped_buckets = {
            (int(dt.timestamp()) // interval_sec) * interval_sec
            for dt in dts
        }
        n_snapped = len(snapped_buckets)

        assert after == 1 + n_snapped, (
            f"Expected 1 anchor + {n_snapped} snapped buckets = {1 + n_snapped} records; "
            f"got {after}"
        )

    def test_sparse_misaligned_timestamps_become_aligned(self):
        """After snapping, in-window timestamps must be on the target interval boundary.

        The anchor record lives outside the compaction window (it is younger than
        age_threshold) and is intentionally misaligned — it must NOT be checked.
        """
        now = to_datetime().in_timezone("UTC")
        interval_minutes = 10
        age_minutes = 30
        seq, age_td, interval_td, dts = self._make_snapping_seq(
            now, offsets_minutes=[3, 8, 13, 18], interval_minutes=interval_minutes,
            age_minutes=age_minutes,
        )
        seq._db_compact_tier(age_td, interval_td)

        # Compute window_end the same way _db_compact_tier does
        # (anchor is db_max; raw_cutoff = anchor - age_threshold ≈ now - 30min)
        anchor_epoch = int(now.subtract(seconds=1).timestamp())
        raw_cutoff_epoch = anchor_epoch - age_minutes * 60
        window_end_epoch = (raw_cutoff_epoch // (interval_minutes * 60)) * (interval_minutes * 60)

        interval_sec = interval_minutes * 60
        for rec in seq.records:
            if rec.date_time is None:
                continue
            epoch = int(rec.date_time.timestamp())
            if epoch >= window_end_epoch:
                continue  # anchor or other post-cutoff record — not compacted
            assert epoch % interval_sec == 0, (
                f"Snapped timestamp {rec.date_time} (epoch={epoch}) is not on a "
                f"{interval_minutes}-min boundary (epoch % {interval_sec} = {epoch % interval_sec})"
            )

    def test_sparse_misaligned_values_preserved_after_snap(self):
        """Snapping must not alter the field values of sparse records."""
        seq = EnergySequence()
        now = to_datetime().in_timezone("UTC")
        # Single misaligned record, old enough for both tiers
        dt = now.subtract(weeks=4).set(minute=7, second=0, microsecond=0)
        seq.db_insert_record(EnergyRecord(date_time=dt, power_w=777.0, price_eur=0.55))
        seq.db_save_records()

        seq.db_compact()

        # Exactly one record must remain and its values must be unchanged
        assert len(seq.records) == 1
        assert seq.records[0].power_w == pytest.approx(777.0)
        assert seq.records[0].price_eur == pytest.approx(0.55)

    # ------------------------------------------------------------------
    # Case 3: two sparse records collide on the same snapped bucket
    # ------------------------------------------------------------------

    def test_sparse_collision_merges_records(self):
        """Two sparse records that snap to the same bucket must be merged.

        Records at :03 and :04 both round to :00 with a 10-min interval.
        With 4 test records and resampled_count=3, the snapping path is entered.
        A newest-anchor record at now-1s pushes db_max ≈ now so the compaction
        cutoff lands at now-30min, which is after all test records.
        """
        now = to_datetime().in_timezone("UTC")
        age_td = to_duration("30 minutes")
        interval_td = to_duration("10 minutes")
        interval_sec = 600
        # Place test records 41+ min ago so they are before cutoff = now - 30min
        # base must be far enough back that all records (+17min max) land before
        # window_end = floor(now - 30min, 600).  Use now - 52min.
        raw_base = now.subtract(minutes=52).set(second=0, microsecond=0)
        base = raw_base.subtract(seconds=int(raw_base.timestamp()) % interval_sec)

        seq = EnergySequence()
        seq.db_insert_record(EnergyRecord(date_time=base.add(minutes=3),
                                          power_w=100.0, price_eur=None))
        seq.db_insert_record(EnergyRecord(date_time=base.add(minutes=4),
                                          power_w=None, price_eur=0.25))
        seq.db_insert_record(EnergyRecord(date_time=base.add(minutes=13), power_w=10.0))
        seq.db_insert_record(EnergyRecord(date_time=base.add(minutes=17), power_w=20.0))
        # Anchor: makes db_max ≈ now → cutoff = now - 30min (after all test records)
        seq.db_insert_record(EnergyRecord(date_time=now.subtract(seconds=1), power_w=0.0))
        seq.db_save_records()

        # existing_count in window = 4, resampled_count = 3 → snapping path
        seq._db_compact_tier(age_td, interval_td)

        snapped_epoch = int(base.timestamp())
        snapped = [
            r for r in seq.records
            if r.date_time is not None and int(r.date_time.timestamp()) == snapped_epoch
        ]
        assert len(snapped) == 1, "The :03 and :04 records must merge into one :00 bucket"
        assert snapped[0].power_w == pytest.approx(100.0), "power_w from :03 must survive"
        assert snapped[0].price_eur == pytest.approx(0.25), "price_eur from :04 must survive"

    def test_sparse_collision_keeps_first_value_for_shared_key(self):
        """When two sparse records floor to the same bucket, the earlier value wins.

        Two records at :03 (power_w=111) and :04 (power_w=222) both floor to :00
        with a 10-min interval (floor division: 3//10=0, 4//10=0).
        existing_count(2) <= resampled_count for the ~22-min window, so the sparse
        snapping path is taken rather than full resampling.  The merged record at
        :00 must carry power_w=111 because the chronologically earlier record wins.
        """
        now = to_datetime().in_timezone("UTC")
        interval_sec = 600
        # Place both records 52 min ago so they are before window_end ≈ now - 30min.
        # Only 2 test records → existing_count(2) <= resampled_count → sparse path.
        raw_base = now.subtract(minutes=52).set(second=0, microsecond=0)
        base = raw_base.subtract(seconds=int(raw_base.timestamp()) % interval_sec)
        seq = EnergySequence()
        seq.db_insert_record(EnergyRecord(date_time=base.add(minutes=3), power_w=111.0))
        seq.db_insert_record(EnergyRecord(date_time=base.add(minutes=4), power_w=222.0))
        # Anchor at now-1s: makes db_max ≈ now so cutoff = now - 30min
        seq.db_insert_record(EnergyRecord(date_time=now.subtract(seconds=1), power_w=0.0))
        seq.db_save_records()
        seq._db_compact_tier(to_duration("30 minutes"), to_duration("10 minutes"))
        snapped_epoch = int(base.timestamp())
        snapped = [
            r for r in seq.records
            if r.date_time is not None and int(r.date_time.timestamp()) == snapped_epoch
        ]
        assert len(snapped) == 1, ":03 and :04 must floor-snap into one :00 record"
        assert snapped[0].power_w == pytest.approx(111.0), "Earlier record's value must win"

    def test_sparse_collision_with_existing_aligned_record(self):
        """A misaligned record that snaps onto an already-aligned record must merge
        into it without raising ValueError.  The aligned record's existing values win.

        :00 (aligned, power_w=50, price_eur=None) and :03 (misaligned,
        power_w=None, price_eur=0.30) both map to :00.  Result: power_w=50
        (aligned wins) and price_eur=0.30 (filled from :03).
        """
        now = to_datetime().in_timezone("UTC")
        interval_sec = 600
        # base must be far enough back that all records (+17min max) land before
        # window_end = floor(now - 30min, 600).  Use now - 52min.
        raw_base = now.subtract(minutes=52).set(second=0, microsecond=0)
        base = raw_base.subtract(seconds=int(raw_base.timestamp()) % interval_sec)

        seq = EnergySequence()
        seq.db_insert_record(EnergyRecord(date_time=base,
                                          power_w=50.0, price_eur=None))
        seq.db_insert_record(EnergyRecord(date_time=base.add(minutes=3),
                                          power_w=None, price_eur=0.30))
        seq.db_insert_record(EnergyRecord(date_time=base.add(minutes=13), power_w=10.0))
        seq.db_insert_record(EnergyRecord(date_time=base.add(minutes=17), power_w=20.0))
        # Anchor: db_max ≈ now → cutoff = now - 30min, after all test records
        seq.db_insert_record(EnergyRecord(date_time=now.subtract(seconds=1), power_w=0.0))
        seq.db_save_records()

        # Must not raise ValueError
        seq._db_compact_tier(to_duration("30 minutes"), to_duration("10 minutes"))

        snapped_epoch = int(base.timestamp())
        snapped = [
            r for r in seq.records
            if r.date_time is not None and int(r.date_time.timestamp()) == snapped_epoch
        ]
        assert len(snapped) == 1, ":00 and :03 must merge into one :00 record"
        rec = snapped[0]
        assert rec.power_w == pytest.approx(50.0),   "Aligned record's power_w must win"
        assert rec.price_eur == pytest.approx(0.30), ":03 record's price_eur must fill in"
        assert rec.date_time is not None
        assert int(rec.date_time.timestamp()) % interval_sec == 0

    def test_sparse_no_duplicate_timestamps_after_collision(self):
        """After collision merging, no duplicate timestamps must remain.

        Three records at :02, :03, :04 all round to :00 with a 10-min interval.
        Together with a record at :13 this gives existing_count(4) >
        resampled_count(3) so the snapping path is entered.
        """
        now = to_datetime().in_timezone("UTC")
        interval_sec = 600
        # base must be far enough back that all records (+17min max) land before
        # window_end = floor(now - 30min, 600).  Use now - 52min.
        raw_base = now.subtract(minutes=52).set(second=0, microsecond=0)
        base = raw_base.subtract(seconds=int(raw_base.timestamp()) % interval_sec)

        seq = EnergySequence()
        for offset_min in [2, 3, 4]:   # all snap to :00
            seq.db_insert_record(EnergyRecord(
                date_time=base.add(minutes=offset_min), power_w=float(offset_min)
            ))
        seq.db_insert_record(EnergyRecord(date_time=base.add(minutes=13), power_w=10.0))
        # Anchor: db_max ≈ now → cutoff = now - 30min, after all test records
        seq.db_insert_record(EnergyRecord(date_time=now.subtract(seconds=1), power_w=0.0))
        seq.db_save_records()

        seq._db_compact_tier(to_duration("30 minutes"), to_duration("10 minutes"))

        timestamps = [
            int(r.date_time.timestamp())
            for r in seq.records
            if r.date_time is not None
        ]
        assert len(timestamps) == len(set(timestamps)), "Duplicate timestamps after collision merge"

    # ------------------------------------------------------------------
    # Existing tier-skip tests (unchanged semantics)
    # ------------------------------------------------------------------

    def test_hourly_data_skips_1h_tier(self):
        """Data already at 1-hour resolution and aligned must not be re-compacted."""
        seq = EnergySequence()
        now = to_datetime().in_timezone("UTC")
        # Use an hour-aligned base so records are on clean boundaries
        base = now.subtract(weeks=3).set(minute=0, second=0, microsecond=0)

        _fill_sequence(seq, base, count=3 * 7 * 24, interval_minutes=60)

        before = seq.db_count_records()
        deleted = seq._db_compact_tier(to_duration("14 days"), to_duration("1 hour"))

        assert deleted == 0
        assert seq.db_count_records() == before

    def test_15min_data_younger_than_2weeks_skips_1h_tier(self):
        """15-min data between 2h and 2weeks old must NOT be compacted by the 1h tier."""
        seq = EnergySequence()
        now = to_datetime().in_timezone("UTC")
        base = now.subtract(weeks=1).set(minute=0, second=0, microsecond=0)
        _fill_sequence(seq, base, count=7 * 24 * 4, interval_minutes=15)

        before = seq.db_count_records()
        deleted = seq._db_compact_tier(to_duration("14 days"), to_duration("1 hour"))

        assert deleted == 0
        assert seq.db_count_records() == before
