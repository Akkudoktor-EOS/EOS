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
