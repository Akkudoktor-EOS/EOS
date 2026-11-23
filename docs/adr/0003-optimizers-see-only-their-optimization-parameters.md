# Optimizers see only their optimization parameters

Configuration, predictions, measurements and EMS state change while an optimization run is in
progress, and an optimizer that reads them live can optimize against one state and report the plan
against another. So the parameter preparation of an optimizer is the only part of it that reads
configuration, predictions or measurements (EMS state reaches it only as arguments, see
[0006](0006-optimizer-interface-prepare-run-convert.md)); it runs once before the optimization run and
puts everything the run needs into the optimization parameters. The algorithm, the simulation, the
simulation devices and the solution conversion use nothing but the optimization parameters and the
results of the run, never configuration, predictions, measurements or EMS state, neither directly
nor through a helper that reads them (the complete list of modules and names is kept in the
architecture test, see [0007](0007-architecture-test-with-shrinking-baseline.md)). This includes information
that is only needed to present the solution, such as the time zone, device IDs, the start of the
run and the prediction series the run was optimized against.

## Considered Options

- **Let the solution conversion read the configuration and predictions**: rejected. A prediction
  update during the run pairs the solution with a forecast it was not optimized for, and display
  details such as the time zone can change between preparation and conversion as well.

## Consequences

- An optimizer can be run, tested and replayed from a stored set of optimization parameters alone.
- The optimization parameters grow to carry presentation data (time zone, device IDs, start
  datetime, the prediction series used).
- The presentation data cover every entry point, not only the automatic run. A client's
  optimization request may start in another time zone than the configured one or carry longer
  arrays, so the prepared series span the widest window the conversion can need, and the
  conversion takes its own share of them (GENETIC0 migration).
