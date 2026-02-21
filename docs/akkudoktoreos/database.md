% SPDX-License-Identifier: Apache-2.0
(database-page)=

# Database

## Overview

The EOS database system provides a flexible, pluggable persistence layer for time-series data
records with automatic lazy loading, dirty tracking, and multi-backend support. The architecture
separates the abstract database interface from concrete storage implementations, allowing seamless
switching between LMDB and SQLite backends.

## Architecture

### Three-Layer Design

**Abstract Interface Layer** (`DatabaseABC`)

- Defines the contract for all database operations
- Provides compression/decompression utilities
- Backend-agnostic API

**Backend Implementation Layer** (`DatabaseBackendABC`)

- Concrete implementations: `LMDBDatabase`, `SQLiteDatabase`
- Singleton pattern ensures single instance per backend
- Thread-safe operations via internal locking

**Record Protocol Layer** (`DatabaseRecordProtocolMixin`)

- Manages in-memory record lifecycle
- Implements lazy loading strategies
- Handles dirty tracking and autosave

## Configuration

### Database Settings (`DatabaseCommonSettings`)

```python
provider: Optional[str] = None        # "LMDB" or "SQLite"
compression_level: int = 9            # 0-9, gzip compression
initial_load_window_h: Optional[int] = None  # Hours, None = full load
keep_duration_h: Optional[int] = None        # Retention period
autosave_interval_sec: Optional[int] = None  # Auto-flush interval
compaction_interval_sec: Optional[int] = 604800  # Compaction interval
batch_size: int = 100                 # Batch operation size
```

### User Configuration Guide

This section explains what each setting does in practical terms and gives
concrete recommendations for common deployment scenarios.

#### `provider` — choosing a backend

Set `provider` to `"LMDB"` or `"SQLite"`. Leave it `None` only during
development or unit testing — with `None` set, nothing is persisted to disk and
all data is lost on restart.

**Use LMDB** for a long-running home server that records data continuously. It
is significantly faster for high-frequency writes and range reads because it
uses memory-mapped files. The trade-off is that it pre-allocates a large file
on disk (default 10 GB) even when mostly empty.

**Use SQLite** when disk space is constrained, for portable single-file
deployments, or when you want to inspect or manipulate the database with
standard SQL tools. SQLite is slightly slower for bulk writes but perfectly
adequate for home energy data volumes.

**Do not** switch backends while data exists in the old backend — records are
not migrated automatically. If you need to switch, vacuum the old database
first, export your data, then reconfigure.

#### `compression_level` — storage size vs. CPU

Values range from `0` (no compression) to `9` (maximum compression). The default of `9` is
appropriate for most deployments: home energy time-series data compresses very well (often
60–80 % reduction) and the CPU overhead is negligible on modern hardware.

**Set to `0`** only if you are running on very constrained hardware (e.g. a single-core ARM
board at full load) and storage space is not a concern.

**Do not** change this setting after data has been written — the database stores each record
with the compression level active at write time and auto-detects the format on read, so mixed
levels are fine technically, but you will not reclaim space from already-written records until
they are rewritten by compaction.

#### `initial_load_window_h` — startup memory usage

Controls how much history is loaded into memory when the application first accesses a namespace.

**Set a window** (e.g. `48`) on systems with limited RAM or large databases. Only the most
recent 48 hours are loaded immediately; older data is fetched on demand if a query reaches
outside that window.

**Leave as `None`** (the default) on well-resourced systems or when you need guaranteed
access to all history from the first query. Full load is simpler and avoids the small latency
spike of incremental loads.

**Do not** set this to a very small value (e.g. `1`) if your forecasting or reporting queries
routinely look back further — every out-of-window query triggers a database read, and many
small reads are slower than one full load.

#### `keep_duration_h` — data retention

Sets the age limit (in hours) for the vacuum operation. Records older than
`max_timestamp - keep_duration_h` are permanently deleted when vacuum runs.

**Set this** to match your actual analysis needs. If your forecast models only look back 7 days,
keeping 14 days (`336`) gives a comfortable safety margin without accumulating indefinitely.

**Leave as `None`** only if you have a strong archival requirement and understand that the
database will grow without bound. Even with compaction reducing resolution, old data is not
deleted unless vacuum runs with a retention limit.

**Do not** set `keep_duration_h` shorter than the oldest data your forecast or reporting
queries ever request — vacuum is permanent and irreversible.

#### `autosave_interval_sec` — write durability

Controls how often dirty (modified) records are flushed to disk automatically, in seconds.

