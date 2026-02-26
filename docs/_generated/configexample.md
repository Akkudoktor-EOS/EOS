## Full example Config

<!-- pyml disable line-length -->
```json
   {
       "adapter": {
           "provider": [
               "HomeAssistant"
           ],
           "homeassistant": {
               "config_entity_ids": null,
               "load_emr_entity_ids": null,
               "grid_export_emr_entity_ids": null,
               "grid_import_emr_entity_ids": null,
               "pv_production_emr_entity_ids": null,
               "device_measurement_entity_ids": null,
               "device_instruction_entity_ids": null,
               "solution_entity_ids": null
           },
           "nodered": {
               "host": "127.0.0.1",
               "port": 1880
           }
       },
       "cache": {
           "subpath": "cache",
           "cleanup_interval": 300.0
       },
       "database": {
           "provider": "LMDB",
           "compression_level": 0,
           "initial_load_window_h": 48,
           "keep_duration_h": 48,
           "autosave_interval_sec": 5,
           "compaction_interval_sec": 604800,
           "batch_size": 100
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
                   "charge_rates": [
                       0.0,
                       0.1,
                       0.2,
                       0.3,
                       0.4,
                       0.5,
                       0.6,
                       0.7,
                       0.8,
                       0.9,
                       1.0
                   ],
                   "min_soc_percentage": 0,
                   "max_soc_percentage": 100
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
                   "charge_rates": [
                       0.0,
                       0.1,
                       0.2,
                       0.3,
                       0.4,
                       0.5,
                       0.6,
                       0.7,
                       0.8,
                       0.9,
                       1.0
                   ],
                   "min_soc_percentage": 0,
                   "max_soc_percentage": 100
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
           "version": "0.2.0.dev2602250574650225",
           "data_folder_path": "/home/user/.local/share/net.akkudoktoreos.net",
           "data_output_subpath": "output",
           "latitude": 52.52,
           "longitude": 13.405
       },
       "load": {
           "provider": "LoadAkkudoktor",
           "loadakkudoktor": {
               "loadakkudoktor_year_energy_kwh": null
           },
           "loadvrm": {
               "load_vrm_token": "your-token",
               "load_vrm_idsite": 12345
           },
           "loadimport": {
               "import_file_path": null,
               "import_json": null
           }
       },
       "logging": {
           "console_level": "TRACE",
           "file_level": "TRACE"
       },
       "measurement": {
           "historic_hours": 17520,
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
