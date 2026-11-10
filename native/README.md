# Native kernels (optional)

This directory holds an optional Rust implementation of the GENETIC optimizer and its Python
binding. EOS does not need it: without the package, or with the default configuration, nothing
here is used.

| Path | What it is |
|---|---|
| `eos-genetic/` | Rust port of `akkudoktoreos.optimization.genetic` (simulation, GA, terminal and tail value, solution). No Python, no I/O. |
| `akkudoktoreos-native/` | PyO3 binding, built into the wheel `akkudoktoreos-native` (Python package `akkudoktoreos_native`) with maturin. One `abi3` wheel per platform covers CPython 3.11+. |
| `scripts/build-wheels.sh` | Builds Linux wheels for x86_64, aarch64 and armv7 (glibc and musl) with maturin and zig. |
| `scripts/genetic_compare_plugin.py` | pytest plugin: compares every GENETIC optimization of a test session with the kernel and lists differences. |
| `scripts/certify.py` | Adds the fingerprint of the current GENETIC sources to `certified.json` after a clean compare run. |

The EOS side of the interface is `akkudoktoreos.optimization.genetic.native`: it builds the
kernel request from the configuration snapshot of a run, validates the response into a
`GeneticSolution` and compares two solutions bit for bit.

## Using it

```sh
pip install akkudoktoreos-native        # or a wheel built with scripts/build-wheels.sh
```

```yaml
optimization:
  genetic:
    backend: native   # python (default) | native | compare
```

- `python` – the Python optimizer, unchanged. The package is never imported.
- `native` – the whole run (`GeneticOptimization._optimize_ems_run`) in Rust, returning the same
  `GeneticSolution`. Falls back to Python, with one warning per reason, when the package is
  missing, was not verified for these GENETIC sources, or rejects the request (see below).
- `compare` – runs both, returns the Python result, logs whether they are identical, and keeps
  the last report in `akkudoktoreos.optimization.genetic.native.last_compare_report`.

## Verification guard

The kernel is a port; Python is the reference. `native.source_fingerprint()` hashes the Python
files that define a GENETIC run (comments and docstrings ignored) plus the self-consumption
table. The package's `certified.json` lists the fingerprints it was verified against. Any change
of those sources disables `native` until the port follows and the new fingerprint is certified:
users then get Python, never a stale port. `certify.py` only certifies from a compare report
without differences that was made for exactly these sources and this kernel version.

## Interface

One run is JSON in, JSON out: `akkudoktoreos_native.run_genetic(request) -> response`. The
request carries what `GeneticOptimization._optimize_ems_run` reads: the parameters as received
(`model_dump(mode="json")`), the frozen GENETIC settings, prediction hours, direct marketing,
timezone, EMS start, fallback charge rates of the configured devices, the previous solution
(warm start dating), seed, generations, individuals, worst case, the worker count and the
numerical profile. The response is `{"solution": <GeneticSolution>, "diagnostics": {...}}`.
The GIL is released during the run. Panics never cross into Python as `PanicException`; they
become `NativeRunError` like every other failure.

The kernel rejects (with `NativeRunError`) what it does not support instead of guessing:

- appliance `time_windows` / `shared_time_windows`,
- a battery without an inverter,
- infinite forecast values (NaN gaps are fine),
- parameter fields it does not know.

## Numerical profile

With the same seed, Python can return different plans on different CPUs. Two operations round
differently per platform:

- `np.dot` in `SelfConsumptionProbabilityInterpolator._expected_direct_consumption` goes to BLAS
  (OpenBLAS picks a kernel per CPU: NEOVERSEN1, CORTEXA53, Haswell, ...; Accelerate on macOS).
- `np.interp` may be compiled with fused multiply-add on some builds.

The kernel computes with a profile given in the request:

- `{"dot": "sequential", "interp_fma": false}` (the release profile, default): dot products are
  summed in index order, no fused multiply-add. The same input and seed give the same plan on
  x86_64, aarch64 and armv7, glibc or musl.
- `dot: "cortexa53"` / `"neoversen1"`: the summation order of these OpenBLAS `ddot` kernels, to
  reproduce an unpatched Python on such CPUs; `interp_fma: true` for NumPy builds with FMA.

`native.reference_profile()` probes this process' NumPy and returns the profile that reproduces
it, or `None` (e.g. Accelerate, x86-64 OpenBLAS kernels). If the interpolator sums in a fixed
order itself instead of calling `np.dot`, the release profile reproduces Python on every CPU
and no detection or patch is needed.

## Verification

Python is the reference; the kernel follows it.

```sh
pytest tests/test_geneticnative.py                      # needs the package for most tests
PYTHONPATH=native/scripts GENETIC_COMPARE_DOT=sequential GENETIC_COMPARE_OUT=compare.json \
  pytest -p genetic_compare_plugin tests/test_genetic*.py
python native/scripts/certify.py compare.json   # only if the summary shows 0 different
```

- `tests/test_geneticnative.py` runs native and Python on the reference inputs, synthetic
  48 h inputs (60 and 15 minutes, with and without direct marketing, EV deadline, appliance),
  warm starts, fitness cache limits, worst case and a start in the repeated DST hour, and
  requires bit-identical solutions (all `GeneticSolution` fields, floats compared bit for bit,
  plus the timezones of the timestamps). Python is put on the release profile for that
  (`native.SequentialDotNumpy`, NumPy grid interpolation).
- The compare plugin runs every GENETIC optimization of the test suite in both and reports
  `identical`, `unsupported` (the kernel rejected a request it does not support), `patched`
  (the test replaces parts of the Python optimizer, so a difference is expected), `different`
  and `failed` (any other native error: a panic, an invalid response).
- The `native` CI workflow builds the wheels, runs both on x86_64 and aarch64 and fails on any
  `different` or `failed` run, and its summary says whether the sources are certified. It runs
  only when GENETIC sources or `native/` change.

## Building

```sh
# development build into the active virtual environment
cd native/akkudoktoreos-native && maturin develop --release
# Linux wheels (needs rustup targets, maturin >= 1.9, ziglang)
native/scripts/build-wheels.sh native/dist
# kernel tests and lints
cd native && cargo test -p eos-genetic && cargo clippy --all-targets -- -D warnings
```

Rust 1.87 or newer. Release builds use `opt-level=3`, fat LTO, one codegen unit.

License: Apache-2.0, like EOS. The kernel was ported from EOS (see `eos-genetic/NOTICE`).
