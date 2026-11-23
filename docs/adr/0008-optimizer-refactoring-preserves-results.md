# Optimizer refactoring preserves results, and GENETIC0 is frozen

An optimization result is a charge schedule for real devices, so a refactoring that silently
changes results is a defect in production, not a code-quality issue. Refactoring an optimizer,
including the migration to the architecture of ADRs
[0003](0003-optimizers-see-only-their-optimization-parameters.md) to
[0007](0007-architecture-test-with-shrinking-baseline.md), is therefore behaviour-preserving: with
a fixed seed every stored input gives the same solution as before. A change that alters a golden
result (`tests/testdata/<optimizer>/optimize_result_*.json` or a replay file) is a behaviour
change and goes in its own, explicitly declared pull request; a refactoring pull request never
regenerates golden data. GENETIC0 is frozen: its results never change. It gets no bug fixes and no
behaviour changes, only the structural changes needed to follow the optimizer architecture, each
proven result-identical by its golden tests.

## Consequences

- Before an optimizer is refactored, its golden tests are extended with replay files: complete
  optimization parameters in the new parameter type plus the expected result.
- GENETIC0 is migrated first, as the pilot; the registry and the interface arrive with its first
  migration pull request. GENETIC follows.
- A GENETIC0 bug that matters to users is fixed in GENETIC (or a new optimizer), not in GENETIC0.
- One declared exception: GENETIC0's parameter preparation follows the "optimization parameters
  are always valid" rule of the `energy-domain` skill, in a behaviour-change pull request of its
  own. It no longer writes demo providers or devices into the configuration and no longer cancels
  a run; results with a complete configuration stay the same.
