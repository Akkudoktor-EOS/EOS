## Feed In Tariff Prediction Configuration

<!-- pyml disable line-length -->
:::{table} feedintariff
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| dvhubonline | `EOS_FEEDINTARIFF__DVHUBONLINE` | `FeedInTariffDvhubOnlineCommonSettings` | `rw` | `required` | DvhubOnline feed in tariff provider settings. |
| energycharts | `EOS_FEEDINTARIFF__ENERGYCHARTS` | `FeedInTariffEnergyChartsCommonSettings` | `rw` | `required` | EnergyCharts feed in tariff provider settings. |
| feedintarifffixed | `EOS_FEEDINTARIFF__FEEDINTARIFFFIXED` | `FeedInTariffFixedCommonSettings` | `rw` | `required` | Fixed feed in tariff provider settings. |
| feedintariffimport | `EOS_FEEDINTARIFF__FEEDINTARIFFIMPORT` | `FeedInTariffImportCommonSettings` | `rw` | `required` | Feed in tarif import provider settings. |
| provider | `EOS_FEEDINTARIFF__PROVIDER` | `str | None` | `rw` | `None` | Feed in tariff provider id of provider to be used. |
| providers | | `list[str]` | `ro` | `N/A` | Available feed in tariff provider ids. |
| smard | `EOS_FEEDINTARIFF__SMARD` | `FeedInTariffSMARDCommonSettings` | `rw` | `required` | SMARD feed in tariff provider settings. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "feedintariff": {
           "provider": "FeedInTariffFixed",
           "feedintarifffixed": {
               "feed_in_tariff_amt_kwh": {
                   "windows": []
               }
           },
           "feedintariffimport": {
               "import_file_path": null,
               "import_json": null
           },
           "dvhubonline": {
               "base_url": "https://dvhub.online",
               "zone": "DE-LU"
           },
           "energycharts": {
               "bidding_zone": "DE-LU"
           },
           "smard": {}
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
       "feedintariff": {
           "provider": "FeedInTariffFixed",
           "feedintarifffixed": {
               "feed_in_tariff_amt_kwh": {
                   "windows": []
               }
           },
           "feedintariffimport": {
               "import_file_path": null,
               "import_json": null
           },
           "dvhubonline": {
               "base_url": "https://dvhub.online",
               "zone": "DE-LU"
           },
           "energycharts": {
               "bidding_zone": "DE-LU"
           },
           "smard": {},
           "providers": [
               "FeedInTariffAkkudoktor",
               "FeedInTariffDvhubOnline",
               "FeedInTariffEnergyCharts",
               "FeedInTariffFixed",
               "FeedInTariffImport",
               "FeedInTariffSMARD",
               "FeedInTariffTibber"
           ]
       }
   }
```
<!-- pyml enable line-length -->

### Settings for SMARD feed-in prices shared with ``elecprice.smard``

<!-- pyml disable line-length -->
:::{table} feedintariff::smard
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "feedintariff": {
           "smard": {}
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for feed in tariff data import from file or JSON string

<!-- pyml disable line-length -->
:::{table} feedintariff::feedintariffimport
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| import_file_path | `str | pathlib.Path | None` | `rw` | `None` | Path to the file to import feed in tariff data from. |
| import_json | `str | None` | `rw` | `None` | JSON string, dictionary of feed in tariff forecast value lists. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "feedintariff": {
           "feedintariffimport": {
               "import_file_path": null,
               "import_json": "{\"fead_in_tariff_wh\": [0.000078, 0.000078, 0.000023]}"
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for elecprice fixed price

<!-- pyml disable line-length -->
:::{table} feedintariff::feedintarifffixed
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| feed_in_tariff_amt_kwh | `ValueTimeWindowSequence` | `rw` | `required` | Sequence of time windows defining the electricity feed in tariff [amount/kWh]. If not provided, no fixed feed in tariff is applied. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "feedintariff": {
           "feedintarifffixed": {
               "feed_in_tariff_amt_kwh": {
                   "windows": [
                       {
                           "start_time": "00:00:00.000000",
                           "duration": "8 hours",
                           "day_of_week": null,
                           "date": null,
                           "locale": null,
                           "value": 0.028
                       },
                       {
                           "start_time": "08:00:00.000000",
                           "duration": "16 hours",
                           "day_of_week": null,
                           "date": null,
                           "locale": null,
                           "value": 0.034
                       }
                   ]
               }
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for Energy-Charts feed-in tariff provider

<!-- pyml disable line-length -->
:::{table} feedintariff::energycharts
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
       "feedintariff": {
           "energycharts": {
               "bidding_zone": "DE-LU"
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for the dvhub.online feed-in tariff provider

<!-- pyml disable line-length -->
:::{table} feedintariff::dvhubonline
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| base_url | `str` | `rw` | `https://dvhub.online` | Base URL of the dvhub.online price API. |
| zone | `str` | `rw` | `DE-LU` | Bidding zone passed to the dvhub.online price API. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "feedintariff": {
           "dvhubonline": {
               "base_url": "https://dvhub.online",
               "zone": "DE-LU"
           }
       }
   }
```
<!-- pyml enable line-length -->
