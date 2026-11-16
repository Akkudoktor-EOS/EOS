% SPDX-License-Identifier: Apache-2.0
(thermal-configuration-page)=

# Optional Thermal Planning

EOS provides optional thermal configuration, native measurement and presence integration,
room-model estimation, and a single-zone shadow planner. It compares a temperature-following
reference with joint heat/battery/EV optimization using the existing electrical FLEET
contracts. Heat-pump selection supports 26 catalog profiles and custom performance data.

The result is a conditional model proposal. It does not change operational battery plans,
room targets, device registration or appliance commands. There is no thermal actuator adapter.

## Optional Air and Water Distribution

The default distribution is direct heat input into the existing building model.
An explicitly configured `air` or `water` distribution adds physical energy-storage
states between the heat pump and the room. These paths require a separately calibrated
`ThermalBuildingRC2Parameters` model and the heat-pump planning mode. An effective
electrical RC2 gain cannot be reused as a thermal gain or multiplied by COP again.

The additional state temperatures use existing native `MeasurementSample` records.
Configure each bound channel as `quantity=temperature`, `unit=degC`; unknown,
estimated, invalid or stale sensor readings are not silently replaced with warm defaults.

| Distribution state | Zone `distribution_measurements` binding | Meaning |
| --- | --- | --- |
| Air near the unit | `air_temperature_measurement_key` | The calibrated near-unit air node; not an inferred occupied-room temperature |
| Water loop | `water_temperature_measurement_key` | The calibrated mixed-water node; not automatically a supply or return thermometer |
| Metal or floor slab | `emitter_temperature_measurement_key` | Optional measured emitter state, otherwise an explicitly enabled bounded observer |
| Optional buffer | `buffer_temperature_measurement_key` | Required fresh buffer temperature when a buffer state is configured |

For underfloor heating, a slab thermometer is optional. The explicit
`distribution_observer.maximum_emitter_uncertainty_c` setting permits a causal
observer using past room and water temperatures and the declared emitter capacity
and conductances. Its initial interval comes from the configured emitter-temperature
bounds. The observer must have sufficient continuous history and reduce the remaining
interval width below the configured limit. Data gaps restart the full initial interval;
missing time is not compressed. A valid bound emitter thermometer takes precedence.
This interval covers only initial-state uncertainty under the declared parameters,
not sensor errors or parameter uncertainty. It is not a heat or slab-temperature measurement.

Room, mass and distribution states are projected together to the forecast origin.
The complete state vector is used for equal-end-state comparisons; stored heat in a
water loop, slab or buffer cannot be consumed as an unpaid terminal benefit.
Unsupported heat-pump rating coordinates or missing distribution calibration/state
produce an explicit unsupported response. W35-only capacity data do not become a
general high-temperature water-heating map merely by configuring a water distribution.

An optional native bundle `distribution_fan_levels` map associates zone IDs with one
declared fan-level ID per forecast interval. Missing schedules use the configured
distribution default. This selects a known model conductance; it does not optimize or
send fan commands. No new flow measurement unit or assumed pump consumption is introduced.

If an explicitly known external distribution pump/fan consumes additional electricity,
the native bundle must acknowledge its zone in `distribution_load_excluded_zone_ids`.
This asserts that the supplied household residual has already had that entire external
heating auxiliary load removed. Without the acknowledgement a positive additional
distribution load produces an unsupported response, rather than being double counted.
An auxiliary already inside the heat-pump COP boundary is not added to the bus again.
Unknown circulation power remains a source-quality limitation; it is not a measured zero.

The native resolver can use `heat_pump.delivery_temperature_measurement_key` as a fresh,
explicit supply/rating-temperature channel. It never silently substitutes the modeled
water-return proxy or slab temperature for that sensor. Missing or unsupported rating
coordinates block the source-heat estimate. During short initialization projection,
all temperatures share the same physical transition; current inferred heat and measured
outdoor temperature are explicitly held, and the configured default circulation profile
is assumed until the forecast origin. The returned source ages and state/observer flags
make those assumptions visible. Declared source-flow/supply limits and calibrated
temperature bounds also apply to that projection.

## Modes and Isolation

Existing configurations require no changes. The default is:

```json
{
  "thermal": {
    "mode": "off",
    "zones": {}
  }
}
```

`shadow` permits the explicit thermal APIs for enabled zones:

| Interface | Purpose |
| --- | --- |
| `/v1/optimize/thermal/shadow` | Explicit effective-electrical RC2 scenario |
| `/v1/optimize/thermal/heatpump/shadow` | Explicit thermal building and heat-pump scenario |
| `/v1/thermal/data_bundle` | Native data admission (`PUT`) in the separate shadow app |
| `/v1/thermal/plan` | Configuration-based native planning (`POST`) |
| `/v1/thermal/status` | Native context readiness and last proposal (`GET`) |

Ordinary EMS optimization and configuration-based FLEET/GENETIC resolution continue to
produce their existing electrical plans; they do not invoke the thermal solver.
The generic APIs do not start a collector, refresh external forecasts, schedule runs or
publish an operational plan. A caller supplies source data and explicitly requests a shadow run.

`control` is reserved. An ordinary optimization run in this mode raises an explicit
unsupported-feature error **before** data acquisition, cache clearing or plan publication.
Configuration-based FLEET/GENETIC resolution also rejects it. Prediction-only runs remain
available. A stored mode or an enabled source never authorizes thermal execution.

The explicit `/v1/optimize/fleet` interface remains an electrical, side-effect-free planner.
It does not yet accept thermal fields. Thermal planning DTOs must not be added to its
request and then silently ignored by the existing solver.

## Staging Zones and Heat Sources

The following complete fragment is an example for preparing a zone while keeping the
feature disabled. Its values are illustrative, not a heat-pump rating or a calibrated model.

```json
{
  "thermal": {
    "mode": "off",
    "plan_ttl_seconds": 1800,
    "planning_time_limit_seconds": 60,
    "absence_delay_seconds": 1800,
    "measurement_max_age_seconds": 180,
    "presence_max_age_seconds": 90,
    "zones": {
      "room1": {
        "enabled": false,
        "room_temperature_measurement_key": "room1-temperature",
        "outdoor_temperature_measurement_key": "outdoor-temperature",
        "presence_measurement_key": "room1-presence",
        "model_reference": "room-model-v1",
        "comfort": {
          "min_temperature_c": 19.5,
          "target_temperature_c": 20.0,
          "max_temperature_c": 20.5
        },
        "unoccupied_comfort": {
          "min_temperature_c": 16.0,
          "target_temperature_c": 17.0,
          "max_temperature_c": 20.5
        },
        "allow_warm_coasting": true
      }
    }
  },
  "devices": {
    "heat_sources": {
      "heater1": {
        "enabled": false,
        "kind": "electric_room",
        "max_electric_power_w": 1000,
        "min_electric_power_w": 0,
        "electric_power_measurement_key": "heater1-power",
        "zone_ids": ["room1"]
      }
    }
  }
}
```

