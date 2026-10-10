% SPDX-License-Identifier: Apache-2.0
(smalldevices-page)=

# Running EOS on Small Devices

EOS runs on single-board computers with 512 MB to 1 GB of RAM and on eMMC or SD storage.
With the default configuration it needs about 290 MB at idle and 400–450 MB while a GENETIC
run is in progress. The settings on this page bring that down to about 140 MB, without
changing the optimization result.

All figures below were measured with GENETIC at 300 individuals × 400 generations and
15-minute slots; the individual measurements state their conditions.

## Summary

| Setting | Effect | Plan |
|---|---|---|
| `optimization.genetic.fitness_cache_max_entries: 0` [^pr1376] | −110 to −160 MB peak per run | identical |
| `general.timezone_override` [^pr1377] | −28 MB at startup (no timezone lookup) | identical |
| `optimization.self_consumption_interpolator: numpy` [^pr1378] | −37 MB after the first run (no SciPy) | identical |
| `database.autosave_interval_sec: null` | storage writes from ~13 MB/min to ~1 KiB/min | identical, see notes |
| `PYTHONMALLOC=malloc` (environment) | memory is returned after each run (glibc) | identical |
| `OPENBLAS_NUM_THREADS=1` (environment) | no per-core BLAS buffers | identical |

[^pr1376]: Added by [#1376](https://github.com/Akkudoktor-EOS/EOS/pull/1376).
[^pr1377]: Added by [#1377](https://github.com/Akkudoktor-EOS/EOS/pull/1377), which also imports
    optional heavy packages on first use (−123 MB at startup without any configuration change).
[^pr1378]: Added by [#1378](https://github.com/Akkudoktor-EOS/EOS/pull/1378).

Example configuration:

```json
{
  "general": { "timezone_override": "Europe/Berlin" },
  "optimization": {
    "self_consumption_interpolator": "numpy",
    "genetic": { "fitness_cache_max_entries": 0 }
  },
  "database": { "autosave_interval_sec": null }
}
```

Environment of the EOS process (for example in the systemd unit):

```ini
Environment=PYTHONMALLOC=malloc
Environment=MALLOC_ARENA_MAX=2
Environment=MALLOC_TRIM_THRESHOLD_=131072
Environment=MALLOC_MMAP_THRESHOLD_=131072
Environment=OPENBLAS_NUM_THREADS=1
```

## Measurements

### Memory, all settings combined

Linux arm64 (Debian 13, glibc, Python 3.13), one CPU per container, server-like process
(EOSdash off), 48 h horizon, 77 kWh battery with direct marketing; "with EV" adds an
electric vehicle and a dynamic tariff.

| | Idle | Peak during a run | Peak with EV |
|---|---|---|---|
| `main` (default settings) | 282–288 MB | 401 MB | 451 MB |
| all settings above | 136 MB | 142 MB | 144 MB |

With the fitness cache disabled, the memory no longer depends on the number of planned
devices: with an EV and five home appliances the peak was 144 MB (about 300 MB with the
unbounded cache). More devices only cost run time.

### Fitness cache

Same platform, two consecutive runs, `MALLOC_ARENA_MAX=2`, measured together with the lazy
imports (server baseline 136.5 MB before the first run):

| Scenario | `fitness_cache_max_entries` | Peak RSS (anon) | Run time | Best fitness |
|---|---|---|---|---|
| base | `null` (unbounded) | 289.3 MB (231.3) | 89.5 s | −17.473689741050 |
| base | `0` | 179.5 MB (121.1) | 88.7 s | −17.473689741050 |
| with EV | `null` (unbounded) | 340.0 MB (282.0) | 131.6 s | −13.851295466546 |
| with EV | `0` | 181.3 MB (123.3) | 133.2 s | −13.851295466546 |

Only 6–15 % of the cache lookups hit. A Raspberry Pi 4 with real installation data (see below)
showed the same picture: the unbounded cache made a run about 3.5 % faster and cost 100 MB.

### Run time on real hardware

Data of a real installation (77 kWh battery, direct marketing, 24 h control horizon at
15 minutes), all settings above:

| Device | Run time | Peak RSS |
|---|---|---|
| Raspberry Pi 4 Model B (2 GB, Cortex-A72), fitness cache off | 623–636 s | 195 MB |
| Raspberry Pi 4 Model B, fitness cache unbounded | 602–609 s | 294 MB |
| x86 VM, 2 vCPU (container, with EV) | 173 s | 127 MB |

On a Raspberry Pi 4 a run therefore takes about 10 minutes. With a 15-minute `ems.interval`
the device is busy about 70 % of the time; a longer interval leaves more headroom.

### Storage writes

With the default `database.autosave_interval_sec` the measurement and prediction databases are
written every few seconds. On an installation that pushes its forecasts and measurements from
an external system this amounted to 12–14 MB per minute; with autosave disabled about
1 KiB per minute remained. On SD cards and small eMMC this is the main source of wear.

Disabling autosave means that data pushed to EOS is lost when EOS restarts. That is fine when an
external system (an energy manager, Home Assistant, Node-RED) pushes forecasts and measurements
again; it is not suitable when EOS fetches and keeps its own history.

### Allocator

On glibc, Python keeps memory freed after a run in its own pools. Measured on an x86 VM with an
unbounded fitness cache, `PYTHONMALLOC=malloc` together with the trim thresholds above lowered
the idle memory from about 295 MB to 238 MB, and after each run EOS fell back to about 260 MB
instead of staying at its peak. With the fitness cache disabled the difference is small
(about 5 MB), because the large per-run allocations no longer happen.
