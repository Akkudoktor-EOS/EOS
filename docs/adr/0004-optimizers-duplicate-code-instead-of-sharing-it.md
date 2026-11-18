# Optimizers duplicate code instead of sharing it

Each optimizer must be removable, and must keep its results, when another optimizer is changed or
removed. So dependencies run one way only. An optimizer may import generic utilities (such as
`datetimeutil`; Pydantic only where
[0009](0009-optimization-parameters-are-dataclasses.md) allows it), the output contract (the optimization solution and
the energy management plan) and, in its parameter preparation only, the configuration settings
(including the shared operation-mode enums and settings-side resolution such as battery profiles),
predictions and measurements. It never imports another optimizer, and it does not share simulation
physics: helpers such as efficiency curve interpolation or the AC/DC energy solvers are copied into
each optimizer that needs them. Code outside an optimizer never imports the optimizer's internals;
converting device settings into an optimizer's device parameters is part of that optimizer's
parameter preparation, not of the settings.

## Considered Options

- **Shared simulation physics in `devicesabc`**: rejected. A fix or model change for one optimizer
  would silently change the results of the others, and no optimizer could be reasoned about alone.
- **Settings that build each optimizer's parameters (`to_genetic_parameters()` and similar)**:
  rejected. It makes the configuration depend on every optimizer, so removing one breaks the
  settings.

## Consequences

- Duplicated code is deliberate. Do not merge copies of simulation code across optimizers to
  remove the duplication; a fix in one copy is applied to another only by a deliberate change to
  that optimizer.
- The output contract still lives in `optimization/optimization.py` together with
  `OptimizationCommonSettings`; it moves to a module of its own during the migration. Until then
  only the contract classes may be imported from there outside parameter preparation.
- The operation-mode enums and settings-side resolution stay shared because they describe the
  configuration, not an optimizer; an optimizer translates them into its own types during
  parameter preparation.