**Set to a low value** (e.g. `10`–`30`) on a system that could lose power unexpectedly,
such as a Raspberry Pi without a UPS. A power cut between autosaves loses that window of data.

**Set to a higher value** (e.g. `300`) on stable systems to reduce write amplification. Each
autosave is a full flush of all dirty records, so frequent saves on large dirty sets are
more expensive.

**Leave as `None`** only if you call `db_save_records()` manually at appropriate points in
your application code. With `None`, data written since the last manual save is lost on crash.

#### `compaction_interval_sec` — automatic tiered downsampling

Controls how often the compaction maintenance job runs, in seconds. The default is
604 800 (one week). Set to `None` to disable automatic compaction entirely.

Compaction applies a tiered downsampling policy to old records:

- Records older than **2 hours** are downsampled to **15-minute** resolution
- Records older than **14 days** are downsampled to **1-hour** resolution

This reduces storage and speeds up range queries on historical data while preserving full
resolution for recent data where it matters most. Each tier is processed incrementally —
only the window since the last compaction run is examined, so weekly runs are fast regardless
of total history length.

**Leave at the default weekly interval** for most deployments. Compaction is idempotent and
cheap when run frequently on small new windows.

**Set to a shorter interval** (e.g. `86400`, daily) if your device records at very high
frequency (sub-minute) and disk space is a concern.

**Set to `None`** only if you have a custom retention policy and manage downsampling manually,
or if you store data that must not be averaged (e.g. raw event logs where mean resampling
would be meaningless).

**Do not** set the interval shorter than `autosave_interval_sec` — compaction reads from the
backend and a record that has not been saved yet will not be visible to it.

**Interaction with vacuum:** compaction and vacuum are complementary. Compaction reduces
resolution of old data; vacuum deletes it entirely past `keep_duration_h`. The recommended
pipeline is: compaction runs first (weekly), then vacuum runs immediately after. This means
vacuum always operates on already-downsampled data, which is faster and produces cleaner
storage boundaries.

### Recommended Configurations by Scenario

#### Home server, typical (Raspberry Pi 4, SSD)

```python
provider = "LMDB"
compression_level = 9
initial_load_window_h = 48
keep_duration_h = 720          # 30 days
autosave_interval_sec = 30
compaction_interval_sec = 604800  # weekly
```

#### Home server, low storage (Raspberry Pi Zero, SD card)

```python
provider = "SQLite"
compression_level = 9
initial_load_window_h = 24
keep_duration_h = 168          # 7 days
autosave_interval_sec = 60
compaction_interval_sec = 86400   # daily — reclaim space faster
```

#### Development / testing

```python
provider = "SQLite"            # or None for fully in-memory
compression_level = 0          # faster without compression overhead
initial_load_window_h = None   # always load everything
keep_duration_h = None         # never vacuum automatically
autosave_interval_sec = None   # manual saves only
compaction_interval_sec = None # disable compaction
```

#### High-frequency recording (sub-minute intervals)

```python
provider = "LMDB"
compression_level = 9
initial_load_window_h = 24
keep_duration_h = 336          # 14 days
autosave_interval_sec = 10
compaction_interval_sec = 86400   # daily — essential at high frequency
```

## Storage Backends

### LMDB Backend

**Characteristics:**

- Memory-mapped file database
- Native namespace support via DBIs (Database Instances)
- High-performance reads with MVCC
- Configurable map size (default: 10 GB)

**Configuration:**

```python
map_size: int = 10 * 1024 * 1024 * 1024  # 10 GB
writemap=True, map_async=True             # Performance optimizations
max_dbs=128                                # Maximum namespaces
```

**File Structure:**

```text
data_folder_path/
└── db/
    └── lmdbdatabase/
        ├── data.mdb
        └── lock.mdb
```

### SQLite Backend

**Characteristics:**

- Single-file relational database
- Namespace emulation via `namespace` column
- ACID transactions with autocommit mode
- Cross-platform compatibility

**Schema:**

```sql
CREATE TABLE records (
    namespace TEXT NOT NULL DEFAULT '',
    key BLOB NOT NULL,
    value BLOB NOT NULL,
    PRIMARY KEY (namespace, key)
);

CREATE TABLE metadata (
    namespace TEXT PRIMARY KEY,
    value BLOB
);
```

**File Structure:**

```text
data_folder_path/
└── db/
    └── sqlitedatabase/
        └── data.db
```

## Timestamp System

### DatabaseTimestamp

All records are indexed by UTC timestamps in sortable ISO 8601 format:

```python
DatabaseTimestamp.from_datetime(dt: DateTime) -> "20241027T123456[Z]"
```

