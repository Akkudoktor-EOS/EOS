## Electricity Price Prediction Configuration

<!-- pyml disable line-length -->
:::{table} elecprice
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| charges_kwh | `EOS_ELECPRICE__CHARGES_KWH` | `Optional[float]` | `rw` | `None` | Electricity price charges [€/kWh]. Will be added to variable market price. |
| provider | `EOS_ELECPRICE__PROVIDER` | `Optional[str]` | `rw` | `None` | Electricity price provider id of provider to be used. |
| provider_settings | `EOS_ELECPRICE__PROVIDER_SETTINGS` | `ElecPriceCommonProviderSettings` | `rw` | `required` | Provider settings |
| vat_rate | `EOS_ELECPRICE__VAT_RATE` | `Optional[float]` | `rw` | `1.19` | VAT rate factor applied to electricity price when charges are used. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "elecprice": {
           "provider": "ElecPriceAkkudoktor",
           "charges_kwh": 0.21,
           "vat_rate": 1.19,
           "provider_settings": {
               "ElecPriceImport": null
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for elecprice data import from file or JSON String

<!-- pyml disable line-length -->
:::{table} elecprice::provider_settings::ElecPriceImport
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
           "provider_settings": {
               "ElecPriceImport": {
                   "import_file_path": null,
                   "import_json": "{\"elecprice_marketprice_wh\": [0.0003384, 0.0003318, 0.0003284]}"
               }
           }
       }
   }
```
<!-- pyml enable line-length -->

### Electricity Price Prediction Provider Configuration

<!-- pyml disable line-length -->
:::{table} elecprice::provider_settings
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| ElecPriceImport | `Optional[akkudoktoreos.prediction.elecpriceimport.ElecPriceImportCommonSettings]` | `rw` | `None` | ElecPriceImport settings |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "elecprice": {
           "provider_settings": {
               "ElecPriceImport": null
           }
       }
   }
```
<!-- pyml enable line-length -->
