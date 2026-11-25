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
- its settings (collapsed, read only).

The diagnostic graphs and events are only collected while the diagnostic log of the controller is
switched on: set `diagnostic_log_capacity` of the controller (e.g. `1000`) in the Config tab.

While the Control adapter is enabled, a changed setting is applied right away: EOS restarts the
controller with the new settings when a setting is changed in the Config tab or by
`PUT /v1/config/{path}`. When the whole configuration is replaced, imported, reset or reverted,
the new settings are applied at the next energy management run. Until then the Control tab shows
that the configured settings differ from the ones the controller runs with.

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
