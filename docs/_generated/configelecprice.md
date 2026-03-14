## Electricity Price Prediction Configuration

<!-- pyml disable line-length -->
:::{table} elecprice
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| charges_kwh | `EOS_ELECPRICE__CHARGES_KWH` | `Optional[float]` | `rw` | `None` | Electricity price charges [€/kWh]. Will be added to variable market price. |
| elecpricefixed | `EOS_ELECPRICE__ELECPRICEFIXED` | `ElecPriceFixedCommonSettings` | `rw` | `required` | Fixed electricity price provider settings. |
| elecpriceimport | `EOS_ELECPRICE__ELECPRICEIMPORT` | `ElecPriceImportCommonSettings` | `rw` | `required` | Import provider settings. |
| energycharts | `EOS_ELECPRICE__ENERGYCHARTS` | `ElecPriceEnergyChartsCommonSettings` | `rw` | `required` | Energy Charts provider settings. |
| provider | `EOS_ELECPRICE__PROVIDER` | `Optional[str]` | `rw` | `None` | Electricity price provider id of provider to be used. |
| providers | | `list[str]` | `ro` | `N/A` | Available electricity price provider ids. |
| vat_rate | `EOS_ELECPRICE__VAT_RATE` | `Optional[float]` | `rw` | `1.19` | VAT rate factor applied to electricity price when charges are used. |
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
           "charges_kwh": 0.21,
           "vat_rate": 1.19,
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
           "charges_kwh": 0.21,
           "vat_rate": 1.19,
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
           "providers": [
               "ElecPriceAkkudoktor",
               "ElecPriceEnergyCharts",
               "ElecPriceFixed",
               "ElecPriceImport"
           ]
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
| bidding_zone | `<enum 'EnergyChartsBiddingZones'>` | `rw` | `EnergyChartsBiddingZones.DE_LU` | Bidding Zone: 'AT', 'BE', 'CH', 'CZ', 'DE-LU', 'DE-AT-LU', 'DK1', 'DK2', 'FR', 'HU', 'IT-NORTH', 'NL', 'NO2', 'PL', 'SE4' or 'SI' |
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
| import_file_path | `Union[str, pathlib.Path, NoneType]` | `rw` | `None` | Path to the file to import elecprice data from. |
| import_json | `Optional[str]` | `rw` | `None` | JSON string, dictionary of electricity price forecast value lists. |
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
| date | `Optional[pydantic_extra_types.pendulum_dt.Date]` | `rw` | `None` | Optional specific calendar date for the time window. Naive — matched against the local date of the datetime passed to contains(). Overrides `day_of_week` if set. |
| day_of_week | `Union[int, str, NoneType]` | `rw` | `None` | Optional day of the week restriction. Can be specified as integer (0=Monday to 6=Sunday) or localized weekday name. If None, applies every day unless `date` is set. |
| duration | `Duration` | `rw` | `required` | Duration of the time window starting from `start_time`. |
| locale | `Optional[str]` | `rw` | `None` | Locale used to parse weekday names in `day_of_week` when given as string. If not set, Pendulum's default locale is used. Examples: 'en', 'de', 'fr', etc. |
| start_time | `Time` | `rw` | `required` | Naive start time of the time window (time of day, no timezone). Interpreted in the timezone of the datetime passed to contains() or earliest_start_time(). |
| value | `Optional[float]` | `rw` | `None` | Value applicable during this time window. |
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