**Properties:**
- Always stored in UTC (timezone-aware required)
- Lexicographically sortable
- Bijective conversion to/from `pendulum.DateTime`
- Second-level precision

### Unbounded Sentinels

```python
UNBOUND_START  # Smaller than any timestamp
UNBOUND_END    # Greater than any timestamp
```

Used for open-ended range queries without special-casing `None`.

## Lazy Loading Strategy

### Three-Phase Loading

The system uses a progressive loading model to minimize memory footprint:

#### **Phase 0: NONE**

- No records loaded
- First query triggers either:
  - Initial window load (if `initial_load_window_h` configured)
  - Full database load (if `initial_load_window_h = None`)
  - Targeted range load (if explicit range requested)

#### **Phase 1: INITIAL**

- Partial time window loaded
- `_db_loaded_range` tracks coverage: `[start_timestamp, end_timestamp)`
- Out-of-window queries trigger incremental expansion:
  - Left expansion: load records before current window
  - Right expansion: load records after current window
- Unbounded queries escalate to FULL

#### **Phase 2: FULL**

- All database records in memory
- No further database access needed
- `_db_loaded_range` spans entire dataset

### Boundary Extension

When loading a range `[start, end)`, the system automatically extends boundaries to include:
- **First record before** `start` (for interpolation/context)
- **First record at or after** `end` (for closing boundary)

This prevents additional database lookups during nearest-neighbor searches.

## Namespace Support

Namespaces provide logical isolation within a single database instance:

```python
# LMDB: uses native DBIs
db.save_records(records, namespace="measurement")

# SQLite: uses namespace column
SELECT * FROM records WHERE namespace='measurement'
```

**Default Namespace:**
- Can be set during `open(namespace="default")`
- Operations with `namespace=None` use the default
- Each record class typically defines its own namespace via `db_namespace()`

## Record Lifecycle

### Insertion

```python
db_insert_record(record, mark_dirty=True)
```

1. Normalize `record.date_time` to UTC `DatabaseTimestamp`
2. Ensure timestamp range is loaded (lazy load if needed)
3. Check for duplicates (raises `ValueError`)
4. Insert into sorted position in memory
5. Update index: `_db_record_index[timestamp] = record`
6. Mark dirty if `mark_dirty=True`

### Retrieval

```python
db_get_record(target_timestamp, time_window=None)
```

**Search Strategies:**

| `time_window` | Behavior |
|---|---|
| `None` | Exact match only |
| `UNBOUND_WINDOW` | Nearest record (unlimited search) |
| `Duration` | Nearest within symmetric window |

**Memory-First:** Checks in-memory index before querying database.

### Deletion

```python
db_delete_records(start_timestamp, end_timestamp)
```

1. Ensure range is fully loaded
2. Remove from memory: `records`, `_db_sorted_timestamps`, `_db_record_index`
3. Add to `_db_deleted_timestamps` (tombstone)
4. Discard from dirty sets (cancel pending writes)
5. Physical deletion deferred until `db_save_records()`

## Dirty Tracking

The system maintains three dirty sets to optimize writes:

```python
_db_dirty_timestamps: set[DatabaseTimestamp]    # Modified records
_db_new_timestamps: set[DatabaseTimestamp]      # Newly inserted
_db_deleted_timestamps: set[DatabaseTimestamp]  # Pending deletes
```

**Write Strategy:**

1. **Saves first:** Insert/update all dirty records
2. **Deletes last:** Remove tombstoned records
3. **Clear tracking sets:** Reset dirty state

**Autosave:** Triggered periodically if `autosave_interval_sec` configured.

## Compression

Optional gzip compression reduces storage footprint:

```python
# Serialize
data = pickle.dumps(record.model_dump())
if compression_level > 0:
    data = gzip.compress(data, compresslevel=compression_level)

# Deserialize (auto-detect)
if data[:2] == b'\x1f\x8b':  # gzip magic bytes
    data = gzip.decompress(data)
record_data = pickle.loads(data)
```

**Compression is transparent:** Application code never handles compressed data directly.

## Metadata

Each namespace can store arbitrary metadata (version, creation time, provider):

```python
_db_metadata = {
    "version": 1,
    "created": "2024-01-01T00:00:00Z",
    "provider_id": "LMDB",
    "compression": True,
    "backend": "LMDBDatabase"
}
```

Stored separately from records using reserved key `__metadata__`.

## Compaction

Compaction reduces storage by downsampling old records to a lower time resolution. Unlike
vacuum — which deletes records outright — compaction preserves the full time span of the
data while replacing many fine-grained records with fewer coarse-grained averages.

