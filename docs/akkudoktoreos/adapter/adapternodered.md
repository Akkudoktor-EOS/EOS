% SPDX-License-Identifier: Apache-2.0
(adapter-nodered-page)=

# Node-RED Adapter

The Node-RED adapter provides a **bidirectional interface** between
**NodeRED** and the **Akkudoktor-EOS (EOS)** energy optimisation system.

It allows EOS to:

* **Receive** entity states and attributes via Node-RED and EOS REST API (The HTTP-IN Node "GET /eos_data_acquisition" is NOT yet functional)
* **Provide** control instructions via the new HTTP-IN Node
* **Provide** Solution and Plan results via REST API

This enables EOS to integrate into tools like ioBroker and Grafana,
while keeping EOS **device simulations and optimisation
logic decoupled from other implementations**.

## 1. Exchanging data between EOS and e.g. ioBroker via Node-RED

### Basic concept

EOS **receives** e.g. measurements from **ioBroker objects** via **MQTT**
before each energy management run.

EOS **provides results** via HTTP-IN and REST API after each optimisation run.
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
* EOS receives measurement values via Node-RED before optimisation.

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
`{"battery1_op_mode":"SELF_CONSUMPTION","battery1_op_factor":1,"ev11_op_mode":"IDLE","ev11_op_factor":1,"homeappliance1_op_mode":"RUN","homeappliance1_op_factor":1}`

* The **entity state** represents the device's selected **operation mode**.
* **Entity attributes** provide additional parameters for the operation mode, such as:

  * `operation_mode_factor`
  * Power or rate limits
  * Mode-specific control parameters


### 2.2 Solution entity IDs

Each energy management run produces an **optimisation solution**.

EOS can publish solution-level details for:

* Debugging and validation
* Visualisation and dashboards
* Gaining deeper insight into optimisation decisions

EOS updates these entities **after each energy management run**.

## 3. Data retrieved by EOS via Node-RED

Before starting an energy optimisation run, EOS can retrieve several categories of
data via Node-RED, including:

### 3.1 Configuration entity IDs (NOT TESTED BY ME)

EOS can synchronise parts of its configuration via REST API.
This is particularly useful for **device (resource) parameters** already provided
by other systems, such as:

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

EOS can retrieve these readings via Node-RED REST API calls **before each energy management run**
to align forecasts with actual consumption.

### 3.4 PV production EMR entity IDs

PV production **Energy Meter Readings (EMR)** are used to adapt and refine the
**photovoltaic generation forecast**.

EOS can retrieve these readings via Node-RED REST API calls **before each optimisation run**
to improve forecast accuracy based on real production data.

## 4. Entity state and value conversion

Value conversion into the expected REST API format can be done in a Node-RED "function" node.

### Example: Battery SoC conversion

Convert a battery state of charge from percentage `[0..100]` to the expected schema


```
if (msg.topic === "Battery_SOC") {
    let now = new Date();

    // Format date/time as 'YYYY-MM-DD HH:MM:SS', then encode
    let pad = n => n < 10 ? "0" + n : n;
    let datetimeRaw =
        now.getFullYear() + "-" +
        pad(now.getMonth() + 1) + "-" +
        pad(now.getDate()) + " " +
        pad(now.getHours()) + ":" +
        pad(now.getMinutes()) + ":" +
        pad(now.getSeconds());
    let datetimeEncoded = encodeURIComponent(datetimeRaw);

    // Prepare value as decimal with leading zero
    let value = String(msg.payload);
    if (value.startsWith(".")) {
        value = "0" + value;
    } else if (!value.includes(".")) {
        let intval = parseInt(value);
        if (value.length === 1) {
            value = "0.0" + value;
        } else if (intval > 1 && intval < 100) {
            value = "0." + value;
        }
    }

    // Build and URL-encode key
    const key = "battery1-soc-factor";
    let keyEncoded = encodeURIComponent(key);

    // Build URL
    let baseUrl = "http://192.168.1.100:8503/v1/measurement/value";
    msg.url = `${baseUrl}?datetime=${datetimeEncoded}&key=${keyEncoded}&value=${encodeURIComponent(value)}`;
    msg.payload = ""; // Empty for PUT

    return msg;
}
return null;
```


## 5. Further processing of EOS data in e.g. ioBroker and Grafana

Once published, EOS data can be used in any needed scenario:

* Used as triggers or conditions in automations
* Mapped to device-specific services or integrations
* Visualised in dashboards
* Compared with measured values for monitoring and validation

EOS does **not** directly control devices.
It provides **structured optimisation results**, while e.g. ioBroker remains
responsible for executing the actual control actions.

### Summary

* **EOS** focuses on **forecasting, simulation, and optimisation**
* **Node-RED** focuses on **moving and processing**
* **ioBroker** focuses on **measurement, integration, and execution**
* **Grafana** focuses on **visualisation**

The Node-RED adapter and the REST API provide a structured interface between EOS and a heterogeneous environment,
allowing flexible integration without coupling EOS to device specifics.