Map keys populate omitted `zone_id` and `device_id` values. Explicit IDs must match
their keys; heat-source IDs must not collide with existing electrical device IDs.
Every source binding must reference a configured thermal zone.

Zones and sources are individually disabled by default. Declaring a zone enabled requires
an explicit room-temperature key, comfort band and model reference. An enabled source
requires at least one zone. Neither setting overrides the global `off` mode or implements
thermal scheduling. The native shadow context resolves these bindings against typed EOS
measurement channels; they are not added to the operational device registry.

Power belongs to the source rather than the zone. Configuration can stage several zone
bindings, but the current native planner requires exactly one zone per source and rejects
shared-source/multi-zone requests. All configured source power values describe electrical
input in watts. The active minimum may not exceed the positive maximum. Model references
are identifiers, never Python imports or model-file paths. Optional `model_parameters`
provide a typed calibrated model; otherwise the native context can fit one from history.

## Timing and Comfort Contracts

Timing settings are positive numbers of seconds. The explicit API enforces issue/observation
freshness and the configured plan lease. Presence filtering is provided separately by
`PresenceHistory`; thermal device control is not implemented. Missing presence is not absence.

The configuration stores temperature preferences and source bindings, not fixed comfort
times. Runtime timing comes from presence forecasts and temporary manual corrections.
Temperatures are finite continuous Celsius values with minimum ≤ target ≤ maximum;
there is no half-degree rounding in these contracts. Appliance-specific quantization
belongs in a future device adapter.

Explicit scenario bands are hard requirements throughout each slot, including shared
boundaries. A continuous room temperature cannot move instantly from an absence maximum
of 20 °C to an occupied minimum of 20.5 °C. Such an incompatible adjacent transition is
reported with its slot/time and bounds; the planner does not remove the boundary check
or silently replace the occupied band. Intersecting bands alone do not prove that a
transition is reachable with the available heating power and thermal state.

For presence-derived policies, the existing model-derived heating lead opens a declared
warm-up period before expected occupancy. The occupied and unknown-presence slots retain
their exact occupied requirements. The warm-up period preserves the absence minimum;
it does not promise that the model can reach the next required temperature in time.

`zone.allow_warm_coasting` defaults to `false`. Explicitly enabling it retains the
absence minimum and target while allowing the absence maximum to include occupied-range
warmth. This permits passive cooling after departure and an initially absent but still
warm room, instead of requiring instantaneous cooling. It is an explicit change to the
absence preference, not a relaxation of occupied comfort. Alternatively, configure the
absence maximum to permit that warmth directly. Native diagnostics list the original
and effective absence bands, the heating lead and the coasting declaration.

The independent DTOs in `akkudoktoreos.thermal.thermalmodels` describe:

- `ThermalRC2Parameters`: effective room/mass rates with time in hours and electrical
  gain per kWh. Runtime power is supplied in watts and converted internally. These
  parameters do not identify physical heat capacity or COP separately.
- `ThermalBuildingRC2Parameters`: a separate model taking delivered **thermal** energy.
  It can be combined with a heat-pump map; the electrical model above cannot simply
  be multiplied by another COP.
- `ThermalObservation`: timestamped observations with at least one room, outdoor or power
  channel. Supplied channels share that observation time. Channels with different source
  timestamps require separate observations; a power-only sample of 0 W is valid.
- `ThermalState`: an estimate and its causal observation boundary; latent mass is not a
  measured sensor.
- `ThermalComfortBand` and `TemperatureInterval`: room bounds and targets, never IR commands.
- `ThermalPlan`: zone identity, plan identity, revisions, an issue time, a positive validity
  lease, and ordered contiguous intervals. All timestamps are timezone-aware and normalized
  to UTC. A multi-day horizon may extend beyond the shorter execution lease.

Constructing or deserializing a plan does not authorize execution. The native context
already checks manual Off and concurrent revisions when computing a proposal. Execution-time
priority, fallback, joint battery/heat publication and actual device control are separate,
unimplemented stages.

## Explicit Shadow Optimization

`ThermalShadowRequest` supplies an immutable scenario with a native `FleetRequest`,
effective RC2 parameters, a causal room/mass state, outdoor temperatures, comfort bands
and electrical heating limits. It supports one zone and one heat source, at most 288
aligned 15-minute intervals. Arrays must cover the same horizon. The state describes
the prospective interval start and identifies the latest underlying observation.

The caller must explicitly declare `load_basis="residual_excluding_heat"`.
`fleet.load_w` contains household demand **excluding** this controlled heater and native
EV loads. The solver adds its own heating schedule. Adding heat to a forecast that already
contains that heat would double-count it. Input prices, PV forecasts, storage state,
efficiencies, limits, targets, tariffs and terminal values come from the supplied FLEET
request; this API does not retrieve or repair them.

The reference first minimizes target-tracking deviation, then electrical heating energy,
and uses native FLEET to optimize its batteries and EVs. The candidate optimizes heating
and storage together. Both use the same comfort bands and optional UTC-aligned power
blocks (30 minutes by default). Comfort is constrained at one-minute sample points.
This reference is a simulated temperature-oriented heating schedule, not continuous
compressor operation and not a recording of an existing MPC. Any night or absence
setback supplied in the comfort bands already applies to both strategies. The cost
difference measures the additional joint heat/electricity planning benefit over that
reference with its batteries already optimized. Savings from adding presence setback
to an otherwise constant-temperature baseline require a separate, explicitly labelled
comparison; they are not included automatically in this difference.
The candidate must match the reference's complete final room/mass state and each
storage endpoint. Its mean room temperature per distinct comfort band must be at least
the reference's. Thus additional savings cannot come simply from leaving the house or
battery colder/emptier at the horizon end. One-minute checks are model checks, not a
continuous physical comfort guarantee.

The objective is electricity cost including native storage throughput costs and terminal
values. The response distinguishes this objective from `FleetSolution.grid_cost_eur`, the
net import/export cash cost. `savings_cash_eur` never substitutes a terminal inventory credit
for money saved on the grid bill. Lower electricity consumption is not required. A savings value is published only
when reference/candidate feasibility and the absolute solver-gap budget qualify the
comparison. All savings remain conditional model results, never measured bill savings.
No absolute COP, measured start loss, fan benefit or manufacturer performance map is
inferred from the effective RC2 electrical gain. The legacy electrical-gain interface
has constant efficiency. The separate heat-pump interface below uses explicit source
maps with the thermal building model. Defrost event dynamics and validation of appliance
tracking remain future work.

Without explicit recovery, an initially cold room returns `infeasible`. An optional
`ThermalRecoverySettings` enables a common maximum-safe heating prefix for both variants.
Its power respects source limits and every modeled minute's upper comfort bound. Recovery
ends at a feasible comfort boundary and has a configured maximum duration. The native
context requests up to four hours; the explicit scenario APIs leave recovery disabled by default.

