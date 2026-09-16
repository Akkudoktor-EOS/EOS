# GENETIC configuration and manual acceptance

Apply the dependencies in [review handoff](review-handoff.md) before the complete
GENETIC PR. These changes implement the feature branch on current EOS interfaces;
they do not migrate a running Home Assistant installation automatically.

## Configuration

Hardware and schedules belong in the typed `devices` maps, keyed by stable device
IDs. Configure an inverter and link its `battery_id` to the configured stationary
battery, or leave it empty when there is no battery. GENETIC currently supports one
inverter, one stationary battery, one EV and multiple household appliances. Explicit
validation rejects unsupported device counts and inconsistent links.

Example optimization settings (merge into an otherwise complete configuration):

```json
{
  "optimization": {
    "algorithm": "GENETIC",
    "genetic": {
      "interval_sec": 900,
      "horizon_hours": 24,
      "tail_horizon_hours": 48,
      "terminal_value_mode": "AUTO",
      "measurement_max_age_seconds": 300
    }
  }
}
```

Use `interval_sec: 3600` for hourly operation. AUTO evaluates available forecast
continuation; FIXED uses `terminal_value_euro_per_kwh`. Battery wear/LCOS remains a
separate cost. The tail affects scoring, never the emitted control horizon.
`prediction.hours` must cover the control horizon; shorter available tails are
clipped with diagnostics. Missing control forecasts cancel the run.

Old feature settings such as `optimization.interval` and flat terminal-value fields
migrate into `optimization.genetic`; explicit nested settings win. Check the saved
configuration after migration. GENETIC0 retains its separate settings and `/optimize` API.

## Requests and measurements

`POST /v1/optimize` runs GENETIC from configuration. An empty JSON object uses the
configured forecasts and fresh measurements. Runtime input may contain `soc`,
`forecasts`, `start_solution` and `start_solution_datetime`. Hardware overrides in
this body are rejected. For example, with a configured device ID `storage`:

```json
{"soc": {"storage": 42}}
```

Request SoC values are integer percentages. Without an override, the measurement
key `<device_id>-soc-factor` must contain a recent factor from 0 to 1. Invalid,
future or stale measurements never become an assumed zero SoC. Completed appliance
cycles use their configured measurement key, default `<device_id>.cycles_completed`.

Optional forecast arrays begin at local midnight, with one value per configured
slot: `pv_forecast_wh` and `total_load` are Wh per slot; `electricity_price_per_wh`
and `feed_in_tariff_per_wh` are currency per Wh. Provider PV/load power in W is
converted by the resolver. Supplied/imported sale tariffs are never replaced by
purchase prices. Missing optional arrays come from the selected providers.

Successful requests return the native result for that run. A failed run returns an
error and cannot masquerade as a cached success. Generic solutions, plans and reports
are derived from the same stored result. Warmstarts retain their source time so a
later run shifts the previous controls to the new slot origin.

Retrieve the new GENETIC report with
`GET /v1/energy-management/optimization/solution/GENETIC/pdf`. It renders the stored
snapshot on demand; a missing result returns 404. The legacy PDF URL remains unchanged.

## Manual acceptance after merge

1. Back up the running configuration and update all merged EOS packages together.
2. Confirm device IDs, inverter linkage, capacities, efficiencies, import/export
   limits, tariff units and the battery-export setting against your actual hardware.
3. Check fresh SoC measurements and forecast coverage, then run Optimize at 60 and
   15 minutes. Inspect timestamps, slot energies, native/generic results and the PDF.
4. Compare AUTO and FIXED terminal value with identical inputs. Tail diagnostics
   must not appear as extra commands; sale prices must retain their configured signs.
5. Exercise an EV departure and a flexible consumer schedule, including a completed
   cycle. Check permitted windows, required energy and device commands in your setup.
6. Make a measurement stale or omit control forecast coverage and confirm the request
   fails explicitly. Verify the legacy GENETIC0 endpoint if your installation uses it.

Automated checks cover modeled behavior. The final manual test validates the actual
HA entities, provider data and physical installation.
