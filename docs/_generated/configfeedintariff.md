## Feed In Tariff Prediction Configuration

<!-- pyml disable line-length -->
:::{table} feedintariff
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| provider | `EOS_FEEDINTARIFF__PROVIDER` | `Optional[str]` | `rw` | `None` | Feed in tariff provider id of provider to be used. |
| provider_settings | `EOS_FEEDINTARIFF__PROVIDER_SETTINGS` | `FeedInTariffCommonProviderSettings` | `rw` | `required` | Provider settings |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "feedintariff": {
           "provider": "FeedInTariffFixed",
           "provider_settings": {
               "FeedInTariffFixed": null,
               "FeedInTariffImport": null
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for feed in tariff data import from file or JSON string

<!-- pyml disable line-length -->
:::{table} feedintariff::provider_settings::FeedInTariffImport
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
           "provider_settings": {
               "FeedInTariffImport": {
                   "import_file_path": null,
                   "import_json": "{\"fead_in_tariff_wh\": [0.000078, 0.000078, 0.000023]}"
               }
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for elecprice fixed price

<!-- pyml disable line-length -->
:::{table} feedintariff::provider_settings::FeedInTariffFixed
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| feed_in_tariff_kwh | `Optional[float]` | `rw` | `None` | Electricity price feed in tariff [€/kWH]. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "feedintariff": {
           "provider_settings": {
               "FeedInTariffFixed": {
                   "feed_in_tariff_kwh": 0.078
               }
           }
       }
   }
```
<!-- pyml enable line-length -->

### Feed In Tariff Prediction Provider Configuration

<!-- pyml disable line-length -->
:::{table} feedintariff::provider_settings
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| FeedInTariffFixed | `Optional[akkudoktoreos.prediction.feedintarifffixed.FeedInTariffFixedCommonSettings]` | `rw` | `None` | FeedInTariffFixed settings |
| FeedInTariffImport | `Optional[akkudoktoreos.prediction.feedintariffimport.FeedInTariffImportCommonSettings]` | `rw` | `None` | FeedInTariffImport settings |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "feedintariff": {
           "provider_settings": {
               "FeedInTariffFixed": null,
               "FeedInTariffImport": null
           }
       }
   }
```
<!-- pyml enable line-length -->
