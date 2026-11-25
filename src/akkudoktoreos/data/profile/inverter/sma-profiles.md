# SMA battery-capable inverter profiles

Generated from SMA Technical Information "Efficiency and Derating",
WirkungDerat-TI-en-53, version 5.3 (status 01/2023).

Included models:
- Sunny Boy Storage: SBS2.5-1VL-10, SBS3.7-10, SBS5.0-10, SBS6.0-10
- Sunny Tripower Storage X: STPS30-20, STPS50-20
- Sunny Tripower Smart Energy: STP5.0-3SE-40, STP6.0-3SE-40,
  STP8.0-3SE-40, STP10.0-3SE-40

Curve handling:
- Rated input voltage column selected.
- Standardized output-power breakpoints retained.
- Percentages converted to fractions.
- The same curve is used for DC-to-AC and AC-to-DC.
  The latter is an approximation, not a reverse-conversion measurement in
  the SMA document.
- `max_ac_charge_power_w` is null because this technical information does not
  specify a separate maximum AC battery-charge power.
- `power_derating` is omitted. The document presents derating as plots, not
  numerical tables; no values have been fabricated or visually digitized.

Not included:
- Sunny Island SI4.4M-13, SI6.0H-13, SI8.0H-13: the document provides their
  efficiency only as plots, without tabulated measurement points. They remain
  battery-capable models, but need a separately documented curve digitization
  before producing profiles with defensible breakpoint values.

Source: https://files.sma.de/downloads/WirkungDerat-TI-en-53.pdf
