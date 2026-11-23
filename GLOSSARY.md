# Akkudoktor-EOS

Energy forecasts, optimization and energy management plans for local energy devices, and the
AI-assisted workflow used to develop them. Written for contributors and coding agents; where
user-facing docs use another name, it is listed under _Avoid_.

## Optimization

**Optimizer**:
One optimization algorithm (such as GENETIC or GENETIC0) together with its parameter preparation,
simulation, simulation devices and solution conversion.
_Avoid_: optimization algorithm (for the whole), optimization

**Parameter preparation**:
The part of an optimizer that derives its optimization parameters from configuration, predictions,
measurements, the run start and the warm start, once before each optimization run; the only part of
an optimizer that reads configuration, predictions or measurements.
_Avoid_: parameter creation, request building

**Solution conversion**:
The part of an optimizer that turns the outcome of an optimization run into the optimization
solution and the energy management plan.
_Avoid_: solution post-processing

**Optimization request**:
The input a client sends to start an optimization run through the API; parameter preparation
completes it into optimization parameters.
_Avoid_: optimization parameters (for client input)

**Optimization result**:
The output of an optimizer's run, specific to that optimizer, before solution conversion.
_Avoid_: solution (for this)

**Optimization solution**:
The optimizer-independent outcome of an optimization run that the energy management publishes,
together with the energy management plan.
_Avoid_: result (for this)

**Warm start**:
The result of an optimizer's previous optimization run, used to seed its next run.
_Avoid_: start solution, previous solution

**Optimization parameters**:
The complete, valid input set handed to one optimizer for one optimization run.
_Avoid_: optimization input, request parameters

**Presentation data**:
The part of the optimization parameters that only the solution conversion and the visualization
use, such as the time zone, device IDs, the run start and the prediction series the run used.
_Avoid_: display data, conversion context

**Simulation device**:
An optimizer's own model of a device (battery, inverter, home appliance, ...) that its simulation
steps through an optimization run, built only from the optimization parameters.
_Avoid_: device (for this), device settings

**EMS state**:
What the energy management keeps between optimization runs, such as the run start, the last
results and the run cache; it reaches an optimizer only as arguments of parameter preparation.
_Avoid_: EMS data

**Golden input**:
A stored optimizer input whose expected output (optimization solution or optimization result) is
kept with it, so a test can show that a refactoring does not change results.
_Avoid_: test fixture (for this), reference data

**Replay file**:
A golden input made of complete optimization parameters plus the expected optimization result, so
an optimization run can be repeated without configuration, predictions or measurements.
_Avoid_: snapshot, recording

**Fallback value**:
A default or heuristic value that replaces an invalid or missing input while the optimization
parameters are prepared, always reported as an error.
_Avoid_: dummy value, demo value

## AI-assisted development

**Host**:
The contributor's own machine, where the repository is checked out and where changes are reviewed,
run and pushed.

**Sandbox**:
The isolated container in which a coding agent works on the repository.
_Avoid_: container, VM

**Task plan**:
The plan the contributor and the agent agreed on for one task; it is the spec the self-review checks
the change against, unless the change references an issue.
_Avoid_: design note, ticket

**Self-review**:
The agent's advisory review of its own change against the project rules and its spec (the
referenced issue, otherwise the task plan), done before it hands the change over and declared in
the pull request.
_Avoid_: AI review, automated review

**Hard violation**:
A self-review finding that breaches a documented project rule; the agent fixes it before handing
the change over.
_Avoid_: error, blocker

**Judgement call**:
A self-review finding based on a heuristic, such as a code smell or possible scope creep; it is
reported and left to the author.
_Avoid_: warning, nit

**Author review**:
The contributor's own review of the complete change on the host; the deciding review before a pull
request is opened or updated.
_Avoid_: human review, manual review
