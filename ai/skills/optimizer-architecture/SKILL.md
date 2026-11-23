---
name: optimizer-architecture
description: Use when creating, changing, refactoring or removing an optimizer (GENETIC, GENETIC0 or a new one), its parameter preparation, simulation, simulation devices, solution conversion, or the code that runs optimizers.
---

# Optimizer Architecture

Terms as in `GLOSSARY.md`: **optimizer**, **parameter preparation**, **optimization
parameters**, **solution conversion**, **warm start**. The decisions and their reasons are in
`docs/adr/` 0003 to 0011; read the ADR before deviating from a rule here, and flag any conflict.

## One optimizer, four parts

| Part | May use |
|---|---|
| Parameter preparation (`<name>prepare.py` or `prepare/`) | configuration, predictions, measurements, and the run start and warm start passed in as arguments |
| Algorithm (`run()`) | the optimization parameters only |
| Simulation and simulation devices | the optimization parameters only |
| Solution conversion (`convert()`) | the optimization parameters and the result of the run only |

- **Hard rule:** outside parameter preparation, an optimizer never reads
  configuration, predictions, measurements or EMS state, directly or through a
  helper that does (e.g. `configured_grid_interpolator_backend()`, the EMS run
  cache in `core.cache`). This includes data only needed to present the
  solution: time zone, device IDs, run start and the prediction series the run
  used travel in the optimization parameters. The modules and names this
  covers are listed once, in `WORLD_MODULES`, `WORLD_NAMES` and `EMS_*` of
  `tests/test_optimizerarchitecture.py`; that list is authoritative. Among
  them: `config`, `prediction`, `measurement`, `adapter`, `server`,
  `core.ems`, `core.cache`, the `core.*settings` and database modules, the
  `core.coreabc` mixins and getters, `OptimizationBase` and the shared device
  configuration (`devices.settings`, `devices.profile`, `devices.devices`,
  `devices.devicesabc`). From `optimization.optimization` only the contract
  class `OptimizationSolution` may be imported (`CONTRACT_NAMES`).
- Parameter preparation runs once per run, never fails, never returns `None` and follows the
  "Optimization parameters are always valid" rules of the `energy-domain` skill.
- Parameter preparation does not read EMS state either (`core.ems`,
  `get_ems()`, `EnergyManagementSystemMixin`, `StartMixin`); the energy
  management passes the run start and the warm start as arguments. No part of
  an optimizer, its preparation included, imports `akkudoktoreos.server`.
- Only `<name>optimizer.py`, the package `__init__.py` and the parameter
  preparation itself import the parameter preparation. Any other module would
  reach the configuration through it. The package and `<name>optimizer.py`
  may re-export the preparation, so the other modules import neither; only
  the package imports `<name>optimizer.py`.

## The interface

This is the one maintained copy of the interface; ADR 0006 records the decision and links here.

```python
class Optimizer(Protocol[P, Q, R]):
    name: str
    async def prepare(
        self, start_datetime: DateTime, warm_start: R | None,
        overrides: Mapping[str, Any] | None = None,
    ) -> P
    async def prepare_from_request(
        self, request: Q, start_datetime: DateTime, warm_start: R | None,
        overrides: Mapping[str, Any] | None = None,
    ) -> P
    def run(self, parameters: P) -> R
    async def convert(
        self, parameters: P, result: R
    ) -> tuple[OptimizationSolution, EnergyManagementPlan]
```

The code is `akkudoktoreos.optimization.optimizer.Optimizer`; the energy management finds an
optimizer through `akkudoktoreos.optimization.optimizerregistry.get_optimizer(name)` and keeps
the last parameters and result per optimizer (`OptimizerRun`); the result is the next warm start.

- `run()` is deterministic: the same optimization parameters give the same result. Generations,
  individuals, seed and penalties are part of the optimization parameters. A run-time override
  (e.g. `?ngen=`) reaches the preparation as `overrides` (`{"generations": 10}`), never beside the
  parameters; the preparation applies the keys it knows and logs an error for any other key.
- `convert()` is `async`, so that an optimizer can migrate step by step while its conversion
  still awaits reads; once it uses only the parameters and the result, it awaits nothing.
- **Optimization parameters (`P`), including the simulation's device
  parameters, are plain dataclasses, never Pydantic models** (ADR 0009). They
  are valid because parameter preparation builds them valid, not because a
  model validates them. Optimization requests (`Q`) and settings models are
  Pydantic models: validation and public schemas belong where external input
  arrives.
- **Optimization requests never go straight into `run()`.** A REST endpoint
  passes the client's optimization request (`Q`) to the optimizer's
  `prepare_from_request()`, which
  completes it from the configuration with the same fallback rules as
  `prepare()`. Keep the request's public schema unchanged; add fields the run
  needs to the optimization parameters, not to the request.
- A run must be reproducible from its stored optimization parameters alone. Add a test that runs
  from a stored parameter file when you change what goes into the parameters.

## Layout and names (ADR 0010)

Everything of an optimizer lives in `src/akkudoktoreos/optimization/<name>/`,
`<name>` being the optimizer's name in lowercase (`genetic`, `genetic0`); its
simulation devices in `devices/` below it. Modules are `<name><role>.py`,
classes `<Name><Role>`. This table is the one maintained copy; ADR 0010 links here.

