## Server Configuration

<!-- pyml disable line-length -->
:::{table} server
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| eosdash_host | `EOS_SERVER__EOSDASH_HOST` | `Optional[str]` | `rw` | `None` | EOSdash server IP address. Defaults to EOS server IP address. |
| eosdash_port | `EOS_SERVER__EOSDASH_PORT` | `Optional[int]` | `rw` | `None` | EOSdash server IP port number. Defaults to EOS server IP port number + 1. |
| host | `EOS_SERVER__HOST` | `Optional[str]` | `rw` | `127.0.0.1` | EOS server IP address. Defaults to 127.0.0.1. |
| port | `EOS_SERVER__PORT` | `Optional[int]` | `rw` | `8503` | EOS server IP port number. Defaults to 8503. |
| startup_eosdash | `EOS_SERVER__STARTUP_EOSDASH` | `Optional[bool]` | `rw` | `True` | EOS server to start EOSdash server. Defaults to True. |
| verbose | `EOS_SERVER__VERBOSE` | `Optional[bool]` | `rw` | `False` | Enable debug output |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "server": {
           "host": "127.0.0.1",
           "port": 8503,
           "verbose": false,
           "startup_eosdash": true,
           "eosdash_host": "127.0.0.1",
           "eosdash_port": 8504
       }
   }
```
<!-- pyml enable line-length -->
