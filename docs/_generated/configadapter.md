## Adapter Configuration

<!-- pyml disable line-length -->
:::{table} adapter
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| homeassistant | `EOS_ADAPTER__HOMEASSISTANT` | `HomeAssistantAdapterCommonSettings` | `rw` | `required` | Home Assistant adapter settings. |
| nodered | `EOS_ADAPTER__NODERED` | `NodeREDAdapterCommonSettings` | `rw` | `required` | NodeRED adapter settings. |
| provider | `EOS_ADAPTER__PROVIDER` | `Union[Literal['HomeAssistant', 'NodeRED'], list[Literal['HomeAssistant', 'NodeRED']], NoneType]` | `rw` | `None` | Adapter provider id(s) of provider(s) to be used [HomeAssistant, NodeRED, None]. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "adapter": {
           "provider": "HomeAssistant",
           "homeassistant": {
               "host": "127.0.0.1",
               "port": 8123
           },
           "nodered": {
               "host": "127.0.0.1",
               "port": 1880
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for the NodeRED adapter

The Node-RED adapter sends to HTTP IN nodes.

This is the example flow:

[HTTP In \\<URL\\>] -> [Function (parse payload)] -> [Debug] -> [HTTP Response]

There are two URLs that are used:

- GET /eos/data_aquisition
  The GET is issued before the optimization.
- POST /eos/control_dispatch
  The POST is issued after the optimization.

<!-- pyml disable line-length -->
:::{table} adapter::nodered
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| host | `Optional[str]` | `rw` | `127.0.0.1` | Node-RED server IP address. Defaults to 127.0.0.1. |
| port | `Optional[int]` | `rw` | `1880` | Node-RED server IP port number. Defaults to 1880. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "adapter": {
           "nodered": {
               "host": "127.0.0.1",
               "port": 1880
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for home assistant adapter provider

<!-- pyml disable line-length -->
:::{table} adapter::homeassistant
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| host | `Optional[str]` | `rw` | `127.0.0.1` | Home Assitant server IP address. Defaults to 127.0.0.1. |
| port | `Optional[int]` | `rw` | `8123` | Home Assistant server IP port number. Defaults to 8123. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "adapter": {
           "homeassistant": {
               "host": "127.0.0.1",
               "port": 8123
           }
       }
   }
```
<!-- pyml enable line-length -->
