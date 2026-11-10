# akkudoktoreos-native

Optional native (Rust) kernels for [Akkudoktor-EOS](https://github.com/Akkudoktor-EOS/EOS).
The first kernel is the GENETIC optimizer (`eos-genetic`, a port of
`akkudoktoreos.optimization.genetic`). For the same request and seed it returns the same
solution as the Python optimizer.

EOS works without this package. To use it, install it next to EOS and select the backend:

```yaml
optimization:
  genetic:
    backend: native   # python (default) | native | compare
```

- `native` runs the optimizer in Rust and falls back to Python (with a log message) when the
  package is missing, does not know the installed EOS sources, or rejects the request.
- `compare` runs both, returns the Python result and logs every difference.

The package only runs for EOS sources it was verified against (`certified.json`, keyed by a
fingerprint of the GENETIC sources). See `native/README.md` in the EOS repository for the
interface, the numerical profile, the verification against Python and how to build wheels.

License: Apache-2.0, like EOS.
