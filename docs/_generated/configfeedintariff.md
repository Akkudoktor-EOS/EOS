## Feed In Tariff Prediction Configuration

<!-- pyml disable line-length -->
:::{table} feedintariff
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| energycharts | `EOS_FEEDINTARIFF__ENERGYCHARTS` | `FeedInTariffEnergyChartsCommonSettings` | `rw` | `required` | EnergyCharts feed in tariff provider settings. |
| feedintarifffixed | `EOS_FEEDINTARIFF__FEEDINTARIFFFIXED` | `FeedInTariffFixedCommonSettings` | `rw` | `required` | Fixed feed in tariff provider settings. |
| feedintariffimport | `EOS_FEEDINTARIFF__FEEDINTARIFFIMPORT` | `FeedInTariffImportCommonSettings` | `rw` | `required` | Feed in tarif import provider settings. |
| provider | `EOS_FEEDINTARIFF__PROVIDER` | `Optional[str]` | `rw` | `None` | Feed in tariff provider id of provider to be used. |
| providers | | `list[str]` | `ro` | `N/A` | Available feed in tariff provider ids. |
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
               "feed_in_tariff_kwh": null
           },
           "feedintariffimport": {
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
       "feedintariff": {
           "provider": "FeedInTariffFixed",
           "feedintarifffixed": {
               "feed_in_tariff_kwh": null
           },
           "feedintariffimport": {
               "import_file_path": null,
               "import_json": null
           },
           "energycharts": {
               "bidding_zone": "DE-LU"
           },
           "providers": [
               "FeedInTariffAkkudoktor",
               "FeedInTariffEnergyCharts",
               "FeedInTariffFixed",
               "FeedInTariffImport"
           ]
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
| import_file_path | `Union[str, pathlib.Path, NoneType]` | `rw` | `None` | Path to the file to import feed in tariff data from. |
| import_json | `Optional[str]` | `rw` | `None` | JSON string, dictionary of feed in tariff forecast value lists. |
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
| feed_in_tariff_kwh | `Optional[float]` | `rw` | `None` | Electricity price feed in tariff [amount/kWh]. |
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
               "feed_in_tariff_kwh": 0.078
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
