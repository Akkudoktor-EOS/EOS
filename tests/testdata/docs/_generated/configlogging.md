## Logging Configuration

<!-- pyml disable line-length -->
:::{table} logging
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| api_level | `EOS_LOGGING__API_LEVEL` | `str | None` | `rw` | `None` | Logging level for API response. |
| console_level | `EOS_LOGGING__CONSOLE_LEVEL` | `str | None` | `rw` | `None` | Logging level for logging to console. |
| file_level | `EOS_LOGGING__FILE_LEVEL` | `str | None` | `rw` | `None` | Logging level for logging to file. |
| file_path | | `pathlib.Path | None` | `ro` | `N/A` | Computed log file path based on data output path. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "logging": {
           "api_level": "TRACE",
           "console_level": "TRACE",
           "file_level": "TRACE"
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
       "logging": {
           "api_level": "TRACE",
           "console_level": "TRACE",
           "file_level": "TRACE",
           "file_path": "/home/user/.local/share/net.akkudoktor.eos/output/eos.log"
       }
   }
```
<!-- pyml enable line-length -->
