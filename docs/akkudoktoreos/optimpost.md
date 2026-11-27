% SPDX-License-Identifier: Apache-2.0

# Optimization by HTTP request

Use POST /v1/energy-management/run to plan with the stored EOS configuration and
optional runtime observations. It returns the energy-management instructions.
See [OHMRAKEL42 optimization](optimohmrakel42.md) for the runtime inputs.

POST /v1/optimize continues to accept the configuration-based compatibility
request and returns its established result and control fields.

The deprecated POST /optimize takes hourly forecasts and devices for an
individual run. Pass start_hour to choose the first control hour. Forecast arrays
and returned control arrays are indexed from local midnight. The result series
start at the chosen hour. Both English field names and the historical German
aliases are supported. The input does not replace the configuration or the plan
published to adapters. See the [upgrade guide](upgrade-ohmrakel42.md) for the
replacement plan and result endpoints and the transition for PDF reports.
