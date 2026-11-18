# One directory and one naming scheme per optimizer

Removing an optimizer ([0005](0005-optimizers-plug-in-through-one-registry.md)) and telling the
optimizers apart in code, tracebacks and search results is easiest when every optimizer has the
same shape. So everything that belongs to an optimizer, its simulation devices included, lives in
one directory `src/akkudoktoreos/optimization/<name>/`, named after the optimizer in lowercase
(`genetic`, `genetic0`). Its simulation devices live in the subdirectory `devices/`; the top-level
`devices/` package keeps only the shared device configuration (`settings/`, `profile/`,
`devices.py`, `devicesabc.py`). Every module is named `<name><role>.py` and every class `<Name><Role>`, with one
fixed set of roles:

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

The package's `__init__.py` is the only module outside code imports. Further modules that do not
fit a role use the `<name>` prefix as well, also inside subdirectories such as `devices/` or a
`prepare/` subpackage for a large parameter preparation.

## Consequences

- The public classes are renamed (e.g. `GeneticOptimizationParameters` → `GeneticRequest`,
  `GeneticCommonSettings` → `GeneticSettings`). Field names and payloads stay unchanged; the
  schema names in OpenAPI and the configuration documentation change.
- `devices/genetic/` and `devices/genetic0/` move into the optimizer directories. For the frozen
  GENETIC0 ([0008](0008-optimizer-refactoring-preserves-results.md)) the moves and renames are
  structural changes, proven by its golden tests.
