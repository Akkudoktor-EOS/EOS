## Electricity Price Prediction Configuration

<!-- pyml disable line-length -->
:::{table} elecprice
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| charges | `EOS_ELECPRICE__CHARGES` | `list[akkudoktoreos.prediction.elecprice.ElecPriceChargeComponent] | None` | `rw` | `None` | Ordered list of electricity price charge/fee components added on top of the market (spot working) price to build the final consumer price. Applied by all providers except the import provider. If omitted, the market price is used unchanged. |
| elecpricefixed | `EOS_ELECPRICE__ELECPRICEFIXED` | `ElecPriceFixedCommonSettings` | `rw` | `required` | Fixed electricity price provider settings. |
| elecpriceimport | `EOS_ELECPRICE__ELECPRICEIMPORT` | `ElecPriceImportCommonSettings` | `rw` | `required` | Electricity price import provider settings. |
| energycharts | `EOS_ELECPRICE__ENERGYCHARTS` | `ElecPriceEnergyChartsCommonSettings` | `rw` | `required` | Energy Charts provider settings. |
| provider | `EOS_ELECPRICE__PROVIDER` | `str | None` | `rw` | `None` | Electricity price provider id of provider to be used. |
| providers | | `list[str]` | `ro` | `N/A` | Available electricity price provider ids. |
| tibber | `EOS_ELECPRICE__TIBBER` | `ElecPriceTibberCommonSettings` | `rw` | `required` | Tibber electricity price provider settings. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "elecprice": {
           "provider": "ElecPriceAkkudoktor",
           "charges": [
               {
                   "name": "Netzentgelt",
                   "type": "fixed",
                   "amount": 0.1153,
                   "basis": null
               },
               {
                   "name": "Stromsteuer",
                   "type": "fixed",
                   "amount": 0.0205,
                   "basis": null
               },
               {
                   "name": "MwSt",
                   "type": "percent",
                   "amount": 0.19,
                   "basis": null
               }
           ],
           "elecpricefixed": {
               "time_windows": {
                   "windows": []
               }
           },
           "elecpriceimport": {
               "import_file_path": null,
               "import_json": null
           },
           "energycharts": {
               "bidding_zone": "DE-LU"
           },
           "tibber": {
               "access_token": null,
               "home_id": null
           }
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
       "elecprice": {
           "provider": "ElecPriceAkkudoktor",
           "charges": [
               {
                   "name": "Netzentgelt",
                   "type": "fixed",
                   "amount": 0.1153,
                   "basis": null
               },
               {
                   "name": "Stromsteuer",
                   "type": "fixed",
                   "amount": 0.0205,
                   "basis": null
               },
               {
                   "name": "MwSt",
                   "type": "percent",
                   "amount": 0.19,
                   "basis": null
               }
           ],
           "elecpricefixed": {
               "time_windows": {
                   "windows": []
               }
           },
           "elecpriceimport": {
               "import_file_path": null,
               "import_json": null
           },
           "energycharts": {
               "bidding_zone": "DE-LU"
           },
           "tibber": {
               "access_token": null,
               "home_id": null
           },
           "providers": [
               "ElecPriceAkkudoktor",
               "ElecPriceEnergyCharts",
               "ElecPriceFixed",
               "ElecPriceImport",
               "ElecPriceTibber"
           ]
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for the Tibber electricity price provider

<!-- pyml disable line-length -->
:::{table} elecprice::tibber
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| access_token | `str | None` | `rw` | `None` | Tibber API access token. |
| home_id | `str | None` | `rw` | `None` | Optional Tibber home id. If omitted, the first home with a subscription is used. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "elecprice": {
           "tibber": {
               "access_token": "tibber_pat_...",
               "home_id": "00000000-0000-0000-0000-000000000000"
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for Energy Charts electricity price provider

<!-- pyml disable line-length -->
:::{table} elecprice::energycharts
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| bidding_zone | `<enum 'EnergyChartsBiddingZones'>` | `rw` | `DE-LU` | Bidding Zone: 'AT', 'BE', 'CH', 'CZ', 'DE-LU', 'DE-AT-LU', 'DK1', 'DK2', 'FR', 'HU', 'IT-NORTH', 'NL', 'NO2', 'PL', 'SE4' or 'SI' |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "elecprice": {
           "energycharts": {
               "bidding_zone": "AT"
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for elecprice data import from file or JSON String

<!-- pyml disable line-length -->
:::{table} elecprice::elecpriceimport
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| import_file_path | `str | pathlib.Path | None` | `rw` | `None` | Path to the file to import elecprice data from. |
| import_json | `str | None` | `rw` | `None` | JSON string, dictionary of electricity price forecast value lists. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "elecprice": {
           "elecpriceimport": {
               "import_file_path": null,
               "import_json": "{\"elecprice_marketprice_wh\": [0.0003384, 0.0003318, 0.0003284]}"
           }
       }
   }
```
<!-- pyml enable line-length -->

### Value applicable during a specific time window

This model extends `TimeWindow` by associating a value with the defined time interval.

<!-- pyml disable line-length -->
:::{table} elecprice::elecpricefixed::time_windows::windows::list
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| date | `pydantic_extra_types.pendulum_dt.Date | None` | `rw` | `None` | Optional specific calendar date for the time window. Naive — matched against the local date of the datetime passed to contains(). Overrides `day_of_week` if set. |
| day_of_week | `int | str | None` | `rw` | `None` | Optional day of the week restriction. Can be specified as integer (0=Monday to 6=Sunday) or localized weekday name. If None, applies every day unless `date` is set. |
| duration | `Duration` | `rw` | `required` | Duration of the time window starting from `start_time`. |
| locale | `str | None` | `rw` | `None` | Locale used to parse weekday names in `day_of_week` when given as string. If not set, Pendulum's default locale is used. Examples: 'en', 'de', 'fr', etc. |
| start_time | `Time` | `rw` | `required` | Naive start time of the time window (time of day, no timezone). Interpreted in the timezone of the datetime passed to contains() or earliest_start_time(). |
| value | `float | None` | `rw` | `None` | Value applicable during this time window. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "elecprice": {
           "elecpricefixed": {
               "time_windows": {
                   "windows": [
                       {
                           "start_time": "00:00:00.000000",
                           "duration": "2 hours",
                           "day_of_week": null,
                           "date": null,
                           "locale": null,
                           "value": 0.288
                       }
                   ]
               }
           }
       }
   }
```
<!-- pyml enable line-length -->

### Sequence of value time windows

This model specializes `TimeWindowSequence` to ensure that all
contained windows are instances of `ValueTimeWindow`.
It provides the full set of sequence operations (containment checks,
availability, start time calculations) for value windows.

<!-- pyml disable line-length -->
:::{table} elecprice::elecpricefixed::time_windows
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| windows | `list[akkudoktoreos.config.configabc.ValueTimeWindow]` | `rw` | `required` | Ordered list of value time windows. Each window defines a time interval and an associated value. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "elecprice": {
           "elecpricefixed": {
               "time_windows": {
                   "windows": []
               }
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common configuration settings for fixed electricity pricing

This model defines a fixed electricity price schedule using a sequence
of time windows. Each window specifies a time interval and the electricity
price applicable during that interval.

<!-- pyml disable line-length -->
:::{table} elecprice::elecpricefixed
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| time_windows | `ValueTimeWindowSequence` | `rw` | `required` | Sequence of time windows defining the fixed price schedule. If not provided, no fixed pricing is applied. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "elecprice": {
           "elecpricefixed": {
               "time_windows": {
                   "windows": [
                       {
                           "start_time": "00:00:00.000000",
                           "duration": "8 hours",
                           "day_of_week": null,
                           "date": null,
                           "locale": null,
                           "value": 0.288
                       },
                       {
                           "start_time": "08:00:00.000000",
                           "duration": "16 hours",
                           "day_of_week": null,
                           "date": null,
                           "locale": null,
                            "value": 0.34
                        }
                    ]
                }
            }
        }
    }
 ```
<!-- pyml enable line-length -->

### A single electricity price charge/fee component

Components are applied in order (first to last) on top of the market
(spot working) price to build the final consumer price. A component is
either a fixed absolute amount per kWh or a percentage add-on computed on a
configurable basis.

<!-- pyml disable line-length -->
:::{table} elecprice::charges::list
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| amount | `float` | `rw` | `required` | For 'fixed' components the absolute amount per kWh [amount/kWh]. For 'percent' components the rate as a fraction (e.g. 0.19 for 19%). |
| basis | `list[str] | None` | `rw` | `None` | Only used for 'percent' components. Names of the preceding components (and/or the literal 'market' for the market price) that form the basis of the percentage. If omitted, the percentage applies to the full accumulated price so far (market price plus all preceding add-ons). |
| name | `str | None` | `rw` | `None` | Optional name of the charge component. Used to reference this component as the basis of a later percentage component. |
| type | `Literal['fixed', 'percent']` | `rw` | `required` | Type of the charge component. 'fixed' adds an absolute amount per kWh, 'percent' adds a percentage of a basis. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "elecprice": {
           "charges": [
               {
                   "name": "Netzentgelt",
                   "type": "fixed",
                   "amount": 0.0205,
                   "basis": [
                       "market"
                   ]
               }
           ]
       }
   }
```
<!-- pyml enable line-length -->
