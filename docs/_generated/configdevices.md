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
| batteries | `EOS_DEVICES__BATTERIES` | `dict[str, akkudoktoreos.devices.settings.batterysettings.BatteriesCommonSettings] | None` | `rw` | `None` | Stationary battery storage devices, keyed by device_id. |
| electric_vehicles | `EOS_DEVICES__ELECTRIC_VEHICLES` | `dict[str, akkudoktoreos.devices.settings.batterysettings.BatteriesCommonSettings] | None` | `rw` | `None` | Electric vehicle battery packs, keyed by device_id. |
| home_appliances | `EOS_DEVICES__HOME_APPLIANCES` | `dict[str, akkudoktoreos.devices.settings.homeappliancesettings.HomeApplianceCommonSettings]` | `rw` | `required` | Shiftable home appliance devices, keyed by device_id. |
| inverters | `EOS_DEVICES__INVERTERS` | `dict[str, akkudoktoreos.devices.settings.invertersettings.InverterCommonSettings] | None` | `rw` | `None` | Inverter devices, keyed by device_id. |
| max_batteries | `EOS_DEVICES__MAX_BATTERIES` | `int | None` | `rw` | `None` | Maximum number of batteries allowed. |
| max_electric_vehicles | `EOS_DEVICES__MAX_ELECTRIC_VEHICLES` | `int | None` | `rw` | `None` | Maximum number of EVs allowed. |
| max_home_appliances | `EOS_DEVICES__MAX_HOME_APPLIANCES` | `int | None` | `rw` | `None` | Maximum number of home appliances allowed. |
| max_inverters | `EOS_DEVICES__MAX_INVERTERS` | `int | None` | `rw` | `None` | Maximum number of inverters allowed. |
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
                   "capacity_wh": 8000,
                   "charging_efficiency": 0.88,
                   "discharging_efficiency": 0.88,
                   "levelized_cost_of_storage_amt_kwh": 0.0,
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
           },
           "max_batteries": 1,
           "electric_vehicles": {
               "ev0": {
                   "device_id": "ev0",
                   "capacity_wh": 60000,
                   "charging_efficiency": 0.88,
                   "discharging_efficiency": 0.88,
                   "levelized_cost_of_storage_amt_kwh": 0.0,
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
                   "cycles_completed_measurement_key": null
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
                   "capacity_wh": 8000,
                   "charging_efficiency": 0.88,
                   "discharging_efficiency": 0.88,
                   "levelized_cost_of_storage_amt_kwh": 0.0,
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
                   "max_soc_percentage": 100,
                   "measurement_key_soc_factor": "bat0-soc-factor",
                   "measurement_key_power_l1_w": "bat0-power-l1-w",
                   "measurement_key_power_l2_w": "bat0-power-l2-w",
                   "measurement_key_power_l3_w": "bat0-power-l3-w",
                   "measurement_key_power_3_phase_sym_w": "bat0-power-3-phase-sym-w",
                   "measurement_keys": [
                       "bat0-soc-factor",
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
                   "capacity_wh": 60000,
                   "charging_efficiency": 0.88,
                   "discharging_efficiency": 0.88,
                   "levelized_cost_of_storage_amt_kwh": 0.0,
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
                   "max_soc_percentage": 100,
                   "measurement_key_soc_factor": "ev0-soc-factor",
                   "measurement_key_power_l1_w": "ev0-power-l1-w",
                   "measurement_key_power_l2_w": "ev0-power-l2-w",
                   "measurement_key_power_l3_w": "ev0-power-l3-w",
                   "measurement_key_power_3_phase_sym_w": "ev0-power-3-phase-sym-w",
                   "measurement_keys": [
                       "ev0-soc-factor",
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
                   "effective_num_cycles": 1,
                   "measurement_keys": []
               }
           },
           "max_home_appliances": 3,
           "measurement_keys": [
               "bat0-soc-factor",
               "bat0-power-l1-w",
               "bat0-power-l2-w",
               "bat0-power-l3-w",
               "bat0-power-3-phase-sym-w",
               "ev0-soc-factor",
               "ev0-power-l1-w",
               "ev0-power-l2-w",
               "ev0-power-l3-w",
               "ev0-power-3-phase-sym-w"
           ]
       }
   }
```
<!-- pyml enable line-length -->
