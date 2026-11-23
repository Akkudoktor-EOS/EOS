# Optimizers implement prepare, run and convert

Every optimizer follows the same three-step interface, so the energy management can drive any of
them the same way and every run can be replayed from its optimization parameters. The interface is
a `Protocol` generic in the optimizer's optimization parameters `P`, optimization request `Q` and
optimization result `R`, with `prepare()` and `prepare_from_request()` (asynchronous, given the run
start, the observation time, the warm start and run-time overrides), `run()` and `convert()`
(asynchronous), the latter
returning the optimization solution and the energy management plan. Its current signature is kept in one place, the
`optimizer-architecture` skill (`ai/skills/optimizer-architecture/SKILL.md`).

- `prepare()` is the parameter preparation. It never fails and never returns `None` (see the
  `energy-domain` skill); the energy management never cancels a run because of it.
- `run()` is CPU-bound and deterministic: the same optimization parameters give the same result.
  All algorithm settings (generations, individuals, seed, penalties) are part of the optimization
  parameters; a REST override such as `?ngen=` is passed to the preparation as an override and
  so becomes part of the parameters before `run()`.
- `convert()` is the solution conversion and uses only the parameters and the result.

The energy management is the only owner of run state. It keeps the last result per optimizer and
passes the run start, the observation time (the unrounded moment measurements refer to) and that
result (the warm start) to `prepare()` as arguments, so no part of an optimizer reads EMS state;
the warm start and its start datetime travel into `run()` inside the optimization parameters.

Optimization requests from clients (the parameters accepted by `POST /optimize` and the GENETIC0
legacy endpoint, and `ConfigOptimizationRequest`) never go straight into `run()`. They keep their
public schema (`Q`) and go through the optimizer's parameter preparation,
`prepare_from_request()`, which fills everything the
request does not carry (time zone, device IDs, algorithm settings) from the configuration and
applies the same fallback rules as `prepare()`. A malformed request is still rejected (422) before
preparation; a request that is valid but incomplete is completed, never rejected. So `run()` and
`convert()` only ever see complete optimization parameters, whatever the entry point.

## Considered Options

- **Preparation reads the previous result from the energy management**: rejected. It keeps the
  optimizer coupled to EMS internals (today `get_ems().genetic_solution()`), and a run cannot be
  reproduced from its parameters alone.
- **Algorithm settings passed beside the parameters**: rejected. A stored parameter set would not
  be enough to replay a run.
- **Clients send complete optimization parameters**: rejected. The presentation data required by
  [0003](0003-optimizers-see-only-their-optimization-parameters.md) would break every existing
  client of the public API.

## Consequences

- The energy management stores results per optimizer in one generic slot, not one attribute per
  optimizer.
- An optimization run that cannot be reproduced from its stored optimization parameters is a bug.
