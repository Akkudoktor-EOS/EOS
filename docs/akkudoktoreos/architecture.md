% SPDX-License-Identifier: Apache-2.0

# Architecture

```{figure} ../_static/architecture-overall.png
:alt: Overall System Architecture

Overall System Architecture
```

## Overview of the Project Structure

## Key Components and Their Roles

```{figure} ../_static/architecture-system.png
:alt: EOS Architecture

EOS Architecture
```

### Configuration

The configuration controls all aspects of EOS: optimization, prediction, measurement, and energy
management.

### Energy Management

Energy management is the overall process to provide planning data for scheduling the different
devices in your system in an optimal way. Energy management cares for the update of predictions and
the optimization of the planning based on the simulated behavior of the devices. The planning is on
the hour.

### Optimization

### Device Simulations

Device simulations simulate devices' behavior based on internal logic and predicted data. They
provide the data needed for optimization.

### Predictions

Predictions provide predicted future data to be used by the optimization.

### Measurements

Measurements are utilized to refine predictions using real data from your system, thereby enhancing
accuracy.

### EOS Server

EOS operates as a [REST](https://en.wikipedia.org/wiki/REST) [API](https://restfulapi.net/) server.

### EOSdash

`EOSdash` is a lightweight support dashboard for EOS. It is pre-integrated with EOS. When enabled,
it can be accessed by navigating to [http://localhost:8503](http://localhost:8503) in your browser.