The full prefix electricity is charged in both variants. `ThermalRecoveryReport` exposes
the common power/state trajectory, boundary, cold minutes and cold degree-hours rather
than calling that period comfortable. `power_input_kind` distinguishes electrical and
thermal prefix power. The requested plan bands remain visible. Unreachable recovery or
initial overheating is not silently excused, and native thermostat tracking is not simulated.

An explicit `allow_passive_cooldown=true` recovery policy also handles a room above
the initial upper bound after lowering its target. The native context enables this
when a manual `target_temperature_c` is supplied; standalone scenarios default to
disabled. Heat input is fixed to zero identically in both schedules until normal
minute-wise comfort is reachable. Residual heat in air, water and emitter stores is
simulated. The lower comfort limit, distribution limits and later tighter upper
limits remain hard. If passive cooldown would undershoot the lower limit on the
15-minute control grid, or cannot finish within the recovery deadline, no plan is
published. There is no active cooling or tariff-driven heating during this prefix.

The report identifies `direction=passive_cooldown`, hot minutes, hot degree-hours
and maximum excess; these are not counted as achieved comfort. Published temperature
bands retain the requested bounds. During either recovery direction, the published
target is the requested target, rather than the initially cold or hot trajectory.
Raising the target uses the existing maximum-safe heating prefix, not a price-driven
delay. Physical warm-up still takes time; the model cannot promise instant comfort.

For an initially absent room, the native recovery allowance is additionally capped at
the first forecast occupied or unknown-presence slot. A cold recovery prefix cannot
extend past that boundary and disguise a missed return-comfort requirement. An
unreachable return remains infeasible. Initial occupied/unknown presence retains the
explicit cold-start recovery contract; it is still reported as recovery, not comfort.

Invalid/stale scenarios, a disabled zone, or a busy solver do not replace an existing plan.
Each response states `shadow_only=true` and `execution_allowed=false`.

Known minimum modulation, start costs and discrete charging add integer variables.
`planning_time_limit_seconds` is a finite positive total planning budget, at most 3600
seconds and 60 seconds by default. The native setting is
`thermal.planning_time_limit_seconds`; explicit scenarios use the request field of the
same name. The budget starts at entry to the public optimization function, including
source-horizon preparation, reference planning, native electrical planning, joint planning
and result verification. It does not include earlier JSON validation, data admission or
native history/model resolution.

`fleet.solver_time_limit_seconds` remains a per-stage cap. Each solver call receives at
most the remaining total budget. Numerical retries and fixed-integer cleanup share their
stage and total remainder; they do not start fresh budgets. The global MIP lower bound
remains part of comparison qualification. Diagnostics expose stage outcomes and elapsed
time, including budget exhaustion. A numerical retry retains the same physical program;
it does not remove comfort, source-flow, storage or equal-inventory constraints.

Joint planning starts in its existing sparse representation. If a numerical rejection
conflicts with a fully verified reference witness, a budgeted retry may substitute the
explicitly registered thermal recurrences and known initial temperatures. Each removed
temperature variable is an affine function of the heating inputs and fixed exogenous
terms. All of its bounds become linear constraints; minute comfort/flow limits, full
thermal terminal matching, mean comfort and all free integer decisions remain. The
complete original state vector and objective/bound constant are reconstructed before
the original-model and independent rollout checks. This is an equivalent numerical
representation, not a change to the physical model or its acceptance tolerance.
Diagnostics report each attempt's representation and any switch reason, plus the number
of substituted states and recurrence rows. The retry shares the original stage/global
deadline. It does not replace an ordinary feasible incumbent or an ordinary time limit
merely to try a different algorithm. Reference tracking/energy and existing two-state
direct/legacy paths retain their original representation.
Water-source flow is additionally checked in absolute watts after the reference and
candidate rollouts. The kelvin-normalized row residual alone is insufficient for this
check: its numerical value changes with flow conductance. Both diagnostics remain
visible, and a plan that fails the absolute physical check is rejected.
The joint rollout checks run inside the stage before a solver result is accepted, not
only after it has been returned. A numerical physical residual may trigger a bounded
condensed continuous cleanup with the same rounded integer decisions. All continuous
values are solved afresh using the real cost objective, then checked against the full
original matrix and rollout again. `checks.cleanup_physical_refinement_used` identifies
an accepted repair; it remains feasible/unqualified without savings or comparison
certificates. Failed attempts remain diagnostic history. A fallback reference must pass
the same physical validator, and no cleanup or fallback receives a fresh time budget.

The remaining budget is initially shared over the remaining four stages: reference
tracking, reference heat energy, native electrical reference and joint planning. Unused
time carries forward. If the first tracking search reaches its time limit without any
incumbent, it may borrow additional remaining time once. Its absolute per-stage cap still
starts at the original stage entry; borrowing never resets that timer or the global
deadline. A declared downstream reserve remains for the later stages and verification.
A stage that already has a feasible incumbent does not borrow simply to prove optimality.
The diagnostic trace exposes the initial and extended allowance and reserve. Borrowing
does not guarantee that every difficult horizon will yield a plan or use every second
of the total budget.

Before extending a tracking search with no incumbent, a bounded fixed-pattern LP may
try to establish a feasible reference. The pattern fixes only a complete set of integer
operating decisions; suggested powers and temperatures are not trusted. All continuous
values are solved afresh against the full hard model and checked in the original units,
including the minute rollout and absolute source-flow watts. A successful pattern LP
reports its actual tracking objective, `feasible`, and
`checks.reference_tracking_fixed_pattern_rescue=true`. Its branch LP bound is not a
global mixed-integer certificate. Savings and comparison certificates remain unset even
if subsequent joint planning finds a valid candidate. An unsuccessful attempt consumes
time from the same stage and proceeds to the existing adaptive/MIP path; it does not
invent a reference or lower a comfort requirement.

A previously verified reference witness may be reused when its secondary energy solve
cannot finish. A fully verified reference schedule may also be returned as an explicitly
labelled reference fallback if joint optimization produces no usable candidate. Such a
fallback remains `feasible`, is not a completed economic optimization and has no qualified
savings value. Diagnostics distinguish the source of the candidate and reuse of a
reference witness. Fallback never excuses invalid physics, contradictory comfort bands,
missing source data, expired deadlines or unpaid terminal inventory.

`checks.candidate_plan_origin` distinguishes `joint_optimization`,
`verified_reference_fallback` and `none`. `checks.reference_energy_witness_reused`
separately reports a reused witness in the reference's secondary energy stage.
Even a genuine joint incumbent does not make the comparison qualified when a required
reference optimum or solver-gap certificate is missing.

Reference tracking reserves part of its own stage budget for a zero-objective feasibility
search if tracking produces no usable incumbent. That search uses the complete hard
thermal program. Its actual temperature-tracking objective is recomputed; zero is not
reported as the attained tracking optimum. `checks.reference_tracking_feasibility_rescue`
identifies this nonoptimal reference, and financial qualification remains disabled.

