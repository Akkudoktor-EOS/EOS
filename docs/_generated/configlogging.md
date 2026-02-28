## Logging Configuration

<!-- pyml disable line-length -->
:::{table} logging
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| console_level | `EOS_LOGGING__CONSOLE_LEVEL` | `Optional[str]` | `rw` | `None` | Logging level when logging to console. |
| file_level | `EOS_LOGGING__FILE_LEVEL` | `Optional[str]` | `rw` | `None` | Logging level when logging to file. |
| file_path | | `Optional[pathlib.Path]` | `ro` | `N/A` | Computed log file path based on data output path. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "logging": {
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
           "console_level": "TRACE",
           "file_level": "TRACE",
           "file_path": "/home/user/.local/share/net.akkudoktor.eos/output/eos.log"
       }
   }
```
<!-- pyml enable line-length -->
