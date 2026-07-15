# `dataabc` — Generic Data Handling

## Overview

The `dataabc` module provides the foundational abstractions for managing time-series data
in EOS. It defines a layered class hierarchy that covers individual data records, ordered
sequences of records, singleton data providers, and multi-provider containers.

All classes in this module are designed for use in predictive modelling workflows and share
three cross-cutting concerns:

- **Configuration access** via `ConfigMixin`, exposing the global EOS configuration as
  `self.config`.
- **Database persistence** via `DatabaseRecordProtocolMixin`, providing optional storage
  in a time-series database alongside in-memory records.
- **Async safety** via a three-level locking scheme described in detail in the
  [Concurrency and Locking](#concurrency-and-locking) section.

## Class Hierarchy

```text
PydanticBaseModel
└── DataABC                      (ConfigMixin, StartMixin)
    ├── DataRecord               (MutableMapping)
    └── DataSequence             (DatabaseRecordProtocolMixin)
        └── DataProvider         (SingletonMixin)
            └── DataImportProvider  (DataImportMixin)

DataContainer                    (SingletonMixin, MutableMapping)
DataImportMixin                  (StartMixin)
```

## Classes

### `DataABC`

Base class for all data-handling objects. Inherits from `ConfigMixin` and `StartMixin`,
making the global EOS configuration available as `self.config` on every derived instance.
Not intended to be instantiated directly.

### `DataRecord`

A single measurement or forecast point at a specific datetime, implemented as a
`MutableMapping` so that field values can be accessed and mutated both as dictionary
entries (`record["field"]`) and as attributes (`record.field`).

#### Key concepts

**Static fields** are declared as Pydantic model fields in the class body. They are always
present and validated on assignment.

**Configured fields** are dynamic: their names are returned by the classmethod
`configured_data_keys()`, which derived classes override to pull key names from the EOS
configuration. These keys are stored in the internal `configured_data` dict but appear
transparent to callers — they show up in `dir()`, `iter()`, and attribute access just like
static fields.

```python
class MeasurementDataRecord(DataRecord):
    @classmethod
    def configured_data_keys(cls) -> Optional[list[str]]:
        return cls.config.measurement.keys
```

#### Important methods

| Method | Description |
|---|---|
| `record_keys()` | All field names, including configured keys. |
| `record_keys_writable()` | Subset of `record_keys()` that can be written. |
| `key_from_description(desc)` | Fuzzy-matches a description string to a field name. |
| `keys_from_descriptions(descs)` | Batch version of `key_from_description`. |

---

### `DataSequence`

An ordered, mutable collection of `DataRecord` instances with time-series behaviour.
Records are always kept sorted in ascending `date_time` order. `DataSequence` is also the
level at which **async safety is enforced** — see
[Concurrency and Locking](#concurrency-and-locking).

Derived classes must redeclare the `records` field with the concrete record type:

```python
class Measurement(DataSequence):
    records: list[MeasurementDataRecord] = Field(default_factory=list)
```

#### Public async interface of `DataSequence`

All methods that mutate sequence state are `async`. Callers in an async context (e.g.
FastAPI endpoint handlers) must `await` them.

| Method | Description |
|---|---|
| `await insert_by_datetime(record)` | Insert or merge a record by its datetime. |
| `await update_value(date, key, value)` | Insert or update a single field value at a datetime. |
| `await key_from_lists(key, dates, values)` | Populate a field from parallel date/value lists. |
| `await key_from_series(key, series)` | Populate a field from a `pd.Series`. |
| `await save()` | Persist all records to the configured storage backend. |
| `await load()` | Load records from the configured storage backend. |
| `await import_from_dict(data)` | Import records from a key-value dictionary. |
| `await import_from_dataframe(df)` | Import records from a `pd.DataFrame`. |
| `await import_from_json(json_str)` | Import records from a JSON string. |
| `await import_from_file(path)` | Import records from a JSON file. |

#### Internal sync interface of `DataSequence`

Each public async method has a private sync counterpart prefixed with `_`. These are
intended **only** for callers that already hold the appropriate locks, or that are running
in a purely sequential context (startup, `_load()` internals, tests). See
[Choosing the right call site](#choosing-the-right-call-site).

| Internal method | Corresponding public method |
|---|---|
| `_insert_by_datetime(record)` | `.insert_by_datetime()` |
| `_update_value(date, ...)` | `.update_value()` |
| `_key_from_lists(key, dates, values)` | `.key_from_lists()` |
| `_key_from_series(key, series)` | `.key_from_series()` |
| `_save()` | `.save()` |
| `_load()` | `.load()` |
| `_import_from_dict(...)` | `.import_from_dict()` |
| `_import_from_dataframe(...)` | `.import_from_dataframe()` |
| `_import_from_json(...)` | `.import_from_json()` |
| `_import_from_file(...)` | `.import_from_file()` |

#### Read-only methods (always sync) of `DataSequence`

These methods do not modify sequence state and require no locking:

| Method | Description |
|---|---|
| `get_by_datetime(dt)` | Exact or nearest record lookup. |
| `get_nearest_by_datetime(dt)` | Nearest record within an optional time window. |
| `key_to_dict(key, ...)` | Extract a `{datetime: value}` dict for a key. |
| `key_to_lists(key, ...)` | Extract parallel date and value lists. |
| `key_to_series(key, ...)` | Extract a `pd.Series` indexed by datetime. |
| `key_to_array(key, ...)` | Extract a resampled `np.ndarray` at a fixed interval. |
| `key_to_value(key, dt)` | Scalar lookup nearest to a datetime. |
| `to_dataframe(...)` | Convert all records to a `pd.DataFrame`. |
| `delete_by_datetime(...)` | Delete records within a datetime range. |
| `key_delete_by_datetime(key, ...)` | Set a field to `None` across a datetime range. |

#### Computed properties of `DataSequence`

| Property | Description |
|---|---|
| `min_datetime` | Earliest datetime in the sequence. |
| `max_datetime` | Latest datetime in the sequence. |
| `record_keys` | All field names for this sequence's record type. |
| `record_keys_writable` | Writable subset of `record_keys`. |

### `DataProvider`

Abstract singleton base class for objects that own and update a `DataSequence`. Each
concrete provider represents one data source (e.g. weather forecast, load measurement,
grid price). `DataProvider` is a specialisation of `DataSequence` and inherits its full
async interface and locking infrastructure — `_record_lock` and `_sequence_lock` — without
adding any new locks of its own.

Derived classes must implement:

| Abstract method | Description |
|---|---|
| `provider_id() -> str` | Unique string identifier for this provider. |
| `enabled() -> bool` | Whether this provider is active per configuration. |
| `_update_data(force_update)` | Custom data fetch/update logic. |

#### `_update_data` contract of `DataProvider`

`_update_data` is always called while the provider's own `_sequence_lock` and
`_record_lock` are both held by `update_data`. Implementations must therefore observe
the following constraints to avoid deadlock:

- Use only the internal sync methods (`_insert_by_datetime`, `_update_value`,
  `_key_from_lists`, `_key_from_series`). Never call their public `async` counterparts,
  which would attempt to re-acquire `_record_lock`.
- Do not call `save()`, `load()`, `_save()`, or `_load()`. Both locks are already held;
  attempting to re-acquire either will deadlock.
- Network or I/O calls are permitted but should be kept short. Offload long-running I/O
  to a thread via `asyncio.to_thread` in the caller before entering the lock scope.

```python
# Correct — both _sequence_lock and _record_lock are already held by the caller
def _update_data(self, force_update=False):
    for dt, value in self._fetch_from_api():
        self._update_value(dt, "temperature_c", value)

# Wrong — would deadlock: _record_lock is not reentrant
def _update_data(self, force_update=False):
    for dt, value in self._fetch_from_api():
        await self.update_value(dt, "temperature_c", value)
```

#### Public async interface of `DataProvider`

<!-- pyml disable line-length -->
| Method | Description |
|---|---|
| `await update_data(force_enable, force_update)` | Call `_update_data` if enabled or forced, holding both `_sequence_lock` and `_record_lock` for the duration. |
<!-- pyml enable line-length -->

### `DataImportMixin`

Mixin that adds bulk import capability to any class that also provides `update_value` and
`record_keys_writable`. Provides `import_from_dict`, `import_from_dataframe`,
`import_from_json`, and `import_from_file`.

The mixin expects values to be lists aligned to a fixed time interval starting from a
`start_datetime`. Two special dictionary keys are handled automatically:

- `start_datetime` — overrides the start of the import window.
- `interval` — overrides the fixed time step between values.

### `DataImportProvider`

Convenience base class combining `DataImportMixin` and `DataProvider`. Derive from this
when a provider's data arrives via JSON, file, or dict import rather than a live API.

### `DataContainer`

A singleton `MutableMapping` that aggregates multiple `DataProvider` instances and
presents their combined data through a single interface. Providers are tried in order;
the first one that contains the requested key wins.

`DataContainer` carries its own pair of locks (`_record_lock` and `_container_lock`)
that are independent of the locks on each provider. See
[Concurrency and Locking](#concurrency-and-locking) for the full acquisition matrix.

#### Public async interface of `DataContainer`

| Method | Description |
|---|---|
| `await __setitem__(key, series)` | Write a `pd.Series` into the appropriate provider. |
| `await __delitem__(key)` | Clear a field across all providers. |
| `await update_data(force_enable, force_update)` | Update all providers. |
| `await save()` | Save all providers to persistent storage. |
| `await load()` | Load all providers from persistent storage. |
| `await db_vacuum()` | Remove old records from all provider databases. |
| `await db_compact()` | Apply tiered compaction to all provider databases. |
| `await keys_to_dataframe(keys, ...)` | Consistent cross-provider snapshot as a `pd.DataFrame`. |

#### Read-only methods (always sync) of `DataContainer`

| Method | Description |
|---|---|
| `__getitem__(key)` | Return a `pd.Series` for a key from the first matching provider. |
| `__iter__()` | Iterate over all unique keys across enabled providers. |
| `__len__()` | Total number of unique keys. |
| `key_to_series(key, ...)` | Extract a series from the first matching provider. |
| `key_to_array(key, ...)` | Extract a resampled array from the first matching provider. |
| `provider_by_id(provider_id)` | Look up a provider by its string identifier. |
| `enabled_providers` | List of currently active providers. |
| `record_keys` | Union of all record keys across enabled providers. |
| `record_keys_writable` | Union of all writable record keys across enabled providers. |

## Concurrency and Locking

### Problem

EOS runs under FastAPI with an async event loop. Multiple HTTP requests are handled
concurrently as coroutines on a single thread. Because coroutines yield at `await` points,
two coroutines can interleave between a read and a subsequent write, producing a
**check-then-act race condition**:

```text
Coroutine A: db_get_record(ts)  → None        # record does not exist yet
Coroutine B: db_get_record(ts)  → None        # same — A has not inserted yet
Coroutine A: db_insert_record(new_rec)        # inserts successfully
Coroutine B: db_insert_record(new_rec)        # raises: duplicate timestamp
```

This is the root cause of the `ValueError: Duplicate timestamp` errors seen in production.

### Solution: three-level locking

The solution uses three distinct `asyncio.Lock` objects, each protecting a different scope
of state. Each name answers the question "what is being protected":

<!-- pyml disable line-length -->
| Lock | Defined on | Attribute | Protects |
|---|---|---|---|
| Record lock | `DataSequence` | `_record_lock` | A single check-then-act on one record. |
| Sequence lock | `DataSequence` | `_sequence_lock` | The full sequence state during operations that touch many records at once. |
| Container lock | `DataContainer` | `_container_lock` | Cross-provider consistency during container-level bulk operations. |
<!-- pyml enable line-length -->

`DataProvider` inherits `_record_lock` and `_sequence_lock` from `DataSequence` without
adding any new locks of its own. It is a specialisation of `DataSequence`, not a new
locking scope.

The fixed acquisition order across all levels is:

```text
_container_lock → _record_lock → provider _sequence_lock → provider _record_lock
```

No code path may acquire a finer-grained lock and then wait for a coarser one at the
same level. This invariant prevents deadlock.

### Lock creation

All locks are created lazily on first access via `_get_or_create_lock()`, which bypasses
Pydantic's `__setattr__` using `object.__setattr__` directly. This is necessary because
Pydantic v2 rejects attributes that are not declared model fields, and `asyncio.Lock`
cannot be safely created outside a running event loop, making `__init__`-time creation
unsafe. `cached_property` is not used for the same reason — Pydantic v2's tight
`__dict__` control makes it unreliable on model instances.

```python
def _get_or_create_lock(self, attr: str) -> asyncio.Lock:
    try:
        return object.__getattribute__(self, attr)
    except AttributeError:
        lock = asyncio.Lock()
        object.__setattr__(self, attr, lock)
        return lock

@property
def _record_lock(self) -> asyncio.Lock:
    return self._get_or_create_lock("_record_lock_instance")

@property
def _sequence_lock(self) -> asyncio.Lock:           # DataSequence / DataProvider
    return self._get_or_create_lock("_sequence_lock_instance")

@property
def _container_lock(self) -> asyncio.Lock:          # DataContainer only
    return self._get_or_create_lock("_container_lock_instance")
```

### Lock acquisition rules

**Individual record writes** acquire only `_record_lock`, held for the minimum time
needed to make the check-then-act atomic:

```python
async def update_value(self, date, *args, **kwargs):
    async with self._record_lock:
        self._update_value(date, *args, **kwargs)
```

**Sequence-level bulk operations** (`save`, `load`, `import_from_*`) acquire
`_sequence_lock` first, then `_record_lock`. This blocks both concurrent bulk operations
and concurrent individual writes for the full duration:

```python
async def load(self):
    async with self._sequence_lock:
        async with self._record_lock:
            self._load()
```

**`update_data`** on a provider acquires both `_sequence_lock` and `_record_lock`,
protecting the provider's full sequence state against concurrent saves, loads, imports,
or individual record writes for the entire duration of `_update_data`:

```python
async def update_data(self, force_enable=False, force_update=False):
    if not force_enable and not self.enabled():
        return
    async with self._sequence_lock:
        async with self._record_lock:
            self._update_data(force_update=force_update)
```

**Container-level bulk operations** acquire `_container_lock` and `_record_lock` on the
container, then delegate to each provider (which independently acquires its own
`_sequence_lock` and `_record_lock`):

```python
async def save(self):              # DataContainer
    async with self._container_lock:
        async with self._record_lock:
            for provider in self.providers:
                await provider.save()  # provider acquires _sequence_lock + _record_lock
```

**Cross-provider consistent reads** acquire only the container's `_record_lock`, which
prevents concurrent writes from producing an inconsistent snapshot without blocking
other read operations:

```python
async def keys_to_dataframe(self, keys, ...):
    async with self._record_lock:
        ...  # read from multiple providers atomically
```

### Lock acquisition matrix

#### `DataSequence` / `DataProvider` lock

| Operation | `_sequence_lock` | `_record_lock` |
|---|---|---|
| `insert_by_datetime` | | ✓ |
| `update_value` | | ✓ |
| `key_from_lists` | | ✓ |
| `key_from_series` | | ✓ |
| `update_data` | ✓ | ✓ |
| `save` | ✓ | ✓ |
| `load` | ✓ | ✓ |
| `import_from_dict` | ✓ | ✓ |
| `import_from_dataframe` | ✓ | ✓ |
| `import_from_json` | ✓ | ✓ |
| `import_from_file` | ✓ | ✓ |
| Read-only methods | | |

#### `DataContainer` lock

<!-- pyml disable line-length -->
| Operation | `_container_lock` | `_record_lock` | Provider `_sequence_lock` | Provider `_record_lock` |
|---|---|---|---|---|
| `update_data` | ✓ | ✓ | ✓ | ✓ |
| `__setitem__` | | ✓ | | |
| `__delitem__` | | ✓ | | |
| `keys_to_dataframe` | | ✓ | | |
| `save` | ✓ | ✓ | ✓ | ✓ |
| `load` | ✓ | ✓ | ✓ | ✓ |
| `db_vacuum` | ✓ | ✓ | | |
| `db_compact` | ✓ | ✓ | | |
| Read-only methods | | | | |
<!-- pyml enable line-length -->

### Why `asyncio.Lock` and not `threading.Lock`

FastAPI's default async workers run all coroutines on a **single OS thread**. Coroutines
interleave at `await` points, not at thread boundaries. `threading.Lock` would not protect
against this interleaving and would risk deadlock if the same coroutine attempts to
re-acquire it. `asyncio.Lock` yields control correctly at `await` points while keeping
other coroutines blocked.

### Why `asyncio.Lock` is not reentrant

Python's `asyncio.Lock` is intentionally non-reentrant. If a coroutine that already holds
`_record_lock` calls a public async method (which also tries to acquire `_record_lock`),
it will deadlock. This is why `_update_data()` implementations and all other internal
callers must use the private `_method()` variants rather than `await self.method()`.

### Choosing the right call site

<!-- pyml disable line-length -->
| Caller context | Correct call |
|---|---|
| FastAPI endpoint or any `async def` | `await sequence.insert_by_datetime(record)` |
| `_update_data()` implementation | `self._insert_by_datetime(record)` |
| Startup, `_load()` internals, tests | `sequence._insert_by_datetime(record)` |
| `DataContainer` delegating to a provider | `await provider.save()` — container holds its own locks; provider independently holds its own |
<!-- pyml enable line-length -->

## Usage Examples

### Reading data (sync, no locking required)

```python
measurement = get_measurement()

# Scalar lookup
value = measurement.key_to_value("grid_import_w", target_datetime=now)

# Resampled array for the next 24 hours
array = measurement.key_to_array(
    key="grid_import_w",
    start_datetime=now,
    end_datetime=now.add(hours=24),
    interval=to_duration("1 hour"),
)

# Pandas Series
series = measurement.key_to_series("grid_import_w", start_datetime=now)
```

### Writing a single value from a FastAPI endpoint

```python
@router.put("/measurement/value")
async def put_measurement_value(datetime: str, key: str, value: float):
    dt = to_datetime(datetime)
    await get_measurement().update_value(dt, key, value)
```

### Bulk import from a FastAPI endpoint

```python
@router.put("/measurement/import")
async def put_measurement_import(data: dict):
    await get_measurement().import_from_dict(data)
```

### Writing from a sync context (startup / tests)

```python
def load_initial_data(measurement: Measurement, records: list[MeasurementDataRecord]):
    # Sequential — no concurrency, use internal sync methods directly
    for record in records:
        measurement._insert_by_datetime(record)
```

### Implementing a custom `DataProvider`

```python
class MyProvider(DataProvider):
    records: list[MyRecord] = Field(default_factory=list)

    def provider_id(self) -> str:
        return "MyProvider"

    def enabled(self) -> bool:
        return self.config.my_provider.enabled

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        # Both _sequence_lock and _record_lock are already held by the caller.
        # Use internal sync methods only — never await public async counterparts.
        for dt, reading in fetch_from_hardware():
            self._update_value(dt, "sensor_w", reading)

    def db_namespace(self) -> str:
        return "MyProvider"

    def db_keep_datetime(self) -> Optional[DateTime]:
        return to_datetime().subtract(hours=48)
```

---

## Design Notes

### Singleton providers and containers

`DataProvider` and `DataContainer` both inherit from `SingletonMixin`. A single instance
is shared across all coroutines handling concurrent requests. This is precisely why
locking is necessary — every concurrent request operates on the same in-memory object.

### Lock naming rationale

The three lock names were chosen to reflect *what is being protected*, not *how coarse
the operation is*:

- `_record_lock` — makes it immediately clear that a single record's check-then-act is
  being made atomic. The same name is used at both `DataSequence` and `DataContainer`
  level because it plays the same role in both: serialising individual write operations.
- `_sequence_lock` — defined on `DataSequence` and inherited unchanged by `DataProvider`.
  The name reflects that the entire sequence state is held exclusively, not just one
  record. `DataProvider` is a specialisation of `DataSequence`, so the name remains
  accurate at both levels without needing a separate `_provider_lock`.
- `_container_lock` — exclusive to `DataContainer`, reflecting that cross-provider
  container-level state is held exclusively during bulk operations.

This naming also makes lock misuse visible in code review: a method that acquires
`_sequence_lock` without also acquiring `_record_lock` (or acquires them in the wrong
order) stands out immediately against the documented fixed acquisition order.

### Database vs in-memory storage

`DataSequence` uses `DatabaseRecordProtocolMixin` to optionally back its records with a
persistent time-series database. When a database is configured, `save()` and `load()`
delegate to it; otherwise they fall back to a JSON file. The in-memory `records` list acts
as a write-through cache. The locking scheme covers both paths — the database operations
are included inside the lock scope so that in-memory state and database state remain
consistent.

### Pydantic v2 compatibility

The `asyncio.Lock` instances are stored on the instance using `object.__setattr__`,
bypassing Pydantic's field validation entirely. This is intentional: `asyncio.Lock` is not
serialisable and must not appear in `model_dump()` or `model_dump_json()` output. The
locks are therefore invisible to Pydantic serialisation and are reconstructed fresh on
each process start, which is correct behaviour for a lock.
