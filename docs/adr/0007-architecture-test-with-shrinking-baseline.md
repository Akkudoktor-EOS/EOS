# An architecture test with a shrinking baseline enforces the optimizer boundaries

The optimizer rules ([0003](0003-optimizers-see-only-their-optimization-parameters.md) to
[0006](0006-optimizer-interface-prepare-run-convert.md)) are mostly import rules, and a skill alone
does not stop an agent or a contributor from breaking them. So `tests/test_optimizerarchitecture.py`
parses the imports of the source tree with `ast` and fails when

- an optimizer imports another optimizer,
- code outside an optimizer imports anything from it but its packages themselves (`__init__.py`),
- the shared device configuration imports an optimizer at all, so settings cannot build optimization
  parameters ([0004](0004-optimizers-duplicate-code-instead-of-sharing-it.md)),
- a module of an optimizer other than its parameter preparation imports a module or name that
  reads configuration, predictions, measurements or EMS state,
- parameter preparation imports EMS state or the server,
- a module of an optimizer other than `<name>optimizer.py`, its package `__init__.py` and the
  parameter preparation itself imports the parameter preparation, the optimizer's own package or
  `<name>optimizer.py` (both may re-export it; only the package imports `<name>optimizer.py`), so
  no other module reaches the configuration through it,
- a module other than settings, request, preparation and API
  ([0010](0010-one-directory-and-one-naming-scheme-per-optimizer.md)) imports Pydantic
  ([0009](0009-optimization-parameters-are-dataclasses.md)).

The test holds the one authoritative list of these modules and names, including helpers that read
the configuration, the server (`akkudoktoreos.server`,
[0011](0011-optimizer-api-visualization-and-wizard-get-no-exception.md)), the shared device
configuration (`devices.settings`, `devices.profile`, `devices.devices`, `devices.devicesabc`) and
the contract classes that may be imported from a module that mixes them with configuration
(`optimization.optimization`). Parameter preparation is recognized by its file name,
`optimization/<name>/<name>prepare.py` or a subpackage `optimization/<name>/prepare/` (a plain
module `prepare.py` does not count); the settings
model `optimization/<name>/<name>settings.py` may import `config.configabc` to define
`optimization.<name>`. The test discovers the optimizers (every package under `optimization/`), so
adding one does not touch it; until the simulation devices have moved into the optimizer
directory, `devices/<name>` counts as part of the optimizer.

The violations that exist when the test lands are listed in a baseline in the test file. A
violation missing from the baseline fails, and so does a baseline entry that no longer occurs, so
the baseline only shrinks and every exception is visible in review. The one exception: when a
module or helper that reads the configuration is added to the list, the existing uses it reveals
are added to the baseline in the same commit. Moving or splitting a module is no exception: its
entries are relabelled to the new module one to one, in the same commit and named in the pull
request, and no entry is added. The test cannot see simulation physics moved into a new shared
module, nor a helper missing from its list; both rely on review.

## Considered Options

- **`import-linter`**: rejected, it adds a dependency for checks a short `ast` walk can do.
- **Refactor first, then add the test without a baseline**: rejected, the boundaries would stay
  unprotected for the whole migration.