If continuous cleanup fails the original-unit feasibility check, a budgeted refinement
may solve the original matrix with the same fixed integer decisions and tighter numerical
settings. The physical acceptance tolerance remains unchanged. The flag
`checks.cleanup_original_refinement_used` exposes this path; its prior mixed-integer bound
is retained only as a diagnostic, not a certificate for qualified savings. A rejected
numerical candidate may be replaced only by a fully verified original-matrix reference
witness, reported by `checks.candidate_numerical_reference_fallback`. Neither this fallback
nor a feasibility rescue can make a genuinely unreachable cold-start scenario feasible.

This is a cooperative deadline, not a process hard kill. Solver setup and Python/SciPy
work can overrun the requested duration. The planner checks the deadline after these
operations and rejects an overdue proposal instead of publishing it as a valid plan.
A time limit without a verified schedule produces no executable proposal or invented
savings. This budget does not establish that every 24/48-hour problem can be solved.

## Isolated App on the EOS Host

`akkudoktoreos.server.thermalshadow:create_app` is a Uvicorn factory for running the shadow
endpoint without the main EOS app, EMS startup or background jobs. Run it only on the
EOS host. It defaults to `off`; `EOS_THERMAL_SHADOW_CONFIG` can point to a separate JSON
file containing a `thermal` section. The operational EOS configuration is not read.
Bind to loopback when an on-host collector calls it. `/health` identifies the shadow-only
service and its mode. The app does not start a collector or write measurements itself.

### Native Data and Configuration Workflow

`akkudoktoreos.server.thermalshadow:create_native_app` provides the configuration-based
workflow using `ThermalRuntimeContext`, native `Measurement` and native `Prediction`.
It requires explicit, separate absolute `EOS_CONFIG_DIR` and `EOS_DATA_DIR` locations and
an existing reviewed `EOS.config.json`. Cache, output and database storage must remain
inside that data location. Database autosave and compaction intervals must be `null`;
the context saves admitted data explicitly. Initialization does not start EMS, adapters,
the main-server lifespan or forecast background jobs.

The workflow is implemented as follows:

1. Configure `thermal.mode="shadow"`, an enabled zone/source pair, typed measurement
   channels and `weather.provider="WeatherImport"`. Current native bindings require room
   and outdoor `temperature`/`degC` channels and source `power`/`W`. Convert other meter
   units before admission; a raw kW value must not be presented as watts.
2. `PUT /v1/thermal/data_bundle` admits a `ThermalDataBundle`. It contains a native
   `FleetRequest`, `load_basis="residual_excluding_heat"`, `forecast_issued_at`, `sampled_at`,
   aligned native `WeatherDataRecord` entries and up to 10,000 original `MeasurementSample`
   entries per call. Quality and original observation times are preserved. Samples enter
   native Measurement storage; weather enters `WeatherImport`. No external source is fetched.
3. `POST /v1/thermal/plan` takes `ThermalConfigRequest` runtime intent, for example:

   ```json
   {
     "zone_id": "room1",
     "heat_source_id": "heater1",
     "basis_control_revision": 7,
     "mode": "auto",
     "master_power": "On"
   }
   ```

   `auto` selects the source's configured electrical or heat-pump model mode. An explicit
   mode still requires matching model input units. Optional room-target changes shift
   the occupied comfort band; a runtime power cap can tighten the configured source limit.
   `presence_correction` carries optional temporary `PresenceCorrection` intent.
4. The context resolves the model, causal state, presence-derived comfort bands, source
   limits and admitted electrical forecasts, then calls the joint shadow solver. It
   automatically supplies the common cold-recovery policy described above.
5. `GET /v1/thermal/status` reports data generation, input timestamps and the last proposal.
   It performs no collection or optimization. The response is not an execution interface.

Data admission requires a recent sampling boundary. Resolution requires a prospective
quarter-hour origin within 15 minutes, forecasts issued within 30 minutes and fresh source
measurements under `thermal.measurement_max_age_seconds`. The bundle's optional
`control_revision` is compared with `basis_control_revision`; producers should supply it
when manual control state is known. A revision omitted by a source cannot be verified.
`master_power="Off"` returns no heating proposal and supersedes an in-flight computation.
The context also rechecks input generation, configuration, revision, origin and lease
after solving; superseded results cannot publish qualified savings.

The caller remains responsible for honest forecast provenance and source freshness.
Re-reading an old forecast does not create a new issue time, and a projected battery
boundary is not a newly measured SoC. The native APIs do not infer either distinction
from household measurements or silently repair an incorrectly labelled bundle.

### Room Model and Initial State

`zone.model_parameters` can provide either typed room model explicitly. If it is absent,
the native context can fit positive bounded RC2 parameters from room/outdoor/source history
and cache the result for that zone/source configuration. Configuration or profile changes
change the cache identity. This is not continuous online model adaptation.

Fitting needs at least 24 hours on a preserved five-minute grid, sufficient covered
heating variation, and covered training/validation windows. The final chronological third
is reserved for 30-minute known-input validation; admission currently requires mean
absolute room-temperature error no greater than 0.75 K. Future **measured** input power is
used in this component validation, so passing it does not validate an ex-ante power forecast,
appliance tracking, seasonal performance or monetary savings.

The state observer filters latent building mass from past measured room temperatures.
It requires long, sufficiently covered history and fresh room/outdoor/power observations,
rejects long gaps, and projects to the prospective origin using an explicitly reported
short input hold. Defaults request 48 hours of history and at least 12 hours for mass
initialization. Mass remains estimated. A configured negative idle-noise tolerance may
floor small negative power for calculation while retaining raw observations.

Two optional zone settings adjust the start of a plan:

* `zones.<id>.state_room_observation_window_seconds` (default `null`, 300 to 3600 s) replaces
  the single newest raw room value by the time-weighted mean of the measured room values in
  the window ending at that value. Use it for sensors that report in coarse steps or bursts,
  where the raw value jumps by several tenths of a kelvin without the room doing so. The
  projection to the plan start then begins in the middle of the window so the mean does not
  lag. With fewer than three measured values in the window, window coverage below 80 %, or
  missing power/outdoor inputs for the longer projection, the raw value is used and the
  state diagnostics name the reason (`room_state_smoothing_fallback`). Freshness checks stay
  on the newest raw value. Not available with an air/water distribution state model.
* `zones.<id>.recovery_max_heating_rate_kelvin_per_hour` (default `null`, above 0 up to
  20 K/h): when the room starts below the comfort minimum, the initial recovery aims at a
  rise of at most this rate instead of heating at full available power immediately, which
  lets the plan reach the minimum with cheaper or calmer operation. If the minimum could not
  be reached in time at that rate, the rate is raised as little as needed, so the option can
  never make a plan unsolvable. The recovery report states the requested and the used rate
  (`requested_heating_rate_kelvin_per_hour`, `heating_rate_kelvin_per_hour`) and whether it
  was raised (`heating_rate_raised`). `null` keeps the immediate-target recovery.

