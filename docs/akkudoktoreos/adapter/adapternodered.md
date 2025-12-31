% SPDX-License-Identifier: Apache-2.0
(adapter-homeassistant-page)=

# Home Assistant Adapter

The Home Assistant adapter provides a bidirectional interface between
**Home Assistant (HA)** and the **Akkudoktor-EOS (EOS)** energy optimisation system.

It allows EOS to:

- **Read** entity states and attributes from Home Assistant
- **Write** optimisation results and control instructions back to Home Assistant

This enables EOS to integrate seamlessly with Home Assistant–managed devices,
sensors, and energy meters, while keeping EOS device simulations and optimisation
logic decoupled from HA-specific implementations.

## Configuration entity IDs

EOS can synchronise parts of its configuration from Home Assistant entity states.
This is particularly useful for **device (resource) parameters** that are already
provided by Home Assistant integrations, such as:

- Battery capacity
- Maximum charge or discharge power
- Nominal device ratings

These configuration values are typically consumed by EOS **device simulations**
during optimisation.

### Entity state conversion rules

When reading configuration values from entity states, the adapter applies the
following heuristics to convert the HA state into a suitable EOS value:

- **Boolean `True`**: `["y", "yes", "on", "true", "home", "open"]`
- **Boolean `False`**: `["n", "no", "off", "false", "closed"]`
- **`None`**: `["unavailable", "none"]`
- **`float`**: if the value can be converted to a floating-point number
- **`str`**: if none of the above apply

## Device instruction entity IDs

After each energy optimisation run, EOS produces **device instructions** for the
controlled resources. These instructions are written back to Home Assistant via
dedicated entities.

- The **entity state** represents the selected **operation mode** of the device.
- **Entity attributes** provide additional parameters for the operation mode, such as:

  - `operation_mode_factor`
  - Power or rate limits
  - Mode-specific control parameters

Home Assistant automations or device integrations can then react to these entity
updates to perform the actual control actions.

## Device measurement entity IDs

Before starting an energy optimisation run, EOS retrieves **measurement values**
from Home Assistant that describe the *current state* of devices.

Typical examples include:

- Battery state of charge (SoC)
- Current power or energy levels
- Device availability or readiness indicators

These measurements are used as input for EOS **device simulations** and strongly
influence optimisation results.

## Load EMR entity IDs

Load **Energy Meter Readings (EMR)** are used to adapt and refine the **load
prediction**.

EOS retrieves these readings from Home Assistant **before** each energy management
run to align forecasts with actual consumption.

## PV production EMR entity IDs

PV production **Energy Meter Readings (EMR)** are used to adapt and refine the
**photovoltaic generation forecast**.

EOS retrieves these readings from Home Assistant **before** each optimisation run
to improve forecast accuracy based on real production data.

## Solution entity IDs

Each energy management run produces an **optimisation solution**.

In addition to device-level instructions, EOS can publish solution-level details to
dedicated Home Assistant entities. These entities are useful for:

- Debugging and validation
- Visualisation and dashboards
- Gaining deeper insight into optimisation decisions

EOS updates these entities **after** each energy management run.

## Entity state and value conversion

To adapt, scale, or transform Home Assistant entity values to match EOS
expectations, it is recommended to use
[template sensors](https://www.home-assistant.io/integrations/template/#sensor).

This allows value conversion to remain fully within Home Assistant, keeping the EOS
configuration clean and consistent.

### Example: Battery SoC conversion

Convert a battery state of charge from percentage `[0..100]` to a normalised factor
`[0.0..1.0]`:

<!-- pyml disable line-length -->
```yaml
template:
  - sensor:
      - name: "Battery1 SoC Factor"
        unique_id: "battery1_soc_factor"
        state: >
          {% set bat_charge_soc = states('sensor.battery1_soc_percent') | float(100) -%}
          {{ bat_charge_soc / 100.0 }}
        state_class: measurement
```
<!-- pyml enable line-length -->
