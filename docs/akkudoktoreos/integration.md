% SPDX-License-Identifier: Apache-2.0
(integration-page)=

# Integration

EOS operates as a [REST](https://en.wikipedia.org/wiki/REST) [API](https://restfulapi.net/) server,
allowing for seamless integration with a wide range of home automation systems.

## EOSdash

`EOSdash` is a lightweight support dashboard for EOS. It is pre-integrated with EOS. When enabled,
it can be accessed by navigating to [http://localhost:8503](http://localhost:8503) in your browser.

### Control tab

The Control tab shows the status of one device controller of the control adapter
(`adapter/control/controllers`) at a time; select the controller at the top of the tab:

- its state (running, start failed with the start error, stopped) and connection,
- the command it executes and its readings, updated every 5 seconds,
- its diagnostic graphs, updated every 30 seconds, on one time axis: pan and zoom move all
  graphs; state series are drawn as labelled lanes,
- the last raw events of its diagnostic log (collapsed, loaded when opened),
- its settings (collapsed, read only; "Open in Config tab" shows them in the Config tab).

The diagnostic graphs and events are only collected while the diagnostic log of the controller is
switched on: set `diagnostic_log_capacity` of the controller (e.g. `1000`) in the Config tab.

The Config tab also takes its search from the URL, e.g.
`/eosdash/configuration?search=latitude`; `open=<name>` expands the card of a setting or of a map
entry, e.g. `open=adapter.control.controllers.inverter1`.

### Configure device controllers

The Config tab shows `adapter.control.controllers` with one card per controller and one form per
setting:

- **Add a controller**: enter its device id, select its `controller_type` and fill in the settings
  the type requires (e.g. `serial` of an SMA Energy Meter; `host`, `battery_id` and
  `grid_power_key` of an SMA Sunny Tripower Smart Energy), then click "Add entry". The other
  settings start with their defaults.
- **Change a setting** in the card of the controller. `battery_id` is selected from the configured
  batteries, `grid_power_key` from the shared data keys the configured controllers provide
  (`adapter.control.shared_data_keys`).
- **Write readings to measurements** in `adapter.control`, by picking the shared data keys of the
  controllers (see [ADR 0012](../adr/0012-measurement-mapping-at-the-control-adapter.md)):
  - an energy meter reading into the list of its role, `load_emr_data_keys`,
    `grid_import_emr_data_keys`, `grid_export_emr_data_keys` or `pv_production_emr_data_keys`,
    e.g. `meter1:grid_import_kwh`. It is stored as measurement `meter1_grid_import_kwh` and
    counts like the keys of `measurement.grid_import_emr_keys`;
  - a reading that a device measures into `device_measurement_data_keys`, keyed by the
    measurement key of the device, e.g. `battery1-soc-factor` → `inverter1:bat_soc_factor` for
    the SoC of a battery.

  The readings are written at the start of every energy management run; a reading older than the
  `measurement_max_age_s` of its controller is skipped.
- **Switch an optional group of settings on or off**, e.g. `pv_curtailment` of an SMA Sunny
  Tripower Smart Energy: "Switch on" sets its defaults and shows its settings, "Switch off" (with a
  second click to confirm) removes them. The same switch is used for the other optional groups of
  settings, e.g. `capacity_estimation` of a battery or `time_windows` of a home appliance.
- **Change the type** of a controller: delete it and add it again with the new type, since the
  types have different settings.

While the Control adapter is enabled, a changed setting is applied right away: EOS restarts the
controller with the new settings when a setting is changed in the Config tab or by
`PUT /v1/config/{path}`. When the whole configuration is replaced, imported, reset or reverted,
the new settings are applied at the next energy management run. Until then the Control tab shows
that the configured settings differ from the ones the controller runs with.

The whole map can still be set as JSON in the update field in the collapsed "Advanced (JSON)"
section of `adapter.control.controllers`; it opens by itself when an update of the map failed. The
cards of devices (e.g. `devices.batteries`) and PV planes have the same section, and each entry
shows its JSON value in a collapsed "JSON" section.

## Node-RED

[Node-RED](https://nodered.org/) is a programming tool designed for connecting hardware devices,
APIs, and online services in creative and practical ways.

Andreas Schmitz uses [Node-RED](https://nodered.org/) as part of his home automation setup.

### Node-Red Resources

- [Installation Guide (German)](https://www.youtube.com/playlist?list=PL8_vk9A-s7zLD865Oou6y3EeQLlNtu-Hn)
  \— A detailed guide on integrating EOS with `Node-RED`.

## Home Assistant

[Home Assistant](https://www.home-assistant.io/) is an open-source home automation platform that
emphasizes local control and user privacy.

(duetting-solution)=

### Home Assistant Resources

- Duetting's [EOS Home Assistant Addon](https://github.com/Duetting/ha_eos_addon).

## EOS Connect

[EOS connect](https://github.com/ohAnd/EOS_connect) uses `EOS` for energy management and optimization,
and connects to smart home platforms to monitor, forecast, and control energy flows.
