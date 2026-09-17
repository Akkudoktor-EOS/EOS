# Upgrading to the new energy planner

This guide covers the unreleased GENETIC and configuration update. It is intended for
existing installations, especially Home Assistant, Node-RED and custom API integrations.
It describes the intended combined update, not a new capability of the last tagged release.

## What changes for you?

EOS can plan energy use in 15-minute or hourly steps. The new GENETIC planner can reuse a
previous plan with its timestamps, charge an EV before a deadline, schedule flexible
household appliances and optionally export stored battery energy to the grid. Battery
export requires explicit opt-in; configuring export rates alone does not enable it.

The planner can also look beyond the period it will control, so it does not treat the last
stored kWh as worthless merely because the plan ends. This remaining-energy value is
separate from battery wear costs. Only the configured control period produces commands.

New measurement APIs report energy, household balance, coverage and quality. Missing
measurements are not automatically zero consumption. Battery-capacity estimates help
assess the battery but do not automatically replace its configured capacity. The
Akkudoktor PV provider offers an optional local model with calibration; local modelling
still needs weather data, and the existing remote backend remains the default.

## Where can existing integrations break?

### 1. Device IDs replace list positions

Device collections now use stable names. For example, `devices.batteries.0` becomes
`devices.batteries.storage` when that battery's ID is `storage`. Use the actual ID in
your migrated configuration; do not assume the example name. An explicit `device_id`
must match its map key, and the inverter must refer to the correct battery ID.

Supported old device lists migrate automatically. External configuration paths in
automations and scripts do not. Check those paths and the saved configuration after
migration. Battery wear cost is named `levelized_cost_of_storage_amt_kwh`.

The new GENETIC currently supports one inverter, one stationary battery, one EV and
multiple household appliances. It rejects unsupported counts or inconsistent links.

### 2. The two optimizer endpoints have different contracts

| Integration | What to use |
| --- | --- |
| Existing legacy optimization request | `POST /optimize`, which continues to run GENETIC0 |
| New configuration-driven GENETIC request | `POST /v1/optimize` |
| Automatic optimization | Explicitly select `optimization.algorithm` and its settings |

Keep algorithm settings under `optimization.genetic` or `optimization.genetic0`.
The default algorithm is GENETIC. If your automatic integration still requires the
legacy algorithm, select GENETIC0 explicitly; the legacy `/optimize` endpoint already does so.
For the new GENETIC, `interval_sec` is `900` or `3600`. Supported flat settings from
the old feature branch migrate into the GENETIC section; explicit nested settings win.

The new request accepts `soc`, `forecasts`, `start_solution` and
`start_solution_datetime`. Hardware, device limits and schedules belong in configuration.
An empty JSON object uses configured providers and fresh measurements. Hardware overrides
in the body and query-string overrides are rejected. Do not migrate by changing only the URL.

The legacy endpoint remains available, but the surrounding configuration has changed.
That is not a promise that every old script works unchanged.

### 3. Fresh measurements and complete forecasts are required

Request SoC values are integer percentages from 0 to 100, keyed by device ID. Automatic
measurement lookup instead reads a factor from 0 to 1 using the configured measurement
key, by default `<device_id>-soc-factor`. For example, 50% is `50` in the request and
`0.5` in that measurement channel.

Without a request override, a missing, future, invalid or stale SoC cancels the run.
The default freshness limit is 300 seconds, controlled by
`optimization.genetic.measurement_max_age_seconds`. Check the measurement update rate.

Missing forecasts within the control period also stop the run. A shorter forecast
continuation is allowed and reported. Failed calls return an error; they no longer look
like a successful new run by returning an older result. Existing automations should
handle errors and verify the timestamp of any separately retrieved stored plan.

### 4. Check units, timestamps and result consumers

- Runtime forecast arrays start at local midnight and use the configured slot interval.
  Energy is **Wh per slot**, not W or kWh. A constant 1,000 W load uses 250 Wh in 15 minutes.
- Runtime purchase and sale prices are **currency per Wh**: 0.30 EUR/kWh is 0.00030 EUR/Wh.
  Provider power forecasts are converted by EOS; do not convert them a second time.
- Read returned timestamps and interval information instead of assuming 24 hourly values
  or a fixed number of slots on daylight-saving transition days. Automatic GENETIC runs
  use the site's timezone derived from its configured coordinates.
- Pass the previous plan's timestamp with a warmstart so it can be aligned to the new run.
  Recheck custom parsers of GENETIC results; its native result and report are not the
  legacy GENETIC0 response format.

The new report endpoint is
`GET /v1/energy-management/optimization/solution/GENETIC/pdf`. It renders the stored
GENETIC result and returns 404 if none exists. The legacy PDF endpoint remains separate.

### 5. Costs and schedules may change even with the same forecasts

Imported sale prices remain authoritative, including constant, zero and negative values.
They are not replaced by purchase prices. Corrected slot power limits, inverter losses
and battery wear costs can therefore produce different schedules and financial totals.
The planner's AUTO/FIXED remaining-energy valuation is separate from those wear costs.

For upgrades from older releases, also check the electricity-fee framework. The old
`elecprice.charges_kwh` and `elecprice.vat_rate` fields are removed during migration,
not automatically translated into a complete fee setup. Configure the selected
`elecfee` provider and compare the resulting purchase/sale prices with your contract.
For the fixed provider, consumption fees use `consumption_amt_kwh` and
`consumption_percent_amt` under `elecfee.elecfeefixed`; the latter is a percentage,
for example `19` for 19%, not the old multiplier `1.19`. These fields use time windows.

### 6. Configuration updates now take effect consistently

Explicit runtime updates take priority over environment and configuration-file values.
Command-line settings still have higher priority. Environment values remain effective
for keys not overridden at runtime. If an integration relied on an environment value
silently overriding a later API update, its behaviour changes. Verify persistence after
a restart according to your configured save mode.

Custom Python integrations must also follow the current asynchronous measurement and
storage interfaces: await those methods. HTTP clients continue to exchange JSON.

## A short upgrade check

1. Back up configuration and stored measurement/database data, and record the currently
   installed version or image. Keep the backup for rollback; migrated data/configuration
   should not be assumed to work with an older executable.
2. Inspect the migrated device IDs, inverter/battery links, algorithm settings and fees.
   Update external paths and request bodies where needed.
3. Check fresh battery state and provider data. Run 60- and 15-minute GENETIC plans and
   compare units, timestamps, expected energy flows and costs before applying commands.
4. Try an EV deadline and a household-appliance window if you use them. Check the stored
   result, execution plan and PDF agree. Test the legacy endpoint if you still rely on it.
5. Test one missing-forecast or stale-measurement case. Confirm your automation handles
   the error and does not execute an old plan as a new one.

Automated tests cover the model and API contracts. They do not replace checking your
actual Home Assistant entities, Node-RED flows, forecast sources and connected devices.
