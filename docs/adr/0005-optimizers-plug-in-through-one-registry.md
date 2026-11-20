# Optimizers plug in through one registry

Removing an optimizer must not require changes all over the code base. So every optimizer
implements the same small interface and is listed in one registry; the energy management, the
server and EOSdash use only the registry and the interface and never name a specific optimizer.
An optimizer is one directory, `optimization/<name>/`
([0010](0010-one-directory-and-one-naming-scheme-per-optimizer.md)); outside code imports only its
package itself (`__init__.py`), which exports what the registry and the settings field need.
The optimizer's package owns everything specific to it: its settings model
(`optimization.<name>`) including the validation of its horizons, its parameter preparation, run
and solution conversion, its own REST endpoints (contributed as a router), its visualization and
its wizard steps and its simulation devices. Removing an optimizer touches exactly four places in
the production code: its package, its registry entry, its settings field in
`OptimizationCommonSettings`, and a configuration migration step that drops `optimization.<name>`
from existing configurations and switches `optimization.algorithm` to a remaining optimizer. What
belongs only to the optimizer goes with it: its tests, its test data and, while the baseline of the
architecture test ([0007](0007-architecture-test-with-shrinking-baseline.md)) still lists
violations of the optimizer, their entries. The tests of the remaining optimizers pass unchanged.

## Considered Options

- **Settings fields added dynamically from the registry**: rejected. A Pydantic model assembled at
  runtime is harder to type-check and to document; a static field per optimizer costs one more
  line to delete on removal.
- **One branch per optimizer in the energy management and the server**: rejected. Each new or
  removed optimizer would touch shared code, and the branches drift apart.
