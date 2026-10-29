## EOS-Dash Configuration

<!-- pyml disable line-length -->
:::{table} eosdash
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| plan | `EOS_EOSDASH__PLAN` | `EOSdashPlanSettings` | `rw` | `required` | Plan page settings. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "eosdash": {
           "plan": {
               "visible": {},
               "alias": {},
               "color": {},
               "style": {},
               "width": {},
               "line_dash": {},
               "device_alias": {},
               "plan_table_open": false,
               "energy_kwh": true,
               "factor_percent": true,
               "price_minor_unit": true
           }
       }
   }
```
<!-- pyml enable line-length -->

### EOSdash plan page settings

Keys are the columns of the optimization solution chart (e.g. ``battery1_soc_factor``)
or EMS plan commands (operation modes) with the prefix ``mode_``
(e.g. ``mode_SELF_CONSUMPTION``). A command applies to the plan timeline and to the
operation mode factor columns of all devices. Only choices of the user are stored,
everything else uses the defaults of EOSdash.

<!-- pyml disable line-length -->
:::{table} eosdash::plan
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| alias | `dict[str, Annotated[str, StringConstraints(strip_whitespace=True, to_upper=None, to_lower=None, strict=None, min_length=1, max_length=32, pattern=None, ascii_only=None)]]` | `rw` | `required` | Display names of solution chart columns and commands. |
| color | `dict[str, Annotated[str, StringConstraints(strip_whitespace=None, to_upper=None, to_lower=None, strict=None, min_length=None, max_length=None, pattern='^#[0-9A-Fa-f]{6}$', ascii_only=None)]]` | `rw` | `required` | Colors of solution chart columns and commands (#RRGGBB). |
| device_alias | `dict[str, Annotated[str, StringConstraints(strip_whitespace=True, to_upper=None, to_lower=None, strict=None, min_length=1, max_length=32, pattern=None, ascii_only=None)]]` | `rw` | `required` | Display names of devices (device id -> name) on the plan page. |
| energy_kwh | `bool` | `rw` | `True` | Energy in kWh instead of Wh in the solution chart. Defaults to True. |
| factor_percent | `bool` | `rw` | `True` | Factors in % instead of 0.0..1.0 in the solution chart. Defaults to True. |
| line_dash | `dict[str, Literal['solid', 'dashed', 'dotted']]` | `rw` | `required` | Line dash of solution chart columns and commands. |
| plan_table_open | `bool` | `rw` | `False` | Energy management plan table open on page load. Defaults to False. |
| price_minor_unit | `bool` | `rw` | `True` | Electricity price in the minor unit of the currency per kWh (e.g. ct/kWh for EUR, Rp./kWh for CHF) instead of currency/kWh in the solution chart. Has no effect for currencies without a minor unit. Defaults to True. |
| style | `dict[str, Literal['line', 'bar']]` | `rw` | `required` | Chart type of solution chart columns and commands. |
| visible | `dict[str, bool]` | `rw` | `required` | Visibility of solution chart columns and commands. |
| width | `dict[str, Annotated[int, FieldInfo(annotation=NoneType, required=True, metadata=[Ge(ge=1), Le(le=5)])]]` | `rw` | `required` | Line width (1..5) of solution chart columns and commands. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "eosdash": {
           "plan": {
               "visible": {
                   "grid_feedin_energy_wh": false
               },
               "alias": {
                   "mode_SELF_CONSUMPTION": "Self consumption"
               },
               "color": {
                   "battery1_soc_factor": "#DAA520"
               },
               "style": {
                   "pvforecast_ac_energy_wh": "bar"
               },
               "width": {
                   "loadforecast_energy_wh": 3
               },
               "line_dash": {
                   "elec_price_amt_kwh": "dotted"
               },
               "device_alias": {
                   "battery1": "House battery"
               },
               "plan_table_open": false,
               "energy_kwh": true,
               "factor_percent": true,
               "price_minor_unit": true
           }
       }
   }
```
<!-- pyml enable line-length -->
