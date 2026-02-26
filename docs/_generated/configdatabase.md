## Configuration model for database settings

Attributes:
    provider: Optional provider identifier (e.g. "LMDB").
    max_records_in_memory: Maximum records kept in memory before auto-save.
    auto_save: Whether to auto-save when threshold exceeded.
    batch_size: Batch size for batch operations.

<!-- pyml disable line-length -->
:::{table} database
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| autosave_interval_sec | `EOS_DATABASE__AUTOSAVE_INTERVAL_SEC` | `int | None` | `rw` | `10` | Automatic saving interval [seconds].
Set to None to disable automatic saving. |
| batch_size | `EOS_DATABASE__BATCH_SIZE` | `int` | `rw` | `100` | Number of records to process in batch operations. |
| compaction_interval_sec | `EOS_DATABASE__COMPACTION_INTERVAL_SEC` | `int | None` | `rw` | `604800` | Interval in between automatic tiered compaction runs [seconds].
Compaction downsamples old records to reduce storage while retaining coverage. Set to None to disable automatic compaction. |
| compression_level | `EOS_DATABASE__COMPRESSION_LEVEL` | `int` | `rw` | `9` | Compression level for database record data. |
| initial_load_window_h | `EOS_DATABASE__INITIAL_LOAD_WINDOW_H` | `int | None` | `rw` | `None` | Specifies the default duration of the initial load window when loading records from the database, in hours. If set to None, the full available range is loaded. The window is centered around the current time by default, unless a different center time is specified. Different database namespaces may define their own default windows. |
| keep_duration_h | `EOS_DATABASE__KEEP_DURATION_H` | `int | None` | `rw` | `None` | Default maximum duration records shall be kept in database [hours, none].
None indicates forever. Database namespaces may have diverging definitions. |
| provider | `EOS_DATABASE__PROVIDER` | `str | None` | `rw` | `None` | Database provider id of provider to be used. |
| providers | | `List[str]` | `ro` | `N/A` | Return available database provider ids. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "database": {
           "provider": "LMDB",
           "compression_level": 0,
           "initial_load_window_h": 48,
           "keep_duration_h": 48,
           "autosave_interval_sec": 5,
           "compaction_interval_sec": 604800,
           "batch_size": 100
       }
   }
```
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "database": {
           "provider": "LMDB",
           "compression_level": 0,
           "initial_load_window_h": 48,
           "keep_duration_h": 48,
           "autosave_interval_sec": 5,
           "compaction_interval_sec": 604800,
           "batch_size": 100,
           "providers": [
               "LMDB",
               "SQLite"
           ]
       }
   }
```
<!-- pyml enable line-length -->
