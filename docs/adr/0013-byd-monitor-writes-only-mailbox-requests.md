# Mailbox requests are the only writes of the BYD monitor

The BYD Battery-Box Premium controller only watches the battery, yet it writes to the BMU. BYD
publishes no register map, and the community sources advise keeping to plain reads. But the
per-tower BMS details (cell voltages, temperatures, balancing, warnings, faults) and the event log
are served only through request-and-response mailboxes: the client writes `[unit, 0x8100]` to
`0x0550` (BMS info) or `0x05A0` (event log), waits for `0x8801` at `0x0551` or `0x05A1`, then reads
the answer. The monitor sends these requests by default, as the Home Assistant integration
`redpomodoro/byd_battery_box` does. All of them go through one guarded method that accepts exactly
these two register and value forms, with the unit checked against the tower count read from the
device, and raises on anything else. The hardware configuration registers `0x0010–0x0012` and the
undocumented `DEBUG` switch at `0x0100` are never written.

## Considered Options

- **Strictly read-only**: rejected. Only the BMU summary would be available: no per-tower
  readings, no cell data, no event log.
- **Mailbox requests only as an opt-in setting**: rejected. The details are the main reason to add
  a BYD monitor next to the inverter, which already reports the SoC. The Home Assistant integration
  sends the same requests routinely without known harm. A strictly read-only run is still possible
  with the diagnostics tool's `--read-only` option.
- **Read cells beyond 128 with the `DEBUG` write to `0x0100`, as a fork of the integration
  does**: rejected. Its effect on the BMU is unknown. A 5-module HVS publishes only cells 1–128.

## Consequences

- The event log requests are skipped while the controller's diagnostic log is disabled, since
  their entries go nowhere else.
- A mailbox that does not report ready within its timeout makes that tower's readings stale. It
  does not count as a lost connection.
