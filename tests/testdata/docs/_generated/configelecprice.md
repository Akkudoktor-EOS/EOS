## Electricity Price Prediction Configuration

<!-- pyml disable line-length -->
:::{table} elecprice
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| akkudoktor | `EOS_ELECPRICE__AKKUDOKTOR` | `ElecPriceAkkudoktorCommonSettings` | `rw` | `required` | Akkudoktor electricity price provider settings. |
| elecpricefixed | `EOS_ELECPRICE__ELECPRICEFIXED` | `ElecPriceFixedCommonSettings` | `rw` | `required` | Fixed electricity price provider settings. |
| elecpriceimport | `EOS_ELECPRICE__ELECPRICEIMPORT` | `ElecPriceImportCommonSettings` | `rw` | `required` | Electricity price import provider settings. |
| energycharts | `EOS_ELECPRICE__ENERGYCHARTS` | `ElecPriceEnergyChartsCommonSettings` | `rw` | `required` | Energy Charts provider settings. |
| provider | `EOS_ELECPRICE__PROVIDER` | `str | None` | `rw` | `None` | Electricity price provider id of provider to be used. |
| providers | | `list[str]` | `ro` | `N/A` | Available electricity price provider ids. |
| smard | `EOS_ELECPRICE__SMARD` | `ElecPriceSMARDCommonSettings` | `rw` | `required` | SMARD electricity price provider settings. |
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
           "akkudoktor": {
               "apply_fees": false
           },
           "elecpricefixed": {
               "apply_fees": false,
               "elecprice_marketprice_amt_kwh": {
                   "windows": []
               }
           },
           "elecpriceimport": {
               "import_file_path": null,
               "import_json": null
           },
           "energycharts": {
               "apply_fees": false,
               "bidding_zone": "DE-LU"
           },
           "smard": {
               "apply_fees": false,
               "filter_id": 4169,
               "region": "DE"
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
           "akkudoktor": {
               "apply_fees": false
           },
           "elecpricefixed": {
               "apply_fees": false,
               "elecprice_marketprice_amt_kwh": {
                   "windows": []
               }
           },
           "elecpriceimport": {
               "import_file_path": null,
               "import_json": null
           },
           "energycharts": {
               "apply_fees": false,
               "bidding_zone": "DE-LU"
           },
           "smard": {
               "apply_fees": false,
               "filter_id": 4169,
               "region": "DE"
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
               "ElecPriceSMARD",
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

### Common settings for the direct SMARD electricity-price provider

<!-- pyml disable line-length -->
:::{table} elecprice::smard
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| apply_fees | `bool` | `rw` | `False` | Apply electricity fees as given by the ElecFee provider to the electricity prices. Electricity fees are added to the consumed energy prices. |
| filter_id | `int` | `rw` | `4169` | SMARD filter id for the German/Luxembourg day-ahead price. |
| region | `str` | `rw` | `DE` | SMARD market region used in the chart-data endpoint. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "elecprice": {
           "smard": {
               "apply_fees": false,
               "filter_id": 4169,
               "region": "DE"
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
| apply_fees | `bool` | `rw` | `False` | Apply electricity fees as given by the ElecFee provider to the electricity prices. Electricity fees are added to the consumed energy prices. |
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
               "apply_fees": false,
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
| apply_fees | `bool` | `rw` | `False` | Apply electricity fees as given by the ElecFee provider to the electricity prices. Electricity fees are added to the consumed energy prices. |
| elecprice_marketprice_amt_kwh | `ValueTimeWindowSequence` | `rw` | `required` | Sequence of time windows defining the fixed price schedule. If not provided, no fixed pricing is applied. |
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
               "apply_fees": false,
               "elecprice_marketprice_amt_kwh": {
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

### Common configuration settings for Akkodoktor electricity pricing

<!-- pyml disable line-length -->
:::{table} elecprice::akkudoktor
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| apply_fees | `bool` | `rw` | `False` | Apply electricity fees as given by the ElecFee provider to the electricity prices. Electricity fees are added to the consumed energy prices. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "elecprice": {
           "akkudoktor": {
               "apply_fees": false
           }
       }
   }
```
<!-- pyml enable line-length -->
