% SPDX-License-Identifier: Apache-2.0
(adapter-page)=

# Adapter

Adapters provide simplyfied integrations for home energy management systems. Besides
the standard REST interface of EOS, the adapters extend EOS by specific integration
interfaces for Home Assistant and NodeRED.

:::{admonition} Warning
:class: warning
Adapter execution is part of the energy management run. The adapters are only working
properly if cyclic energy management runs are configured.
:::

```{toctree}
:maxdepth: 2
:caption: Adapters

adapter/adapterhomeassistant.md
adapter/adapternodered.md

```