| Role | Module | Class | Kind |
|---|---|---|---|
| settings | `<name>settings.py` | `<Name>Settings` | Pydantic, `optimization.<name>` |
| request | `<name>request.py` | `<Name>Request` | Pydantic, public API |
| parameters | `<name>parameters.py` | `<Name>Parameters` | dataclass, input of `run()` |
| preparation | `<name>prepare.py` | `prepare()`, `prepare_from_request()` | |
| optimizer | `<name>optimizer.py` | `<Name>Optimizer` | implements the interface |
| algorithm | `<name>algorithm.py` | | the search |
| result | `<name>result.py` | `<Name>Result` | dataclass, output of `run()` |
| simulation | `<name>simulation.py`, `devices/<name><device>.py` | `<Name><Device>`, `<Name><Device>Parameters` | dataclass parameters |
| conversion | `<name>convert.py` | `convert()` | |
| visualization | `<name>visualize.py` | | renders parameters and result |
| API | `<name>api.py` | REST router | fed by FastAPI dependencies |

- A new module that fits no role still gets the `<name>` prefix. Do not
  invent a second name for an existing role.
- Dataclasses are `@dataclass(frozen=True, slots=True)`. A run never changes
  its inputs.
- Only `<name>settings.py`, `<name>request.py`, parameter preparation and
  `<name>api.py` may import `pydantic` or `akkudoktoreos.core.pydantic`.

## Independence

- An optimizer never imports another optimizer.
- **Duplicate code instead of sharing it.** Simulation physics (efficiency curves, AC/DC energy
  solvers, device models) is copied into each optimizer that needs it. Never move simulation code
  into a shared module, and never merge copies across optimizers to remove duplication. A fix in
  one copy reaches another optimizer only through a deliberate change to that optimizer.
- An optimizer may import: generic utilities (such as `datetimeutil`; Pydantic only in the roles allowed above), the
  output contract (`OptimizationSolution`, the energy management plan) and, in parameter
  preparation only, the configuration settings, including the shared operation-mode enums and
  settings-side resolution such as battery profiles. Translate those into the optimizer's own
  types during parameter preparation; do not pass settings objects or enums from the
  configuration into the simulation.
- Code outside an optimizer imports nothing from it but its package
  (`optimization/<name>/__init__.py`), which exports what the registry and the
  settings field need. Converting device
  settings into an optimizer's device parameters belongs to that optimizer's parameter
  preparation, not to `devices/settings/`: the shared device configuration
  (`devices.settings`, `devices.profile`, `devices.devices`, `devices.devicesabc`)
  never imports an optimizer, not even its package.

## What an optimizer owns

Its package holds everything specific to it: the settings model `optimization.<name>` and the
validation of its horizons, parameter preparation, run, simulation, simulation devices, solution
conversion, visualization, its REST endpoints (contributed as a router) and its EOSdash wizard
steps. The energy management, the server and EOSdash use only the registry and the interface;
never add a branch per optimizer there.

None of these gets an exception from the hard rule (ADR 0011):

- `<name>visualize.py` renders from the optimization parameters and result
  only, like `convert()`.
- `<name>api.py` fetches nothing itself. It declares placeholder dependency
  functions for the last parameters and result and the run trigger; the
  generic server fills them in through `app.dependency_overrides` from the
  registry. It imports FastAPI, Pydantic and its request, never
  configuration, EMS state or `akkudoktoreos.server`.
- Wizard steps are field metadata in `<name>settings.py` that EOSdash reads;
  no EOSdash code inside an optimizer.

Removing an optimizer touches exactly four places in the production code: its directory, its
registry entry, its static settings field in `OptimizationCommonSettings`, and a configuration
migration step that drops `optimization.<name>` and switches `optimization.algorithm` to a
remaining optimizer. If a change makes removal need anything else, it breaks this rule. What
belongs only to the optimizer goes with it: its tests, its test data and, while the architecture
test's `BASELINE` lists violations of the optimizer, those entries. The remaining optimizers'
tests pass unchanged.

## Enforcement

`tests/test_optimizerarchitecture.py` (ADR 0007) checks the import rules above
against `BASELINE`, the violations that existed when it was introduced.

- Parameter preparation is recognised by file name:
  `optimization/<name>/<name>prepare.py` or a subpackage
  `optimization/<name>/prepare/` (a package; a plain `prepare.py` does not
  count). Only there may an optimizer import the modules and names of the hard
  rule, except EMS state and the server.
- When you find another module or helper that reads configuration,
  predictions, measurements or EMS state, add it to the list in the test.
  That is the only case in which `BASELINE` may grow: by the existing uses the
  new list entry reveals, in the same commit, and named in the pull request.
- `optimization/<name>/<name>settings.py` may import `config.configabc` to define
  `optimization.<name>`, nothing else from the configuration.
- Otherwise never add an entry to `BASELINE`, and never rename a module to
  `*prepare.py` or `*settings.py` just to pass the test. Remove the entries
  your change fixes; the test fails on stale entries.
- The test discovers optimizers (every package under `optimization/`); do not
  hard-code optimizer names in it.
- It cannot detect simulation physics moved into a new shared module, nor a
  helper that reads the configuration but is missing from the list. Both are
  enforced by review: treat them as hard violations.
- A failing architecture test is a hard violation.

## Refactoring versus behaviour change (ADR 0008)

- **Refactoring an optimizer never changes its results.** With a fixed seed,
  every golden input (`tests/testdata/<optimizer>/optimize_result_*.json`,
  replay files) gives the same solution as before. Never regenerate or edit
  golden data in a refactoring; if a golden test fails, the refactoring is
  wrong.
- A change that alters results is a behaviour change: its own pull request,
  declared as such, never mixed with refactoring.
- **GENETIC0 is frozen.** No bug fixes, no behaviour changes, no tuning. Only
  structural changes needed to follow this architecture, each proven
  result-identical by its golden tests. Fix a GENETIC0 bug in GENETIC instead,
  and report it to the user.
- Before refactoring an optimizer, add replay files: complete optimization
  parameters in the new parameter type plus the expected result.
