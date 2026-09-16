# Slot-aware GENETIC device physics

This package depends on the device-map and algorithm-converter configuration work
from PR #1256 and the runtime settings foundation from PR #1305. Parameter classes
remain in `devices/genetic`; settings remain in `devices/settings`.

Battery and inverter models accept a slot duration, apply power limits as energy
per slot, and preserve the total battery charge/discharge budget across multiple
calls within that slot. Battery export is an explicit device-simulation operation;
setting export rates does not by itself activate optimizer export states. The
converter carries stable device IDs, charge/export levels and LCOS unchanged.
Configuration LCOS uses amount/kWh; no Wh conversion is applied by the converter.

The GENETIC inverter now computes expected direct PV-to-load power from the
minute-load distribution, then converts power to interval energy. This changes
GENETIC simulation economics even at the default hourly interval. The probability
table was calibrated with hourly mean loads: use with quarter-hour means remains
an approximation, not a separately calibrated quarter-hour model. Its legacy
cumulative-probability API now clamps values at the table boundary and to [0, 1].
The GENETIC0 inverter uses a separate implementation and separate interpolation
data file; this package does not change either. Existing GENETIC0 optimization
and PDF golden tests remain required.

The shared in-memory cache must include callable identity in each key. Otherwise
the legacy cumulative probability and new direct-power method on the same object
can return each other's cached results for identical arguments. This package
includes that prerequisite fix and verifies both call orders and cache reuse.

This package alone does **not** make an Optimize mode quarter-hour capable.
GENETIC parameter preparation still forces the interval to 3600 seconds, and
`/optimize` still selects the separate GENETIC0 algorithm. Quarter-hour scheduling,
warm-start alignment, forecast/control horizons, terminal values, export states,
EV deadlines and flexible-consumer planning require the later optimizer port.
No inactive EV-deadline fields are introduced here.

Validation covers charge/discharge budgets, inverter/export limits, efficiencies,
energy conservation, interpolation boundaries, converter fields, both algorithms'
short optimization/PDF runs and unchanged GENETIC0 monetary goldens. GENETIC runs
use independent repricing of grid energy, schema checks and physical bounds rather
than requiring the previous direct-consumption model's monetary golden. Long
`--finalize` optimization runs and pinned CI environments remain separate checks.
