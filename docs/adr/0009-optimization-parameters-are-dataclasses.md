# Optimization parameters are dataclasses, optimization requests are Pydantic models

The optimization parameters are read in the innermost loops of an optimization run, and they no
longer cross the public API: clients send optimization requests, which parameter preparation
completes into optimization parameters ([0006](0006-optimizer-interface-prepare-run-convert.md)).
So the optimization parameters, including the device parameters of the simulation, are plain
Python dataclasses (`@dataclass(frozen=True, slots=True)`: a run never changes its inputs, and
slots keep attribute access fast), never Pydantic models; they are valid by construction because parameter
preparation always builds a valid set (see the `energy-domain` skill), not because a model
validates them. Validation and the public schema live where external input arrives: optimization
requests are Pydantic models, and the settings models of the configuration stay Pydantic models.

## Considered Options

- **Pydantic models for the optimization parameters**: rejected. Attribute access and copies of
  Pydantic models cost time in the run, and the validation they offer is redundant behind a
  parameter preparation that never produces an invalid set.

## Consequences

- An invalid optimization parameter set is a bug in parameter preparation, found by its tests,
  not a validation error at run time.
- Replay files need an explicit (de)serialization of the dataclasses instead of Pydantic's; a
  test asserts that the round trip is lossless.
- The simulation's device parameters are derived from the optimization request by pure functions
  (no configuration, no clock, no change to the request), so a replay file may store the request
  and derive them again. GENETIC0's home appliance still reads today's date there and writes a
  default time window into the request; it is frozen.
- The optimization result is a frozen dataclass as well. Where public response models are built
  from it, its values are normalised as those models validate them (NumPy arrays to lists, NaN to
  `None`, the fields' number types), so the responses do not change when the result stops being
  a Pydantic model.
