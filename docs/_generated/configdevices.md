## Configuration for all controllable devices in the simulation

Every device collection is a ``dict[str, <Settings>]`` keyed by
``device_id``.  This makes config paths stable regardless of
declaration order and lets each device settings class build its own
config path from ``self.device_id`` without needing an external index.

<!-- pyml disable line-length -->
:::{table} devices
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| batteries | `EOS_DEVICES__BATTERIES` | `Optional[dict[str, akkudoktoreos.devices.settings.batterysettings.BatteriesCommonSettings]]` | `rw` | `None` | Stationary battery storage devices, keyed by device_id. |
| electric_vehicles | `EOS_DEVICES__ELECTRIC_VEHICLES` | `Optional[dict[str, akkudoktoreos.devices.settings.batterysettings.BatteriesCommonSettings]]` | `rw` | `None` | Electric vehicle battery packs, keyed by device_id. |
| home_appliances | `EOS_DEVICES__HOME_APPLIANCES` | `dict[str, akkudoktoreos.devices.settings.homeappliancesettings.HomeApplianceCommonSettings]` | `rw` | `required` | Shiftable home appliance devices, keyed by device_id. |
| inverters | `EOS_DEVICES__INVERTERS` | `Optional[dict[str, akkudoktoreos.devices.settings.invertersettings.InverterCommonSettings]]` | `rw` | `None` | Inverter devices, keyed by device_id. |
| max_batteries | `EOS_DEVICES__MAX_BATTERIES` | `Optional[int]` | `rw` | `None` | Maximum number of batteries allowed. |
| max_electric_vehicles | `EOS_DEVICES__MAX_ELECTRIC_VEHICLES` | `Optional[int]` | `rw` | `None` | Maximum number of EVs allowed. |
| max_home_appliances | `EOS_DEVICES__MAX_HOME_APPLIANCES` | `Optional[int]` | `rw` | `None` | Maximum number of home appliances allowed. |
| max_inverters | `EOS_DEVICES__MAX_INVERTERS` | `Optional[int]` | `rw` | `None` | Maximum number of inverters allowed. |
| measurement_keys | | `list[str]` | `ro` | `N/A` | All measurement keys across all configured devices. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "devices": {
           "batteries": {
               "bat0": {
                   "device_id": "bat0",
                   "capacity_estimation": null,
                   "capacity_estimate": null,
                   "battery_profile": "generic-lfp",
                   "profile_adjustment": {
                       "nominal_capacity_wh": 8000,
                       "max_charge_power_w": null,
                       "max_discharge_power_w": null,
                       "full_charge_interval_days": null,
                       "full_charge_detect_percentage": null
                   },
                   "levelized_cost_of_storage_amt_kwh": 0.0,
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
                   "max_soc_percentage": 100,
                   "grid_export_rates": [
                       0.25,
                       0.5,
                       0.75,
                       1.0
                   ],
                   "min_soc_deadline_datetime": null,
                   "min_soc_max_duration_h": null,
                   "full_charge_soc_percentage": 100,
                   "full_charge_allow_grid": false,
                   "reserve_soc_percentage": null,
                   "reserve_soc_time_windows": null
               }
           },
           "max_batteries": 1,
           "electric_vehicles": {
               "ev0": {
                   "device_id": "ev0",
                   "capacity_estimation": null,
                   "capacity_estimate": null,
                   "battery_profile": "generic-lfp",
                   "profile_adjustment": {
                       "nominal_capacity_wh": 60000,
                       "max_charge_power_w": null,
                       "max_discharge_power_w": null,
                       "full_charge_interval_days": null,
                       "full_charge_detect_percentage": null
                   },
                   "levelized_cost_of_storage_amt_kwh": 0.0,
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
                   "max_soc_percentage": 100,
                   "grid_export_rates": [
                       0.25,
                       0.5,
                       0.75,
                       1.0
                   ],
                   "min_soc_deadline_datetime": null,
                   "min_soc_max_duration_h": null,
                   "full_charge_soc_percentage": 100,
                   "full_charge_allow_grid": false,
                   "reserve_soc_percentage": null,
                   "reserve_soc_time_windows": null
               }
           },
           "max_electric_vehicles": 1,
           "inverters": {},
           "max_inverters": 1,
           "home_appliances": {
               "dishwasher": {
                   "device_id": "dishwasher",
                   "consumption_wh": 1500,
                   "duration_h": 2,
                   "num_cycles": 1,
                   "cycle_time_windows": null,
                   "min_cycle_gap_h": 0,
                   "cycles_completed_measurement_key": null,
                   "load_profile_power_w": null,
                   "load_profile_interval_seconds": null,
                   "schedule_mode": "ONCE",
                   "time_windows": null,
                   "earliest_start_datetime": null,
                   "deadline_datetime": null,
                   "deadline_policy": "BEST_EFFORT"
               }
           },
           "max_home_appliances": 3
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
       "devices": {
           "batteries": {
               "bat0": {
                   "device_id": "bat0",
                   "capacity_estimation": null,
                   "capacity_estimate": null,
                   "battery_profile": "generic-lfp",
                   "profile_adjustment": {
                       "nominal_capacity_wh": 8000,
                       "max_charge_power_w": null,
                       "max_discharge_power_w": null,
                       "full_charge_interval_days": null,
                       "full_charge_detect_percentage": null
                   },
                   "levelized_cost_of_storage_amt_kwh": 0.0,
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
                   "max_soc_percentage": 100,
                   "grid_export_rates": [
                       0.25,
                       0.5,
                       0.75,
                       1.0
                   ],
                   "min_soc_deadline_datetime": null,
                   "min_soc_max_duration_h": null,
                   "full_charge_soc_percentage": 100,
                   "full_charge_allow_grid": false,
                   "reserve_soc_percentage": null,
                   "reserve_soc_time_windows": null,
                   "effective_nominal_capacity_wh": 8000,
                   "effective_max_charge_power_w": 5000.0,
                   "effective_max_discharge_power_w": 4000.0,
                   "effective_charging_efficiency": 0.955,
                   "effective_discharging_efficiency": 0.96,
                   "effective_min_soc_percentage": 0,
                   "effective_max_soc_percentage": 100,
                   "effective_full_charge_interval_days": null,
                   "effective_full_charge_detect_percentage": 99,
                   "measurement_key_soc_factor": "bat0-soc-factor",
                   "measurement_key_temperature_degc": "bat0-temperature-degc",
                   "measurement_key_charge_power_w": "bat0-charge-power-w",
                   "measurement_key_discharge_power_w": "bat0-discharge-power-w",
                   "measurement_key_power_l1_w": "bat0-power-l1-w",
                   "measurement_key_power_l2_w": "bat0-power-l2-w",
                   "measurement_key_power_l3_w": "bat0-power-l3-w",
                   "measurement_key_power_3_phase_sym_w": "bat0-power-3-phase-sym-w",
                   "measurement_keys": [
                       "bat0-soc-factor",
                       "bat0-temperature-degc",
                       "bat0-charge-power-w",
                       "bat0-discharge-power-w",
                       "bat0-power-l1-w",
                       "bat0-power-l2-w",
                       "bat0-power-l3-w",
                       "bat0-power-3-phase-sym-w"
                   ]
               }
           },
           "max_batteries": 1,
           "electric_vehicles": {
               "ev0": {
                   "device_id": "ev0",
                   "capacity_estimation": null,
                   "capacity_estimate": null,
                   "battery_profile": "generic-lfp",
                   "profile_adjustment": {
                       "nominal_capacity_wh": 60000,
                       "max_charge_power_w": null,
                       "max_discharge_power_w": null,
                       "full_charge_interval_days": null,
                       "full_charge_detect_percentage": null
                   },
                   "levelized_cost_of_storage_amt_kwh": 0.0,
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
                   "max_soc_percentage": 100,
                   "grid_export_rates": [
                       0.25,
                       0.5,
                       0.75,
                       1.0
                   ],
                   "min_soc_deadline_datetime": null,
                   "min_soc_max_duration_h": null,
                   "full_charge_soc_percentage": 100,
                   "full_charge_allow_grid": false,
                   "reserve_soc_percentage": null,
                   "reserve_soc_time_windows": null,
                   "effective_nominal_capacity_wh": 60000,
                   "effective_max_charge_power_w": 5000.0,
                   "effective_max_discharge_power_w": 5000.0,
                   "effective_charging_efficiency": 0.975,
                   "effective_discharging_efficiency": 0.975,
                   "effective_min_soc_percentage": 0,
                   "effective_max_soc_percentage": 100,
                   "effective_full_charge_interval_days": null,
                   "effective_full_charge_detect_percentage": 99,
                   "measurement_key_soc_factor": "ev0-soc-factor",
                   "measurement_key_temperature_degc": "ev0-temperature-degc",
                   "measurement_key_charge_power_w": "ev0-charge-power-w",
                   "measurement_key_discharge_power_w": "ev0-discharge-power-w",
                   "measurement_key_power_l1_w": "ev0-power-l1-w",
                   "measurement_key_power_l2_w": "ev0-power-l2-w",
                   "measurement_key_power_l3_w": "ev0-power-l3-w",
                   "measurement_key_power_3_phase_sym_w": "ev0-power-3-phase-sym-w",
                   "measurement_keys": [
                       "ev0-soc-factor",
                       "ev0-temperature-degc",
                       "ev0-charge-power-w",
                       "ev0-discharge-power-w",
                       "ev0-power-l1-w",
                       "ev0-power-l2-w",
                       "ev0-power-l3-w",
                       "ev0-power-3-phase-sym-w"
                   ]
               }
           },
           "max_electric_vehicles": 1,
           "inverters": {},
           "max_inverters": 1,
           "home_appliances": {
               "dishwasher": {
                   "device_id": "dishwasher",
                   "consumption_wh": 1500,
                   "duration_h": 2,
                   "num_cycles": 1,
                   "cycle_time_windows": null,
                   "min_cycle_gap_h": 0,
                   "cycles_completed_measurement_key": null,
                   "load_profile_power_w": null,
                   "load_profile_interval_seconds": null,
                   "schedule_mode": "ONCE",
                   "time_windows": null,
                   "earliest_start_datetime": null,
                   "deadline_datetime": null,
                   "deadline_policy": "BEST_EFFORT",
                   "effective_num_cycles": 1,
                   "measurement_keys": [
                       "dishwasher.cycles_completed"
                   ]
               }
           },
           "max_home_appliances": 3,
           "measurement_keys": [
               "bat0-soc-factor",
               "bat0-temperature-degc",
               "bat0-charge-power-w",
               "bat0-discharge-power-w",
               "bat0-power-l1-w",
               "bat0-power-l2-w",
               "bat0-power-l3-w",
               "bat0-power-3-phase-sym-w",
               "ev0-soc-factor",
               "ev0-temperature-degc",
               "ev0-charge-power-w",
               "ev0-discharge-power-w",
               "ev0-power-l1-w",
               "ev0-power-l2-w",
               "ev0-power-l3-w",
               "ev0-power-3-phase-sym-w",
               "dishwasher.cycles_completed"
           ]
       }
   }
```
<!-- pyml enable line-length -->
