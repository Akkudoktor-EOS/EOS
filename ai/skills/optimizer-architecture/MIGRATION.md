# Migrating an optimizer to the architecture

The path GENETIC0 took (ADR 0008), for GENETIC and any later optimizer. One pull request per
step; every step proves its results identical with the golden tests and removes the `BASELINE`
entries it fixes.

## Steps

0. **Safety net.** Characterization tests that pin the whole output per golden input: raw
   result, optimization solution, energy management plan and visualization. No production code.
1. **Interface and registry.** An adapter `<Name>Optimizer` wraps the existing code; the energy
   management drives the optimizer only through the registry and keeps its run in the generic
   slot.
2. **Layout.** Move into `optimization/<name>/` and rename to the role names. Relabel the moved
   modules' `BASELINE` entries one to one.
3. **Preparation and parameters.** `<name>prepare.py` and the parameters dataclass with the run
   settings and presentation data; `run()` reads only the parameters. Replay files arrive here.
4. **Simulation devices** as frozen dataclasses derived from the request.
5. **Conversion and visualization** from the parameters and the result only.
6. **Result and API.** The result dataclass, `<name>api.py` with the response models and
   `api()`; the server no longer imports the optimizer.

The step is done when its golden and replay tests pass unchanged, the long golden case passes
with `--finalize`, and the architecture test passes with the fixed entries removed.

## Characterization tests

- Fix the run start (a date and hour, plus one case across a DST switch) instead of today.
- Mask wall-clock values (solution `id`, `generated_at`, the UUID part of instruction IDs).
- Feed the conversion from a fake prediction whose values follow the absolute time and encode
  the fill method, so a shifted window or a swapped fill method changes the pinned output. When
  the prediction series move into the parameters, patch the same fake into the parameter
  preparation; the golden output stays the same.
- Pin the visualization by spying on the arguments of the report's chart methods, not the PDF
  text (it holds `now()`). Later steps keep those method names and signatures.
- Run each case twice in separate processes before committing the goldens.
- Compare Pydantic models by `model_dump()` or their JSON, not `==`.

## Move, then fix

- Rule breaches whose fix changes results (a preparation that writes the configuration, invents
  demo devices or cancels the run) move unchanged in the structural step. The fix is the next,
  separately declared behaviour-change pull request.
- Known bugs of the old output stay reproduced (GENETIC0: EV and home appliance IDs always
  `ev1` / `homeappliance1`). Report them; for GENETIC0, fix them in GENETIC if it has the same
  code.
- A structural step that changes behaviour outside the results (GENETIC0 step 1: a raising
  preparation now cancels the run, `POST /optimize` answers 404 instead of 500) declares it in
  its pull request under **Merge Danger**; the golden results still stay the same.
- A step may keep a transitional seam (e.g. an optional argument the conversion falls back from)
  only when the plan names the step that removes it.

## What may remain in `BASELINE`

After step 6 GENETIC0 kept three entries, all types of the output contract or the public request
schema that live in banned modules: the solution's DataFrame (`core.pydantic`), the plan's
operation-mode enums (`devices.devicesabc`) and `TimeWindowSequence` (`config.configabc`).
Whether the test should allow these types is an open decision; until it is made they stay in
`BASELINE`, and any other entry left is unfinished migration.