### Tiered Downsampling Policy

The default policy has two tiers, applied coarsest-first:

| Age threshold | Target resolution | Effect |
|---|---|---|
| Older than 14 days | 1 hour | 15-min records → 1 per hour (75 % reduction) |
| Older than 2 hours | 15 minutes | 1-min records → 1 per 15 min (93 % reduction) |

Records within the most recent 2 hours are never touched.

### How Compaction Works

Each tier is processed incrementally using a stored cutoff timestamp per tier. On each run,
only the window `[last_cutoff, new_cutoff)` is examined — records already compacted in a
previous run are never re-processed. This makes weekly runs fast even on years of history.

For each writable numeric field, records in the window are mean-resampled at the target
interval using time interpolation. The original records are deleted and the downsampled
records are written back. A **sparse-data guard** skips any window where the existing record
count is already at or below the resampled bucket count, preventing compaction from
accidentally *increasing* record count for data that is already coarse or irregular.

### Customising the Policy per Namespace

Individual data providers can override `db_compact_tiers()` to use a different policy:

```python
class PriceDataProvider(DataProvider):
    def db_compact_tiers(self):
        # Price data is already at 15-min resolution from the source.
        # Skip the first tier; only compact to hourly after 2 weeks.
        return [(to_duration("14 days"), to_duration("1 hour"))]
```

Return an empty list to disable compaction for a specific namespace entirely:

```python
class EventLogProvider(DataProvider):
    def db_compact_tiers(self):
        return []  # Raw events must not be averaged
```

### Manual Invocation

```python
# Compact all providers in the container
data_container.db_compact()

# Compact a single provider
provider.db_compact()

# Use a one-off policy without changing the instance default
provider.db_compact(compact_tiers=[
    (to_duration("7 days"), to_duration("1 hour"))
])
```

### Interaction with Vacuum

Compaction and vacuum are complementary and should always run in this order:

```text
compact → vacuum
```

Compact first so that vacuum operates on already-downsampled records. This produces cleaner
retention boundaries and ensures the vacuum cutoff falls on hour-aligned timestamps rather
than arbitrary sub-minute ones. Running them in reverse order (vacuum then compact) wastes
work: vacuum may delete records that compaction would have downsampled and kept.

The `RetentionManager` registers both jobs and ensures compaction always runs before vacuum
within the same maintenance window.

## Vacuum Operation

Remove old records to reclaim space:

```python
db_vacuum(keep_hours=48)        # Keep last 48 hours
db_vacuum(keep_timestamp=cutoff) # Keep from cutoff onward
```

**Strategy:**
- Computes cutoff relative to `max_timestamp - keep_hours`
- Deletes all records before cutoff
- Immediately persists changes via `db_save_records()`

## Thread Safety

- **LMDB:** Internal lock protects write transactions; reads are lock-free via MVCC
- **SQLite:** Lock guards all operations (autocommit mode eliminates transaction deadlocks)
- **Record Protocol:** No internal locking (assumes single-threaded access per instance)

## Performance Characteristics

| Operation | LMDB | SQLite |
|---|---|---|
| Sequential read | Excellent (mmap) | Good (indexed) |
| Random read | Excellent (mmap) | Good (B-tree) |
| Bulk write | Excellent (single txn) | Good (batch insert) |
| Range query | Excellent (cursor) | Good (indexed scan) |
| Disk usage | Moderate (pre-allocated) | Compact (auto-grow) |
| Concurrency | High (MVCC readers) | Low (write serialization) |

**Recommendation:** Use LMDB for high-frequency time-series workloads;
SQLite for portability and simpler deployment.

## Example Usage

```python
# Configuration
config.database.provider = "LMDB"
config.database.compression_level = 9
config.database.initial_load_window_h = 24  # Load last 24h initially
config.database.keep_duration_h = 720       # Retain 30 days
config.database.compaction_interval_sec = 604800  # Compact weekly

# Access (automatic singleton initialization)
class MeasurementData(DatabaseRecordProtocolMixin):
    records: list[MeasurementRecord] = []

    def db_namespace(self) -> str:
        return "measurement"

# Operations
measurement = MeasurementData()

# Lazy load on first access
record = measurement.db_get_record(
    DatabaseTimestamp.from_datetime(now),
    time_window=Duration(hours=1)
)

# Insert new record
measurement.db_insert_record(new_record)

# Automatic save (if autosave configured) or manual
measurement.db_save_records()

# Maintenance pipeline (normally handled by RetentionManager)
measurement.db_compact()    # downsample old records first
measurement.db_vacuum(keep_hours=720)  # then delete beyond retention
```