Heat-pump-mode calibration/state estimation derives thermal input from supported source
maps and measured electricity with declared auxiliary boundaries. Unsupported or ambiguous
conversion remains a gap or blocks resolution; it does not become zero heat. This is an
estimated heat input, not an independent heat-meter measurement. Historical delivery
conditions derived using a current heating curve/target are also model assumptions.

## Native Presence Provider

`presence.provider` defaults to `null`. Selecting `PresenceHistory` enables a native EOS
`PredictionProvider` using `MeasurementSample` history, sample quality and the normal EOS
database-backed prediction sequence. Areas are arbitrary identifiers rather than a
hard-coded living room. This configuration example does not enable thermal control:

```json
{
  "measurement": {
    "channels": {
      "area1-presence": {"quantity": "presence", "unit": "1"},
      "area1-temperature": {"quantity": "temperature", "unit": "degC"}
    }
  },
  "presence": {
    "provider": "PresenceHistory",
    "zones": {"area1": {"measurement_key": "area1-presence"}},
    "absence_delay_seconds": 1800
  }
}
```

History is grouped into local quarter-hours, using matching weekdays or pooled workday/
weekend dates when individual weekdays lack support. Recent dates receive more weight.
Each date contributes at most one independent vote, including the repeated DST hour.
Only measured 0/1 samples count. Coverage is integrated with a bounded sample lease;
outages, nulls, invalid samples and unobserved days never become absence examples.

Current presence raises the near-term forecast immediately. Continuous valid absence
requires the configured delay. Corrections decay over a bounded near-term horizon;
they do not pretend to predict an entire day from a single current sensor state.
Insufficient history or an unhealthy current sensor preserves comfort. An approximate
Wilson upper bound with recency-weighted effective days provides a conservative gate;
it is not a calibrated guarantee for correlated household behavior.

An exact weekday is selected only when it has the configured raw support and its
effective support could put the Wilson upper bound below the absence threshold even
with zero occupied examples. Otherwise the provider pools workdays or weekends, with
one vote per local date. Crossing seven raw Mondays therefore cannot silently replace
a usable workday pool with an exact-weekday population too small for an absence decision.
Qualified occupied observations for the exact weekday additionally guard the upper bound,
including before seven such weekdays exist; other absent weekdays cannot average that
known occupancy away. Those observations remain a subset of the pool, not extra votes.
This guard can deliberately keep mixed weekday patterns conservative.

`PresenceEstimate` exposes `support_population`, `effective_support_days`,
`weekday_support_days`, `weekday_effective_support_days`, `weekday_probability`,
`weekday_probability_upper` and `weekday_presence_guard`. The historical point estimate
describes the selected population; the separately exposed weekday guard may increase
the conservative upper bound. Sensor freshness, manual priorities and absence thresholds
are unchanged. The native forecast DTO includes these diagnostics.

The thermal context uses the longer of the presence-provider and thermal absence delays,
and the shorter of their sample-freshness limits. This can make thermal comfort more
conservative than the separately published provider forecast. Defaults require seven
supporting local dates and use up to 56 days of history; a new sensor therefore cannot
immediately justify learned absence setbacks.

Normal EOS prediction keys are `presence_probability_<area>`, `presence_baseline_<area>`,
`presence_upper_<area>`, `presence_comfort_<area>` and `presence_support_days_<area>`.
`PresenceCorrection` provides temporary `comfort` or `away` intent with expiry. Fresh
detected presence overrides an away correction. These runtime methods do not create a
dashboard button, persist a correction across restart, or change an appliance themselves.

`comfort_bands_from_presence` combines these estimates with occupied/absent temperature
preferences and a model-derived warm-up lead. It allows warming during the lead and
requires occupied comfort at the actual occupancy boundary. An unexpected return cannot
guarantee instantaneous warmth after a substantial setback. Forecast accuracy, return
misses and comfort violations must be validated before live setback is enabled.

### Reporting absences

A learned presence pattern cannot know about a holiday or a business trip. A *reported
absence* tells the planner that nobody is home from a start time until an end time; it takes
precedence over the learned pattern. Absences can come from three places, which are merged:

* `presence.absences` in the configuration (up to 32 entries),
* `absences` in a `POST /v1/thermal/plan` request (up to 16 entries, for that run only),
* absences stored at run time through the REST interface below.

Each entry has `start`, an optional `end`, an optional `zone_ids` list and an optional
`label` (at most 80 characters). `start`/`end` are ISO date-times. A value with an offset
(`+01:00`, `Z`) is exact; a value without one is read in the installation's time zone
(`general.timezone`). `end` must be after `start`, and both must either carry an offset or
neither. Without `end` the absence lasts until revoked: the plan then assumes absence for its
whole horizon. `zone_ids` restricts an entry to the listed presence areas; an empty list
means all areas.

```json
{
  "measurement": {
    "channels": {
      "area1-presence": {"quantity": "presence", "unit": "1"}
    }
  },
  "presence": {
    "provider": "PresenceHistory",
    "zones": {"area1": {"measurement_key": "area1-presence"}},
    "absences": [
      {"start": "2026-12-20T08:00:00", "end": "2026-12-28T18:00:00", "label": "holiday"}
    ],
    "absence_return_hold_minutes": 120
  }
}
```

**Effect on comfort and preheating.** For every 15-minute slot that lies completely inside a
reported absence the estimate has the source `reported_absence` and comfort is no longer
required. A fresh presence detection still wins (`sensor_present`): someone at home right now
is not overridden. The slot containing the reported return and the following
`presence.absence_return_hold_minutes` require comfort (source `reported_return`), so the
planner warms the room up beforehand with its normal preheating and recovery logic; a return
does not need to be reported separately. Reported absence and return slots keep their
presence-derived band even where a routine `comfort_windows` entry would otherwise apply.
Without any reported absence the behaviour is unchanged.

**Lowest temperature during a reported absence.** The band for these slots is chosen in this
order:

1. `zone.absence_minimum_temperature_c`, if set (5 to 25 °C). Its minimum is always
   respected. The target and maximum then follow `unoccupied_comfort` if that is configured,
   otherwise the target is the minimum plus 0.3 K and the maximum is the occupied target.
2. otherwise the zone's `unoccupied_comfort` band, if configured,
3. otherwise 14 °C as damp/frost protection.

The minimum never exceeds the occupied minimum. `absence_minimum_temperature_c` affects only
reported absences, not learned setbacks; learned reductions still need an
`unoccupied_comfort` band, otherwise they keep the occupied band. Raise the value for
buildings that need a warmer holiday floor, lower it to save more heat on a long trip.

**Settings**

