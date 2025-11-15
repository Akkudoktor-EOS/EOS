## Full example Config

<!-- pyml disable line-length -->
```json
   {
       "cache": {
           "subpath": "cache",
           "cleanup_interval": 300.0
       },
       "devices": {
           "batteries": [
               {
                   "device_id": "battery1",
                   "capacity_wh": 8000,
                   "charging_efficiency": 0.88,
                   "discharging_efficiency": 0.88,
                   "levelized_cost_of_storage_kwh": 0.0,
                   "max_charge_power_w": 5000,
                   "min_charge_power_w": 50,
                   "charge_rates": "[0.  0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9 1. ]",
                   "min_soc_percentage": 0,
                   "max_soc_percentage": 100,
                   "measurement_key_soc_factor": "battery1-soc-factor",
                   "measurement_key_power_l1_w": "battery1-power-l1-w",
                   "measurement_key_power_l2_w": "battery1-power-l2-w",
                   "measurement_key_power_l3_w": "battery1-power-l3-w",
                   "measurement_key_power_3_phase_sym_w": "battery1-power-3-phase-sym-w",
                   "measurement_keys": [
                       "battery1-soc-factor",
                       "battery1-power-l1-w",
                       "battery1-power-l2-w",
                       "battery1-power-l3-w",
                       "battery1-power-3-phase-sym-w"
                   ]
               }
           ],
           "max_batteries": 1,
           "electric_vehicles": [
               {
                   "device_id": "battery1",
                   "capacity_wh": 8000,
                   "charging_efficiency": 0.88,
                   "discharging_efficiency": 0.88,
                   "levelized_cost_of_storage_kwh": 0.0,
                   "max_charge_power_w": 5000,
                   "min_charge_power_w": 50,
                   "charge_rates": "[0.  0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9 1. ]",
                   "min_soc_percentage": 0,
                   "max_soc_percentage": 100,
                   "measurement_key_soc_factor": "battery1-soc-factor",
                   "measurement_key_power_l1_w": "battery1-power-l1-w",
                   "measurement_key_power_l2_w": "battery1-power-l2-w",
                   "measurement_key_power_l3_w": "battery1-power-l3-w",
                   "measurement_key_power_3_phase_sym_w": "battery1-power-3-phase-sym-w",
                   "measurement_keys": [
                       "battery1-soc-factor",
                       "battery1-power-l1-w",
                       "battery1-power-l2-w",
                       "battery1-power-l3-w",
                       "battery1-power-3-phase-sym-w"
                   ]
               }
           ],
           "max_electric_vehicles": 1,
           "inverters": [],
           "max_inverters": 1,
           "home_appliances": [],
           "max_home_appliances": 1
       },
       "elecprice": {
           "provider": "ElecPriceAkkudoktor",
           "charges_kwh": 0.21,
           "vat_rate": 1.19,
           "elecpriceimport": {
               "import_file_path": null,
               "import_json": null
           },
           "energycharts": {
               "bidding_zone": "DE-LU"
           }
       },
       "ems": {
           "startup_delay": 5.0,
           "interval": 300.0,
           "mode": "OPTIMIZATION"
       },
       "feedintariff": {
           "provider": "FeedInTariffFixed",
           "provider_settings": {
               "FeedInTariffFixed": null,
               "FeedInTariffImport": null
           }
       },
       "general": {
           "version": "0.2.0+dev.4dbc2d",
           "data_folder_path": null,
           "data_output_subpath": "output",
           "latitude": 52.52,
           "longitude": 13.405
       },
       "load": {
           "provider": "LoadAkkudoktor",
           "provider_settings": {
               "LoadAkkudoktor": null,
               "LoadVrm": null,
               "LoadImport": null
           }
       },
       "logging": {
           "console_level": "TRACE",
           "file_level": "TRACE"
       },
       "measurement": {
           "load_emr_keys": [
               "load0_emr"
           ],
           "grid_export_emr_keys": [
               "grid_export_emr"
           ],
           "grid_import_emr_keys": [
               "grid_import_emr"
           ],
           "pv_production_emr_keys": [
               "pv1_emr"
           ]
       },
       "optimization": {
           "horizon_hours": 24,
           "interval": 3600,
           "algorithm": "GENETIC",
           "genetic": {
               "individuals": 400,
               "generations": 400,
               "seed": null,
               "penalties": {
                   "ev_soc_miss": 10
               }
           }
       },
       "prediction": {
           "hours": 48,
           "historic_hours": 48
       },
       "pvforecast": {
           "provider": "PVForecastAkkudoktor",
           "provider_settings": {
               "PVForecastImport": null,
               "PVForecastVrm": null
           },
           "planes": [
               {
                   "surface_tilt": 10.0,
                   "surface_azimuth": 180.0,
                   "userhorizon": [
                       10.0,
                       20.0,
                       30.0
                   ],
                   "peakpower": 5.0,
                   "pvtechchoice": "crystSi",
                   "mountingplace": "free",
                   "loss": 14.0,
                   "trackingtype": 0,
                   "optimal_surface_tilt": false,
                   "optimalangles": false,
                   "albedo": null,
                   "module_model": null,
                   "inverter_model": null,
                   "inverter_paco": 6000,
                   "modules_per_string": 20,
                   "strings_per_inverter": 2
               },
               {
                   "surface_tilt": 20.0,
                   "surface_azimuth": 90.0,
                   "userhorizon": [
                       5.0,
                       15.0,
                       25.0
                   ],
                   "peakpower": 3.5,
                   "pvtechchoice": "crystSi",
                   "mountingplace": "free",
                   "loss": 14.0,
                   "trackingtype": 1,
                   "optimal_surface_tilt": false,
                   "optimalangles": false,
                   "albedo": null,
                   "module_model": null,
                   "inverter_model": null,
                   "inverter_paco": 4000,
                   "modules_per_string": 20,
                   "strings_per_inverter": 2
               }
           ],
           "max_planes": 1
       },
       "server": {
           "host": "127.0.0.1",
           "port": 8503,
           "verbose": false,
           "startup_eosdash": true,
           "eosdash_host": "127.0.0.1",
           "eosdash_port": 8504
       },
       "utils": {},
       "weather": {
           "provider": "WeatherImport",
           "provider_settings": {
               "WeatherImport": null
           }
       }
   }
```
<!-- pyml enable line-length -->
