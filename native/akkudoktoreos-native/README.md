# akkudoktoreos-native

Optional native (Rust) kernels for [Akkudoktor-EOS](https://github.com/Akkudoktor-EOS/EOS).
The first kernel is the GENETIC optimizer (`eos-genetic`, a port of
`akkudoktoreos.optimization.genetic`). For the same request and seed it returns the same
solution as the Python optimizer.

EOS works without this package and does not use it yet. See `native/README.md` in the EOS
repository for the interface, the numerical profile, the verification against Python and how
to build wheels.

License: Apache-2.0, like EOS.
