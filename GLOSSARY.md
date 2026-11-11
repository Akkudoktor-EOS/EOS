# Akkudoktor-EOS

Energy forecasts, optimization and energy management plans for local energy devices, and the
AI-assisted workflow used to develop them. Written for contributors and coding agents; where
user-facing docs use another name, it is listed under _Avoid_.

## Optimization

**Optimization parameters**:
The complete, valid input set handed to one optimizer for one optimization run.
_Avoid_: optimization input, request parameters

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
