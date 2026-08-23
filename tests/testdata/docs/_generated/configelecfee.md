## Electricity Price Prediction Configuration

<!-- pyml disable line-length -->
:::{table} elecfee
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| elecfeefixed | `EOS_ELECFEE__ELECFEEFIXED` | `ElecFeeFixedCommonSettings` | `rw` | `required` | Fixed electricity fees provider settings. |
| elecfeeimport | `EOS_ELECFEE__ELECFEEIMPORT` | `ElecFeeImportCommonSettings` | `rw` | `required` | Electricity fees import provider settings. |
| provider | `EOS_ELECFEE__PROVIDER` | `str | None` | `rw` | `None` | Electricity fee provider id of provider to be used. |
| providers | | `list[str]` | `ro` | `N/A` | Available electricity fee provider ids. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "elecfee": {
           "provider": "ElecFeeFixed",
           "elecfeefixed": {
               "consumption_amt_kwh": {
                   "windows": []
               },
               "consumption_percent_amt": {
                   "windows": []
               },
               "feedin_amt_kwh": {
                   "windows": []
               },
               "feedin_percent_amt": {
                   "windows": []
               }
           },
           "elecfeeimport": {
               "import_file_path": null,
               "import_json": null
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
       "elecfee": {
           "provider": "ElecFeeFixed",
           "elecfeefixed": {
               "consumption_amt_kwh": {
                   "windows": []
               },
               "consumption_percent_amt": {
                   "windows": []
               },
               "feedin_amt_kwh": {
                   "windows": []
               },
               "feedin_percent_amt": {
                   "windows": []
               }
           },
           "elecfeeimport": {
               "import_file_path": null,
               "import_json": null
           },
           "providers": [
               "ElecFeeFixed",
               "ElecFeeImport"
           ]
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for elecfee data import from file or JSON String

<!-- pyml disable line-length -->
:::{table} elecfee::elecfeeimport
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| import_file_path | `str | pathlib.Path | None` | `rw` | `None` | Path to the file to import elecfee data from. |
| import_json | `str | None` | `rw` | `None` | JSON string, dictionary of electricity fee forecast value lists. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "elecfee": {
           "elecfeeimport": {
               "import_file_path": null,
               "import_json": "{\"elecfee_consumption_amt_wh\": [0.0003384, 0.0003318, 0.0003284]}"
           }
       }
   }
```
<!-- pyml enable line-length -->

### Value applicable during a specific time window

This model extends `TimeWindow` by associating a value with the defined time interval.

<!-- pyml disable line-length -->
:::{table} elecfee::elecfeefixed::consumption_amt_kwh::windows::list
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
       "elecfee": {
           "elecfeefixed": {
               "consumption_amt_kwh": {
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
:::{table} elecfee::elecfeefixed::consumption_amt_kwh
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
       "elecfee": {
           "elecfeefixed": {
               "consumption_amt_kwh": {
                   "windows": []
               }
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for fixed electricity fees

This model defines a fixed electricity fee schedule using a sequence
of time windows. Each window specifies a time interval and the electricity
fee applicable during that interval.

<!-- pyml disable line-length -->
:::{table} elecfee::elecfeefixed
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| consumption_amt_kwh | `ValueTimeWindowSequence` | `rw` | `required` | Sequence of time windows defining the total fixed per-kWh electricty fee charged for consumed energy, accumulating all applicable fixed per-kWh charges (e.g. network charge, metering fee, concession fee) into a single amount [amount/kWh]. If not provided, no fixed per-kWh consumption fee is applied. |
| consumption_percent_amt | `ValueTimeWindowSequence` | `rw` | `required` | Sequence of time windows defining the total fixed electricity surcharge applied as a percentage of the monetary amount already charged for consumed energy, accumulating all applicable percentage-based surcharges (e.g. VAT, electricity tax) into a single percentage [%]. This is a percentage of the fee amount, not a per-kWh rate. If not provided, no percentage-based consumption surcharge is applied. |
| feedin_amt_kwh | `ValueTimeWindowSequence` | `rw` | `required` | Sequence of time windows defining the total deduction from feed-in energy per Wh [amount/Wh]. This is the accumulation of all fixed per-Wh charges deducted from feed-in energy - such as metering fees or grid-operator handling charges - into a single amount. Applied after the percentage-based deduction, i.e. it reduces the price by a flat amount per Wh rather than by a share of the raw price. If not provided, no fixed per-kWh feed-in fee is applied. |
| feedin_percent_amt | `ValueTimeWindowSequence` | `rw` | `required` | Sequence of time windows defining the total percentage deducted from the raw feed-in price (spot price) [%]. This is the accumulation of all percentage-based deductions payable on the feed-in tariff - such as a marketing or balancing fee retained by the aggregator - into a single percentage. It is applied as `raw_price * (100 - percent) / 100`, i.e. it scales down the raw price rather than adding a surcharge to it. If not provided, no percentage-based feed-in deduction is applied. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "elecfee": {
           "elecfeefixed": {
               "consumption_amt_kwh": {
                   "windows": [
                       {
                           "start_time": "00:00:00.000000",
                           "duration": "8 hours",
                           "day_of_week": null,
                           "date": null,
                           "locale": null,
                           "value": 0.00288
                       },
                       {
                           "start_time": "08:00:00.000000",
                           "duration": "16 hours",
                           "day_of_week": null,
                           "date": null,
                           "locale": null,
                           "value": 0.0034
                       }
                   ]
               },
               "consumption_percent_amt": {
                   "windows": [
                       {
                           "start_time": "00:00:00.000000",
                           "duration": "1 day",
                           "day_of_week": null,
                           "date": null,
                           "locale": null,
                           "value": 19.0
                       }
                   ]
               },
               "feedin_amt_kwh": {
                   "windows": [
                       {
                           "start_time": "00:00:00.000000",
                           "duration": "8 hours",
                           "day_of_week": null,
                           "date": null,
                           "locale": null,
                           "value": 0.00288
                       },
                       {
                           "start_time": "08:00:00.000000",
                           "duration": "16 hours",
                           "day_of_week": null,
                           "date": null,
                           "locale": null,
                           "value": 0.0034
                       }
                   ]
               },
               "feedin_percent_amt": {
                   "windows": [
                       {
                           "start_time": "00:00:00.000000",
                           "duration": "1 day",
                           "day_of_week": null,
                           "date": null,
                           "locale": null,
                           "value": 19.0
                       }
                   ]
               }
           }
       }
   }
```
<!-- pyml enable line-length -->
