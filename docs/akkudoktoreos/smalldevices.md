% SPDX-License-Identifier: Apache-2.0
(smalldevices-page)=

# Running EOS on Small Devices

EOS can run on single-board computers and use eMMC or SD storage. Memory and run time
depend on the optimization horizon, slot duration, devices and forecast providers.

- Set general.timezone_override to an IANA time zone to skip automatic time zone lookup.
- Use optimization.search.effort to control the amount of work spent on each device.
- Set optimization.search.time_budget_seconds to stop starting new search moves after
  the budget expires.

## Storage writes

With the default `database.autosave_interval_sec` the measurement and prediction databases are
written every few seconds. On an installation that pushes its forecasts and measurements from
an external system this amounted to 12–14 MB per minute; with autosave disabled about
1 KiB per minute remained. On SD cards and small eMMC this is the main source of wear.

Disabling autosave means that data pushed to EOS is lost when EOS restarts. That is fine when an
external system (an energy manager, Home Assistant, Node-RED) pushes forecasts and measurements
again; it is not suitable when EOS fetches and keeps its own history.
