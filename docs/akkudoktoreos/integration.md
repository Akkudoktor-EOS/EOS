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

### Validate prices before requesting a plan

A flow that starts a price check and optimization in parallel can read the previous check's
result. After an EOS restart this can block a valid new plan, even when the price request
finishes successfully a moment later. Wire the optimization trigger **after** price validation.

The [importable preflight example](../_static/nodered-price-preflight.json) uses only core
Node-RED nodes and EOS's raw price series API. Import the JSON through the editor's
[Import dialog](https://nodered.org/docs/user-guide/editor/workspace/import-export).

1. Move the example nodes into your existing optimizer tab so they share its flow context.
   Set the flow environment variable `EOS_BASE_URL` to your EOS server URL. Run Node-RED
   in the same timezone as EOS. This example requires 15-minute raw price records.
2. Send messages with `topic: "rc_optimize"` from your optimization timer to
   **Price check requests**, removing any direct timer connection to the optimizer.
3. Connect **Validated: build fresh optimize request** to the function that reads current
   SoCs, forecasts and configuration and builds your `POST /v1/optimize` request. The output
   contains only the trigger topic; it does not forward the price HTTP response as a request body.
4. Connect **Reload all forecast inputs** to your existing forecast readers. After a successful
   cold-start prediction update, reload every input and let the next regular timer request a plan.
   Do not connect this refresh output directly to the optimizer.

The example reads `/v1/prediction/series` with `processing=raw`. The default resampled
`/v1/prediction/list` response can interpolate missing numeric values, which would hide gaps.
The check matches exact timestamps for every remaining slot until the next local midnight,
including days with 92 or 100 quarter-hours. Zero and negative prices are valid. Missing past
slots do not block the remaining day. Truncated responses, missing future slots, HTTP failures
and responses crossing midnight do block a new optimization trigger. Background checks with
other topics update coverage without starting optimization.

An empty raw series after a server restart can request `POST /v1/prediction/update` once
per five minutes, provided `flow.rcBusy` is false. Set that flag during your optimization
request if your integration uses it. Partial price gaps do not start this recovery path.
`flow.rcPriceCoverage` records the check; `flow.rcPredictionRecovery` records recovery progress.
Handle HTTP/validation failures in your existing plan-age fallback rather than executing an
old plan as a fresh result.

This check validates the stored raw series for the remaining current day. Raw prices exclude
fees, but providers can also store extrapolated prices beyond the published horizon. Verify
publication coverage against your provider when that distinction matters. The check does not
validate tomorrow's prices, other forecasts, SoC freshness or device commands; keep those checks
in your existing integration. See [the GENETIC upgrade guide](upgrade-genetic.md) for API contracts.

The example does not run on deployment and its link outputs are initially unconnected.
Run its Function-node regression tests with `node --test tests/nodered_price_preflight.cjs`.

## Home Assistant

[Home Assistant](https://www.home-assistant.io/) is an open-source home automation platform that
emphasizes local control and user privacy.

(duetting-solution)=

### Home Assistant Resources

- Duetting's [EOS Home Assistant Addon](https://github.com/Duetting/ha_eos_addon).

## EOS Connect

[EOS connect](https://github.com/ohAnd/EOS_connect) uses `EOS` for energy management and optimization,
and connects to smart home platforms to monitor, forecast, and control energy flows.