| Setting | Default | Limits | Meaning |
| --- | --- | --- | --- |
| `presence.absences` | `[]` | up to 32 entries | Reported absences from configuration. |
| `presence.absence_return_hold_minutes` | `120` | 0 to 720 | Comfort is required for this long after a reported absence ends, and in the slot of the return. `0` requires comfort only from the return slot. |
| `zones.<id>.absence_minimum_temperature_c` | `null` | 5 to 25 °C | Lowest room temperature during a reported absence, see above. |

**Learning.** Quarter-hours that overlap a reported absence are left out of the presence
learning, so a week-long trip does not teach the forecast that the same weekday is usually
empty. To end an absence early, keep the entry and set `end` to the real return time (or use
the interface below, which does exactly that); deleting it from the configuration would make
the empty days count as learnable again for as long as they lie inside `history_days`.

**REST interface.** The routes belong to the native thermal service
(`akkudoktoreos.server.thermalshadow`, prefix `/v1/thermal`). Absences set this way are kept
in `presence_absences.json` in the native data folder, survive a restart and are merged with
the two other sources at each plan run. In a process without the store the routes answer
`503`. At most 64 entries are stored; entries that ended longer than `history_days` plus
two days ago are dropped. An unreadable file is moved aside as `*.damaged-<timestamp>` and
the store starts empty.

