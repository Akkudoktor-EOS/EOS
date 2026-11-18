# Optimizer API, visualization and wizard steps get no exception from the access ban

An optimizer owns its REST endpoints, its visualization and its EOSdash wizard steps
([0005](0005-optimizers-plug-in-through-one-registry.md)), and all three read configuration or EMS
state today. None of them gets an exception from
[0003](0003-optimizers-see-only-their-optimization-parameters.md):

- The visualization (`<name>visualize.py`) is part of the solution conversion and renders from the
  optimization parameters and the optimization result only.
- The REST router (`<name>api.py`) never fetches anything itself. The generic server hands it what
  it needs through FastAPI dependencies: the optimizer's last optimization parameters and result,
  kept by the energy management, and the call that starts a run. The router may import FastAPI,
  Pydantic and its own optimization request, never configuration or EMS state.
- Wizard steps are not code. They are metadata on the fields of `<name>settings.py` (which fields
  the wizard asks for, and in which order), which EOSdash reads; an optimizer contains no EOSdash
  code.

## Considered Options

- **An "integration" role that may read configuration and EMS state**: rejected. It would become
  the place where optimizer code quietly reaches the outside world again, and removing an
  optimizer would still mean finding its integration code in the server and EOSdash.
