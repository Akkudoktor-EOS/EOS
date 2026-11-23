# Optimizer API, visualization and wizard steps get no exception

An optimizer owns its REST endpoints, its visualization and its EOSdash wizard steps
([0005](0005-optimizers-plug-in-through-one-registry.md)), and all three read configuration or EMS
state today. None of them gets an exception from
[0003](0003-optimizers-see-only-their-optimization-parameters.md):

- The visualization (`<name>visualize.py`) is part of the solution conversion and renders from the
  optimization parameters and the optimization result only.
- The REST router (`<name>api.py`) never fetches anything itself. It declares placeholder
  dependency functions for what it needs: the optimizer's last optimization parameters and result,
  kept by the energy management, and the call that starts a run. The generic server fills them in
  through FastAPI's `app.dependency_overrides`, using the registry. The router may import FastAPI,
  Pydantic and its own optimization request, never configuration, EMS state or the server
  (`akkudoktoreos.server`). No other part of the optimizer, its parameter preparation included,
  imports the server either. It reports errors as the generic `OptimizerProblem`, which the server
  renders like its own problem responses, and it holds the public response models, built from the
  optimization parameters and result. The server includes the optimizers' routers before its own
  routes, so an optimizer's specific path (`.../solution/GENETIC0`) wins over a generic one
  (`.../solution/{algorithm}`).
- Wizard steps are not code. They are metadata on the fields of `<name>settings.py` (which fields
  the wizard asks for, and in which order), which EOSdash reads; an optimizer contains no EOSdash
  code.

## Considered Options

- **An "integration" role that may read configuration and EMS state**: rejected. It would become
  the place where optimizer code quietly reaches the outside world again, and removing an
  optimizer would still mean finding its integration code in the server and EOSdash.
- **The router imports its dependency functions from the server**: rejected. The optimizer would
  depend on the server, and a server helper is an easy way back to the configuration.
