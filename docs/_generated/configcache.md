## Cache Configuration

<!-- pyml disable line-length -->
:::{table} cache
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| cleanup_interval | `EOS_CACHE__CLEANUP_INTERVAL` | `float` | `rw` | `300.0` | Intervall in seconds for EOS file cache cleanup. |
| subpath | `EOS_CACHE__SUBPATH` | `pathlib.Path | None` | `rw` | `cache` | Sub-path for the EOS cache data directory. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "cache": {
           "subpath": "cache",
           "cleanup_interval": 300.0
       }
   }
```
<!-- pyml enable line-length -->
