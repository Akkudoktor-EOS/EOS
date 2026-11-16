# Optional room-temperature tracking

`thermal.tracking.operation` selects `pilot` (the default described below) or
`continuous`. Both remain disabled until `thermal.tracking.enabled=true` and a
downstream controller explicitly enables the corresponding operation. Continuous
operation has no overall 90-minute deadline; every individual proposal still
expires after at most the configured short lease, the current interval or the
original source plan validity, whichever comes first.

In continuous operation `minimum_temperature_c` (default 18 °C) is the setback
floor. Present or unhealthy/unknown current occupancy protects the manual goal
minus `occupied_tolerance_c` (default 0.3 K). A fresh sensor must continuously
confirm absence for `absence_delay_seconds` before deeper lowering is allowed.
This current safety decision is independent of an uncertain historical forecast.
The plan can grant `heating_allowed=false` only when its current electrical heat
schedule is zero and the measured room remains above the active floor and target
with a small margin. A colder room restores heating permission. This is a request
to the existing local controller, not an IR command or measured appliance state.

Continuous operation retains manual Off/direct-mode priority, revision checks,
quiet-mode restrictions and source freshness. On restart a deployment may restore
operator preferences, but must never restore a previously issued execution lease.
The room-policy flags do not authorize electrical execution. A deployment that
also consumes the optimizer's joint battery result must validate that result in
the existing electrical controller and match it to the current room-plan identity
and acknowledged local-controller generation. A manual change, expired plan or
controller fallback must revoke joint selection and restore its normal electrical
planning path. Predicted power remains a model estimate; real bill savings need
measurement even after successful integration.

Runtime `ThermalConfigRequest.comfort_windows` carries ordered, non-overlapping
quarter-hour comfort requirements. It leaves uncovered slots to the native
presence provider. `comfort_windows_policy="presence_fallback"` uses a declared
routine only for unknown presence slots, preserving learned/observed presence and
manual corrections. The default `override` treats supplied windows as explicit
user requirements. An initial cold-recovery exception cannot waive a later rising
minimum, such as a declared morning deadline. These requirements do not prescribe
the heating start; the thermal optimizer determines the necessary preheating.

## Bounded pilot compatibility

The tracking pilot is a separate, disabled-by-default interface. It does not
enable full thermal optimization, publish a battery plan or communicate with an
actuator. Both `shadow_only=true` and `execution_allowed=false` remain unchanged.
The additional `pilot_tracking_allowed` permission concerns only a short-lived
room target passed to an existing local controller.

`thermal.tracking.enabled` defaults to `false`. Enabling the policy requires an
explicit `zone_id`. Target changes are limited to at most 0.5 K above or below
the manual baseline. Occupied, stale or unknown presence always prevents a
reduction. A reduction additionally requires fresh, continuously verified
absence and `require_comfort=false` for the configured delay. A cold room below
the manual target by more than 0.25 K, or an active recovery request, uses the
existing controller at its manual goal. A measured room at or above manual plus
0.5 K also stops extra prewarming. The existing controller's own hysteresis and
delays remain; these guards do not guarantee a realized temperature ceiling.

The pure `ThermalTrackingPolicy` accepts typed operator, room, presence and latest
source-attempt snapshots. It produces a proposal with the plan identity, source
issue time, manual revision, current interval and an independent lease. The
default lease is 150 seconds and the configuration limit is 300 seconds. Lease
end is also bounded by the current interval, original plan lease and separately
authorized pilot expiry. An old source response cannot acquire a new issue time.
The room input must be a fresh measurement with its original observation time,
not a predicted room state or a new timestamp assigned to a cached value.

A successful plan for a future interval is cached as pending. A still-valid
current plan may continue until the pending plan starts, but permission is
recomputed using current observations on every evaluation. A failed, stale,
superseded or physically unverified latest source attempt clears both snapshots.
No previous success is substituted for a failed computation.

An explicitly initialized isolated application can expose
`POST /v1/thermal/tracking/proposal`. This endpoint requires injected settings,
one persistent policy and a serialization lock. It performs no data acquisition,
optimization, EMS publication or controller command. Its source adapter must
retain original timestamps and attest the source's physical checks. This is a
trusted local integration seam, not authentication or an independent physics
proof.

The downstream adapter must independently check the current manual revision,
master Off, direct mode, quiet fan cap, current interval, lease and pilot expiry.
Manual edits revoke the pilot lease. A denied proposal, transport failure or
expired lease means return to the retained manual goal in the existing MPC;
it never means automatic On. The manual display value must remain separate from
the temporary effective target.

The model's interval target is an optimized mean room temperature, not a proven
native controller action. This pilot tests room-curve tracking and fallback.
It does not establish the predicted electrical schedule, joint battery savings,
comfort-equivalent savings or seasonal performance. No language model runs in
the control loop.

### Continuous presence evidence and short manual absence

`ThermalTrackingPresence.absent_since` is optional evidence from the trusted sensor
adapter. It denotes the start of uninterrupted, fresh absent observations. The
adapter must clear it on presence, invalid data, or a gap longer than the sensor
freshness limit. This avoids interpreting a slow solver as a sensor outage; a
single absent observation must never be backdated. Freshness of the current
observation is still required. Existing callers without the field retain the
conservative polling-based delay.

`ThermalTrackingRequest.presence_correction` accepts the existing
`PresenceCorrection` model. An active, zone-matching `away` correction permits
continuous setback without waiting for the automatic absence delay. A fresh
present observation overrides it. `comfort` vetoes setback. Expired, future, or
wrong-zone corrections grant no permission. These corrections do not alter raw
sensor history, operator setpoints, or manual Off/Direct priority. The execution
adapter must also revoke in-flight authority on return/cancel/expiry rather than
waiting for a long optimization run.


### Presence transitions and prospective planning

`ThermalConfigRequest.presence_revision` optionally identifies the adapter's presence
intent separately from `basis_control_revision`. The native result echoes it in
`diagnostics.presence_revision`; it does not grant execution permission. Manual
power, target and mode changes must continue to invalidate the manual revision.

A presence-only transition can retain a valid room plan under the tracking policy's
fresh presence, comfort, interval and lease checks. Adapters must immediately revoke
in-flight actuator authority, bind new tracking responses to the current presence
revision, and reject responses from an older revision. A retained room plan does
not authorize reuse of its joint electrical trajectory: electrical admission and
selection must check the presence revision captured by the original computation.
Do not relabel an old computation with a new presence revision.

Schedule prospective solves with enough time for the bounded solver, transport and
input processing. If that margin is exhausted, keep a pending retry for the next
capture, while renewing only still-valid room authority. Recheck the margin just
before dispatch. A late completion remains superseded; changing its origin or
extending its lease would invalidate the projected state. Without a valid current
plan, use the independent controller fallback until a fresh plan becomes current.
