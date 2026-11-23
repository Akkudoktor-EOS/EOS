# One directory and one naming scheme per optimizer

Removing an optimizer ([0005](0005-optimizers-plug-in-through-one-registry.md)) and telling the
optimizers apart in code, tracebacks and search results is easiest when every optimizer has the
same shape. So everything that belongs to an optimizer, its simulation devices included, lives in
one directory `src/akkudoktoreos/optimization/<name>/`, named after the optimizer in lowercase
(`genetic`, `genetic0`). Its simulation devices live in the subdirectory `devices/`; the top-level
`devices/` package keeps only the shared device configuration (`settings/`, `profile/`,
`devices.py`, `devicesabc.py`). Every module is named `<name><role>.py` and every class
`<Name><Role>`, with one fixed set of roles: settings, request, parameters, preparation, optimizer,
algorithm, result, simulation (with its devices), conversion, visualization and API. The table of
roles with their modules, classes and kinds is kept in one place, the `optimizer-architecture`
skill (`ai/skills/optimizer-architecture/SKILL.md`).

The package's `__init__.py` is the only module outside code imports. Further modules that do not
fit a role use the `<name>` prefix as well, also inside subdirectories such as `devices/` or a
`prepare/` subpackage for a large parameter preparation.

## Consequences

- The public classes are renamed (e.g. `GeneticOptimizationParameters` → `GeneticRequest`,
  `GeneticCommonSettings` → `GeneticSettings`). Field names and payloads stay unchanged; the
  schema names in OpenAPI and the configuration documentation change. Nested request models are
  renamed `<Name><Thing>Request` as well (e.g. `Genetic0InverterParameters` →
  `Genetic0InverterRequest`); the name `<Name><Device>Parameters` belongs to the simulation's
  device dataclasses.
- `devices/genetic/` and `devices/genetic0/` move into the optimizer directories. For the frozen
  GENETIC0 ([0008](0008-optimizer-refactoring-preserves-results.md)) the moves and renames are
  structural changes, proven by its golden tests.
