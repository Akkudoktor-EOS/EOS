# Readings of device controllers are mapped to measurements at the Control adapter

A device controller provides its readings under fixed shared data keys (`meter1:grid_import_kwh`),
and EOS reads measurements under keys whose meaning is fixed elsewhere: a device key such as the
SoC of a battery (`battery1-soc-factor`), or a role list of energy meter readings (load, grid
import, grid export, PV production). The mapping is configured where the meaning of the target is
defined, at `adapter.control`, and references the shared data keys: a list of shared data keys per
role (`grid_import_emr_data_keys`, ...) and a map from device measurement key to shared data key
(`device_measurement_data_keys`). This is the shape of the Home Assistant adapter
(`*_emr_entity_ids`, `device_measurement_entity_ids`), every field is a pick-list, and the
settings of a controller describe only its device. The measurement key of a role reading is
derived from its shared data key (`:` becomes `_`), and the measurement side includes these keys
in its role lists without changing the saved configuration.

## Considered Options

- **A map per controller (`measurement_sources`: measurement key → own key)**: rejected, the
  measurement key had to be typed although it is fixed by a device or derived, and a role is a
  property of the site, not of one device.
- **Add the derived keys to `measurement.*_emr_keys` at runtime, as the Home Assistant adapter
  does**: rejected, it changes the saved configuration by itself and leaves stale keys behind.
