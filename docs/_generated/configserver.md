## Server Configuration

<!-- pyml disable line-length -->
:::{table} server
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| eosdash_host | `EOS_SERVER__EOSDASH_HOST` | `str` | `rw` | `127.0.0.1` | EOSdash server IP address. Defaults to EOS server IP address. |
| eosdash_port | `EOS_SERVER__EOSDASH_PORT` | `int` | `rw` | `8504` | EOSdash server IP port number. Defaults to 8504. |
| eosdash_public_url | `EOS_SERVER__EOSDASH_PUBLIC_URL` | `Optional[str]` | `rw` | `None` | Public EOSdash base URL for redirects and error-page links, including an optional proxy path prefix. Set this for reverse proxies or mapped ports; it does not change the bind address. Without it, direct access uses the request host and EOSdash port. Raw forwarded headers are not used. |
| eosdash_supervise_interval_sec | `EOS_SERVER__EOSDASH_SUPERVISE_INTERVAL_SEC` | `int` | `rw` | `10` | Supervision interval for EOS server to supervise EOSdash [seconds]. |
| host | `EOS_SERVER__HOST` | `str` | `rw` | `127.0.0.1` | EOS server IP address. Defaults to 127.0.0.1. |
| port | `EOS_SERVER__PORT` | `int` | `rw` | `8503` | EOS server IP port number. Defaults to 8503. |
| reload | `EOS_SERVER__RELOAD` | `Optional[bool]` | `rw` | `False` | Enable server auto-reload for debugging or development. Default is False. Monitors the package directory for changes and reloads the server. |
| run_as_user | `EOS_SERVER__RUN_AS_USER` | `Optional[str]` | `rw` | `None` | The name of the target user to switch to. If ``None`` (default), the current effective user is used and no privilege change is attempted. |
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
           "eosdash_port": 8504,
           "eosdash_public_url": "https://energy.example.com/dashboard",
           "eosdash_supervise_interval_sec": 10,
           "run_as_user": null,
           "reload": true
       }
   }
```
<!-- pyml enable line-length -->
