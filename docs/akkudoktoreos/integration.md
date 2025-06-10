% SPDX-License-Identifier: Apache-2.0
(integration-page)=

# Integration

EOS operates as a [REST](https://en.wikipedia.org/wiki/REST) [API](https://restfulapi.net/) server,
allowing for seamless integration with a wide range of home automation systems.

## EOSdash

`EOSdash` is a lightweight support dashboard for EOS. It is pre-integrated with EOS. When enabled,
it can be accessed by navigating to [http://localhost:8503](http://localhost:8503) in your browser.

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

- Duetting's [EOS Home Assistant Addon](https://github.com/Duetting/ha_eos_addon) — Additional
  details can be found in this [discussion thread](https://github.com/Akkudoktor-EOS/EOS/discussions/294).

## EOS Connect

[EOS connect](https://github.com/ohAnd/EOS_connect) uses `EOS` for energy management and optimization,
and connects to smart home platforms to monitor, forecast, and control energy flows.
