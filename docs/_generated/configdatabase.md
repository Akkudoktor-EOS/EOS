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
| auto_save | `EOS_DATABASE__AUTO_SAVE` | `bool` | `rw` | `True` | Enable automatic saving when threshold exceeded. |
| batch_size | `EOS_DATABASE__BATCH_SIZE` | `int` | `rw` | `100` | Number of records to process in batch operations. |
| compression_level | `EOS_DATABASE__COMPRESSION_LEVEL` | `int` | `rw` | `9` | Compression level for database record data. |
| max_records_in_memory | `EOS_DATABASE__MAX_RECORDS_IN_MEMORY` | `int` | `rw` | `1000` | Maximum records to keep in memory before auto-save. |
| provider | `EOS_DATABASE__PROVIDER` | `Optional[str]` | `rw` | `None` | Database provider id of provider to be used. |
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
           "max_records_in_memory": 1000,
           "auto_save": true,
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
           "max_records_in_memory": 1000,
           "auto_save": true,
           "batch_size": 100,
           "providers": [
               "LMDB",
               "SQLite"
           ]
       }
   }
```
<!-- pyml enable line-length -->
