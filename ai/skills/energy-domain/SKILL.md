---
name: energy-domain
description: Use when working on optimization logic, adapters, controllers, device communication, or power/energy units and sign conventions.
---

# Energy Management and Device Integration

- Keep optimization logic separate from device-specific communication where
  practical.
- Follow existing adapter and controller interfaces.
- Respect the distinction between long-horizon energy optimization and fast
  local device control.
- Handle communication failures, device availability, and invalid measurements
  explicitly.
- Avoid introducing cloud dependencies into workflows intended to operate
  locally.
- **Preserve existing energy sign conventions, units, and conversion
  semantics.** Verify them before changing power, energy, or charging
  behavior.

## Optimization parameters are always valid

The preparation of the **optimization parameters** (see `GLOSSARY.md`) always
creates a valid parameter set. It never fails and never cancels an
optimization run. This is a hard rule.

- It covers the whole path from configuration, predictions and measurements to
  the parameters handed to an optimizer, for every optimizer (GENETIC,
  GENETIC0 and future ones), including device and operating-point parameters.
- Replace an invalid or missing input with a **fallback value**:
  1. the configuration field's default,
  2. a heuristic only when there is no default or the default does not fit
     the operating state (e.g. a charge limit of 0 when charging is
     inhibited). For a missing prediction: PV = 0, the last known price
     carried forward, the load default.
- Every fallback:
  - logs at error level, naming the input and the substitute value,
  - never changes the user's configuration (no write-back),
  - never invents devices: a device that cannot be resolved is left out or
    neutralised, never replaced by a demo device,
  - never lets an exception escape from the preparation.
- A parameter set that is still invalid after preparation is a bug: cover the
  case with a test and fix the preparation.