| Route | Purpose |
| --- | --- |
| `GET /v1/thermal/absences` | Read only: `now`, `timezone`, the running entry `active`, the next one `next` and all `absences` (times in the installation's time zone). |
| `POST /v1/thermal/absences` | Report an absence. `start` defaults to now, no `end` means until revoked. The response contains `created` (the new id) plus the summary. |
| `PUT /v1/thermal/absences/{id}` | Create or replace the entry with this id; idempotent for scripts that keep their own id. |
| `DELETE /v1/thermal/absences/{id}` | Revoke one entry. A running absence ends now and is kept (for learning); an entry that has not started, or was reported less than a minute ago, is removed. `404` for an unknown id. |
| `POST /v1/thermal/absences/back` | Back home: end every running absence now. The response lists the ended ids in `ended`. |

Invalid bodies are rejected with `422`. Examples with made-up values:

```bash
# Away from now until further notice
curl -X POST http://localhost:8504/v1/thermal/absences \
  -H "Content-Type: application/json" -d '{"label": "trip"}'

# Planned holiday; the id is chosen by the caller
curl -X PUT http://localhost:8504/v1/thermal/absences/holiday-2026 \
  -H "Content-Type: application/json" \
  -d '{"start": "2026-12-20T08:00:00", "end": "2026-12-28T18:00:00", "zone_ids": ["area1"]}'

# Back earlier than planned
curl -X POST http://localhost:8504/v1/thermal/absences/back
```

The host and port depend on how the native service is started. Reported absences are
shadow-planning input; like the rest of the native interface they do not command a device.

## Heat-Pump Module and Selectable Maps

`devices.heatpump` simulates air-to-water or air-to-air performance separately from the
building. `HeatPumpProfile` stores explicit COP and thermal minimum/maximum maps, their
rating temperatures and provenance. Water delivery means flow temperature; air delivery
means indoor rating temperature, never a W35 value applied to a split appliance.

The same interface also supports `brine_to_water`, `water_to_water`, `brine_to_air`
and `water_to_air`. These types require an explicit source inlet temperature;
outdoor weather still drives building losses and is never substituted for brine
or groundwater temperature. Existing map coordinate names
`outdoor_temperature_c` denote the source axis for these profiles for JSON
compatibility. Select a suitable `custom_profile` when no catalog preset exists.
Air frost corrections are rejected for water and brine sources.

Native configuration accepts `heat_pump.source_temperature_measurement_key` for
the current and historical inlet temperature. Supply an aligned forecast through
`ThermalDataBundle.source_temperature_forecasts[heat_source_id]`, or declare a
constant source assumption with `heat_pump.source_temperature_c`. The direct
planning request exposes the corresponding `source_temperature_c` tuple.
The room model must use `heat_gain_kelvin_per_kwh`; an electrical-input gain
cannot be relabelled without calibration or an explicitly justified conversion.

An optional `devices.heat_sources[id].thermal_power_measurement_key` supplies
delivered heat directly for state estimation and calibration. Bind a `power/W`
channel with an explicit integration method and gap limit. Set
`thermal_power_is_estimated=true` for derived air-side values; they are never
reported as a heat-meter measurement. For an estimated channel only, the optional
`thermal_power_idle_electric_threshold_w` suppresses apparent heat from warm
sensors when source electricity is below the declared idle threshold. Real heat
meters do not use this gating by default. Without a thermal channel the existing
supported-map inversion of measured electricity remains available.

Heat-pump results expose `candidate_thermal_power_w`, `candidate_cop` and
`candidate_source_electric_power_w` separately. COP is null when not heating.
It refers to the map's declared electrical boundary; additional electrical
consumers remain itemized in `candidate_electricity`. The historical
`candidate_heat_power_w` field retains its electrical-base meaning for existing
consumers and should not be interpreted as thermal output.

Paired maps can opt into `operating_point_interpolation="linear_common_heat"`.
Electrical input is interpolated at identical thermal loads across the enclosing
source/delivery slices. Only their common load range is supported; there is no
temperature or load extrapolation. The default `exact` retains the original
strict behavior. Interpolated points carry a quality flag. A learned or approximate
map must retain its provenance and must not claim heat-meter accuracy for an
airflow-based heat estimate. Existing catalog profiles are not changed implicitly.

```json
{
  "devices": {
    "heat_sources": {
      "wp1": {
        "enabled": false,
        "kind": "heat_pump",
        "max_electric_power_w": 3000,
        "heat_pump": {
          "preset": "wolf_cha07",
          "delivery_temperature_c": 35,
          "defrost": {"mode": "off"}
        }
      }
    }
  }
}
```

The preset enum contains **26 selectable profiles**: 25 datasets from the supplied annual
HTML tool and the separately sourced Panasonic Heatcharge VZ12 profile `panasonic_vz12ske`.
Use `list_heat_pump_presets()` or the configuration schema for exact identifiers.

| Catalog group | Preset identifiers |
| --- | --- |
| Aira | `aira_6`, `aira_8`, `aira_12` |
| Alpha | `alpha_08`, `alpha_11` |
| Lambda | `lambda_10L`, `lambda_13L`, `lambda_15L`, `lambda_20L`, `lambda_35L` |
| LG | `lg_12` |
| Mitsubishi | `mitsubishi_13_10` |
| Panasonic water profiles | `pana_l5`, `pana_l7`, `pana_l9` |
| Panasonic air profile | `panasonic_vz12ske` |
| Stiebel | `stiebel_07_2`, `stiebel_10_2` |
| Vaillant | `vaillant_35`, `vaillant_55`, `vaillant_75`, `vaillant_105` |
| Viessmann | `viessmann_250A` |
| Weishaupt | `weishaupt_11` |
| Wolf | `wolf_cha07`, `wolf_cha10` |

The HTML datasets are marked `unverified_html`, not independently manufacturer-verified.
Seven of those 25 datasets have no minimum-capacity data; the gap remains explicit. Their
capacity curves are treated as W35 based on the tool's usage and record that assumption;
they do not establish capacity at other flow temperatures. A caller may instead provide
`custom_profile` with documented or measured data. Exactly one catalog/custom source is required.

Interpolation is deterministic between supported source points. Diagnostic values may
be clamped outside support, but the planner rejects those conditions. Unlike the HTML
tool, the module does not fabricate missing W55 COP from other devices, infer minimum
power as 25 percent of maximum, or impose a universal 8 percent W55 capacity reduction.
The HTML's random curve fitting and generic cycling penalty are not imported.

The Panasonic profile records manufacturer/Eurovent sources and paired thermal/electrical
operating points. Independently listed heat-output and electrical-input extrema are not
paired to invent minimum/maximum COP. EN14825 part-load A/B/C/D points at different outdoor
temperatures are not treated as one same-weather modulation curve. The declared operating
temperature envelope also excludes colder laboratory-only points.

For profiles with paired `operating_points`, the planner uses separate thermal and
electrical variables and adjacent piecewise-linear segments. It cannot mix non-adjacent
points to obtain a cheaper fictitious COP. This first implementation requires an exact
supported outdoor/delivery slice for every interval; it does not borrow a +7 C curve at
another temperature. A single-point slice supports only that ON operating point. The lowest
documented pair is not automatically physical minimum modulation. Missing coverage can
therefore block a forecast run or leave only a restricted conditional comparison.

The optional `html_heuristic` frost calculation uses native `weather_temp_air`,
`weather_relative_humidity` and `weather_dew_point` (or a Magnus-derived dew point),
estimated evaporator temperature and load fraction. It is a static frost/COP estimate,
not an identified defrost event or ice-storage model. Defrost-inclusive COP is never
penalized twice. Unknown test basis or missing weather/modulation data remains visible;
unknown basis requires an explicit opt-in before a correction is attempted.

Paired load-axis maps are not given an additional heuristic frost correction unless their
COP already includes defrost, in which case no second penalty is applied. Otherwise that
combination is explicitly unsupported. There is no dynamic icing/defrost cycle simulation.

`WaterHeatingCurve` optionally computes bounded water-flow temperatures from outdoor air
and a declared reference room target/correction gain. It does not command an appliance,
apply to an air-to-air unit, identify an emitter model or optimize the flow curve itself.

`ThermalHeatPlanningRequest` consumes a native `FleetRequest`, aligned native
`WeatherDataRecord` objects, a separate thermal building model, a heat-pump profile and
runtime comfort bands. `optimize_heat_pump_shadow` jointly solves heat electricity,
battery and EV operation and reports both electrical and delivered-thermal power.
For profiles without paired load-axis data, slot COP is held at an explicit assumed thermal
load fraction. For paired profiles, the joint solver chooses within the supported adjacent
Q/P segments. Neither path identifies missing performance data or simulates subinterval
compressor control. Physical building identification cannot be inferred from room temperature alone.

### Source Electricity and Comparison Quality

`HeatPumpElectricalSettings` separates optional continuous `standby_power_w`, ON-only
`running_auxiliary_power_w`, and `startup_energy_wh` per OFF-to-ON transition. `null` means
unknown, not a measured zero. Positive additions require `extras_are_outside_cop=true` so
that components already inside a COP's electrical boundary are not counted twice.

These components enter the same grid/battery power balance as base source electricity.
Start energy is divided by the quarter-hour duration for that slot's mean electrical load;
it is not an arbitrary monetary penalty and is not added as useful heat. Exact transition
variables prevent fictitious additional starts even when electricity prices are negative.
This is not a measured transient peak or subinterval cycling model. A startup heat loss can be modelled
as electricity with `startup_warmup_seconds`, see below.

`startup_warmup_seconds` (optional, 0 to 900 s, default `null`) describes the start loss as a
warm-up time: per OFF-to-ON transition the compressor draws the electric power of the start
slot for this long while the heat output is still ramping up. The extra electricity is
`startup_warmup_seconds / 3600` times that slot's electrical power and is added to
`startup_energy_wh`; with `null` or `0` nothing changes. Like the other additions it requires
`extras_are_outside_cop=true`. A configured warm-up time counts as a modelled start loss: the
`unmodelled_electricity:startup_energy_wh` flag is not raised, an unknown initial ON state
blocks savings qualification, and the usable electrical maximum per slot is reduced
accordingly (it is divided by `1 + startup_warmup_seconds / 900`). It is also rejected for a
conversion from an electrical snapshot to heat, because a start cannot be separated from a
snapshot. Set it from a measured start (time until full heat output), not from a guess.

A heat-pump map without `thermal_min_points` has no power floor: EOS then assumes the unit can
modulate down to the smallest map point, while real units below that only run cycled, so the
plan may contain operating states the unit cannot do. In this case a warning is logged once
per profile and service start and the result diagnostics contain `source_minimum_power_warning`;
savings are not qualified. Enter the smallest operating point per temperature slice as
`thermal_min_points` (same heat as the lowest operating point) to remove the warning.

The configured electrical maximum bounds total source input including declared additions.
The active steady minimum is converted to the COP input boundary by subtracting known
standby/running auxiliaries, then combined with supported minimum modulation. Startup
energy cannot compensate for that continuous-power floor. A segment-based floor is
inverted only inside its documented P(Q) range, never extrapolated.

The result exposes total `reference_source_electric_power_w` and
`candidate_source_electric_power_w`, base `*_heat_power_w`, delivered `*_thermal_power_w`
and auditable `*_electricity` components. Electrical and thermal watts must not be interchanged.

Even a numerically feasible plan can remain unqualified. The generic source comparison
requires a documented total-system COP boundary, or an explicitly completed compressor-only
boundary with known additional components. Unknown minimum modulation, an unsupported paired
minimum, unknown components needed to interpret a configured active floor, or unknown initial
ON state with positive start energy block savings qualification. No pump/fan/start defaults
are invented. The native context does not turn a wattage threshold into confirmed compressor state.
Conditional plan/cost arrays can remain available while `comparison_qualified=false` and
both savings fields are `null`. Catalog provenance and approximation flags remain visible.

## Data Needed and Current Boundaries

| Input | Existing EOS structure | Purpose |
| --- | --- | --- |
| Room temperature with time/quality | `MeasurementSample`, `temperature`/`degC` | State and calibration |
| Presence 0/1 with time/quality | `MeasurementSample`, `presence`/`1` channel | History forecast and local correction |
| Source electrical power in W | `MeasurementSample`, `power`/`W` | State, calibration and validation |
| Outdoor temperature, humidity, dew point | `WeatherDataRecord` | Heat loss, map conditions and frost estimate |
| Import/export prices, PV, residual load | Existing prediction data and `FleetRequest` | Joint electricity cost |
| Battery/EV state and constraints | `FleetRequest` | Native limits, efficiencies and terminal values |
| Source maps and electrical limits | `HeatPumpProfile`, source settings | Feasible heat/electric power |
| Room model and state | Thermal model, room history | Losses, warm-up and recovery |
| Optional delivered-heat data | Explicit heat/flow channels | Additional calibration evidence |

Configuration-based resolution, native data admission, optional calibration/state estimation,
presence-derived comfort, source evaluation and coupled electrical planning form an explicit
native shadow workflow. Source collectors, run scheduling, dashboards and installation-specific
logging are separate integrations; enabling the configuration does not create them.

The current scope remains one room/mass RC2 zone and one heat source. It does **not** provide:

- A multi-zone hydraulic/emitter or room-air stratification/fan model.
- Dynamic ice accumulation, identified defrost events or the Panasonic Heatcharge heat-storage transient.
- A domestic-hot-water tank, stratified thermal store or simultaneous parallel/backup-heater optimization.
- A complete annual or seasonal simulation/report, measured seasonal COP or a validated savings guarantee.
- Continuous online building/source correction, thermostat/PID simulation, device transport or actuator output.

`annual_heat_loss_prior` can derive a sizing prior from declared delivered space heat and a
complete representative weather year. It requires domestic hot water/fuel efficiency to be
separated beforehand; it is not an annual operating-cost or equipment-payback report.

Use measured comfort, electrical energy, starts and manual-override history to validate
forecasts before considering any control adapter. Weather, residual-load separation,
model mismatch and other heat sources remain material uncertainty. The APIs report conditional
model economics; they do not establish real household bill or heating-season savings.
No installation addresses, credentials, measurement dumps or one-house profile belong in these generic modules.


## Isolated planning and control availability

For an enabled continuous tracker bound to the planned zone, native planning
intersects its comfort bands with the same execution limits used by tracking.
`thermal.tracking.maximum_preheat_temperature_c` is an absolute ceiling for
automatic preheating (default30°C); `max_increase_c` additionally limits the
increase above the manual target. An explicitly higher manual target takes
priority over the preheat ceiling. For example, ceiling23°C and increase3K
permit a20.5°C manual target to preheat up to23°C. The device adapter must admit
the same limits; otherwise the projected trajectory is not executable.

The direct-room state observer accepts a separate thermal-input history when
available. Observed inputs are integrated causally to the observation clock;
only the remaining future lead holds the latest input. Diagnostics distinguish
measured-input time from `projection_unobserved_seconds`. Integrations should
schedule their normal solve close enough to the next boundary to limit this
uncertainty while preserving their solver and dispatch margins.

`thermal.electric_power_block_seconds` controls an optional hard equality of
electrical power within wall-clock blocks. The compatibility default is 1800 s;
`null` removes that equality and lets the source map determine electricity in
each quarter-hour. This is not a compressor minimum run/off time or IR thermostat
setting. A physical heat-pump curve may need changing electric power even when
the requested room conditions are steady.

Presence forecasts are also refreshed during native data ingestion, independently
of heating being enabled or a solver succeeding. The native status/data response
exposes `presence_forecasts` per area and `presence_issued_at`; the same
`PresenceHistory` provider publishes ordinary EOS prediction records. A single
covered historical day can supply a provisional daily pattern. The separate
`minimum_support_days` and conservative upper bound still govern comfort reduction.
`history_is_provisional` and `support_population` distinguish weak evidence and a
cross-day-type fallback from an established weekday pattern. Missing coverage
remains unknown. Manual heating choices must not be relabelled as occupancy data.

Native thermal planning and both explicit shadow routes run the numerical solver in
one disposable process per admitted request. Only the existing typed request and
result cross the boundary as JSON. The child does not start an EOS server or device
adapter. The native event loop remains available for status, data and manual-off
requests. Manual off cancels the child; a superseded request cannot replace that off
state, including when off arrives during input resolution.

`thermal.planning_time_limit_seconds` remains the cooperative total planning budget
(default 60 seconds). `thermal.solver_process_grace_seconds` adds a bounded startup
and transport allowance (default 5 seconds, maximum 30). The parent terminates the
worker after that wall-clock deadline, on cancellation or excessive sampled memory
use. Crash, invalid output and start failure return an explicit no-plan result.
Cleanup/reaping follows termination; a result arriving after the deadline is rejected.

`thermal.solver_process_memory_limit_mb` defaults to 1024 MiB (range 128–8192).
The process tree's RSS is sampled every 50 ms. Linux additionally applies address-space
and CPU-time resource limits, a separate process group and lower scheduling priority.
Windows uses sampled RSS and a lower scheduling priority; it is not a kernel-enforced
Windows memory quota. Linux and Windows workers are restricted to one available CPU.
These limits apply to the solver, not to input resolution, source collectors, the
parent EOS process or all concurrently running services combined.

`thermal.pattern_polish_seconds` (default `0`, range 0 to 60 s) reserves part of the joint
stage for an exact local improvement of the heat source's on/off pattern, for example removing
short pauses or bursts that the mixed-integer search leaves behind. `0` disables it and keeps
the previous behaviour. The time is taken from the joint search, so it is not meant to extend
the total duration; it still has to fit inside `thermal.planning_time_limit_seconds`. When it
is enabled, the result's checks report `pattern_polish_evaluations`,
`pattern_polish_gain_eur`, `pattern_polish_start_objective_eur` and
`pattern_polish_seconds_used`. A plan is only changed if the verified model cost gets lower;
the setting does not enter the request fingerprint. Enable it (for example 10 to 20 s) when
plans show many short heat-pump runs and the planning budget has room; leave it at `0` on
slow hardware or with a tight budget.

The first thermal reference receives 55% of the remaining time instead of 25%.
Reference energy refinement receives 25% of its then-remaining time and can reuse a
verified witness. The native battery reference and joint optimization keep sharing
one absolute deadline, with a final verification reserve. At horizons of at least
96 quarter-hour slots, the initial mixed-integer reference prioritizes feasibility
before energy/cost refinement. For horizons of at least 192 slots with additional distribution/storage states,
the reference first tries shared on/off decisions within two-slot blocks. Heating
power and every physical constraint remain on the original quarter-hour grid;
comfort transitions and the fixed recovery prefix are not tied across blocks.
An unsuccessful restricted search retries the unrestricted model within the same
remaining total budget. The joint cost optimizer is always unrestricted.
Its original continuous model, integer constraints,
minute comfort, source limits and distribution physics are still verified. A pure
feasibility witness supplies no tracking optimality bound and cannot certify savings.

The result's `checks` expose process outcome, elapsed wall time, sampled peak RSS and
exit code. `feasible` is a verified model plan, not proof of optimality, real comfort
or household savings. `limit` with no plan is a safe rejection, not a successful plan.
A deployment must retain independent local comfort/lease handling during computation;
a solver process alone cannot fix a collector that stops renewing its control lease.
The installation-specific collector is maintained separately and is not installed
by enabling these settings.
