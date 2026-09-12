# Control horizon and battery lookahead

The genetic optimizer issues controls only for `optimization.horizon_hours`,
measured from the run start. The default is 24 hours. Battery and EV genomes,
appliance schedules, simulation costs and returned control arrays all stop at
that boundary. A larger prediction horizon does not add control genes.

```json
{
  "optimization": {
    "horizon_hours": 24,
    "tail_horizon_hours": 48,
    "terminal_value_mode": "AUTO"
  },
  "prediction": {"hours": 72}
}
```

The forecast budget is never enforced by rejecting the configuration.
`prediction.hours` also serves callers that do not optimize at all, so a budget
that cannot serve the optimization horizons is reported rather than refused.

A `prediction.hours` below `horizon_hours + tail_horizon_hours` shortens the
tail to `prediction.hours - horizon_hours`, logged once at INFO and reported per
run as `effective_tail_hours`. A `prediction.hours` below `horizon_hours` is
logged as a warning at configuration time and rejected by the optimizer itself
when a run starts, naming the forecast series that ends too early. EOS never
rewrites the settings; raise `prediction.hours` to use the requested horizons in
full.

## Tail value and continuation

In AUTO mode, a deterministic dynamic program evaluates the battery state at
the end of the control horizon against the following forecast intervals. It
uses a 101-point SOC grid and interpolates between states. Each transition uses
the production battery and inverter models, including SOC bounds, power caps,
conversion losses, configured charging/export rates, PV, load, direct-marketing
permission and LCOS on delivered DC energy.

The value curve is computed once per run. Genetic fitness subtracts its
interpolated value from the simulated control cost. The older AC break-even
penalty is disabled in TAIL mode because it does not account for future tail
opportunities. Other feasibility penalties, including EV targets, still apply.

The curve may decrease with SOC: free capacity can earn money at negative
prices. Even an empty battery can have nonzero value. Values include net tail
cash flows and the continuation credit; this constant baseline does not affect
which control plan wins within a run. Tail actions are never returned.

The existing AUTO proxy supplies the continuation value at the **effective tail
end**. Its trailing window is controlled by `terminal_value_window_hours`. A
window with no valuable load or export opportunities conservatively has zero
continuation credit. With `tail_horizon_hours: 0`, AUTO uses the existing proxy
directly at the control end. FIXED preserves the scalar terminal credit directly
at the control end and does not solve a tail.

This is a deterministic approximation on a discretized SOC grid, using the
configured action levels. It does not model forecast uncertainty or schedule
additional EV/appliance activity beyond the control horizon.

## Forecast availability and API indexing

The four required series are load, PV, import price and feed-in tariff. Every
control interval must contain a finite value. Missing control data rejects the
run. The tail stops at the first missing value in any required series, logs a
warning and moves continuation to that point. No missing price becomes zero.
Provider interval values are held within their source interval, never extended
indefinitely beyond the last observed forecast timestamp.

Legacy API forecast arrays still begin at midnight of the run's start day.
They must therefore include the elapsed prefix plus the desired forecast
coverage from now. The prefix is removed before optimization. Declare
`forecast_interval_seconds: 900` for shortened native quarter-hour inputs;
hourly inputs use `3600`. Fully sized native arrays remain auto-detected for
compatibility. Scalar feed-in tariffs represent an explicitly constant tariff.

New solutions set `controls_start_at_now: true`: index zero of every returned
control array and warm-start genome corresponds to the run timestamp. The
generic solution and plan adapters still understand older, midnight-indexed
solutions where this flag is absent. Incompatible old genome lengths are
discarded by warm-start validation.

`terminal_value` reports the mode (`TAIL`, `AUTO`, or `FIXED`), usable remaining
AC energy, control hours, requested/effective tail hours, continuation mode and
any degradation reason. `tail_end_hour` is elapsed hours from the run start.
FIXED mode reports zero effective tail hours.

The credited amount is explicitly decomposed:

```json
{
  "mode": "TAIL",
  "battery_energy_wh": 5230,
  "credited_euro": 1.84,
  "tail_operating_euro": 1.21,
  "continuation_value_euro": 0.63,
  "control_horizon_hours": 24,
  "requested_tail_hours": 48,
  "effective_tail_hours": 48,
  "tail_end_hour": 72,
  "continuation_mode": "AUTO",
  "curve": {
    "energy_wh": [],
    "value_euro": [],
    "operating_value_euro": [],
    "continuation_value_euro": [],
    "marginal_euro_per_kwh": []
  },
  "continuation_curve": {
    "energy_wh": [],
    "value_euro": [],
    "marginal_euro_per_kwh": []
  },
  "tail_diagnostics": {
    "slots": 192,
    "slot_hours": 0.25,
    "soc_grid_points": 101,
    "min_import_price_euro_per_kwh": -0.08,
    "max_import_price_euro_per_kwh": 0.34,
    "min_feed_in_tariff_euro_per_kwh": 0.0,
    "max_feed_in_tariff_euro_per_kwh": 0.29,
    "negative_import_price_slots": 4,
    "positive_battery_export_slots": 160
  }
}
```

`credited_euro` always equals `tail_operating_euro +
continuation_value_euro`. The combined `curve` is the function read by genetic
fitness. It contains the same decomposition at every SOC breakpoint.
`continuation_curve` is the proxy at the effective tail end before the dynamic
program folds the tail backwards onto it. Tail diagnostics summarize the actual
forecast segment; they do not contain executable actions.
