% SPDX-License-Identifier: Apache-2.0
(adapter-nodered-page)=

# Node-RED Adapter

The Node-RED adapter provides a **bidirectional interface** between
**NodeRED** and the **Akkudoktor-EOS (EOS)** energy optimisation system.

It allows EOS to:

* **Receive** entity states and attributes via Node-RED and EOS Rest API (The HTTP-IN Node "GET /eos_data_acquisition" is NOT yet functional)
* **Provide** control instructions via the new HTTP-IN Node
* **Provide** Solution and Plan results via Rest API

This enables EOS to integrate into tools like ioBroker and Grafana,
while keeping EOS **device simulations and optimisation
logic decoupled from other implementations**.

## 1. Exchanging data between EOS and e.g. ioBroker via Node-RED

### Basic concept

EOS **receives** e.g. measurements from **ioBroker objects** via **MQTT**
before each energy management run.

EOS **provides results** via HTTP-IN and Rest API after each optimisation run.
The values of these results can be moved via Node-RED. 

Typical use cases with Node-RED:

* optimal for heterogeneous systems
* Dashboards and visualisation
* Automations and scripts
* Device or manufacturer integrations
* Debugging and validation

### Configuration steps in EOS

#### 1. Enable and configure the Node-RED adapter

EOS must be configured with access to the Node-RED instance in Config->adapter.
* prerequisite is an already installed and running Node-RED instance
* adapter.nodered.host: 192.168.1.100 (example IP of your Node-RED instance)
* adapter.nodered.port: 1880 (default)
* adapter.provider: NodeRED

#### 2. Run energy optimisation

Continiously:
* EOS receives (via Node-RED) continiously measurement values before optimisation.

After the run, EOS provides:
* The device instruction and solution entities for the current time slot via the new HTTP-IN "Control Dispatch".
* The **Solution** via "http://192.168.1.100:8503/v1/energy-management/optimization/solution".
* The **Plan** via "http://192.168.1.100:8503/v1/energy-management/optimization/plan"

### Configuration steps in NodeRED

#### 1. Create Node-RED flow with nodes for processing

* battery SoC
* EOS Control Disptach
* EOS Solution
* EOS Plan

#### 2. Use EOS data

EOS entities can be referenced in:

* Automations
* Scripts
* Dashboards
* Device control logic


## 2. Data obtained *from EOS*

### 2.1 Device instruction entity IDs (Control Dispatch)

After each energy optimisation run, EOS produces **device instructions** for the
controlled resources.
E.g.:
{"battery1_op_mode":"SELF_CONSUMPTION","battery1_op_factor":1,"ev11_op_mode":"IDLE","ev11_op_factor":1,"homeappliance1_op_mode":"RUN","homeappliance1_op_factor":1}

* The **entity state** represents the device's selected **operation mode**.
* **Entity attributes** provide additional parameters for the operation mode, such as:

  * `operation_mode_factor`
  * Power or rate limits
  * Mode-specific control parameters
  * 

### 2.2 Solution entity IDs

Each energy management run produces an **optimisation solution**.

EOS can publish solution-level details to dedicated Home Assistant entities for:

* Debugging and validation
* Visualisation and dashboards
* Gaining deeper insight into optimisation decisions

EOS updates these entities **after each energy management run**.

## 3. Data read by EOS from Home Assistant

Before starting an energy optimisation run, EOS retrieves several categories of
data from Home Assistant, including:

### 3.1 Configuration entity IDs

EOS can synchronise parts of its configuration from Home Assistant entity states.
This is particularly useful for **device (resource) parameters** already provided
by Home Assistant integrations, such as:

* Battery capacity
* Maximum charge or discharge power
* Nominal device ratings

These values are typically consumed by EOS **device simulations** during optimisation.

### 3.2 Device measurement entity IDs

EOS retrieves **measurement values** that describe the *current state* of devices, such as:

* Battery state of charge (SoC)
* Current power or energy levels
* Device availability or readiness indicators

These measurements are used as input for EOS simulations and strongly influence
optimisation results.

### 3.3 Load EMR entity IDs

Load **Energy Meter Readings (EMR)** are used to adapt and refine the **load prediction**.

EOS retrieves these readings from Home Assistant **before each energy management run**
to align forecasts with actual consumption.

### 3.4 PV production EMR entity IDs

PV production **Energy Meter Readings (EMR)** are used to adapt and refine the
**photovoltaic generation forecast**.

EOS retrieves these readings from Home Assistant **before each optimisation run**
to improve forecast accuracy based on real production data.

## 4. Entity state and value conversion

When reading configuration values and measurements from entity states, the adapter
applies the following heuristics to convert the Home Assistant state into a suitable
EOS value:

* **Boolean `True`**: `["y", "yes", "on", "true", "home", "open"]`
* **Boolean `False`**: `["n", "no", "off", "false", "closed"]`
* **`None`**: `["unavailable", "none"]`
* **`float`**: if the value can be converted to a floating-point number
* **`str`**: if none of the above apply

### Recommendation: value conversion in Home Assistant

To adapt, scale, or transform Home Assistant entity values to match EOS
expectations, it is recommended to use
[template sensors](https://www.home-assistant.io/integrations/template/#sensor).

This keeps value conversion fully within Home Assistant, ensuring a clean and
consistent EOS configuration.

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

## 5. Further processing of EOS data in Home Assistant

Once published, EOS data behaves like any other Home Assistant entity and can be:

* Used as triggers or conditions in automations
* Mapped to device-specific services or integrations
* Visualised in dashboards
* Compared with measured values for monitoring and validation

EOS does **not** directly control devices.
It provides **structured optimisation results**, while Home Assistant remains
responsible for executing the actual control actions.

## 6. Data sent *to EOS* and how

All data sent to EOS is provided via **Home Assistant entity states and attributes**.

| Data type             | HA entity type  | Purpose in EOS       |
| --------------------- | --------------- | -------------------- |
| Configuration values  | Sensor / Number | Device modelling     |
| Measurements          | Sensor          | Initial device state |
| Energy meter readings | Sensor          | Forecast correction  |
| Availability flags    | Binary sensor   | Device availability  |

> EOS always **reads** this data; Home Assistant remains the authoritative source for measurements and configuration.

### Summary

* **EOS** focuses on **forecasting, simulation, and optimisation**
* **Home Assistant** focuses on **measurement, integration, and execution**

The Home Assistant adapter provides a clear, structured interface between both
systems, allowing flexible integration without coupling EOS to Home Assistant
device specifics.
