# BYD Battery-Box Premium monitor

Device controller `byd:battery-box-premium` for BYD Battery-Box Premium HVS, HVM and HVL. It only
reads the battery management unit (BMU); the battery is controlled by its inverter. The readings
are published as shared data keys of the Control adapter.

## Files

- `bydbatterybox.py`: decoding, the monitor thread, settings and controller.
- `bydbatteryboxdiagnostics.py`: diagnostics tool for a real BMU, not used by EOS.
- Tests: `tests/test_adaptcontrolbydbatterybox.py`, with a real event log page as fixture.

## Sources

BYD publishes no register map. The protocol was worked out by the community from the installer
software:

- [redpomodoro/byd_battery_box](https://github.com/redpomodoro/byd_battery_box): Home Assistant
  integration. It reads the same registers and mailboxes as this controller.
- [sarnau/BYD-Battery-Box-Infos](https://github.com/sarnau/BYD-Battery-Box-Infos): reference
  script and protocol notes.
- [mbmap: BYD Battery-Box Premium](https://mbmap.nobrega.me/devices/byd/battery-box-premium/):
  register map based on sarnau. Registers `0x0511` to `0x0514` there are total charged and
  discharged energy (uint32, low word first, ×0.1 kWh), not charge cycles.

Treat every value as unchecked.

## Connection

- Modbus RTU frames (with CRC) over a TCP connection, not Modbus TCP: port 8080, unit id 1.
- The BMU has the fixed Ethernet address 192.168.16.254. EOS needs a route to 192.168.16.0/24,
  or a forwarded address. Wi-Fi access of the BMU switches off after a while, so use the wired
  port.
- **One client only.** While the controller is connected, BE Connect Plus or a Home Assistant
  integration cannot connect. Disable the controller (remove it from
  `adapter.control.controllers`) before using BE Connect Plus.

## Configuration

```json
"adapter": {
  "control": {
    "controllers": {
      "battery": {
        "controller_type": "byd:battery-box-premium",
        "host": "192.168.16.254"
      }
    }
  }
}
```

| Setting | Default | |
|---|---|---|
| `host` | | Address of the BMU. |
| `port`, `unit_id` | 8080, 1 | |
| `timeout_s` | 5 | Timeout of a Modbus transaction and of a mailbox request. |
| `status_interval_s` | 30 (min. 10) | Status of the BMU. Live. |
| `bms_info_interval_s` | 600 (min. 60) | BMS information of each tower. Live. |
| `event_log_interval_s` | 600 (min. 120) | Event log, only while the diagnostic log is enabled. Live. |
| `model` | detected | `HVS`, `HVM` or `HVL`. |

Live settings apply without a restart; the others restart the controller.

## What is read

| Data | Registers | When |
|---|---|---|
| Device information | `0x0000–0x0013` | After every connect |
| BMU status | `0x0500–0x0514` | Every `status_interval_s` |
| BMS information per tower | mailbox `0x0550` | Every `bms_info_interval_s` |
| Event log of the BMU and each tower, newest page | mailbox `0x05A0` | Every `event_log_interval_s` |

A mailbox request writes `[unit, 0x8100]` to `0x0550` or `0x05A0`, waits for `0x8801` at the next
register and reads the answer in packets of 65 registers from `0x0558` or `0x05A8`. These
requests are the only writes of the controller (ADR 0013). The configuration registers
`0x0010–0x0012` and the undocumented `DEBUG` switch at `0x0100` are never written. Without that
switch the mailbox reports cells 1 to 128 only, so a 5-module HVS publishes no cells of its 5th
module.

A mailbox that does not report ready within `timeout_s` leaves the readings of that tower stale
and records `mailbox_error`; the connection is kept.

## Shared data keys

`<device_id>:` plus:

- BMU status: `soc_factor` (0..1), `soh_percent`, `battery_voltage_v`, `output_voltage_v`,
  `current_a`, `power_w`, `cell_voltage_max_v`, `cell_voltage_min_v`, `cell_temperature_max_c`,
  `cell_temperature_min_c`, `bmu_temperature_c`, `charged_energy_kwh`, `discharged_energy_kwh`,
  `bmu_error_bits`.
- Device: `tower_count`, `module_count`.
- Tower n (1..3), only for towers that exist: `bms<n>_soc_percent`,
  `bms<n>_cell_voltage_max_v`, `bms<n>_cell_voltage_min_v`, `bms<n>_cell_temperature_max_c`,
  `bms<n>_cell_temperature_min_c`, `bms<n>_warning1_bits` to `bms<n>_warning3_bits`,
  `bms<n>_error_bits`, `bms<n>_balancing_cells`.

Current and power are positive when the battery discharges, negative when it charges. Set error
or warning bits are logged once per change with their names. The single cell voltages, the
temperature sensor values and the event log are only in the diagnostic log. The Control tab of
EOSdash graphs the cell voltages and temperature sensors of the towers: the latest values per
cell and sensor (profile) and their history (one heatmap per tower).

## Model detection

1. The `model` setting, if set.
2. Else the cell count: battery voltage / mean cell voltage / modules. 32 cells per module is
   HVS, 16 is HVM. HVL cannot be told apart from HVM this way.
3. Else the low byte of `0x0011` (0 HVL, 1 HVM, 2 HVS).

`0x0011` is not reliable: an HVS system was seen reporting 0 (HVL). A disagreement is logged and
recorded in the `device_info` event. Only HV towers are supported (serial number prefix `P03` or
`E0P3`). LVS and LVL are not supported. HVS+, HVM+ and HVB have no Ethernet BMU, so this protocol
does not apply to them.

## Checked on a real system

HVS, 2 towers of 4 modules, BMU firmware 3.26/3.27, BMS firmware 3.32, SMA STP SE (2026-11-16):

- Device information, status, BMS information of both towers and the event logs decode
  plausibly. The mailboxes answered without timeouts.
- `0x0011` reports 0 (HVL); the cell count gives 32 cells per module (HVS).
- The current register reads -96 (-9.6 A) while charging and +37 (+3.7 A) while discharging
  (2026-11-24): negative is charging. The tower currents add up to the BMU current.
- Each tower reports 128 cell voltages and 48 temperature sensor values (4 modules of 32 cells and 12
  sensors). The numbers of the maximum and minimum cell match the cell arrays, except once
  where the reported minimum (#48, 3292 mV) was 1 mV above the array minimum (#80, 3291 mV),
  exactly one module further on; probably a snapshot within the BMS, not confirmed.
- A read takes about 75 ms, a mailbox request with its answer well under a second.
- Event codes `0x2D` (BMU) and `0x1C` (tower) are not known to the community tables.
- Recorded registers of the discharging run are the test fixture
  `tests/testdata/bydbatterybox_hvs_discharging.json`.
- Not checked: HVM, HVL, three towers, set warning and error bits.

## Diagnostics tool

Close BE Connect Plus and stop the BYD controller of EOS first.

```bash
uv run python -m akkudoktoreos.adapter.controllers.byd.bydbatteryboxdiagnostics \
    --host 192.168.16.254 --count 2 --interval 10 --event-log-history
```

- `--read-only`: no mailbox requests, i.e. no writes at all.
- `--json`: one JSON object per event, including the raw words of every Modbus read.
- `--model HVS`: set the model instead of detecting it.
